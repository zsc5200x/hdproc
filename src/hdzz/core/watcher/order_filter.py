from __future__ import annotations

import re
from typing import Any, Dict, List, Set

_ACCOUNT_KEYS = ("NAME0", "USER", "username", "ACCOUNT", "account")
_DOWNLINE_KEYS = (
    "DOWNLINE",
    "downline",
    "DOWNLINE_ID",
    "DOWNLINEID",
    "source_downline",
    "SOURCE_DOWNLINE",
)
_SEPARATOR_RE = re.compile(r"[\s,，;；\n\r\t]+")


def normalize_member_names(raw: Any) -> List[str]:
    items: List[str] = []
    if raw is None:
        return items

    chunks: List[str] = []
    if isinstance(raw, str):
        chunks = [raw]
    elif isinstance(raw, (list, tuple, set)):
        for it in raw:
            if it is None:
                continue
            chunks.append(str(it))
    else:
        chunks = [str(raw)]

    out: List[str] = []
    seen: Set[str] = set()
    for chunk in chunks:
        for part in _SEPARATOR_RE.split(str(chunk or "")):
            name = str(part or "").strip()
            if not name:
                continue
            key = name.casefold()
            if key in seen:
                continue
            seen.add(key)
            out.append(name)
    return out


def build_member_filter_set(raw: Any) -> Set[str]:
    return {x.casefold() for x in normalize_member_names(raw)}


def normalize_downline_alias_map(raw: Any) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if not isinstance(raw, dict):
        return out
    for k, v in raw.items():
        key = str(k or "").strip()
        val = str(v or "").strip()
        if not key or not val:
            continue
        out[key.casefold()] = val
    return out


def _looks_like_downline_id_token(value: Any) -> bool:
    s = str(value or "").strip()
    if not s:
        return False
    lower = s.lower()
    if lower.startswith("downline_"):
        tail = s.split("_", 1)[1].strip()
        return bool(tail) and tail.isdigit()
    return s.isdigit()


def _normalize_source_tokens(value: Any) -> Set[str]:
    s = str(value or "").strip()
    if not s:
        return set()

    out: Set[str] = {s.casefold()}
    if _looks_like_downline_id_token(s):
        digits = "".join(ch for ch in s if ch.isdigit())
        if digits:
            out.add(digits)

        lower = s.lower()
        if lower.startswith("downline_"):
            tail = s.split("_", 1)[1].strip()
            if tail:
                out.add(tail.casefold())
            tail_digits = "".join(ch for ch in tail if ch.isdigit())
            if tail_digits:
                out.add(tail_digits)
    return out


def build_downline_filter_set(raw: Any, alias_map: Any = None) -> Set[str]:
    amap = normalize_downline_alias_map(alias_map)
    out: Set[str] = set()
    for item in normalize_member_names(raw):
        out.update(_normalize_source_tokens(item))
        mapped = amap.get(str(item or "").strip().casefold())
        if mapped:
            out.update(_normalize_source_tokens(mapped))
    return out


def extract_order_source_account(order: Dict[str, Any]) -> str:
    if not isinstance(order, dict):
        return ""

    account = str(order.get("account") or "").strip()
    if account:
        return account

    raw = order.get("raw")
    if not isinstance(raw, dict):
        return ""

    for key in _ACCOUNT_KEYS:
        val = raw.get(key)
        if val is None:
            continue
        s = str(val).strip()
        if s:
            return s
    return ""


def extract_order_source_downline(order: Dict[str, Any]) -> str:
    if not isinstance(order, dict):
        return ""

    direct = str(order.get("downline") or order.get("source_downline") or "").strip()
    if direct:
        return direct

    raw = order.get("raw")
    if not isinstance(raw, dict):
        return ""

    for key in _DOWNLINE_KEYS:
        val = raw.get(key)
        if val is None:
            continue
        s = str(val).strip()
        if s:
            return s
    return ""


def should_forward_order_for_target(
    *,
    relogin_login_tab: int,
    login2_watch_members: Any,
    login2_watch_downlines: Any = None,
    login2_downline_alias_map: Any = None,
    order: Dict[str, Any],
) -> bool:
    login_tab = int(relogin_login_tab or 0)
    if login_tab not in (2, 3):
        return True

    allow_member_set = build_member_filter_set(login2_watch_members) if login_tab == 2 else set()
    allow_downline_set = build_downline_filter_set(login2_watch_downlines, login2_downline_alias_map)
    if not allow_member_set and not allow_downline_set:
        return True

    if allow_member_set:
        account = extract_order_source_account(order).casefold()
        if not account or account not in allow_member_set:
            return False

    if allow_downline_set:
        downline = extract_order_source_downline(order)
        if not downline:
            return False
        if not (_normalize_source_tokens(downline) & allow_downline_set):
            return False

    return True
