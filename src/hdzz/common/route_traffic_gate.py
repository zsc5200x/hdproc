"""Cross-process traffic shaping for member ``transform.php`` requests.

The member pool can use several accounts concurrently, but accounts without a
proxy still share one network route.  The upstream edge applies limits to that
route, so account-level concurrency alone is not a safe traffic boundary.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

try:
    import fcntl
except ImportError:  # pragma: no cover - the production runtime is macOS.
    fcntl = None  # type: ignore[assignment]


BLOCKED_502_BODY_SHA256 = "5cabae7fe50d472590c91a058986facf6fe866165574b7895047abd6f3638637"


def _positive_float(value: Any, fallback: float, *, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except Exception:
        parsed = float(fallback)
    return max(float(minimum), min(float(maximum), parsed))


def _positive_int(value: Any, fallback: int, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = int(fallback)
    return max(int(minimum), min(int(maximum), parsed))


class RoutePermit:
    def __init__(
        self,
        gate: Optional["RouteTrafficGate"] = None,
        *,
        route_key: str = "",
        token: str = "",
        kind: str = "disabled",
        waited_ms: float = 0.0,
    ) -> None:
        self._gate = gate
        self.route_key = str(route_key or "")
        self.token = str(token or "")
        self.kind = str(kind or "disabled")
        self.waited_ms = float(waited_ms or 0.0)
        self._finished = False
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self._gate is not None and bool(self.route_key and self.token)

    def finish(
        self,
        *,
        http_status: Any = None,
        response_body_length: Any = None,
        response_body_sha256: Any = None,
    ) -> None:
        with self._lock:
            if self._finished:
                return
            self._finished = True
        if self._gate is None:
            return
        self._gate._finish(
            route_key=self.route_key,
            token=self.token,
            kind=self.kind,
            http_status=http_status,
            response_body_length=response_body_length,
            response_body_sha256=response_body_sha256,
        )


class RouteTrafficGate:
    """Coordinate request bursts by upstream host and actual proxy route."""

    def __init__(self) -> None:
        self._enabled = False
        self._state_dir: Optional[Path] = None
        self._configure_lock = threading.RLock()
        self._file_locks: Dict[str, threading.RLock] = {}
        self.max_read_inflight = 2
        self.max_context_inflight = 2
        self.submit_min_interval_s = 0.5
        self.context_hold_s = 0.75
        self.post_submit_hold_s = 0.25
        self.blocked_502_backoff_s = 1.0
        self.entry_ttl_s = 30.0

    def configure(
        self,
        data_dir: Path,
        *,
        enabled: bool = True,
        max_read_inflight: int = 2,
        max_context_inflight: int = 2,
        submit_min_interval_s: float = 0.5,
        context_hold_s: float = 0.75,
        post_submit_hold_s: float = 0.25,
        blocked_502_backoff_s: float = 1.0,
    ) -> "RouteTrafficGate":
        with self._configure_lock:
            self._enabled = bool(enabled)
            self._state_dir = Path(data_dir).resolve() / ".route_traffic_gate"
            self.max_read_inflight = _positive_int(max_read_inflight, 2, minimum=1, maximum=8)
            self.max_context_inflight = _positive_int(max_context_inflight, 2, minimum=1, maximum=8)
            self.submit_min_interval_s = _positive_float(submit_min_interval_s, 0.5, minimum=0.05, maximum=5.0)
            self.context_hold_s = _positive_float(context_hold_s, 0.75, minimum=0.05, maximum=5.0)
            self.post_submit_hold_s = _positive_float(post_submit_hold_s, 0.25, minimum=0.0, maximum=5.0)
            self.blocked_502_backoff_s = _positive_float(blocked_502_backoff_s, 1.0, minimum=0.1, maximum=10.0)
            if self._enabled:
                self._state_dir.mkdir(parents=True, exist_ok=True)
        return self

    @property
    def enabled(self) -> bool:
        return bool(self._enabled and self._state_dir is not None)

    @staticmethod
    def _request_kind(request_type: Any) -> str:
        value = str(request_type or "").strip().lower()
        if value == "ft_bet":
            return "submit"
        if value == "ft_order_view":
            return "context"
        return "read"

    @staticmethod
    def _proxy_from_session(session: Any) -> str:
        try:
            proxies = getattr(session, "proxies", {}) or {}
            return str(proxies.get("https") or proxies.get("http") or "").strip()
        except Exception:
            return ""

    @staticmethod
    def _route_key(base_url: Any, proxy: Any) -> str:
        try:
            parsed = urlsplit(str(base_url or "").strip())
            host = str(parsed.hostname or "").strip().lower()
            if not host:
                return ""
            port = parsed.port or (443 if str(parsed.scheme or "").lower() == "https" else 80)
        except Exception:
            return ""
        proxy_value = str(proxy or "").strip().lower() or "direct"
        raw = f"{host}:{port}|{proxy_value}"
        return hashlib.sha256(raw.encode("utf-8", "ignore")).hexdigest()

    def _path(self, route_key: str) -> Path:
        assert self._state_dir is not None
        return self._state_dir / f"{route_key}.json"

    @staticmethod
    def _empty_state() -> Dict[str, Any]:
        return {
            "reads": {},
            "contexts": {},
            "submits": {},
            "priority_waiters": {},
            "last_submit_started_at": 0.0,
            "critical_until": 0.0,
            "blocked_until": 0.0,
        }

    def _clean_state(self, state: Dict[str, Any], now: float) -> Dict[str, Any]:
        for name in ("reads", "contexts", "submits", "priority_waiters"):
            values = state.get(name)
            if not isinstance(values, dict):
                values = {}
            state[name] = {
                str(token): float(expiry)
                for token, expiry in values.items()
                if str(token) and _positive_float(expiry, 0.0, minimum=0.0, maximum=now + 86400.0) > now
            }
        for name in ("last_submit_started_at", "critical_until", "blocked_until"):
            try:
                state[name] = float(state.get(name) or 0.0)
            except Exception:
                state[name] = 0.0
        return state

    def _mutate(self, route_key: str, callback: Any) -> Any:
        path = self._path(route_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._configure_lock:
            local_lock = self._file_locks.setdefault(route_key, threading.RLock())
        with local_lock:
            with path.open("a+", encoding="utf-8") as handle:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    handle.seek(0)
                    raw = handle.read()
                    try:
                        loaded = json.loads(raw) if raw.strip() else self._empty_state()
                    except Exception:
                        loaded = self._empty_state()
                    state = loaded if isinstance(loaded, dict) else self._empty_state()
                    now = time.time()
                    state = self._clean_state(state, now)
                    result = callback(state, now)
                    handle.seek(0)
                    handle.truncate()
                    json.dump(state, handle, ensure_ascii=True, separators=(",", ":"))
                    handle.flush()
                    return result
                finally:
                    if fcntl is not None:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _announce_priority(self, route_key: str, token: str) -> None:
        def update(state: Dict[str, Any], now: float) -> None:
            state["priority_waiters"][token] = now + self.entry_ttl_s
            state["critical_until"] = max(float(state.get("critical_until") or 0.0), now + 0.1)

        self._mutate(route_key, update)

    def acquire(
        self,
        *,
        base_url: Any,
        request_type: Any,
        session: Any = None,
        proxy: Any = None,
        timeout_s: Any = 20,
    ) -> RoutePermit:
        if not self.enabled:
            return RoutePermit()
        proxy_value = str(proxy or "").strip() or self._proxy_from_session(session)
        route_key = self._route_key(base_url, proxy_value)
        if not route_key:
            return RoutePermit()

        kind = self._request_kind(request_type)
        token = f"{os.getpid()}-{threading.get_ident()}-{uuid.uuid4().hex}"
        started = time.monotonic()
        ttl = max(self.entry_ttl_s, _positive_float(timeout_s, 20.0, minimum=1.0, maximum=120.0) + 5.0)
        if kind in {"context", "submit"}:
            try:
                self._announce_priority(route_key, token)
            except Exception:
                return RoutePermit()

        while True:
            try:
                def try_admit(state: Dict[str, Any], now: float) -> Dict[str, Any]:
                    blocked_wait = max(0.0, float(state.get("blocked_until") or 0.0) - now)
                    if kind == "read":
                        critical_wait = max(0.0, float(state.get("critical_until") or 0.0) - now)
                        busy_priority = bool(state.get("priority_waiters") or state.get("contexts") or state.get("submits"))
                        if blocked_wait > 0.0 or critical_wait > 0.0 or busy_priority:
                            return {"admitted": False, "wait": max(0.02, blocked_wait, critical_wait)}
                        if len(state.get("reads") or {}) >= self.max_read_inflight:
                            return {"admitted": False, "wait": 0.02}
                        state["reads"][token] = now + ttl
                        return {"admitted": True, "wait": 0.0}

                    if blocked_wait > 0.0:
                        return {"admitted": False, "wait": max(0.02, blocked_wait)}
                    if state.get("reads"):
                        return {"admitted": False, "wait": 0.02}
                    if kind == "context":
                        if state.get("submits"):
                            state["priority_waiters"][token] = now + ttl
                            return {"admitted": False, "wait": 0.02}
                        if len(state.get("contexts") or {}) >= self.max_context_inflight:
                            state["priority_waiters"][token] = now + ttl
                            return {"admitted": False, "wait": 0.02}
                        state["priority_waiters"].pop(token, None)
                        state["contexts"][token] = now + ttl
                        state["critical_until"] = max(float(state.get("critical_until") or 0.0), now + self.context_hold_s)
                        return {"admitted": True, "wait": 0.0}

                    if state.get("contexts"):
                        state["priority_waiters"][token] = now + ttl
                        return {"admitted": False, "wait": 0.02}
                    interval_wait = max(
                        0.0,
                        float(state.get("last_submit_started_at") or 0.0) + self.submit_min_interval_s - now,
                    )
                    if interval_wait > 0.0:
                        state["priority_waiters"][token] = now + ttl
                        return {"admitted": False, "wait": max(0.02, interval_wait)}
                    state["priority_waiters"].pop(token, None)
                    state["submits"][token] = now + ttl
                    state["last_submit_started_at"] = now
                    state["critical_until"] = max(float(state.get("critical_until") or 0.0), now + self.post_submit_hold_s)
                    return {"admitted": True, "wait": 0.0}

                result = self._mutate(route_key, try_admit)
            except Exception:
                return RoutePermit()
            if bool((result or {}).get("admitted")):
                return RoutePermit(
                    self,
                    route_key=route_key,
                    token=token,
                    kind=kind,
                    waited_ms=(time.monotonic() - started) * 1000.0,
                )
            wait_s = _positive_float((result or {}).get("wait"), 0.02, minimum=0.01, maximum=0.1)
            time.sleep(wait_s)

    def _finish(
        self,
        *,
        route_key: str,
        token: str,
        kind: str,
        http_status: Any,
        response_body_length: Any,
        response_body_sha256: Any,
    ) -> None:
        try:
            status = int(http_status or 0)
        except Exception:
            status = 0
        try:
            body_length = int(response_body_length or 0)
        except Exception:
            body_length = 0
        body_sha = str(response_body_sha256 or "").strip()

        def update(state: Dict[str, Any], now: float) -> None:
            state["priority_waiters"].pop(token, None)
            state["reads"].pop(token, None)
            state["contexts"].pop(token, None)
            state["submits"].pop(token, None)
            if kind == "context":
                state["critical_until"] = max(float(state.get("critical_until") or 0.0), now + self.context_hold_s)
            elif kind == "submit":
                state["critical_until"] = max(float(state.get("critical_until") or 0.0), now + self.post_submit_hold_s)
            blocked_fingerprint = body_length == 301 and body_sha == BLOCKED_502_BODY_SHA256
            if status == 502 or blocked_fingerprint:
                state["blocked_until"] = max(float(state.get("blocked_until") or 0.0), now + self.blocked_502_backoff_s)
                state["critical_until"] = max(float(state.get("critical_until") or 0.0), state["blocked_until"])

        try:
            self._mutate(route_key, update)
        except Exception:
            return


_ROUTE_TRAFFIC_GATE = RouteTrafficGate()


def configure_route_traffic_gate(data_dir: Path, **kwargs: Any) -> RouteTrafficGate:
    return _ROUTE_TRAFFIC_GATE.configure(data_dir, **kwargs)


def route_traffic_gate() -> RouteTrafficGate:
    return _ROUTE_TRAFFIC_GATE
