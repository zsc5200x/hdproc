from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set


def _normalize_usage(member: Dict[str, Any]) -> str:
    raw = str((member or {}).get("usage") or "").strip().lower()
    if raw == "read":
        return "read"
    return "read" if str((member or {}).get("role") or "").strip().lower() == "quote" else "bet"


def is_readonly_quote_candidate(member: Dict[str, Any]) -> bool:
    if not isinstance(member, dict):
        return False
    if _normalize_usage(member) != "read":
        return False
    if str(member.get("status") or "").strip().lower() != "online":
        return False
    return bool(
        str(member.get("base_url") or "").strip()
        and str(member.get("uid") or "").strip()
        and str(member.get("ver") or "").strip()
        and str(member.get("cookie") or "").strip()
    )


def list_readonly_quote_candidates(members: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [m for m in (members or []) if is_readonly_quote_candidate(m)]


def pick_readonly_quote_member(
    members: Iterable[Dict[str, Any]],
    preferred_member_id: str = "",
    *,
    exclude_member_ids: Optional[Set[str]] = None,
) -> Optional[Dict[str, Any]]:
    candidates = list_readonly_quote_candidates(members)
    preferred = str(preferred_member_id or "").strip()
    if preferred:
        for member in candidates:
            if str(member.get("id") or "").strip() == preferred:
                return member
    excludes = {str(x).strip() for x in (exclude_member_ids or set()) if str(x).strip()}
    filtered = [m for m in candidates if str(m.get("id") or "").strip() not in excludes]
    if not filtered:
        filtered = candidates
    if not filtered:
        return None
    return random.choice(filtered)


def load_readonly_quote_pool(arsenal_path: Path) -> List[Dict[str, Any]]:
    try:
        obj = json.loads(Path(arsenal_path).read_text(encoding="utf-8-sig"))
    except Exception:
        obj = {}
    members = obj.get("members") if isinstance(obj, dict) else []
    return [m for m in (members or []) if isinstance(m, dict)]


def select_readonly_quote_member(
    arsenal_path: Path,
    preferred_member_id: str = "",
    *,
    exclude_member_ids: Optional[Set[str]] = None,
) -> Optional[Dict[str, Any]]:
    members = load_readonly_quote_pool(arsenal_path)
    return pick_readonly_quote_member(
        members,
        preferred_member_id=preferred_member_id,
        exclude_member_ids=exclude_member_ids,
    )
