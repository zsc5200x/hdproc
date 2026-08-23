from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Tuple


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ensure_access_doc(doc: Dict[str, Any] | None) -> Dict[str, Any]:
    src = doc if isinstance(doc, dict) else {}
    users_raw = src.get("users")
    users: Dict[str, Dict[str, Any]] = {}
    if isinstance(users_raw, dict):
        for k, v in users_raw.items():
            key = str(k or "").strip()
            if not key:
                continue
            vv = v if isinstance(v, dict) else {}
            orders_raw = vv.get("orders")
            orders: Dict[str, Dict[str, Any]] = {}
            if isinstance(orders_raw, dict):
                for oid, item in orders_raw.items():
                    oid_s = str(oid or "").strip()
                    if not oid_s:
                        continue
                    it = item if isinstance(item, dict) else {}
                    orders[oid_s] = {
                        "pay_no": str(it.get("pay_no") or "").strip(),
                        "granted_at": str(it.get("granted_at") or "").strip() or now_iso(),
                    }
            users[key] = {"orders": orders}
    cards_raw = src.get("day_cards")
    day_cards: Dict[str, Dict[str, Any]] = {}
    if isinstance(cards_raw, dict):
        for k, v in cards_raw.items():
            key = str(k or "").strip()
            if not key:
                continue
            vv = v if isinstance(v, dict) else {}
            day_cards[key] = {
                "expires_at": str(vv.get("expires_at") or "").strip(),
                "granted_at": str(vv.get("granted_at") or "").strip() or now_iso(),
                "granted_by": str(vv.get("granted_by") or "").strip(),
            }
    out: Dict[str, Any] = {
        "users": users,
        "day_cards": day_cards,
        "updated_at": str(src.get("updated_at") or "").strip() or now_iso(),
    }
    return out


def _ensure_user_orders(doc: Dict[str, Any], user_key: str) -> Dict[str, Dict[str, Any]]:
    user_key = str(user_key or "").strip()
    if not user_key:
        return {}
    users = doc.setdefault("users", {})
    if not isinstance(users, dict):
        users = {}
        doc["users"] = users
    u = users.get(user_key)
    if not isinstance(u, dict):
        u = {}
        users[user_key] = u
    orders = u.get("orders")
    if not isinstance(orders, dict):
        orders = {}
        u["orders"] = orders
    return orders


def has_order_access(doc: Dict[str, Any], *, user_key: str, order_id: str) -> bool:
    user_key = str(user_key or "").strip()
    oid = str(order_id or "").strip()
    if not user_key or not oid:
        return False
    users = doc.get("users")
    if not isinstance(users, dict):
        return False
    u = users.get(user_key)
    if not isinstance(u, dict):
        return False
    orders = u.get("orders")
    if not isinstance(orders, dict):
        return False
    return oid in orders


def grant_order_access(
    doc: Dict[str, Any],
    *,
    user_key: str,
    order_id: str,
    pay_no: str = "",
) -> Tuple[Dict[str, Any], bool]:
    d = ensure_access_doc(doc)
    user_key = str(user_key or "").strip()
    oid = str(order_id or "").strip()
    if not user_key or not oid:
        return d, False
    orders = _ensure_user_orders(d, user_key)
    changed = oid not in orders
    orders[oid] = {
        "pay_no": str(pay_no or "").strip(),
        "granted_at": now_iso(),
    }
    d["updated_at"] = now_iso()
    return d, changed


def revoke_order_access(
    doc: Dict[str, Any],
    *,
    user_key: str,
    order_id: str,
) -> Tuple[Dict[str, Any], bool]:
    d = ensure_access_doc(doc)
    user_key = str(user_key or "").strip()
    oid = str(order_id or "").strip()
    if not user_key or not oid:
        return d, False
    users = d.get("users")
    if not isinstance(users, dict):
        return d, False
    u = users.get(user_key)
    if not isinstance(u, dict):
        return d, False
    orders = u.get("orders")
    if not isinstance(orders, dict):
        return d, False
    if oid not in orders:
        return d, False
    orders.pop(oid, None)
    d["updated_at"] = now_iso()
    return d, True


def _parse_dt(s: str) -> datetime | None:
    txt = str(s or "").strip()
    if not txt:
        return None
    try:
        return datetime.strptime(txt, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def grant_day_pass(
    doc: Dict[str, Any],
    *,
    user_key: str,
    duration_seconds: int,
    granted_by: str = "",
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    d = ensure_access_doc(doc)
    key = str(user_key or "").strip()
    seconds = int(duration_seconds or 0)
    if not key or seconds <= 0:
        return d, {}
    cards = d.get("day_cards")
    if not isinstance(cards, dict):
        cards = {}
        d["day_cards"] = cards

    now = datetime.now()
    base = now
    old = cards.get(key)
    if isinstance(old, dict):
        old_exp = _parse_dt(str(old.get("expires_at") or ""))
        if old_exp and old_exp > now:
            base = old_exp
    exp = base + timedelta(seconds=seconds)
    rec = {
        "expires_at": exp.strftime("%Y-%m-%d %H:%M:%S"),
        "granted_at": now_iso(),
        "granted_by": str(granted_by or "").strip(),
    }
    cards[key] = rec
    d["updated_at"] = now_iso()
    return d, rec


def has_day_pass(
    doc: Dict[str, Any],
    *,
    user_keys: List[str],
) -> bool:
    d = ensure_access_doc(doc)
    cards = d.get("day_cards")
    if not isinstance(cards, dict):
        return False
    now = datetime.now()
    for key in user_keys or []:
        k = str(key or "").strip()
        if not k:
            continue
        rec = cards.get(k)
        if not isinstance(rec, dict):
            continue
        exp = _parse_dt(str(rec.get("expires_at") or ""))
        if exp and exp > now:
            return True
    return False
