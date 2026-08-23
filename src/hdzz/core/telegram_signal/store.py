from __future__ import annotations

import json
import os
import re
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any, Dict, Iterable, List


def default_settings() -> Dict[str, Any]:
    return {
        "enabled": False,
        "dry_run": True,
        "source_chat_ids": [],
        "source_chats": [],
        "target_name": "telegram_signal",
        "webui_url": "http://127.0.0.1:2299",
    }


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except Exception:
        return default


def _atomic_write_json(path: Path, payload: Any, *, private: bool = False) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{uuid.uuid4().hex}")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if private:
        os.chmod(tmp, 0o600)
    tmp.replace(path)
    if private:
        os.chmod(path, 0o600)


def write_private_json(path: Path, payload: Dict[str, Any]) -> None:
    _atomic_write_json(Path(path), dict(payload or {}), private=True)


def load_private_json(path: Path) -> Dict[str, Any]:
    payload = read_json(Path(path), {})
    return payload if isinstance(payload, dict) else {}


def _normalize_chat_ids(values: Any) -> List[str]:
    if not isinstance(values, (list, tuple, set)):
        values = [values]
    out: List[str] = []
    for value in values:
        text = str(value or "").strip()
        if not re.fullmatch(r"-?\d+", text) or text in out:
            continue
        out.append(text)
    return out


def apply_settings_patch(current: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    out = default_settings()
    if isinstance(current, dict):
        for key in out:
            if key in current:
                out[key] = current[key]
    patch = patch if isinstance(patch, dict) else {}
    if "enabled" in patch:
        out["enabled"] = bool(patch.get("enabled"))
    if "dry_run" in patch:
        out["dry_run"] = bool(patch.get("dry_run"))
    if "source_chat_ids" in patch:
        out["source_chat_ids"] = _normalize_chat_ids(patch.get("source_chat_ids"))
    if "source_chats" in patch:
        selected = set(out.get("source_chat_ids") or [])
        out["source_chats"] = [
            {
                "id": str(item.get("id") or "").strip(),
                "title": str(item.get("title") or "").strip(),
                "type": str(item.get("type") or "").strip(),
            }
            for item in (patch.get("source_chats") or [])
            if isinstance(item, dict) and str(item.get("id") or "").strip() in selected
        ]
    if "target_name" in patch:
        target = re.sub(r"[^\w.\-]+", "_", str(patch.get("target_name") or "").strip())
        out["target_name"] = target.strip("._") or "telegram_signal"
    if "webui_url" in patch:
        webui_url = str(patch.get("webui_url") or "").strip().rstrip("/")
        if re.fullmatch(r"https?://(?:127\.0\.0\.1|localhost)(?::\d+)?", webui_url):
            out["webui_url"] = webui_url
    out["source_chat_ids"] = _normalize_chat_ids(out.get("source_chat_ids"))
    return out


def load_settings(path: Path) -> Dict[str, Any]:
    payload = read_json(Path(path), {})
    return apply_settings_patch(default_settings(), payload if isinstance(payload, dict) else {})


def save_settings(path: Path, settings: Dict[str, Any]) -> Dict[str, Any]:
    normalized = apply_settings_patch(default_settings(), settings)
    _atomic_write_json(Path(path), normalized)
    return normalized


def _mask_phone(phone: Any) -> str:
    text = str(phone or "").strip()
    if len(text) <= 8:
        return "****" if text else ""
    return f"{text[:6]}****{text[-4:]}"


def sanitize_secret_status(secret: Dict[str, Any]) -> Dict[str, Any]:
    secret = secret if isinstance(secret, dict) else {}
    api_id = str(secret.get("api_id") or "").strip()
    api_hash = str(secret.get("api_hash") or "").strip()
    session = str(secret.get("session") or "").strip()
    return {
        "credentials_configured": bool(api_id and api_hash),
        "session_configured": bool(session),
        "phone_masked": _mask_phone(secret.get("phone")),
    }


def _remember_value(path: Path, bucket: str, key: str, *, max_seen: int) -> bool:
    key = str(key or "").strip()
    if not key:
        return False
    payload = read_json(Path(path), {})
    if not isinstance(payload, dict):
        payload = {}
    seen = [str(item) for item in (payload.get(bucket) or []) if str(item)]
    if key in seen:
        return False
    seen.append(key)
    limit = max(1, int(max_seen or 1))
    if len(seen) > limit:
        seen = seen[-limit:]
    payload[bucket] = seen
    payload["updated_at"] = int(time.time())
    _atomic_write_json(Path(path), payload)
    return True


def remember_message(path: Path, key: str, *, max_seen: int = 10000) -> bool:
    return _remember_value(Path(path), "seen_message_keys", key, max_seen=max_seen)


def remember_order_id(path: Path, order_id: str, *, max_seen: int = 10000) -> bool:
    return _remember_value(Path(path), "seen_order_ids", order_id, max_seen=max_seen)


def append_event(path: Path, event: Dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    row = dict(event or {})
    row.setdefault("ts", int(time.time()))
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def read_recent_events(path: Path, *, limit: int = 100) -> List[Dict[str, Any]]:
    rows: deque = deque(maxlen=max(1, min(1000, int(limit or 100))))
    try:
        with Path(path).open("r", encoding="utf-8-sig") as handle:
            for line in handle:
                try:
                    item = json.loads(line)
                except Exception:
                    continue
                if isinstance(item, dict):
                    rows.append(item)
    except Exception:
        return []
    return list(reversed(rows))
