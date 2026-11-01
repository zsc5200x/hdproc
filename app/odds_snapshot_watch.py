from __future__ import annotations

import argparse
import html
import json
import os
import re
import threading
import tempfile
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from runtime_bootstrap import bootstrap_app_runtime

bootstrap_app_runtime(Path(__file__))

from hdzz.api.webui import server as webui_server  # noqa: E402
from hdzz.core.follower.odds_snapshot import load_or_refresh_snapshot, load_snapshot  # noqa: E402
from hdzz.core.follower.member_ioratio import store_member_snapshot_ioratios  # noqa: E402
from hdzz.core.follower.market_rows import extract_market_rows  # noqa: E402
from hdzz.core.follower.snapshot_direct import MarketIntent, intent_market_fields, line_matches  # noqa: E402
from hdzz.core.follower.snapshot_metrics import (  # noqa: E402
    record_snapshot_publish_event,
    record_snapshot_watch_event,
)


_RESOLVED_CACHE: Dict[Tuple[str, str, str], Tuple[float, Dict[str, Any]]] = {}
_RESOLVED_CACHE_TTL_S = 30.0
_RESOLVED_CACHE_LOCK = threading.RLock()
_SESSION_POOL: Dict[str, Any] = {}
_SESSION_POOL_LOCK = threading.RLock()
_RAW_SAMPLE_LOCK = threading.RLock()
_MEMBER_QUOTE_EXECUTOR = ThreadPoolExecutor(max_workers=4)
_PUBLIC_MEMBER_EXECUTOR = ThreadPoolExecutor(max_workers=4)
_MEMBER_QUOTE_INFLIGHT: set[Tuple[str, str, str]] = set()
_MEMBER_QUOTE_INFLIGHT_LOCK = threading.RLock()
_PRIORITY_REQUEST_LOCK = threading.RLock()
_MEMBER_QUOTE_METRICS_LOCK = threading.RLock()


class SingleInstanceFileLock:
    """Cross-process lock that is released automatically when the worker exits."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._handle: Optional[Any] = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt  # type: ignore

                handle.seek(0)
                if not handle.read(1):
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            handle.seek(0)
            handle.truncate()
            handle.write(str(os.getpid()).encode("ascii"))
            handle.flush()
        except (OSError, IOError):
            handle.close()
            return False
        self._handle = handle
        return True

    def release(self) -> None:
        handle = self._handle
        self._handle = None
        if handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt  # type: ignore

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except (OSError, IOError):
            pass
        handle.close()


def _global_snapshot_lock_path() -> Path:
    """Return the per-user lock shared by source and Tauri runtimes."""
    uid = getattr(os, "getuid", lambda: 0)()
    return Path(tempfile.gettempdir()) / f"hdzz-odds-snapshot-watch-{uid}.lock"


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        obj = json.loads(path.read_text(encoding="utf-8-sig") or "{}")
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _read_priority_refresh_requests(data_dir: Path) -> List[Dict[str, Any]]:
    path = Path(data_dir) / "member_ioratio_refresh_requests.jsonl"
    if not path.exists():
        return []
    with _PRIORITY_REQUEST_LOCK:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
            path.write_text("", encoding="utf-8")
        except Exception:
            return []
    requests: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for line in lines:
        try:
            item = json.loads(line)
        except Exception:
            continue
        if not isinstance(item, dict):
            continue
        request_id = str(item.get("request_id") or "")
        if request_id and request_id in seen:
            continue
        if request_id:
            seen.add(request_id)
        requests.append(item)
    return requests


def _priority_refresh_member_quote(data_dir: Path, request: Dict[str, Any]) -> Dict[str, Any]:
    """Refresh one execution member for one exact MarketRow in the kitchen."""
    member_id = str(request.get("member_id") or "").strip()
    row = request.get("market_row") if isinstance(request.get("market_row"), dict) else {}
    if not member_id or not row:
        return {"ok": False, "error": "priority_request_invalid"}
    mode = str(request.get("mode") or "live").strip().lower() or "live"
    members = _load_quote_members(data_dir)
    member = next((m for m in members if str(m.get("id") or "").strip() == member_id), None)
    if not isinstance(member, dict):
        return {"ok": False, "member_id": member_id, "error": "member_not_found"}
    started_at = float(time.time())
    queued_at = float(request.get("_scheduled_at") or request.get("created_at") or started_at)
    timeout_s = max(1, int(request.get("_timeout_s") or 2))
    source_gid = str(request.get("source_gid") or row.get("source_gid") or row.get("gid") or "")
    requested_fp = str(row.get("market_row_fingerprint") or row.get("fingerprint") or "")
    metric: Dict[str, Any] = {
        "event": "member_ioratio_refresh",
        "member_id": member_id,
        "market_row_fingerprint": requested_fp,
        "requested_interval_ms": float(request.get("requested_interval_ms") or 400.0),
        "priority_refresh": bool(request.get("priority_refresh", False)),
        "refresh_started_at": started_at,
        "queue_delay_ms": round(max(0.0, started_at - queued_at) * 1000.0, 3),
    }
    try:
        session = _pooled_member_session(member)
        resolved = _resolve_game_cached(
            session=session,
            member=member,
            gid=source_gid,
            mode=mode,
            timeout_s=timeout_s,
            fixture_locator=row.get("fixture_locator") if isinstance(row.get("fixture_locator"), dict) else {},
        )
        snapshot = webui_server._chashui_get_odds_snapshot_with_empty_retry(
            session=session,
            member=member,
            resolved=resolved,
            include_raw=False,
            confirm_odds=False,
            timeout_s=timeout_s,
            required_markets=[{
                "market_type": row.get("market_type"),
                "period": row.get("period"),
                "side": row.get("side"),
                "target_line": row.get("line"),
                "wtype": row.get("wtype"),
                "rtype": row.get("rtype"),
            }],
        )
        public = load_snapshot(
            data_dir / "odds_snapshot_cache.json",
            gid=source_gid,
            mode=mode,
            max_age_s=float("inf"),
        ) or {}
        public_revision = int(public.get("revision") or row.get("revision") or 0)
        parsed_rows = extract_market_rows(
            snapshot,
            mode=mode,
            requested_gid=source_gid,
            include_ioratio=True,
        )
        target_row = next(
            (
                item for item in parsed_rows
                if str(item.get("market_row_fingerprint") or item.get("fingerprint") or "") == requested_fp
            ),
            None,
        )
        if target_row is None:
            finished_at = float(time.time())
            metric.update({
                "refresh_finished_at": finished_at,
                "refresh_elapsed_ms": round(max(0.0, finished_at - started_at) * 1000.0, 3),
                "upstream_elapsed_ms": round(max(0.0, finished_at - started_at) * 1000.0, 3),
                "quote_age_ms": None,
                "ok": False,
                "error_code": "TARGET_LINE_MISSING",
            })
            return {
                "ok": False,
                "member_id": member_id,
                "requested_market_row_fingerprint": requested_fp,
                "elapsed_ms": metric["refresh_elapsed_ms"],
                "started_at": started_at,
                "finished_at": finished_at,
                "error": "TARGET_LINE_MISSING",
                "error_code": "TARGET_LINE_MISSING",
                "priority": True,
            }
        stored = store_member_snapshot_ioratios(
            data_dir / "member_ioratio_cache.json",
            member=member,
            snapshot=snapshot,
            mode=mode,
            requested_gid=source_gid,
            public_snapshot_revision=public_revision,
            fetched_at=float(time.time()),
        )
        finished_at = float(time.time())
        metric.update({
            "refresh_finished_at": finished_at,
            "refresh_elapsed_ms": round(max(0.0, finished_at - started_at) * 1000.0, 3),
            "upstream_elapsed_ms": round(max(0.0, finished_at - started_at) * 1000.0, 3),
            "quote_age_ms": 0,
            "ok": True,
            "error_code": None,
            "target_line_found": True,
        })
        return {
            "ok": True,
            "member_id": member_id,
            "stored_count": int(stored.get("stored_count") or 0),
            "requested_market_row_fingerprint": str(row.get("market_row_fingerprint") or row.get("fingerprint") or ""),
            "elapsed_ms": round(max(0.0, finished_at - started_at) * 1000.0, 3),
            "started_at": started_at,
            "finished_at": finished_at,
            "priority": True,
            "target_line_found": True,
            "refresh_started_at": started_at,
            "refresh_finished_at": finished_at,
            "queue_delay_ms": metric["queue_delay_ms"],
            "upstream_elapsed_ms": metric["upstream_elapsed_ms"],
            "error_code": None,
        }
    except Exception as exc:
        finished_at = float(time.time())
        error_code = _classify_snapshot_error(exc)
        metric.update({
            "refresh_finished_at": finished_at,
            "refresh_elapsed_ms": round(max(0.0, finished_at - started_at) * 1000.0, 3),
            "upstream_elapsed_ms": round(max(0.0, finished_at - started_at) * 1000.0, 3),
            "quote_age_ms": None,
            "ok": False,
            "error_code": error_code,
            "error": f"{type(exc).__name__}: {exc}"[:500],
        })
        return {
            "ok": False,
            "member_id": member_id,
            "elapsed_ms": round(max(0.0, finished_at - started_at) * 1000.0, 3),
            "started_at": started_at,
            "finished_at": finished_at,
            "error": f"{type(exc).__name__}: {exc}",
            "error_code": error_code,
            "priority": True,
            "refresh_started_at": started_at,
            "refresh_finished_at": finished_at,
            "queue_delay_ms": metric["queue_delay_ms"],
            "upstream_elapsed_ms": metric["upstream_elapsed_ms"],
        }


def _record_member_quote_metric(data_dir: Path, metric: Dict[str, Any]) -> None:
    """Write member-quote telemetry separately from public snapshot metrics."""
    path = Path(data_dir) / "member_ioratio_watch_metrics.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(dict(metric), ensure_ascii=False, separators=(",", ":")) + "\n"
    with _MEMBER_QUOTE_METRICS_LOCK:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()


@dataclass
class MemberQuoteState:
    key: Tuple[str, str, str]
    data_dir: Path
    request: Dict[str, Any]
    requested_interval_ms: float = 400.0
    deadline_ms: float = 2000.0
    active_until: float = 0.0
    next_refresh_at: float = 0.0
    last_attempt_at: Optional[float] = None
    last_success_at: Optional[float] = None
    last_quote_age_ms: Optional[float] = None
    inflight: Optional[Future] = None
    deadline_at: Optional[float] = None
    deadline_recorded: bool = False
    failure_count: int = 0
    previous_attempt_at: Optional[float] = None
    queue_delay_ms: float = 0.0


class MemberQuoteScheduler:
    """Independent bounded scheduler for one member and one exact MarketRow."""

    def __init__(
        self,
        *,
        executor: ThreadPoolExecutor,
        requested_interval_ms: float = 400.0,
        deadline_ms: float = 2000.0,
        active_ttl_s: float = 120.0,
        max_workers: Optional[int] = None,
    ) -> None:
        self.executor = executor
        self.requested_interval_ms = max(50.0, float(requested_interval_ms or 400.0))
        self.deadline_ms = max(100.0, float(deadline_ms or 2000.0))
        self.active_ttl_s = max(1.0, float(active_ttl_s or 120.0))
        executor_workers = int(getattr(executor, "_max_workers", 1) or 1)
        self.max_workers = max(1, int(max_workers or executor_workers))
        self.states: Dict[Tuple[str, str, str], MemberQuoteState] = {}
        self._lock = threading.RLock()

    def register(self, data_dir: Path, request: Dict[str, Any], *, now: Optional[float] = None) -> Optional[MemberQuoteState]:
        now_value = float(time.time() if now is None else now)
        member_id = str(request.get("member_id") or "").strip()
        mode = str(request.get("mode") or "live").strip().lower() or "live"
        row = request.get("market_row") if isinstance(request.get("market_row"), dict) else {}
        fingerprint = str(row.get("market_row_fingerprint") or row.get("fingerprint") or "").strip()
        if not member_id or not fingerprint or not row:
            return None
        key = (member_id, mode, fingerprint)
        request_copy = dict(request)
        request_copy["priority_refresh"] = bool(request.get("priority_refresh", False))
        request_copy["requested_interval_ms"] = self.requested_interval_ms
        request_copy["_timeout_s"] = max(1, int(round(self.deadline_ms / 1000.0)))
        with self._lock:
            state = self.states.get(key)
            if state is None:
                state = MemberQuoteState(
                    key=key,
                    data_dir=Path(data_dir),
                    request=request_copy,
                    requested_interval_ms=self.requested_interval_ms,
                    deadline_ms=self.deadline_ms,
                    active_until=now_value + self.active_ttl_s,
                    next_refresh_at=now_value,
                )
                self.states[key] = state
            else:
                if state.inflight is not None and state.inflight.done():
                    state.inflight = None
                    state.deadline_at = None
                state.data_dir = Path(data_dir)
                state.request.update(request_copy)
                state.active_until = now_value + self.active_ttl_s
                state.next_refresh_at = min(state.next_refresh_at, now_value)
            return state

    def _submit(self, state: MemberQuoteState, now_value: float) -> None:
        state.previous_attempt_at = state.last_attempt_at
        state.last_attempt_at = now_value
        scheduled_at = state.next_refresh_at
        state.queue_delay_ms = round(max(0.0, now_value - scheduled_at) * 1000.0, 3)
        state.deadline_at = now_value + state.deadline_ms / 1000.0
        state.deadline_recorded = False
        request = dict(state.request)
        request["_scheduled_at"] = now_value
        request["requested_interval_ms"] = state.requested_interval_ms
        state.inflight = self.executor.submit(_priority_refresh_member_quote, state.data_dir, request)

    def _record_completion(self, state: MemberQuoteState, now_value: float, result: Dict[str, Any]) -> None:
        ok = bool(result.get("ok")) and bool(result.get("target_line_found", True))
        if ok:
            state.failure_count = 0
            state.last_success_at = now_value
            state.last_quote_age_ms = float(result.get("quote_age_ms") or 0.0)
        else:
            state.failure_count += 1
            state.last_quote_age_ms = None
        actual_interval = None
        if state.previous_attempt_at is not None and state.last_attempt_at is not None:
            actual_interval = round(max(0.0, state.last_attempt_at - state.previous_attempt_at) * 1000.0, 3)
        event = {
            "ts": now_value,
            "event": "member_ioratio_refresh",
            "member_id": state.key[0],
            "mode": state.key[1],
            "gid": (state.request.get("market_row") or {}).get("gid"),
            "market_row_fingerprint": state.key[2],
            "requested_interval_ms": state.requested_interval_ms,
            "actual_refresh_interval_ms": actual_interval,
            "quote_age_ms": state.last_quote_age_ms,
            "refresh_started_at": result.get("refresh_started_at") or state.last_attempt_at,
            "refresh_finished_at": result.get("refresh_finished_at") or now_value,
            "refresh_elapsed_ms": result.get("elapsed_ms"),
            "queue_delay_ms": result.get("queue_delay_ms", state.queue_delay_ms),
            "upstream_elapsed_ms": result.get("upstream_elapsed_ms"),
            "priority_refresh": bool(state.request.get("priority_refresh")),
            "ok": ok,
            "error_code": result.get("error_code"),
            "failure_count": state.failure_count,
        }
        _record_member_quote_metric(state.data_dir, event)
        # Keep the existing combined stream compatible with the WebUI/report.
        record_snapshot_watch_event(state.data_dir, {"ts": now_value, **event})

    def poll(self, now: Optional[float] = None) -> None:
        now_value = float(time.time() if now is None else now)
        with self._lock:
            for key, state in list(self.states.items()):
                if state.inflight is None and now_value > state.active_until:
                    self.states.pop(key, None)
                    continue
                future = state.inflight
                if future is None:
                    continue
                if not future.done():
                    if state.deadline_at and now_value >= state.deadline_at and not state.deadline_recorded:
                        state.deadline_recorded = True
                        self._record_completion(
                            state,
                            now_value,
                            {"ok": False, "error_code": "READ_TIMEOUT", "elapsed_ms": state.deadline_ms},
                        )
                    continue
                state.inflight = None
                late = bool(state.deadline_recorded) or bool(state.deadline_at and now_value >= state.deadline_at)
                state.deadline_at = None
                if late and not state.deadline_recorded:
                    state.deadline_recorded = True
                    self._record_completion(
                        state,
                        now_value,
                        {"ok": False, "error_code": "READ_TIMEOUT", "elapsed_ms": state.deadline_ms},
                    )
                if not late:
                    try:
                        result = future.result()
                    except BaseException as exc:
                        result = {"ok": False, "error_code": _classify_snapshot_error(exc), "error": str(exc)}
                    self._record_completion(state, now_value, result if isinstance(result, dict) else {"ok": False})
                backoff = min(12.0, 2.0 ** max(0, state.failure_count - 1))
                state.next_refresh_at = now_value + state.requested_interval_ms / 1000.0 * backoff

    def start_due(self, now: Optional[float] = None) -> int:
        now_value = float(time.time() if now is None else now)
        started = 0
        with self._lock:
            active = sum(1 for state in self.states.values() if state.inflight is not None and not state.inflight.done())
            available = max(0, self.max_workers - active)
            for state in sorted(
                self.states.values(),
                key=lambda item: (-int(bool(item.request.get("priority_refresh"))), item.next_refresh_at, item.key),
            ):
                if started >= available:
                    break
                if now_value > state.active_until or state.inflight is not None or now_value < state.next_refresh_at:
                    continue
                self._submit(state, now_value)
                started += 1
        return started

    def close(self) -> None:
        with self._lock:
            self.states.clear()


_MEMBER_QUOTE_SCHEDULER = MemberQuoteScheduler(executor=_MEMBER_QUOTE_EXECUTOR)


def _schedule_priority_refreshes(data_dir: Path) -> int:
    requests = _read_priority_refresh_requests(data_dir)
    scheduled = 0
    now_value = float(time.time())
    for request in requests:
        if _MEMBER_QUOTE_SCHEDULER.register(data_dir, request, now=now_value) is not None:
            scheduled += 1
    # Do not wait for the main loop's next tick to start a selected-member
    # refresh.  Subsequent attempts are driven independently by poll/start_due.
    _MEMBER_QUOTE_SCHEDULER.start_due(now_value)
    return scheduled


def _is_live(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "live", "滚球", "rb"} or "滚球" in text or "live" in text


def _global_only_live_enabled(state_path: Path) -> bool:
    """Read the explicit master filter without affecting order ingestion.

    Early orders must remain in ``orders/*.jsonl`` and in the WebUI.  When the
    master switch is on, only avoid spending snapshot-worker capacity on
    those orders because the follow engine will record them as skipped.
    ``None`` keeps legacy per-target behavior unchanged.
    """
    settings = _read_json(Path(state_path).parent / "follow_settings.json")
    if "only_live" not in settings or settings.get("only_live") is None:
        return False
    value = settings.get("only_live")
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def order_subscription(order: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    if not isinstance(order, dict):
        return None
    raw = order.get("raw") if isinstance(order.get("raw"), dict) else {}
    gid = str(
        order.get("gid")
        or order.get("game_gid")
        or order.get("GID")
        or raw.get("_EVENT")
        or raw.get("GID")
        or ""
    ).strip()
    if not gid:
        return None
    market_text = str(
        order.get("market")
        or raw.get("MARKET")
        or order.get("betType")
        or order.get("wagerType")
        or raw.get("WAGERSTYPE")
        or ""
    )
    mode = "live" if _is_live(order.get("live")) or _is_live(market_text) else "today"
    return gid, mode


def market_demand(order: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Normalize the active order into the market the snapshot must contain."""
    if not isinstance(order, dict):
        return None
    try:
        intent = MarketIntent.from_order(order)
    except Exception:
        return None
    if not intent.gid:
        return None
    mode = "live" if intent.is_live else "today"
    fields = intent_market_fields(intent)
    fields["mode"] = mode
    fields["order_id"] = str(order.get("orderId") or ((order.get("raw") or {}).get("TID") if isinstance(order.get("raw"), dict) else "") or "")
    fixture_locator = _order_fixture_locator(order)
    # Corner orders carry the CN child board's provider team numbers, while
    # get_game_list exposes the ordinary parent fixture's different numbers.
    # Keep this explicit so parent resolution can trust exact names for corner
    # orders without weakening number matching for ordinary football markets.
    fixture_locator["is_corner"] = bool(intent.is_corner)
    fields["fixture_locator"] = fixture_locator
    return fields


def _plain_order_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _order_fixture_locator(order: Dict[str, Any]) -> Dict[str, Any]:
    """Keep source fixture identity for child-board gid resolution.

    Corner full/half gids can exist only inside ``game_more`` and may be absent
    from ``get_game_list``.  The watcher uses the source fixture names only to
    locate the parent response; the requested child gid is still required to
    exist in that response before a Snapshot can be published.
    """
    raw = order.get("raw") if isinstance(order.get("raw"), dict) else {}
    league = _plain_order_text(
        order.get("league") or raw.get("SHOWTEXT_LEAGUE") or raw.get("LEAGUE_NAME")
    )
    sides = _plain_order_text(
        order.get("sides") or raw.get("SHOWTEXT_TEAM") or raw.get("TEAM")
    )
    sides = re.sub(r"\s*\(\s*\d+\s*[-:]\s*\d+\s*\)\s*$", "", sides).strip()
    parts = re.split(r"\s+(?:v|vs|VS|对|對)\s+", sides, maxsplit=1)
    team_h = parts[0].strip() if len(parts) == 2 else ""
    team_c = parts[1].strip() if len(parts) == 2 else ""
    numbers = re.findall(
        r"\[(\d+)\]",
        str(raw.get("SHOWTEXT_TEAM_NUM") or raw.get("TNAME") or ""),
    )
    gnum_h = str(
        raw.get("GNUM_H") or raw.get("GNUMH") or raw.get("TEAM_H_NUM") or order.get("gnum_h") or ""
    ).strip()
    gnum_c = str(
        raw.get("GNUM_C") or raw.get("GNUMC") or raw.get("TEAM_C_NUM") or order.get("gnum_c") or ""
    ).strip()
    return {
        "league": league,
        "team_h": team_h,
        "team_c": team_c,
        "gnum_h": gnum_h or (numbers[0] if len(numbers) >= 1 else ""),
        "gnum_c": gnum_c or (numbers[1] if len(numbers) >= 2 else ""),
    }


def _iter_jsonl(path: Path, *, tail_lines: int = 300) -> Iterable[Dict[str, Any]]:
    if not path.exists():
        return []
    try:
        rows = path.read_text(encoding="utf-8-sig").splitlines()[-max(1, int(tail_lines)) :]
    except Exception:
        return []
    out: List[Dict[str, Any]] = []
    for line in rows:
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def collect_subscriptions(
    *,
    orders_dir: Path,
    state_path: Path,
    now: Optional[float] = None,
    active_seconds: float = 120.0,
) -> List[Tuple[str, str]]:
    now_value = float(time.time() if now is None else now)
    found: Dict[Tuple[str, str], None] = {}

    for path in sorted(Path(orders_dir).glob("*.jsonl")):
        for order in _iter_jsonl(path):
            # 接收时间与本机时区一致；后台 bet_ts 可能存在固定时差，只作回退。
            ts = order.get("ts") or order.get("bet_ts") or order.get("timestamp")
            if ts not in (None, ""):
                try:
                    if now_value - float(ts) > float(active_seconds):
                        continue
                except Exception:
                    pass
            item = order_subscription(order)
            if item:
                found[item] = None

    state = _read_json(Path(state_path))
    for section in ("pending_orders", "jobs"):
        records = state.get(section) if isinstance(state.get(section), dict) else {}
        for record in records.values():
            if not isinstance(record, dict):
                continue
            item = order_subscription(record.get("order") if isinstance(record.get("order"), dict) else record)
            if item:
                found[item] = None
    return sorted(found.keys())


def _active_orders(*, orders_dir: Path, state_path: Path, now: float, active_seconds: float) -> Iterable[Dict[str, Any]]:
    for path in sorted(Path(orders_dir).glob("*.jsonl")):
        for order in _iter_jsonl(path):
            ts = order.get("ts") or order.get("bet_ts") or order.get("timestamp")
            if ts not in (None, ""):
                try:
                    if now - float(ts) > float(active_seconds):
                        continue
                except Exception:
                    pass
            yield order
    state = _read_json(Path(state_path))
    for section in ("pending_orders", "jobs"):
        records = state.get(section) if isinstance(state.get(section), dict) else {}
        for record in records.values():
            if not isinstance(record, dict):
                continue
            order = record.get("order") if isinstance(record.get("order"), dict) else record
            if isinstance(order, dict):
                yield order


def collect_market_demands(
    *,
    orders_dir: Path,
    state_path: Path,
    now: Optional[float] = None,
    active_seconds: float = 120.0,
) -> List[Dict[str, Any]]:
    """Group active orders by match while retaining every requested market.

    The explicit global only-live switch only changes snapshot demand; it does
    not remove or mutate the source order.
    """
    now_value = float(time.time() if now is None else now)
    only_live = _global_only_live_enabled(state_path)
    grouped: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for order in _active_orders(orders_dir=orders_dir, state_path=state_path, now=now_value, active_seconds=active_seconds):
        if only_live:
            subscription = order_subscription(order)
            if subscription and subscription[1] != "live":
                continue
        item = market_demand(order)
        if item is None:
            sub = order_subscription(order)
            if not sub:
                continue
            gid, mode = sub
            item = {"gid": gid, "mode": mode, "market_type": "", "period": "", "side": "", "target_line": None, "wtype": "", "rtype": ""}
        key = (str(item.get("gid") or ""), str(item.get("mode") or "live"))
        if not key[0]:
            continue
        row = grouped.setdefault(
            key,
            {
                "gid": key[0],
                "mode": key[1],
                "requirements": [],
                "fixture_locator": dict(item.get("fixture_locator") or {}),
            },
        )
        if not row.get("fixture_locator") and item.get("fixture_locator"):
            row["fixture_locator"] = dict(item.get("fixture_locator") or {})
        req_key = json.dumps({k: item.get(k) for k in ("market_type", "period", "side", "target_line", "wtype", "rtype", "is_corner")}, sort_keys=True, ensure_ascii=False)
        if req_key not in {json.dumps({k: r.get(k) for k in ("market_type", "period", "side", "target_line", "wtype", "rtype", "is_corner")}, sort_keys=True, ensure_ascii=False) for r in row["requirements"]}:
            row["requirements"].append(item)
    return [grouped[key] for key in sorted(grouped)]


def active_market_key(item: Dict[str, Any], requirement: Dict[str, Any]) -> str:
    """Build the stable scheduler key for one exact requested market."""
    fields = (
        str(item.get("mode") or "live").strip().lower(),
        str(requirement.get("gid") or item.get("gid") or "").strip(),
        str(requirement.get("period") or "").strip().upper(),
        str(requirement.get("market_type") or "").strip().upper(),
        str(requirement.get("side") or "").strip().upper(),
        str(requirement.get("target_line") if requirement.get("target_line") is not None else "").strip(),
        str(requirement.get("wtype") or "").strip().upper(),
        "CORNER" if bool(requirement.get("is_corner")) else "STANDARD",
    )
    return "|".join(fields)


def flatten_active_markets(demands: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Expand grouped match demands into independently scheduled markets."""
    out: List[Dict[str, Any]] = []
    for item in demands or []:
        requirements = item.get("requirements") if isinstance(item.get("requirements"), list) else []
        if not requirements:
            requirements = [{
                "gid": item.get("gid"),
                "market_type": "",
                "period": "",
                "side": "",
                "target_line": None,
                "wtype": "",
                "rtype": "",
            }]
        for requirement in requirements:
            if not isinstance(requirement, dict):
                continue
            req = dict(requirement)
            key = active_market_key(item, req)
            if not key or key.startswith("|||||"):
                continue
            out.append({
                "gid": str(item.get("gid") or req.get("gid") or ""),
                "mode": str(item.get("mode") or "live").strip().lower() or "live",
                "requirements": [req],
                "requirement": req,
                "fixture_locator": dict(item.get("fixture_locator") or req.get("fixture_locator") or {}),
                "active_market_key": key,
                # Every demand originates from an active follow task. It gets
                # priority over any future background/non-order subscription.
                "priority": 100,
            })
    return out


def _load_quote_members(data_dir: Path) -> List[Dict[str, Any]]:
    doc = _read_json(Path(data_dir) / "arsenal.json")
    members = doc.get("members") if isinstance(doc.get("members"), list) else []
    return [m for m in members if isinstance(m, dict)]


def _execution_quote_members(members: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return real execution members whose sessions can be read by the kitchen."""
    out: List[Dict[str, Any]] = []
    for member in members:
        if not isinstance(member, dict):
            continue
        if str(member.get("status") or "").strip().lower() != "online":
            continue
        if str(member.get("role") or "").strip().lower() == "quote":
            continue
        if str(member.get("usage") or "").strip().lower() in {"read", "quote"}:
            continue
        if not all(str(member.get(key) or "").strip() for key in ("id", "base_url", "uid", "ver", "cookie")):
            continue
        out.append(member)
    return out


def _requested_market_rows(
    snapshot: Dict[str, Any],
    *,
    gid: str,
    mode: str,
    requirements: Optional[List[Dict[str, Any]]],
    revision: int,
    fetched_at: float,
) -> List[Dict[str, Any]]:
    """Return exact public rows that active orders currently require."""
    rows = extract_market_rows(snapshot, mode=mode, requested_gid=gid, include_ioratio=False)
    wanted = [item for item in (requirements or []) if isinstance(item, dict)]
    if not wanted:
        return []
    selected: List[Dict[str, Any]] = []
    for row in rows:
        for requirement in wanted:
            if str(row.get("market_type") or "").upper() != str(requirement.get("market_type") or "").upper():
                continue
            if str(row.get("period") or "").upper() != str(requirement.get("period") or "").upper():
                continue
            if str(row.get("side") or "").upper() != str(requirement.get("side") or "").upper():
                continue
            if bool(requirement.get("is_corner")):
                requested_gid = str(requirement.get("gid") or gid or "").strip()
                if requested_gid and str(row.get("gid") or "").strip() != requested_gid:
                    continue
            target_line = requirement.get("target_line")
            if target_line not in (None, ""):
                try:
                    if row.get("line") in (None, "") or not line_matches(
                        float(target_line),
                        float(row.get("line")),
                        market_type=str(requirement.get("market_type") or ""),
                        period=str(requirement.get("period") or ""),
                        is_corner=bool(requirement.get("is_corner")),
                    ):
                        continue
                except Exception:
                    continue
            for field_name in ("wtype", "rtype"):
                expected = str(requirement.get(field_name) or "").strip()
                if expected and str(row.get(field_name) or "").strip() != expected:
                    break
            else:
                result = dict(row)
                result["revision"] = int(revision or 0)
                result["fetched_at"] = float(fetched_at or 0.0)
                result["source_gid"] = str(gid or "")
                result["fixture_locator"] = dict(requirement.get("fixture_locator") or {})
                selected.append(result)
                break
    return selected


def _pooled_member_session(member: Dict[str, Any]) -> Any:
    key = f"{str(member.get('id') or member.get('uid') or '').strip()}|{threading.get_ident()}"
    with _SESSION_POOL_LOCK:
        session = _SESSION_POOL.get(key)
        if session is None:
            session = webui_server._make_member_session(
                base_url=str(member.get("base_url") or "").strip(),
                proxy=str(member.get("proxy") or ""),
                cookie=str(member.get("cookie") or ""),
            )
            _SESSION_POOL[key] = session
        return session


def _write_raw_snapshot_sample(
    data_dir: Path,
    *,
    gid: str,
    mode: str,
    member: Dict[str, Any],
    resolved: Dict[str, Any],
    snapshot: Dict[str, Any],
) -> None:
    raw_xml = snapshot.get("raw_xml")
    if not raw_xml:
        return
    debug_dir = Path(data_dir) / "debug"
    debug_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "saved_at": time.time(),
        "gid": str(gid),
        "mode": str(mode),
        "source_member": {"id": str(member.get("id") or ""), "label": str(member.get("label") or member.get("username") or "")},
        "resolved": dict(resolved),
        "raw_xml": str(raw_xml),
        "raw_board": snapshot.get("raw") if isinstance(snapshot.get("raw"), dict) else {},
        "snapshot_diagnostics": snapshot.get("_snapshot_diagnostics") if isinstance(snapshot.get("_snapshot_diagnostics"), dict) else {},
    }
    path = debug_dir / f"snapshot_raw_{str(mode).lower()}_{str(gid)}.json"
    with _RAW_SAMPLE_LOCK:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _readonly_candidates(
    members: List[Dict[str, Any]],
    *,
    preferred_member_id: str = "",
) -> List[Dict[str, Any]]:
    """Return bounded, healthy quote candidates in preference order."""
    builder = getattr(webui_server, "_build_readonly_failover_candidates", None)
    if callable(builder):
        return list(
            builder(
                members,
                preferred_member_id=str(preferred_member_id or ""),
            )
        )
    return [member for member in members if isinstance(member, dict)]


def _run_public_member_hedged(
    *,
    members: List[Dict[str, Any]],
    preferred_member_id: str,
    runner: Any,
    deadline_s: float,
    hedge_delay_s: float = 0.35,
    max_parallel: int = 2,
) -> Dict[str, Any]:
    """Fetch a public snapshot without serially waiting through every member.

    A preferred member gets a short head start. A bounded backup is launched
    when it does not answer promptly; the first valid target-market response is
    published and remaining futures are cancelled/ignored. The runner itself
    receives a short HTTP timeout, while this function enforces the scheduler
    deadline at the orchestration layer.
    """
    candidates = _readonly_candidates(members, preferred_member_id=preferred_member_id)
    if not candidates:
        raise RuntimeError("readonly_pool_empty")
    limit = max(1, min(int(max_parallel or 1), len(candidates), 2))
    started = time.monotonic()
    deadline = started + max(0.1, float(deadline_s or 0.1))
    hedge_delay = max(0.0, min(float(hedge_delay_s or 0.0), max(0.0, deadline - started)))
    pending: Dict[Future, Dict[str, Any]] = {}
    attempted: List[str] = []
    next_index = 0
    last_error: Optional[BaseException] = None
    launched_at: Dict[str, float] = {}

    def launch(member: Dict[str, Any]) -> None:
        nonlocal next_index
        member_id = str(member.get("id") or "").strip()
        if member_id:
            attempted.append(member_id)
        launched_at[member_id] = time.monotonic()
        future = _PUBLIC_MEMBER_EXECUTOR.submit(runner, member)
        pending[future] = member
        next_index += 1

    launch(candidates[next_index])
    hedge_started = False
    while pending:
        remaining = max(0.0, deadline - time.monotonic())
        if remaining <= 0:
            break
        wait_for = remaining
        if not hedge_started and len(pending) < limit:
            wait_for = min(wait_for, hedge_delay)
        done, _ = wait(tuple(pending), timeout=wait_for, return_when=FIRST_COMPLETED)
        if not done:
            if not hedge_started and next_index < len(candidates) and len(pending) < limit:
                launch(candidates[next_index])
                hedge_started = True
                continue
            break
        for future in done:
            member = pending.pop(future)
            try:
                result = future.result()
                if not isinstance(result, dict):
                    raise RuntimeError("snapshot_fetcher_returned_non_dict")
                finished = time.monotonic()
                return {
                    "ok": True,
                    "member": member,
                    "result": result,
                    "fallback_used": len(attempted) > 1,
                    "attempted_member_ids": attempted[:],
                    "failover_count": max(0, len(attempted) - 1),
                    "hedged": len(attempted) > 1,
                    "started_at": started,
                    "finished_at": finished,
                    "upstream_elapsed_ms": round(max(0.0, finished - float(launched_at.get(str(member.get("id") or ""), started))) * 1000.0, 3),
                }
            except BaseException as exc:
                last_error = exc
        if not pending and next_index < len(candidates):
            launch(candidates[next_index])
            hedge_started = True
        elif not hedge_started and next_index < len(candidates) and len(pending) < limit:
            launch(candidates[next_index])
            hedge_started = True

    for future in pending:
        future.cancel()
    if last_error is None:
        last_error = TimeoutError("public_snapshot_deadline_exceeded")
    # Preserve failover telemetry on the exception path. The scheduler may
    # record a failure after hedge futures have been unwound.
    try:
        setattr(last_error, "attempted_member_ids", attempted[:])
        setattr(last_error, "failover_count", max(0, len(attempted) - 1))
        setattr(last_error, "hedged", len(attempted) > 1)
    except Exception:
        pass
    raise last_error


def _resolve_game_cached(
    *,
    session: Any,
    member: Dict[str, Any],
    gid: str,
    mode: str,
    timeout_s: int,
    fixture_locator: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    key = (str(member.get("id") or ""), str(mode or "live"), str(gid or ""))
    now_value = time.time()
    with _RESOLVED_CACHE_LOCK:
        cached = _RESOLVED_CACHE.get(key)
    if cached and now_value - float(cached[0]) <= _RESOLVED_CACHE_TTL_S:
        return dict(cached[1])
    resolved = webui_server._chashui_resolve_game_by_gid(
        session=session,
        member=member,
        requested_gid=gid,
        mode=mode,
        timeout_s=timeout_s,
        fixture_locator=fixture_locator,
    )
    with _RESOLVED_CACHE_LOCK:
        _RESOLVED_CACHE[key] = (now_value, dict(resolved))
    return resolved


def refresh_subscription(
    *,
    data_dir: Path,
    gid: str,
    mode: str,
    member_id: str = "",
    force: bool = True,
    requirements: Optional[List[Dict[str, Any]]] = None,
    fixture_locator: Optional[Dict[str, Any]] = None,
    public_timeout_s: float = 2.0,
    resolve_timeout_s: Optional[float] = None,
    snapshot_timeout_s: Optional[float] = None,
    public_deadline_s: Optional[float] = None,
    hedge_delay_s: float = 0.35,
) -> Dict[str, Any]:
    members = _load_quote_members(data_dir)
    resolve_timeout = max(1.0, float(resolve_timeout_s if resolve_timeout_s is not None else public_timeout_s))
    snapshot_timeout = max(1.0, float(snapshot_timeout_s if snapshot_timeout_s is not None else public_timeout_s))
    # Resolution and board retrieval are independent network phases. The
    # default deadline covers both phases plus a small scheduling margin.
    request_deadline_s = max(
        0.1,
        float(public_deadline_s if public_deadline_s is not None else resolve_timeout + snapshot_timeout + 0.5),
    )

    def fetch_for_member(selected: Dict[str, Any]) -> Dict[str, Any]:
        base_url = str(selected.get("base_url") or "").strip()
        uid = str(selected.get("uid") or "").strip()
        ver = str(selected.get("ver") or "").strip()
        cookie = str(selected.get("cookie") or "").strip()
        if not (base_url and uid and ver and cookie):
            raise RuntimeError("member_missing_uid_cookie_ver")
        session = _pooled_member_session(selected)
        fetch_started_at = float(time.time())
        resolved = _resolve_game_cached(
            session=session,
            member=selected,
            gid=gid,
            mode=mode,
            timeout_s=max(1, int(round(resolve_timeout))),
            fixture_locator=fixture_locator,
        )
        snapshot = webui_server._chashui_get_odds_snapshot_with_empty_retry(
            session=session,
            member=selected,
            resolved=resolved,
            include_raw=True,
            confirm_odds=False,
            timeout_s=max(1, int(round(snapshot_timeout))),
            required_markets=list(requirements or []),
        )
        fetch_finished_at = float(time.time())
        snapshot["_snapshot_fetch_started_at"] = fetch_started_at
        snapshot["_snapshot_fetch_finished_at"] = fetch_finished_at
        snapshot["_snapshot_fetch_elapsed_ms"] = round(max(0.0, fetch_finished_at - fetch_started_at) * 1000.0, 3)
        _write_raw_snapshot_sample(data_dir, gid=gid, mode=mode, member=selected, resolved=resolved, snapshot=snapshot)
        return snapshot

    run = _run_public_member_hedged(
        members=members,
        preferred_member_id=str(member_id or "").strip(),
        runner=fetch_for_member,
        deadline_s=request_deadline_s,
        hedge_delay_s=hedge_delay_s,
        max_parallel=2,
    )
    if not run.get("ok"):
        raise RuntimeError(str(run.get("error") or "readonly_pool_empty"))
    if time.monotonic() - float(run.get("started_at") or time.monotonic()) > request_deadline_s:
        # Do not publish a response that completed after this Market's
        # deadline. The scheduler may already have started a newer attempt.
        exc = TimeoutError("public_snapshot_deadline_exceeded")
        try:
            setattr(exc, "attempted_member_ids", list(run.get("attempted_member_ids") or []))
            setattr(exc, "failover_count", int(run.get("failover_count") or 0))
            setattr(exc, "hedged", bool(run.get("hedged")))
        except Exception:
            pass
        raise exc
    selected = run.get("member") if isinstance(run.get("member"), dict) else {}
    snapshot = run.get("result") if isinstance(run.get("result"), dict) else {}
    snapshot_for_cache = dict(snapshot)
    snapshot_for_cache.pop("raw_xml", None)
    snapshot_for_cache.pop("raw", None)
    cached = load_or_refresh_snapshot(
        data_dir / "odds_snapshot_cache.json",
        gid=gid,
        mode=mode,
        fetcher=lambda: snapshot_for_cache,
        force_refresh=bool(force),
        max_age_s=1.5,
        allow_stale_s=15.0,
        source_member=selected,
    )
    cached["_snapshot_fetch_started_at"] = snapshot.get("_snapshot_fetch_started_at")
    cached["_snapshot_fetch_finished_at"] = snapshot.get("_snapshot_fetch_finished_at")
    cached["_snapshot_fetch_elapsed_ms"] = snapshot.get("_snapshot_fetch_elapsed_ms")
    cached["_snapshot_diagnostics"] = snapshot.get("_snapshot_diagnostics") if isinstance(snapshot.get("_snapshot_diagnostics"), dict) else {}
    cached["_snapshot_fetch_filter"] = snapshot.get("snapshot_fetch_filter")
    cached["_snapshot_target_missing"] = bool(snapshot.get("snapshot_fetch_target_missing"))
    cached["_watch_failover_count"] = int(run.get("failover_count") or 0)
    cached["_watch_attempted_member_ids"] = list(run.get("attempted_member_ids") or [])
    cached["_watch_hedged"] = bool(run.get("hedged"))
    cached["_watch_upstream_elapsed_ms"] = run.get("upstream_elapsed_ms")
    cached["_watch_public_started_at"] = run.get("started_at")
    cached["_watch_public_finished_at"] = run.get("finished_at")

    quote_cache_path = data_dir / "member_ioratio_cache.json"
    quote_refresh_rows: List[Dict[str, Any]] = []

    # Preserve the selected source member's own quote separately. The public
    # cache has already stripped all odd/ioratio values at this point.
    try:
        stored = store_member_snapshot_ioratios(
            quote_cache_path,
            member=selected,
            snapshot=snapshot,
            mode=mode,
            requested_gid=gid,
            public_snapshot_revision=int(cached.get("revision") or 0),
            fetched_at=float(snapshot.get("_snapshot_fetch_finished_at") or time.time()),
        )
        quote_refresh_rows.append(
            {
                "member_id": str(selected.get("id") or ""),
                "member": str(selected.get("label") or selected.get("username") or ""),
                "ok": True,
                "stored_count": int(stored.get("stored_count") or 0),
                "reused_public_fetch": True,
            }
        )
    except Exception as exc:
        quote_refresh_rows.append(
            {
                "member_id": str(selected.get("id") or ""),
                "member": str(selected.get("label") or selected.get("username") or ""),
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "reused_public_fetch": True,
            }
        )

    execution_members = _execution_quote_members(members)
    source_member_id = str(selected.get("id") or "").strip()
    execution_members = [
        member for member in execution_members
        if str(member.get("id") or "").strip() != source_member_id
    ]

    def refresh_member_ioratio(execution_member: Dict[str, Any]) -> Dict[str, Any]:
        started_at = float(time.time())
        member_snapshot: Dict[str, Any] = {}
        try:
            session = _pooled_member_session(execution_member)
            resolved = _resolve_game_cached(
                session=session,
                member=execution_member,
                gid=gid,
                mode=mode,
                timeout_s=10,
                fixture_locator=fixture_locator,
            )
            member_snapshot = webui_server._chashui_get_odds_snapshot_with_empty_retry(
                session=session,
                member=execution_member,
                resolved=resolved,
                include_raw=False,
                confirm_odds=False,
                timeout_s=10,
                required_markets=list(requirements or []),
            )
            finished_at = float(time.time())
            stored = store_member_snapshot_ioratios(
                quote_cache_path,
                member=execution_member,
                snapshot=member_snapshot,
                mode=mode,
                requested_gid=gid,
                public_snapshot_revision=int(cached.get("revision") or 0),
                fetched_at=finished_at,
            )
            return {
                "member_id": str(execution_member.get("id") or ""),
                "member": str(execution_member.get("label") or execution_member.get("username") or ""),
                "ok": True,
                "stored_count": int(stored.get("stored_count") or 0),
                "started_at": started_at,
                "finished_at": finished_at,
                "elapsed_ms": round(max(0.0, finished_at - started_at) * 1000.0, 3),
                "reused_public_fetch": False,
            }
        except Exception as exc:
            finished_at = float(time.time())
            return {
                "member_id": str(execution_member.get("id") or ""),
                "member": str(execution_member.get("label") or execution_member.get("username") or ""),
                "ok": False,
                "stored_count": 0,
                "started_at": started_at,
                "finished_at": finished_at,
                "elapsed_ms": round(max(0.0, finished_at - started_at) * 1000.0, 3),
                "error": f"{type(exc).__name__}: {exc}",
                "reused_public_fetch": False,
            }

    # Register exact execution-member rows with the independent quote
    # scheduler.  Public snapshot publication never waits for these tasks.
    for execution_member in execution_members:
        rows = _requested_market_rows(
            cached,
            gid=gid,
            mode=mode,
            requirements=requirements,
            revision=int(cached.get("revision") or 0),
            fetched_at=float(cached.get("fetched_at") or time.time()),
        )
        for row in rows:
            request = {
                "request_id": f"watch:{execution_member.get('id')}:{row.get('market_row_fingerprint')}:{time.time_ns()}",
                "member_id": str(execution_member.get("id") or ""),
                "mode": str(mode or "live").strip().lower() or "live",
                "created_at": float(time.time()),
                "source_gid": str(gid or ""),
                "market_row": row,
            }
            state = _MEMBER_QUOTE_SCHEDULER.register(data_dir, request)
            quote_refresh_rows.append(
                {
                    "member_id": str(execution_member.get("id") or ""),
                    "member": str(execution_member.get("label") or execution_member.get("username") or ""),
                    "ok": bool(state is not None),
                    "scheduled": bool(state is not None),
                    "market_row_fingerprint": str(row.get("market_row_fingerprint") or ""),
                    "priority_refresh": False,
                }
            )

    _MEMBER_QUOTE_SCHEDULER.start_due(float(time.time()))

    cached["_member_ioratio_refresh"] = quote_refresh_rows
    cached["_member_ioratio_success_count"] = sum(1 for row in quote_refresh_rows if row.get("ok"))
    cached["_member_ioratio_failure_count"] = sum(1 for row in quote_refresh_rows if not row.get("ok"))
    return cached


def _classify_snapshot_error(exc: BaseException) -> str:
    text = str(exc or "")
    lowered = text.lower()
    if "502" in lowered or "bad gateway" in lowered:
        return "UPSTREAM_502"
    if "proxy" in lowered:
        return "PROXY_ERROR"
    if "remote end closed" in lowered or "connection aborted" in lowered or "connection reset" in lowered:
        return "CONNECTION_ERROR"
    if "timed out" in lowered or "timeout" in lowered or "deadline" in lowered:
        return "READ_TIMEOUT"
    if "game_not_found" in lowered:
        return "GAME_NOT_FOUND"
    return type(exc).__name__ or "UNKNOWN_ERROR"


def _snapshot_failure_summary(counts: Dict[str, int]) -> str:
    """Render bounded, human-readable watcher diagnostics for the WebUI/logs."""
    labels = {
        "READ_TIMEOUT": "盘口采集超时",
        "GAME_NOT_FOUND": "未找到赛事",
        "TARGET_LINE_MISSING": "目标盘口不存在",
        "CONNECTION_ERROR": "连接中断",
        "PROXY_ERROR": "代理错误",
        "UPSTREAM_502": "上游502",
    }
    parts = [
        f"{labels.get(code, code)} {int(count)} 次"
        for code, count in counts.items()
        if int(count or 0) > 0
    ]
    return "，".join(parts) if parts else "盘口采集失败"


@dataclass
class ActiveMarketState:
    key: str
    item: Dict[str, Any]
    requested_interval_ms: float
    deadline_ms: float
    next_refresh_at: float = 0.0
    last_attempt_at: Optional[float] = None
    last_success_at: Optional[float] = None
    last_failure_at: Optional[float] = None
    last_fetched_at: Optional[float] = None
    last_revision: int = 0
    inflight: Optional[Future] = None
    deadline_at: Optional[float] = None
    consecutive_failures: int = 0
    queue_delay_ms: float = 0.0
    deadline_recorded: bool = False
    attempt_no: int = 0
    previous_attempt_at: Optional[float] = None
    last_event_at: Optional[float] = None
    failure_counts: Dict[str, int] = field(default_factory=dict)


class ActiveMarketScheduler:
    """Continuous per-market scheduler with no global completion barrier."""

    def __init__(
        self,
        *,
        data_dir: Path,
        member_id: str,
        max_workers: int,
        requested_interval_ms: float = 400.0,
        deadline_ms: float = 6000.0,
        public_timeout_s: float = 2.0,
        resolve_timeout_s: float = 2.0,
        snapshot_timeout_s: float = 2.5,
        hedge_delay_s: float = 0.35,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.member_id = str(member_id or "")
        self.requested_interval_ms = max(50.0, float(requested_interval_ms or 400.0))
        self.deadline_ms = max(100.0, float(deadline_ms or 6000.0))
        self.public_timeout_s = max(1.0, float(public_timeout_s or 2.0))
        self.resolve_timeout_s = max(1.0, float(resolve_timeout_s or self.public_timeout_s))
        self.snapshot_timeout_s = max(1.0, float(snapshot_timeout_s or self.public_timeout_s))
        self.hedge_delay_s = max(0.0, float(hedge_delay_s or 0.0))
        self.max_workers = max(1, min(8, int(max_workers or 1)))
        self.states: Dict[str, ActiveMarketState] = {}
        self.executor = ThreadPoolExecutor(
            max_workers=self.max_workers,
            thread_name_prefix="snapshot-market",
        )
        self._event_no = 0

    def close(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=True)

    def sync(self, markets: List[Dict[str, Any]], now: Optional[float] = None) -> None:
        now_value = float(time.time() if now is None else now)
        desired: Dict[str, Dict[str, Any]] = {
            str(item.get("active_market_key") or active_market_key(item, item.get("requirement") or {})): item
            for item in markets
            if str(item.get("active_market_key") or "").strip()
        }
        for key in list(self.states):
            if key not in desired:
                state = self.states.pop(key)
                if state.inflight is not None:
                    state.inflight.cancel()
        for key, item in desired.items():
            state = self.states.get(key)
            if state is None:
                state = ActiveMarketState(
                    key=key,
                    item=dict(item),
                    requested_interval_ms=self.requested_interval_ms,
                    deadline_ms=self.deadline_ms,
                    next_refresh_at=now_value,
                )
                self.states[key] = state
            else:
                state.item = dict(item)

    def _submit(self, state: ActiveMarketState, now_value: float) -> None:
        state.previous_attempt_at = state.last_attempt_at
        state.last_attempt_at = now_value
        state.attempt_no += 1
        state.queue_delay_ms = round(max(0.0, now_value - state.next_refresh_at) * 1000.0, 3)
        state.deadline_at = now_value + state.deadline_ms / 1000.0
        state.deadline_recorded = False
        state.inflight = self.executor.submit(
            refresh_subscription,
            data_dir=self.data_dir,
            gid=str(state.item.get("gid") or ""),
            mode=str(state.item.get("mode") or "live"),
            member_id=self.member_id,
            force=True,
            requirements=state.item.get("requirements") if isinstance(state.item.get("requirements"), list) else [],
            fixture_locator=state.item.get("fixture_locator") if isinstance(state.item.get("fixture_locator"), dict) else {},
            public_timeout_s=self.public_timeout_s,
            resolve_timeout_s=self.resolve_timeout_s,
            snapshot_timeout_s=self.snapshot_timeout_s,
            public_deadline_s=self.deadline_ms / 1000.0,
            hedge_delay_s=self.hedge_delay_s,
        )

    def _base_event(self, state: ActiveMarketState, now_value: float) -> Dict[str, Any]:
        actual_interval = None
        if state.previous_attempt_at is not None and state.last_attempt_at is not None:
            actual_interval = round(max(0.0, state.last_attempt_at - state.previous_attempt_at) * 1000.0, 3)
        requirement = state.item.get("requirement") if isinstance(state.item.get("requirement"), dict) else {}
        return {
            "ts": now_value,
            "active_market_key": state.key,
            "gid": str(state.item.get("gid") or ""),
            "mode": str(state.item.get("mode") or "live"),
            "market_type": requirement.get("market_type"),
            "period": requirement.get("period"),
            "side": requirement.get("side"),
            "target_line": requirement.get("target_line"),
            "requested_interval_ms": round(state.requested_interval_ms, 3),
            "actual_refresh_interval_ms": actual_interval,
            "refresh_started_at": state.last_attempt_at,
            "queue_delay_ms": state.queue_delay_ms,
            "deadline_at": state.deadline_at,
            "active_match_count": len({str(s.item.get("gid") or "") for s in self.states.values()}),
            "requirements": state.item.get("requirements") or [],
            "attempt_no": state.attempt_no,
        }

    def _record_failure(self, state: ActiveMarketState, now_value: float, exc: BaseException, *, deadline: bool = False) -> None:
        state.last_failure_at = now_value
        state.consecutive_failures += 1
        error_class = "READ_TIMEOUT" if deadline else _classify_snapshot_error(exc)
        state.failure_counts[error_class] = int(state.failure_counts.get(error_class) or 0) + 1
        attempted_member_ids = list(getattr(exc, "attempted_member_ids", []) or [])
        failover_count = int(getattr(exc, "failover_count", max(0, len(attempted_member_ids) - 1)) or 0)
        event = self._base_event(state, now_value)
        event.update({
            "finished_at": now_value,
            "refresh_finished_at": now_value,
            "refresh_elapsed_ms": round(max(0.0, now_value - float(state.last_attempt_at or now_value)) * 1000.0, 3),
            "upstream_elapsed_ms": None,
            "publish_at": None,
            "ok": False,
            "error_class": error_class,
            "error_code": error_class,
            "error": str(exc or "public_snapshot_deadline_exceeded")[:500],
            "deadline_exceeded": bool(deadline),
            "consecutive_failures": state.consecutive_failures,
            "failover_count": failover_count,
            "hedged": bool(getattr(exc, "hedged", len(attempted_member_ids) > 1)),
            "attempted_member_ids": attempted_member_ids,
            "failure_counts": dict(state.failure_counts),
            "failure_summary": _snapshot_failure_summary(state.failure_counts),
            "submission": "none",
        })
        record_snapshot_watch_event(self.data_dir, event)
    def _record_success(self, state: ActiveMarketState, now_value: float, rec: Dict[str, Any], *, late: bool = False) -> None:
        finished_at = float(rec.get("_snapshot_fetch_finished_at") or now_value)
        fetched_at = float(rec.get("fetched_at") or finished_at)
        diagnostics = rec.get("_snapshot_diagnostics") if isinstance(rec.get("_snapshot_diagnostics"), dict) else {}
        source = rec.get("source_member") if isinstance(rec.get("source_member"), dict) else {}
        requirement = state.item.get("requirement") if isinstance(state.item.get("requirement"), dict) else {}
        target_missing = bool(state.item.get("requirements")) and diagnostics.get("target_line_found") is not True
        state.last_success_at = now_value if not target_missing else state.last_success_at
        state.last_fetched_at = fetched_at
        state.last_revision = int(rec.get("revision") or 0)
        if not target_missing:
            state.consecutive_failures = 0
        event = self._base_event(state, now_value)
        event.update({
            "finished_at": now_value,
            "refresh_finished_at": now_value,
            "refresh_elapsed_ms": round(max(0.0, now_value - float(state.last_attempt_at or now_value)) * 1000.0, 3),
            "fetch_started_at": rec.get("_snapshot_fetch_started_at"),
            "fetch_finished_at": rec.get("_snapshot_fetch_finished_at"),
            "fetch_elapsed_ms": rec.get("_snapshot_fetch_elapsed_ms"),
            "upstream_elapsed_ms": rec.get("_watch_upstream_elapsed_ms") or rec.get("_snapshot_fetch_elapsed_ms"),
            "source_member_id": str(source.get("id") or ""),
            "source_member": str(source.get("label") or ""),
            "failover_count": int(rec.get("_watch_failover_count") or 0),
            "hedged": bool(rec.get("_watch_hedged")),
            "revision": state.last_revision,
            "snapshot_fetched_at": fetched_at,
            "snapshot_age_ms": int(rec.get("age_ms") or 0),
            "last_success_age_ms": round(max(0.0, now_value - fetched_at) * 1000.0, 3),
            "snapshot_fetch_filter": rec.get("_snapshot_fetch_filter"),
            "market_diagnostics": diagnostics,
            "ok": True,
            "target_line_found": not target_missing,
            "error_code": "TARGET_LINE_MISSING" if target_missing else None,
            "deadline_exceeded": bool(late),
            "publish_at": fetched_at,
            "consecutive_failures": state.consecutive_failures,
        })
        record_snapshot_watch_event(self.data_dir, event)
        if not target_missing:
            # The follow engine consumes this tiny event stream to wake only
            # jobs waiting for this exact market; metrics remain diagnostic.
            record_snapshot_publish_event(
                self.data_dir,
                {
                    "ts": now_value,
                    "publish_at": fetched_at,
                    "active_market_key": state.key,
                    "gid": str(state.item.get("gid") or ""),
                    "mode": str(state.item.get("mode") or "live"),
                    "revision": state.last_revision,
                    "market_row_fingerprint": str(
                        (diagnostics.get("market_row_fingerprint") if isinstance(diagnostics, dict) else "") or ""
                    ),
                    "market_type": requirement.get("market_type"),
                    "period": requirement.get("period"),
                    "side": requirement.get("side"),
                    "target_line": requirement.get("target_line"),
                },
            )
        print(
            f"snapshot market={state.key} revision={state.last_revision} age_ms={event['snapshot_age_ms']} "
            f"interval_ms={event.get('actual_refresh_interval_ms')} deadline_exceeded={late}",
            flush=True,
        )

    def poll(self, now: Optional[float] = None) -> None:
        now_value = float(time.time() if now is None else now)
        for state in list(self.states.values()):
            future = state.inflight
            if future is None:
                continue
            if not future.done():
                if state.deadline_at and now_value >= state.deadline_at and not state.deadline_recorded:
                    state.deadline_recorded = True
                    self._record_failure(state, now_value, TimeoutError("public_snapshot_deadline_exceeded"), deadline=True)
                    # Keep ownership of the underlying request until the HTTP
                    # call actually unwinds. Detaching it would allow a second
                    # request for the same market while the first one is still
                    # consuming an executor slot and upstream capacity.
                continue
            state.inflight = None
            late = bool(state.deadline_recorded) or bool(state.deadline_at and now_value >= state.deadline_at)
            state.deadline_at = None
            if late:
                if not state.deadline_recorded:
                    self._record_failure(
                        state,
                        now_value,
                        TimeoutError("public_snapshot_deadline_exceeded"),
                        deadline=True,
                    )
                backoff = min(12.0, 2.0 ** max(0, state.consecutive_failures - 1))
                state.next_refresh_at = now_value + (state.requested_interval_ms / 1000.0) * backoff
                continue
            try:
                rec = future.result()
                self._record_success(state, now_value, rec if isinstance(rec, dict) else {}, late=False)
            except BaseException as exc:
                if not state.deadline_recorded:
                    self._record_failure(state, now_value, exc)
            backoff = min(12.0, 2.0 ** max(0, state.consecutive_failures - 1))
            state.next_refresh_at = now_value + (state.requested_interval_ms / 1000.0) * backoff

    def start_due(self, now: Optional[float] = None) -> int:
        now_value = float(time.time() if now is None else now)
        started = 0
        active = sum(1 for state in self.states.values() if state.inflight is not None and not state.inflight.done())
        available = max(0, self.max_workers - active)
        for state in sorted(
            self.states.values(),
            key=lambda item: (-int(item.item.get("priority") or 0), item.next_refresh_at, item.key),
        ):
            if started >= available:
                break
            if state.inflight is not None or now_value < state.next_refresh_at:
                continue
            self._submit(state, now_value)
            started += 1
        return started

    def next_wakeup(self, now: Optional[float] = None, default_s: float = 0.05) -> float:
        now_value = float(time.time() if now is None else now)
        due = [state.next_refresh_at for state in self.states.values() if state.inflight is None]
        if not due:
            return max(0.01, float(default_s))
        return max(0.01, min(float(default_s), max(0.0, min(due) - now_value)))


def main() -> int:
    parser = argparse.ArgumentParser(description="Shared live odds snapshot worker")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--orders-dir", default="orders")
    # Kept for CLI compatibility; active markets use target_interval_ms below.
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--active-seconds", type=float, default=120.0)
    parser.add_argument("--member-id", default="")
    parser.add_argument("--max-workers", type=int, default=4)
    parser.add_argument("--target-interval-ms", type=float, default=400.0)
    parser.add_argument("--market-deadline-ms", type=float, default=6000.0)
    parser.add_argument("--public-timeout-s", type=float, default=2.0)
    parser.add_argument("--resolve-timeout-s", type=float, default=2.0)
    parser.add_argument("--snapshot-timeout-s", type=float, default=2.5)
    parser.add_argument("--hedge-delay-ms", type=float, default=350.0)
    parser.add_argument("--scheduler-tick-ms", type=float, default=50.0)
    args = parser.parse_args()
    data_dir = Path(args.data_dir)
    orders_dir = Path(args.orders_dir)
    instance_lock = SingleInstanceFileLock(data_dir / ".odds_snapshot_watch.lock")
    if not instance_lock.acquire():
        print("odds_snapshot_watch already running for this data directory", flush=True)
        return 2
    global_lock = SingleInstanceFileLock(_global_snapshot_lock_path())
    if not global_lock.acquire():
        instance_lock.release()
        print("odds_snapshot_watch already running for this user", flush=True)
        return 2
    state_path = data_dir / "follow_state.json"
    print(
        f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] odds_snapshot_watch started "
        f"scheduler=per_market target_interval_ms={float(args.target_interval_ms):.0f} "
        f"deadline_ms={float(args.market_deadline_ms):.0f} max_workers={int(args.max_workers)}",
        flush=True,
    )
    scheduler = ActiveMarketScheduler(
        data_dir=data_dir,
        member_id=args.member_id,
        max_workers=args.max_workers,
        requested_interval_ms=args.target_interval_ms,
        deadline_ms=args.market_deadline_ms,
        public_timeout_s=args.public_timeout_s,
        resolve_timeout_s=args.resolve_timeout_s,
        snapshot_timeout_s=args.snapshot_timeout_s,
        hedge_delay_s=max(0.0, float(args.hedge_delay_ms) / 1000.0),
    )
    last_demand_scan = 0.0
    try:
        while True:
            now_value = float(time.time())
            priority_scheduled = _schedule_priority_refreshes(data_dir)
            if priority_scheduled:
                print(f"priority member ioratio refresh scheduled={priority_scheduled}", flush=True)
            now_value = float(time.time())
            _MEMBER_QUOTE_SCHEDULER.poll(now_value)
            _MEMBER_QUOTE_SCHEDULER.start_due(now_value)
            if now_value - last_demand_scan >= 0.20:
                demands = collect_market_demands(
                    orders_dir=orders_dir,
                    state_path=state_path,
                    now=now_value,
                    active_seconds=float(args.active_seconds),
                )
                scheduler.sync(flatten_active_markets(demands), now=now_value)
                last_demand_scan = now_value
            scheduler.poll(now_value)
            scheduler.start_due(now_value)
            time.sleep(scheduler.next_wakeup(now_value, default_s=max(0.01, float(args.scheduler_tick_ms) / 1000.0)))
    finally:
        _MEMBER_QUOTE_SCHEDULER.close()
        scheduler.close()
        global_lock.release()
        instance_lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
