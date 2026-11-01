from __future__ import annotations

import json
import os
import re
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple
from urllib.parse import urljoin


PLATFORM_STATE_FILENAME = "platform_maintenance.json"
PLATFORM_MAINTENANCE_CODE = "PLATFORM_MAINTENANCE"
PLATFORM_MAINTENANCE_MESSAGE = "平台系统维护"
DEFAULT_PROBE_INTERVAL_SECONDS = 15.0
DEFAULT_RECOVERY_SUCCESS_REQUIRED = 2

_STATE_LOCK = threading.Lock()


def _now_ts(now: Optional[float] = None) -> float:
    return float(time.time() if now is None else now)


def _flag_enabled(value: Any) -> bool:
    return str(value or "").strip().upper() in {"Y", "YES", "1", "TRUE", "ON"}


def _compact(value: Any, limit: int = 240) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def platform_state_path(data_dir_or_path: Path) -> Path:
    path = Path(data_dir_or_path)
    return path if path.name == PLATFORM_STATE_FILENAME else path / PLATFORM_STATE_FILENAME


def default_platform_state() -> Dict[str, Any]:
    return {
        "status": "unknown",
        "maintenance_start_at": 0.0,
        "maintenance_until": 0.0,
        "maintenance_message": "",
        "maintenance_source": "",
        "last_probe_at": 0.0,
        "next_probe_at": 0.0,
        "last_probe_error": "",
        "recovery_started_at": 0.0,
        "recovery_success_count": 0,
        "recovery_success_required": DEFAULT_RECOVERY_SUCCESS_REQUIRED,
        "last_business_ok_at": 0.0,
        "business_success_sources": {},
        "updated_at": 0.0,
    }


def normalize_platform_state(raw: Any) -> Dict[str, Any]:
    state = default_platform_state()
    if isinstance(raw, Mapping):
        state.update(dict(raw))
    status = str(state.get("status") or "unknown").strip().lower()
    if status not in {"unknown", "online", "maintenance", "recovering"}:
        status = "unknown"
    state["status"] = status
    for key in (
        "maintenance_start_at",
        "maintenance_until",
        "last_probe_at",
        "next_probe_at",
        "recovery_started_at",
        "last_business_ok_at",
        "updated_at",
    ):
        try:
            state[key] = float(state.get(key) or 0.0)
        except Exception:
            state[key] = 0.0
    try:
        state["recovery_success_count"] = max(0, int(state.get("recovery_success_count") or 0))
    except Exception:
        state["recovery_success_count"] = 0
    try:
        state["recovery_success_required"] = max(
            1,
            int(state.get("recovery_success_required") or DEFAULT_RECOVERY_SUCCESS_REQUIRED),
        )
    except Exception:
        state["recovery_success_required"] = DEFAULT_RECOVERY_SUCCESS_REQUIRED
    state["maintenance_message"] = _compact(state.get("maintenance_message"))
    state["maintenance_source"] = _compact(state.get("maintenance_source"), 80)
    state["last_probe_error"] = _compact(state.get("last_probe_error"))
    sources = state.get("business_success_sources")
    state["business_success_sources"] = dict(sources) if isinstance(sources, Mapping) else {}
    return state


def read_platform_state(data_dir_or_path: Path) -> Dict[str, Any]:
    path = platform_state_path(data_dir_or_path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        raw = {}
    return normalize_platform_state(raw)


def write_platform_state(data_dir_or_path: Path, state: Mapping[str, Any]) -> Dict[str, Any]:
    path = platform_state_path(data_dir_or_path)
    normalized = normalize_platform_state(state)
    normalized["updated_at"] = _now_ts()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(normalized, ensure_ascii=False, indent=2)
    with _STATE_LOCK:
        tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(str(tmp), str(path))
    return normalized


def _parse_datetime(value: str) -> Optional[datetime]:
    text = str(value or "").strip()
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M",
    ):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def parse_maintenance_window(value: Any) -> Tuple[float, float]:
    text = _compact(value, 200)
    if not text:
        return 0.0, 0.0
    match = re.search(
        r"(20\d{2}[-/]\d{1,2}[-/]\d{1,2}\s+\d{1,2}:\d{2}(?::\d{2})?)\s*[~～至-]\s*"
        r"(?:(20\d{2}[-/]\d{1,2}[-/]\d{1,2})\s+)?(\d{1,2}:\d{2}(?::\d{2})?)",
        text,
    )
    if not match:
        return 0.0, 0.0
    start = _parse_datetime(match.group(1))
    if start is None:
        return 0.0, 0.0
    end_date = match.group(2) or start.strftime("%Y-%m-%d")
    end = _parse_datetime(f"{end_date} {match.group(3)}")
    if end is None:
        return start.timestamp(), 0.0
    if end.timestamp() < start.timestamp() and not match.group(2):
        end = datetime.fromtimestamp(end.timestamp() + 86400)
    return start.timestamp(), end.timestamp()


def _mapping_maintenance_signal(payload: Mapping[str, Any]) -> Dict[str, Any]:
    data = {str(key).strip().lower(): value for key, value in payload.items()}
    active = any(
        _flag_enabled(data.get(key))
        for key in ("maintain_sw", "maintenance_sw", "emergency_sw", "urgent_sw")
    )
    code = str(data.get("code") or data.get("status") or "").strip()
    window = data.get("maintain_time") or data.get("maintenance_time") or data.get("time") or ""
    start_at, until = parse_maintenance_window(window)
    return {
        "active": bool(active),
        "code": code,
        "maintenance_start_at": start_at,
        "maintenance_until": until,
        "maintenance_message": _compact(
            data.get("maintenance_message")
            or data.get("maintain_msg")
            or data.get("msg")
            or PLATFORM_MAINTENANCE_MESSAGE
        ),
        "source": "structured_payload",
    }


def parse_backend_maintenance_response(text: Any) -> Dict[str, Any]:
    """Parse the lightweight AG maintenance endpoint without scanning hidden HTML templates."""
    raw = str(text or "").strip()
    inactive = {
        "active": False,
        "maintenance_start_at": 0.0,
        "maintenance_until": 0.0,
        "maintenance_message": "",
        "source": "backend_maintenance_endpoint",
    }
    if not raw:
        return inactive

    try:
        parsed = json.loads(raw)
    except Exception:
        parsed = None
    if isinstance(parsed, Mapping):
        out = _mapping_maintenance_signal(parsed)
        out["source"] = "backend_maintenance_endpoint"
        return out

    if raw.startswith("<") and len(raw) <= 16000:
        try:
            root = ET.fromstring(raw)
            payload = {str(child.tag): (child.text or "").strip() for child in list(root)}
        except Exception:
            payload = {}
        if payload:
            out = _mapping_maintenance_signal(payload)
            out["source"] = "backend_maintenance_endpoint"
            return out

    # Current AG endpoint format:
    #   2026-08-27 13:30:00~16:30:00,Y,13:05:00,N
    # The second flag is scheduled maintenance; the fourth is urgent maintenance.
    parts = [part.strip() for part in raw.split(",")]
    if len(parts) >= 2:
        start_at, until = parse_maintenance_window(parts[0])
        scheduled_active = _flag_enabled(parts[1])
        urgent_active = len(parts) >= 4 and _flag_enabled(parts[3])
        if scheduled_active or urgent_active:
            return {
                "active": True,
                "maintenance_start_at": start_at,
                "maintenance_until": until,
                "maintenance_message": PLATFORM_MAINTENANCE_MESSAGE,
                "source": "backend_maintenance_endpoint",
            }
        if start_at or until:
            return inactive

    # Deliberately do not search arbitrary HTML for maintenance wording. The member
    # service page permanently embeds a hidden 2019 template even when service is healthy.
    return inactive


def classify_maintenance_payload(payload: Any) -> Dict[str, Any]:
    if isinstance(payload, Mapping):
        return _mapping_maintenance_signal(payload)
    return parse_backend_maintenance_response(payload)


def is_platform_maintenance(state: Mapping[str, Any]) -> bool:
    return str((state or {}).get("status") or "").strip().lower() == "maintenance"


def maintenance_status_message(state: Mapping[str, Any]) -> str:
    normalized = normalize_platform_state(state)
    until = float(normalized.get("maintenance_until") or 0.0)
    if until > 0:
        return f"平台系统维护，预计 {datetime.fromtimestamp(until).strftime('%m-%d %H:%M')} 恢复"
    return normalized.get("maintenance_message") or PLATFORM_MAINTENANCE_MESSAGE


def refresh_backend_maintenance_state(
    *,
    session: Any,
    base_url: str,
    data_dir_or_path: Path,
    cookie: str = "",
    proxy: str = "",
    timeout_s: int = 8,
    min_probe_interval_s: float = DEFAULT_PROBE_INTERVAL_SECONDS,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    ts = _now_ts(now)
    previous = read_platform_state(data_dir_or_path)
    last_probe_at = float(previous.get("last_probe_at") or 0.0)
    if last_probe_at > 0 and ts - last_probe_at < max(1.0, float(min_probe_interval_s)):
        return previous

    state = dict(previous)
    state["last_probe_at"] = ts
    state["next_probe_at"] = ts + max(1.0, float(min_probe_interval_s))
    url = urljoin(str(base_url or "").rstrip("/") + "/", "app/showMaintain.php")
    headers = {
        "Accept": "*/*",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    }
    if cookie:
        headers["Cookie"] = str(cookie)
    proxies = {"http": proxy, "https": proxy} if proxy else None
    try:
        response = session.get(
            url,
            headers=headers,
            timeout=(min(5, max(1, int(timeout_s))), max(1, int(timeout_s))),
            proxies=proxies,
            allow_redirects=True,
        )
        response.raise_for_status()
        parsed = parse_backend_maintenance_response(response.text or "")
        state["last_probe_error"] = ""
    except Exception as exc:
        state["last_probe_error"] = _compact(f"{type(exc).__name__}: {exc}")
        return write_platform_state(data_dir_or_path, state)

    if parsed.get("active"):
        state.update(
            {
                "status": "maintenance",
                "maintenance_start_at": float(parsed.get("maintenance_start_at") or 0.0),
                "maintenance_until": float(parsed.get("maintenance_until") or 0.0),
                "maintenance_message": parsed.get("maintenance_message") or PLATFORM_MAINTENANCE_MESSAGE,
                "maintenance_source": parsed.get("source") or "backend_maintenance_endpoint",
                "recovery_started_at": 0.0,
                "recovery_success_count": 0,
                "last_business_ok_at": 0.0,
                "business_success_sources": {},
            }
        )
    elif str(previous.get("status") or "") == "maintenance":
        state.update(
            {
                "status": "recovering",
                "maintenance_message": "维护窗口已结束，正在验证原有会话",
                "recovery_started_at": ts,
                "recovery_success_count": 0,
                "business_success_sources": {},
            }
        )
    elif str(previous.get("status") or "") != "recovering":
        state.update(
            {
                "status": "online",
                "maintenance_message": "",
                "maintenance_source": parsed.get("source") or "backend_maintenance_endpoint",
            }
        )
    return write_platform_state(data_dir_or_path, state)


def record_platform_business_success(
    data_dir_or_path: Path,
    *,
    kind: str = "business",
    now: Optional[float] = None,
    required: int = DEFAULT_RECOVERY_SUCCESS_REQUIRED,
) -> Dict[str, Any]:
    ts = _now_ts(now)
    state = read_platform_state(data_dir_or_path)
    state["last_business_ok_at"] = ts
    if str(state.get("status") or "") == "recovering":
        source = _compact(kind, 40).lower() or "business"
        sources = dict(state.get("business_success_sources") or {})
        sources[source] = ts
        state["business_success_sources"] = sources
        count = len(sources)
        required_eff = max(1, int(required or DEFAULT_RECOVERY_SUCCESS_REQUIRED))
        state["recovery_success_count"] = count
        state["recovery_success_required"] = required_eff
        if count >= required_eff:
            state["status"] = "online"
            state["maintenance_message"] = ""
    return write_platform_state(data_dir_or_path, state)
