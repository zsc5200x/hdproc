import argparse
import hashlib
import json
import math
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
import uuid

import requests


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")

MENU_MAIN = "main"
MENU_FOLLOW = "follow"
MENU_BACKEND = "backend"
MENU_ARSENAL = "arsenal"

BTN_BACK = "返回主菜单"


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
    #   若未配置 telegram_allow_chat，则回退使用 telegram_chat_id 作为白名单配置。
    chat_id = str(cfg.get("telegram_allow_chat") or cfg.get("telegram_chat_id") or "").strip()
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
    params = {"timeout": max(1, int(timeout_s)), "offset": int(offset)}
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
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"telegram error: {data}")


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
    return datetime.now().date()


def is_today_ts(ts: Any) -> bool:
    try:
        ts_i = int(float(ts))
        return datetime.fromtimestamp(ts_i).date() == today_date()
    except Exception:
        return False


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

    rows: List[Dict[str, Any]] = []
    for r in results_all:
        if only_live and not bool(r.get("live")):
            continue
        if only_ok and not bool(r.get("ok")):
            continue
        if not is_today_ts(r.get("ts")):
            continue
        oid = str(r.get("orderId") or "").strip()
        if not oid:
            continue
        rows.append(r)

    rows.sort(key=lambda x: int(float(x.get("ts") or 0)))
    if not rows:
        return f"<b>📊 今日跟单报表</b>\n⏰ 日期：{today_date().strftime('%Y-%m-%d')}\n\n暂无跟单记录。"

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
    lines.append("<b>📊 今日跟单报表</b>")
    lines.append(f"⏰ 日期：{today_date().strftime('%Y-%m-%d')}")
    lines.append(f"✅ 成功：{len(group_items)} 单")
    lines.append(f"💰 合计：{total_stake:g}")
    lines.append("")

    # 在不改动既有标题区文案的前提下，把输赢汇总插入到标题区（需要先从浏览器导入 settlements）。
    try:
        blank_i = lines.index("")
    except ValueError:
        blank_i = len(lines)

    if settlements_idx:
        lines.insert(blank_i, f"今日盈亏：{total_profit:+g}")
        if total_settled_tickets or total_pending_tickets or total_unknown_tickets:
            lines.insert(
                blank_i + 1,
                f"已结算：{total_settled_tickets} 单 / 未结算：{total_pending_tickets} 单 / 未匹配：{total_unknown_tickets} 单",
            )
    else:
        lines.insert(blank_i, "今日盈亏：--（未导入结算数据）")

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

    results_path = data_dir / "follow_results.jsonl"
    results_all = list(iter_jsonl(results_path))

    rows: List[Dict[str, Any]] = []
    for r in results_all:
        if _digits_only(r.get("orderId") or "") != _digits_only(oid):
            continue
        rows.append(r)

    if not rows:
        return (
            "<b>后台单查询</b>\n"
            f"后台单号：{oid}\n\n"
            "未找到该后台单的跟单记录。"
        )

    ok_rows = [r for r in rows if bool(r.get("ok"))]
    ok_rows.sort(key=lambda x: int(float(x.get("ts") or 0)))

    orders_idx = load_orders_index(orders_dir, [oid])
    o = orders_idx.get(oid) or {}
    league = str(o.get("league") or "未知")
    sides = str(o.get("sides") or "未知")
    line = str(o.get("line") or "未知")
    odds = str(o.get("odds") or "未知")

    total_stake = 0.0
    for r in ok_rows:
        try:
            total_stake += float(r.get("stake") or 0)
        except Exception:
            pass

    settlements_idx = _load_ticket_settlements(data_dir)

    lines: List[str] = []
    lines.append("<b>后台单查询</b>")
    lines.append(f"后台单号：{oid}")
    if o:
        lines.append(f"联赛：{league}")
        lines.append(f"比赛：{sides}")
        lines.append(f"玩法：{line} @{odds}")
    lines.append(f"跟单成功：{len(ok_rows)} 笔")
    lines.append(f"跟单金额：{fmt_amount(total_stake)}")

    if not ok_rows:
        lines.append("")
        lines.append("该后台单暂无跟单成功记录。")
        return "\n".join(lines).strip()

    if not settlements_idx:
        lines.append("本场盈亏：--（未导入结算数据）")
    else:
        settled_profit_sum = 0.0
        settled_profit_known = True
        settled_count = 0
        pending_count = 0
        unknown_count = 0
        for r in ok_rows:
            tid = _digits_only(r.get("ticket_id") or "")
            if not tid:
                unknown_count += 1
                continue
            srec = settlements_idx.get(tid)
            if not srec:
                unknown_count += 1
                continue
            status = str(srec.get("status") or "").strip().lower()
            if status in {"pending", "unsettled", "running"}:
                pending_count += 1
                continue
            settled_count += 1
            if srec.get("profit") is None:
                settled_profit_known = False
                continue
            try:
                settled_profit_sum += float(srec.get("profit"))
            except Exception:
                settled_profit_known = False

        if settled_count and settled_profit_known:
            lines.append(f"本场盈亏：{settled_profit_sum:+g}")
        elif settled_count:
            lines.append("本场盈亏：已结算（盈亏未知）")
        else:
            lines.append("本场盈亏：比赛进行中（未结算）")

        lines.append(
            " / ".join(
                [
                    f"已结算：{settled_count} 笔",
                    f"未结算：{pending_count} 笔",
                    f"未匹配：{unknown_count} 笔",
                ]
            )
        )

    lines.append("")
    lines.append("<b>会员汇总</b>")
    per_member: Dict[str, Dict[str, Any]] = {}
    for r in ok_rows:
        member = str(r.get("member") or "").strip() or str(r.get("member_id") or "").strip() or "未知会员"
        rec = per_member.setdefault(
            member,
            {
                "count": 0,
                "stake": 0.0,
                "profit": 0.0,
                "has_pending": False,
                "has_unknown": False,
                "has_profit_unknown": False,
            },
        )
        rec["count"] += 1
        try:
            rec["stake"] += float(r.get("stake") or 0)
        except Exception:
            pass

        if not settlements_idx:
            continue
        tid = _digits_only(r.get("ticket_id") or "")
        if not tid:
            rec["has_unknown"] = True
            continue
        srec = settlements_idx.get(tid)
        if not srec:
            rec["has_unknown"] = True
            continue
        status = str(srec.get("status") or "").strip().lower()
        if status in {"pending", "unsettled", "running"}:
            rec["has_pending"] = True
            continue
        if srec.get("profit") is None:
            rec["has_profit_unknown"] = True
            continue
        try:
            rec["profit"] += float(srec.get("profit"))
        except Exception:
            rec["has_profit_unknown"] = True

    items = list(per_member.items())
    items.sort(key=lambda kv: float(kv[1].get("stake") or 0), reverse=True)
    for m, rec in items:
        if not settlements_idx:
            profit_str = "盈亏：--"
        else:
            if bool(rec.get("has_pending")):
                    profit_str = "盈亏：比赛进行中（未结算）"
            elif bool(rec.get("has_unknown")):
                profit_str = "盈亏：未匹配"
            elif bool(rec.get("has_profit_unknown")):
                profit_str = "盈亏：已结算（盈亏未知）"
            else:
                profit_str = f"盈亏：{float(rec.get('profit') or 0.0):+g}"
        lines.append(f"- {m}：{int(rec.get('count') or 0)} 笔 / 金额：{fmt_amount(rec.get('stake') or 0)} / {profit_str}")

    return "\n".join(lines).strip()


def build_project_winrate_report(
    *,
    data_dir: Path,
    orders_dir: Path,
    amount_threshold: float = 2000.0,
) -> str:
    """
    项目胜率统计（按后台订单口径）：
    - 统计单位：后台订单 orderId（有跟单成功票才纳入）
    - 金额分档：按后台订单 stake（<threshold / >=threshold）
    - 平均水位：按后台订单 odds（不区分后台来源）
    - 胜负判定：基于跟单成功票的结算 profit 汇总（需要导入 settlements）
    - 本期不输出“净胜”相关指标
    """

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
        if abs(f - round(f)) < 1e-9:
            return f"{f:.1f}"
        s = f"{f:.2f}".rstrip("0").rstrip(".")
        return s or str(v)

    def pct(v: Optional[float]) -> str:
        if v is None:
            return "--"
        return f"{v * 100.0:.2f}%"

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
        return (
            "<b>📈 项目胜率统计</b>\n"
            f"日期：{today_str()}\n\n"
            "暂无跟单成功记录。"
        )

    groups: Dict[str, List[Dict[str, Any]]] = {}
    for r in ok_rows:
        oid = str(r.get("orderId") or "").strip()
        groups.setdefault(oid, []).append(r)

    order_ids = list(groups.keys())
    orders_idx = load_orders_index(orders_dir, order_ids)
    settlements_idx = _load_ticket_settlements(data_dir)

    class Agg:
        def __init__(self) -> None:
            self.order_count = 0
            self.order_with_backend = 0
            self.sum_backend_stake = 0.0
            self.sum_backend_odds = 0.0
            self.cnt_backend_odds = 0
            self.sum_backend_odds_weighted = 0.0
            self.sum_backend_stake_for_odds_weighted = 0.0
            self.sum_follow_stake = 0.0
            self.sum_profit = 0.0
            self.settled_orders = 0
            self.win = 0
            self.lose = 0
            self.push = 0
            self.pending = 0
            self.unmatched = 0
            self.profit_unknown = 0
            self.win_money = 0.0
            self.lose_money = 0.0

        def add_backend(self, stake: Optional[float], odds: Optional[float]) -> None:
            if stake is not None:
                self.order_with_backend += 1
                self.sum_backend_stake += float(stake)
            if odds is not None:
                self.cnt_backend_odds += 1
                self.sum_backend_odds += float(odds)
            if stake is not None and odds is not None:
                self.sum_backend_odds_weighted += float(stake) * float(odds)
                self.sum_backend_stake_for_odds_weighted += float(stake)

        def add_follow(self, stake: float) -> None:
            self.sum_follow_stake += float(stake)

        def add_settled(self, profit: float) -> None:
            self.sum_profit += float(profit)

    def summarize(agg: Agg, title: str) -> List[str]:
        lines0: List[str] = []
        lines0.append(f"<b>{title}</b>")
        lines0.append(f"订单：{agg.order_count} 单（后台金额可用：{agg.order_with_backend} 单）")
        if agg.order_with_backend:
            lines0.append(f"后台金额：{fmt_amount(agg.sum_backend_stake)}（均值：{fmt_amount(agg.sum_backend_stake / max(1, agg.order_with_backend))}）")
        if agg.cnt_backend_odds:
            avg_odds_orders = agg.sum_backend_odds / max(1, agg.cnt_backend_odds)
            lines0.append(f"后台平均水位：{avg_odds_orders:.3f}（场次均值）")
        if agg.sum_backend_stake_for_odds_weighted > 1e-9:
            avg_odds_w = agg.sum_backend_odds_weighted / agg.sum_backend_stake_for_odds_weighted
            lines0.append(f"后台平均水位：{avg_odds_w:.3f}（按后台金额加权）")

        lines0.append(
            "结算："
            + " / ".join(
                [
                    f"可判定 {agg.settled_orders} 单",
                    f"比赛进行中 {agg.pending} 单",
                    f"未匹配 {agg.unmatched} 单",
                ]
            )
        )

        if agg.profit_unknown:
            lines0.append(f"提示：已结算但盈亏未知 {agg.profit_unknown} 单（导入数据不全或缺字段）")

        if settlements_idx:
            if agg.settled_orders:
                denom = agg.win + agg.lose
                wr_orders = (agg.win / denom) if denom else None
                lines0.append(f"胜负：赢 {agg.win} / 输 {agg.lose} / 走水 {agg.push}（胜率：{pct(wr_orders)}）")

                denom_money = agg.win_money + agg.lose_money
                wr_money = (agg.win_money / denom_money) if denom_money > 1e-9 else None
                lines0.append(f"胜率（按后台金额权重）：{pct(wr_money)}")

            if agg.sum_follow_stake > 1e-9:
                roi = agg.sum_profit / agg.sum_follow_stake
                lines0.append(f"已结算盈亏：{agg.sum_profit:+g}（ROI：{pct(roi)}，按跟单成功金额）")
        else:
            lines0.append("提示：未导入结算数据，胜率/盈亏相关统计为 --")

        return lines0

    overall = Agg()
    small = Agg()
    big = Agg()

    for oid, rs in groups.items():
        overall.order_count += 1

        o = orders_idx.get(oid) or {}
        backend_stake = safe_float(o.get("stake"))
        backend_odds = safe_float(o.get("odds"))
        overall.add_backend(backend_stake, backend_odds)

        seg = "unknown"
        if backend_stake is not None:
            seg = "small" if backend_stake < float(amount_threshold) else "big"
        if seg == "small":
            small.order_count += 1
            small.add_backend(backend_stake, backend_odds)
        elif seg == "big":
            big.order_count += 1
            big.add_backend(backend_stake, backend_odds)

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
        overall.add_follow(follow_stake_sum)
        if seg == "small":
            small.add_follow(follow_stake_sum)
        elif seg == "big":
            big.add_follow(follow_stake_sum)

        if not settlements_idx:
            continue

        if not tickets:
            overall.unmatched += 1
            if seg == "small":
                small.unmatched += 1
            elif seg == "big":
                big.unmatched += 1
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
            overall.unmatched += 1
            if seg == "small":
                small.unmatched += 1
            elif seg == "big":
                big.unmatched += 1
            continue
        if has_pending:
            overall.pending += 1
            if seg == "small":
                small.pending += 1
            elif seg == "big":
                big.pending += 1
            continue
        if settled_ticket_count == 0:
            overall.unmatched += 1
            if seg == "small":
                small.unmatched += 1
            elif seg == "big":
                big.unmatched += 1
            continue
        if has_profit_unknown:
            overall.profit_unknown += 1
            if seg == "small":
                small.profit_unknown += 1
            elif seg == "big":
                big.profit_unknown += 1
            continue

        # 可判定（全部已结算且输赢可计算）
        overall.settled_orders += 1
        overall.add_settled(profit_sum)
        if seg == "small":
            small.settled_orders += 1
            small.add_settled(profit_sum)
        elif seg == "big":
            big.settled_orders += 1
            big.add_settled(profit_sum)

        if profit_sum > 1e-9:
            overall.win += 1
            if backend_stake is not None:
                overall.win_money += float(backend_stake)
            if seg == "small":
                small.win += 1
                if backend_stake is not None:
                    small.win_money += float(backend_stake)
            elif seg == "big":
                big.win += 1
                if backend_stake is not None:
                    big.win_money += float(backend_stake)
        elif profit_sum < -1e-9:
            overall.lose += 1
            if backend_stake is not None:
                overall.lose_money += float(backend_stake)
            if seg == "small":
                small.lose += 1
                if backend_stake is not None:
                    small.lose_money += float(backend_stake)
            elif seg == "big":
                big.lose += 1
                if backend_stake is not None:
                    big.lose_money += float(backend_stake)
        else:
            overall.push += 1
            if seg == "small":
                small.push += 1
            elif seg == "big":
                big.push += 1

    lines: List[str] = []
    lines.append("<b>📈 项目胜率统计</b>")
    lines.append(f"日期：{today_str()}")
    lines.append(f"分档：按后台金额 <{fmt_amount(amount_threshold)} / >={fmt_amount(amount_threshold)}")
    lines.append("")
    lines.extend(summarize(overall, "总览（全部后台订单）"))
    lines.append("")
    lines.extend(summarize(small, f"小额（后台金额 < {fmt_amount(amount_threshold)}）"))
    lines.append("")
    lines.extend(summarize(big, f"大额（后台金额 >= {fmt_amount(amount_threshold)}）"))

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


def is_winrate_command(text: str) -> bool:
    t = str(text or "").strip()
    if not t:
        return False
    t_low = t.lower()
    if t_low.startswith("/stats") or t_low.startswith("/winrate") or t_low.startswith("/rate"):
        return True
    if t in {"查询胜率", "胜率", "胜率报表", "项目胜率"}:
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
    if t == "余额":
        return True
    parts = t.split()
    if not parts:
        return False
    cmd = str(parts[0] or "").strip()
    cmd = cmd.split("@", 1)[0].lower()
    return cmd in {"/balance", "/bal", "/money", "/credit"}


def main_keyboard() -> Dict[str, Any]:
    return {
        "keyboard": [
            [{"text": "今日跟单"}, {"text": "跟单配置"}],
            [{"text": "添加子账号"}, {"text": "弹药库"}],
            [{"text": "帮助"}],
        ],
        "resize_keyboard": True,
        "one_time_keyboard": False,
        "is_persistent": True,
    }


def follow_keyboard() -> Dict[str, Any]:
    # ReplyKeyboardMarkup（类似你截图那种大按钮）
    return {
        "keyboard": [
            [{"text": "固定金额"}, {"text": "比例跟单"}],
            [{"text": "封顶金额"}, {"text": "启用/停用"}, {"text": "查看当前配置"}],
            [{"text": BTN_BACK}],
        ],
        "resize_keyboard": True,
        "one_time_keyboard": False,
        "is_persistent": True,
    }


def ensure_follow_settings(path: Path) -> Dict[str, Any]:
    if path.exists():
        obj = read_json(path)
        return obj if isinstance(obj, dict) else {}
    default = {
        "enabled": False,
        "mode": "fixed",
        "fixed_amount": 50,
        "ratio_percent": 100,
        "max_amount": 0,
        "targets": [],
        "members": [],
        "updated_at": "",
    }
    atomic_write_json(path, default)
    return dict(default)


def write_follow_settings(path: Path, updates: Dict[str, Any]) -> Dict[str, Any]:
    cfg = ensure_follow_settings(path)
    cfg = dict(cfg)
    cfg.update({k: v for k, v in updates.items()})
    cfg["updated_at"] = now_iso()
    atomic_write_json(path, cfg)
    return cfg


def parse_ratio_percent_input(raw: str) -> float:
    """
    解析 TG 输入的“比例跟单”参数。

    支持：
    - 95 / 120 / 300（百分比）
    - 300% / 300％（百分比）
    - 3倍 / 3x（倍率）=> 300%
    - 兼容旧格式：1-10 => 10%-100%
    """
    s0 = str(raw or "").strip()
    if not s0:
        raise ValueError("empty")

    s = s0.replace("％", "%").strip().lower()

    is_times = False
    if s.endswith("倍"):
        is_times = True
        s = s[:-1].strip()
    elif s.endswith("x"):
        is_times = True
        s = s[:-1].strip()

    if s.endswith("%"):
        s = s[:-1].strip()
        return float(s)

    v = float(s)
    if is_times:
        return float(v * 100.0)

    # 兼容旧输入：1-10 => 10%-100%
    if float(v).is_integer():
        n = int(v)
        if 1 <= n <= 10:
            return float(n * 10)

    return float(v)


def follow_settings_summary(cfg: Dict[str, Any]) -> str:
    enabled = "✅ 开启" if bool(cfg.get("enabled")) else "❌ 关闭"
    mode = str(cfg.get("mode") or "fixed")
    fixed_amount = cfg.get("fixed_amount")
    ratio_percent = cfg.get("ratio_percent")
    max_amount = cfg.get("max_amount")
    mode_zh = "固定金额" if mode == "fixed" else ("比例跟单" if mode == "ratio" else mode)
    return (
        "<b>⚙️ 跟单配置</b>\n"
        f"状态：{enabled}\n"
        f"方案：{mode_zh}\n"
        "信号源：全部后台账号（无需勾选）\n"
        "执行：弹药库会员随机（余额足够优先）\n"
        f"固定金额：{fixed_amount}\n"
        f"比例：{ratio_percent}%\n"
        f"封顶：{max_amount}（0=不封顶）\n"
        f"日期：{today_str()}"
    )


def is_follow_config_command(text: str) -> bool:
    t = str(text or "").strip()
    if not t:
        return False
    if t.startswith("/跟单配置") or t.lower().startswith("/follow_config") or t.lower().startswith("/config"):
        return True
    if "跟单配置" == t:
        return True
    return False


def is_follow_button(text: str) -> bool:
    return str(text or "").strip() in {"固定金额", "比例跟单", "封顶金额", "启用/停用", "查看当前配置"}


def backend_keyboard() -> Dict[str, Any]:
    return {
        "keyboard": [
            [{"text": "添加登3"}],
            [{"text": "当前后台数"}, {"text": "查看后台列表"}],
            [{"text": "删除后台账号"}, {"text": BTN_BACK}],
        ],
        "resize_keyboard": True,
        "one_time_keyboard": False,
        "is_persistent": True,
    }


def arsenal_keyboard() -> Dict[str, Any]:
    return {
        "keyboard": [
            [{"text": "添加会员"}],
            [{"text": "当前会员数"}, {"text": "查看成员列表"}],
            [{"text": "删除会员"}, {"text": BTN_BACK}],
        ],
        "resize_keyboard": True,
        "one_time_keyboard": False,
        "is_persistent": True,
    }


def is_backend_command(text: str) -> bool:
    t = str(text or "").strip()
    if not t:
        return False
    if t.startswith("/添加子账号") or t.lower().startswith("/add_backend") or t.lower().startswith("/add_sub"):
        return True
    if t == "添加子账号":
        return True
    return False


def is_arsenal_command(text: str) -> bool:
    t = str(text or "").strip()
    if not t:
        return False
    if t.startswith("/弹药库") or t.lower().startswith("/arsenal"):
        return True
    if t == "弹药库":
        return True
    return False


def is_main_menu(text: str) -> bool:
    t = str(text or "").strip()
    if not t:
        return False
    if t in {BTN_BACK, "菜单", "帮助"}:
        return True
    tl = t.lower()
    return tl in {"/start", "/menu", "/help"}


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
        money = nowcredit or maxcredit or cash or ""
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
        money = nowcredit or maxcredit or cash or "未知"
        if currency:
            lines.append(f"{i}. {label}（{status_zh(m)}）：{money} {currency}")
        else:
            lines.append(f"{i}. {label}（{status_zh(m)}）：{money}")
    return "\n".join(lines)


def help_text() -> str:
    return (
        "<b>可用指令</b>\n"
        "/today - 查询今日跟单报表\n"
        "/report - 查询今日跟单报表\n"
        "/balance - 查询在线会员余额（同：余额）\n"
        "/stats - 查询项目胜率统计（同：查询胜率）\n"
        "/oid <后台单号> - 查询该单跟单金额/盈亏（别名：/order）\n"
        "/跟单配置 - 打开跟单配置面板\n"
        "/添加子账号 - 管理后台账号（登3）\n"
        "/弹药库 - 管理会员账号（弹药库）\n"
        f"{BTN_BACK} - 返回主菜单\n"
        "也支持直接发送：今日跟单 / 跟单报表 / 查询胜率 / 跟单配置 / 添加子账号 / 弹药库 / 余额"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Telegram Bot (commands)")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--orders-dir", default="orders")
    parser.add_argument("--state", default=".run/tg_bot_state.json")
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument("--timeout", type=int, default=20)
    args = parser.parse_args()

    config_path = Path(args.config)
    data_dir = Path(args.data_dir)
    orders_dir = Path(args.orders_dir)
    state_path = Path(args.state)

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

            for u in updates:
                try:
                    uid = int(u.get("update_id") or 0)
                except Exception:
                    uid = 0
                if uid and uid >= offset:
                    offset = uid + 1

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
                pending_key = f"{chat_id}:{user_id}" if user_id else chat_id
                menu = str(menus.get(pending_key) or MENU_MAIN).strip() or MENU_MAIN

                def set_menu(m: str) -> None:
                    nonlocal menu
                    menu = str(m or MENU_MAIN).strip() or MENU_MAIN
                    menus[pending_key] = menu

                def menu_keyboard(m: str) -> Dict[str, Any]:
                    m = str(m or MENU_MAIN)
                    if m == MENU_FOLLOW:
                        return follow_keyboard()
                    if m == MENU_BACKEND:
                        return backend_keyboard()
                    if m == MENU_ARSENAL:
                        return arsenal_keyboard()
                    return main_keyboard()

                # 限制：如果 config.json 配了 telegram_chat_id，则只响应该 chat
                # 兼容：允许配置为数字 chat_id（-100...）或 @username（频道/群用户名），也支持多值逗号分隔。
                if allow_chat_id:
                    chat_username = _normalize_tg_username(chat.get("username") or "")
                    if not ((chat_id and chat_id in allow_ids) or (chat_username and chat_username in allow_usernames)):
                        continue

                if str(text).strip().lower().startswith("/help") or str(text).strip() == "帮助":
                    for part in split_message(help_text()):
                        tg_send_message(
                            bot_token=bot_token,
                            chat_id=chat_id,
                            html_text=part,
                            reply_markup=main_keyboard(),
                            proxy=proxy,
                            timeout_s=10,
                        )
                    continue

                if str(text).strip() == BTN_BACK:
                    pending.pop(pending_key, None)
                    set_menu(MENU_MAIN)
                    for part in split_message(help_text()):
                        tg_send_message(
                            bot_token=bot_token,
                            chat_id=chat_id,
                            html_text=part,
                            reply_markup=main_keyboard(),
                            proxy=proxy,
                            timeout_s=10,
                        )
                    continue

                # 快捷：发送“余额”或 /balance 查看在线会员与余额（不打断当前菜单/输入流程）
                if is_balance_command(text):
                    arsenal_path = data_dir / "arsenal.json"
                    doc = ensure_arsenal(arsenal_path)
                    tg_send_message(
                        bot_token=bot_token,
                        chat_id=chat_id,
                        html_text=arsenal_online_balance_summary(doc),
                        reply_markup=menu_keyboard(menu),
                        proxy=proxy,
                        timeout_s=10,
                    )
                    continue

                # 快捷：发送“查询胜率”查看项目胜率统计（按后台订单口径）
                if is_winrate_command(text):
                    report = build_project_winrate_report(data_dir=data_dir, orders_dir=orders_dir, amount_threshold=2000.0)
                    for part in split_message(report):
                        tg_send_message(
                            bot_token=bot_token,
                            chat_id=chat_id,
                            html_text=part,
                            reply_markup=menu_keyboard(menu),
                            proxy=proxy,
                            timeout_s=15,
                        )
                    continue

                # 主菜单快捷入口（文本按钮）
                if str(text).strip() == "跟单配置":
                    set_menu(MENU_FOLLOW)
                elif str(text).strip() == "添加子账号":
                    set_menu(MENU_BACKEND)
                elif str(text).strip() == "弹药库":
                    set_menu(MENU_ARSENAL)

                # 跟单配置面板
                if is_follow_config_command(text):
                    set_menu(MENU_FOLLOW)
                    settings_path = data_dir / "follow_settings.json"
                    cfg = ensure_follow_settings(settings_path)
                    intro = (
                        "<b>⚙️ 跟单配置面板</b>\n"
                        "说明：点击下方按钮进行设置。\n"
                        "固定金额：每单固定下注金额\n"
                        "比例跟单：输入百分比（如 95/120/300），也支持 3倍/3x；兼容旧输入 1-10=10%-100%\n"
                        "封顶金额：跟单金额上限（0=不封顶）\n"
                        "启用/停用：一键开关跟单\n"
                    )
                    msg_text = intro + "\n" + follow_settings_summary(cfg)
                    tg_send_message(
                        bot_token=bot_token,
                        chat_id=chat_id,
                        html_text=msg_text,
                        reply_markup=follow_keyboard(),
                        proxy=proxy,
                        timeout_s=10,
                    )
                    continue

                # 后台账号面板
                if is_backend_command(text):
                    set_menu(MENU_BACKEND)
                    cfg = read_config(config_path)
                    intro = (
                        "<b>🧩 后台账号管理</b>\n"
                        "说明：用于添加后台账号并监听订单。\n"
                        "⚠️ 注意：你在 Telegram 输入的账号/密码会保存到本机的 config.json。\n"
                        "添加登3：添加一个“登入3”账号（ag_wmc_watch 会自动登录并开始监听订单）\n"
                        "删除后台账号：仅从 config.json 移除（不会删除历史 orders/state 文件）\n"
                    )
                    tg_send_message(
                        bot_token=bot_token,
                        chat_id=chat_id,
                        html_text=intro + "\n" + backend_targets_summary(cfg),
                        reply_markup=backend_keyboard(),
                        proxy=proxy,
                        timeout_s=10,
                    )
                    continue

                # 弹药库面板
                if is_arsenal_command(text):
                    set_menu(MENU_ARSENAL)
                    arsenal_path = data_dir / "arsenal.json"
                    doc = ensure_arsenal(arsenal_path)
                    intro = (
                        "<b>🧰 弹药库（会员账号）</b>\n"
                        "说明：这里管理用于跟单的会员账号。\n"
                        "⚠️ 注意：你在 Telegram 输入的账号/密码会保存到本机的 data/arsenal.json。\n"
                        "添加会员：依次输入 base_url、账号、密码（member_arsenal_watch 会自动登录并保持在线）\n"
                    )
                    tg_send_message(
                        bot_token=bot_token,
                        chat_id=chat_id,
                        html_text=intro + "\n" + arsenal_summary(doc),
                        reply_markup=arsenal_keyboard(),
                        proxy=proxy,
                        timeout_s=10,
                    )
                    continue

                # 处理按钮点击（ReplyKeyboard：按钮会作为普通文本发过来）
                if is_follow_button(text):
                    settings_path = data_dir / "follow_settings.json"
                    cfg = ensure_follow_settings(settings_path)
                    t = str(text).strip()
                    if t == "查看当前配置":
                        tg_send_message(
                            bot_token=bot_token,
                            chat_id=chat_id,
                            html_text=follow_settings_summary(cfg),
                            reply_markup=follow_keyboard(),
                            proxy=proxy,
                            timeout_s=10,
                        )
                        continue
                    if t == "启用/停用":
                        new_enabled = not bool(cfg.get("enabled"))
                        cfg2 = write_follow_settings(settings_path, {"enabled": new_enabled})
                        tg_send_message(
                            bot_token=bot_token,
                            chat_id=chat_id,
                            html_text="已更新：\n" + follow_settings_summary(cfg2),
                            reply_markup=follow_keyboard(),
                            proxy=proxy,
                            timeout_s=10,
                        )
                        continue
                    if t == "固定金额":
                        pending[pending_key] = {"action": "set_fixed", "ts": int(time.time())}
                        tg_send_message(
                            bot_token=bot_token,
                            chat_id=chat_id,
                            html_text="请输入固定跟单金额（例如 50 或 100）：",
                            reply_markup=follow_keyboard(),
                            proxy=proxy,
                            timeout_s=10,
                        )
                        continue
                    if t == "比例跟单":
                        pending[pending_key] = {"action": "set_ratio", "ts": int(time.time())}
                        tg_send_message(
                            bot_token=bot_token,
                            chat_id=chat_id,
                            html_text="请输入比例（百分比）：例如 95、120、300、300%、3倍、3x；兼容旧输入 1-10=10%-100%",
                            reply_markup=follow_keyboard(),
                            proxy=proxy,
                            timeout_s=10,
                        )
                        continue
                    if t == "封顶金额":
                        pending[pending_key] = {"action": "set_cap", "ts": int(time.time())}
                        tg_send_message(
                            bot_token=bot_token,
                            chat_id=chat_id,
                            html_text="请输入封顶金额（0=不封顶，例如 200 或 0）：",
                            reply_markup=follow_keyboard(),
                            proxy=proxy,
                            timeout_s=10,
                        )
                        continue

                # 后台账号按钮（ReplyKeyboard：按钮会作为普通文本发过来）
                t2 = str(text).strip()
                if t2 in {"添加登3", "当前后台数", "查看后台列表", "删除后台账号"}:
                    cfg0 = read_config(config_path)
                    if t2 in {"当前后台数", "查看后台列表"}:
                        tg_send_message(
                            bot_token=bot_token,
                            chat_id=chat_id,
                            html_text=backend_targets_summary(cfg0),
                            reply_markup=backend_keyboard(),
                            proxy=proxy,
                            timeout_s=10,
                        )
                        continue
                    if t2 == "添加登3":
                        pending[pending_key] = {"action": "backend_add_username", "login_tab": 3, "ts": int(time.time())}
                        tg_send_message(
                            bot_token=bot_token,
                            chat_id=chat_id,
                            html_text="请输入后台账号（将添加为 登3）：",
                            reply_markup=backend_keyboard(),
                            proxy=proxy,
                            timeout_s=10,
                        )
                        continue
                    if t2 == "删除后台账号":
                        pending[pending_key] = {"action": "backend_delete_choose", "ts": int(time.time())}
                        tg_send_message(
                            bot_token=bot_token,
                            chat_id=chat_id,
                            html_text="请输入要删除的后台账号 name（或序号），发送 /cancel 可取消：\n\n" + backend_targets_summary(cfg0),
                            reply_markup=backend_keyboard(),
                            proxy=proxy,
                            timeout_s=10,
                        )
                        continue

                # 弹药库按钮
                if t2 in {"添加会员", "当前会员数", "查看成员列表", "删除会员"}:
                    arsenal_path = data_dir / "arsenal.json"
                    doc0 = ensure_arsenal(arsenal_path)
                    if t2 in {"当前会员数", "查看成员列表"}:
                        tg_send_message(
                            bot_token=bot_token,
                            chat_id=chat_id,
                            html_text=arsenal_summary(doc0),
                            reply_markup=arsenal_keyboard(),
                            proxy=proxy,
                            timeout_s=10,
                        )
                        continue
                    if t2 == "添加会员":
                        pending[pending_key] = {"action": "arsenal_add_base_url", "ts": int(time.time())}
                        tg_send_message(
                            bot_token=bot_token,
                            chat_id=chat_id,
                            html_text="请输入会员 base_url（例如 https://205.201.0.120）：",
                            reply_markup=arsenal_keyboard(),
                            proxy=proxy,
                            timeout_s=10,
                        )
                        continue
                    if t2 == "删除会员":
                        pending[pending_key] = {"action": "arsenal_delete_choose", "ts": int(time.time())}
                        tg_send_message(
                            bot_token=bot_token,
                            chat_id=chat_id,
                            html_text="请输入要删除的会员序号（1-N），发送 /cancel 可取消：\n\n" + arsenal_summary(doc0),
                            reply_markup=arsenal_keyboard(),
                            proxy=proxy,
                            timeout_s=10,
                        )
                        continue

                # 处理“等待输入”状态
                if pending_key in pending:
                    action = str((pending.get(pending_key) or {}).get("action") or "")

                    def current_kb() -> Dict[str, Any]:
                        if action.startswith("set_"):
                            return follow_keyboard()
                        if action.startswith("backend_"):
                            return backend_keyboard()
                        if action.startswith("arsenal_"):
                            return arsenal_keyboard()
                        return menu_keyboard(menu)

                    if str(text).strip().lower().startswith("/cancel"):
                        pending.pop(pending_key, None)
                        tg_send_message(
                            bot_token=bot_token,
                            chat_id=chat_id,
                            html_text="已取消本次设置。",
                            reply_markup=current_kb(),
                            proxy=proxy,
                            timeout_s=10,
                        )
                        continue

                    settings_path = data_dir / "follow_settings.json"
                    cfg = ensure_follow_settings(settings_path)
                    raw = str(text).strip()

                    def bad_input(hint: str) -> None:
                        tg_send_message(
                            bot_token=bot_token,
                            chat_id=chat_id,
                            html_text=f"输入无效：{hint}\n发送 /cancel 可取消。",
                            reply_markup=current_kb(),
                            proxy=proxy,
                            timeout_s=10,
                        )

                    if action == "set_fixed":
                        try:
                            amt = float(raw)
                        except Exception:
                            bad_input("请输入数字，例如 50")
                            continue
                        if amt <= 0:
                            bad_input("金额必须大于 0")
                            continue
                        cfg2 = write_follow_settings(settings_path, {"mode": "fixed", "fixed_amount": float(amt)})
                        pending.pop(pending_key, None)
                        tg_send_message(
                            bot_token=bot_token,
                            chat_id=chat_id,
                            html_text="✅ 固定金额已保存。\n" + follow_settings_summary(cfg2),
                            reply_markup=follow_keyboard(),
                            proxy=proxy,
                            timeout_s=10,
                        )
                        continue

                    if action == "set_ratio":
                        try:
                            percent = parse_ratio_percent_input(raw)
                        except Exception:
                            bad_input("请输入比例，例如 95、120、300、300%、3倍（兼容 1-10=10%-100%）")
                            continue
                        if percent <= 0:
                            bad_input("比例必须大于 0")
                            continue
                        if percent > 10000:
                            bad_input("比例过大（>10000%），请确认后重新输入")
                            continue
                        percent2: Any = int(percent) if float(percent).is_integer() else float(percent)
                        cfg2 = write_follow_settings(settings_path, {"mode": "ratio", "ratio_percent": percent2})
                        pending.pop(pending_key, None)
                        tg_send_message(
                            bot_token=bot_token,
                            chat_id=chat_id,
                            html_text=f"✅ 比例跟单已保存：{percent2}%\n" + follow_settings_summary(cfg2),
                            reply_markup=follow_keyboard(),
                            proxy=proxy,
                            timeout_s=10,
                        )
                        continue

                    if action == "set_cap":
                        try:
                            cap = float(raw)
                        except Exception:
                            bad_input("请输入数字，例如 200 或 0")
                            continue
                        if cap < 0:
                            bad_input("封顶金额不能小于 0")
                            continue
                        cfg2 = write_follow_settings(settings_path, {"max_amount": float(cap)})
                        pending.pop(pending_key, None)
                        tg_send_message(
                            bot_token=bot_token,
                            chat_id=chat_id,
                            html_text="✅ 封顶金额已保存。\n" + follow_settings_summary(cfg2),
                            reply_markup=follow_keyboard(),
                            proxy=proxy,
                            timeout_s=10,
                        )
                        continue

                    if action == "backend_add_username":
                        username = raw
                        if not username:
                            bad_input("请输入账号")
                            continue
                        pending[pending_key] = {
                            "action": "backend_add_password",
                            "username": username,
                            "login_tab": int((pending.get(pending_key) or {}).get("login_tab") or 3),
                            "ts": int(time.time()),
                        }
                        tg_send_message(
                            bot_token=bot_token,
                            chat_id=chat_id,
                            html_text="请输入后台密码：",
                            reply_markup=backend_keyboard(),
                            proxy=proxy,
                            timeout_s=10,
                        )
                        continue

                    if action == "backend_add_password":
                        username = str((pending.get(pending_key) or {}).get("username") or "").strip()
                        pwd = raw
                        if not username or not pwd:
                            bad_input("缺少账号或密码")
                            continue
                        cfg0 = read_config(config_path)
                        try:
                            cfg1, name = add_backend_target(cfg=cfg0, username=username, password=pwd, login_tab=3)
                        except Exception as e:
                            pending.pop(pending_key, None)
                            tg_send_message(
                                bot_token=bot_token,
                                chat_id=chat_id,
                                html_text=f"❌ 添加失败：{e}",
                                reply_markup=backend_keyboard(),
                                proxy=proxy,
                                timeout_s=10,
                            )
                            continue
                        write_config(config_path, cfg1)
                        pending.pop(pending_key, None)
                        tg_send_message(
                            bot_token=bot_token,
                            chat_id=chat_id,
                            html_text=(
                                f"✅ 已添加后台账号：{name}\n"
                                "已写入 config.json，ag_wmc_watch 将在下一轮轮询自动登录（登3）并开始监听订单。"
                            ),
                            reply_markup=backend_keyboard(),
                            proxy=proxy,
                            timeout_s=10,
                        )
                        continue

                    if action == "backend_delete_choose":
                        cfg0 = read_config(config_path)
                        targets = cfg0.get("targets") or []
                        if not isinstance(targets, list) or not targets:
                            pending.pop(pending_key, None)
                            tg_send_message(
                                bot_token=bot_token,
                                chat_id=chat_id,
                                html_text="当前没有后台账号可删除。",
                                reply_markup=backend_keyboard(),
                                proxy=proxy,
                                timeout_s=10,
                            )
                            continue
                        name_in = raw
                        chosen = ""
                        try:
                            idx = int(name_in)
                            if 1 <= idx <= len(targets):
                                t = targets[idx - 1]
                                if isinstance(t, dict):
                                    chosen = str(t.get("name") or "")
                        except Exception:
                            chosen = str(name_in)
                        chosen = chosen.strip()
                        if not chosen:
                            bad_input("请输入 name 或序号")
                            continue
                        pending[pending_key] = {"action": "backend_delete_confirm", "name": chosen, "ts": int(time.time())}
                        tg_send_message(
                            bot_token=bot_token,
                            chat_id=chat_id,
                            html_text=f"确认删除后台账号：{chosen} ？回复“确认删除”继续，或 /cancel 取消。",
                            reply_markup=backend_keyboard(),
                            proxy=proxy,
                            timeout_s=10,
                        )
                        continue

                    if action == "backend_delete_confirm":
                        name = str((pending.get(pending_key) or {}).get("name") or "").strip()
                        if raw != "确认删除":
                            bad_input("请回复“确认删除”以继续")
                            continue
                        cfg0 = read_config(config_path)
                        cfg1, ok_del = delete_target_by_name(cfg0, name)
                        pending.pop(pending_key, None)
                        if not ok_del:
                            tg_send_message(
                                bot_token=bot_token,
                                chat_id=chat_id,
                                html_text=f"未找到后台账号：{name}",
                                reply_markup=backend_keyboard(),
                                proxy=proxy,
                                timeout_s=10,
                            )
                            continue
                        write_config(config_path, cfg1)
                        tg_send_message(
                            bot_token=bot_token,
                            chat_id=chat_id,
                            html_text=f"✅ 已删除后台账号：{name}\n（仅从 config.json 移除，不会删除历史 orders/state 文件）",
                            reply_markup=backend_keyboard(),
                            proxy=proxy,
                            timeout_s=10,
                        )
                        continue

                    if action == "arsenal_add_base_url":
                        base_url = raw
                        if not base_url.startswith("http"):
                            bad_input("base_url 需要以 http/https 开头")
                            continue
                        pending[pending_key] = {"action": "arsenal_add_username", "base_url": base_url, "ts": int(time.time())}
                        tg_send_message(
                            bot_token=bot_token,
                            chat_id=chat_id,
                            html_text="请输入会员账号：",
                            reply_markup=arsenal_keyboard(),
                            proxy=proxy,
                            timeout_s=10,
                        )
                        continue

                    if action == "arsenal_add_username":
                        username = raw
                        if not username:
                            bad_input("请输入账号")
                            continue
                        base_url = str((pending.get(pending_key) or {}).get("base_url") or "").strip()
                        pending[pending_key] = {
                            "action": "arsenal_add_password",
                            "base_url": base_url,
                            "username": username,
                            "ts": int(time.time()),
                        }
                        tg_send_message(
                            bot_token=bot_token,
                            chat_id=chat_id,
                            html_text="请输入会员密码：",
                            reply_markup=arsenal_keyboard(),
                            proxy=proxy,
                            timeout_s=10,
                        )
                        continue

                    if action == "arsenal_add_password":
                        base_url = str((pending.get(pending_key) or {}).get("base_url") or "").strip()
                        username = str((pending.get(pending_key) or {}).get("username") or "").strip()
                        pwd = raw
                        if not (base_url and username and pwd):
                            bad_input("缺少 base_url/账号/密码")
                            continue
                        cfg0 = read_config(config_path)
                        proxy2 = str(cfg0.get("proxy") or "").strip()
                        arsenal_path = data_dir / "arsenal.json"
                        doc0 = ensure_arsenal(arsenal_path)
                        try:
                            doc1, member_id = add_member_to_arsenal(
                                doc=doc0,
                                base_url=base_url,
                                username=username,
                                password=pwd,
                                proxy=proxy2,
                            )
                        except Exception as e:
                            pending.pop(pending_key, None)
                            tg_send_message(
                                bot_token=bot_token,
                                chat_id=chat_id,
                                html_text=f"❌ 添加会员失败：{e}",
                                reply_markup=arsenal_keyboard(),
                                proxy=proxy,
                                timeout_s=10,
                            )
                            continue
                        atomic_write_json(arsenal_path, doc1)
                        pending.pop(pending_key, None)
                        tg_send_message(
                            bot_token=bot_token,
                            chat_id=chat_id,
                            html_text=f"✅ 已添加会员：{username}\nID：{member_id}\nmember_arsenal_watch 会自动登录并保持在线。",
                            reply_markup=arsenal_keyboard(),
                            proxy=proxy,
                            timeout_s=10,
                        )
                        continue

                    if action == "arsenal_delete_choose":
                        arsenal_path = data_dir / "arsenal.json"
                        doc0 = ensure_arsenal(arsenal_path)
                        members = list_members(doc0)
                        if not members:
                            pending.pop(pending_key, None)
                            tg_send_message(
                                bot_token=bot_token,
                                chat_id=chat_id,
                                html_text="当前没有会员可删除。",
                                reply_markup=arsenal_keyboard(),
                                proxy=proxy,
                                timeout_s=10,
                            )
                            continue
                        inp = raw
                        chosen = ""
                        try:
                            idx = int(inp)
                            if 1 <= idx <= len(members):
                                chosen = str(members[idx - 1].get("id") or "")
                        except Exception:
                            chosen = str(inp)
                        chosen = chosen.strip()
                        if not chosen:
                            bad_input("请输入会员序号（1-N）")
                            continue
                        pending[pending_key] = {"action": "arsenal_delete_confirm", "id": chosen, "ts": int(time.time())}
                        tg_send_message(
                            bot_token=bot_token,
                            chat_id=chat_id,
                            html_text=f"确认删除会员：{chosen} ？回复“确认删除”继续，或 /cancel 取消。",
                            reply_markup=arsenal_keyboard(),
                            proxy=proxy,
                            timeout_s=10,
                        )
                        continue

                    if action == "arsenal_delete_confirm":
                        mid_del = str((pending.get(pending_key) or {}).get("id") or "").strip()
                        if raw != "确认删除":
                            bad_input("请回复“确认删除”以继续")
                            continue
                        arsenal_path = data_dir / "arsenal.json"
                        doc0 = ensure_arsenal(arsenal_path)
                        doc1, ok_del = delete_member_by_id(doc0, mid_del)
                        pending.pop(pending_key, None)
                        if not ok_del:
                            tg_send_message(
                                bot_token=bot_token,
                                chat_id=chat_id,
                                html_text=f"未找到会员：{mid_del}",
                                reply_markup=arsenal_keyboard(),
                                proxy=proxy,
                                timeout_s=10,
                            )
                            continue
                        atomic_write_json(arsenal_path, doc1)
                        tg_send_message(
                            bot_token=bot_token,
                            chat_id=chat_id,
                            html_text=f"✅ 已删除会员：{mid_del}",
                            reply_markup=arsenal_keyboard(),
                            proxy=proxy,
                            timeout_s=10,
                        )
                        continue

                oid_query = parse_order_query(text)
                if oid_query is not None:
                    if not oid_query:
                        tg_send_message(
                            bot_token=bot_token,
                            chat_id=chat_id,
                            html_text="用法：/oid <后台单号>（或 /order <后台单号>）",
                            reply_markup=menu_keyboard(menu),
                            proxy=proxy,
                            timeout_s=10,
                        )
                        continue

                    report = build_order_follow_report(data_dir=data_dir, orders_dir=orders_dir, order_id=oid_query)
                    for part in split_message(report):
                        tg_send_message(
                            bot_token=bot_token,
                            chat_id=chat_id,
                            html_text=part,
                            reply_markup=menu_keyboard(menu),
                            proxy=proxy,
                            timeout_s=15,
                        )
                    continue

                if not is_report_command(text):
                    continue

                report = build_today_follow_report(data_dir=data_dir, orders_dir=orders_dir, only_ok=True, only_live=True)
                for part in split_message(report):
                    tg_send_message(
                        bot_token=bot_token,
                        chat_id=chat_id,
                        html_text=part,
                        reply_markup=menu_keyboard(menu),
                        proxy=proxy,
                        timeout_s=15,
                    )

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
