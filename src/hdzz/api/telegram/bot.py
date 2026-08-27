import argparse
import html
import hashlib
import json
import math
import re
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import urlencode
import uuid
import xml.etree.ElementTree as ET

import requests


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

BJ_TZ = timezone(timedelta(hours=8))
REPORT_DAY_CUTOFF_HOUR = 12  # 12:00-次日12:00 算一天（北京时间）
TARGET_ONLINE_TTL_S = 20.0

def now_bj() -> datetime:
    return datetime.now(tz=BJ_TZ)

def report_day_window(now: Optional[datetime] = None) -> Tuple[datetime, datetime]:
    """
    报表“当日”统计窗口：北京时间中午12点 ~ 次日中午12点
    返回 [start, end)
    """
    dt = now or now_bj()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=BJ_TZ)
    dt = dt.astimezone(BJ_TZ)
    cutoff = dt.replace(hour=REPORT_DAY_CUTOFF_HOUR, minute=0, second=0, microsecond=0)
    if dt < cutoff:
        cutoff = cutoff - timedelta(days=1)
    start = cutoff
    end = start + timedelta(days=1)
    return start, end


def report_day_window_by_offset(day_shift: int, now: Optional[datetime] = None) -> Tuple[datetime, datetime]:
    start, end = report_day_window(now=now)
    shift = timedelta(days=int(day_shift or 0))
    return start + shift, end + shift


def report_day_start(dt: datetime) -> datetime:
    start, _ = report_day_window(dt)
    return start

def today_str() -> str:
    return now_bj().strftime("%Y-%m-%d")

MENU_MAIN = "main"
MENU_ADMIN = "admin"

BTN_QUERY_BALANCE = "💰 查询余额"
BTN_NODE_STATUS = "🟢 节点状态"
BTN_TODAY_REPORT = "📊 今日跟单"
BTN_PNL_REPORT = "📉 输赢报表"
BTN_WINRATE = "📈 投资回报率"
BTN_ADMIN_AUTH = "🔐 管理员授权"

BTN_ADMIN_GRANT = "✅ 授权用户"
BTN_ADMIN_REVOKE = "🚫 取消授权"
BTN_ADMIN_LIST = "📋 查看授权"
BTN_BACK = "⬅️ 返回菜单"
ORDER_CB_PREFIX = "odr"
REPORT_CB_PREFIX = "rpt"


def safe_target_name(name: str) -> str:
    s = "".join(c for c in str(name) if c.isalnum() or c in ("-", "_")).strip()
    return s or "default"


def _decode_handicap_line(con: Any, ratio: Any) -> Optional[float]:
    """
    让球盘线解码：line ≈ -con + ratio/200
    （与 follow_engine.encode_handicap_line 约定一致）
    """
    try:
        c = float(str(con).strip())
        r = float(str(ratio).strip())
        return (-c) + (r / 200.0)
    except Exception:
        return None


def _format_handicap_line_value(line_val: float) -> str:
    try:
        x = float(line_val)
    except Exception:
        return str(line_val)
    if abs(x) < 1e-9:
        return "0"
    sign = "+" if x > 0 else "-"
    ax = abs(x)
    base = int(math.floor(ax + 1e-9))
    frac = ax - base
    q = int(round(frac * 4))
    frac_q = max(0.0, min(0.75, q / 4.0))

    def fmt_num(v: float) -> str:
        if abs(v - round(v)) < 1e-9:
            return str(int(round(v)))
        s = f"{v:.2f}".rstrip("0").rstrip(".")
        return s or str(v)

    if frac_q == 0.0:
        return f"{sign}{fmt_num(float(base))}"
    if frac_q == 0.5:
        return f"{sign}{fmt_num(base + 0.5)}"
    if frac_q == 0.25:
        return f"{sign}{fmt_num(float(base))} / {fmt_num(base + 0.5)}"
    return f"{sign}{fmt_num(base + 0.5)} / {fmt_num(base + 1.0)}"


def _build_follow_line_display(order_line: Any, params: Dict[str, Any]) -> str:
    order_line = str(order_line or "").strip() or "未知"
    wtype = str(params.get("wtype") or "").strip()
    con = params.get("con")
    ratio = params.get("ratio")
    if wtype in {"R", "RE", "HRE"} and con is not None and ratio is not None:
        lv = _decode_handicap_line(con, ratio)
        if lv is None:
            return order_line
        if " " in order_line:
            prefix = order_line.rsplit(" ", 1)[0].strip()
            return f"{prefix} {_format_handicap_line_value(lv)}".strip()
        return f"{order_line} {_format_handicap_line_value(lv)}".strip()
    return order_line

def read_json(path: Path) -> Dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8-sig")
        try:
            obj = json.loads(raw)
            return obj if isinstance(obj, dict) else {}
        except json.JSONDecodeError:
            # 兼容：文件末尾被误追加了多余内容（常见于并发写入临时文件冲突）
            try:
                dec = json.JSONDecoder()
                obj, _ = dec.raw_decode(raw.lstrip("\ufeff").lstrip())
                return obj if isinstance(obj, dict) else {}
            except Exception:
                return {}
    except Exception:
        return {}


def atomic_write_json(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # IMPORTANT: 多进程同时写同一个 *.tmp 会导致文件内容互相覆盖/交错，最终把 JSON 写坏。
    tmp = path.with_suffix(path.suffix + f".tmp.{uuid.uuid4().hex}")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")

    last_err: Optional[Exception] = None
    for i in range(20):
        try:
            tmp.replace(path)
            return
        except PermissionError as e:
            last_err = e
            time.sleep(0.05 + i * 0.02)
        except Exception as e:
            last_err = e
            break
    try:
        tmp.unlink(missing_ok=True)
    except Exception:
        pass
    if last_err:
        raise last_err


def load_tg_config(config_path: Path) -> Tuple[str, str, str]:
    cfg = read_json(config_path)
    bot_token = str(cfg.get("telegram_bot_token") or "").strip()
    # 兼容：
    # - telegram_chat_id：用于“推送目标 chat_id”（follow_engine 会用）
    # - telegram_allow_chat：用于“允许响应指令的 chat”（本机器人用，支持多值）
    # 注意：不再回退 telegram_chat_id，避免把“推送目标”误当成“指令白名单”导致私聊无响应。
    chat_id = str(cfg.get("telegram_allow_chat") or "").strip()
    proxy = str(cfg.get("telegram_proxy") or "").strip()
    return bot_token, chat_id, proxy


def _bot_token_sha256(bot_token: str) -> str:
    t = str(bot_token or "").strip()
    if not t:
        return ""
    return hashlib.sha256(t.encode("utf-8")).hexdigest()


def _normalize_tg_username(s: str) -> str:
    s = str(s or "").strip()
    if not s:
        return ""
    if s.startswith("@"):
        s = s[1:]
    return s.lower()


def parse_allow_chat_spec(spec: str) -> Tuple[Set[str], Set[str]]:
    """
    解析“允许响应指令的 chat”配置：
    - 支持数字 chat_id（例如 -100xxxx）
    - 支持 @username（例如 @mygroup / @mychannel）
    - 支持多值：用逗号/空格/换行/| 分隔（例如 "-100..,@xxx"）

    返回：(allowed_chat_ids, allowed_usernames)
    """
    raw = str(spec or "").strip()
    if not raw:
        return set(), set()

    allowed_chat_ids: Set[str] = set()
    allowed_usernames: Set[str] = set()
    for part in re.split(r"[,\s|]+", raw):
        p = str(part or "").strip()
        if not p:
            continue
        if re.fullmatch(r"-?\d+", p):
            allowed_chat_ids.add(p)
            continue
        allowed_usernames.add(_normalize_tg_username(p))
    return allowed_chat_ids, allowed_usernames


def is_allowed_chat(*, allow_spec: str, chat: Dict[str, Any]) -> bool:
    allow_spec = str(allow_spec or "").strip()
    if not allow_spec:
        return True
    allowed_chat_ids, allowed_usernames = parse_allow_chat_spec(allow_spec)
    chat_id = str(chat.get("id") or "").strip()
    if chat_id and chat_id in allowed_chat_ids:
        return True
    username = _normalize_tg_username(chat.get("username") or "")
    if username and username in allowed_usernames:
        return True
    return False


def read_config(path: Path) -> Dict[str, Any]:
    return read_json(path)


def write_config(path: Path, obj: Dict[str, Any]) -> None:
    atomic_write_json(path, obj)


def tg_get_updates(
    *,
    bot_token: str,
    offset: int,
    timeout_s: int,
    proxy: str = "",
) -> List[Dict[str, Any]]:
    bot_token = str(bot_token or "").strip()
    if not bot_token:
        return []
    url = f"https://api.telegram.org/bot{bot_token}/getUpdates"
    params = {
        "timeout": max(1, int(timeout_s)),
        "offset": int(offset),
        # 关键：显式声明 callback_query，避免 Telegram 端残留 allowed_updates=["message"]
        # 导致按钮点击只转圈、不触发回调。
        "allowed_updates": json.dumps(
            [
                "message",
                "edited_message",
                "channel_post",
                "edited_channel_post",
                "callback_query",
            ],
            ensure_ascii=False,
        ),
    }
    proxies = {"http": proxy, "https": proxy} if proxy else None
    r = requests.get(url, params=params, timeout=max(5, int(timeout_s) + 5), proxies=proxies)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, dict) or not data.get("ok"):
        return []
    res = data.get("result")
    return res if isinstance(res, list) else []


def tg_send_message(
    *,
    bot_token: str,
    chat_id: str,
    html_text: str,
    reply_markup: Optional[Dict[str, Any]] = None,
    proxy: str = "",
    timeout_s: int = 10,
) -> None:
    bot_token = str(bot_token or "").strip()
    chat_id = str(chat_id or "").strip()
    if not bot_token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    proxies = {"http": proxy, "https": proxy} if proxy else None
    payload: Dict[str, Any] = {
        "chat_id": chat_id,
        "text": html_text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    r = requests.post(
        url,
        json=payload,
        timeout=max(5, int(timeout_s)),
        proxies=proxies,
    )
    try:
        r.raise_for_status()
    except Exception as e:
        body = ""
        try:
            body = str(r.text or "").strip()
        except Exception:
            body = ""
        if body:
            raise RuntimeError(f"sendMessage_http_error: {e}; body={body}") from e
        raise
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"telegram error: {data}")


def tg_send_rich_text(
    *,
    bot_token: str,
    chat_id: str,
    text: str,
    entities: Optional[List[Dict[str, Any]]] = None,
    reply_markup: Optional[Dict[str, Any]] = None,
    proxy: str = "",
    timeout_s: int = 10,
    message_effect_id: str = "",
) -> None:
    bot_token = str(bot_token or "").strip()
    chat_id = str(chat_id or "").strip()
    if not bot_token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    proxies = {"http": proxy, "https": proxy} if proxy else None
    payload: Dict[str, Any] = {
        "chat_id": chat_id,
        "text": str(text or ""),
        "disable_web_page_preview": True,
    }
    if entities:
        payload["entities"] = entities
    if reply_markup:
        payload["reply_markup"] = reply_markup
    if str(message_effect_id or "").strip():
        payload["message_effect_id"] = str(message_effect_id).strip()
    r = requests.post(
        url,
        json=payload,
        timeout=max(5, int(timeout_s)),
        proxies=proxies,
    )
    try:
        r.raise_for_status()
    except Exception as e:
        body = ""
        try:
            body = str(r.text or "").strip()
        except Exception:
            body = ""
        if body:
            raise RuntimeError(f"sendRichText_http_error: {e}; body={body}") from e
        raise
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"telegram rich text error: {data}")


def tg_send_photo(
    *,
    bot_token: str,
    chat_id: str,
    photo_path: Path,
    caption: str = "",
    reply_markup: Optional[Dict[str, Any]] = None,
    proxy: str = "",
    timeout_s: int = 20,
) -> None:
    bot_token = str(bot_token or "").strip()
    chat_id = str(chat_id or "").strip()
    if not bot_token or not chat_id:
        return
    p = Path(photo_path)
    if not p.exists():
        raise FileNotFoundError(str(p))
    url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
    proxies = {"http": proxy, "https": proxy} if proxy else None
    payload: Dict[str, Any] = {
        "chat_id": chat_id,
    }
    if str(caption or "").strip():
        payload["caption"] = str(caption)
        payload["parse_mode"] = "HTML"
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
    with p.open("rb") as f:
        r = requests.post(
            url,
            data=payload,
            files={"photo": (p.name, f, "image/png")},
            timeout=max(5, int(timeout_s)),
            proxies=proxies,
        )
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
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"telegram photo error: {data}")


def tg_answer_callback_query(
    *,
    bot_token: str,
    callback_query_id: str,
    text: str = "",
    show_alert: bool = False,
    proxy: str = "",
    timeout_s: int = 10,
) -> None:
    bot_token = str(bot_token or "").strip()
    callback_query_id = str(callback_query_id or "").strip()
    if not bot_token or not callback_query_id:
        return
    url = f"https://api.telegram.org/bot{bot_token}/answerCallbackQuery"
    proxies = {"http": proxy, "https": proxy} if proxy else None
    payload: Dict[str, Any] = {
        "callback_query_id": callback_query_id,
        "show_alert": bool(show_alert),
    }
    if str(text or "").strip():
        payload["text"] = str(text).strip()
    r = requests.post(url, json=payload, timeout=max(5, int(timeout_s)), proxies=proxies)
    try:
        r.raise_for_status()
    except Exception as e:
        body = ""
        try:
            body = str(r.text or "").strip()
        except Exception:
            body = ""
        if body:
            raise RuntimeError(f"answerCallbackQuery_http_error: {e}; body={body}") from e
        raise
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"answerCallbackQuery_telegram_error: {data}")


def tg_log(msg: str) -> None:
    print(f"[{now_iso()}] telegram_bot {msg}", flush=True)


def safe_tg_answer_callback_query(
    *,
    bot_token: str,
    callback_query_id: str,
    text: str = "",
    show_alert: bool = False,
    proxy: str = "",
    timeout_s: int = 10,
    context: str = "",
) -> bool:
    try:
        tg_answer_callback_query(
            bot_token=bot_token,
            callback_query_id=callback_query_id,
            text=text,
            show_alert=show_alert,
            proxy=proxy,
            timeout_s=timeout_s,
        )
        return True
    except Exception as e:
        ctx = f" context={context}" if str(context or "").strip() else ""
        try:
            tg_log(
                "callback_answer_failed"
                f"{ctx} cb_id={str(callback_query_id or '').strip()[:18]} "
                f"show_alert={bool(show_alert)} err={e}"
            )
        except Exception:
            pass
        return False


def iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    if not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8-sig") as f:
            for line in f:
                line = (line or "").strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict):
                        out.append(obj)
                except Exception:
                    continue
    except Exception:
        return []
    return out


def today_date() -> datetime.date:
    return now_bj().date()


def is_today_ts(ts: Any) -> bool:
    dt = _parse_ts_datetime(ts)
    if dt is None:
        return False
    start, end = report_day_window()
    return bool(start <= dt < end)


def _digits_only(v: Any) -> str:
    s = str(v or "")
    return "".join(ch for ch in s if ch.isdigit())


def _load_ticket_settlements(data_dir: Path) -> Dict[str, Dict[str, Any]]:
    """
    读取 data/settlements/*.jsonl（由 settlement_importer.py 自动导入，或离线导入工具生成）
    建立 ticket_id -> 最新记录 的索引。
    """
    root = data_dir / "settlements"
    if not root.exists():
        return {}
    idx: Dict[str, Dict[str, Any]] = {}
    for p in sorted(root.glob("wagers_*.jsonl")):
        try:
            with p.open("r", encoding="utf-8-sig") as f:
                for line in f:
                    line = (line or "").strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    if not isinstance(obj, dict):
                        continue
                    tid = _digits_only(obj.get("ticket_id") or obj.get("w_id") or "")
                    if not tid:
                        continue
                    ts = 0
                    try:
                        ts = int(float(obj.get("ts") or 0))
                    except Exception:
                        ts = 0
                    old = idx.get(tid)
                    if old is None:
                        idx[tid] = obj
                        continue
                    old_ts = 0
                    try:
                        old_ts = int(float(old.get("ts") or 0))
                    except Exception:
                        old_ts = 0
                    if ts >= old_ts:
                        idx[tid] = obj
        except Exception:
            continue
    return idx


def load_orders_index(orders_dir: Path, wanted_order_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    wanted = {str(x) for x in wanted_order_ids if str(x)}
    if not wanted:
        return {}
    idx: Dict[str, Dict[str, Any]] = {}
    for p in orders_dir.glob("*.jsonl"):
        try:
            with p.open("r", encoding="utf-8-sig") as f:
                for line in f:
                    line = (line or "").strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    if not isinstance(obj, dict):
                        continue
                    oid = str(obj.get("orderId") or "").strip()
                    if not oid or oid not in wanted:
                        continue
                    idx[oid] = obj
        except Exception:
            continue
    return idx


def build_today_follow_report(
    *,
    data_dir: Path,
    orders_dir: Path,
    only_ok: bool = True,
    only_live: bool = True,
    day_shift: int = 0,
) -> str:
    def fmt_amount(v: Any) -> str:
        try:
            f = float(v)
        except Exception:
            return str(v)
        if abs(f - round(f)) < 1e-9:
            return f"{f:.1f}"
        s = f"{f:.2f}"
        s = s.rstrip("0").rstrip(".")
        return s or str(v)

    results_path = data_dir / "follow_results.jsonl"
    results_all = list(iter_jsonl(results_path))
    settlements_idx = _load_ticket_settlements(data_dir)
    day_start, day_end = report_day_window_by_offset(day_shift=day_shift)
    period_label = "昨日" if int(day_shift or 0) == -1 else "今日"

    rows: List[Dict[str, Any]] = []
    for r in results_all:
        if only_live and not bool(r.get("live")):
            continue
        if only_ok and not bool(r.get("ok")):
            continue
        dt = _parse_ts_datetime(r.get("ts"))
        if dt is None or not (day_start <= dt < day_end):
            continue
        oid = str(r.get("orderId") or "").strip()
        if not oid:
            continue
        rows.append(r)

    rows.sort(key=lambda x: int(float(x.get("ts") or 0)))
    if not rows:
        return (
            f"<b>📊 {period_label}跟单报表</b>\n"
            f"⏰ 统计口径：北京时间 {day_start.strftime('%Y-%m-%d %H:%M')} ~ {day_end.strftime('%Y-%m-%d %H:%M')}\n\n"
            "暂无跟单记录。"
        )

    # 关键：分单会写入多条 ok 记录（每笔一条），但报表希望“同一后台订单只显示一条 + 跟单总额”
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        oid = str(r.get("orderId") or "").strip()
        if not oid:
            continue
        groups.setdefault(oid, []).append(r)

    group_items = list(groups.items())
    group_items.sort(key=lambda kv: int(float(min((x.get("ts") or 0) for x in kv[1]) or 0)))

    order_ids = [oid for oid, _ in group_items]
    orders_idx = load_orders_index(orders_dir, order_ids)

    total_stake = 0.0
    total_profit = 0.0
    total_settled_tickets = 0
    total_pending_tickets = 0
    total_unknown_tickets = 0
    for _, rs in group_items:
        for r in rs:
            try:
                total_stake += float(r.get("stake") or 0)
            except Exception:
                pass
            tid = str(r.get("ticket_id") or "").strip()
            if not tid or not settlements_idx:
                continue
            srec = settlements_idx.get(str(tid))
            if not srec:
                total_unknown_tickets += 1
                continue
            status = str(srec.get("status") or "").strip().lower()
            if status in {"pending", "unsettled", "running"}:
                total_pending_tickets += 1
                continue
            total_settled_tickets += 1
            if srec.get("profit") is None:
                continue
            try:
                total_profit += float(srec.get("profit"))
            except Exception:
                pass

    lines: List[str] = []
    lines.append(f"<b>📊 {period_label}跟单报表</b>")
    lines.append(f"⏰ 统计口径：北京时间 {day_start.strftime('%Y-%m-%d %H:%M')} ~ {day_end.strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"✅ 成功：{len(group_items)} 单")
    lines.append(f"💰 合计：{total_stake:g}")
    lines.append("")

    # 在不改动既有标题区文案的前提下，把输赢汇总插入到标题区（需要先从浏览器导入 settlements）。
    try:
        blank_i = lines.index("")
    except ValueError:
        blank_i = len(lines)

    if settlements_idx:
        lines.insert(blank_i, f"当日盈亏：{total_profit:+g}")
        if total_settled_tickets or total_pending_tickets or total_unknown_tickets:
            lines.insert(
                blank_i + 1,
                f"已结算：{total_settled_tickets} 单 / 未结算：{total_pending_tickets} 单 / 未匹配：{total_unknown_tickets} 单",
            )
    else:
        lines.insert(blank_i, "当日盈亏：--（未导入结算数据）")

    for i, (oid, rs) in enumerate(group_items, start=1):
        o = orders_idx.get(oid) or {}

        league = str(o.get("league") or "未知")
        sides = str(o.get("sides") or "未知")
        line_signal = str(o.get("line") or "未知")
        # 报表展示用“实际成交盘口”（避免后台信号 +0.5 但实际下注成 0/0.5 这类误导）
        line = line_signal
        for r in rs:
            params0 = r.get("params") or {}
            if isinstance(params0, dict) and params0.get("wtype") in {"R", "RE", "HRE"}:
                line = _build_follow_line_display(line_signal, params0)
                break
        odds_vals: List[float] = []
        for r in rs:
            params = r.get("params") or {}
            if not isinstance(params, dict):
                continue
            s = str(params.get("ioratio") or "").strip()
            if not s:
                continue
            try:
                odds_vals.append(float(s))
            except Exception:
                continue
        if odds_vals:
            lo = min(odds_vals)
            hi = max(odds_vals)
            follow_odds = f"{lo:g}" if abs(lo - hi) < 1e-9 else f"{lo:g}~{hi:g}"
        else:
            follow_odds = str(o.get("odds") or "未知")
        stake_sum = 0.0
        for r in rs:
            try:
                stake_sum += float(r.get("stake") or 0)
            except Exception:
                pass

        lines.append(f"🎯第{i}场")
        # 结算结果（需要先用 run_import_wagers_from_clipboard.cmd 导入“交易状况/账户历史”的 cURL）
        result_line = ""
        if settlements_idx:
            tickets = [str(x.get("ticket_id") or "").strip() for x in rs if str(x.get("ticket_id") or "").strip()]
            has_pending = False
            has_unknown = False
            settled_profit_sum = 0.0
            settled_profit_known = True
            for tid in tickets:
                srec = settlements_idx.get(tid)
                if not srec:
                    has_unknown = True
                    continue
                status = str(srec.get("status") or "").strip().lower()
                if status in {"pending", "unsettled", "running"}:
                    has_pending = True
                    continue
                if srec.get("profit") is None:
                    settled_profit_known = False
                    continue
                try:
                    settled_profit_sum += float(srec.get("profit"))
                except Exception:
                    settled_profit_known = False
            if tickets:
                if has_pending:
                    result_line = " 结果：比赛进行中（未结算）"
                elif has_unknown:
                    result_line = " 结果：未匹配（请导入账户历史）"
                else:
                    if settled_profit_known:
                        if settled_profit_sum > 1e-9:
                            result_line = f" 结果：赢（输赢 {settled_profit_sum:+g}）"
                        elif settled_profit_sum < -1e-9:
                            result_line = f" 结果：输（输赢 {settled_profit_sum:+g}）"
                        else:
                            result_line = " 结果：走水"
                    else:
                        result_line = " 结果：已结算（输赢未知）"

        lines.append(f" 后台单号：{oid}")
        lines.append(f" 联赛：{league}")
        lines.append(f" 比赛：{sides}")
        lines.append(f" 玩法：{line} @{follow_odds}")
        if result_line:
            lines.append(result_line)
        lines.append(f"💰 金额：{fmt_amount(stake_sum)}")
        lines.append("")

    return "\n".join(lines).strip()


def _fmt_report_number(v: Any, *, signed: bool = False) -> str:
    try:
        f = float(v)
    except Exception:
        s = str(v or "").strip()
        return s or "0"
    sign = ""
    if signed:
        if f > 1e-9:
            sign = "+"
        elif f < -1e-9:
            sign = "-"
    elif f < -1e-9:
        sign = "-"
    af = abs(f)
    if abs(af - round(af)) < 1e-9:
        body = str(int(round(af)))
    else:
        body = f"{af:.2f}".rstrip("0").rstrip(".")
    return f"{sign}{body}"


def _today_follow_report_row_result(
    rs: List[Dict[str, Any]],
    settlements_idx: Dict[str, Dict[str, Any]],
) -> Tuple[str, str, float]:
    if not settlements_idx:
        return "--", "unknown", 0.0
    tickets = [str(x.get("ticket_id") or "").strip() for x in rs if str(x.get("ticket_id") or "").strip()]
    if not tickets:
        return "未匹配", "unmatched", 0.0

    has_pending = False
    has_unknown = False
    settled_seen = False
    profit_sum = 0.0
    profit_known = True
    for tid in tickets:
        srec = settlements_idx.get(tid)
        if not srec:
            has_unknown = True
            continue
        status = str(srec.get("status") or "").strip().lower()
        if status in {"pending", "unsettled", "running"}:
            has_pending = True
            continue
        settled_seen = True
        if srec.get("profit") is None:
            profit_known = False
            continue
        try:
            profit_sum += float(srec.get("profit"))
        except Exception:
            profit_known = False

    if has_pending:
        return "未结算", "pending", profit_sum
    if has_unknown or not settled_seen:
        return "未匹配", "unmatched", profit_sum
    if not profit_known:
        return "已结算", "settled", profit_sum
    if profit_sum > 1e-9:
        return f"赢（输赢 {_fmt_report_number(profit_sum, signed=True)}）", "win", profit_sum
    if profit_sum < -1e-9:
        return f"输（输赢 {_fmt_report_number(profit_sum, signed=True)}）", "loss", profit_sum
    return "走水", "push", profit_sum


def build_today_follow_report_image_model(
    *,
    data_dir: Path,
    orders_dir: Path,
    only_ok: bool = True,
    only_live: bool = True,
    day_shift: int = 0,
    max_rows: int = 30,
) -> Dict[str, Any]:
    results_path = data_dir / "follow_results.jsonl"
    results_all = list(iter_jsonl(results_path))
    settlements_idx = _load_ticket_settlements(data_dir)
    day_start, day_end = report_day_window_by_offset(day_shift=day_shift)
    period_label = "昨日" if int(day_shift or 0) == -1 else "今日"

    rows_in_day: List[Dict[str, Any]] = []
    for r in results_all:
        if only_live and not bool(r.get("live")):
            continue
        if only_ok and not bool(r.get("ok")):
            continue
        dt = _parse_ts_datetime(r.get("ts"))
        if dt is None or not (day_start <= dt < day_end):
            continue
        oid = str(r.get("orderId") or "").strip()
        if oid:
            rows_in_day.append(r)

    rows_in_day.sort(key=lambda x: int(float(x.get("ts") or 0)))
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows_in_day:
        oid = str(r.get("orderId") or "").strip()
        if oid:
            groups.setdefault(oid, []).append(r)

    group_items = list(groups.items())
    group_items.sort(key=lambda kv: int(float(min((x.get("ts") or 0) for x in kv[1]) or 0)))
    orders_idx = load_orders_index(orders_dir, [oid for oid, _ in group_items])

    total_stake = 0.0
    total_profit = 0.0
    settled_count = 0
    pending_count = 0
    unmatched_count = 0
    if group_items:
        for _, rs in group_items:
            for r in rs:
                try:
                    total_stake += float(r.get("stake") or 0)
                except Exception:
                    pass
                if not settlements_idx:
                    continue
                tid = str(r.get("ticket_id") or "").strip()
                if not tid:
                    unmatched_count += 1
                    continue
                srec = settlements_idx.get(tid)
                if not srec:
                    unmatched_count += 1
                    continue
                status = str(srec.get("status") or "").strip().lower()
                if status in {"pending", "unsettled", "running"}:
                    pending_count += 1
                    continue
                settled_count += 1
                if srec.get("profit") is None:
                    continue
                try:
                    total_profit += float(srec.get("profit"))
                except Exception:
                    pass

    display_rows: List[Dict[str, Any]] = []
    limit = max(1, int(max_rows or 30))
    for i, (oid, rs) in enumerate(group_items[:limit], start=1):
        o = orders_idx.get(oid) or {}
        league = str(o.get("league") or "未知").strip() or "未知"
        sides = str(o.get("sides") or "未知").strip() or "未知"
        line_signal = str(o.get("line") or "未知").strip() or "未知"
        line = line_signal
        for r in rs:
            params0 = r.get("params") or {}
            if isinstance(params0, dict) and params0.get("wtype") in {"R", "RE", "HRE"}:
                line = _build_follow_line_display(line_signal, params0)
                break

        odds_vals: List[float] = []
        for r in rs:
            params = r.get("params") or {}
            if not isinstance(params, dict):
                continue
            s = str(params.get("ioratio") or "").strip()
            if not s:
                continue
            try:
                odds_vals.append(float(s))
            except Exception:
                continue
        if odds_vals:
            lo = min(odds_vals)
            hi = max(odds_vals)
            follow_odds = f"{lo:g}" if abs(lo - hi) < 1e-9 else f"{lo:g}~{hi:g}"
        else:
            follow_odds = str(o.get("odds") or "未知").strip() or "未知"

        stake_sum = 0.0
        for r in rs:
            try:
                stake_sum += float(r.get("stake") or 0)
            except Exception:
                pass
        result_text, result_type, profit_sum = _today_follow_report_row_result(rs, settlements_idx)
        display_rows.append(
            {
                "index": i,
                "order_id": oid,
                "league": league,
                "match": sides,
                "play": f"{line} @{follow_odds}",
                "result": result_text,
                "result_type": result_type,
                "profit": profit_sum,
                "amount": _fmt_report_number(stake_sum),
            }
        )

    truncated = len(group_items) > limit
    if truncated:
        footer_note = f"注：今日共 {len(group_items)} 场，本图仅展示前 {limit} 场。"
        if int(day_shift or 0) == -1:
            footer_note = f"注：昨日共 {len(group_items)} 场，本图仅展示前 {limit} 场。"
    elif group_items:
        footer_note = "注：表格按后台订单合并展示，同一后台订单多笔跟单会汇总金额。"
    else:
        footer_note = "暂无跟单记录。"

    return {
        "title": "HD Sport报表",
        "period_label": period_label,
        "day_shift": int(day_shift or 0),
        "period_text": f"北京时间 {day_start.strftime('%Y-%m-%d %H:%M')} ~ {day_end.strftime('%Y-%m-%d %H:%M')}",
        "stats": {
            "success_count": len(group_items),
            "total_stake": total_stake,
            "total_stake_text": _fmt_report_number(total_stake),
            "profit": total_profit,
            "profit_text": _fmt_report_number(total_profit, signed=True) if settlements_idx else "--",
            "settled_count": settled_count,
            "pending_count": pending_count,
            "unmatched_count": unmatched_count,
        },
        "rows": display_rows,
        "total_rows": len(group_items),
        "display_rows": len(display_rows),
        "truncated": truncated,
        "footer_note": footer_note,
    }


def render_today_follow_report_html(model: Dict[str, Any]) -> str:
    def esc(v: Any) -> str:
        return html.escape(str(v if v is not None else ""))

    stats = model.get("stats") if isinstance(model.get("stats"), dict) else {}
    stat_defs = [
        ("🏆", "成功", f"{int(stats.get('success_count') or 0)}<small>单</small>", "gold"),
        ("🪙", "合计", esc(stats.get("total_stake_text") or "0"), "blue"),
        ("📈", "当日盈亏", esc(stats.get("profit_text") or "--"), "profit"),
        ("✓", "已结算", f"{int(stats.get('settled_count') or 0)}<small>单</small>", "blue"),
        ("◷", "未结算", f"{int(stats.get('pending_count') or 0)}<small>单</small>", "gold"),
        ("∞", "未匹配", f"{int(stats.get('unmatched_count') or 0)}<small>单</small>", "plain"),
    ]
    stat_html = "\n".join(
        (
            f'<div class="stat {kind}">'
            f'<div class="stat-label"><span>{icon}</span>{esc(label)}</div>'
            f'<div class="stat-value">{value}</div>'
            "</div>"
        )
        for icon, label, value, kind in stat_defs
    )

    rows = model.get("rows") if isinstance(model.get("rows"), list) else []
    if rows:
        row_html = "\n".join(
            (
                f"<tr>"
                f"<td>{int(row.get('index') or 0)}</td>"
                f"<td>{esc(row.get('league') or '未知')}</td>"
                f"<td>{esc(row.get('match') or '未知')}</td>"
                f"<td>{esc(row.get('play') or '未知')}</td>"
                f"<td class=\"result {esc(row.get('result_type') or 'unknown')}\">{esc(row.get('result') or '--')}</td>"
                f"<td>{esc(row.get('amount') or '0')}</td>"
                f"</tr>"
            )
            for row in rows
        )
    else:
        row_html = '<tr class="empty"><td colspan="6">暂无跟单记录</td></tr>'

    css = """
* { box-sizing: border-box; }
html, body { margin: 0; width: 720px; height: 1280px; overflow: hidden; }
body {
  font-family: "PingFang SC", "Microsoft YaHei", Arial, sans-serif;
  color: #f8e7ba;
  background: #240202;
}
.report {
  position: relative;
  width: 720px;
  height: 1280px;
  padding: 24px 16px 18px;
  overflow: hidden;
  background:
    radial-gradient(circle at 24% 0%, rgba(255, 193, 91, 0.16), transparent 24%),
    radial-gradient(circle at 74% 18%, rgba(255, 91, 58, 0.14), transparent 28%),
    linear-gradient(135deg, rgba(255, 214, 122, 0.10) 0 1px, transparent 1px 28px),
    linear-gradient(180deg, #3b0505 0%, #6d0c08 47%, #330404 100%);
}
.report::before {
  content: "";
  position: absolute;
  inset: 0;
  background:
    linear-gradient(90deg, rgba(255, 221, 137, 0.08), transparent 18%, transparent 82%, rgba(255, 221, 137, 0.08)),
    repeating-linear-gradient(100deg, rgba(255,255,255,.04) 0 1px, transparent 1px 9px);
  opacity: .65;
}
.report::after {
  content: "H.D";
  position: absolute;
  right: 34px;
  top: 142px;
  color: rgba(255, 215, 146, 0.07);
  font-size: 106px;
  font-weight: 800;
  letter-spacing: 0;
}
.content { position: relative; z-index: 1; }
.top { display: grid; grid-template-columns: 116px 1fr 166px; align-items: center; min-height: 88px; }
.crest {
  width: 86px;
  height: 86px;
  display: grid;
  place-items: center;
  border: 2px solid rgba(246, 203, 123, .72);
  border-radius: 18px 18px 30px 30px;
  background: linear-gradient(180deg, rgba(255,255,255,.10), rgba(255,196,88,.08));
  box-shadow: inset 0 0 20px rgba(255, 214, 145, .12), 0 0 14px rgba(0,0,0,.35);
}
.crest strong { display: block; color: #f8d37d; font-size: 30px; line-height: 1; }
.crest span { display: block; margin-top: 5px; color: #f4d6a3; font-size: 12px; }
.title { text-align: center; transform: translateX(-10px); }
.title h1 {
  margin: 0;
  color: #f6d78e;
  font-family: Georgia, "Times New Roman", serif;
  font-size: 54px;
  font-weight: 700;
  line-height: .98;
  letter-spacing: 0;
  text-shadow: 0 2px 0 rgba(30,0,0,.8), 0 0 18px rgba(255, 204, 102, .18);
}
.subtitle { margin-top: 12px; color: #efd2a0; font-size: 15px; white-space: nowrap; }
.badge {
  justify-self: end;
  width: 154px;
  padding: 10px 14px;
  border: 1px solid rgba(246, 203, 123, .75);
  color: #f3d89d;
  font-size: 13px;
  line-height: 1;
  white-space: nowrap;
  background: rgba(78, 8, 6, .62);
  clip-path: polygon(10px 0, calc(100% - 10px) 0, 100% 50%, calc(100% - 10px) 100%, 10px 100%, 0 50%);
}
.stats {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: 10px;
  margin-top: 10px;
}
.stat {
  height: 92px;
  border: 1px solid rgba(246, 203, 123, .46);
  background: linear-gradient(180deg, rgba(117, 14, 10, .92), rgba(74, 7, 6, .72));
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  box-shadow: inset 0 0 18px rgba(255, 210, 124, .06);
}
.stat-label { display: flex; align-items: center; gap: 5px; color: #f3d9ad; font-size: 15px; white-space: nowrap; }
.stat-value { margin-top: 10px; font-size: 28px; font-weight: 800; line-height: 1; color: #f7d88f; }
.stat-value small { margin-left: 4px; font-size: 15px; font-weight: 600; }
.stat.blue .stat-value { color: #69a8ff; }
.stat.profit .stat-value { color: #61dd5a; }
.stat.plain .stat-value { color: #f0e8d8; }
.section-title {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 14px;
  margin: 20px 0 12px;
  color: #f1d18e;
  font-size: 25px;
  font-weight: 800;
  letter-spacing: 0;
}
.section-title::before, .section-title::after {
  content: "";
  width: 130px;
  height: 1px;
  background: linear-gradient(90deg, transparent, rgba(247, 205, 125, .85), transparent);
}
.table-wrap {
  height: 850px;
  border: 1px solid rgba(247, 205, 125, .50);
  background: rgba(47, 3, 3, .48);
  overflow: hidden;
}
table { width: 100%; border-collapse: collapse; table-layout: fixed; }
col.idx { width: 44px; }
col.league { width: 122px; }
col.match { width: 184px; }
col.play { width: 158px; }
col.result { width: 118px; }
col.amount { width: 62px; }
th, td {
  border-right: 1px solid rgba(247, 205, 125, .28);
  border-bottom: 1px solid rgba(247, 205, 125, .24);
  height: 27px;
  padding: 0 7px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  vertical-align: middle;
}
th {
  height: 34px;
  color: #ffe1a8;
  font-size: 13px;
  font-weight: 700;
  background: rgba(122, 17, 11, .82);
}
td { color: #ead6bd; font-size: 12px; }
td:first-child, th:first-child, td:last-child, th:last-child { text-align: center; }
.result { font-weight: 800; }
.result.win { color: #61d84f; }
.result.loss { color: #ff544b; }
.result.push, .result.pending { color: #59a7ff; }
.result.unmatched, .result.unknown { color: #f2d18d; }
.empty td { height: 810px; color: #f2d18d; font-size: 22px; text-align: center; }
.footer {
  margin-top: 18px;
  min-height: 58px;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0 24px;
  border-top: 1px solid rgba(247, 205, 125, .42);
  border-bottom: 1px solid rgba(247, 205, 125, .42);
  color: #f1d79e;
  font-size: 15px;
  text-align: center;
}
"""

    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<style>{css}</style>
</head>
<body>
  <main class="report">
    <section class="content">
      <header class="top">
        <div class="crest"><div><strong>HD</strong><span>SPORT</span></div></div>
        <div class="title">
          <h1>{esc(model.get("title") or "HD Sport报表")}</h1>
          <div class="subtitle">统计口径：{esc(model.get("period_text") or "")}</div>
        </div>
        <div class="badge">{esc(model.get("period_label") or "日报")} / Follow Report</div>
      </header>
      <section class="stats">{stat_html}</section>
      <div class="section-title">比赛明细（表格模式）</div>
      <section class="table-wrap">
        <table>
          <colgroup>
            <col class="idx"><col class="league"><col class="match"><col class="play"><col class="result"><col class="amount">
          </colgroup>
          <thead><tr><th>序号</th><th>联赛</th><th>比赛</th><th>玩法</th><th>结果</th><th>金额</th></tr></thead>
          <tbody>{row_html}</tbody>
        </table>
      </section>
      <footer class="footer">{esc(model.get("footer_note") or "")}</footer>
    </section>
  </main>
</body>
</html>"""


def build_today_follow_report_image(
    *,
    data_dir: Path,
    orders_dir: Path,
    only_ok: bool = True,
    only_live: bool = True,
    day_shift: int = 0,
    image_dir: Optional[Path] = None,
) -> Path:
    model = build_today_follow_report_image_model(
        data_dir=data_dir,
        orders_dir=orders_dir,
        only_ok=only_ok,
        only_live=only_live,
        day_shift=day_shift,
    )
    html_doc = render_today_follow_report_html(model)
    out_dir = Path(image_dir) if image_dir is not None else (Path(data_dir).parent / ".run" / "telegram_report_images")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"today_follow_{int(day_shift or 0)}_{int(time.time() * 1000)}.png"

    from playwright.sync_api import sync_playwright  # type: ignore

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 720, "height": 1280}, device_scale_factor=1)
            page.set_content(html_doc, wait_until="load")
            page.screenshot(path=str(out_path), full_page=False)
        finally:
            browser.close()
    return out_path


def send_today_follow_report(
    *,
    bot_token: str,
    chat_id: str,
    data_dir: Path,
    orders_dir: Path,
    only_ok: bool = True,
    only_live: bool = True,
    day_shift: int = 0,
    proxy: str = "",
    timeout_s: int = 15,
    image_dir: Optional[Path] = None,
    text_reply_markup: Optional[Dict[str, Any]] = None,
) -> str:
    inline_markup = follow_report_inline_keyboard(day_shift=day_shift)
    period_label = "昨日" if int(day_shift or 0) == -1 else "今日"
    try:
        image_path = build_today_follow_report_image(
            data_dir=data_dir,
            orders_dir=orders_dir,
            only_ok=only_ok,
            only_live=only_live,
            day_shift=day_shift,
            image_dir=image_dir,
        )
        tg_send_photo(
            bot_token=bot_token,
            chat_id=chat_id,
            photo_path=image_path,
            caption=f"<b>📊 {period_label}跟单报表</b>",
            reply_markup=inline_markup,
            proxy=proxy,
            timeout_s=max(20, int(timeout_s)),
        )
        return "photo"
    except Exception as e:
        try:
            tg_log(f"today_follow_report_image_failed day_shift={int(day_shift or 0)} err={e}")
        except Exception:
            pass

    report = build_today_follow_report(
        data_dir=data_dir,
        orders_dir=orders_dir,
        only_ok=only_ok,
        only_live=only_live,
        day_shift=day_shift,
    )
    parts = split_message(report)
    total = max(1, len(parts))
    for idx_part, part in enumerate(parts):
        rm = inline_markup if idx_part == (total - 1) else text_reply_markup
        tg_send_message(
            bot_token=bot_token,
            chat_id=chat_id,
            html_text=part,
            reply_markup=rm,
            proxy=proxy,
            timeout_s=timeout_s,
        )
    return "text"


def build_order_follow_report(*, data_dir: Path, orders_dir: Path, order_id: str) -> str:
    def fmt_amount(v: Any) -> str:
        try:
            f = float(v)
        except Exception:
            return str(v)
        if abs(f - round(f)) < 1e-9:
            return f"{f:.1f}"
        s = f"{f:.2f}"
        s = s.rstrip("0").rstrip(".")
        return s or str(v)

    oid_raw = str(order_id or "").strip()
    oid = _digits_only(oid_raw) or oid_raw

    rows = _order_rows_from_follow_results(data_dir, oid)
    if not rows:
        return (
            "<b>🎯 新注单提醒</b>\n\n"
            f"🆔 <b>单号：</b>{html.escape(oid)}\n"
            "未找到该后台单的跟单记录。"
        )

    ok_rows = [r for r in rows if bool(r.get("ok"))]
    orders_idx = load_orders_index(orders_dir, [oid])
    o = orders_idx.get(oid) or {}

    backend = str(o.get("target") or "未知")
    account = str(o.get("account") or "未知")
    league = str(o.get("league") or "未知")
    sides = str(o.get("sides") or "未知")
    line = str(o.get("line") or "未知")
    odds = str(o.get("odds") or "未知")
    sport = str(o.get("sport") or "未知")
    bet_type = str(o.get("betType") or "未知")
    backend_stake = str(o.get("stake") or "未知")
    dt = f"{(o.get('date') or '')} {(o.get('time') or '')}".strip()
    type_text = " ".join([x for x in [sport, bet_type] if x and x != "未知"]).strip() or bet_type or sport or "未知"

    ok_stake = 0.0
    for r in ok_rows:
        try:
            ok_stake += float(r.get("stake") or 0.0)
        except Exception:
            continue

    def _part_no(r: Dict[str, Any], idx: int) -> int:
        try:
            n = int(r.get("split_index") or 0)
            if n > 0:
                return n
        except Exception:
            pass
        return idx + 1

    def _ok_sort_key(r: Dict[str, Any], idx: int) -> Tuple[int, int]:
        part = _part_no(r, idx)
        try:
            ts = int(float(r.get("ts") or 0))
        except Exception:
            ts = 0
        return part, ts

    ok_with_idx = list(enumerate(ok_rows))
    ok_with_idx.sort(key=lambda t: _ok_sort_key(t[1], t[0]))

    lines: List[str] = []
    lines.append("<b>🎯 新注单提醒</b>")
    lines.append("")
    lines.append(f"🧩 <b>后台：</b>{html.escape(backend)}")
    lines.append(f"🆔 <b>单号：</b>{html.escape(oid)}")
    if dt:
        lines.append(f"⏰ <b>时间：</b>{html.escape(dt)}")
    lines.append(f"👤 <b>账号：</b>{html.escape(account)}")
    lines.append(f"🎯 <b>比赛：</b>{html.escape(sides)}")
    lines.append(f"🏆 <b>联赛：</b>{html.escape(league)}")
    lines.append(f"🎲 <b>类型：</b>{html.escape(type_text)}")
    lines.append(f"💰 <b>金额：</b>{html.escape(backend_stake)}")
    lines.append(f"🎯 <b>玩法：</b>{html.escape(line)} @{html.escape(odds)}")
    lines.append(f"📌 <b>跟单成功金额：</b>{html.escape(fmt_amount(ok_stake))}")

    if not ok_with_idx:
        lines.append("")
        lines.append("⏳ 暂无跟单成功记录。")
        return "\n".join(lines).strip()

    for i, r in ok_with_idx:
        part_no = _part_no(r, i)
        member = str(r.get("member") or "").strip() or str(r.get("member_id") or "").strip() or "-"
        ticket_id = str(r.get("ticket_id") or "").strip()
        msg = str(r.get("message") or "").strip()
        params = r.get("params") if isinstance(r.get("params"), dict) else {}
        follow_odds = str(params.get("ioratio") or odds or "未知")
        follow_line = _build_follow_line_display(line, params)
        stake_txt = fmt_amount(r.get("stake") or 0.0)
        dt2 = _parse_ts_datetime(r.get("ts"))
        ts_txt = dt2.strftime("%Y-%m-%d %H:%M:%S") if dt2 else "未知"

        lines.append("")
        lines.append(f"✅ <b>跟单成功</b>（{part_no}）")
        lines.append(f"会员：{html.escape(member)}")
        lines.append(f"玩法：{html.escape(follow_line)} @{html.escape(follow_odds)}")
        lines.append(f"金额：{html.escape(stake_txt)}")
        if ticket_id:
            lines.append(f"单号：{html.escape(ticket_id)}")
        if msg:
            lines.append(f"提示：{html.escape(msg)}")
        lines.append(f"⏰ 时间：{html.escape(ts_txt)}")

    return "\n".join(lines).strip()


def build_project_winrate_report(
    *,
    data_dir: Path,
    orders_dir: Path,
    amount_threshold: float = 2000.0,
) -> str:
    """
    投资回报率总览（按后台订单口径）：
    - 统计单位：后台订单 orderId（有跟单成功票才纳入）
    - 总投入：仅统计“已结算且可计算盈亏”的后台单对应跟单金额
    - 结构：当前结算数据无法稳定识别赢半/输半，先按全赢/走水/全输输出
    """
    _ = amount_threshold

    def safe_float(v: Any) -> Optional[float]:
        try:
            s = str(v).strip()
            if not s:
                return None
            return float(s)
        except Exception:
            return None

    def fmt_amount(v: Any) -> str:
        try:
            f = float(v)
        except Exception:
            return str(v)
        af = abs(f)
        if abs(af - round(af)) < 1e-9:
            body = f"{af:,.0f}"
        else:
            body = f"{af:,.2f}".rstrip("0").rstrip(".")
        return f"-{body}" if f < 0 else body

    def pct(v: Optional[float]) -> str:
        if v is None:
            return "--"
        return f"{v * 100.0:.2f}"

    def fmt_signed(v: float) -> str:
        sign = "+" if float(v) >= 0 else "-"
        return f"{sign}{fmt_amount(abs(float(v)))}"

    results_path = data_dir / "follow_results.jsonl"
    results_all = list(iter_jsonl(results_path))

    ok_rows: List[Dict[str, Any]] = []
    for r in results_all:
        if not bool(r.get("ok")):
            continue
        oid = str(r.get("orderId") or "").strip()
        if not oid:
            continue
        ok_rows.append(r)

    if not ok_rows:
        return "\n".join(
            [
                "👑 HD 跟单数据总揽 👑",
                "──────────────",
                "",
                "📍 长期累计",
                "",
                "🏆 总单量        0 单",
                "有效胜率        --%",
                "",
                "💰 总投入        0",
                "净收益          0",
                "投资回报率      --%",
                "单均收益        0",
                "",
                "📌 结构",
                "全赢 0 ｜ 赢半 0 ｜ 走水 0",
                "输半 0 ｜ 全输 0",
            ]
        )

    groups: Dict[str, List[Dict[str, Any]]] = {}
    for r in ok_rows:
        oid = str(r.get("orderId") or "").strip()
        groups.setdefault(oid, []).append(r)

    order_ids = list(groups.keys())
    load_orders_index(orders_dir, order_ids)
    settlements_idx = _load_ticket_settlements(data_dir)
    total_orders = len(groups)
    settled_orders = 0
    total_stake = 0.0
    total_profit = 0.0
    pending_orders = 0
    unmatched_orders = 0
    unknown_profit_orders = 0
    win = 0
    half = 0
    push = 0
    half_loss = 0
    loss = 0

    for oid, rs in groups.items():
        follow_stake_sum = 0.0
        tickets: List[str] = []
        for r in rs:
            try:
                follow_stake_sum += float(r.get("stake") or 0)
            except Exception:
                pass
            tid = _digits_only(r.get("ticket_id") or "")
            if tid:
                tickets.append(tid)

        if not settlements_idx:
            continue

        if not tickets:
            unmatched_orders += 1
            continue

        has_pending = False
        has_unmatched = False
        has_profit_unknown = False
        profit_sum = 0.0
        settled_ticket_count = 0
        for tid in tickets:
            srec = settlements_idx.get(tid)
            if not srec:
                has_unmatched = True
                continue
            status = str(srec.get("status") or "").strip().lower()
            if status in {"pending", "unsettled", "running"}:
                has_pending = True
                continue
            settled_ticket_count += 1
            if srec.get("profit") is None:
                has_profit_unknown = True
                continue
            try:
                profit_sum += float(srec.get("profit"))
            except Exception:
                has_profit_unknown = True

        if has_unmatched:
            unmatched_orders += 1
            continue
        if has_pending:
            pending_orders += 1
            continue
        if settled_ticket_count == 0:
            unmatched_orders += 1
            continue
        if has_profit_unknown:
            unknown_profit_orders += 1
            continue

        settled_orders += 1
        total_stake += float(follow_stake_sum)
        total_profit += float(profit_sum)

        if profit_sum > 1e-9:
            win += 1
        elif profit_sum < -1e-9:
            loss += 1
        else:
            push += 1

    if not settlements_idx:
        effective_win_rate: Optional[float] = None
        roi: Optional[float] = None
        avg_profit: Optional[float] = None
    else:
        effective_win_rate = (win + (half * 0.5)) / settled_orders if settled_orders else None
        roi = (total_profit / total_stake) if total_stake > 1e-9 else None
        avg_profit = (total_profit / settled_orders) if settled_orders else None

    lines: List[str] = []
    lines.append("👑 HD 跟单数据总揽 👑")
    lines.append("──────────────")
    lines.append("")
    lines.append("📍 长期累计")
    lines.append("")
    lines.append(f"🏆 总单量        {total_orders} 单")
    lines.append(f"有效胜率        {pct(effective_win_rate)}%")
    lines.append("")
    lines.append(f"💰 总投入        {fmt_amount(total_stake)}")
    lines.append(f"净收益          {fmt_signed(total_profit)}")
    lines.append(f"投资回报率      {pct(roi)}%")
    lines.append(f"单均收益        {fmt_signed(avg_profit or 0.0) if avg_profit is not None else '--'}")
    lines.append("")
    lines.append("📌 结构")
    lines.append(f"全赢 {win} ｜ 赢半 {half} ｜ 走水 {push}")
    lines.append(f"输半 {half_loss} ｜ 全输 {loss}")
    if settlements_idx:
        if pending_orders or unmatched_orders or unknown_profit_orders:
            lines.append("")
            lines.append(
                f"说明：未结算 {pending_orders} ｜ 未匹配 {unmatched_orders} ｜ 盈亏未知 {unknown_profit_orders}"
            )
    else:
        lines.append("")
        lines.append("说明：未检测到结算数据，盈亏与ROI暂不可用。")

    return "\n".join(lines).strip()


def split_message(text: str, max_chars: int = 3500) -> List[str]:
    text = str(text or "")
    if len(text) <= max_chars:
        return [text]
    parts: List[str] = []
    buf: List[str] = []
    cur = 0
    for line in text.splitlines():
        add = (line + "\n")
        if cur + len(add) > max_chars and buf:
            parts.append("".join(buf).rstrip())
            buf = []
            cur = 0
        buf.append(add)
        cur += len(add)
    if buf:
        parts.append("".join(buf).rstrip())
    return parts


def parse_order_query(text: str) -> Optional[str]:
    """
    解析按后台单号查询命令：
    - /oid <orderId>
    - /order <orderId>
    兼容群聊场景：/oid@YourBot <orderId>
    返回：
    - None：不是该命令
    - ""：是命令但缺少参数
    - "2315..."：提取到的 orderId（仅数字）
    """
    t = str(text or "").strip()
    if not t:
        return None
    parts = t.split()
    if not parts:
        return None
    cmd = str(parts[0] or "").strip()
    cmd = cmd.split("@", 1)[0].lower()
    if cmd not in {"/oid", "/order"}:
        return None
    if len(parts) < 2:
        return ""
    oid = _digits_only(parts[1])
    return oid or ""


def parse_order_callback_data(data: Any) -> Optional[Tuple[str, str]]:
    s = str(data or "").strip()
    if not s:
        return None
    parts = s.split("|")
    if len(parts) != 3:
        return None
    if parts[0] != ORDER_CB_PREFIX:
        return None
    action = str(parts[1] or "").strip().lower()
    if action not in {"res", "log", "pnl", "amt"}:
        return None
    oid = _digits_only(parts[2])
    if not oid:
        return None
    return action, oid


def parse_report_callback_data(data: Any) -> Optional[str]:
    s = str(data or "").strip()
    if not s:
        return None
    parts = s.split("|")
    if len(parts) != 2:
        return None
    if parts[0] != REPORT_CB_PREFIX:
        return None
    action = str(parts[1] or "").strip().lower()
    if action not in {"yday", "today"}:
        return None
    return action


def follow_report_inline_keyboard(day_shift: int = 0) -> Dict[str, Any]:
    if int(day_shift or 0) == -1:
        text = "📊 返回今日"
        action = "today"
    else:
        text = "📅 昨日跟单"
        action = "yday"
    return {
        "inline_keyboard": [
            [
                {"text": text, "callback_data": f"{REPORT_CB_PREFIX}|{action}"},
            ]
        ]
    }


def _alert_text_from_report(report_html: str, max_chars: int = 180) -> str:
    txt = str(report_html or "")
    txt = re.sub(r"<[^>]+>", "", txt)
    txt = html.unescape(txt)
    lines = [ln.strip() for ln in txt.splitlines() if str(ln).strip()]
    plain = "\n".join(lines).strip() or "暂无结果"
    n = max(20, int(max_chars))
    if len(plain) > n:
        plain = plain[: n - 1] + "…"
    return plain


def build_order_follow_alert(
    *,
    data_dir: Path,
    order_id: str,
    orders_dir: Optional[Path] = None,
    max_rows: int = 3,
    max_chars: int = 180,
) -> str:
    def fmt_amount(v: Any) -> str:
        try:
            f = float(v)
        except Exception:
            return str(v)
        if abs(f - round(f)) < 1e-9:
            return f"{f:.1f}"
        s = f"{f:.2f}".rstrip("0").rstrip(".")
        return s or str(v)

    def fmt_odds(v: Any) -> str:
        s = str(v or "").strip()
        if not s:
            return "-"
        try:
            f = float(s)
        except Exception:
            return s
        return f"{f:.2f}".rstrip("0").rstrip(".")

    oid = _digits_only(order_id) or str(order_id or "").strip()
    rows = _order_rows_from_follow_results(data_dir, oid)
    ok_rows = [r for r in rows if bool(r.get("ok"))]
    ok_rows.sort(key=lambda r: (int(r.get("split_index") or 0), int(float(r.get("ts") or 0))))

    backend_stake: Any = ""
    if orders_dir is not None:
        try:
            backend_stake = (load_orders_index(orders_dir, [oid]).get(oid) or {}).get("stake")
        except Exception:
            backend_stake = ""
    if backend_stake in (None, ""):
        try:
            backend_stake = sum(float(r.get("stake") or 0.0) for r in rows)
        except Exception:
            backend_stake = 0.0

    odds_vals: List[float] = []
    row_lines: List[str] = []
    limit = max(1, int(max_rows))
    for idx, r in enumerate(ok_rows):
        member = str(r.get("member") or "").strip() or str(r.get("member_id") or "").strip() or "-"
        stake = fmt_amount(r.get("stake") or 0.0)
        params = r.get("params") if isinstance(r.get("params"), dict) else {}
        odds_raw = str(params.get("ioratio") or "").strip()
        if not odds_raw:
            odds_raw = str(params.get("odds") or "").strip()
        odds = fmt_odds(odds_raw)
        try:
            odds_vals.append(float(odds_raw))
        except Exception:
            pass
        if idx < limit:
            row_lines.append(f"✔ {member}   {stake}   @{odds}")

    avg_odds = "--"
    if odds_vals:
        avg_odds = f"{(sum(odds_vals) / max(1, len(odds_vals))):.2f}".rstrip("0").rstrip(".")

    lines: List[str] = []
    lines.append("────────────")
    lines.append("📋 跟单详细")
    lines.append("────────────")
    lines.append(f"🆔 单号：{oid or '-'}")
    lines.append(f"💰 原单金额：{fmt_amount(backend_stake)}")
    lines.append(f"📊 平均水位：{avg_odds}")
    lines.append("👥 跟单账号")
    if not row_lines:
        lines.append("暂无成功记录")
    else:
        lines.extend(row_lines)
        if len(ok_rows) > limit:
            lines.append(f"... 还有{len(ok_rows) - limit}笔")

    text = "\n".join(lines).strip()
    n = max(20, int(max_chars))
    if len(text) > n:
        text = text[: n - 1] + "…"
    return text


def _order_rows_from_follow_results(data_dir: Path, order_id: str) -> List[Dict[str, Any]]:
    oid_norm = _digits_only(order_id)
    out: List[Dict[str, Any]] = []
    for r in iter_jsonl(data_dir / "follow_results.jsonl"):
        oid = _digits_only(r.get("orderId") or "")
        if oid and oid == oid_norm:
            out.append(r)
    out.sort(key=lambda x: int(float(x.get("ts") or 0)))
    return out


def build_order_amount_report(*, data_dir: Path, orders_dir: Path, order_id: str) -> str:
    oid = _digits_only(order_id) or str(order_id or "").strip()
    rows = _order_rows_from_follow_results(data_dir, oid)
    ok_rows = [r for r in rows if bool(r.get("ok"))]
    o = load_orders_index(orders_dir, [oid]).get(oid) or {}

    backend_stake = 0.0
    try:
        backend_stake = float(o.get("stake") or 0.0)
    except Exception:
        backend_stake = 0.0

    ok_stake = 0.0
    fail_stake = 0.0
    for r in rows:
        try:
            s = float(r.get("stake") or 0.0)
        except Exception:
            s = 0.0
        if bool(r.get("ok")):
            ok_stake += s
        else:
            fail_stake += s

    lines: List[str] = []
    lines.append("<b>💰 跟单金额</b>")
    lines.append(f"后台单号：{oid}")
    if o:
        lines.append(f"比赛：{str(o.get('sides') or '未知')}")
        lines.append(f"后台金额：{backend_stake:g}")
    lines.append(f"跟单成功金额：{ok_stake:g}")
    lines.append(f"跟单失败尝试金额：{fail_stake:g}")
    lines.append(f"成功笔数：{len(ok_rows)} / 总尝试：{len(rows)}")
    if not rows:
        lines.append("")
        lines.append("暂无该后台单的跟单记录。")
    return "\n".join(lines).strip()


def build_order_pnl_report(*, data_dir: Path, order_id: str) -> str:
    oid = _digits_only(order_id) or str(order_id or "").strip()
    rows = _order_rows_from_follow_results(data_dir, oid)
    ok_rows = [r for r in rows if bool(r.get("ok"))]
    settlements_idx = _load_ticket_settlements(data_dir)

    settled = 0
    pending = 0
    unmatched = 0
    profit_sum = 0.0
    profit_known = True
    for r in ok_rows:
        tid = _digits_only(r.get("ticket_id") or "")
        if not tid:
            unmatched += 1
            continue
        srec = settlements_idx.get(tid)
        if not isinstance(srec, dict):
            unmatched += 1
            continue
        st = str(srec.get("status") or "").strip().lower()
        if st in {"pending", "unsettled", "running", "processing"}:
            pending += 1
            continue
        settled += 1
        if srec.get("profit") is None:
            profit_known = False
            continue
        try:
            profit_sum += float(srec.get("profit"))
        except Exception:
            profit_known = False

    status_text = "未结算"
    if ok_rows and pending == 0 and unmatched == 0 and settled > 0 and profit_known:
        if profit_sum > 1e-9:
            status_text = "赢"
        elif profit_sum < -1e-9:
            status_text = "输"
        else:
            status_text = "走水"

    lines: List[str] = []
    lines.append("<b>📉 输赢结果</b>")
    lines.append(f"后台单号：{oid}")
    lines.append(f"状态：{status_text}")
    lines.append(f"输赢：{profit_sum:+g}" if (settled > 0 and profit_known) else "输赢：--")
    lines.append(f"已结算：{settled} / 未结算：{pending} / 未匹配：{unmatched}")
    if not ok_rows:
        lines.append("")
        lines.append("暂无跟单成功票，暂时无法统计输赢。")
    return "\n".join(lines).strip()


def build_order_pnl_alert(*, data_dir: Path, orders_dir: Path, order_id: str, max_chars: int = 180) -> str:
    def fmt_amount(v: Any) -> str:
        try:
            f = float(v)
        except Exception:
            return str(v or "0")
        if abs(f - round(f)) < 1e-9:
            return str(int(round(f)))
        return f"{f:.2f}".rstrip("0").rstrip(".")

    def fmt_profit(v: Any) -> str:
        try:
            f = float(v)
        except Exception:
            return str(v or "0")
        return f"{f:+.2f}"

    def clean_sides(v: Any) -> str:
        s = str(v or "").strip() or "未知"
        s = re.sub(r"\s*\([^)]*\)\s*$", "", s).strip()
        s = re.sub(r"\s+[vV]\s+", " vs ", s)
        return s or "未知"

    oid = _digits_only(order_id) or str(order_id or "").strip()
    rows = _order_rows_from_follow_results(data_dir, oid)
    ok_rows = [r for r in rows if bool(r.get("ok"))]
    settlements_idx = _load_ticket_settlements(data_dir)
    o = load_orders_index(orders_dir, [oid]).get(oid) or {}

    league = str(o.get("league") or "未知")
    sides = clean_sides(o.get("sides") or "未知")
    line = str(o.get("line") or "未知")
    odds = str(o.get("odds") or "未知")

    ok_stake_total = 0.0
    seen_ok: set = set()
    for r in ok_rows:
        ticket = _digits_only(r.get("ticket_id") or r.get("w_id") or "")
        if ticket:
            key = f"ticket:{ticket}"
        else:
            key = "|".join(
                [
                    str(r.get("member") or "").strip(),
                    str(r.get("split_index") or "").strip(),
                    str(r.get("stake") or "").strip(),
                    str(r.get("ts_ms") or r.get("ts") or "").strip(),
                ]
            )
        if key in seen_ok:
            continue
        seen_ok.add(key)
        try:
            ok_stake_total += float(r.get("stake") or 0.0)
        except Exception:
            continue

    follow_stake = fmt_amount(ok_stake_total)

    settled = 0
    pending = 0
    unmatched = 0
    profit_sum = 0.0
    profit_known = True
    for r in ok_rows:
        tid = _digits_only(r.get("ticket_id") or "")
        if not tid:
            unmatched += 1
            continue
        srec = settlements_idx.get(tid)
        if not isinstance(srec, dict):
            unmatched += 1
            continue
        st = str(srec.get("status") or "").strip().lower()
        if st in {"pending", "unsettled", "running", "processing"}:
            pending += 1
            continue
        settled += 1
        if srec.get("profit") is None:
            profit_known = False
            continue
        try:
            profit_sum += float(srec.get("profit"))
        except Exception:
            profit_known = False

    is_settled = bool(ok_rows) and pending == 0 and unmatched == 0 and settled > 0 and profit_known
    lines: List[str] = []
    if is_settled:
        lines.append("✅ 注单结算成功")
        lines.append("")
        lines.append(f"🏆 {league}")
        lines.append(f"⚽ {sides}")
        lines.append("")
        lines.append(f"🎯 玩法：{line}")
        lines.append(f"📈 水位：{odds}")
        lines.append(f"💰 盈利：{fmt_profit(profit_sum)}")
    else:
        lines.append("⏳ 注单已提交")
        lines.append("")
        lines.append(f"🏆 {league}")
        lines.append(f"⚽ {sides}")
        lines.append("")
        lines.append(f"🎯 玩法：{line}")
        lines.append(f"📈 水位：{odds}")
        lines.append(f"💰 金额：{follow_stake}")
        lines.append("")
        lines.append("⌛ 状态：等待结算")

    txt = "\n".join(lines).strip()
    n = max(20, int(max_chars))
    if len(txt) > n:
        txt = txt[: n - 1] + "…"
    return txt


def build_order_log_report(*, data_dir: Path, order_id: str, limit: int = 12) -> str:
    oid = _digits_only(order_id) or str(order_id or "").strip()
    rows: List[Dict[str, Any]] = []
    for r in iter_jsonl(data_dir / "follow_trace.jsonl"):
        if _digits_only(r.get("orderId") or "") != oid:
            continue
        rows.append(r)
    rows.sort(key=lambda x: int(float(x.get("ts") or 0)))
    if not rows:
        return (
            "<b>🧾 跟单日志</b>\n"
            f"后台单号：{oid}\n\n"
            "暂无日志。"
        )

    event_map = {
        "job_created": "任务创建",
        "attempt_start": "开始尝试",
        "attempt_result": "尝试结果",
        "part_scheduled": "分单排队",
        "job_done": "任务完成",
    }
    lines: List[str] = []
    lines.append("<b>🧾 跟单日志</b>")
    lines.append(f"后台单号：{oid}")
    lines.append("")
    for r in rows[-max(1, int(limit)):]:
        dt = _parse_ts_datetime(r.get("ts"))
        ts_txt = dt.strftime("%m-%d %H:%M:%S") if dt else "未知时间"
        ev = str(r.get("event") or "").strip()
        ev_txt = event_map.get(ev, ev or "event")
        attempt = int(r.get("attempt") or 0)
        split_i = int(r.get("split_index") or 0)
        split_c = int(r.get("split_count") or 0)
        ok = r.get("ok")
        member = str(r.get("member") or "").strip()
        msg = str(r.get("message") or r.get("reason") or "").strip()
        parts: List[str] = [f"{ts_txt}", ev_txt]
        if split_c > 0:
            parts.append(f"分单{max(1, split_i)}/{split_c}")
        if attempt > 0:
            parts.append(f"第{attempt}次")
        if ok is True:
            parts.append("成功")
        elif ok is False:
            parts.append("失败")
        if member:
            parts.append(member)
        if msg:
            parts.append(msg)
        lines.append("• " + " | ".join(parts))
    return "\n".join(lines).strip()


def build_order_log_alert(*, data_dir: Path, order_id: str, max_rows: int = 3, max_chars: int = 180) -> str:
    oid = _digits_only(order_id) or str(order_id or "").strip()
    rows: List[Dict[str, Any]] = []
    for r in iter_jsonl(data_dir / "follow_trace.jsonl"):
        if _digits_only(r.get("orderId") or "") != oid:
            continue
        rows.append(r)
    rows.sort(key=lambda x: int(float(x.get("ts") or 0)))

    state_done_status = ""
    state_done_reason = ""
    state_done_ts = 0.0
    try:
        state_obj = read_json(data_dir / "follow_state.json")
        done_map = state_obj.get("done")
        if isinstance(done_map, dict):
            for k, v in done_map.items():
                if not isinstance(v, dict):
                    continue
                key_txt = str(k or "").strip()
                key_oid = _digits_only(key_txt.rsplit("|", 1)[-1])
                if key_oid != oid:
                    continue
                st = str(v.get("status") or "").strip().lower()
                if not st:
                    continue
                ts0 = 0.0
                try:
                    ts0 = float(v.get("ts") or 0.0)
                except Exception:
                    ts0 = 0.0
                if ts0 < state_done_ts:
                    continue
                state_done_ts = ts0
                state_done_status = st
                state_done_reason = str(v.get("reason") or "").strip()
    except Exception:
        pass

    if not rows:
        if state_done_status:
            lines = [
                "══════════════",
                "🧾 跟单日志",
                "══════════════",
                f"🆔 后台单号：{oid}",
                "──────────────",
            ]
            if state_done_status == "failed":
                lines.append("❌ 任务已停止")
                if state_done_reason:
                    lines.append(f"原因：{state_done_reason}")
            elif state_done_status in {"skipped", "skip", "not_live"}:
                lines.append("⏹ 任务已跳过")
                if state_done_reason:
                    lines.append(f"原因：{state_done_reason}")
            else:
                lines.append("✅ 任务完成")
            lines.append("══════════════")
            text = "\n".join(lines).strip()
            return text[: max(20, int(max_chars))]
        text = (
            "══════════════\n"
            "🧾 跟单日志\n"
            "══════════════\n\n"
            f"🆔 后台单号：{oid}\n\n"
            "暂无日志"
        )
        return text[: max(20, int(max_chars))]

    split_count = 0
    for r in rows:
        try:
            split_count = max(split_count, int(r.get("split_count") or 0))
        except Exception:
            continue
    if split_count <= 0:
        split_count = 1

    parts: Dict[int, Dict[str, Any]] = {}
    job_done = False
    job_status = ""
    job_reason = ""
    latest_failed_reason = ""
    for r in rows:
        ev = str(r.get("event") or "").strip()
        if ev == "job_done":
            job_done = True
            st = str(r.get("status") or "").strip().lower()
            if st:
                job_status = st
            rr = str(r.get("reason") or "").strip()
            if rr:
                job_reason = rr
            continue
        idx = 0
        try:
            idx = int(r.get("split_index") or 0)
        except Exception:
            idx = 0
        if idx <= 0:
            continue
        p = parts.setdefault(idx, {})
        if ev == "attempt_start" and not p.get("start_ts"):
            p["start_ts"] = r.get("ts")
        if ev == "attempt_result":
            p["result_ts"] = r.get("ts")
            p["ok"] = r.get("ok")
            p["member"] = str(r.get("member") or "").strip()
            msg = str(r.get("message") or r.get("reason") or "").strip()
            p["message"] = msg
            p["err"] = str(r.get("err") or "").strip()
            if p.get("ok") is False and msg:
                latest_failed_reason = msg
    if (not job_done) and state_done_status:
        job_done = True
        job_status = state_done_status
        if state_done_reason:
            job_reason = state_done_reason
            if not latest_failed_reason:
                latest_failed_reason = state_done_reason

    def _fmt_short_ts(ts: Any) -> str:
        dt = _parse_ts_datetime(ts)
        return dt.strftime("%m-%d %H:%M:%S") if dt else "--"

    def _short_text(s: str, n: int) -> str:
        s = re.sub(r"\s+", " ", str(s or "").strip())
        if not s:
            return ""
        n = max(8, int(n))
        if len(s) > n:
            return s[: n - 1] + "…"
        return s

    compact_mode = int(max_chars) <= 180
    lines: List[str] = []
    if compact_mode:
        lines.append("🧾 跟单日志")
        lines.append(f"🆔 {oid}")
    else:
        lines.append("══════════════")
        lines.append("🧾 跟单日志")
        lines.append("══════════════")
        lines.append(f"🆔 后台单号：{oid}")
        lines.append("──────────────")
    rich_mode = int(max_chars) >= 260

    limit = max(1, int(max_rows))
    keys = sorted(parts.keys())[:limit]
    if not keys:
        lines.append("暂无分单日志")
    for i in keys:
        p = parts.get(i) or {}
        ts_show = _fmt_short_ts(p.get("result_ts") or p.get("start_ts"))
        if compact_mode and ts_show != "--":
            ts_show = ts_show[-8:]
        ok = p.get("ok")
        icon = "✔" if ok is True else ("✘" if ok is False else "…")
        member = str(p.get("member") or "").strip() or "-"
        if compact_mode:
            lines.append(f"📌{i}/{split_count} {ts_show} {icon} {member}")
        else:
            lines.append(f"📌 {i}/{split_count} {ts_show} {icon} {member}")
        msg = str(p.get("message") or "").strip()
        if msg:
            if ok is False:
                lines.append(f"   原因：{_short_text(msg, 24 if compact_mode else 42)}")
            elif rich_mode:
                lines.append(f"   ↳ {_short_text(msg, 28)}")

    if len(parts) > limit:
        lines.append(f"... 还有{len(parts) - limit}笔")
    if not compact_mode:
        lines.append("──────────────")
    if job_done:
        if job_status == "failed":
            lines.append("❌ 任务已停止")
            reason_show = _short_text(job_reason or latest_failed_reason, 28 if compact_mode else 48)
            if reason_show:
                lines.append(f"原因：{reason_show}")
        elif job_status in {"skipped", "skip", "not_live"}:
            lines.append("⏹ 任务已跳过")
            reason_show = _short_text(job_reason, 28 if compact_mode else 48)
            if reason_show:
                lines.append(f"原因：{reason_show}")
        else:
            lines.append("✅ 任务完成")
    else:
        lines.append("⏳ 任务进行中")
        if latest_failed_reason and not compact_mode:
            lines.append(f"最近失败：{_short_text(latest_failed_reason, 36)}")
    if not compact_mode:
        lines.append("══════════════")

    txt = "\n".join(lines).strip()
    n = max(20, int(max_chars))
    if len(txt) > n:
        txt = txt[: n - 1] + "…"
    return txt


def is_winrate_command(text: str) -> bool:
    t = str(text or "").strip()
    if not t:
        return False
    t_low = t.lower()
    if t_low.startswith("/roi"):
        return True
    if t in {"投资回报率", "ROI", "roi", BTN_WINRATE}:
        return True
    return False


def is_report_command(text: str) -> bool:
    t = str(text or "").strip()
    if not t:
        return False
    t_low = t.lower()
    if t_low.startswith("/today") or t_low.startswith("/report") or t_low.startswith("/follow"):
        return True
    if "今日跟单" in t or "跟单报表" in t:
        return True
    return False


def is_balance_command(text: str) -> bool:
    t = str(text or "").strip()
    if not t:
        return False
    if t in {"余额", "查询余额", BTN_QUERY_BALANCE}:
        return True
    parts = t.split()
    if not parts:
        return False
    cmd = str(parts[0] or "").strip()
    cmd = cmd.split("@", 1)[0].lower()
    return cmd in {"/balance", "/bal", "/money", "/credit"}


def is_node_status_command(text: str) -> bool:
    t = str(text or "").strip()
    if not t:
        return False
    if t in {BTN_NODE_STATUS, "节点状态", "在线状态", "登3状态", "登入3状态"}:
        return True
    parts = t.split()
    if not parts:
        return False
    cmd = str(parts[0] or "").strip()
    cmd = cmd.split("@", 1)[0].lower()
    return cmd in {"/node", "/nodes", "/node_status", "/login3"}


def main_keyboard() -> Dict[str, Any]:
    return {
        "keyboard": [
            [{"text": BTN_QUERY_BALANCE}, {"text": BTN_NODE_STATUS}],
            [{"text": BTN_TODAY_REPORT}, {"text": BTN_PNL_REPORT}],
            [{"text": BTN_WINRATE}],
            [{"text": BTN_ADMIN_AUTH}],
        ],
        "resize_keyboard": True,
        "one_time_keyboard": False,
        "is_persistent": True,
    }


def admin_keyboard() -> Dict[str, Any]:
    return {
        "keyboard": [
            [{"text": BTN_ADMIN_GRANT}, {"text": BTN_ADMIN_REVOKE}],
            [{"text": BTN_ADMIN_LIST}],
            [{"text": BTN_BACK}],
        ],
        "resize_keyboard": True,
        "one_time_keyboard": False,
        "is_persistent": True,
    }


def is_pnl_report_command(text: str) -> bool:
    t = str(text or "").strip()
    if not t:
        return False
    tl = t.lower()
    if tl.startswith("/pnl") or tl.startswith("/profit") or tl.startswith("/pl"):
        return True
    return t in {BTN_PNL_REPORT, "输赢", "盈亏报表", "输赢报表"}


def is_admin_auth_command(text: str) -> bool:
    t = str(text or "").strip()
    if not t:
        return False
    tl = t.lower()
    if tl.startswith("/admin") or tl.startswith("/auth"):
        return True
    return t in {BTN_ADMIN_AUTH, "管理员授权", "授权管理"}


def is_deprecated_command(text: str) -> bool:
    t = str(text or "").strip()
    if not t:
        return False
    deprecated = {
        "跟单配置",
        "添加子账号",
        "弹药库",
        "固定金额",
        "比例跟单",
        "启用/停用",
        "查看当前配置",
        "添加登3",
        "当前后台数",
        "查看后台列表",
        "删除后台账号",
        "添加会员",
        "当前会员数",
        "查看成员列表",
        "删除会员",
    }
    if t in deprecated:
        return True
    tl = t.lower()
    return tl.startswith("/follow_config") or tl.startswith("/config") or tl.startswith("/add_backend") or tl.startswith("/arsenal")


def is_main_menu(text: str) -> bool:
    t = str(text or "").strip()
    if not t:
        return False
    if t in {BTN_BACK, "菜单", "帮助", "返回菜单", "返回主菜单"}:
        return True
    tl = t.lower()
    return tl in {"/start", "/menu", "/help"}


def _split_command(text: str) -> Tuple[str, str]:
    t = str(text or "").strip()
    if not t:
        return "", ""
    parts = t.split(maxsplit=1)
    cmd = str(parts[0] or "").strip()
    if cmd.startswith("/"):
        cmd = cmd.split("@", 1)[0].lower()
    else:
        cmd = ""
    arg = str(parts[1] or "").strip() if len(parts) > 1 else ""
    return cmd, arg


def parse_admin_grant_command(text: str) -> Optional[str]:
    cmd, arg = _split_command(text)
    if cmd not in {"/grant", "/auth_add"}:
        return None
    return arg


def parse_admin_revoke_command(text: str) -> Optional[str]:
    cmd, arg = _split_command(text)
    if cmd not in {"/revoke", "/auth_del", "/ungrant"}:
        return None
    return arg


def is_admin_list_command(text: str) -> bool:
    cmd, _ = _split_command(text)
    return cmd in {"/authlist", "/grantlist", "/listauth"}


def ensure_tg_auth(path: Path) -> Dict[str, Any]:
    doc = read_json(path) if path.exists() else {}
    if not isinstance(doc, dict):
        doc = {}

    def norm_section(name: str) -> Dict[str, Any]:
        sec = doc.get(name)
        if not isinstance(sec, dict):
            sec = {}
        ids = sorted({str(x).strip() for x in (sec.get("ids") or []) if str(x).strip()})
        usernames = sorted(
            {
                _normalize_tg_username(x)
                for x in (sec.get("usernames") or [])
                if _normalize_tg_username(x)
            }
        )
        return {"ids": ids, "usernames": usernames}

    normalized = {
        "admins": norm_section("admins"),
        "grants": norm_section("grants"),
        "updated_at": str(doc.get("updated_at") or ""),
    }
    if normalized.get("updated_at") == "":
        normalized["updated_at"] = now_iso()

    if normalized != doc:
        atomic_write_json(path, normalized)
    return normalized


def _auth_parse_subject(raw: str) -> Optional[Tuple[str, str]]:
    s = str(raw or "").strip()
    if not s:
        return None
    if re.fullmatch(r"-?\d+", s):
        return ("ids", s)
    u = _normalize_tg_username(s)
    if u and re.fullmatch(r"[a-z0-9_]{3,64}", u):
        return ("usernames", u)
    return None


def _auth_has_subject(auth_doc: Dict[str, Any], section: str, *, chat_id: str, user_id: str, usernames: Iterable[str]) -> bool:
    sec = auth_doc.get(section) or {}
    if not isinstance(sec, dict):
        return False
    ids = {str(x).strip() for x in (sec.get("ids") or []) if str(x).strip()}
    us = {_normalize_tg_username(x) for x in (sec.get("usernames") or []) if _normalize_tg_username(x)}
    if chat_id and chat_id in ids:
        return True
    if user_id and user_id in ids:
        return True
    for n in usernames:
        nn = _normalize_tg_username(n)
        if nn and nn in us:
            return True
    return False


def is_admin_user(
    *,
    auth_doc: Dict[str, Any],
    chat_id: str,
    user_id: str,
    usernames: Iterable[str],
    allow_ids: Set[str],
    allow_usernames: Set[str],
) -> bool:
    if _auth_has_subject(auth_doc, "admins", chat_id=chat_id, user_id=user_id, usernames=usernames):
        return True
    if chat_id and chat_id in allow_ids:
        return True
    for n in usernames:
        nn = _normalize_tg_username(n)
        if nn and nn in allow_usernames:
            return True
    return False


def is_authorized_user(
    *,
    auth_doc: Dict[str, Any],
    chat_id: str,
    user_id: str,
    usernames: Iterable[str],
    allow_ids: Set[str],
    allow_usernames: Set[str],
) -> bool:
    if is_admin_user(
        auth_doc=auth_doc,
        chat_id=chat_id,
        user_id=user_id,
        usernames=usernames,
        allow_ids=allow_ids,
        allow_usernames=allow_usernames,
    ):
        return True
    return _auth_has_subject(auth_doc, "grants", chat_id=chat_id, user_id=user_id, usernames=usernames)


def _auth_add_subject(auth_doc: Dict[str, Any], section: str, raw: str) -> Tuple[Dict[str, Any], bool, str]:
    parsed = _auth_parse_subject(raw)
    if not parsed:
        return auth_doc, False, "请输入 chat_id/user_id 或 @username"
    key, value = parsed
    sec = dict(auth_doc.get(section) or {})
    items = {str(x).strip() for x in (sec.get(key) or []) if str(x).strip()}
    before = len(items)
    items.add(value)
    sec[key] = sorted(items)
    out = dict(auth_doc)
    out[section] = sec
    out["updated_at"] = now_iso()
    changed = len(items) != before
    return out, changed, ("已存在，无需重复授权" if not changed else "授权成功")


def _auth_remove_subject(auth_doc: Dict[str, Any], section: str, raw: str) -> Tuple[Dict[str, Any], bool, str]:
    parsed = _auth_parse_subject(raw)
    if not parsed:
        return auth_doc, False, "请输入 chat_id/user_id 或 @username"
    key, value = parsed
    sec = dict(auth_doc.get(section) or {})
    items = {str(x).strip() for x in (sec.get(key) or []) if str(x).strip()}
    if value not in items:
        return auth_doc, False, "未找到该授权对象"
    items.remove(value)
    sec[key] = sorted(items)
    out = dict(auth_doc)
    out[section] = sec
    out["updated_at"] = now_iso()
    return out, True, "已取消授权"


def auth_summary_text(auth_doc: Dict[str, Any]) -> str:
    def sec_lines(title: str, sec_name: str) -> List[str]:
        sec = auth_doc.get(sec_name) or {}
        if not isinstance(sec, dict):
            sec = {}
        ids = [str(x).strip() for x in (sec.get("ids") or []) if str(x).strip()]
        users = [_normalize_tg_username(x) for x in (sec.get("usernames") or []) if _normalize_tg_username(x)]
        lines: List[str] = [f"<b>{title}</b>"]
        if not ids and not users:
            lines.append("- （空）")
            return lines
        for x in ids:
            lines.append(f"- {x}")
        for u in users:
            lines.append(f"- @{u}")
        return lines

    lines: List[str] = []
    lines.append("<b>🔐 授权列表</b>")
    lines.append("")
    lines.extend(sec_lines("管理员", "admins"))
    lines.append("")
    lines.extend(sec_lines("已授权用户", "grants"))
    updated_at = str(auth_doc.get("updated_at") or "").strip()
    if updated_at:
        lines.append("")
        lines.append(f"更新时间：{updated_at}")
    return "\n".join(lines)


def _parse_ts_datetime(ts: Any) -> Optional[datetime]:
    try:
        fv = float(ts)
    except Exception:
        return None
    if fv <= 0:
        return None
    if fv > 1_000_000_000_000:
        fv = fv / 1000.0
    try:
        return datetime.fromtimestamp(int(fv), tz=BJ_TZ)
    except Exception:
        return None


def _order_date_from_rows(rows: List[Dict[str, Any]]) -> Optional[datetime.date]:
    dt0: Optional[datetime] = None
    for r in rows:
        dt = _parse_ts_datetime(r.get("ts"))
        if dt is None:
            continue
        if dt0 is None or dt < dt0:
            dt0 = dt
    return report_day_start(dt0).date() if dt0 else None


def _order_settlement_summary(rows: List[Dict[str, Any]], settlements_idx: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    tickets = [_digits_only(r.get("ticket_id") or "") for r in rows]
    tickets = [t for t in tickets if t]
    if not tickets:
        return {
            "status": "unmatched",
            "profit_sum": 0.0,
            "settled_count": 0,
            "pending_count": 0,
            "unmatched_count": 0,
            "ticket_count": 0,
        }

    profit_sum = 0.0
    settled_count = 0
    pending_count = 0
    unmatched_count = 0
    profit_known = True

    for tid in tickets:
        srec = settlements_idx.get(tid)
        if not srec:
            unmatched_count += 1
            continue
        status = str(srec.get("status") or "").strip().lower()
        if status in {"pending", "unsettled", "running"}:
            pending_count += 1
            continue
        settled_count += 1
        if srec.get("profit") is None:
            profit_known = False
            continue
        try:
            profit_sum += float(srec.get("profit"))
        except Exception:
            profit_known = False

    out_status = "unmatched"
    if pending_count > 0:
        out_status = "pending"
    elif unmatched_count > 0 or settled_count <= 0 or not profit_known:
        out_status = "unmatched"
    else:
        out_status = "settled"

    return {
        "status": out_status,
        "profit_sum": float(profit_sum),
        "settled_count": int(settled_count),
        "pending_count": int(pending_count),
        "unmatched_count": int(unmatched_count),
        "ticket_count": int(len(tickets)),
    }


def _history_number(v: Any) -> float:
    s = str(v or "").strip()
    if not s or s == "-":
        return 0.0
    s = s.replace(",", "")
    try:
        return float(s)
    except Exception:
        return 0.0


def _format_report_amount(v: Any) -> str:
    try:
        f = float(v)
    except Exception:
        return str(v)
    if abs(f - round(f)) < 1e-9:
        return f"{f:.1f}"
    return f"{f:.2f}".rstrip("0").rstrip(".")


def _format_report_signed_amount(v: Any) -> str:
    try:
        f = float(v)
    except Exception:
        return str(v)
    if f > 1e-9:
        return f"+{_format_report_amount(f)}"
    if f < -1e-9:
        return f"-{_format_report_amount(abs(f))}"
    return _format_report_amount(0.0)


def _format_report_pct(v: float) -> str:
    return f"{max(0.0, float(v)) * 100.0:.1f}%"


def _curl_transform_post(
    *,
    base_url: str,
    ver: str,
    cookie: str,
    form: Dict[str, Any],
    timeout_s: int = 15,
    proxy: str = "",
) -> Tuple[Optional[str], str]:
    base = str(base_url or "").strip().rstrip("/")
    v = str(ver or "").strip()
    if not base or not v:
        return None, "bad_base_or_ver"
    url = f"{base}/transform.php?ver={v}"
    cmd = [
        "curl",
        "-k",
        "-sS",
        url,
        "-H",
        "Accept: */*",
        "-H",
        "Accept-Language: zh-CN,zh;q=0.9",
        "-H",
        "Content-Type: application/x-www-form-urlencoded",
        "-H",
        f"Origin: {base}",
        "-H",
        f"Referer: {base}/",
        "-H",
        "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
        "-b",
        str(cookie or ""),
        "--data-raw",
        urlencode({k: "" if v2 is None else str(v2) for k, v2 in (form or {}).items()}),
        "--max-time",
        str(max(2, int(timeout_s or 0))),
    ]
    proxy_s = str(proxy or "").strip()
    if proxy_s:
        cmd.extend(["-x", proxy_s])
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        err = str(res.stderr or "").strip() or f"curl_rc={res.returncode}"
        return None, f"curl_failed: {err[:200]}"
    out = str(res.stdout or "").strip()
    if not out:
        return None, "empty_response"
    return out, "ok"


def parse_member_history_report_xml(xml_text: str) -> Dict[str, Any]:
    text = str(xml_text or "").strip()
    if not text:
        raise ValueError("empty_history_xml")
    root = ET.fromstring(text)
    rows: List[Dict[str, Any]] = []
    for node in root.findall("history"):
        date_class = str(node.findtext("date_class") or "").strip()
        gold_raw = node.findtext("gold")
        vgold_raw = node.findtext("vgold")
        winloss_raw = node.findtext("winloss")
        has_data = (
            date_class != "his_list_none"
            and str(gold_raw or "").strip() != "-"
            and str(vgold_raw or "").strip() != "-"
            and str(winloss_raw or "").strip() != "-"
        )
        rows.append(
            {
                "date": str(node.findtext("date") or "").strip(),
                "date_name": str(node.findtext("date_name") or "").strip(),
                "date_class": date_class,
                "gold": _history_number(gold_raw),
                "vgold": _history_number(vgold_raw),
                "winloss": _history_number(winloss_raw),
                "has_data": bool(has_data),
            }
        )
    return {
        "ok": True,
        "code": str(root.findtext("code") or "").strip(),
        "total_gold": _history_number(root.findtext("total_gold")),
        "total_vgold": _history_number(root.findtext("total_vgold")),
        "total_winloss": _history_number(root.findtext("total_winloss")),
        "rows": rows,
        "date_bar": str(root.findtext("dateBar") or "").strip(),
        "history_view_day": str(root.findtext("history_view_day") or "").strip(),
    }


def fetch_member_history_report(
    *,
    member: Dict[str, Any],
    startdate: str,
    enddate: str,
    timeout_s: int = 15,
) -> Dict[str, Any]:
    base_url = str(member.get("base_url") or "").strip()
    ver = str(member.get("ver") or "").strip()
    uid = str(member.get("uid") or "").strip()
    cookie = str(member.get("cookie") or "").strip()
    proxy = str(member.get("proxy") or "").strip()
    label = str(member.get("label") or member.get("username") or member.get("id") or "").strip() or "unknown"
    if not (base_url and ver and uid and cookie):
        return {"ok": False, "member": label, "error": "missing_base_url_ver_uid_or_cookie"}

    text, msg = _curl_transform_post(
        base_url=base_url,
        ver=ver,
        cookie=cookie,
        proxy=proxy,
        timeout_s=timeout_s,
        form={
            "p": "get_history_data",
            "uid": uid,
            "langx": "zh-cn",
            "gtype": "ALL",
            "isAll": "N",
            "startdate": str(startdate or "").strip(),
            "enddate": str(enddate or "").strip(),
            "filter": "Y",
        },
    )
    if not text:
        return {"ok": False, "member": label, "error": msg}
    try:
        parsed = parse_member_history_report_xml(text)
    except Exception as e:
        return {"ok": False, "member": label, "error": f"history_xml_parse_failed: {type(e).__name__}: {e}"}
    parsed["member"] = label
    return parsed


def _history_report_has_data(report: Dict[str, Any]) -> bool:
    if not bool(report.get("ok")):
        return False
    if abs(float(report.get("total_gold") or 0.0)) > 1e-9:
        return True
    if abs(float(report.get("total_vgold") or 0.0)) > 1e-9:
        return True
    if abs(float(report.get("total_winloss") or 0.0)) > 1e-9:
        return True
    for row in report.get("rows") or []:
        if bool(row.get("has_data")):
            return True
    return False


PNL_REPORT_MESSAGE_EFFECT_ID = "5046509860389126442"
PNL_REPORT_CUSTOM_EMOJI_SPECS: List[Tuple[str, int, str]] = [
    ("🗓", 0, "5929035234950519861"),
    ("🏦", 0, "6034925080947989851"),
    ("✅️", 0, "5771711424711626153"),
    ("🗓", 1, "5929165466948869003"),
    ("🤣", 0, "5936182279574396054"),
    ("🏦", 1, "6034925080947989851"),
    ("✅️", 1, "5771711424711626153"),
    ("⭐️", 0, "5913447097826282755"),
]


def _utf16_units(s: str) -> int:
    return len(str(s or "").encode("utf-16-le")) // 2


def _utf16_offset_of_nth(text: str, marker: str, occurrence: int) -> int:
    start = 0
    idx = -1
    for _ in range(max(0, int(occurrence)) + 1):
        idx = text.find(marker, start)
        if idx < 0:
            raise ValueError(f"marker_not_found: {marker}#{occurrence}")
        start = idx + len(marker)
    return _utf16_units(text[:idx])


def build_remote_member_pnl_report_entities(text: str) -> List[Dict[str, Any]]:
    entities: List[Dict[str, Any]] = []
    for marker, occurrence, custom_emoji_id in PNL_REPORT_CUSTOM_EMOJI_SPECS:
        entities.append(
            {
                "type": "custom_emoji",
                "offset": _utf16_offset_of_nth(text, marker, occurrence),
                "length": _utf16_units(marker),
                "custom_emoji_id": custom_emoji_id,
            }
        )
    return entities


def build_remote_member_pnl_report(
    *,
    data_dir: Path,
    now: Optional[datetime] = None,
) -> str:
    dt_now = now or now_bj()
    if dt_now.tzinfo is None:
        dt_now = dt_now.replace(tzinfo=BJ_TZ)
    dt_now = dt_now.astimezone(BJ_TZ)
    report_start, _report_end = report_day_window(now=dt_now)
    report_date = report_start.date()
    week_start = report_date - timedelta(days=report_date.weekday())
    week_end = report_date

    arsenal_path = data_dir / "arsenal.json"
    doc = ensure_arsenal(arsenal_path)
    members = [
        m for m in list_members(doc)
        if str(m.get("status") or "").strip().lower() == "online"
        and str(m.get("base_url") or "").strip()
        and str(m.get("ver") or "").strip()
        and str(m.get("uid") or "").strip()
        and str(m.get("cookie") or "").strip()
    ]
    if not members:
        return (
            "📉 HD Sport 战绩报表\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "暂无可用的在线会员历史报表数据。"
        )

    today_stats = {
        "pnl": 0.0,
        "gold": 0.0,
        "vgold": 0.0,
        "win": 0,
        "lose": 0,
        "push": 0,
        "settled": 0,
    }
    week_stats = {
        "pnl": 0.0,
        "gold": 0.0,
        "vgold": 0.0,
        "win": 0,
        "lose": 0,
        "push": 0,
        "settled": 0,
    }
    failed_members: List[str] = []
    success_members = 0

    today_s = report_date.strftime("%Y-%m-%d")
    week_start_s = week_start.strftime("%Y-%m-%d")
    week_end_s = week_end.strftime("%Y-%m-%d")

    for member in members:
        rep_week = fetch_member_history_report(member=member, startdate=week_start_s, enddate=week_end_s, timeout_s=15)
        fallback_end = week_end
        while (
            rep_week.get("ok")
            and not _history_report_has_data(rep_week)
            and fallback_end > week_start
        ):
            fallback_end = fallback_end - timedelta(days=1)
            rep_week = fetch_member_history_report(
                member=member,
                startdate=week_start_s,
                enddate=fallback_end.strftime("%Y-%m-%d"),
                timeout_s=15,
            )
        if not rep_week.get("ok"):
            failed_members.append(str(rep_week.get("member") or member.get("label") or "unknown"))
            continue
        success_members += 1

        week_stats["pnl"] += float(rep_week.get("total_winloss") or 0.0)
        week_stats["gold"] += float(rep_week.get("total_gold") or 0.0)
        week_stats["vgold"] += float(rep_week.get("total_vgold") or 0.0)
        for row in rep_week.get("rows") or []:
            if not bool(row.get("has_data")):
                continue
            wl = float(row.get("winloss") or 0.0)
            week_stats["settled"] += 1
            if wl > 1e-9:
                week_stats["win"] += 1
            elif wl < -1e-9:
                week_stats["lose"] += 1
            else:
                week_stats["push"] += 1

            if str(row.get("date") or "").strip() != today_s:
                continue
            today_stats["pnl"] += wl
            today_stats["gold"] += float(row.get("gold") or 0.0)
            today_stats["vgold"] += float(row.get("vgold") or 0.0)
            today_stats["settled"] += 1
            if wl > 1e-9:
                today_stats["win"] += 1
            elif wl < -1e-9:
                today_stats["lose"] += 1
            else:
                today_stats["push"] += 1

    if (
        today_stats["settled"] <= 0
        and week_stats["settled"] <= 0
        and abs(today_stats["gold"]) < 1e-9
        and abs(week_stats["gold"]) < 1e-9
    ):
        return (
            "📉 HD Sport 战绩报表\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "未获取到可用的会员历史报表数据。"
        )

    today_total = int(today_stats.get("win") or 0) + int(today_stats.get("lose") or 0) + int(today_stats.get("push") or 0)
    today_winrate = (float(int(today_stats.get("win") or 0)) / float(today_total)) if today_total > 0 else 0.0
    lines = [
        f"🗓今日数据（{today_s}）",
        "",
        f"💸 账面亏盈：  {_format_report_signed_amount(today_stats.get('pnl') or 0.0)}",
        f"🏦投注金额：  {_format_report_amount(today_stats.get('gold') or 0.0)}",
        f"✅️有效金额：  {_format_report_amount(today_stats.get('vgold') or 0.0)}",
        "",
        f"🗓本周数据（{week_start_s} ~ {week_end_s}）",
        "",
        f"🤣账面亏盈：  {_format_report_signed_amount(week_stats.get('pnl') or 0.0)}",
        f"🏦投注金额：  {_format_report_amount(week_stats.get('gold') or 0.0)}",
        f"✅️有效金额：  {_format_report_amount(week_stats.get('vgold') or 0.0)}",
        f"⭐️查询会员总数:{max(0, int(success_members))}",
    ]
    return "\n".join(lines).strip()


def build_remote_member_pnl_report_message(
    *,
    data_dir: Path,
    now: Optional[datetime] = None,
    enable_effect: bool = False,
) -> Dict[str, Any]:
    text = build_remote_member_pnl_report(data_dir=data_dir, now=now)
    payload: Dict[str, Any] = {"text": text}
    if "查询会员总数:" in text and "🗓本周数据" in text:
        try:
            payload["entities"] = build_remote_member_pnl_report_entities(text)
        except Exception:
            payload["entities"] = []
    if enable_effect:
        payload["message_effect_id"] = PNL_REPORT_MESSAGE_EFFECT_ID
    return payload


def build_pnl_report(*, data_dir: Path) -> str:
    return build_remote_member_pnl_report(data_dir=data_dir)


def build_follow_summary_alert(*, data_dir: Path, max_chars: int = 200) -> str:
    results_path = data_dir / "follow_results.jsonl"
    all_rows = list(iter_jsonl(results_path))
    ok_rows = [r for r in all_rows if bool(r.get("ok")) and str(r.get("orderId") or "").strip()]

    day_start, _day_end = report_day_window()
    today_key = day_start.date()
    week_start = today_key - timedelta(days=today_key.weekday())
    settlements_idx = _load_ticket_settlements(data_dir)

    periods: Dict[str, Dict[str, Any]] = {
        "today": {"orders": 0, "settled": 0, "pending": 0, "unmatched": 0, "win": 0, "lose": 0, "push": 0, "pnl": 0.0},
        "week": {"orders": 0, "settled": 0, "pending": 0, "unmatched": 0, "win": 0, "lose": 0, "push": 0, "pnl": 0.0},
    }

    groups: Dict[str, List[Dict[str, Any]]] = {}
    for r in ok_rows:
        oid = str(r.get("orderId") or "").strip()
        if oid:
            groups.setdefault(oid, []).append(r)

    for rows in groups.values():
        d = _order_date_from_rows(rows)
        if d is None:
            continue
        keys: List[str] = []
        if d == today_key:
            keys.append("today")
        if week_start <= d <= today_key:
            keys.append("week")
        if not keys:
            continue

        summary = _order_settlement_summary(rows, settlements_idx)
        for k in keys:
            p = periods[k]
            p["orders"] = int(p["orders"]) + 1
            st = str(summary.get("status") or "")
            if st == "settled":
                pnl = float(summary.get("profit_sum") or 0.0)
                p["settled"] = int(p["settled"]) + 1
                p["pnl"] = float(p["pnl"] or 0.0) + pnl
                if pnl > 1e-9:
                    p["win"] = int(p["win"]) + 1
                elif pnl < -1e-9:
                    p["lose"] = int(p["lose"]) + 1
                else:
                    p["push"] = int(p["push"]) + 1
            elif st == "pending":
                p["pending"] = int(p["pending"]) + 1
            else:
                p["unmatched"] = int(p["unmatched"]) + 1

    def fmt_amount(v: Any) -> str:
        try:
            f = float(v)
        except Exception:
            return str(v)
        if abs(f - round(f)) < 1e-9:
            return f"{f:.1f}"
        return f"{f:.2f}".rstrip("0").rstrip(".")

    def fmt_signed(v: Any) -> str:
        try:
            f = float(v)
        except Exception:
            return str(v)
        if f > 1e-9:
            return f"+{fmt_amount(f)}"
        if f < -1e-9:
            return f"-{fmt_amount(abs(f))}"
        return fmt_amount(0.0)

    def fmt_pct(v: Optional[float]) -> str:
        if v is None:
            return "--"
        return f"{v * 100.0:.1f}%"

    def block_lines(p: Dict[str, Any], *, title: str, show_date: bool = False) -> List[str]:
        win = int(p.get("win") or 0)
        lose = int(p.get("lose") or 0)
        push = int(p.get("push") or 0)
        settled = int(p.get("settled") or 0)
        pending = int(p.get("pending") or 0)
        unmatched = int(p.get("unmatched") or 0)
        orders = int(p.get("orders") or 0)
        unsettled = pending + unmatched
        denom = float(win + lose + push)
        wr = (win / denom) if denom > 0 else None

        head = f"{title}（{today_str()}）" if show_date else title
        return [
            head,
            f"💸 盈亏： {fmt_signed(p.get('pnl') or 0.0)}",
            f"🎯 胜率： {fmt_pct(wr)}",
            f"📊 战绩： {win}胜 / {lose}负 / {push}走",
            f"🎮 场次：{orders} ｜ 已结{settled} ｜ 未结{unsettled}",
        ]

    lines: List[str] = []
    lines.append("📉 输赢报表")
    lines.append("────────────────")
    lines.extend(block_lines(periods["today"], title="📅 今日", show_date=True))
    lines.append("")
    lines.append("────────────────")
    lines.extend(block_lines(periods["week"], title="🗓 本周累计", show_date=False))

    txt = "\n".join(lines).strip()
    n = max(20, int(max_chars))
    if len(txt) > n:
        txt = txt[: n - 1] + "…"
    return txt


def backend_targets_summary(cfg: Dict[str, Any]) -> str:
    targets = cfg.get("targets") or []
    if not isinstance(targets, list):
        targets = []
    lines: List[str] = []
    lines.append("<b>🧩 后台账号</b>")
    lines.append(f"数量：{len(targets)}")
    if targets:
        lines.append("")
        for i, t in enumerate(targets, start=1):
            if not isinstance(t, dict):
                continue
            name = str(t.get("name") or "")
            username = str(t.get("username") or "")
            base_url = str(t.get("base_url") or "")
            tab = str(t.get("relogin_login_tab") or "")
            lines.append(f"{i}. {name}（账号:{username or name} tab:{tab or '-'}）")
            if base_url:
                lines.append(f"   {base_url}")
    return "\n".join(lines)


def delete_target_by_name(cfg: Dict[str, Any], name: str) -> Tuple[Dict[str, Any], bool]:
    targets = cfg.get("targets") or []
    if not isinstance(targets, list):
        return cfg, False
    before = len(targets)
    out = [t for t in targets if not (isinstance(t, dict) and str(t.get("name") or "") == str(name))]
    if len(out) == before:
        return cfg, False
    cfg2 = dict(cfg)
    cfg2["targets"] = out
    return cfg2, True


def add_backend_target(
    *,
    cfg: Dict[str, Any],
    username: str,
    password: str,
    login_tab: int,
) -> Tuple[Dict[str, Any], str]:
    username = str(username or "").strip()
    password = str(password or "").strip()
    if not username or not password:
        raise ValueError("缺少账号或密码")

    targets = cfg.get("targets") or []
    if not isinstance(targets, list):
        targets = []

    template = next((t for t in targets if isinstance(t, dict)), None) or {}
    base_url = str(template.get("base_url") or cfg.get("base_url") or "").strip()
    ver = str(template.get("ver") or cfg.get("ver") or "").strip()
    if not base_url or not ver:
        raise ValueError("缺少 base_url/ver（请先在 config.json 配置至少一个后台账号作为模板）")

    existing_names = {str(t.get("name") or "") for t in targets if isinstance(t, dict)}
    name = username
    if name in existing_names:
        name = f"{username}-{int(time.time())}"

    new_t = dict(template)
    new_t["name"] = name
    new_t["username"] = username
    new_t["password"] = password
    new_t.setdefault("security_code", "")
    new_t["base_url"] = base_url
    new_t["ver"] = ver
    new_t["poll_mode"] = "playwright"
    new_t["relogin_mode"] = "playwright"
    new_t["force_relogin_on_offline"] = True
    new_t["relogin_headless"] = True
    new_t["relogin_login_tab"] = int(login_tab)
    new_t["relogin_timeout_seconds"] = int(new_t.get("relogin_timeout_seconds") or 180)
    new_t["relogin_min_interval_seconds"] = int(new_t.get("relogin_min_interval_seconds") or 10)
    new_t["write_cookie_to_config"] = True
    new_t["uid"] = ""
    new_t["cookie"] = ""
    new_t.setdefault("telegram_chat_id", "")

    cfg2 = dict(cfg)
    cfg2["targets"] = list(targets) + [new_t]
    return cfg2, name


def ensure_arsenal(path: Path) -> Dict[str, Any]:
    if path.exists():
        obj = read_json(path)
        if isinstance(obj, dict):
            obj.setdefault("members", [])
            return obj
    doc = {"members": []}
    atomic_write_json(path, doc)
    return doc


def list_members(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    ms = doc.get("members") or []
    if not isinstance(ms, list):
        return []
    return [m for m in ms if isinstance(m, dict)]


def add_member_to_arsenal(
    *,
    doc: Dict[str, Any],
    base_url: str,
    username: str,
    password: str,
    proxy: str = "",
) -> Tuple[Dict[str, Any], str]:
    base_url = str(base_url or "").strip()
    username = str(username or "").strip()
    password = str(password or "").strip()
    if not (base_url and username and password):
        raise ValueError("缺少 base_url/账号/密码")

    members = list_members(doc)
    member_id = uuid.uuid4().hex
    members.append(
        {
            "id": member_id,
            "label": username,
            "base_url": base_url,
            "username": username,
            "password": password,
            "proxy": proxy,
            "uid": "",
            "mid": "",
            "cookie": "",
            "ver": "",
            "status": "unknown",
            "auto_relogin": True,
            "relogin_headless": True,
            "relogin_login_tab": 3,
            "relogin_timeout_seconds": 120,
            "relogin_min_interval_seconds": 30,
            "last_error": "",
            "last_ok_at": 0,
            "last_check_at": 0,
            "last_relogin_at": 0,
        }
    )
    doc2 = dict(doc)
    doc2["members"] = members
    return doc2, member_id


def delete_member_by_id(doc: Dict[str, Any], member_id: str) -> Tuple[Dict[str, Any], bool]:
    members = list_members(doc)
    before = len(members)
    out = [m for m in members if str(m.get("id") or "") != str(member_id)]
    if len(out) == before:
        return doc, False
    doc2 = dict(doc)
    doc2["members"] = out
    return doc2, True


def arsenal_summary(doc: Dict[str, Any]) -> str:
    members = list_members(doc)
    lines: List[str] = []
    lines.append("<b>🧰 弹药库（会员账号）</b>")
    lines.append(f"数量：{len(members)}")
    if members:
        lines.append("")
        for i, m in enumerate(members, start=1):
            mid = str(m.get("id") or "")
            label = str(m.get("label") or m.get("username") or mid)
            status = str(m.get("status") or "unknown").strip().lower()
            status_zh = {
                "online": "在线",
                "offline": "离线",
                "relogin": "重登中",
                "unknown": "未知",
            }.get(status, status or "未知")

            currency = str(m.get("currency") or "").strip()
            cash = str(m.get("cash") or "").strip()
            maxcredit = str(m.get("maxcredit") or "").strip()

            lines.append(f"{i}. {label}（{status_zh}）")
            # 站点常见：cash 可能一直为 0；maxcredit 才是可用余额/信用额度。
            money = maxcredit or cash or "未知"
            cur = currency or ""
            if cur:
                lines.append(f"   余额：{money} {cur}")
            else:
                lines.append(f"   余额：{money}")
    return "\n".join(lines)


def arsenal_online_balance_summary(doc: Dict[str, Any]) -> str:
    members = list_members(doc)
    online = [m for m in members if str(m.get("status") or "").strip().lower() == "online"]

    lines: List[str] = []
    lines.append("<b>💰 会员余额</b>")
    lines.append(f"在线：{len(online)}/{len(members)}")
    if not members:
        lines.append("")
        lines.append("当前没有会员。")
        return "\n".join(lines)

    if not online:
        lines.append("")
        lines.append("当前无在线会员。")
        return "\n".join(lines)

    def fmt_total_amount(v: float) -> str:
        if abs(v - round(v)) < 1e-9:
            return f"{v:.1f}"
        s = f"{v:.2f}".rstrip("0").rstrip(".")
        return s or str(v)

    def status_zh(m: Dict[str, Any]) -> str:
        s = str(m.get("status") or "unknown").strip().lower()
        return {
            "online": "在线",
            "offline": "离线",
            "relogin": "重登中",
            "unknown": "未知",
        }.get(s, s or "未知")

    def sort_key(m: Dict[str, Any]) -> tuple:
        s = str(m.get("status") or "").strip().lower()
        rank = 0 if s == "online" else (1 if s == "relogin" else 2)
        label = str(m.get("label") or m.get("username") or m.get("id") or "")
        return (rank, label)

    totals: Dict[str, float] = {}
    for m in online:
        currency = str(m.get("currency") or "").strip()
        cash = str(m.get("cash") or "").strip()
        maxcredit = str(m.get("maxcredit") or "").strip()
        nowcredit = str(m.get("nowcredit") or "").strip()
        money = maxcredit or nowcredit or cash or ""
        try:
            v = float(str(money).strip())
        except Exception:
            continue
        totals[currency] = float(totals.get(currency, 0.0)) + v

    if totals:
        if len(totals) == 1:
            cur, v = next(iter(totals.items()))
            cur_s = f" {cur}" if cur else ""
            lines.append(f"总额度：{fmt_total_amount(v)}{cur_s}")
        else:
            parts: List[str] = []
            for cur in sorted(totals.keys()):
                cur_label = cur or "未知币种"
                parts.append(f"{cur_label} {fmt_total_amount(float(totals.get(cur) or 0.0))}")
            lines.append("总额度：" + " / ".join(parts))

    lines.append("")
    for i, m in enumerate(sorted(online, key=sort_key), start=1):
        mid = str(m.get("id") or "")
        label = str(m.get("label") or m.get("username") or mid)
        currency = str(m.get("currency") or "").strip()
        cash = str(m.get("cash") or "").strip()
        maxcredit = str(m.get("maxcredit") or "").strip()
        nowcredit = str(m.get("nowcredit") or "").strip()

        # 站点常见：cash 可能一直为 0；maxcredit/nowcredit 才是可用余额/信用额度。
        money = maxcredit or nowcredit or cash or "未知"
        if currency:
            lines.append(f"{i}. {label}（{status_zh(m)}）：{money} {currency}")
        else:
            lines.append(f"{i}. {label}（{status_zh(m)}）：{money}")
    return "\n".join(lines)


def arsenal_login3_status_summary(doc: Dict[str, Any]) -> str:
    members = list_members(doc)

    def is_login3(m: Dict[str, Any]) -> bool:
        tab = m.get("relogin_login_tab")
        if tab in (None, ""):
            return True
        try:
            return int(tab) == 3
        except Exception:
            return str(tab).strip() == "3"

    nodes = [m for m in members if is_login3(m)]
    online = [m for m in nodes if str(m.get("status") or "").strip().lower() == "online"]

    lines: List[str] = []
    lines.append("<b>🟢 登入3节点状态</b>")
    lines.append(f"在线：{len(online)}/{len(nodes)}")
    if not nodes:
        lines.append("")
        lines.append("当前没有登入3节点。")
        return "\n".join(lines)

    def status_zh(m: Dict[str, Any]) -> str:
        s = str(m.get("status") or "unknown").strip().lower()
        return {
            "online": "在线",
            "offline": "离线",
            "relogin": "重登中",
            "unknown": "未知",
        }.get(s, s or "未知")

    def sort_key(m: Dict[str, Any]) -> tuple:
        s = str(m.get("status") or "").strip().lower()
        rank = 0 if s == "online" else (1 if s == "relogin" else (2 if s == "offline" else 3))
        label = str(m.get("label") or m.get("username") or m.get("id") or "")
        return (rank, label)

    lines.append("")
    for i, m in enumerate(sorted(nodes, key=sort_key), start=1):
        mid = str(m.get("id") or "")
        label = str(m.get("label") or m.get("username") or mid or f"node-{i}")
        lines.append(f"{i}. {label}：{status_zh(m)}")
    return "\n".join(lines)


def _resolve_target_runtime_status(st: Dict[str, Any], *, now_ts: float, ttl_s: float) -> str:
    last_ok_at = float(st.get("last_ok_at") or 0)
    last_poll_at = float(st.get("last_poll_at") or 0)
    last_status = str(st.get("last_status") or "").strip().lower()
    last_error_code = str(st.get("last_error_code") or "").strip()
    relogin_in_progress = bool(st.get("relogin_in_progress") or False)
    relogin_started_at = float(st.get("relogin_started_at") or 0)

    # 与 WebUI /api/status 保持一致：最近拉单成功即在线
    if last_ok_at and (now_ts - last_ok_at) <= ttl_s:
        return "online"
    if relogin_in_progress or last_status == "relogin":
        if relogin_started_at and (now_ts - relogin_started_at) < 600:
            return "relogin"
        return "offline"
    if last_status == "offline" or last_error_code in {"4X014", "4X013", "logout", "login_required"}:
        return "offline"
    if last_poll_at and (now_ts - last_poll_at) <= ttl_s:
        return "offline"
    return "offline"


def _target_status_zh(status: str) -> str:
    s = str(status or "").strip().lower()
    return {
        "online": "在线",
        "offline": "离线",
        "relogin": "重登中",
        "unknown": "未知",
    }.get(s, s or "未知")


def login3_target_status_summary(
    *,
    config_path: Path,
    state_dir: Path,
    fallback_doc: Optional[Dict[str, Any]] = None,
) -> str:
    cfg = read_json(config_path)
    targets = cfg.get("targets") if isinstance(cfg, dict) else []
    if not isinstance(targets, list):
        targets = []

    def _is_login3_target(t: Dict[str, Any]) -> bool:
        tab = t.get("relogin_login_tab")
        if tab in (None, ""):
            return False
        try:
            return int(tab) == 3
        except Exception:
            return str(tab).strip() == "3"

    # 优先只看登入3；若没有配置 tab=3，则回退到全部节点（兼容老配置）。
    all_targets: List[Dict[str, Any]] = [t for t in targets if isinstance(t, dict) and str(t.get("name") or "").strip()]
    login3_targets = [t for t in all_targets if _is_login3_target(t)]
    selected = login3_targets if login3_targets else all_targets

    if not selected:
        if fallback_doc is not None:
            return arsenal_login3_status_summary(fallback_doc)
        return "<b>🟢 登入3节点状态</b>\n在线：0/0\n\n当前没有登入3节点。"

    now_ts = time.time()
    ttl_s = float(TARGET_ONLINE_TTL_S)
    rows: List[Dict[str, Any]] = []
    for t in selected:
        name = str(t.get("name") or "").strip()
        if not name:
            continue
        st = read_json(state_dir / f"{safe_target_name(name)}.json")
        status = _resolve_target_runtime_status(st if isinstance(st, dict) else {}, now_ts=now_ts, ttl_s=ttl_s)
        rows.append({"name": name, "status": status})

    if not rows:
        if fallback_doc is not None:
            return arsenal_login3_status_summary(fallback_doc)
        return "<b>🟢 登入3节点状态</b>\n在线：0/0\n\n当前没有登入3节点。"

    def sort_key(x: Dict[str, Any]) -> tuple:
        s = str(x.get("status") or "").strip().lower()
        rank = 0 if s == "online" else (1 if s == "relogin" else (2 if s == "offline" else 3))
        return (rank, str(x.get("name") or ""))

    online_count = sum(1 for x in rows if str(x.get("status") or "").strip().lower() == "online")
    lines = ["<b>🟢 登入3节点状态</b>", f"在线：{online_count}/{len(rows)}", ""]
    for i, row in enumerate(sorted(rows, key=sort_key), start=1):
        lines.append(f"{i}. {row['name']}：{_target_status_zh(str(row.get('status') or ''))}")
    return "\n".join(lines)


def help_text() -> str:
    return (
        "<b>可用功能</b>\n"
        f"• {BTN_QUERY_BALANCE}：查询在线会员余额\n"
        f"• {BTN_NODE_STATUS}：查询登入3节点在线状态\n"
        f"• {BTN_TODAY_REPORT}：查询今日跟单报表\n"
        f"• {BTN_PNL_REPORT}：查询今日 + 本周输赢\n"
        f"• {BTN_WINRATE}：查询历史投资回报率\n"
        "• /oid &lt;后台单号&gt;：按后台单号查询详情（别名 /order）\n"
        "• /menu：返回主菜单\n"
        "\n"
        "<b>管理员功能</b>\n"
        f"• {BTN_ADMIN_AUTH}：进入授权菜单\n"
        "• /grant &lt;chat_id|user_id|@username&gt;：授权查询\n"
        "• /revoke &lt;chat_id|user_id|@username&gt;：取消授权\n"
        "• /authlist：查看授权列表"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Telegram Bot (commands)")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--orders-dir", default="orders")
    parser.add_argument("--watcher-state-dir", default="state")
    parser.add_argument("--state", default=".run/tg_bot_state.json")
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument("--timeout", type=int, default=20)
    args = parser.parse_args()

    config_path = Path(args.config)
    data_dir = Path(args.data_dir)
    orders_dir = Path(args.orders_dir)
    watcher_state_dir = Path(args.watcher_state_dir)
    state_path = Path(args.state)
    auth_path = data_dir / "tg_auth.json"

    state = read_json(state_path) if state_path.exists() else {}
    offset = int(state.get("offset") or 0)
    pending: Dict[str, Dict[str, Any]] = state.get("pending") if isinstance(state.get("pending"), dict) else {}
    menus: Dict[str, str] = state.get("menus") if isinstance(state.get("menus"), dict) else {}
    bot_token_sha256 = str(state.get("bot_token_sha256") or "").strip()

    print(f"[{now_iso()}] telegram_bot 启动 state={state_path} data_dir={data_dir} orders_dir={orders_dir}", flush=True)

    while True:
        try:
            bot_token, allow_chat_id, proxy = load_tg_config(config_path)
            if not bot_token:
                time.sleep(2.0)
                continue

            current_token_sha256 = _bot_token_sha256(bot_token)
            if current_token_sha256:
                if not bot_token_sha256:
                    # 兼容旧版 state：未记录 token 指纹时，offset 可能来自旧机器人导致永远收不到新机器人的 update。
                    # 安全策略：重置 offset=0（不会“恢复”已确认的旧消息），并清空对话状态。
                    if offset:
                        print(f"[{now_iso()}] 检测到旧 tg_bot_state（无 bot_token_sha256），已重置 offset=0 以兼容换机器人", flush=True)
                    offset = 0
                    pending.clear()
                    menus.clear()
                    bot_token_sha256 = current_token_sha256
                elif bot_token_sha256 != current_token_sha256:
                    print(f"[{now_iso()}] 检测到 bot_token 变更，已重置 offset=0 并清空会话状态", flush=True)
                    offset = 0
                    pending.clear()
                    menus.clear()
                    bot_token_sha256 = current_token_sha256

            allow_ids, allow_usernames = parse_allow_chat_spec(allow_chat_id)
            updates = tg_get_updates(bot_token=bot_token, offset=offset, timeout_s=int(args.timeout), proxy=proxy)
            if not updates:
                time.sleep(max(0.5, float(args.poll_interval)))
                continue

            auth_doc = ensure_tg_auth(auth_path)
            auth_dirty = False

            for u in updates:
                try:
                    uid = int(u.get("update_id") or 0)
                except Exception:
                    uid = 0
                if uid and uid >= offset:
                    offset = uid + 1

                cb = u.get("callback_query") or {}
                if isinstance(cb, dict) and cb:
                    cb_id = str(cb.get("id") or "").strip()
                    cb_data = str(cb.get("data") or "").strip()
                    cb_msg = cb.get("message") or {}
                    if not isinstance(cb_msg, dict):
                        continue
                    chat = cb_msg.get("chat") or {}
                    chat_id = str(chat.get("id") or "").strip()
                    if not chat_id:
                        continue
                    frm = cb.get("from") or {}
                    user_id = str(frm.get("id") or "").strip()
                    from_username = _normalize_tg_username(frm.get("username") or "")
                    chat_username = _normalize_tg_username(chat.get("username") or "")
                    usernames = [x for x in [from_username, chat_username] if x]

                    parsed_order_cb = parse_order_callback_data(cb_data)
                    parsed_report_cb = parse_report_callback_data(cb_data)
                    if (parsed_order_cb is None) and (parsed_report_cb is None):
                        tg_log(
                            "callback_query_invalid "
                            f"raw={cb_data[:80]} chat_id={chat_id} user_id={user_id or '-'}"
                        )
                        safe_tg_answer_callback_query(
                            bot_token=bot_token,
                            callback_query_id=cb_id,
                            text="按钮已失效，请等待新消息。",
                            show_alert=False,
                            proxy=proxy,
                            timeout_s=8,
                            context=f"invalid_cb_data:{cb_data[:48]}",
                        )
                        continue

                    action = ""
                    oid = ""
                    if parsed_order_cb is not None:
                        action, oid = parsed_order_cb
                    else:
                        action = str(parsed_report_cb or "")
                        oid = "-"
                    tg_log(
                        "callback_query "
                        f"action={action} order_id={oid} "
                        f"chat_id={chat_id} user_id={user_id or '-'}"
                    )

                    # 与文本命令一致：若配置了允许会话，先做 allow 命中判断，再回退到授权文档。
                    if allow_chat_id:
                        allow_hit = bool((chat_id and chat_id in allow_ids) or any(x in allow_usernames for x in usernames))
                        if not allow_hit:
                            in_admin_doc = _auth_has_subject(
                                auth_doc,
                                "admins",
                                chat_id=chat_id,
                                user_id=user_id,
                                usernames=usernames,
                            )
                            in_grant_doc = _auth_has_subject(
                                auth_doc,
                                "grants",
                                chat_id=chat_id,
                                user_id=user_id,
                                usernames=usernames,
                            )
                            if not in_admin_doc and not in_grant_doc:
                                safe_tg_answer_callback_query(
                                    bot_token=bot_token,
                                    callback_query_id=cb_id,
                                    text="未在允许会话内。",
                                    show_alert=True,
                                    proxy=proxy,
                                    timeout_s=8,
                                    context=f"{action}:{oid}:allow_chat",
                                )
                                continue

                    admin_sec = auth_doc.get("admins") or {}
                    admin_ids = [str(x).strip() for x in (admin_sec.get("ids") if isinstance(admin_sec, dict) else []) if str(x).strip()]
                    admin_users = [_normalize_tg_username(x) for x in (admin_sec.get("usernames") if isinstance(admin_sec, dict) else []) if _normalize_tg_username(x)]
                    if not admin_ids and not admin_users and not allow_ids and not allow_usernames:
                        seed = user_id or chat_id
                        auth_doc, changed0, _ = _auth_add_subject(auth_doc, "admins", seed)
                        auth_dirty = auth_dirty or changed0

                    authorized = is_authorized_user(
                        auth_doc=auth_doc,
                        chat_id=chat_id,
                        user_id=user_id,
                        usernames=usernames,
                        allow_ids=allow_ids,
                        allow_usernames=allow_usernames,
                    )
                    if not authorized:
                        safe_tg_answer_callback_query(
                            bot_token=bot_token,
                            callback_query_id=cb_id,
                            text="❌ 当前未授权访问报表。",
                            show_alert=True,
                            proxy=proxy,
                            timeout_s=8,
                            context=f"{action}:{oid}:unauthorized",
                        )
                        continue

                    if action in {"yday", "today"}:
                        try:
                            day_shift = -1 if action == "yday" else 0
                            send_today_follow_report(
                                bot_token=bot_token,
                                chat_id=chat_id,
                                data_dir=data_dir,
                                orders_dir=orders_dir,
                                only_ok=True,
                                only_live=True,
                                day_shift=day_shift,
                                proxy=proxy,
                                timeout_s=15,
                            )
                            safe_tg_answer_callback_query(
                                bot_token=bot_token,
                                callback_query_id=cb_id,
                                text=("已发送昨日跟单数据" if day_shift == -1 else "已发送今日跟单数据"),
                                show_alert=False,
                                proxy=proxy,
                                timeout_s=8,
                                context=f"{action}:send_report",
                            )
                        except Exception as e:
                            tg_log(f"callback_report_failed action={action} err={e}")
                            safe_tg_answer_callback_query(
                                bot_token=bot_token,
                                callback_query_id=cb_id,
                                text="查询失败，请稍后重试。",
                                show_alert=True,
                                proxy=proxy,
                                timeout_s=8,
                                context=f"{action}:exception",
                            )
                        continue

                    if action == "res":
                        try:
                            alert_text = build_order_follow_alert(
                                data_dir=data_dir,
                                order_id=oid,
                                orders_dir=orders_dir,
                                max_rows=3,
                                max_chars=180,
                            )
                            if not alert_text:
                                alert_text = "暂无跟单结果。"
                            shown = safe_tg_answer_callback_query(
                                bot_token=bot_token,
                                callback_query_id=cb_id,
                                text=alert_text,
                                show_alert=True,
                                proxy=proxy,
                                timeout_s=8,
                                context=f"{action}:{oid}:popup",
                            )
                            if not shown:
                                fallback_text = alert_text.replace("\n", " | ").strip()
                                if len(fallback_text) > 120:
                                    fallback_text = fallback_text[:119] + "…"
                                safe_tg_answer_callback_query(
                                    bot_token=bot_token,
                                    callback_query_id=cb_id,
                                    text=fallback_text or "结果已生成，请稍后重试。",
                                    show_alert=False,
                                    proxy=proxy,
                                    timeout_s=8,
                                    context=f"{action}:{oid}:popup_fallback",
                                )
                        except Exception as e:
                            tg_log(f"callback_report_failed action={action} order_id={oid} err={e}")
                            safe_tg_answer_callback_query(
                                bot_token=bot_token,
                                callback_query_id=cb_id,
                                text="查询失败，请稍后重试。",
                                show_alert=True,
                                proxy=proxy,
                                timeout_s=8,
                                context=f"{action}:{oid}:exception",
                            )
                        continue

                    if action == "pnl":
                        try:
                            pnl_alert = build_order_pnl_alert(
                                data_dir=data_dir,
                                orders_dir=orders_dir,
                                order_id=oid,
                                max_chars=180,
                            )
                            shown = safe_tg_answer_callback_query(
                                bot_token=bot_token,
                                callback_query_id=cb_id,
                                text=pnl_alert,
                                show_alert=True,
                                proxy=proxy,
                                timeout_s=8,
                                context=f"{action}:{oid}:popup",
                            )
                            if not shown:
                                fallback_text = pnl_alert.replace("\n", " | ").strip()
                                if len(fallback_text) > 120:
                                    fallback_text = fallback_text[:119] + "…"
                                safe_tg_answer_callback_query(
                                    bot_token=bot_token,
                                    callback_query_id=cb_id,
                                    text=fallback_text or "结果已生成，请稍后重试。",
                                    show_alert=False,
                                    proxy=proxy,
                                    timeout_s=8,
                                    context=f"{action}:{oid}:popup_fallback",
                                )
                        except Exception as e:
                            tg_log(f"callback_report_failed action={action} order_id={oid} err={e}")
                            safe_tg_answer_callback_query(
                                bot_token=bot_token,
                                callback_query_id=cb_id,
                                text="查询失败，请稍后重试。",
                                show_alert=True,
                                proxy=proxy,
                                timeout_s=8,
                                context=f"{action}:{oid}:exception",
                            )
                        continue

                    if action == "log":
                        try:
                            log_alert = build_order_log_alert(
                                data_dir=data_dir,
                                order_id=oid,
                                max_rows=3,
                                max_chars=180,
                            )
                            shown = safe_tg_answer_callback_query(
                                bot_token=bot_token,
                                callback_query_id=cb_id,
                                text=log_alert,
                                show_alert=True,
                                proxy=proxy,
                                timeout_s=8,
                                context=f"{action}:{oid}:popup",
                            )
                            if not shown:
                                fallback_text = log_alert.replace("\n", " | ").strip()
                                if len(fallback_text) > 120:
                                    fallback_text = fallback_text[:119] + "…"
                                safe_tg_answer_callback_query(
                                    bot_token=bot_token,
                                    callback_query_id=cb_id,
                                    text=fallback_text or "结果已生成，请稍后重试。",
                                    show_alert=False,
                                    proxy=proxy,
                                    timeout_s=8,
                                    context=f"{action}:{oid}:popup_fallback",
                                )
                        except Exception as e:
                            tg_log(f"callback_report_failed action={action} order_id={oid} err={e}")
                            safe_tg_answer_callback_query(
                                bot_token=bot_token,
                                callback_query_id=cb_id,
                                text="查询失败，请稍后重试。",
                                show_alert=True,
                                proxy=proxy,
                                timeout_s=8,
                                context=f"{action}:{oid}:exception",
                            )
                        continue

                    if action == "amt":
                        try:
                            summary_alert = build_follow_summary_alert(
                                data_dir=data_dir,
                                max_chars=200,
                            )
                            shown = safe_tg_answer_callback_query(
                                bot_token=bot_token,
                                callback_query_id=cb_id,
                                text=summary_alert,
                                show_alert=True,
                                proxy=proxy,
                                timeout_s=8,
                                context=f"{action}:{oid}:popup",
                            )
                            if not shown:
                                fallback_text = summary_alert.replace("\n", " | ").strip()
                                if len(fallback_text) > 120:
                                    fallback_text = fallback_text[:119] + "…"
                                safe_tg_answer_callback_query(
                                    bot_token=bot_token,
                                    callback_query_id=cb_id,
                                    text=fallback_text or "结果已生成，请稍后重试。",
                                    show_alert=False,
                                    proxy=proxy,
                                    timeout_s=8,
                                    context=f"{action}:{oid}:popup_fallback",
                                )
                        except Exception as e:
                            tg_log(f"callback_report_failed action={action} order_id={oid} err={e}")
                            safe_tg_answer_callback_query(
                                bot_token=bot_token,
                                callback_query_id=cb_id,
                                text="查询失败，请稍后重试。",
                                show_alert=True,
                                proxy=proxy,
                                timeout_s=8,
                                context=f"{action}:{oid}:exception",
                            )
                        continue

                    safe_tg_answer_callback_query(
                        bot_token=bot_token,
                        callback_query_id=cb_id,
                        text="按钮暂不支持。",
                        show_alert=True,
                        proxy=proxy,
                        timeout_s=8,
                        context=f"{action}:{oid}:unsupported",
                    )
                    continue

                msg = (
                    u.get("message")
                    or u.get("edited_message")
                    or u.get("channel_post")
                    or u.get("edited_channel_post")
                    or {}
                )
                if not isinstance(msg, dict):
                    continue
                text = msg.get("text") or ""
                chat = msg.get("chat") or {}
                chat_id = str(chat.get("id") or "").strip()
                if not chat_id:
                    continue
                frm = msg.get("from") or {}
                user_id = str(frm.get("id") or "").strip()
                from_username = _normalize_tg_username(frm.get("username") or "")
                chat_username = _normalize_tg_username(chat.get("username") or "")
                usernames = [x for x in [from_username, chat_username] if x]
                pending_key = f"{chat_id}:{user_id}" if user_id else chat_id
                menu = str(menus.get(pending_key) or MENU_MAIN).strip() or MENU_MAIN

                def set_menu(m: str) -> None:
                    nonlocal menu
                    menu = str(m or MENU_MAIN).strip() or MENU_MAIN
                    menus[pending_key] = menu

                def menu_keyboard(m: str) -> Dict[str, Any]:
                    m = str(m or MENU_MAIN)
                    if m == MENU_ADMIN:
                        return admin_keyboard()
                    return main_keyboard()

                def send_one(html_text: str, *, reply_markup: Optional[Dict[str, Any]] = None, timeout_s: int = 10) -> None:
                    tg_send_message(
                        bot_token=bot_token,
                        chat_id=chat_id,
                        html_text=html_text,
                        reply_markup=(reply_markup if reply_markup is not None else menu_keyboard(menu)),
                        proxy=proxy,
                        timeout_s=timeout_s,
                    )

                def send_text(html_text: str, *, reply_markup: Optional[Dict[str, Any]] = None, timeout_s: int = 10) -> None:
                    for part in split_message(html_text):
                        send_one(part, reply_markup=reply_markup, timeout_s=timeout_s)

                # 限制：如果 config.json 配了 telegram_chat_id，则只响应该 chat
                # 兼容：允许配置为数字 chat_id（-100...）或 @username（频道/群用户名），也支持多值逗号分隔。
                if allow_chat_id:
                    allow_hit = bool((chat_id and chat_id in allow_ids) or any(x in allow_usernames for x in usernames))
                    if not allow_hit:
                        in_admin_doc = _auth_has_subject(
                            auth_doc,
                            "admins",
                            chat_id=chat_id,
                            user_id=user_id,
                            usernames=usernames,
                        )
                        in_grant_doc = _auth_has_subject(
                            auth_doc,
                            "grants",
                            chat_id=chat_id,
                            user_id=user_id,
                            usernames=usernames,
                        )
                        if not in_admin_doc and not in_grant_doc:
                            continue

                admin_sec = auth_doc.get("admins") or {}
                admin_ids = [str(x).strip() for x in (admin_sec.get("ids") if isinstance(admin_sec, dict) else []) if str(x).strip()]
                admin_users = [_normalize_tg_username(x) for x in (admin_sec.get("usernames") if isinstance(admin_sec, dict) else []) if _normalize_tg_username(x)]
                if not admin_ids and not admin_users and not allow_ids and not allow_usernames:
                    seed = user_id or chat_id
                    auth_doc, changed0, _ = _auth_add_subject(auth_doc, "admins", seed)
                    auth_dirty = auth_dirty or changed0

                is_admin = is_admin_user(
                    auth_doc=auth_doc,
                    chat_id=chat_id,
                    user_id=user_id,
                    usernames=usernames,
                    allow_ids=allow_ids,
                    allow_usernames=allow_usernames,
                )

                raw_text = str(text or "").strip()
                grant_arg = parse_admin_grant_command(raw_text)
                revoke_arg = parse_admin_revoke_command(raw_text)
                oid_query = parse_order_query(raw_text)

                if pending_key in pending:
                    action = str((pending.get(pending_key) or {}).get("action") or "").strip()
                    if raw_text.lower().startswith("/cancel"):
                        pending.pop(pending_key, None)
                        send_text("已取消当前操作。")
                        continue
                    if action in {"auth_grant_input", "auth_revoke_input"}:
                        if not is_admin:
                            pending.pop(pending_key, None)
                            send_text("❌ 仅管理员可执行授权管理。", reply_markup=main_keyboard())
                            continue
                        if action == "auth_grant_input":
                            auth_doc, changed0, msg0 = _auth_add_subject(auth_doc, "grants", raw_text)
                            auth_dirty = auth_dirty or changed0
                            pending.pop(pending_key, None)
                            send_text(f"{msg0}：{raw_text}", reply_markup=admin_keyboard())
                        else:
                            auth_doc, changed0, msg0 = _auth_remove_subject(auth_doc, "grants", raw_text)
                            auth_dirty = auth_dirty or changed0
                            pending.pop(pending_key, None)
                            send_text(f"{msg0}：{raw_text}", reply_markup=admin_keyboard())
                        continue
                    pending.pop(pending_key, None)

                if raw_text == BTN_BACK:
                    pending.pop(pending_key, None)
                    set_menu(MENU_MAIN)
                    send_text("已返回主菜单。", reply_markup=main_keyboard())
                    continue

                if is_main_menu(raw_text):
                    pending.pop(pending_key, None)
                    set_menu(MENU_MAIN)
                    send_text(help_text(), reply_markup=main_keyboard())
                    continue

                if is_deprecated_command(raw_text):
                    send_text(
                        "该功能已下线。\n请使用新菜单：查询余额 / 今日跟单 / 输赢报表 / 投资回报率。",
                        reply_markup=main_keyboard(),
                    )
                    continue

                if is_admin_auth_command(raw_text):
                    if not is_admin:
                        send_text("❌ 仅管理员可进入授权菜单。", reply_markup=main_keyboard())
                        continue
                    set_menu(MENU_ADMIN)
                    send_text(
                        "<b>管理员授权</b>\n"
                        "可用操作：授权用户 / 取消授权 / 查看授权。\n"
                        "也支持命令：/grant /revoke /authlist",
                        reply_markup=admin_keyboard(),
                    )
                    continue

                if raw_text == BTN_ADMIN_LIST or is_admin_list_command(raw_text):
                    if not is_admin:
                        send_text("❌ 仅管理员可查看授权列表。")
                        continue
                    set_menu(MENU_ADMIN)
                    send_text(auth_summary_text(auth_doc), reply_markup=admin_keyboard(), timeout_s=15)
                    continue

                if raw_text == BTN_ADMIN_GRANT or grant_arg is not None:
                    if not is_admin:
                        send_text("❌ 仅管理员可授权用户。")
                        continue
                    set_menu(MENU_ADMIN)
                    target = raw_text if grant_arg is None else grant_arg
                    if grant_arg is None or not str(target).strip():
                        pending[pending_key] = {"action": "auth_grant_input", "ts": int(time.time())}
                        send_text(
                            "请输入待授权对象（chat_id / user_id / @username）。\n"
                            "示例：123456789 或 @alice",
                            reply_markup=admin_keyboard(),
                        )
                        continue
                    auth_doc, changed0, msg0 = _auth_add_subject(auth_doc, "grants", target)
                    auth_dirty = auth_dirty or changed0
                    send_text(f"{msg0}：{target}", reply_markup=admin_keyboard())
                    continue

                if raw_text == BTN_ADMIN_REVOKE or revoke_arg is not None:
                    if not is_admin:
                        send_text("❌ 仅管理员可取消授权。")
                        continue
                    set_menu(MENU_ADMIN)
                    target = raw_text if revoke_arg is None else revoke_arg
                    if revoke_arg is None or not str(target).strip():
                        pending[pending_key] = {"action": "auth_revoke_input", "ts": int(time.time())}
                        send_text(
                            "请输入要取消授权的对象（chat_id / user_id / @username）。",
                            reply_markup=admin_keyboard(),
                        )
                        continue
                    auth_doc, changed0, msg0 = _auth_remove_subject(auth_doc, "grants", target)
                    auth_dirty = auth_dirty or changed0
                    send_text(f"{msg0}：{target}", reply_markup=admin_keyboard())
                    continue

                authorized = is_authorized_user(
                    auth_doc=auth_doc,
                    chat_id=chat_id,
                    user_id=user_id,
                    usernames=usernames,
                    allow_ids=allow_ids,
                    allow_usernames=allow_usernames,
                )
                needs_auth = (
                    is_balance_command(raw_text)
                    or is_node_status_command(raw_text)
                    or is_report_command(raw_text)
                    or is_pnl_report_command(raw_text)
                    or is_winrate_command(raw_text)
                    or oid_query is not None
                )
                if needs_auth and not authorized:
                    send_text(
                        "❌ 当前未授权访问报表。\n请联系管理员在机器人中使用“管理员授权”给你开通。",
                        reply_markup=main_keyboard(),
                    )
                    continue

                if is_balance_command(raw_text):
                    arsenal_path = data_dir / "arsenal.json"
                    doc = ensure_arsenal(arsenal_path)
                    send_text(arsenal_online_balance_summary(doc), timeout_s=10)
                    continue

                if is_node_status_command(raw_text):
                    arsenal_path = data_dir / "arsenal.json"
                    doc = ensure_arsenal(arsenal_path)
                    send_text(
                        login3_target_status_summary(
                            config_path=config_path,
                            state_dir=watcher_state_dir,
                            fallback_doc=doc,
                        ),
                        timeout_s=10,
                    )
                    continue

                if is_report_command(raw_text):
                    send_today_follow_report(
                        bot_token=bot_token,
                        chat_id=chat_id,
                        data_dir=data_dir,
                        orders_dir=orders_dir,
                        only_ok=True,
                        only_live=True,
                        day_shift=0,
                        proxy=proxy,
                        timeout_s=15,
                        text_reply_markup=menu_keyboard(menu),
                    )
                    continue

                if is_pnl_report_command(raw_text):
                    report_msg = build_remote_member_pnl_report_message(
                        data_dir=data_dir,
                        enable_effect=(str(chat.get("type") or "").strip().lower() == "private"),
                    )
                    tg_send_rich_text(
                        bot_token=bot_token,
                        chat_id=chat_id,
                        text=str(report_msg.get("text") or ""),
                        entities=(report_msg.get("entities") if isinstance(report_msg.get("entities"), list) else None),
                        reply_markup=menu_keyboard(menu),
                        proxy=proxy,
                        timeout_s=15,
                        message_effect_id=str(report_msg.get("message_effect_id") or ""),
                    )
                    continue

                if is_winrate_command(raw_text):
                    report = build_project_winrate_report(data_dir=data_dir, orders_dir=orders_dir, amount_threshold=2000.0)
                    send_text(report, timeout_s=15)
                    continue

                if oid_query is not None:
                    if not oid_query:
                        send_text("用法：/oid &lt;后台单号&gt;（或 /order &lt;后台单号&gt;）")
                        continue
                    report = build_order_follow_report(data_dir=data_dir, orders_dir=orders_dir, order_id=oid_query)
                    send_text(report, timeout_s=15)
                    continue

            if auth_dirty:
                atomic_write_json(auth_path, auth_doc)

            atomic_write_json(
                state_path,
                {
                    "offset": offset,
                    "pending": pending,
                    "menus": menus,
                    "bot_token_sha256": bot_token_sha256,
                    "updated_at": now_iso(),
                },
            )
        except Exception as e:
            # 不中断服务，避免网络波动导致退出
            print(f"[{now_iso()}] telegram_bot error: {e}", flush=True)
            time.sleep(2.0)


if __name__ == "__main__":
    raise SystemExit(main())
