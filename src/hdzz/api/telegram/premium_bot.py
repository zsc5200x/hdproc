from __future__ import annotations

import argparse
import json
import os
import random
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests

from hdzz.api.telegram.bot import (
    atomic_write_json,
    read_json,
    safe_tg_answer_callback_query,
    tg_get_updates,
    tg_send_message,
)
from hdzz.api.telegram.premium_access import (
    ensure_access_doc,
    grant_day_pass,
    grant_order_access,
    has_day_pass,
    has_order_access,
)
from hdzz.api.telegram.premium_payment import (
    check_deposit,
    create_pay_link,
    is_deposit_paid,
    parse_pay_link_result,
)


PREMIUM_SETTINGS: Dict[str, Any] = {
    "bot_token": os.environ.get("HDZZ_PREMIUM_BOT_TOKEN", ""),
    "proxy": "",
    "admin_ids": [
        int(value.strip())
        for value in os.environ.get("HDZZ_PREMIUM_ADMIN_IDS", "").split(",")
        if value.strip().isdigit()
    ],
    "free_push_mode": True,
    "push_mode": "private_subscribers",
    "wallet_api_base": "https://api.okaypay.me/shop",
    "wallet_app_id": os.environ.get("HDZZ_PREMIUM_WALLET_APP_ID", ""),
    "wallet_secret": os.environ.get("HDZZ_PREMIUM_WALLET_SECRET", ""),
    "wallet_coin": "USDT",
    "price_per_order": "8",
    "wallet_callback_url": "",
    "wallet_return_url": "",
    "wallet_poll_enabled": True,
    "wallet_poll_interval_s": 15,
    "buy_contact": os.environ.get("HDZZ_PREMIUM_BUY_CONTACT", ""),
    "business_contact": os.environ.get("HDZZ_PREMIUM_BUSINESS_CONTACT", ""),
    "teaser_image_path": os.environ.get("HDZZ_PREMIUM_TEASER_IMAGE_PATH", ""),
}

CB_PREFIX = "pm"
REPO_ROOT = Path(__file__).resolve().parents[4]


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def now_ts() -> int:
    return int(time.time())


def _ensure_list_str(v: Any) -> List[str]:
    if not isinstance(v, list):
        return []
    out: List[str] = []
    for x in v:
        s = str(x or "").strip()
        if s:
            out.append(s)
    return out


def ensure_subscribers_doc(doc: Dict[str, Any] | None) -> Dict[str, Any]:
    src = doc if isinstance(doc, dict) else {}
    users_raw = src.get("users")
    users: Dict[str, Dict[str, Any]] = {}
    if isinstance(users_raw, dict):
        for k, v in users_raw.items():
            uid = str(k or "").strip()
            if not uid:
                continue
            vv = v if isinstance(v, dict) else {}
            users[uid] = {
                "username": str(vv.get("username") or "").strip(),
                "last_seen_at": str(vv.get("last_seen_at") or "").strip() or now_iso(),
            }
    out = {
        "subscribers": sorted(set(_ensure_list_str(src.get("subscribers")))),
        "users": users,
        "updated_at": str(src.get("updated_at") or "").strip() or now_iso(),
    }
    return out


def _norm_username(v: Any) -> str:
    s = str(v or "").strip().lower()
    if s.startswith("@"):
        s = s[1:]
    return s


def _touch_subscriber_user(subscribers_doc: Dict[str, Any], *, user_id: str, username: str) -> bool:
    changed = False
    uid = str(user_id or "").strip()
    if not uid:
        return False
    subs = subscribers_doc.get("subscribers")
    if not isinstance(subs, list):
        subs = []
        subscribers_doc["subscribers"] = subs
        changed = True
    if uid not in subs:
        subs.append(uid)
        subs[:] = sorted(set(_ensure_list_str(subs)))
        changed = True

    users = subscribers_doc.get("users")
    if not isinstance(users, dict):
        users = {}
        subscribers_doc["users"] = users
        changed = True
    rec = users.get(uid)
    if not isinstance(rec, dict):
        rec = {}
        users[uid] = rec
        changed = True

    uname = _norm_username(username)
    if uname and str(rec.get("username") or "").strip() != uname:
        rec["username"] = uname
        changed = True
    prev_seen = str(rec.get("last_seen_at") or "").strip()
    now = now_iso()
    if prev_seen != now:
        rec["last_seen_at"] = now
        changed = True
    return changed


def _get_username_from_subscribers(subscribers_doc: Dict[str, Any], user_id: str) -> str:
    uid = str(user_id or "").strip()
    if not uid:
        return ""
    users = subscribers_doc.get("users")
    if not isinstance(users, dict):
        return ""
    rec = users.get(uid)
    if not isinstance(rec, dict):
        return ""
    return _norm_username(rec.get("username"))


def ensure_orders_doc(doc: Dict[str, Any] | None) -> Dict[str, Any]:
    src = doc if isinstance(doc, dict) else {}
    raw = src.get("orders")
    orders: Dict[str, Dict[str, Any]] = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            oid = str(k or "").strip()
            if not oid or not isinstance(v, dict):
                continue
            orders[oid] = dict(v)
    return {
        "orders": orders,
        "updated_at": str(src.get("updated_at") or "").strip() or now_iso(),
    }


def ensure_payments_doc(doc: Dict[str, Any] | None) -> Dict[str, Any]:
    src = doc if isinstance(doc, dict) else {}
    raw = src.get("payments")
    items: Dict[str, Dict[str, Any]] = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            pay_no = str(k or "").strip()
            if not pay_no or not isinstance(v, dict):
                continue
            items[pay_no] = dict(v)
    return {
        "payments": items,
        "updated_at": str(src.get("updated_at") or "").strip() or now_iso(),
    }


def _state_default() -> Dict[str, Any]:
    return {
        "offset": 0,
        "order_offsets": {},
        "last_payment_poll_ts": 0,
        "updated_at": now_iso(),
    }


def _read_state(path: Path) -> Dict[str, Any]:
    doc = read_json(path) if path.exists() else {}
    if not isinstance(doc, dict):
        doc = {}
    d = _state_default()
    d["offset"] = int(doc.get("offset") or 0)
    d["last_payment_poll_ts"] = int(doc.get("last_payment_poll_ts") or 0)
    offs = doc.get("order_offsets")
    if isinstance(offs, dict):
        d["order_offsets"] = {str(k): int(v or 0) for k, v in offs.items()}
    return d


def _write_state(path: Path, d: Dict[str, Any]) -> None:
    out = {
        "offset": int(d.get("offset") or 0),
        "order_offsets": d.get("order_offsets") if isinstance(d.get("order_offsets"), dict) else {},
        "last_payment_poll_ts": int(d.get("last_payment_poll_ts") or 0),
        "updated_at": now_iso(),
    }
    atomic_write_json(path, out)


def _fmt_ts(ts: Any) -> str:
    try:
        x = float(ts)
        if x > 1_000_000_000_000:
            x = x / 1000.0
        return datetime.fromtimestamp(int(x)).strftime("%m-%d %H:%M:%S")
    except Exception:
        return now_iso()[5:]


def _order_mode(order: Dict[str, Any]) -> str:
    if bool(order.get("live")):
        return "滚球"
    raw = order.get("raw") if isinstance(order.get("raw"), dict) else {}
    isbk = str(raw.get("ISBK") or "").strip().lower()
    if isbk in {"1", "true", "y", "yes"}:
        return "滚球"
    txt = " ".join(
        [
            str(order.get("betType") or ""),
            str(order.get("line") or ""),
            str(raw.get("WAGERSTYPE") or ""),
        ]
    )
    return "滚球" if ("滚球" in txt) else "早盘"


def _split_sides(sides: str) -> Tuple[str, str]:
    s = str(sides or "").strip()
    if not s:
        return "-", "-"
    for sep in [" vs ", " VS ", " v ", " V "]:
        if sep in s:
            a, b = s.split(sep, 1)
            return a.strip() or "-", b.strip() or "-"
    return s, "-"


def _fmt_amount(v: Any) -> str:
    try:
        f = float(v)
    except Exception:
        return str(v or "0")
    if abs(f - round(f)) < 1e-9:
        return str(int(round(f)))
    return f"{f:.2f}".rstrip("0").rstrip(".")


def _order_time_text(order: Dict[str, Any]) -> str:
    date = str(order.get("date") or "").strip()
    clock = str(order.get("time") or "").strip()
    dt = f"{date} {clock}".strip()
    if dt:
        return dt
    return str(_fmt_ts(order.get("ts")) or "-").strip() or "-"


def _summarize_order(order: Dict[str, Any]) -> Dict[str, Any]:
    oid = str(order.get("orderId") or "").strip()
    league = str(order.get("league") or "").strip() or "未知联赛"
    sides = str(order.get("sides") or "").strip()
    home, away = _split_sides(sides)
    play_line = str(order.get("line") or "").strip() or "-"
    odds = str(order.get("odds") or "").strip()
    play = play_line if not odds else f"{play_line} @{odds}"
    amount = str(order.get("stake") or "").strip() or "0"
    source = str(
        order.get("_source_target")
        or order.get("target")
        or order.get("account")
        or "-"
    ).strip() or "-"
    return {
        "order_id": oid,
        "mode": _order_mode(order),
        "league": league,
        "sides": sides or f"{home} v {away}",
        "home": home,
        "away": away,
        "time": _order_time_text(order),
        "play": play,
        "amount": amount,
        "backend_source": source,
        "raw": order,
        "created_at": now_iso(),
    }


def build_teaser_message(order: Dict[str, Any]) -> str:
    mode = str(order.get("mode") or "早盘")
    return (
        f"⚡ 出货提醒｜{mode}\n\n"
        "🏆 ***\n"
        "⚽ *** vs ***\n\n"
        "🔒 信号已生成\n"
        "仅限已开通权限账户查看完整内容\n\n"
        "👇 点击下方按钮验证权限。"
    )


def build_full_message(order: Dict[str, Any], *, buy_contact: str, business_contact: str) -> str:
    mode = str(order.get("mode") or "早盘")
    league = str(order.get("league") or "未知联赛")
    home = str(order.get("home") or "-")
    away = str(order.get("away") or "-")
    t = str(order.get("time") or "-")
    play = str(order.get("play") or "-")
    amount = str(order.get("amount") or "0")
    return (
        f"📣 新注单提醒｜{mode}\n\n"
        f"🏆 联赛：{league}\n"
        f"⚽ 对阵：{home} vs {away}\n"
        f"⏱ 时间：{t}\n\n"
        f"🎯 推荐玩法：{play}\n"
        f"💰 金额：{amount}\n\n"
        "━━━━━━━━━━━━━━\n"
        "📌 说明\n"
        f"充值请联系 {buy_contact}\n"
        f"官方合作请联系 {business_contact}\n\n"
        "订单来源于多渠道数据整合，\n"
        "胜率长期稳定，历史 ROI 长期保持 15%+"
    )


def build_group_broadcast_message(order: Dict[str, Any]) -> str:
    mode = str(order.get("mode") or "早盘").strip() or "早盘"
    league = str(order.get("league") or "未知联赛").strip() or "未知联赛"
    sides = str(order.get("sides") or "").strip()
    if not sides:
        home = str(order.get("home") or "-").strip() or "-"
        away = str(order.get("away") or "-").strip() or "-"
        sides = f"{home} v {away}"
    play = str(order.get("play") or "").strip()
    if not play:
        line = str(order.get("line") or "-").strip() or "-"
        odds = str(order.get("odds") or "").strip()
        play = line if not odds else f"{line} @{odds}"
    amount = _fmt_amount(order.get("amount") if ("amount" in order) else order.get("stake"))
    backend_source = str(
        order.get("backend_source")
        or order.get("target")
        or order.get("account")
        or "-"
    ).strip() or "-"
    order_id = str(order.get("order_id") or order.get("orderId") or "-").strip() or "-"
    dt = str(order.get("time") or _order_time_text(order)).strip() or "-"
    return (
        "📣 新注单提醒\n\n"
        f"🏆 {league}\n"
        f"⚽ {sides}\n\n"
        f"📍 状态：{mode}\n"
        f"🎯 玩法：{play}\n\n"
        f"💰 原注金额：{amount}\n"
        "💲 跟单金额：0\n"
        "📌 跟单成功：0\n\n"
        f"👑 来源后台：{backend_source}\n"
        f"🆔 单号：{order_id}\n"
        f"⏰ 时间：{dt}"
    )


def parse_callback_data(data: Any) -> Optional[Tuple[str, str]]:
    s = str(data or "").strip()
    if not s:
        return None
    parts = s.split("|", 2)
    if len(parts) != 3:
        return None
    if parts[0] != CB_PREFIX:
        return None
    action = str(parts[1] or "").strip().lower()
    payload = str(parts[2] or "").strip()
    if action not in {"buy", "view", "check"}:
        return None
    if not payload:
        return None
    return action, payload


def _menu_keyboard() -> Dict[str, Any]:
    return {
        "keyboard": [
            [{"text": "📦 我的订单权限"}, {"text": "📮 联系客服"}],
        ],
        "resize_keyboard": True,
        "one_time_keyboard": False,
        "is_persistent": True,
    }


def _teaser_keyboard(order_id: str) -> Dict[str, Any]:
    oid = str(order_id or "").strip()
    buy_btn: Dict[str, Any] = {"text": "💳 点击购买", "callback_data": f"{CB_PREFIX}|buy|{oid}"}
    return {
        "inline_keyboard": [
            [
                buy_btn,
                {"text": "🔓 查看订单", "callback_data": f"{CB_PREFIX}|view|{oid}"},
            ]
        ]
    }


def _teaser_keyboard_with_url(order_id: str, pay_url: str) -> Dict[str, Any]:
    oid = str(order_id or "").strip()
    url = str(pay_url or "").strip()
    if not url:
        return _teaser_keyboard(oid)
    return {
        "inline_keyboard": [
            [
                {"text": "💳 点击购买", "url": url},
                {"text": "🔓 查看订单", "callback_data": f"{CB_PREFIX}|view|{oid}"},
            ]
        ]
    }


def _pay_url_keyboard(url: str, pay_no: str) -> Dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": "🧷 去 Okpay 付款", "url": str(url or "").strip()}],
            [{"text": "✅ 我已付款，立即校验", "callback_data": f"{CB_PREFIX}|check|{str(pay_no or '').strip()}"}],
        ]
    }


def _view_keyboard(order_id: str) -> Dict[str, Any]:
    return {
        "inline_keyboard": [[{"text": "🔓 查看订单", "callback_data": f"{CB_PREFIX}|view|{str(order_id or '').strip()}"}]]
    }


def _view_only_keyboard(order_id: str) -> Dict[str, Any]:
    return _view_keyboard(order_id)


def _parse_duration_seconds(raw: str) -> int:
    txt = str(raw or "").strip().lower()
    if not txt:
        return 86400
    txt = txt.replace("＋", "+")
    txt = txt.replace(" ", "")
    m = re.fullmatch(r"(\d+)(d|h|m)", txt)
    if m:
        num = int(m.group(1))
        unit = m.group(2)
        if unit == "d":
            return num * 86400
        if unit == "h":
            return num * 3600
        return num * 60
    m_cn = re.fullmatch(r"(\d+)(天|小时|时|分钟|分)", txt)
    if m_cn:
        num = int(m_cn.group(1))
        unit = m_cn.group(2)
        if unit == "天":
            return num * 86400
        if unit in {"小时", "时"}:
            return num * 3600
        return num * 60
    if txt.isdigit():
        return int(txt) * 3600
    return 0


def _resolve_target_keys(target: str, subscribers_doc: Dict[str, Any]) -> Tuple[List[str], str]:
    t = str(target or "").strip()
    if not t:
        return [], ""
    if re.fullmatch(r"-?\d+", t):
        return [f"uid:{t}"], t
    uname = _norm_username(t)
    if not uname:
        return [], ""
    keys: List[str] = [f"uname:{uname}"]
    users = subscribers_doc.get("users")
    if isinstance(users, dict):
        for uid, rec in users.items():
            if not isinstance(rec, dict):
                continue
            if _norm_username(rec.get("username")) == uname:
                keys.insert(0, f"uid:{str(uid or '').strip()}")
                break
    return list(dict.fromkeys(keys)), uname


def _order_ts(order: Dict[str, Any]) -> float:
    raw = order.get("raw") if isinstance(order.get("raw"), dict) else {}
    ts = raw.get("ts") if isinstance(raw, dict) else None
    if ts is None:
        ts = order.get("ts")
    try:
        x = float(ts)
        if x > 1_000_000_000_000:
            x = x / 1000.0
        return x
    except Exception:
        return 0.0


def _is_today_order(order: Dict[str, Any]) -> bool:
    ts = _order_ts(order)
    if ts <= 0:
        return True
    try:
        d = datetime.fromtimestamp(int(ts)).date()
        return d == datetime.now().date()
    except Exception:
        return True


def _can_view_order(
    *,
    access_doc: Dict[str, Any],
    user_id: str,
    username: str,
    order: Dict[str, Any],
) -> bool:
    oid = str(order.get("order_id") or order.get("orderId") or "").strip()
    uid = str(user_id or "").strip()
    if uid and oid and has_order_access(access_doc, user_key=f"uid:{uid}", order_id=oid):
        return True
    keys: List[str] = []
    if uid:
        keys.append(f"uid:{uid}")
    uname = _norm_username(username)
    if uname:
        keys.append(f"uname:{uname}")
    if not keys:
        return False
    if not has_day_pass(access_doc, user_keys=keys):
        return False
    return _is_today_order(order)


def _load_doc(path: Path, ensure_fn) -> Dict[str, Any]:
    raw = read_json(path) if path.exists() else {}
    return ensure_fn(raw)


def _save_doc(path: Path, doc: Dict[str, Any]) -> None:
    d = dict(doc)
    d["updated_at"] = now_iso()
    atomic_write_json(path, d)


def _find_pending_payment(payments_doc: Dict[str, Any], *, user_id: str, order_id: str) -> Optional[Dict[str, Any]]:
    items = payments_doc.get("payments")
    if not isinstance(items, dict):
        return None
    user_id = str(user_id or "").strip()
    order_id = str(order_id or "").strip()
    best: Optional[Dict[str, Any]] = None
    for pay_no, rec in items.items():
        if not isinstance(rec, dict):
            continue
        if str(rec.get("user_id") or "").strip() != user_id:
            continue
        if str(rec.get("order_id") or "").strip() != order_id:
            continue
        if str(rec.get("status") or "").strip() != "pending":
            continue
        out = dict(rec)
        out["pay_no"] = str(pay_no or "").strip()
        best = out
    return best


def _make_pay_no(order_id: str, user_id: str) -> str:
    return f"VIP{now_ts()}{str(user_id or '').strip()[-4:]}{random.randint(100, 999)}_{str(order_id or '').strip()}"


def _ensure_pending_pay_link(
    *,
    payments_doc: Dict[str, Any],
    settings: Dict[str, Any],
    user_id: str,
    order_id: str,
    proxy: str,
) -> Tuple[Dict[str, Any], str, str, bool]:
    uid = str(user_id or "").strip()
    oid = str(order_id or "").strip()
    if not uid or not oid:
        raise RuntimeError("missing user_id/order_id")
    pending = _find_pending_payment(payments_doc, user_id=uid, order_id=oid)
    pay_no = str((pending or {}).get("pay_no") or "").strip()
    pay_url = str((pending or {}).get("pay_url") or "").strip()
    if pay_no and pay_url:
        return payments_doc, pay_no, pay_url, False

    pay_no = _make_pay_no(order_id=oid, user_id=uid)
    resp = create_pay_link(
        api_base=str(settings.get("wallet_api_base") or ""),
        app_id=str(settings.get("wallet_app_id") or ""),
        token=str(settings.get("wallet_secret") or ""),
        unique_id=pay_no,
        amount=str(settings.get("price_per_order") or "8"),
        coin=str(settings.get("wallet_coin") or "USDT"),
        name=f"Order {oid}",
        return_url=str(settings.get("wallet_return_url") or ""),
        callback_url=str(settings.get("wallet_callback_url") or ""),
        timeout_s=12,
        proxy=proxy,
    )
    wallet_order_id, pay_url = parse_pay_link_result(resp)
    if not pay_url:
        code = str(resp.get("code") or "").strip() if isinstance(resp, dict) else ""
        stat = str(resp.get("status") or "").strip() if isinstance(resp, dict) else ""
        raise RuntimeError(f"empty_pay_url code={code or '-'} status={stat or '-'}")

    items = payments_doc.setdefault("payments", {})
    if not isinstance(items, dict):
        items = {}
        payments_doc["payments"] = items
    items[pay_no] = {
        "pay_no": pay_no,
        "wallet_order_id": wallet_order_id,
        "user_id": uid,
        "order_id": oid,
        "amount": str(settings.get("price_per_order") or "8"),
        "coin": str(settings.get("wallet_coin") or "USDT"),
        "pay_url": pay_url,
        "status": "pending",
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "last_check_at": "",
        "check_count": 0,
    }
    return payments_doc, pay_no, pay_url, True


def _mark_payment_paid(
    *,
    payments_doc: Dict[str, Any],
    access_doc: Dict[str, Any],
    pay_no: str,
) -> Tuple[Dict[str, Any], Dict[str, Any], Optional[Dict[str, Any]]]:
    items = payments_doc.get("payments")
    if not isinstance(items, dict):
        return payments_doc, access_doc, None
    rec = items.get(pay_no)
    if not isinstance(rec, dict):
        return payments_doc, access_doc, None
    if str(rec.get("status") or "").strip() == "paid":
        return payments_doc, access_doc, rec
    rec["status"] = "paid"
    rec["paid_at"] = now_iso()
    rec["updated_at"] = now_iso()
    items[pay_no] = rec
    user_id = str(rec.get("user_id") or "").strip()
    order_id = str(rec.get("order_id") or "").strip()
    if user_id and order_id:
        access_doc, _ = grant_order_access(
            access_doc,
            user_key=f"uid:{user_id}",
            order_id=order_id,
            pay_no=pay_no,
        )
    return payments_doc, access_doc, rec


def _extract_new_orders(
    *,
    orders_dir: Path,
    state: Dict[str, Any],
) -> Iterable[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    offs = state.get("order_offsets")
    if not isinstance(offs, dict):
        offs = {}
        state["order_offsets"] = offs
    for path in sorted(orders_dir.glob("*.jsonl")):
        key = path.name
        size = int(path.stat().st_size)
        prev = int(offs.get(key) or 0)
        # 首次见到文件：直接同步到末尾（不回放历史）
        if key not in offs:
            offs[key] = size
            continue
        if prev > size:
            prev = 0
        if prev == size:
            continue
        try:
            with path.open("rb") as f:
                f.seek(prev)
                while True:
                    line = f.readline()
                    if not line:
                        break
                    offs[key] = int(f.tell())
                    try:
                        obj = json.loads(line.decode("utf-8", errors="ignore").strip())
                    except Exception:
                        continue
                    if isinstance(obj, dict):
                        obj.setdefault("_source_target", str(path.stem or "").strip())
                        out.append(obj)
        except Exception:
            continue
    return out


def _is_admin(user_id: str, settings: Dict[str, Any]) -> bool:
    uid = str(user_id or "").strip()
    if not uid:
        return False
    for x in settings.get("admin_ids") or []:
        if uid == str(x).strip():
            return True
    return False


def _resolve_teaser_image_path(settings: Dict[str, Any]) -> Optional[Path]:
    raw = str(settings.get("teaser_image_path") or "").strip()
    if not raw:
        return None
    p = Path(raw)
    candidates: List[Path] = []
    if p.is_absolute():
        candidates.append(p)
    else:
        candidates.extend(
            [
                Path.cwd() / p,
                REPO_ROOT / p,
            ]
        )
    for c in candidates:
        try:
            if c.exists() and c.is_file():
                return c
        except Exception:
            continue
    return None


def _send_text(
    *,
    bot_token: str,
    chat_id: str,
    text: str,
    proxy: str,
    reply_markup: Optional[Dict[str, Any]] = None,
) -> None:
    tg_send_message(
        bot_token=bot_token,
        chat_id=chat_id,
        html_text=text,
        reply_markup=reply_markup,
        proxy=proxy,
        timeout_s=12,
    )


def _send_photo(
    *,
    bot_token: str,
    chat_id: str,
    caption: str,
    image_path: Path,
    proxy: str,
    reply_markup: Optional[Dict[str, Any]] = None,
) -> None:
    bot_token = str(bot_token or "").strip()
    chat_id = str(chat_id or "").strip()
    if not bot_token or not chat_id:
        raise RuntimeError("missing bot_token/chat_id")
    if image_path is None or (not image_path.exists()) or (not image_path.is_file()):
        raise RuntimeError(f"image_not_found: {image_path}")
    url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
    proxies = {"http": proxy, "https": proxy} if str(proxy or "").strip() else None
    data: Dict[str, Any] = {
        "chat_id": chat_id,
        "caption": str(caption or ""),
        "parse_mode": "HTML",
    }
    if reply_markup is not None:
        data["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
    with image_path.open("rb") as f:
        files = {"photo": (image_path.name, f)}
        r = requests.post(url, data=data, files=files, timeout=15, proxies=proxies)
    try:
        r.raise_for_status()
    except Exception as e:
        body = ""
        try:
            body = str(r.text or "").strip()
        except Exception:
            body = ""
        if body:
            raise RuntimeError(f"sendPhoto_http_error: {e}; body={body}") from e
        raise
    j = r.json()
    if not isinstance(j, dict) or (not j.get("ok")):
        raise RuntimeError(f"sendPhoto_telegram_error: {j}")


def _edit_text(
    *,
    bot_token: str,
    chat_id: str,
    message_id: int,
    text: str,
    proxy: str,
    reply_markup: Optional[Dict[str, Any]] = None,
) -> None:
    bot_token = str(bot_token or "").strip()
    chat_id = str(chat_id or "").strip()
    if not bot_token or not chat_id or int(message_id) <= 0:
        raise RuntimeError("missing bot_token/chat_id/message_id")
    url = f"https://api.telegram.org/bot{bot_token}/editMessageText"
    proxies = {"http": proxy, "https": proxy} if str(proxy or "").strip() else None
    payload: Dict[str, Any] = {
        "chat_id": chat_id,
        "message_id": int(message_id),
        "text": str(text or ""),
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    r = requests.post(url, json=payload, timeout=12, proxies=proxies)
    try:
        r.raise_for_status()
    except Exception as e:
        body = ""
        try:
            body = str(r.text or "").strip()
        except Exception:
            body = ""
        if "message is not modified" in body.lower():
            return
        if body:
            raise RuntimeError(f"editMessageText_http_error: {e}; body={body}") from e
        raise
    data = r.json()
    if not isinstance(data, dict) or (not data.get("ok")):
        raise RuntimeError(f"editMessageText_telegram_error: {data}")


def _edit_caption(
    *,
    bot_token: str,
    chat_id: str,
    message_id: int,
    caption: str,
    proxy: str,
    reply_markup: Optional[Dict[str, Any]] = None,
) -> None:
    bot_token = str(bot_token or "").strip()
    chat_id = str(chat_id or "").strip()
    if not bot_token or not chat_id or int(message_id) <= 0:
        raise RuntimeError("missing bot_token/chat_id/message_id")
    url = f"https://api.telegram.org/bot{bot_token}/editMessageCaption"
    proxies = {"http": proxy, "https": proxy} if str(proxy or "").strip() else None
    payload: Dict[str, Any] = {
        "chat_id": chat_id,
        "message_id": int(message_id),
        "caption": str(caption or ""),
        "parse_mode": "HTML",
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    r = requests.post(url, json=payload, timeout=12, proxies=proxies)
    try:
        r.raise_for_status()
    except Exception as e:
        body = ""
        try:
            body = str(r.text or "").strip()
        except Exception:
            body = ""
        if "message is not modified" in body.lower():
            return
        if body:
            raise RuntimeError(f"editMessageCaption_http_error: {e}; body={body}") from e
        raise
    data = r.json()
    if not isinstance(data, dict) or (not data.get("ok")):
        raise RuntimeError(f"editMessageCaption_telegram_error: {data}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Premium Telegram Bot (OkayPay)")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--orders-dir", default="orders")
    parser.add_argument("--state", default=".run/tg_premium_state.json")
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument("--timeout", type=int, default=20)
    args = parser.parse_args()

    settings = dict(PREMIUM_SETTINGS)
    bot_token = str(settings.get("bot_token") or "").strip()
    proxy = str(settings.get("proxy") or "").strip()
    teaser_image_path = _resolve_teaser_image_path(settings)
    free_push_mode = bool(settings.get("free_push_mode", True))
    if not bot_token:
        print(f"[{now_iso()}] premium_bot missing bot_token", flush=True)
        return 2

    data_dir = Path(args.data_dir)
    orders_dir = Path(args.orders_dir)
    state_path = Path(args.state)
    data_dir.mkdir(parents=True, exist_ok=True)
    state_path.parent.mkdir(parents=True, exist_ok=True)

    subscribers_path = data_dir / "premium_subscribers.json"
    orders_cache_path = data_dir / "premium_orders.json"
    payments_path = data_dir / "premium_payments.json"
    access_path = data_dir / "premium_access.json"

    state = _read_state(state_path)
    subscribers_doc = _load_doc(subscribers_path, ensure_subscribers_doc)
    orders_doc = _load_doc(orders_cache_path, ensure_orders_doc)
    payments_doc = _load_doc(payments_path, ensure_payments_doc)
    access_doc = _load_doc(access_path, ensure_access_doc)

    print(
        f"[{now_iso()}] premium_bot started data_dir={data_dir} orders_dir={orders_dir} state={state_path}",
        flush=True,
    )
    if teaser_image_path:
        print(f"[{now_iso()}] premium_bot teaser_image={teaser_image_path}", flush=True)
    else:
        print(f"[{now_iso()}] premium_bot teaser_image=disabled", flush=True)

    while True:
        changed_state = False
        changed_subscribers = False
        changed_orders = False
        changed_payments = False
        changed_access = False
        try:
            updates = tg_get_updates(
                bot_token=bot_token,
                offset=int(state.get("offset") or 0),
                timeout_s=int(args.timeout),
                proxy=proxy,
            )
            for u in updates:
                try:
                    uid = int(u.get("update_id") or 0)
                except Exception:
                    uid = 0
                if uid and uid >= int(state.get("offset") or 0):
                    state["offset"] = uid + 1
                    changed_state = True

                cb = u.get("callback_query") or {}
                if isinstance(cb, dict) and cb:
                    cb_id = str(cb.get("id") or "").strip()
                    data = parse_callback_data(cb.get("data"))
                    msg = cb.get("message") or {}
                    chat = msg.get("chat") if isinstance(msg, dict) else {}
                    msg_id = int((msg or {}).get("message_id") or 0) if isinstance(msg, dict) else 0
                    frm = cb.get("from") or {}
                    chat_id = str((chat or {}).get("id") or "").strip()
                    user_id = str((frm or {}).get("id") or "").strip()
                    if not cb_id or not data or not chat_id or not user_id:
                        continue
                    if _touch_subscriber_user(
                        subscribers_doc,
                        user_id=user_id,
                        username=str((frm or {}).get("username") or ""),
                    ):
                        changed_subscribers = True
                    action, payload = data

                    if free_push_mode:
                        oid = str(payload or "").strip()
                        if action == "view":
                            order = (orders_doc.get("orders") or {}).get(oid) if isinstance(orders_doc.get("orders"), dict) else None
                            if not isinstance(order, dict):
                                safe_tg_answer_callback_query(
                                    bot_token=bot_token,
                                    callback_query_id=cb_id,
                                    text="订单已过期或不存在。",
                                    show_alert=True,
                                    proxy=proxy,
                                    timeout_s=8,
                                    context=f"view:{oid}:missing:free",
                                )
                                continue
                            full = build_group_broadcast_message(order)
                            try:
                                msg_has_photo = bool((msg or {}).get("photo")) if isinstance(msg, dict) else False
                                if msg_has_photo:
                                    _edit_caption(
                                        bot_token=bot_token,
                                        chat_id=chat_id,
                                        message_id=msg_id,
                                        caption=full,
                                        proxy=proxy,
                                        reply_markup=None,
                                    )
                                else:
                                    _edit_text(
                                        bot_token=bot_token,
                                        chat_id=chat_id,
                                        message_id=msg_id,
                                        text=full,
                                        proxy=proxy,
                                        reply_markup=None,
                                    )
                            except Exception as e:
                                safe_tg_answer_callback_query(
                                    bot_token=bot_token,
                                    callback_query_id=cb_id,
                                    text="显示订单失败，请重试。",
                                    show_alert=True,
                                    proxy=proxy,
                                    timeout_s=8,
                                    context=f"view:{oid}:edit_err:free:{e}",
                                )
                                continue
                            safe_tg_answer_callback_query(
                                bot_token=bot_token,
                                callback_query_id=cb_id,
                                text="已显示完整订单。",
                                show_alert=False,
                                proxy=proxy,
                                timeout_s=8,
                                context=f"view:{oid}:ok:free",
                            )
                            continue

                        safe_tg_answer_callback_query(
                            bot_token=bot_token,
                            callback_query_id=cb_id,
                            text="当前已切换免费推送模式，无需购买或验权。",
                            show_alert=True,
                            proxy=proxy,
                            timeout_s=8,
                            context=f"{action}:{oid}:free",
                        )
                        continue

                    if action == "view":
                        oid = str(payload or "").strip()
                        if not oid:
                            continue
                        order = (orders_doc.get("orders") or {}).get(oid) if isinstance(orders_doc.get("orders"), dict) else None
                        if not isinstance(order, dict):
                            safe_tg_answer_callback_query(
                                bot_token=bot_token,
                                callback_query_id=cb_id,
                                text="订单已过期或不存在。",
                                show_alert=True,
                                proxy=proxy,
                                timeout_s=8,
                                context=f"view:{oid}:missing",
                            )
                            continue
                        cb_username = _norm_username(frm.get("username") or "") or _get_username_from_subscribers(
                            subscribers_doc, user_id
                        )
                        if not _can_view_order(
                            access_doc=access_doc,
                            user_id=user_id,
                            username=cb_username,
                            order=order,
                        ):
                            pending = _find_pending_payment(payments_doc, user_id=user_id, order_id=oid)
                            pay_no = str((pending or {}).get("pay_no") or "").strip()
                            if pay_no:
                                try:
                                    resp = check_deposit(
                                        api_base=str(settings.get("wallet_api_base") or ""),
                                        app_id=str(settings.get("wallet_app_id") or ""),
                                        token=str(settings.get("wallet_secret") or ""),
                                        unique_id=pay_no,
                                        timeout_s=12,
                                        proxy=proxy,
                                    )
                                    paid = is_deposit_paid(resp)
                                    items = payments_doc.get("payments")
                                    rec = items.get(pay_no) if isinstance(items, dict) else None
                                    if isinstance(rec, dict):
                                        rec["last_check_at"] = now_iso()
                                        rec["check_count"] = int(rec.get("check_count") or 0) + 1
                                        rec["updated_at"] = now_iso()
                                        changed_payments = True
                                    if paid:
                                        payments_doc, access_doc, done = _mark_payment_paid(
                                            payments_doc=payments_doc,
                                            access_doc=access_doc,
                                            pay_no=pay_no,
                                        )
                                        changed_payments = True
                                        changed_access = True
                                        oid_done = str((done or {}).get("order_id") or "").strip()
                                        safe_tg_answer_callback_query(
                                            bot_token=bot_token,
                                            callback_query_id=cb_id,
                                            text="✅ 已到账，正在打开订单。",
                                            show_alert=False,
                                            proxy=proxy,
                                            timeout_s=8,
                                            context=f"view:{oid}:auto_paid",
                                        )
                                        if oid_done:
                                            oid = oid_done
                                except Exception as e:
                                    safe_tg_answer_callback_query(
                                        bot_token=bot_token,
                                        callback_query_id=cb_id,
                                        text="支付状态查询失败，请稍后重试。",
                                        show_alert=True,
                                        proxy=proxy,
                                        timeout_s=8,
                                        context=f"view:{oid}:auto_check_err:{e}",
                                    )
                                    continue
                            if not _can_view_order(
                                access_doc=access_doc,
                                user_id=user_id,
                                username=cb_username,
                                order=order,
                            ):
                                safe_tg_answer_callback_query(
                                    bot_token=bot_token,
                                    callback_query_id=cb_id,
                                    text="❌ 未开通该订单权限，请先点击购买。",
                                    show_alert=True,
                                    proxy=proxy,
                                    timeout_s=8,
                                    context=f"view:{oid}:deny",
                                )
                                continue
                        full = build_full_message(
                            order,
                            buy_contact=str(settings.get("buy_contact") or "@sgty888"),
                            business_contact=str(settings.get("business_contact") or "@sgty888"),
                        )
                        try:
                            msg_has_photo = bool((msg or {}).get("photo")) if isinstance(msg, dict) else False
                            if msg_has_photo:
                                _edit_caption(
                                    bot_token=bot_token,
                                    chat_id=chat_id,
                                    message_id=msg_id,
                                    caption=full,
                                    proxy=proxy,
                                    reply_markup=None,
                                )
                            else:
                                _edit_text(
                                    bot_token=bot_token,
                                    chat_id=chat_id,
                                    message_id=msg_id,
                                    text=full,
                                    proxy=proxy,
                                    reply_markup=None,
                                )
                        except Exception as e:
                            safe_tg_answer_callback_query(
                                bot_token=bot_token,
                                callback_query_id=cb_id,
                                text="显示订单失败，请重试。",
                                show_alert=True,
                                proxy=proxy,
                                timeout_s=8,
                                context=f"view:{oid}:edit_err:{e}",
                            )
                            continue
                        safe_tg_answer_callback_query(
                            bot_token=bot_token,
                            callback_query_id=cb_id,
                            text="已显示完整订单。",
                            show_alert=False,
                            proxy=proxy,
                            timeout_s=8,
                            context=f"view:{oid}:ok",
                        )
                        continue

                    if action == "buy":
                        oid = str(payload or "").strip()
                        order = (orders_doc.get("orders") or {}).get(oid) if isinstance(orders_doc.get("orders"), dict) else None
                        if not isinstance(order, dict):
                            safe_tg_answer_callback_query(
                                bot_token=bot_token,
                                callback_query_id=cb_id,
                                text="订单不存在或已过期。",
                                show_alert=True,
                                proxy=proxy,
                                timeout_s=8,
                                context=f"buy:{oid}:missing",
                            )
                            continue
                        cb_username = _norm_username(frm.get("username") or "") or _get_username_from_subscribers(
                            subscribers_doc, user_id
                        )
                        if _can_view_order(
                            access_doc=access_doc,
                            user_id=user_id,
                            username=cb_username,
                            order=order,
                        ):
                            safe_tg_answer_callback_query(
                                bot_token=bot_token,
                                callback_query_id=cb_id,
                                text="你已开通该订单权限，点“查看订单”即可。",
                                show_alert=True,
                                proxy=proxy,
                                timeout_s=8,
                                context=f"buy:{oid}:already",
                            )
                            continue

                        pending = _find_pending_payment(payments_doc, user_id=user_id, order_id=oid)
                        pay_no = str((pending or {}).get("pay_no") or "").strip()
                        pay_url = str((pending or {}).get("pay_url") or "").strip()
                        if not pay_no or not pay_url:
                            try:
                                payments_doc, pay_no, pay_url, created = _ensure_pending_pay_link(
                                    payments_doc=payments_doc,
                                    settings=settings,
                                    user_id=user_id,
                                    order_id=oid,
                                    proxy=proxy,
                                )
                                if created:
                                    changed_payments = True
                            except Exception as e:
                                safe_tg_answer_callback_query(
                                    bot_token=bot_token,
                                    callback_query_id=cb_id,
                                    text="创建支付链接失败，请稍后再试。",
                                    show_alert=True,
                                    proxy=proxy,
                                    timeout_s=8,
                                    context=f"buy:{oid}:error:{e}",
                                )
                                continue
                        pay_text = (
                            "👇 请点击下方按钮支付\n\n"
                            f"订单：{oid}\n"
                            f"支付金额：{str(settings.get('price_per_order') or '8')} {str(settings.get('wallet_coin') or 'USDT')}\n"
                            f"充值联系：{str(settings.get('buy_contact') or '@sgty888')}"
                        )
                        _send_text(
                            bot_token=bot_token,
                            chat_id=chat_id,
                            text=pay_text,
                            proxy=proxy,
                            reply_markup=_pay_url_keyboard(pay_url, pay_no),
                        )
                        safe_tg_answer_callback_query(
                            bot_token=bot_token,
                            callback_query_id=cb_id,
                            text="已生成支付链接。",
                            show_alert=False,
                            proxy=proxy,
                            timeout_s=8,
                            context=f"buy:{oid}:ok",
                        )
                        continue

                    if action == "check":
                        pay_no = str(payload or "").strip()
                        items = payments_doc.get("payments")
                        rec = items.get(pay_no) if isinstance(items, dict) else None
                        if not isinstance(rec, dict):
                            safe_tg_answer_callback_query(
                                bot_token=bot_token,
                                callback_query_id=cb_id,
                                text="支付单不存在。",
                                show_alert=True,
                                proxy=proxy,
                                timeout_s=8,
                                context=f"check:{pay_no}:missing",
                            )
                            continue
                        owner = str(rec.get("user_id") or "").strip()
                        if owner and owner != user_id and (not _is_admin(user_id, settings)):
                            safe_tg_answer_callback_query(
                                bot_token=bot_token,
                                callback_query_id=cb_id,
                                text="无权操作该支付单。",
                                show_alert=True,
                                proxy=proxy,
                                timeout_s=8,
                                context=f"check:{pay_no}:forbidden",
                            )
                            continue
                        try:
                            resp = check_deposit(
                                api_base=str(settings.get("wallet_api_base") or ""),
                                app_id=str(settings.get("wallet_app_id") or ""),
                                token=str(settings.get("wallet_secret") or ""),
                                unique_id=pay_no,
                                timeout_s=12,
                                proxy=proxy,
                            )
                            paid = is_deposit_paid(resp)
                        except Exception as e:
                            safe_tg_answer_callback_query(
                                bot_token=bot_token,
                                callback_query_id=cb_id,
                                text="查单失败，请稍后重试。",
                                show_alert=True,
                                proxy=proxy,
                                timeout_s=8,
                                context=f"check:{pay_no}:error:{e}",
                            )
                            continue
                        rec["last_check_at"] = now_iso()
                        rec["check_count"] = int(rec.get("check_count") or 0) + 1
                        rec["updated_at"] = now_iso()
                        changed_payments = True
                        if paid:
                            payments_doc, access_doc, done = _mark_payment_paid(
                                payments_doc=payments_doc,
                                access_doc=access_doc,
                                pay_no=pay_no,
                            )
                            changed_payments = True
                            changed_access = True
                            oid = str((done or {}).get("order_id") or "").strip()
                            safe_tg_answer_callback_query(
                                bot_token=bot_token,
                                callback_query_id=cb_id,
                                text="✅ 到账成功，权限已开通。",
                                show_alert=True,
                                proxy=proxy,
                                timeout_s=8,
                                context=f"check:{pay_no}:paid",
                            )
                        else:
                            safe_tg_answer_callback_query(
                                bot_token=bot_token,
                                callback_query_id=cb_id,
                                text="尚未到账，请完成付款后再点一次。",
                                show_alert=True,
                                proxy=proxy,
                                timeout_s=8,
                                context=f"check:{pay_no}:pending",
                            )
                        continue

                msg = u.get("message") or {}
                if not isinstance(msg, dict):
                    continue
                text = str(msg.get("text") or "").strip()
                chat = msg.get("chat") or {}
                frm = msg.get("from") or {}
                chat_id = str(chat.get("id") or "").strip()
                user_id = str(frm.get("id") or "").strip()
                if not chat_id or not user_id:
                    continue
                if _touch_subscriber_user(
                    subscribers_doc,
                    user_id=user_id,
                    username=str(frm.get("username") or ""),
                ):
                    changed_subscribers = True
                chat_type = str(chat.get("type") or "").strip()
                is_private = chat_type == "private"
                lower = text.lower()

                if is_private and (lower.startswith("/start") or text == "📮 联系客服"):
                    subs = subscribers_doc.get("subscribers")
                    if not isinstance(subs, list):
                        subs = []
                        subscribers_doc["subscribers"] = subs
                    if chat_id not in subs:
                        subs.append(chat_id)
                        subs[:] = sorted(set(_ensure_list_str(subs)))
                        changed_subscribers = True
                    _send_text(
                        bot_token=bot_token,
                        chat_id=chat_id,
                        text=(
                            "✅ 已接入信号机器人。\n"
                            + (
                                "新订单将直接推送完整内容（免费模式，无需购买/点击查看）。\n"
                                if free_push_mode
                                else "新订单会推送隐藏预告，你可点击“购买/查看订单”。\n"
                            )
                            + f"联系：{str(settings.get('buy_contact') or '@sgty888')}"
                        ),
                        proxy=proxy,
                        reply_markup=_menu_keyboard(),
                    )
                    continue

                if is_private and text == "📦 我的订单权限":
                    if free_push_mode:
                        _send_text(
                            bot_token=bot_token,
                            chat_id=chat_id,
                            text="当前为免费推送模式，无需订单权限。",
                            proxy=proxy,
                            reply_markup=_menu_keyboard(),
                        )
                        continue
                    doc_u = ensure_access_doc(access_doc)
                    users = doc_u.get("users") if isinstance(doc_u.get("users"), dict) else {}
                    udoc = users.get(f"uid:{user_id}") if isinstance(users, dict) else {}
                    orders = udoc.get("orders") if isinstance(udoc, dict) and isinstance(udoc.get("orders"), dict) else {}
                    cnt = len(orders)
                    _send_text(
                        bot_token=bot_token,
                        chat_id=chat_id,
                        text=f"当前已开通订单权限：{cnt} 单",
                        proxy=proxy,
                        reply_markup=_menu_keyboard(),
                    )
                    continue

                # admin commands
                if is_private and _is_admin(user_id, settings) and (
                    lower.startswith("/grant_day") or lower.startswith("/开卡") or text.startswith("开卡")
                ):
                    normalized = str(text or "").replace("＋", "+").strip()
                    parts = [x for x in re.split(r"[\s+]+", normalized) if str(x or "").strip()]
                    if len(parts) < 2:
                        _send_text(
                            bot_token=bot_token,
                            chat_id=chat_id,
                            text="用法：开卡+用户名+时间（例：开卡 @abc 1d，支持 d/h/m、天/小时/分钟）",
                            proxy=proxy,
                        )
                        continue
                    target = str(parts[1] or "").strip()
                    span = str(parts[2] or "1d").strip() if len(parts) > 2 else "1d"
                    seconds = _parse_duration_seconds(span)
                    if seconds <= 0:
                        _send_text(
                            bot_token=bot_token,
                            chat_id=chat_id,
                            text="时间格式不对，示例：1d / 12h / 30m / 1天",
                            proxy=proxy,
                        )
                        continue
                    target_keys, target_name = _resolve_target_keys(target, subscribers_doc)
                    if not target_keys:
                        _send_text(
                            bot_token=bot_token,
                            chat_id=chat_id,
                            text="找不到目标用户，请填用户ID或 @用户名。",
                            proxy=proxy,
                        )
                        continue
                    grant_key = target_keys[0]
                    access_doc, rec = grant_day_pass(
                        access_doc,
                        user_key=grant_key,
                        duration_seconds=seconds,
                        granted_by=f"uid:{user_id}",
                    )
                    changed_access = True
                    exp = str(rec.get("expires_at") or "-").strip() if isinstance(rec, dict) else "-"
                    who = target_name or target
                    _send_text(
                        bot_token=bot_token,
                        chat_id=chat_id,
                        text=f"✅ 已开天卡\n用户：{who}\n有效期至：{exp}\n范围：今日赛事可免费查看",
                        proxy=proxy,
                    )
                    continue

                if is_private and _is_admin(user_id, settings) and lower.startswith("/pay_query"):
                    parts = text.split()
                    pay_no = str(parts[1] if len(parts) > 1 else "").strip()
                    rec = (payments_doc.get("payments") or {}).get(pay_no) if isinstance(payments_doc.get("payments"), dict) else None
                    if not isinstance(rec, dict):
                        _send_text(bot_token=bot_token, chat_id=chat_id, text="支付单不存在。", proxy=proxy)
                        continue
                    _send_text(
                        bot_token=bot_token,
                        chat_id=chat_id,
                        text=(
                            f"pay_no: {pay_no}\n"
                            f"user_id: {rec.get('user_id')}\n"
                            f"order_id: {rec.get('order_id')}\n"
                            f"status: {rec.get('status')}\n"
                            f"amount: {rec.get('amount')} {rec.get('coin')}"
                        ),
                        proxy=proxy,
                    )
                    continue

                if is_private and _is_admin(user_id, settings) and lower.startswith("/pay_ok"):
                    parts = text.split()
                    pay_no = str(parts[1] if len(parts) > 1 else "").strip()
                    payments_doc, access_doc, done = _mark_payment_paid(
                        payments_doc=payments_doc,
                        access_doc=access_doc,
                        pay_no=pay_no,
                    )
                    if done is None:
                        _send_text(bot_token=bot_token, chat_id=chat_id, text="支付单不存在。", proxy=proxy)
                        continue
                    changed_payments = True
                    changed_access = True
                    _send_text(
                        bot_token=bot_token,
                        chat_id=chat_id,
                        text=f"已手动确认到账：{pay_no}",
                        proxy=proxy,
                    )
                    continue

            # scan new orders
            new_orders = _extract_new_orders(orders_dir=orders_dir, state=state)
            if new_orders:
                changed_state = True
                od = orders_doc.get("orders")
                if not isinstance(od, dict):
                    od = {}
                    orders_doc["orders"] = od
                subs = _ensure_list_str(subscribers_doc.get("subscribers"))
                for o in new_orders:
                    oid = str(o.get("orderId") or "").strip()
                    if not oid:
                        continue
                    if oid in od:
                        continue
                    s = _summarize_order(o)
                    od[oid] = s
                    changed_orders = True
                    if subs:
                        if free_push_mode:
                            broadcast = build_group_broadcast_message(s)
                            for sid in list(subs):
                                try:
                                    _send_text(
                                        bot_token=bot_token,
                                        chat_id=sid,
                                        text=broadcast,
                                        proxy=proxy,
                                        reply_markup=None,
                                    )
                                except Exception:
                                    continue
                        else:
                            teaser = build_teaser_message(s)
                            for sid in list(subs):
                                sub_username = _get_username_from_subscribers(subscribers_doc, sid)
                                if _can_view_order(
                                    access_doc=access_doc,
                                    user_id=sid,
                                    username=sub_username,
                                    order=s,
                                ):
                                    kb = _view_only_keyboard(oid)
                                else:
                                    kb = _teaser_keyboard(oid)
                                    try:
                                        payments_doc, _pay_no, pay_url, created = _ensure_pending_pay_link(
                                            payments_doc=payments_doc,
                                            settings=settings,
                                            user_id=sid,
                                            order_id=oid,
                                            proxy=proxy,
                                        )
                                        if created:
                                            changed_payments = True
                                        if pay_url:
                                            kb = _teaser_keyboard_with_url(oid, pay_url)
                                    except Exception:
                                        kb = _teaser_keyboard(oid)
                                try:
                                    if teaser_image_path:
                                        _send_photo(
                                            bot_token=bot_token,
                                            chat_id=sid,
                                            caption=teaser,
                                            image_path=teaser_image_path,
                                            proxy=proxy,
                                            reply_markup=kb,
                                        )
                                    else:
                                        _send_text(
                                            bot_token=bot_token,
                                            chat_id=sid,
                                            text=teaser,
                                            proxy=proxy,
                                            reply_markup=kb,
                                        )
                                except Exception:
                                    try:
                                        _send_text(
                                            bot_token=bot_token,
                                            chat_id=sid,
                                            text=teaser,
                                            proxy=proxy,
                                            reply_markup=kb,
                                        )
                                    except Exception:
                                        continue
                # cache cleanup
                if len(od) > 5000:
                    keys = list(od.keys())
                    for k in keys[:-3000]:
                        od.pop(k, None)
                    changed_orders = True

            # payment poll
            poll_enabled = bool(settings.get("wallet_poll_enabled"))
            poll_itv = int(settings.get("wallet_poll_interval_s") or 15)
            last_poll = int(state.get("last_payment_poll_ts") or 0)
            if poll_enabled and (now_ts() - last_poll >= max(5, poll_itv)):
                items = payments_doc.get("payments")
                if isinstance(items, dict):
                    for pay_no, rec in list(items.items()):
                        if not isinstance(rec, dict):
                            continue
                        if str(rec.get("status") or "").strip() != "pending":
                            continue
                        try:
                            resp = check_deposit(
                                api_base=str(settings.get("wallet_api_base") or ""),
                                app_id=str(settings.get("wallet_app_id") or ""),
                                token=str(settings.get("wallet_secret") or ""),
                                unique_id=str(pay_no),
                                timeout_s=12,
                                proxy=proxy,
                            )
                            paid = is_deposit_paid(resp)
                        except Exception:
                            continue
                        rec["last_check_at"] = now_iso()
                        rec["check_count"] = int(rec.get("check_count") or 0) + 1
                        rec["updated_at"] = now_iso()
                        changed_payments = True
                        if paid:
                            payments_doc, access_doc, done = _mark_payment_paid(
                                payments_doc=payments_doc,
                                access_doc=access_doc,
                                pay_no=str(pay_no),
                            )
                            changed_payments = True
                            changed_access = True
                state["last_payment_poll_ts"] = now_ts()
                changed_state = True

            if changed_subscribers:
                _save_doc(subscribers_path, subscribers_doc)
            if changed_orders:
                _save_doc(orders_cache_path, orders_doc)
            if changed_payments:
                _save_doc(payments_path, payments_doc)
            if changed_access:
                _save_doc(access_path, access_doc)
            if changed_state:
                _write_state(state_path, state)
        except Exception as e:
            print(f"[{now_iso()}] premium_bot error: {e}", flush=True)

        time.sleep(max(0.2, float(args.poll_interval)))


if __name__ == "__main__":
    raise SystemExit(main())
