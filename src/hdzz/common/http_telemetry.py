"""Low-overhead, best-effort HTTP telemetry for transform requests.

This module is deliberately observational.  It never retries, changes a
request, or raises an exception to a caller.  Records are sent to a bounded
daemon queue and may be dropped when the process is under pressure.
"""

from __future__ import annotations

import hashlib
import heapq
import json
import os
import platform
import queue
import re
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any, Dict, Mapping, Optional
from urllib.parse import urlsplit


def _text(value: Any) -> str:
    return str(value or "").strip()


def _sha(value: Any) -> str:
    return hashlib.sha256(_text(value).encode("utf-8", "ignore")).hexdigest()


def _url_identity(url: Any) -> Dict[str, Any]:
    try:
        parsed = urlsplit(_text(url))
        return {"scheme": _text(parsed.scheme).lower(), "host_sha256": _sha(parsed.hostname or "") if parsed.hostname else "", "port": parsed.port, "path": parsed.path or "/"}
    except Exception:
        return {"scheme": "", "host_sha256": "", "port": None, "path": ""}


def _safe_exception(exc: Any) -> str:
    return type(exc).__name__ if isinstance(exc, BaseException) else _text(exc)


def _body_meta(body: Any) -> Dict[str, Any]:
    raw = _text(body)
    return {"response_body_length": len(raw.encode("utf-8", "ignore")), "response_body_sha256": _sha(raw) if raw else ""}


class RequestHandle:
    def __init__(self, manager: "TelemetryManager", record: Dict[str, Any]) -> None:
        self._manager = manager
        self._record = record
        self._finished = False
        self._lock = threading.Lock()

    def finish(self, response: Any = None, exception: Any = None, *, body: Any = None, response_meta: Optional[Mapping[str, Any]] = None) -> None:
        with self._lock:
            if self._finished:
                return
            self._finished = True
        try:
            rec = dict(self._record)
            finished = time.time()
            rec["response_finished_at"] = finished
            rec["total_ms"] = round(max(0.0, finished - float(rec.get("request_started_at") or finished)) * 1000.0, 3)
            rec["ttfb_ms"] = rec["total_ms"]
            meta = dict(response_meta or {})
            status = meta.get("http_status")
            headers = meta.get("headers") if isinstance(meta.get("headers"), Mapping) else {}
            if response is not None:
                try:
                    status = int(getattr(response, "status_code", status) or 0)
                except Exception:
                    pass
                try:
                    headers = dict(getattr(response, "headers", headers) or headers or {})
                except Exception:
                    pass
                if body is None:
                    try:
                        body = getattr(response, "text", "")
                    except Exception:
                        body = ""
                try:
                    conn = getattr(getattr(response, "raw", None), "_connection", None)
                    sock = getattr(conn, "sock", None)
                    if sock is not None:
                        local = sock.getsockname()
                        peer = sock.getpeername()
                        rec["app_peer_ip_hash"] = _sha(peer[0]) if peer else None
                        rec["local_port_hash"] = _sha(local[1]) if local else None
                        rec["peer_port"] = int(peer[1]) if peer else None
                    rec["http_version"] = getattr(getattr(response, "raw", None), "version", None)
                    rec["connection_reused"] = bool(getattr(conn, "sock", None)) if conn is not None else None
                except Exception:
                    pass
            rec["http_status"] = int(status) if str(status or "").isdigit() else status
            rec["response_headers_at"] = meta.get("response_headers_at") or (finished if status is not None else None)
            rec["response_content_type"] = _text(meta.get("response_content_type") or headers.get("Content-Type") or headers.get("content-type"))
            rec["response_content_length"] = meta.get("response_content_length") or headers.get("Content-Length") or headers.get("content-length")
            rec["server"] = _text(meta.get("response_server") or headers.get("Server") or headers.get("server")) or None
            rec["via"] = _text(meta.get("response_via") or headers.get("Via") or headers.get("via")) or None
            rec["connection"] = _text(headers.get("Connection") or headers.get("connection")) or None
            rec.update(_body_meta(body if body is not None else meta.get("response_body")))
            if meta.get("response_body_length") is not None:
                rec["response_body_length"] = meta.get("response_body_length")
            if meta.get("response_body_sha256"):
                rec["response_body_sha256"] = _text(meta.get("response_body_sha256"))
            body_text = _text(body if body is not None else meta.get("response_body"))
            code_match = re.search(r"<code>\s*([^<]+)", body_text, re.IGNORECASE)
            error_match = re.search(r"<errormsg>\s*([^<]*)", body_text, re.IGNORECASE)
            ticket_match = re.search(r"<(?:ticket_id|ticketId|ticket)>\s*([^<]+)", body_text, re.IGNORECASE)
            rec["xml_code"] = _text(code_match.group(1)) if code_match else None
            rec["xml_errormsg"] = _text(error_match.group(1)) if error_match else None
            rec["ticket_present"] = bool(ticket_match and _text(ticket_match.group(1)))
            rec["control"] = bool(
                (rec.get("request_type") == "FT_order_view" and rec.get("xml_code") == "501")
                or (rec.get("request_type") == "FT_bet" and rec.get("xml_code") == "560" and rec.get("ticket_present"))
            )
            rec["exception_type"] = _safe_exception(exception) if exception is not None else _text(meta.get("exception_type")) or None
            rec["blocked_502"] = self._manager._is_blocked_502(rec)
            rec.setdefault("app_peer_ip_hash", None)
            rec.setdefault("local_port_hash", None)
            rec.setdefault("peer_port", None)
            rec.setdefault("http_version", None)
            rec.setdefault("connection_reused", None)
            rec.setdefault("shadowrocket_remote_peer_hash", None)
            self._manager._finish(rec)
        except Exception:
            self._manager._drop_count += 1


class TelemetryManager:
    def __init__(self, *, incident_queue_capacity: int = 256) -> None:
        self._lock = threading.RLock()
        self._queue: "queue.Queue[Any]" = queue.Queue(maxsize=2048)
        self._ring: deque[Dict[str, Any]] = deque(maxlen=4096)
        self._request_times: deque[float] = deque(maxlen=10000)
        self._session_times: deque[float] = deque(maxlen=10000)
        self._inflight: Dict[str, int] = {"critical": 0, "context": 0, "bet": 0, "snapshot": 0, "member_quote": 0, "settlement": 0, "total_transform": 0}
        self._data_dir: Optional[Path] = None
        self._started = False
        self._drop_count = 0
        self._incidents: Dict[str, float] = {}
        # Incident finalization is deliberately separate from the request
        # writer queue.  A single condition-driven worker replaces the old
        # one-thread-per-502 Timer implementation.
        self._incident_capacity = max(1, int(incident_queue_capacity or 256))
        self._incident_cv = threading.Condition(threading.RLock())
        self._incident_heap: list[tuple[float, int, str]] = []
        self._incident_groups: Dict[str, Dict[str, Any]] = {}
        self._incident_sequence = 0
        self._incident_stop = False
        self._incident_thread: Optional[threading.Thread] = None
        self._dropped_incident_count = 0
        self._dropped_incident_reasons: Dict[str, int] = {}
        self._telemetry_error_count = 0

    def configure(self, data_dir: Optional[Any] = None) -> "TelemetryManager":
        with self._lock:
            path = Path(data_dir or os.environ.get("HDZZ_DATA_DIR") or (Path.cwd() / "data")).expanduser().resolve()
            if self._data_dir == path and self._started:
                return self
            self._data_dir = path
            path.mkdir(parents=True, exist_ok=True)
            if not self._started:
                self._started = True
                threading.Thread(target=self._writer, name="hdzz-http-telemetry", daemon=True).start()
                self._incident_thread = threading.Thread(
                    target=self._incident_worker,
                    name="hdzz-http-incident-finalizer",
                    daemon=True,
                )
                self._incident_thread.start()
                self._enqueue(("record", {"event": "runtime_start", "timestamp": time.time(), "pid": os.getpid(), "python": platform.python_version(), "ssl": _ssl_info()}))
        return self

    @property
    def data_dir(self) -> Optional[Path]:
        return self._data_dir

    def _writer(self) -> None:
        while True:
            try:
                item = self._queue.get()
                if item is None:
                    return
                kind, payload = item
                if kind == "record":
                    path = (self._data_dir or Path.cwd() / "data") / "http_network_telemetry.jsonl"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        if path.exists() and path.stat().st_size >= 20 * 1024 * 1024:
                            backup = path.with_suffix(path.suffix + ".1")
                            if backup.exists():
                                backup.unlink()
                            path.replace(backup)
                    except Exception:
                        pass
                    with path.open("a", encoding="utf-8") as fh:
                        fh.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
                elif kind == "incident":
                    incident_id = _text(payload.get("incident_id"))
                    directory = (self._data_dir or Path.cwd() / "data") / "502_incidents"
                    directory.mkdir(parents=True, exist_ok=True)
                    path = directory / f"{incident_id}.json"
                    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception as exc:
                self._record_telemetry_error("writer", exc)
            finally:
                try:
                    self._queue.task_done()
                except Exception:
                    pass

    def _enqueue(self, item: Any) -> None:
        try:
            self._queue.put_nowait(item)
        except Exception:
            with self._lock:
                self._drop_count += 1
            if isinstance(item, tuple) and item and item[0] == "incident":
                self._note_incident_drop("telemetry_queue_full")

    def _record_telemetry_error(self, reason: str, exc: Any = None) -> None:
        """Count telemetry failures without ever touching request control flow."""

        with self._lock:
            self._telemetry_error_count += 1

    def _note_incident_drop(self, reason: str) -> None:
        with self._incident_cv:
            self._dropped_incident_count += 1
            self._dropped_incident_reasons[reason] = int(self._dropped_incident_reasons.get(reason) or 0) + 1

    def _incident_group_key(self, record: Mapping[str, Any]) -> str:
        # Host and blocked-body hash are already non-sensitive telemetry
        # identities.  Keeping the key at this granularity merges one common
        # upstream failure while preserving every original HTTP record.
        return "|".join(
            (
                _text(record.get("host")),
                _text(record.get("response_body_sha256")),
                _text(record.get("http_status")),
            )
        )

    def _schedule_incident(self, record: Mapping[str, Any]) -> None:
        """Queue a bounded, mergeable incident without blocking the caller."""

        try:
            triggered_at = float(record.get("response_finished_at") or time.time())
            key = self._incident_group_key(record)
            request_id = _text(record.get("request_id"))
            request_type = _text(record.get("request_type")) or "unknown"
            member_hash = _text(record.get("member_hash"))
            with self._incident_cv:
                group = self._incident_groups.get(key)
                # A group remains open for ten seconds after its newest
                # trigger.  Later 502s in that window extend the post-window
                # and update the aggregate, rather than creating another
                # delayed worker or another incident file.
                if group is not None and triggered_at - float(group.get("last_triggered_at") or 0.0) <= 10.0:
                    group["last_triggered_at"] = max(float(group.get("last_triggered_at") or 0.0), triggered_at)
                    group["due_at"] = max(float(group.get("due_at") or 0.0), triggered_at + 10.0)
                    group["trigger_count"] = int(group.get("trigger_count") or 0) + 1
                    group["last_trigger_request_id"] = request_id or group.get("last_trigger_request_id")
                    group.setdefault("request_types", set()).add(request_type)
                    if member_hash:
                        group.setdefault("members", set()).add(member_hash)
                    # Keep the original heap entry.  The worker re-checks
                    # ``due_at`` when it wakes, so merges do not add one
                    # stale priority-queue item per 502 response.
                    self._incident_cv.notify()
                    return

                if len(self._incident_groups) >= self._incident_capacity:
                    self._note_incident_drop("incident_queue_full")
                    return

                incident_id = f"502-{time.strftime('%Y%m%dT%H%M%S', time.localtime(triggered_at))}-group-{uuid.uuid4().hex}"
                self._incident_sequence += 1
                group = {
                    "incident_id": incident_id,
                    "triggered_at": triggered_at,
                    "last_triggered_at": triggered_at,
                    "due_at": triggered_at + 10.0,
                    "sequence": self._incident_sequence,
                    "trigger_count": 1,
                    "trigger_request_id": request_id,
                    "last_trigger_request_id": request_id,
                    "request_types": {request_type},
                    "members": {member_hash} if member_hash else set(),
                    "host": _text(record.get("host")),
                    "blocked_body_sha256": _text(record.get("response_body_sha256")),
                    "environment": None,
                }
                self._incident_groups[key] = group
                self._incidents[incident_id] = triggered_at
                heapq.heappush(self._incident_heap, (float(group["due_at"]), self._incident_sequence, key))
                self._incident_cv.notify()
        except Exception as exc:
            self._record_telemetry_error("incident_schedule", exc)

    def _incident_worker(self) -> None:
        while True:
            group: Optional[Dict[str, Any]] = None
            with self._incident_cv:
                while not self._incident_stop and not self._incident_heap:
                    self._incident_cv.wait()
                if self._incident_stop:
                    return
                due_at, sequence, key = self._incident_heap[0]
                remaining = float(due_at) - time.time()
                if remaining > 0:
                    self._incident_cv.wait(timeout=remaining)
                    continue
                heapq.heappop(self._incident_heap)
                candidate = self._incident_groups.get(key)
                if candidate is None or int(candidate.get("sequence") or 0) != int(sequence):
                    continue
                current_due = float(candidate.get("due_at") or due_at)
                if current_due > time.time():
                    # A merge extended the post-window after this entry was
                    # inserted.  Requeue exactly one current entry.
                    heapq.heappush(self._incident_heap, (current_due, sequence, key))
                    continue
                group = self._incident_groups.pop(key, None)
                if group is not None:
                    self._incidents.pop(_text(group.get("incident_id")), None)
            if group is None:
                continue
            try:
                payload = {
                    "incident_id": group.get("incident_id"),
                    "triggered_at": group.get("triggered_at"),
                    "last_triggered_at": group.get("last_triggered_at"),
                    "trigger_request_id": group.get("trigger_request_id"),
                    "last_trigger_request_id": group.get("last_trigger_request_id"),
                    "pre_window_seconds": 30,
                    "post_window_seconds": 10,
                    "trigger_count": int(group.get("trigger_count") or 0),
                    "affected_request_types": sorted(group.get("request_types") or set()),
                    "affected_members": sorted(group.get("members") or set()),
                    "host": group.get("host"),
                    "blocked_body_sha256": group.get("blocked_body_sha256"),
                }
                self._finalize_incident(str(payload.get("incident_id") or ""), payload)
            except Exception as exc:
                self._record_telemetry_error("incident_worker", exc)

    def close(self) -> None:
        """Stop the incident worker for deterministic tests/process shutdown."""

        with self._incident_cv:
            self._incident_stop = True
            self._incident_heap.clear()
            self._incident_groups.clear()
            self._incident_cv.notify_all()
        worker = self._incident_thread
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=1.0)

    def close_writer(self) -> None:
        """Best-effort writer shutdown hook for embedding/tests."""

        try:
            self._queue.put_nowait(None)
        except Exception:
            self._record_telemetry_error("writer_shutdown")

    def _prune(self, now: float) -> None:
        cutoff = now - 60.0
        while self._request_times and self._request_times[0] < cutoff:
            self._request_times.popleft()
        while self._session_times and self._session_times[0] < cutoff:
            self._session_times.popleft()

    def _rate(self, values: deque[float], seconds: int, now: float) -> int:
        return sum(1 for value in values if value >= now - seconds)

    def _request_kind(self, form: Mapping[str, Any], component: str) -> str:
        p = _text(form.get("p") or form.get("request_type")).lower()
        if p == "ft_bet":
            return "bet"
        if p == "ft_order_view":
            return "context"
        c = _text(component).lower()
        if "snapshot" in c:
            return "snapshot"
        if "quote" in c:
            return "member_quote"
        if "settlement" in c:
            return "settlement"
        return "critical"

    def record_session_created(self, session: Any, *, component: str = "follower", meta: Optional[Mapping[str, Any]] = None) -> str:
        try:
            session_hash = _sha(f"{os.getpid()}:{id(session)}:{uuid.uuid4().hex}")
            setattr(session, "_hdzz_session_hash", session_hash)
            setattr(session, "_hdzz_session_created_at", time.time())
            setattr(session, "_hdzz_request_index", 0)
            setattr(session, "_hdzz_telemetry_component", component)
            setattr(session, "_hdzz_telemetry_meta", dict(meta or {}))
            now = time.time()
            with self._lock:
                self._session_times.append(now)
                self._prune(now)
            self._enqueue(("record", {"event": "session_created", "timestamp": now, "monotonic_ns": time.monotonic_ns(), "pid": os.getpid(), "thread_id": threading.get_ident(), "component": component, "session_hash": session_hash, "session_created_at": now, "session_creation_rate_1s": self._rate(self._session_times, 1, now)}))
            return session_hash
        except Exception:
            return ""

    def record_request_start(self, *, session: Any, url: str, form: Mapping[str, Any], component: str = "follower", meta: Optional[Mapping[str, Any]] = None) -> RequestHandle:
        now = time.time()
        kind = self._request_kind(form, component)
        try:
            session_hash = _text(getattr(session, "_hdzz_session_hash", ""))
            if not session_hash:
                session_hash = self.record_session_created(session, component=component)
            index = int(getattr(session, "_hdzz_request_index", 0) or 0) + 1
            setattr(session, "_hdzz_request_index", index)
            session_meta = getattr(session, "_hdzz_telemetry_meta", {})
            merged = dict(session_meta if isinstance(session_meta, Mapping) else {})
            merged.update(dict(meta or {}))
            ident = _url_identity(url)
            record = {"event": "http_request", "timestamp": now, "monotonic_ns": time.monotonic_ns(), "pid": os.getpid(), "thread_id": threading.get_ident(), "component": component, "request_id": f"http-{uuid.uuid4().hex}", "order_id": merged.get("order_id"), "split_id": merged.get("split_id"), "attempt": merged.get("attempt"), "request_type": _text(form.get("p")) or "unknown", "member_hash": merged.get("member_hash") or merged.get("member_id_hash"), "session_hash": session_hash, "host": ident.get("host_sha256"), "path": ident.get("path"), "method": "POST", "request_started_at": now, "ttfb_ms": None, "gtype": form.get("gtype"), "wtype": form.get("wtype"), "rtype": form.get("rtype"), "chose_team": form.get("chose_team"), "gid": form.get("gid"), "con": form.get("con"), "ratio": form.get("ratio"), "route_gate_kind": merged.get("route_gate_kind"), "route_gate_wait_ms": merged.get("route_gate_wait_ms"), "session_request_index": index, "request_rate_1s": 0, "request_rate_5s": 0, "request_rate_10s": 0, "request_rate_30s": 0}
            try:
                proxies = getattr(session, "proxies", {}) or {}
                record["proxy_enabled"] = bool(proxies.get("http") or proxies.get("https"))
            except Exception:
                record["proxy_enabled"] = None
            with self._lock:
                self._request_times.append(now)
                self._inflight["total_transform"] += 1
                self._inflight[kind] = self._inflight.get(kind, 0) + 1
                self._inflight["critical"] += 1
                self._prune(now)
                record["request_rate_1s"] = self._rate(self._request_times, 1, now)
                record["request_rate_5s"] = self._rate(self._request_times, 5, now)
                record["request_rate_10s"] = self._rate(self._request_times, 10, now)
                record["request_rate_30s"] = self._rate(self._request_times, 30, now)
                record["inflight_at_start"] = dict(self._inflight)
            return RequestHandle(self, record)
        except Exception:
            return RequestHandle(self, {"event": "http_request", "timestamp": now, "request_type": _text(form.get("p")), "request_started_at": now, "component": component})

    def _is_blocked_502(self, record: Mapping[str, Any]) -> bool:
        return int(record.get("http_status") or 0) == 502 and int(record.get("response_body_length") or 0) == 301 and _text(record.get("response_body_sha256")) == "5cabae7fe50d472590c91a058986facf6fe866165574b7895047abd6f3638637"

    def _finish(self, record: Dict[str, Any]) -> None:
        now = time.time()
        kind = self._request_kind(record, _text(record.get("component")))
        with self._lock:
            self._inflight["total_transform"] = max(0, self._inflight.get("total_transform", 0) - 1)
            self._inflight[kind] = max(0, self._inflight.get(kind, 0) - 1)
            self._inflight["critical"] = max(0, self._inflight.get("critical", 0) - 1)
            record["inflight_at_finish"] = dict(self._inflight)
            self._ring.append(dict(record))
            ring_snapshot = list(self._ring)
        self._enqueue(("record", record))
        if record.get("blocked_502"):
            self._schedule_incident(record)

    def _finalize_incident(self, incident_id: str, payload: Dict[str, Any]) -> None:
        try:
            now = time.time()
            triggered_at = float(payload.get("triggered_at") or now)
            last_triggered_at = float(payload.get("last_triggered_at") or triggered_at)
            window_start = triggered_at - float(payload.get("pre_window_seconds") or 30.0)
            window_end = last_triggered_at + float(payload.get("post_window_seconds") or 10.0)
            with self._lock:
                records = [
                    item
                    for item in self._ring
                    if window_start <= float(item.get("timestamp") or 0.0) <= window_end
                ]
            payload["records"] = records
            payload["finalized_at"] = now
            if "environment" not in payload:
                payload["environment"] = environment_snapshot()
            self._enqueue(("incident", payload))
        except Exception as exc:
            self._record_telemetry_error("incident_finalize", exc)

    def stats(self) -> Dict[str, Any]:
        now = time.time()
        with self._lock:
            self._prune(now)
            result = {
                "inflight": dict(self._inflight),
                "request_rates": {str(s): self._rate(self._request_times, s, now) for s in (1, 5, 10, 30)},
                "sessions_created": {str(s): self._rate(self._session_times, s, now) for s in (1, 5, 10, 30)},
                "queue_size": self._queue.qsize(),
                "dropped": self._drop_count,
                "telemetry_error_count": self._telemetry_error_count,
                "incident_finalizer_alive": bool(self._incident_thread and self._incident_thread.is_alive()),
            }
        with self._incident_cv:
            result.update(
                {
                    "incident_queue_size": len(self._incident_groups),
                    "incident_queue_capacity": self._incident_capacity,
                    "dropped_incident_count": self._dropped_incident_count,
                    "dropped_incident_reasons": dict(self._dropped_incident_reasons),
                }
            )
        return result


def _ssl_info() -> Dict[str, Any]:
    try:
        import ssl
        return {"openssl": ssl.OPENSSL_VERSION, "python": platform.python_version()}
    except Exception:
        return {"openssl": None, "python": platform.python_version()}


def environment_snapshot() -> Dict[str, Any]:
    """Best-effort, read-only process/network environment snapshot."""
    try:
        import shutil
        route = None
        if shutil.which("route"):
            import subprocess
            route = subprocess.run(["route", "-n", "get", "default"], capture_output=True, text=True, timeout=1).stdout[-2000:]
        return {"timestamp": time.time(), "pid": os.getpid(), "platform": platform.platform(), "python": platform.python_version(), "default_route_tail": route, "system_proxy_env": {k: bool(os.environ.get(k)) for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")}}
    except Exception:
        return {"timestamp": time.time(), "pid": os.getpid()}


_MANAGER = TelemetryManager()


def telemetry_manager() -> TelemetryManager:
    return _MANAGER.configure()


__all__ = ["RequestHandle", "TelemetryManager", "environment_snapshot", "telemetry_manager"]
