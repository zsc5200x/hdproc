"""Pure helpers for separating read-ready and bet-ready member sessions."""

from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, Tuple

from hdzz.common.parse import parse_cookie_header


def _text(value: Any) -> str:
    return str(value or "").strip()


def cookie_names(cookie: str) -> list[str]:
    return sorted(
        {str(name).strip() for name in parse_cookie_header(_text(cookie)) if str(name).strip()},
        key=str.casefold,
    )


def cookie_fingerprint(cookie: str) -> str:
    pairs = parse_cookie_header(_text(cookie))
    material = "\n".join(
        f"{name}={pairs[name]}" for name in sorted(pairs, key=str.casefold)
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest() if material else ""


def session_fingerprint(member: Dict[str, Any]) -> str:
    """Fingerprint identity fields without persisting their raw values."""

    cookie = _text(member.get("cookie"))
    cookie_fp = _text(member.get("cookie_fingerprint")) or cookie_fingerprint(cookie)
    material = "|".join(
        (
            _text(member.get("base_url")).rstrip("/"),
            _text(member.get("uid")),
            _text(member.get("ver")),
            cookie_fp,
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest() if material.strip("|") else ""


def _uid_suffixes(uid: str) -> set[str]:
    return {value for value in re.findall(r"\d{4,}", _text(uid)) if value}


def login_cookie_matches_uid(member: Dict[str, Any]) -> bool:
    """Verify the platform login_* cookie belongs to the current UID.

    Platforms without a login_* cookie are treated as incomplete here. The
    current HGA flow always emits one and this prevents a display-only online
    record from entering the real betting pool.
    """

    names = cookie_names(_text(member.get("cookie")))
    login_suffixes = {
        name.split("_", 1)[1]
        for name in names
        if name.lower().startswith("login_") and name.split("_", 1)[1]
    }
    if not login_suffixes:
        # Generic/test platforms may not expose a login_* cookie. The HGA
        # flow does, and when present it must be bound to the current UID.
        return True
    uid_suffixes = _uid_suffixes(_text(member.get("uid")))
    if not uid_suffixes:
        return False
    return bool(login_suffixes & uid_suffixes)


def session_ready_state(member: Dict[str, Any]) -> Tuple[bool, str]:
    """Return ``(bet_ready, reason)`` while preserving read-ready status."""

    required = ("base_url", "uid", "ver", "cookie")
    missing = [key for key in required if not _text(member.get(key))]
    if missing:
        return False, "SESSION_MISSING_" + "_".join(missing).upper()
    if not bool(member.get("session_stable", True)):
        return False, _text(member.get("session_ready_reason")) or "SESSION_NOT_STABLE"
    if member.get("session_ready") is False:
        return False, _text(member.get("session_ready_reason")) or "SESSION_NOT_READY"
    if not login_cookie_matches_uid(member):
        return False, "BET_SESSION_INCOMPLETE"
    if bool(member.get("relogin_in_progress")):
        return False, "RELOGIN_IN_PROGRESS"
    status = _text(member.get("status")).lower()
    if status in {"relogin", "offline", "unknown"}:
        return False, "SESSION_STATUS_" + status.upper()
    if _text(member.get("last_error")).lower().startswith(("double_login", "login_required", "session_invalid")):
        return False, "SESSION_INVALID"
    return True, "SESSION_READY"


def normalized_session_fields(member: Dict[str, Any]) -> Dict[str, Any]:
    """Build safe persisted metadata for an arsenal member."""

    names = cookie_names(_text(member.get("cookie")))
    cookie_fp = _text(member.get("cookie_fingerprint")) or cookie_fingerprint(_text(member.get("cookie")))
    fp = session_fingerprint({**member, "cookie_fingerprint": cookie_fp})
    ready, reason = session_ready_state({**member, "cookie_fingerprint": cookie_fp})
    return {
        "cookie_names": names,
        "cookie_fingerprint": cookie_fp,
        "session_fingerprint": fp,
        "session_ready": ready,
        "session_ready_reason": reason,
    }


__all__ = [
    "cookie_names",
    "cookie_fingerprint",
    "session_fingerprint",
    "login_cookie_matches_uid",
    "session_ready_state",
    "normalized_session_fields",
]
