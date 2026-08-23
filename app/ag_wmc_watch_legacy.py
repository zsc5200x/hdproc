import argparse
import hashlib
import html
import json
import os
import re
import socket
import sys
import time
from time import time as now_ts
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from requests.utils import get_environ_proxies
from urllib3.util.retry import Retry

from tg_status_log import load_tg_push_config, log_and_maybe_push
from playwright_proxy import build_playwright_proxy
from portable_env import ensure_playwright_browsers_path


DEFAULT_HEADERS = {
    "accept": "*/*",
    "accept-language": "zh-CN,zh;q=0.9",
    "cache-control": "no-cache",
    "pragma": "no-cache",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) PythonWatch/1.0",
}

try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Portable packages bundle Playwright browsers under ../ms-playwright (relative to this file).
ensure_playwright_browsers_path(Path(__file__).resolve().parent)

_WATCH_LOCK_FH = None


def now_cn() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def acquire_single_instance_lock(lock_path: Path) -> bool:
    """
    防止同一套目录下同时启动多个 ag_wmc_watch：
    - 多实例会重复推送同一后台订单（Telegram 出现多条“等待结果”），并导致 follow_engine 只能更新其中一条。
    """
    global _WATCH_LOCK_FH
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fh = lock_path.open("a+", encoding="utf-8")
    except Exception:
        return False

    try:
        fh.seek(0)
        fh.truncate(0)
        fh.write(f"pid={os.getpid()}\nstarted_at={now_cn()}\n")
        fh.flush()
    except Exception:
        pass

    try:
        if os.name == "nt":
            import msvcrt  # type: ignore

            fh.seek(0)
            # locking 需要锁定一定长度，确保文件至少有 1 byte
            try:
                if fh.read(1) == "":
                    fh.seek(0)
                    fh.write("0")
                    fh.flush()
            except Exception:
                pass
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl  # type: ignore

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except Exception:
        try:
            fh.close()
        except Exception:
            pass
        return False

    _WATCH_LOCK_FH = fh
    return True


def redact_cookie(cookie: str) -> str:
    if not cookie:
        return ""
    parts = []
    for kv in cookie.split(";"):
        kv = kv.strip()
        if not kv:
            continue
        if "=" not in kv:
            parts.append(kv)
            continue
        k, _v = kv.split("=", 1)
        parts.append(f"{k}=***")
    return "; ".join(parts)

def safe_name(name: str) -> str:
    s = "".join(c for c in str(name) if c.isalnum() or c in ("-", "_")).strip()
    return s or "default"


def _parse_proxy_host_port(proxy: str) -> Tuple[str, int]:
    p = str(proxy or "").strip()
    if not p:
        return "", 0

    # 支持：ip|port|user|pass|...（忽略后面的字段）
    if "|" in p:
        parts = [x.strip() for x in p.split("|")]
        while parts and not parts[-1]:
            parts.pop()
        if len(parts) >= 2 and parts[0] and parts[1]:
            try:
                return parts[0], int(parts[1])
            except Exception:
                return "", 0
        return "", 0

    # 支持标准 URL：http(s)://user:pass@host:port / socks5://...
    u = urlparse(p)
    if u.hostname and u.port:
        return str(u.hostname), int(u.port)

    # 兼容无 scheme：127.0.0.1:7890
    if "://" not in p and ":" in p:
        host, port_s = p.rsplit(":", 1)
        try:
            return host.strip(), int(port_s)
        except Exception:
            return "", 0

    return "", 0


def _is_localhost(host: str) -> bool:
    h = (host or "").strip().lower()
    return h in ("127.0.0.1", "localhost", "::1", "0.0.0.0")


def _tcp_port_listening(host: str, port: int, timeout_s: float = 0.25) -> bool:
    if not host or port <= 0:
        return False
    try:
        with socket.create_connection((host, int(port)), timeout=float(timeout_s)):
            return True
    except OSError:
        return False


def ensure_local_proxy_available(target: "Target") -> None:
    """
    若配置了本地代理，但本机未监听对应端口，则临时禁用该代理，避免：
    - requests/Playwright 直接报 proxy connection refused
    - WebUI 体验为“浏览器一闪而过 / 无法登录”
    """
    p = str(getattr(target, "proxy", "") or "").strip()
    if not p:
        return
    host, port = _parse_proxy_host_port(p)
    if host and port and _is_localhost(host) and not _tcp_port_listening(host, port):
        print(
            f"[{now_cn()}] [{target.name}] 检测到本地代理不可用（{host}:{port} 未监听），已临时禁用代理：{p}。"
            "如需走代理，请先开启代理软件并确认端口；或清空 config.json 的 proxy。",
            flush=True,
        )
        target.proxy = ""


def stable_id_from_obj(obj: Any) -> str:
    raw = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def strip_html(text: Any) -> str:
    if text is None:
        return ""
    s = html.unescape(str(text))
    s = _TAG_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip()
    return s


def extract_odds_from_text(text: str) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"@\s*([0-9]+(?:\.[0-9]+)?)", text)
    return m.group(1) if m else None


def extract_selection_from_text(text: str) -> Optional[str]:
    if not text:
        return None
    # 常见展示："{球队} -1 / 1.5 @ 0.96" 或 "{球队} -0.5 @ 1.01"
    # 取 "@" 之前的部分作为“球队+盘口”
    if "@" in text:
        left = text.split("@", 1)[0].strip()
        return left if left else None
    return None


def looks_like_logged_out(data: Dict[str, Any]) -> bool:
    if not isinstance(data, dict):
        return False
    if data.get("status") != "error":
        return False
    code = str(data.get("code") or "").strip()
    msg = str(data.get("msg") or "").strip()
    # 站点在掉线/被踢时常见的返回（你截图里出现了 code=4X014）
    if code in {"4X014", "4X013", "logout", "login_required"}:
        return True
    # 兜底：根据文本判断
    msg_l = msg.lower()
    for kw in (
        "重新登录",
        "重新登入",
        "请重新登录",
        "请重新登入",
        "已登出",
        "被踢",
        "踢出",
        "登录",
        "登入",
        "session",
        "expired",
        "timeout",
    ):
        if kw.lower() in msg_l:
            return True
    # 非 JSON 时我们会把片段放在 msg 里
    if code == "non_json" and any(
        k in msg_l
        for k in (
            "login",
            "username",
            "password",
            "session",
            "登入",
            "登录",
            "账号",
            "帳號",
            "密码",
            "密碼",
            "<form",
        )
    ):
        return True
    return False


_BUSY_CODE_MSG: Dict[str, str] = {
    # 站点常见“系统忙/流量高”错误码（部分站点不会返回 msg，需要本地兜底文案）
    "0X001": "由于网站流量较高，请稍后重试。",
    "0X002": "由于网站流量较高，请稍后重试。",
    "0X003": "系统正在忙碌中，请稍后再试。",
    "0X004": "系统正在忙碌中，请稍后再试。",
}


def _normalize_error_msg(code: str, msg: str) -> str:
    msg = str(msg or "").strip()
    if msg:
        return msg
    code = str(code or "").strip()
    return _BUSY_CODE_MSG.get(code, "")


def cookie_string_from_pairs(pairs: List[Tuple[str, str]]) -> str:
    items = []
    for k, v in pairs:
        k = str(k).strip()
        if not k:
            continue
        items.append(f"{k}={v}")
    return "; ".join(items)

def dedupe_cookies(cookies: List[Dict[str, Any]], host: str) -> str:
    # 同名 cookie 可能存在多份（不同 domain/path）；优先选择 domain 更精确的一份
    best: Dict[str, Tuple[int, str]] = {}
    for c in cookies:
        name = str(c.get("name") or "").strip()
        value = str(c.get("value") or "")
        if not name:
            continue
        domain = str(c.get("domain") or "").lstrip(".")
        if host and domain:
            if not (host == domain or host.endswith("." + domain)):
                continue
        score = len(domain) if domain else 0
        prev = best.get(name)
        if prev is None or score >= prev[0]:
            best[name] = (score, value)
    pairs = [(k, v) for k, (_score, v) in best.items()]
    pairs.sort(key=lambda kv: kv[0].lower())
    return cookie_string_from_pairs(pairs)


def update_config_target(config_path: Path, target_name: str, updates: Dict[str, Any]) -> None:
    # 兼容某些编辑器写入的 UTF-8 BOM
    raw = json.loads(config_path.read_text(encoding="utf-8-sig"))
    targets = raw.get("targets") or []
    for t in targets:
        if str(t.get("name")) == str(target_name):
            for k, v in updates.items():
                if v is None:
                    continue
                t[k] = v
            break
    config_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

def append_jsonl(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")

def persist_orders(orders_dir: Path, target_name: str, orders: List[Dict[str, Any]]) -> None:
    if not orders:
        return
    p = orders_dir / f"{safe_name(target_name)}.jsonl"
    ts = int(now_ts())
    for o in orders:
        bet_ts = 0
        try:
            bet_ts = bet_timestamp(o.get("date"), o.get("time"))
        except Exception:
            bet_ts = 0
        item = {"ts": ts, "bet_ts": bet_ts, "target": target_name, **o}
        append_jsonl(p, item)


def extract_order_id(w: Dict[str, Any]) -> Optional[str]:
    for key in (
        "TID",
        "ORDERID",
        "ORDER_ID",
        "orderId",
        "order_id",
        "WID",
        "WAGERID",
        "WAGER_ID",
        "ID",
        "id",
    ):
        val = w.get(key)
        if val is None:
            continue
        s = str(val).strip()
        if s:
            return s
    return None


def bet_timestamp(date_str: Any, time_str: Any) -> int:
    # 后台常见：MM-DD-YYYY + HH:MM:SS
    d = str(date_str or "").strip()
    t = str(time_str or "").strip()
    m = re.match(r"^(\d{1,2})[-/](\d{1,2})[-/](\d{4})$", d)
    mt = re.match(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$", t)
    if not m or not mt:
        return 0
    mm, dd, yyyy = int(m.group(1)), int(m.group(2)), int(m.group(3))
    hh, mi, ss = int(mt.group(1)), int(mt.group(2)), int(mt.group(3) or 0)
    try:
        dt = datetime(yyyy, mm, dd, hh, mi, ss)
        return int(dt.timestamp())
    except Exception:
        return 0


def normalize_wager(w: Dict[str, Any]) -> Dict[str, Any]:
    # 尽量容错：字段不固定就保留 raw，消息里按优先级挑选常见字段。
    order_id = extract_order_id(w) or stable_id_from_obj(w)
    pick = lambda *keys: next((w.get(k) for k in keys if w.get(k) not in (None, "")), None)

    account = strip_html(pick("NAME0", "USER", "username", "ACCOUNT", "account"))
    stake = pick("GOLD", "STAKE", "stake", "BET", "bet", "AMOUNT", "amount")
    sport = strip_html(pick("GT", "SPORT", "sport"))

    league = strip_html(pick("SHOWTEXT_LEAGUE", "LEAGUE", "league"))
    sides = strip_html(pick("SHOWTEXT_TEAM", "SIDES", "sides"))
    if not sides:
        th = strip_html(pick("TEAM_H", "HOME", "home"))
        tc = strip_html(pick("TEAM_C", "AWAY", "away"))
        if th or tc:
            sides = f"{th} vs {tc}".strip()

    bet_type = strip_html(pick("WAGERSTYPE", "BETTYPE", "betType", "TYPE", "type"))

    # 盘口展示：优先从展示文本提取（包含球队+盘口），否则拼接 ORDER_TYPE + ORDER_CON
    selection_text = strip_html(pick("ORDER_TYPE", "ORDER", "ORDERTEAM", "TEAM"))
    order_con = strip_html(pick("ORDER_CON", "LINE", "line"))
    show_line_text = strip_html(pick("SHOWTEXT_ORDER_TYPE_IORATIO", "TNAME"))
    line = extract_selection_from_text(show_line_text) or (
        f"{selection_text} {order_con}".strip() if (selection_text or order_con) else ""
    )
    date = pick("DATE", "date", "WDATE", "BETDATE")
    time_ = pick("TIME", "time", "WTIME", "BETTIME")

    odds = pick("IORATIO", "ODDS", "odds")
    if odds in (None, ""):
        odds = extract_odds_from_text(show_line_text)

    return {
        "orderId": str(order_id),
        "account": account,
        "sport": sport,
        "stake": stake,
        "odds": strip_html(odds) if odds is not None else None,
        "league": league,
        "sides": sides,
        "betType": bet_type,
        "line": line,
        "date": date,
        "time": time_,
        "raw": w,
    }


def format_telegram_message(target_name: str, order: Dict[str, Any]) -> str:
    # 这条消息后续会被 follow_engine 用 editMessageText “就地更新”为“后台订单 + 跟单结果”的合并消息。
    dt = f"{(order.get('date') or '')} {(order.get('time') or '')}".strip()
    sport = str(order.get("sport") or "").strip()
    bet_type = str(order.get("betType") or "").strip()
    type_text = " ".join([x for x in [sport, bet_type] if x and x != "未知"]).strip() or bet_type or sport or "未知"
    return (
        f"<b>🎯 新注单提醒</b>\n\n"
        f"🧩 <b>后台：</b>{html.escape(str(target_name or '未知'))}\n"
        f"🆔 <b>单号：</b>{html.escape(str(order.get('orderId') or '未知'))}\n"
        f"👤 <b>账号：</b>{html.escape(str(order.get('account') or '未知'))}\n"
        f"🎯 <b>比赛：</b>{html.escape(str(order.get('sides') or '未知'))}\n"
        f"🏆 <b>联赛：</b>{html.escape(str(order.get('league') or '未知'))}\n"
        f"🎲 <b>类型：</b>{html.escape(type_text)}\n"
        f"💰 <b>金额：</b>{html.escape(str(order.get('stake') or '未知'))}\n"
        f"🎯 <b>玩法：</b>{html.escape(str(order.get('line') or '未知'))} @{html.escape(str(order.get('odds') or '未知'))}\n"
        f"\n⏳ <b>跟单中</b>（等待结果…）\n"
        f"\n{html.escape(now_cn())}"
    )


def format_console_order(target_name: str, order: Dict[str, Any]) -> str:
    parts = [
        f"后台={target_name}",
        f"账号={order.get('account') or '未知'}",
        f"单号={order.get('orderId')}",
        f"赛种={order.get('sport') or '未知'}",
        f"联赛={order.get('league') or '未知'}",
        f"比赛={order.get('sides') or '未知'}",
        f"类型={order.get('betType') or '未知'}",
        f"盘口={order.get('line') or '未知'}",
        f"赔率={order.get('odds') or '未知'}",
        f"金额={order.get('stake') or '未知'}",
        f"时间={(order.get('date') or '')} {(order.get('time') or '')}".strip(),
    ]
    return " | ".join(parts)


def tg_send(bot_token: str, chat_id: str, html: str, timeout_s: int = 10, proxy: str = "") -> Optional[int]:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    proxies = None
    if proxy:
        proxies = {"http": proxy, "https": proxy}
    r = requests.post(
        url,
        json={"chat_id": chat_id, "text": html, "parse_mode": "HTML", "disable_web_page_preview": True},
        timeout=timeout_s,
        proxies=proxies,
    )
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"telegram error: {data}")
    try:
        return int((data.get("result") or {}).get("message_id"))
    except Exception:
        return None


@dataclass
class Target:
    name: str
    base_url: str
    ver: str
    uid: str
    cookie: str
    login_layer: str = "ag"
    langx: str = "zh-cn"
    gtype: str = "ALL"
    total_bets: str = "wmc"
    sel_maxid: str = "0"
    telegram_chat_id: Optional[str] = None
    proxy: str = ""
    cooldown_on_m_rep: bool = True
    offline_alert_seconds: int = 60
    poll_mode: str = "requests"  # requests | playwright
    force_relogin_on_offline: bool = False
    relogin_mode: str = "none"  # none | playwright
    relogin_headless: bool = False
    relogin_timeout_seconds: int = 180
    relogin_min_interval_seconds: int = 60
    relogin_on_start: bool = False
    relogin_login_tab: int = 0  # 0=不切换；1/2/3=点击“登入1/2/3”
    write_cookie_to_config: bool = True
    username: str = ""
    password: str = ""
    security_code: str = ""

    @property
    def transform_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/transform.php?ver={self.ver}"

    @property
    def referer(self) -> str:
        return f"{self.base_url.rstrip('/')}/app/wmc/index.php"

    def form(self, sel_maxid: str) -> Dict[str, str]:
        return {
            "ver": self.ver,
            "login_layer": self.login_layer,
            "uid": self.uid,
            "langx": self.langx,
            "p": "get_wmc_list_bet",
            "totalBets": self.total_bets,
            "gtype": self.gtype,
            "sel_maxid": str(sel_maxid),
        }


def load_config(path: Path) -> Tuple[Dict[str, Any], List[Target]]:
    # 兼容某些编辑器写入的 UTF-8 BOM
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    targets = []
    for t in raw.get("targets", []):
        targets.append(
            Target(
                name=str(t["name"]),
                base_url=str(t.get("base_url") or "https://ag.hga030.com"),
                ver=str(t.get("ver") or "version-10-23"),
                uid=str(t.get("uid", "")),
                cookie=str(t.get("cookie", "")),
                login_layer=str(t.get("login_layer") or "ag"),
                langx=str(t.get("langx") or "zh-cn"),
                gtype=str(t.get("gtype") or "ALL"),
                total_bets=str(t.get("totalBets") or "wmc"),
                sel_maxid=str(t.get("sel_maxid") or "0"),
                telegram_chat_id=t.get("telegram_chat_id"),
                proxy=str(t.get("proxy") or raw.get("proxy") or ""),
                cooldown_on_m_rep=bool(t.get("cooldown_on_m_rep", True)),
                offline_alert_seconds=int(t.get("offline_alert_seconds", 60)),
                poll_mode=str(t.get("poll_mode", "requests") or "requests"),
                force_relogin_on_offline=bool(t.get("force_relogin_on_offline", False)),
                relogin_mode=str(t.get("relogin_mode", "none") or "none"),
                relogin_headless=bool(t.get("relogin_headless", False)),
                relogin_timeout_seconds=int(t.get("relogin_timeout_seconds", 180)),
                relogin_min_interval_seconds=int(t.get("relogin_min_interval_seconds", 60)),
                relogin_on_start=bool(t.get("relogin_on_start", False)),
                relogin_login_tab=int(t.get("relogin_login_tab", 0) or 0),
                write_cookie_to_config=bool(t.get("write_cookie_to_config", True)),
                username=str(t.get("username", "") or ""),
                password=str(t.get("password", "") or ""),
                security_code=str(t.get("security_code", "") or ""),
            )
        )
    return raw, targets


def file_mtime(path: Path) -> float:
    try:
        return float(path.stat().st_mtime)
    except Exception:
        return 0.0


def update_target_in_place(dst: Target, src: Target) -> None:
    # name 作为唯一键，不在这里更新
    dst.base_url = src.base_url or dst.base_url
    dst.ver = src.ver or dst.ver
    if src.uid:
        dst.uid = src.uid
    if src.cookie:
        dst.cookie = src.cookie
    if src.login_layer:
        dst.login_layer = src.login_layer
    dst.langx = src.langx or dst.langx
    dst.gtype = src.gtype or dst.gtype
    dst.total_bets = src.total_bets or dst.total_bets
    dst.sel_maxid = src.sel_maxid or dst.sel_maxid
    dst.proxy = src.proxy or dst.proxy
    dst.cooldown_on_m_rep = bool(src.cooldown_on_m_rep)
    dst.offline_alert_seconds = int(src.offline_alert_seconds)
    dst.poll_mode = src.poll_mode or dst.poll_mode
    dst.force_relogin_on_offline = bool(src.force_relogin_on_offline)
    dst.relogin_mode = src.relogin_mode or dst.relogin_mode
    dst.relogin_headless = bool(src.relogin_headless)
    dst.relogin_timeout_seconds = int(src.relogin_timeout_seconds)
    dst.relogin_min_interval_seconds = int(src.relogin_min_interval_seconds)
    dst.relogin_on_start = bool(src.relogin_on_start)
    dst.relogin_login_tab = int(src.relogin_login_tab)
    dst.write_cookie_to_config = bool(src.write_cookie_to_config)
    dst.username = src.username or dst.username
    dst.password = src.password or dst.password
    dst.security_code = src.security_code or dst.security_code
    if src.telegram_chat_id is not None:
        dst.telegram_chat_id = src.telegram_chat_id


def state_path(state_dir: Path, target_name: str) -> Path:
    return state_dir / f"{safe_name(target_name)}.json"


def load_state(state_dir: Path, target_name: str) -> Dict[str, Any]:
    p = state_path(state_dir, target_name)
    if not p.exists():
        return {
            "sel_maxid": "0",
            "seen": [],
            "cooldown_until": 0,
            "m_rep_count": 0,
            "last_offline_alert_at": 0,
            "last_relogin_at": 0,
            "cookie": "",
            "last_poll_at": 0,
            "last_ok_at": 0,
            "last_status": "",
            "last_error_code": "",
            "last_error_msg": "",
            "relogin_in_progress": 0,
            "relogin_started_at": 0,
            # 新账号首次运行：先同步 maxid，不把历史单当作新单
            "initialized": 0,
        }
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(obj, dict):
            raise ValueError("bad state")
        # 兼容旧状态文件：补齐新字段
        obj.setdefault("last_poll_at", 0)
        obj.setdefault("last_ok_at", 0)
        obj.setdefault("last_status", "")
        obj.setdefault("last_error_code", "")
        obj.setdefault("last_error_msg", "")
        obj.setdefault("relogin_in_progress", 0)
        obj.setdefault("relogin_started_at", 0)
        # 新账号首次运行时跳过历史订单
        obj.setdefault("initialized", 1)
        return obj
    except Exception:
        return {
            "sel_maxid": "0",
            "seen": [],
            "cooldown_until": 0,
            "m_rep_count": 0,
            "last_offline_alert_at": 0,
            "last_relogin_at": 0,
            "cookie": "",
            "last_poll_at": 0,
            "last_ok_at": 0,
            "last_status": "",
            "last_error_code": "",
            "last_error_msg": "",
            "relogin_in_progress": 0,
            "relogin_started_at": 0,
            "initialized": 0,
        }


def save_state(state_dir: Path, target_name: str, state: Dict[str, Any]) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    p = state_path(state_dir, target_name)
    p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def request_wagers(session: requests.Session, target: Target, sel_maxid: str, timeout_s: int) -> Dict[str, Any]:
    headers = dict(DEFAULT_HEADERS)
    headers["origin"] = target.base_url
    headers["referer"] = target.referer
    headers["content-type"] = "application/x-www-form-urlencoded; charset=UTF-8"
    headers["cookie"] = target.cookie

    ensure_local_proxy_available(target)
    proxies = None
    if target.proxy:
        proxies = {"http": target.proxy, "https": target.proxy}
    else:
        env_proxies = get_environ_proxies(target.transform_url) or {}
        if env_proxies:
            proxies = env_proxies

    connect_timeout = min(10, max(1, int(timeout_s)))
    timeout = (connect_timeout, max(1, int(timeout_s)))

    r = session.post(
        target.transform_url,
        data=target.form(sel_maxid),
        headers=headers,
        timeout=timeout,
        proxies=proxies,
        allow_redirects=False,
    )
    # 302/301 往往表示被打回登录页
    if r.status_code in (301, 302, 303, 307, 308):
        loc = r.headers.get("location") or ""
        return {"status": "error", "code": "login_required", "msg": f"redirect:{loc}"}
    r.raise_for_status()
    text = (r.text or "").strip()
    try:
        return json.loads(text)
    except Exception:
        # 便于你调试：把前200字符打印出来（有时会返回登录页HTML）
        return {"status": "error", "code": "non_json", "msg": text[:200]}


def cookie_str_to_playwright_cookies(cookie_str: str, base_url: str) -> List[Dict[str, Any]]:
    # cookie header: "a=1; b=2" -> playwright cookies list (best-effort)
    items: List[Dict[str, Any]] = []
    for part in (cookie_str or "").split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, value = part.split("=", 1)
        name = name.strip()
        value = value.strip()
        if not name:
            continue
        items.append({"name": name, "value": value, "url": base_url})
    return items


class PlaywrightPool:
    def __init__(self, state_dir: Path, debug: bool) -> None:
        try:
            from playwright.sync_api import sync_playwright  # type: ignore
        except Exception as e:
            raise RuntimeError(
                "未安装 playwright，无法启用 poll_mode=playwright。请先执行："
                "python -m pip install -r requirements.txt 以及 python -m playwright install chromium"
            ) from e
        self._state_dir = state_dir
        self._debug = debug
        self._p = sync_playwright().start()
        self._contexts: Dict[str, Any] = {}

    def close(self) -> None:
        for name in list(self._contexts.keys()):
            self.close_target(name)
        try:
            self._p.stop()
        except Exception:
            pass

    def pw(self) -> Any:
        return self._p

    def close_target(self, target_name: str) -> None:
        ctx = self._contexts.pop(target_name, None)
        if ctx is None:
            return
        try:
            ctx.close()
        except Exception:
            pass

    def recreate(self, target: Target) -> Any:
        self.close_target(target.name)
        return self.get(target)

    def get(self, target: Target) -> Any:
        ctx = self._contexts.get(target.name)
        if ctx is not None:
            return ctx

        base = target.base_url.rstrip("/")
        user_data_dir = self._state_dir / "_pw" / safe_name(target.name)
        profile_has_data = user_data_dir.exists() and any(user_data_dir.iterdir())
        user_data_dir.mkdir(parents=True, exist_ok=True)

        ensure_local_proxy_available(target)
        proxy_server = target.proxy or (get_environ_proxies(base).get("https") or get_environ_proxies(base).get("http") or "")
        proxy = build_playwright_proxy(proxy_server)
        headless = bool(target.relogin_headless)
        if headless and not profile_has_data:
            headless = False
            print(
                f"[{now_cn()}] [{target.name}] 检测到 Playwright 会话目录为空；为便于首次登录/验证码处理，本次临时使用有头模式，登录成功后会话会被复用，再切回 headless 即可。",
                flush=True,
            )
        ctx = self._p.chromium.launch_persistent_context(
            str(user_data_dir),
            headless=headless,
            viewport={"width": 1280, "height": 860},
            ignore_https_errors=True,
            args=["--disable-blink-features=AutomationControlled", "--ignore-certificate-errors"],
            proxy=proxy,
        )

        # best-effort: seed cookies from config for API requests
        if target.cookie:
            try:
                ctx.add_cookies(cookie_str_to_playwright_cookies(target.cookie, base_url=base))
            except Exception:
                pass

        if self._debug:
            print(f"[{now_cn()}] [{target.name}] poll_mode=playwright 已启用", flush=True)

        self._contexts[target.name] = ctx
        return ctx


def request_wagers_playwright(ctx: Any, target: Target, sel_maxid: str, timeout_s: int) -> Dict[str, Any]:
    headers = dict(DEFAULT_HEADERS)
    headers["origin"] = target.base_url
    headers["referer"] = target.referer
    headers["content-type"] = "application/x-www-form-urlencoded; charset=UTF-8"

    timeout_ms = max(1_000, int(timeout_s) * 1000)
    r = ctx.request.post(
        target.transform_url,
        form=target.form(sel_maxid),
        headers=headers,
        timeout=timeout_ms,
        max_redirects=0,
        ignore_https_errors=True,
    )
    if r.status in (301, 302, 303, 307, 308):
        loc = (r.headers or {}).get("location") or (r.headers or {}).get("Location") or ""
        return {"status": "error", "code": "login_required", "msg": f"redirect:{loc}"}
    if r.status >= 400:
        return {"status": "error", "code": "http_error", "msg": f"status={r.status}"}
    text = (r.text() or "").strip()
    try:
        return json.loads(text)
    except Exception:
        return {"status": "error", "code": "non_json", "msg": text[:200]}


def relogin_via_playwright(target: Target, *, state_dir: Path, debug: bool, pw: Any = None) -> Dict[str, Any]:
    # pw arg is ignored to match hdd3j behavior (fresh instance every time)
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except Exception as e:
        raise RuntimeError(
            "未安装 playwright：请先执行 python -m pip install -r requirements.txt "
            "以及 python -m playwright install chromium"
        ) from e

    base = target.base_url.rstrip("/")
    host = urlparse(base).hostname or ""
    user_data_dir = state_dir / "_pw" / target.name
    user_data_dir.mkdir(parents=True, exist_ok=True)

    ensure_local_proxy_available(target)
    if debug:
        print(f"[{now_cn()}] [{target.name}] 开始 Playwright 重登（登入{target.relogin_login_tab}）", flush=True)

    def do_login(p: Any) -> Dict[str, Any]:
        proxy = build_playwright_proxy(target.proxy)
        try:
            ctx = p.chromium.launch_persistent_context(
                str(user_data_dir),
                headless=bool(target.relogin_headless),
                viewport={"width": 1280, "height": 860},
                ignore_https_errors=True,
                args=["--disable-blink-features=AutomationControlled", "--ignore-certificate-errors"],
                proxy=proxy,
            )
        except Exception as e:
            browsers = (os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or "").strip()
            hint: List[str] = []
            if not browsers:
                hint.append("PLAYWRIGHT_BROWSERS_PATH 未设置")
            elif browsers in ("0", "1"):
                hint.append(f"PLAYWRIGHT_BROWSERS_PATH={browsers}（特殊值）")
            else:
                ok_dir = False
                try:
                    ok_dir = Path(browsers).is_dir()
                except Exception:
                    ok_dir = False
                hint.append(f"PLAYWRIGHT_BROWSERS_PATH={browsers}")
                if not ok_dir:
                    hint.append("该目录不存在/不可访问")
            hint.append("可移植包请用 Start_AG_Matrix.bat 启动，并确认包根目录存在 ms-playwright/")
            raise RuntimeError(f"Playwright 启动浏览器失败：{type(e).__name__}: {e}；{'；'.join(hint)}") from e
        try:
            page = ctx.new_page()
            goto_timeout_ms = max(60_000, int(target.relogin_timeout_seconds) * 1000)
            try:
                page.goto(base + "/", wait_until="domcontentloaded", timeout=goto_timeout_ms)
            except Exception:
                page.goto(base + "/", wait_until="commit", timeout=goto_timeout_ms)

            tab = int(target.relogin_login_tab or 0)
            if tab in (1, 2, 3):
                id_map = {1: "btn_A", 2: "btn_B", 3: "btn_C"}
                tid = id_map.get(tab)
                selectors = [
                    f"#{tid}",
                    f"li#{tid}",
                    f"text=登入{tab}",
                    f"text=登入 {tab}",
                    f"text=登录{tab}",
                    f"text=登录 {tab}",
                ]
                for _ in range(3):
                    for sel in selectors:
                        try:
                            loc = page.locator(sel).first
                            if loc.count() == 0:
                                continue
                            loc.click(timeout=2_000)
                            break
                        except Exception:
                            continue
                    time.sleep(0.4)

            if target.username and target.password:
                try:
                    user_loc = page.locator(
                        "#username, input[name='username'], input[placeholder*='账号'], input[placeholder*='帳號'], input[placeholder*='用户名']"
                    ).first
                    pwd_loc = page.locator(
                        "#pwd, input[name='pwd'], input[type='password'], input[placeholder*='密码'], input[placeholder*='密碼']"
                    ).first
                    user_loc.fill(target.username, timeout=10_000)
                    pwd_loc.fill(target.password, timeout=10_000)
                    if target.security_code:
                        try:
                            sc_loc = page.locator(
                                "input[placeholder*='安全'], input[placeholder*='验证码'], input[placeholder*='驗證碼'], #scode, #security_code, input[name='scode'], input[name='security_code'], input[name='code']"
                            ).first
                            sc_loc.fill(target.security_code, timeout=5_000)
                        except Exception:
                            pass
                    btn = page.locator(
                        "#loginBtn, .lg_login_btn, button:has-text('登录'), button:has-text('登入'), input[value*='登录'], input[value*='登入']"
                    ).first
                    btn.click(timeout=10_000)
                except Exception:
                    if debug:
                        print(f"[{now_cn()}] [{target.name}] 自动填充失败，请手动完成登录/验证码", flush=True)

            deadline = time.time() + max(30, int(target.relogin_timeout_seconds))
            got_cookie = False
            last_uid = ""
            last_layer = ""

            def read_top_vars() -> Tuple[str, str]:
                uid_val = ""
                layer_val = ""
                for fr in page.frames:
                    try:
                        uid = fr.evaluate("() => (window.top && window.top.uid) ? String(window.top.uid) : ''")
                        if uid and len(str(uid)) > len(uid_val):
                            uid_val = str(uid)
                    except Exception:
                        pass
                    try:
                        layer = fr.evaluate(
                            "() => (window.top && window.top.login_layer) ? String(window.top.login_layer) : ''"
                        )
                        if layer and len(str(layer)) > len(layer_val):
                            layer_val = str(layer)
                    except Exception:
                        pass
                return uid_val, layer_val

            while time.time() < deadline:
                cookies = ctx.cookies()
                got_cookie = any("lpsid" in (c.get("name") or "").lower() for c in cookies)
                uid, layer = read_top_vars()
                if uid:
                    last_uid = uid
                if layer:
                    last_layer = layer
                if got_cookie:
                    break
                time.sleep(1)

            if not got_cookie:
                raise RuntimeError("重登超时：未检测到 LPSID Cookie（可能需要手动验证码）")

            cookie_str = dedupe_cookies(cookies, host=host)
            return {"cookie": cookie_str, "uid": last_uid, "login_layer": last_layer or target.login_layer}
        finally:
            ctx.close()

    with sync_playwright() as p:
        return do_login(p)


def poll_once(
    session: requests.Session,
    target: Target,
    state_dir: Path,
    config_path: Path,
    bot_token: str,
    default_chat_id: str,
    timeout_s: int,
    tg_timeout_s: int,
    tg_proxy: str,
    debug: bool,
    only_orders: bool,
    orders_dir: Path,
    interval_s: int,
) -> None:
    state = load_state(state_dir, target.name)
    poll_mode = (target.poll_mode or "requests").lower()
    interval_s = max(1, int(interval_s))
    timeout_s = max(1, int(timeout_s))

    def do_request(sel: str) -> Dict[str, Any]:
        if poll_mode == "playwright":
            # For poll_mode=playwright, we launch a new context for each request
            # to ensure isolation and avoid state issues from long-lived contexts.
            # This is different from the PlaywrightPool approach.
            try:
                from playwright.sync_api import sync_playwright  # type: ignore
            except Exception as e:
                raise RuntimeError(
                    "未安装 playwright，无法启用 poll_mode=playwright。请先执行："
                    "python -m pip install -r requirements.txt 以及 python -m playwright install chromium"
                ) from e

            base = target.base_url.rstrip("/")
            user_data_dir = state_dir / "_pw" / safe_name(target.name)
            user_data_dir.mkdir(parents=True, exist_ok=True)

            with sync_playwright() as p:
                proxy_server = target.proxy or (get_environ_proxies(base).get("https") or get_environ_proxies(base).get("http") or "")
                proxy = build_playwright_proxy(proxy_server)
                ctx = p.chromium.launch_persistent_context(
                    str(user_data_dir),
                    headless=True, # Always headless for polling
                    viewport={"width": 1280, "height": 860},
                    ignore_https_errors=True,
                    args=["--disable-blink-features=AutomationControlled", "--ignore-certificate-errors"],
                    proxy=proxy,
                )
                try:
                    # best-effort: seed cookies from config for API requests
                    if target.cookie:
                        try:
                            ctx.add_cookies(cookie_str_to_playwright_cookies(target.cookie, base_url=base))
                        except Exception:
                            pass
                    return request_wagers_playwright(ctx, target, sel_maxid=sel, timeout_s=timeout_s)
                finally:
                    ctx.close()
        return request_wagers(session, target, sel_maxid=sel, timeout_s=timeout_s)

    # 若自动重登刷新过 Cookie，优先使用 state 中的最新 Cookie
    if isinstance(state, dict) and state.get("cookie"):
        target.cookie = str(state.get("cookie") or target.cookie)

    data_dir = config_path.parent / "data"
    status_push_enabled, _, _, _ = load_tg_push_config(config_path)
    if target.cooldown_on_m_rep:
        cooldown_until = float(state.get("cooldown_until") or 0)
        if cooldown_until and now_ts() < cooldown_until:
            if debug:
                remain = int(cooldown_until - now_ts())
                print(f"[{now_cn()}] [{target.name}] 冷却中，跳过本轮（剩余 {remain}s）", flush=True)
            return

    sel_maxid = str(state.get("sel_maxid") or target.sel_maxid or "0")
    seen: List[str] = list(state.get("seen") or [])
    seen_set = set(seen)

    if debug and not only_orders:
        env_proxies = get_environ_proxies(target.transform_url) or {}
        eff_proxy = target.proxy or (env_proxies.get("https") or env_proxies.get("http") or "")
        proxy_hint = eff_proxy if eff_proxy else "（无）"
        print(
            f"[{now_cn()}] [{target.name}] 请求 sel_maxid={sel_maxid} "
            f"mode={poll_mode} proxy={proxy_hint} cookie={redact_cookie(target.cookie)}"
        , flush=True)

    # 记录本轮发起请求时间（用于 WebUI 判断在线/离线）
    # 并在站点提示 ver 需要更新时自动更新后重试一次
    data: Dict[str, Any]
    for attempt in range(2):
        state["last_poll_at"] = now_ts()
        # 动态 TTL：让 WebUI 状态灯更快响应（避免写死 90s 导致“红灯太慢”）
        # 这里偏“快”：约 3 个轮询间隔后就判离线（避免卡住时 UI 还绿很久）
        state["heartbeat_ttl_s"] = float(max(6, (3 * interval_s) + 2))
        # 先落盘一次，避免请求卡住导致 WebUI 看起来“完全不动”
        save_state(state_dir, target.name, state)

        data = do_request(sel_maxid)
        if isinstance(data, dict) and data.get("status") == "error":
            code = str(data.get("code") or "").strip()
            if code == "4X042":
                new_ver = str(data.get("v") or "").strip()
                if new_ver and new_ver != target.ver and attempt == 0:
                    # 站点返回：{"status":"error","code":"4X042","v":"version-xx-xx"}
                    target.ver = new_ver
                    state["last_error_code"] = "ver_update"
                    state["last_error_msg"] = f"server_requires_ver:{new_ver}"
                    save_state(state_dir, target.name, state)
                    update_config_target(config_path, target.name, {"ver": new_ver})
                    if debug and not only_orders:
                        print(f"[{now_cn()}] [{target.name}] ver 已更新为 {new_ver}，重试请求", flush=True)
                    continue
        break

    # 站点会返回 {"status":"error","code":"m_rep","msg":"系统忙碌中..."}，需要退避避免持续触发
    if isinstance(data, dict) and data.get("status") == "error":
        code = str(data.get("code") or "")
        msg = _normalize_error_msg(code, str(data.get("msg") or ""))
        state["last_error_code"] = code
        state["last_error_msg"] = msg
        state["consecutive_errors"] = int(state.get("consecutive_errors") or 0) + 1
        # 除了明确 online 之外，一律不要保持 online（避免 UI 误判）
        if state.get("last_status") != "relogin":
            state["last_status"] = "offline"
        if debug and not only_orders:
            print(f"[{now_cn()}] [{target.name}] 错误响应: code={code} msg={msg}", flush=True)
        force_any = bool(getattr(target, "force_relogin_on_offline", False))
        # 只在“明确被踢/掉线”时才重登；网络抖动/服务端忙碌不应该触发重登（避免把其它正常账号也拉去重登）
        need_relogin = looks_like_logged_out(data)
        if need_relogin:
            state["last_status"] = "offline"
            # 1) 优先尝试自动重新登录
            mode = (target.relogin_mode or "none").lower()
            now = now_ts()
            last_relogin = float(state.get("last_relogin_at") or 0)
            min_gap = max(5, int(target.relogin_min_interval_seconds))
            if force_any and mode in ("playwright", "auto"):
                # 强制重登：将最小间隔下调到 10 秒，保证响应快但不至于疯狂刷登录
                min_gap = 10
            can_relogin = mode in ("playwright", "auto") and (now - last_relogin) >= min_gap
            if can_relogin:
                state["last_relogin_at"] = now
                state["last_status"] = "relogin"
                state["relogin_in_progress"] = 1
                state["relogin_started_at"] = now
                save_state(state_dir, target.name, state)
                try:
                    last_tg = float(state.get("last_tg_relogin_start_at") or 0)
                    if (now - last_tg) >= 5:
                        log_and_maybe_push(
                            config_path=config_path,
                            data_dir=data_dir,
                            kind="backend",
                            account=target.name,
                            status="relogin",
                            message=f"账号掉线，开始重新登录（code={code}）",
                            extra={"code": code, "base_url": target.base_url},
                        )
                        if status_push_enabled:
                            state["last_tg_relogin_start_at"] = now
                            save_state(state_dir, target.name, state)
                except Exception as e:
                    print(f"[{now_cn()}] [{target.name}] Telegram 重登开始通知失败: {e}", flush=True)
                try:
                    res = relogin_via_playwright(
                        target,
                        state_dir=state_dir,
                        debug=debug,
                    )
                    new_cookie = str(res.get("cookie") or "").strip()
                    new_uid = str(res.get("uid") or "").strip()
                    new_layer = str(res.get("login_layer") or "").strip()
                    if new_cookie:
                        target.cookie = new_cookie
                        if new_uid:
                            target.uid = new_uid
                        if new_layer:
                            target.login_layer = new_layer
                        state["cookie"] = new_cookie
                        # 登录态刷新后，部分站点会让 sel_maxid 失效/回卷，导致本轮把历史订单当成新单。
                        # 策略：强制下一轮“初始化同步 maxid”（仅同步，不推送/不落盘订单）。
                        state["initialized"] = 0
                        state["seen"] = []
                        # UI 体验：重登成功但尚未完成一次成功拉单前，保持黄灯（relogin），避免“明明登录成功却红灯”误解
                        state["last_status"] = "relogin"
                        state["relogin_started_at"] = now_ts()
                        state["last_error_code"] = ""
                        state["last_error_msg"] = ""
                        save_state(state_dir, target.name, state)
                        if target.write_cookie_to_config:
                            update_config_target(
                                config_path,
                                target.name,
                                {
                                    "cookie": new_cookie,
                                    "uid": (new_uid or None),
                                    "login_layer": (new_layer or None),
                                },
                            )
                        try:
                            now_ok = now_ts()
                            last_ok = float(state.get("last_tg_relogin_ok_at") or 0)
                            if (now_ok - last_ok) >= 5:
                                log_and_maybe_push(
                                    config_path=config_path,
                                    data_dir=data_dir,
                                    kind="backend",
                                    account=target.name,
                                    status="online",
                                    message="重新登录成功",
                                    extra={"base_url": target.base_url},
                                )
                                if status_push_enabled:
                                    state["last_tg_relogin_ok_at"] = now_ok
                                    save_state(state_dir, target.name, state)
                        except Exception as e:
                            print(f"[{now_cn()}] [{target.name}] Telegram 重登成功通知失败: {e}", flush=True)
                        if debug and not only_orders:
                            print(
                                f"[{now_cn()}] [{target.name}] 已重新登录并刷新 Cookie/uid/login_layer，准备继续轮询",
                                flush=True,
                            )
                        # 重新拉一次，避免等待到下一轮
                        # No need to recreate pw_pool context as it's not pooled anymore
                        data = do_request(sel_maxid)
                    else:
                        raise RuntimeError("重新登录未获得 Cookie")
                except Exception as e:
                    state["last_status"] = "offline"
                    state["last_error_code"] = "relogin_failed"
                    state["last_error_msg"] = str(e)
                    print(f"[{now_cn()}] [{target.name}] 自动重新登录失败: {e}", flush=True)
                    try:
                        now_fail = now_ts()
                        last_fail = float(state.get("last_tg_relogin_fail_at") or 0)
                        if (now_fail - last_fail) >= 5:
                            log_and_maybe_push(
                                config_path=config_path,
                                data_dir=data_dir,
                                kind="backend",
                                account=target.name,
                                status="failed",
                                message=f"重新登录失败：{str(e)[:200]}",
                                extra={"base_url": target.base_url},
                            )
                            if status_push_enabled:
                                state["last_tg_relogin_fail_at"] = now_fail
                                save_state(state_dir, target.name, state)
                    except Exception as e2:
                        print(f"[{now_cn()}] [{target.name}] Telegram 重登失败通知失败: {e2}", flush=True)
                finally:
                    state["relogin_in_progress"] = 0
                    save_state(state_dir, target.name, state)

            if isinstance(data, dict) and data.get("status") == "error":
                # 2) 无法自动重登或重登失败：提示你手动登录刷新 Cookie（限频）
                last = float(state.get("last_offline_alert_at") or 0)
                now2 = now_ts()
                should_alert = (now2 - last) >= max(5, int(target.offline_alert_seconds))
                if should_alert:
                    state["last_offline_alert_at"] = now2
                    save_state(state_dir, target.name, state)
                    console_text = f"掉线：{target.name}（code={code}）自动重登失败，请手动登录刷新 cookie"
                    tg_text = f"{console_text}"
                    print(f"[{now_cn()}] [{target.name}] {console_text}", flush=True)
                    try:
                        log_and_maybe_push(
                            config_path=config_path,
                            data_dir=data_dir,
                            kind="backend",
                            account=target.name,
                            status="alert",
                            message=tg_text,
                            extra={"code": code, "base_url": target.base_url},
                        )
                    except Exception as e:
                        print(f"[{now_cn()}] [{target.name}] Telegram 掉线通知失败: {e}", flush=True)
                elif debug:
                    if not only_orders:
                        remain = max(0, int(max(5, int(target.offline_alert_seconds)) - (now2 - last)))
                        print(f"[{now_cn()}] [{target.name}] 掉线：已提示过，将在 {remain}s 后再次提示", flush=True)
                save_state(state_dir, target.name, state)
                return
        if isinstance(data, dict) and data.get("status") == "error":
            # 兼容：部分站点用 0X00x 表示“系统忙/流量高”，等价于 m_rep，需退避避免持续触发。
            if (code == "m_rep" or code in _BUSY_CODE_MSG) and target.cooldown_on_m_rep:
                n = int(state.get("m_rep_count") or 0) + 1
                state["m_rep_count"] = n
                cooldown = min(300, 30 * (2 ** (n - 1)))
                state["cooldown_until"] = now_ts() + cooldown
            # 无论是什么 error，都要落盘，便于 WebUI 显示原因/触发重登策略
            save_state(state_dir, target.name, state)
            return

    # 常见结构：{ "maxid": ..., "wagers": [...] }
    maxid = data.get("maxid") if isinstance(data, dict) else None
    wagers = data.get("wagers") if isinstance(data, dict) else None
    if not isinstance(wagers, list):
        if debug:
            keys = list(data.keys()) if isinstance(data, dict) else type(data)
            print(f"[{now_cn()}] [{target.name}] 未找到 wagers 列表，data keys/type: {keys}", flush=True)
            if isinstance(data, dict) and any(k in data for k in ("status", "code", "msg")):
                print(f"[{now_cn()}] [{target.name}] 返回内容: {data}", flush=True)
        state["last_error_code"] = "bad_response"
        state["last_error_msg"] = str(type(data))
        save_state(state_dir, target.name, state)
        return

    if debug and not only_orders:
        print(f"[{now_cn()}] [{target.name}] 响应摘要: maxid={maxid} wagers={len(wagers)}", flush=True)

    state["last_ok_at"] = now_ts()
    state["last_status"] = "online"
    state["last_error_code"] = ""
    state["last_error_msg"] = ""
    state["consecutive_errors"] = 0
    state["m_rep_count"] = 0
    state["cooldown_until"] = 0
    # 若之前进入过重登流程或状态文件残留标记，一旦成功拉单就应清除黄灯
    state["relogin_in_progress"] = 0

    # 首次初始化：只同步 maxid，不把返回列表当作新注单（避免启动/新增账号时把历史单刷出来）
    if not int(state.get("initialized") or 0):
        if maxid is not None:
            state["sel_maxid"] = str(maxid)
        state["seen"] = []
        state["initialized"] = 1
        save_state(state_dir, target.name, state)
        return

    new_orders: List[Dict[str, Any]] = []
    for w in wagers:
        if not isinstance(w, dict):
            continue
        order = normalize_wager(w)
        oid = str(order["orderId"])
        if oid in seen_set:
            continue
        new_orders.append(order)
        seen_set.add(oid)
        seen.append(oid)

    # 限制 seen 规模，避免状态文件无限增长
    max_seen = 500
    if len(seen) > max_seen:
        seen = seen[-max_seen:]

    # 推送（优先推送，拿到 message_id 后再持久化，便于 follow_engine 合并成“同一条消息”）
    chat_id = target.telegram_chat_id or default_chat_id
    # 控制台输出：有新注单时打印明细（debug模式必打印；未配置Telegram时也打印，避免“只看到1条”）
    if new_orders and (debug or not (bot_token and chat_id)):
        for order in reversed(new_orders):
            print(f"[{now_cn()}] [订单] {format_console_order(target.name, order)}", flush=True)

    if bot_token and chat_id:
        for order in reversed(new_orders):  # 从旧到新推送
            html = format_telegram_message(target.name, order)
            try:
                mid = tg_send(
                    bot_token,
                    chat_id,
                    html,
                    timeout_s=tg_timeout_s,
                    proxy=(tg_proxy or target.proxy or ""),
                )
                if mid:
                    order["_tg_message_id"] = mid
                    order["_tg_chat_id"] = chat_id
            except Exception as e:
                if debug:
                    print(f"[{now_cn()}] [{target.name}] Telegram 推送失败: {e}", flush=True)

    # 持久化订单：供 WebUI/回溯使用（包含 _tg_message_id/_tg_chat_id 以支持合并推送）
    if new_orders:
        persist_orders(orders_dir, target.name, new_orders)

    if debug and not only_orders and new_orders:
        print(f"[{now_cn()}] [{target.name}] 新注单 {len(new_orders)} 条，maxid={maxid}", flush=True)
    elif debug and not only_orders:
        print(f"[{now_cn()}] [{target.name}] 无新注单", flush=True)

    if maxid is not None:
        state["sel_maxid"] = str(maxid)
    state["seen"] = seen
    save_state(state_dir, target.name, state)


def maybe_relogin_on_start(
    *,
    targets: List[Target],
    force: bool,
    state_dir: Path,
    cfg_path: Path,
    debug: bool,
) -> None:
    for t in targets:
        want = bool(force or t.relogin_on_start)
        if not want:
            continue
        if (t.relogin_mode or "none").lower() not in ("playwright", "auto"):
            continue
        st = load_state(state_dir, t.name)
        last = float(st.get("last_relogin_at") or 0)
        now = now_ts()
        if (now - last) < max(5, int(t.relogin_min_interval_seconds)):
            if debug:
                remain = int(max(5, int(t.relogin_min_interval_seconds)) - (now - last))
                print(f"[{now_cn()}] [{t.name}] 启动重登：距离上次过近，跳过（剩余 {remain}s）", flush=True)
            continue
        try:
            res = relogin_via_playwright(t, state_dir=state_dir, debug=debug)
            new_cookie = str(res.get("cookie") or "").strip()
            new_uid = str(res.get("uid") or "").strip()
            new_layer = str(res.get("login_layer") or "").strip()
            if not new_cookie:
                raise RuntimeError("未获得 Cookie")
            t.cookie = new_cookie
            if new_uid:
                t.uid = new_uid
            if new_layer:
                t.login_layer = new_layer
            st["cookie"] = new_cookie
            st["last_relogin_at"] = now_ts()
            # 启动重登后：下一轮先初始化同步 maxid，避免把历史订单当作新单推送/跟单
            st["initialized"] = 0
            st["seen"] = []
            # UI 体验：启动重登完成后先显示黄灯，直到成功拉单一次
            st["last_status"] = "relogin"
            st["relogin_started_at"] = now_ts()
            save_state(state_dir, t.name, st)
            if t.write_cookie_to_config:
                update_config_target(
                    cfg_path,
                    t.name,
                    {"cookie": new_cookie, "uid": (new_uid or None), "login_layer": (new_layer or None)},
                )
            if debug:
                print(f"[{now_cn()}] [{t.name}] 启动重登完成", flush=True)
        except Exception as e:
            # 重要：把失败原因落盘到 state，避免 WebUI 只看到“offline/0X001”而误以为没触发登录。
            try:
                st["last_status"] = "offline"
                st["last_error_code"] = "relogin_failed"
                st["last_error_msg"] = str(e)
                st["relogin_in_progress"] = 0
                save_state(state_dir, t.name, st)
            except Exception:
                pass
            print(f"[{now_cn()}] [{t.name}] 启动重登失败: {e}", file=sys.stderr, flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="AG 注单监听（纯HTTP轮询 transform.php）")
    parser.add_argument("--config", default="config.json", help="配置文件路径（JSON）")
    parser.add_argument("--state-dir", default="state", help="状态目录（保存 sel_maxid/去重）")
    parser.add_argument("--orders-dir", default="orders", help="订单落盘目录（jsonl，用于WebUI展示）")
    parser.add_argument("--interval", type=int, default=5, help="轮询间隔秒")
    parser.add_argument("--timeout", type=int, default=20, help="请求超时秒（读超时为主，默认20）")
    parser.add_argument("--tg-timeout", type=int, default=10, help="Telegram 超时秒")
    parser.add_argument("--once", action="store_true", help="只请求一次后退出（便于调试）")
    parser.add_argument("--only-orders", action="store_true", help="只输出订单/掉线/错误（降低日志噪声）")
    parser.add_argument(
        "--relogin-on-start",
        action="store_true",
        help="启动时强制执行一次自动重新登录（需要 relogin_mode=playwright）",
    )
    parser.add_argument("--debug", action="store_true", help="输出调试信息")
    args = parser.parse_args()

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        print(f"配置文件不存在: {cfg_path}", file=sys.stderr, flush=True)
        return 2

    raw_cfg, targets = load_config(cfg_path)
    if not targets:
        print("配置中未找到 targets", file=sys.stderr, flush=True)
        return 2

    bot_token = str(raw_cfg.get("telegram_bot_token") or os.getenv("TELEGRAM_BOT_TOKEN") or "")
    default_chat_id = str(raw_cfg.get("telegram_chat_id") or os.getenv("TELEGRAM_CHAT_ID") or "")
    tg_proxy = str(raw_cfg.get("telegram_proxy") or os.getenv("TELEGRAM_PROXY") or "")

    state_dir = Path(args.state_dir)
    orders_dir = Path(args.orders_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    orders_dir.mkdir(parents=True, exist_ok=True)

    lock_path = state_dir / "ag_wmc_watch.lock"
    if not acquire_single_instance_lock(lock_path):
        print(f"[{now_cn()}] ag_wmc_watch 已在运行，退出 lock={lock_path}", flush=True)
        return 2

    sess = requests.Session()
    retry = Retry(
        # 避免在代理/网络异常时长时间卡住（会导致 WebUI 一直显示离线，且无法触发重登）
        total=0,
        connect=0,
        read=0,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["POST"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    sess.mount("https://", adapter)
    sess.mount("http://", adapter)

    cfg_mtime = file_mtime(cfg_path)
    targets_by_name: Dict[str, Target] = {t.name: t for t in targets}

    # 启动时可选：先执行一次自动登录/刷新 Cookie（你即使有 Cookie，也想“启动就自动登录”时用）
    maybe_relogin_on_start(
        targets=targets,
        force=bool(args.relogin_on_start),
        state_dir=state_dir,
        cfg_path=cfg_path,
        debug=bool(args.debug),
    )

    if args.debug:
        print(f"[{now_cn()}] 启动：targets={len(targets)} interval={args.interval}s state_dir={state_dir}", flush=True)
    try:
        while True:
            # 热加载 config.json：支持 WebUI 添加账号后无需重启
            new_mtime = file_mtime(cfg_path)
            if new_mtime and new_mtime != cfg_mtime:
                try:
                    raw_cfg2, targets2 = load_config(cfg_path)
                    cfg_mtime = new_mtime
                    bot_token = str(raw_cfg2.get("telegram_bot_token") or os.getenv("TELEGRAM_BOT_TOKEN") or "")
                    default_chat_id = str(raw_cfg2.get("telegram_chat_id") or os.getenv("TELEGRAM_CHAT_ID") or "")
                    tg_proxy = str(raw_cfg2.get("telegram_proxy") or os.getenv("TELEGRAM_PROXY") or "")

                    new_names = {t.name for t in targets2}

                    # 1) 更新/新增
                    newly_added: List[Target] = []
                    for nt in targets2:
                        if nt.name in targets_by_name:
                            update_target_in_place(targets_by_name[nt.name], nt)
                        else:
                            targets.append(nt)
                            targets_by_name[nt.name] = nt
                            newly_added.append(nt)

                    # 2) 移除（配置里删掉的目标）
                    removed = [name for name in list(targets_by_name.keys()) if name not in new_names]
                    if removed:
                        for name in removed:
                            targets_by_name.pop(name, None)
                        targets[:] = [t for t in targets if t.name in targets_by_name]

                    if newly_added or removed:
                        msg_parts = []
                        if newly_added:
                            msg_parts.append(f"新增 {len(newly_added)} 个账号")
                        if removed:
                            msg_parts.append(f"移除 {len(removed)} 个账号")
                        print(f"[{now_cn()}] [config] 已热加载：{', '.join(msg_parts)}", flush=True)

                    # 新增账号：不自动弹窗登录（避免重复打开浏览器/验证码打断工作流）。
                    # 由 WebUI 里的“登”按钮触发一次手动登录，成功后会写回 cookie/uid 到 config.json。
                    # 若你确实希望新增后自动登录，可在 config.json 中把该账号 relogin_on_start 设为 true，
                    # 或启动 ag_wmc_watch 时加 --relogin-on-start。
                    if newly_added:
                        maybe_relogin_on_start(
                            targets=newly_added,
                            force=bool(args.relogin_on_start),
                            state_dir=state_dir,
                            cfg_path=cfg_path,
                            debug=bool(args.debug),
                        )
                except Exception as e:
                    print(f"[{now_cn()}] [config] 热加载失败: {e}", file=sys.stderr, flush=True)

            for t in targets:
                try:
                    # WebUI 手动触发登录：点击“登”后会在 state/<name>.json 写入 manual_relogin=1
                    # 这里检测到就立刻弹浏览器登录一次（你可手动完成验证码），成功后会写回 config.json。
                    try:
                        st0 = load_state(state_dir, t.name)
                        if int(st0.get("manual_relogin") or 0) == 1:
                            st0["manual_relogin"] = 0
                            st0["manual_relogin_requested_at"] = 0
                            # last_relogin_at 由 WebUI 置 0 用于绕过间隔，这里保持不改动
                            save_state(state_dir, t.name, st0)
                            maybe_relogin_on_start(
                                targets=[t],
                                force=True,
                                state_dir=state_dir,
                                cfg_path=cfg_path,
                                debug=bool(args.debug),
                            )
                            # 若重登失败，避免立刻进入 poll_once 覆盖错误原因（否则 UI 常只看到 0X001 等忙碌码）。
                            st1 = load_state(state_dir, t.name)
                            if str(st1.get("last_error_code") or "") == "relogin_failed":
                                continue
                    except Exception:
                        pass

                    poll_once(
                        sess,
                        t,
                        state_dir=state_dir,
                        config_path=cfg_path,
                        bot_token=bot_token,
                        default_chat_id=default_chat_id,
                        timeout_s=args.timeout,
                        tg_timeout_s=args.tg_timeout,
                        tg_proxy=tg_proxy,
                        debug=args.debug,
                        only_orders=args.only_orders,
                        orders_dir=orders_dir,
                        interval_s=args.interval,
                    )
                except Exception as e:
                    msg = str(e)
                    hint = ""
                    if "Read timed out" in msg or "timed out" in msg:
                        hint = "（提示：检查本机代理是否开启；或把 --timeout 调大，例如 30）"
                    elif (
                        "err_proxy_connection_failed" in msg.lower()
                        or "unable to connect to proxy" in msg.lower()
                        or "proxyerror" in msg.lower()
                    ):
                        hint = "（提示：检测到代理连接失败。若你不需要代理，请清空 config.json 的 proxy；若需要代理，请先开启代理软件并确认端口。）"

                    # 把异常写入 state，便于 WebUI 显示原因；并在特定条件下触发自动重登
                    st = load_state(state_dir, t.name)
                    st["last_poll_at"] = now_ts()
                    st["heartbeat_ttl_s"] = float(max(6, (3 * max(1, int(args.interval))) + 2))
                    st["last_status"] = "offline"
                    st["last_error_code"] = "timeout" if ("timed out" in msg.lower()) else "exception"
                    st["last_error_msg"] = msg + (hint or "")
                    st["consecutive_errors"] = int(st.get("consecutive_errors") or 0) + 1
                    # 异常路径也不要遗留“重登中”标记（否则 UI 会长期黄灯）
                    st["relogin_in_progress"] = 0
                    save_state(state_dir, t.name, st)

                    # 默认不自动重登（避免代理不通/验证码等导致疯狂拉浏览器）。
                    # 只有当 force_relogin_on_offline=true 时才允许在连续失败后自动重登；否则请用 WebUI 的“登”按钮手动触发。
                    try:
                        mode = (t.relogin_mode or "none").lower()
                        if mode in ("playwright", "auto") and t.username and t.password and bool(t.force_relogin_on_offline):
                            # timeout 多半是代理/网络问题；这里保持保守阈值，避免在网络抖动时频繁重登
                            if st["last_error_code"] == "timeout":
                                can_try = st["consecutive_errors"] >= 5
                            else:
                                can_try = st["consecutive_errors"] >= 3
                            last_relogin = float(st.get("last_relogin_at") or 0)
                            now = now_ts()
                            if can_try and (now - last_relogin) >= max(5, int(t.relogin_min_interval_seconds)):
                                st["last_relogin_at"] = now
                                st["last_status"] = "relogin"
                                st["relogin_in_progress"] = 1
                                st["relogin_started_at"] = now
                                save_state(state_dir, t.name, st)
                                res = relogin_via_playwright(
                                    t,
                                    state_dir=state_dir,
                                    debug=args.debug,
                                )
                                new_cookie = str(res.get("cookie") or "").strip()
                                new_uid = str(res.get("uid") or "").strip()
                                new_layer = str(res.get("login_layer") or "").strip()
                                if new_cookie:
                                    t.cookie = new_cookie
                                    if new_uid:
                                        t.uid = new_uid
                                    if new_layer:
                                        t.login_layer = new_layer
                                    st["cookie"] = new_cookie
                                    st["last_error_code"] = ""
                                    st["last_error_msg"] = ""
                                    st["consecutive_errors"] = 0
                                    save_state(state_dir, t.name, st)
                                    if t.write_cookie_to_config:
                                        update_config_target(
                                            cfg_path,
                                            t.name,
                                            {"cookie": new_cookie, "uid": (new_uid or None), "login_layer": (new_layer or None)},
                                        )

                    except Exception as _re:
                        st = load_state(state_dir, t.name)
                        st["last_status"] = "offline"
                        st["last_error_code"] = "relogin_failed"
                        st["last_error_msg"] = str(_re)
                        st["relogin_in_progress"] = 0
                        save_state(state_dir, t.name, st)

                    print(f"[{now_cn()}] [{t.name}] 错误: {msg}{hint}", file=sys.stderr, flush=True)
            if args.once:
                return 0
            time.sleep(max(1, int(args.interval)))
    finally:
        pass


if __name__ == "__main__":
    raise SystemExit(main())
