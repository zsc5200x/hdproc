import argparse
import collections
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import html
import json
import os
import re
import socket
import sys
import threading
import time
from time import time as now_ts
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from requests.utils import get_environ_proxies
from urllib3.util.retry import Retry

from tg_status_log import log_and_maybe_push
from playwright_proxy import build_playwright_proxy
from portable_env import ensure_playwright_browsers_path
from hdzz.core.watcher.order_filter import (
    normalize_downline_alias_map,
    normalize_member_names,
    should_forward_order_for_target,
)
from hdzz.core.order_display import parse_bet_display
from hdzz.common.platform_maintenance import (
    PLATFORM_MAINTENANCE_CODE,
    is_platform_maintenance,
    maintenance_status_message,
    read_platform_state,
    record_platform_business_success,
    refresh_backend_maintenance_state,
)


DEFAULT_HEADERS = {
    "accept": "*/*",
    "accept-language": "zh-CN,zh;q=0.9",
    "cache-control": "no-cache",
    "pragma": "no-cache",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) PythonWatch/1.0",
}

WMC_PERMISSION_DENIED_CODE = "wmc_permission_denied"
WMC_PERMISSION_DENIED_MSG = "已登录，但无注单监视权限（goToHome）"
_CONFIG_WRITE_LOCK = threading.Lock()

try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# worker 通常在 app/ 目录运行；兼容 wrapper 通过 HDZZ_APP_DIR 传入真实 app 目录
_APP_DIR = Path(os.environ.get("HDZZ_APP_DIR") or Path.cwd())
ensure_playwright_browsers_path(_APP_DIR)

_WATCH_LOCK_FH = None
_TG_ORDER_CB_PREFIX = "odr"
_TG_ORDER_BUTTON_SPECS: Dict[str, Dict[str, str]] = {
    "res": {"text": "跟单结果", "style": "primary"},
    "log": {"text": "跟单日志", "style": "primary"},
    "pnl": {"text": "输赢结果", "style": "danger"},
    "amt": {"text": "跟单报表", "style": "success"},
}
_ORDER_DIVIDER_FALLBACKS: Tuple[str, ...] = ("🇪🇸", "🇪🇸", "🇪🇸", "🏴", "🏴", "🏴", "🏴", "🇩🇪", "🇫🇷", "🇩🇪")


def now_cn() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _to_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return bool(default)
    if isinstance(value, (int, float)):
        return bool(value)
    s = str(value).strip().lower()
    if s in {"1", "true", "yes", "y", "on"}:
        return True
    if s in {"0", "false", "no", "n", "off"}:
        return False
    return bool(default)


def _digits_only(v: Any) -> str:
    s = str(v or "")
    return "".join(ch for ch in s if ch.isdigit())


def _normalize_tg_ui_theme(raw: Any) -> Dict[str, Any]:
    theme = raw if isinstance(raw, dict) else {}
    btn_ids = theme.get("button_custom_emoji_ids") if isinstance(theme.get("button_custom_emoji_ids"), dict) else {}
    txt_ids = theme.get("text_custom_emoji_ids") if isinstance(theme.get("text_custom_emoji_ids"), dict) else {}
    divider_ids = theme.get("divider_custom_emoji_ids") if isinstance(theme.get("divider_custom_emoji_ids"), list) else []
    return {
        "button_custom_emoji_ids": {
            str(k): str(v or "").strip()
            for k, v in btn_ids.items()
            if str(k or "").strip()
        },
        "text_custom_emoji_ids": {
            str(k): str(v or "").strip()
            for k, v in txt_ids.items()
            if str(k or "").strip()
        },
        "divider_custom_emoji_ids": [str(v or "").strip() for v in divider_ids if str(v or "").strip()],
    }


def _load_tg_ui_theme(cfg: Any) -> Dict[str, Any]:
    if not isinstance(cfg, dict):
        return _normalize_tg_ui_theme({})
    ui = cfg.get("telegram_ui")
    if not isinstance(ui, dict):
        return _normalize_tg_ui_theme({})
    return _normalize_tg_ui_theme(
        {
            "button_custom_emoji_ids": ui.get("order_button_custom_emoji_ids"),
            "text_custom_emoji_ids": ui.get("order_text_custom_emoji_ids"),
            "divider_custom_emoji_ids": ui.get("order_divider_custom_emoji_ids"),
        }
    )


def _tg_custom_emoji_html(emoji_id: Any, fallback: str = "") -> str:
    eid = str(emoji_id or "").strip()
    if eid:
        inner = html.escape(str(fallback or "").strip())
        return f'<tg-emoji emoji-id="{html.escape(eid, quote=True)}">{inner}</tg-emoji>'
    return fallback


def _order_card_icon(ui_theme: Optional[Dict[str, Any]], key: str, fallback: str) -> str:
    theme = _normalize_tg_ui_theme(ui_theme)
    text_ids = theme.get("text_custom_emoji_ids") or {}
    return _tg_custom_emoji_html(text_ids.get(str(key) or ""), fallback)


def _order_card_suffix(ui_theme: Optional[Dict[str, Any]], key: str, fallback: str = "") -> str:
    theme = _normalize_tg_ui_theme(ui_theme)
    text_ids = theme.get("text_custom_emoji_ids") or {}
    suffix_key = f"{str(key or '').strip()}_suffix"
    emoji = _tg_custom_emoji_html(text_ids.get(suffix_key) or "", fallback)
    return f" {emoji}" if emoji else ""


def _order_card_divider(ui_theme: Optional[Dict[str, Any]], fallback: Any = "🐸") -> str:
    theme = _normalize_tg_ui_theme(ui_theme)
    ids = theme.get("divider_custom_emoji_ids") or []
    if not ids:
        return ""
    fallback_seq = list(fallback) if isinstance(fallback, (list, tuple)) else []
    out: List[str] = []
    for idx, eid in enumerate(ids):
        fb = fallback_seq[idx] if idx < len(fallback_seq) else fallback
        out.append(_tg_custom_emoji_html(eid, str(fb or "")))
    return "".join(out)


def _themed_line(icon_html: str, text: str, *, spaced: bool) -> str:
    icon = str(icon_html or "").strip()
    if not icon:
        return text
    return f"{icon}{' ' if spaced else ''}{text}"


def _order_inline_keyboard(order_id: Any, ui_theme: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    oid = _digits_only(order_id)
    if not oid:
        return None
    theme = _normalize_tg_ui_theme(ui_theme)
    icon_ids = theme.get("button_custom_emoji_ids") or {}
    rows: List[List[Dict[str, str]]] = []
    for row_actions in (("res", "log"), ("pnl", "amt")):
        row: List[Dict[str, str]] = []
        for action in row_actions:
            spec = _TG_ORDER_BUTTON_SPECS[action]
            btn = {
                "text": spec["text"],
                "style": spec["style"],
                "callback_data": f"{_TG_ORDER_CB_PREFIX}|{action}|{oid}",
            }
            icon_id = str(icon_ids.get(action) or "").strip()
            if icon_id:
                btn["icon_custom_emoji_id"] = icon_id
            row.append(btn)
        rows.append(row)
    return {"inline_keyboard": rows}


def _is_http_url(v: Any) -> bool:
    s = str(v or "").strip().lower()
    return s.startswith("http://") or s.startswith("https://")


def _resolve_order_preview_local_path(image_ref: Any) -> Optional[Path]:
    s = str(image_ref or "").strip()
    if not s or _is_http_url(s):
        return None

    p0 = Path(s).expanduser()
    cands: List[Path] = []
    if p0.is_absolute():
        cands.append(p0)
    else:
        cwd = Path.cwd()
        cands.append((cwd / p0).resolve())
        cands.append((cwd.parent / p0).resolve())
        try:
            repo_root = Path(__file__).resolve().parents[4]
            cands.append((repo_root / p0).resolve())
            cands.append((repo_root / "app" / p0).resolve())
        except Exception:
            pass
    seen: set[str] = set()
    for p in cands:
        k = str(p)
        if k in seen:
            continue
        seen.add(k)
        try:
            if p.is_file():
                return p
        except Exception:
            continue
    return None


def _detect_default_order_preview_image_ref() -> str:
    for name in ("gdcg.jpg", "gdcc.jpg", "gdcg.jpeg", "gdcc.jpeg", "gdcg.png", "gdcc.png"):
        if _resolve_order_preview_local_path(name):
            return name
    return ""


def _preview_image_url_for_attach(image_ref: Any) -> str:
    s = str(image_ref or "").strip()
    return s if _is_http_url(s) else ""


def _resolve_order_preview_photo_ref(image_ref: Any) -> str:
    s = str(image_ref or "").strip()
    if not s:
        return ""
    if _is_http_url(s):
        return s
    p = _resolve_order_preview_local_path(s)
    return str(p) if p else ""


def _attach_order_preview_image(html_text: str, image_url: str) -> str:
    text = str(html_text or "")
    url = _preview_image_url_for_attach(image_url)
    if not url:
        return text
    if text.startswith("<a href="):
        return text
    u = html.escape(url, quote=True)
    return f'<a href="{u}">&#8205;</a>\n{text}'


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


def _looks_like_downline_id_token(value: Any) -> bool:
    s = str(value or "").strip()
    if not s:
        return False
    lower = s.lower()
    if lower.startswith("downline_"):
        tail = s.split("_", 1)[1].strip()
        return bool(tail) and tail.isdigit()
    return s.isdigit()


def _infer_single_downline_id_from_orders(orders_dir: Path, target_name: str, sample_size: int = 200) -> str:
    path = orders_dir / f"{safe_name(target_name)}.jsonl"
    if not path.exists():
        return ""
    rows: collections.deque[str] = collections.deque(maxlen=max(20, int(sample_size)))
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = str(line or "").strip()
                if line:
                    rows.append(line)
    except Exception:
        return ""

    ids: set = set()
    for line in rows:
        try:
            obj = json.loads(line)
        except Exception:
            continue
        raw = obj.get("raw") if isinstance(obj, dict) else None
        did = ""
        if isinstance(raw, dict):
            did = str(raw.get("DOWNLINE") or raw.get("downline") or raw.get("DOWNLINE_ID") or "").strip()
        if not did:
            did = str((obj if isinstance(obj, dict) else {}).get("downline") or "").strip()
        if did:
            ids.add(did)
        if len(ids) > 1:
            return ""
    if len(ids) == 1:
        return next(iter(ids))
    return ""


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
    m = re.search(r"@\s*([+-]?[0-9]+(?:\.[0-9]+)?)", text)
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


def _msg_is_go_to_home(msg: Any) -> bool:
    return "gotohome" in str(msg or "").lower().replace(" ", "")


def _response_value_is_go_to_home(data: Mapping[str, Any]) -> bool:
    if not isinstance(data, Mapping):
        return False
    return any(
        "gotohome" in str(data.get(key) or "").lower().replace(" ", "")
        for key in ("code", "msg", "message", "error")
    )


def _is_wmc_permission_denied_response(data: Dict[str, Any]) -> bool:
    if not isinstance(data, dict):
        return False
    if data.get("status") != "error":
        return False
    code = str(data.get("code") or "").strip()
    # 无权限进入注单监视页时，站点会返回空 code + goToHome；
    # 真正被踢/掉线通常有 4X014/4X013/login_required 等明确 code。
    return code == "" and _msg_is_go_to_home(data.get("msg"))


def _recent_relogin_success_cooldown_active(
    state: Dict[str, Any],
    *,
    now: Optional[float] = None,
    min_gap: int = 5,
) -> bool:
    last = float((state or {}).get("last_relogin_success_at") or 0)
    if last <= 0:
        return False
    now2 = now_ts() if now is None else float(now)
    window_s = float(max(45, int(min_gap) * 6))
    return (now2 - last) < window_s


def _mark_wmc_permission_denied_state(state: Dict[str, Any], *, now: Optional[float] = None) -> None:
    now2 = now_ts() if now is None else float(now)
    state["last_status"] = "offline"
    state["last_error_code"] = WMC_PERMISSION_DENIED_CODE
    state["last_error_msg"] = WMC_PERMISSION_DENIED_MSG
    state["relogin_in_progress"] = 0
    state["relogin_started_at"] = 0
    state["last_wmc_permission_denied_at"] = now2


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
    if _response_value_is_go_to_home(data) and not _msg_is_go_to_home(msg_l):
        return True
    if _msg_is_go_to_home(msg_l):
        return True
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


def _looks_like_transport_exception_message(msg: str) -> bool:
    text = str(msg or "").lower()
    needles = (
        "ssleoferror",
        "eof occurred",
        "max retries exceeded",
        "proxyerror",
        "unable to connect to proxy",
        "connection reset",
        "connection aborted",
        "remotedisconnected",
        "remote end closed connection",
        "read timed out",
        "timed out",
    )
    return any(token in text for token in needles)


def auto_relogin_threshold_for_error(error_code: str, error_message: str) -> int:
    code = str(error_code or "").strip().lower()
    msg = str(error_message or "")
    if code == "timeout":
        return 3
    if code == "exception" and _looks_like_transport_exception_message(msg):
        return 2
    return 3


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
    # 多线程下可能会并发写 config.json（例如多个账号同时触发 ver 更新 / cookie 刷新）。
    # 这里用进程内锁保证读-改-写是原子的，避免“后写覆盖前写”的丢更新问题。
    with _CONFIG_WRITE_LOCK:
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


def load_follow_settings_for_order_push(data_dir: Path) -> Dict[str, Any]:
    path = data_dir / "follow_settings.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _find_follow_target_rule(settings: Dict[str, Any], target_name: str) -> Optional[Dict[str, Any]]:
    name = str(target_name or "").strip()
    if not name:
        return None
    items = settings.get("targets") if isinstance(settings, dict) else None
    if not isinstance(items, list):
        return None
    for item in items:
        if not isinstance(item, dict):
            continue
        if str(item.get("name") or "").strip() == name:
            return item
    return None


def should_push_order_to_telegram_for_target(settings: Dict[str, Any], target_name: str) -> bool:
    rule = _find_follow_target_rule(settings, target_name)
    if not isinstance(rule, dict):
        return True
    if rule.get("enabled", True) is False:
        return False
    raw_ratio = rule.get("ratio_percent")
    if raw_ratio in (None, ""):
        return True
    try:
        return float(raw_ratio) != 0.0
    except Exception:
        return True


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
    downline = strip_html(pick("DOWNLINE", "downline", "DOWNLINE_ID", "DOWNLINEID", "source_downline", "SOURCE_DOWNLINE"))

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
        "downline": downline,
        "raw": w,
    }


def format_telegram_message(
    target_name: str,
    order: Dict[str, Any],
    ui_theme: Optional[Dict[str, Any]] = None,
) -> str:
    # 这条消息后续会被 follow_engine 用 editMessageText “就地更新”为“后台订单 + 跟单结果”的合并消息。
    def fmt_plain_amount(v: Any) -> str:
        try:
            f = float(v)
        except Exception:
            return str(v or "未知")
        if abs(f - round(f)) < 1e-9:
            return str(int(round(f)))
        return f"{f:.2f}".rstrip("0").rstrip(".")

    dt = f"{(order.get('date') or '')} {(order.get('time') or '')}".strip()
    league = str(order.get("league") or "未知")
    sides = str(order.get("sides") or "未知")
    stake = fmt_plain_amount(order.get("stake") or "未知")
    order_id = str(order.get("orderId") or "未知")
    order_status = "滚球" if "滚球" in str(order.get("betType") or "") else "早盘"
    bet_display = parse_bet_display(order, order_status)
    lines: List[str] = []
    lines.append(
        f"<b>{_order_card_icon(ui_theme, 'title', '🤣')}新注单提醒"
        f"{_order_card_suffix(ui_theme, 'title', '🤣')}</b>"
    )
    divider = _order_card_divider(ui_theme, fallback=_ORDER_DIVIDER_FALLBACKS)
    if divider:
        lines.append(divider)
    lines.append(_themed_line(_order_card_icon(ui_theme, 'league', '🤣'), html.escape(league), spaced=False))
    lines.append(_themed_line(_order_card_icon(ui_theme, 'match', '🤣'), html.escape(sides), spaced=False))
    lines.append("")
    lines.append(_themed_line(_order_card_icon(ui_theme, 'status', '📍'), f"状态：{html.escape(order_status)}", spaced=True))
    lines.append(_themed_line(_order_card_icon(ui_theme, 'play', ''), f"玩法：{html.escape(bet_display['bet_label'])}", spaced=False))
    lines.append(_themed_line(_order_card_icon(ui_theme, 'play', ''), f"时段：{html.escape(bet_display['period'])}", spaced=False))
    lines.append(_themed_line(_order_card_icon(ui_theme, 'play', ''), f"选择：{html.escape(bet_display['selection_text'])}", spaced=False))
    lines.append("")
    lines.append(_themed_line(_order_card_icon(ui_theme, 'stake', '💰'), f"原注金额：{html.escape(stake)}", spaced=True))
    lines.append(_themed_line(_order_card_icon(ui_theme, 'follow_stake', '💲'), "跟单金额：等待结果", spaced=True))
    lines.append(
        f"{_themed_line(_order_card_icon(ui_theme, 'follow_success', '🐦‍⬛'), '跟单成功：处理中', spaced=False)}"
        f"{_order_card_suffix(ui_theme, 'follow_success', '')}"
    )
    lines.append("")
    lines.append(_themed_line(_order_card_icon(ui_theme, 'source', ''), f"来源后台：{html.escape(str(target_name or '-'))}", spaced=False))
    lines.append(_themed_line(_order_card_icon(ui_theme, 'order_id', '🧠'), f"单号：{html.escape(order_id)}", spaced=False))
    lines.append(_themed_line(_order_card_icon(ui_theme, 'time', '🗓'), f"时间：{html.escape(dt or now_cn())}", spaced=False))
    return "\n".join(lines)


def format_backend_order_telegram_message(
    order: Dict[str, Any],
    ui_theme: Optional[Dict[str, Any]] = None,
) -> str:
    """Format a one-shot source-order notification that is never edited by follow_engine."""
    def fmt_amount(value: Any) -> str:
        try:
            amount = float(value)
        except Exception:
            return str(value or "未知")
        if abs(amount - round(amount)) < 1e-9:
            return str(int(round(amount)))
        return f"{amount:.2f}".rstrip("0").rstrip(".")

    league = str(order.get("league") or "未知")
    sides = str(order.get("sides") or "未知")
    order_status = "滚球" if "滚球" in str(order.get("betType") or "") else "早盘"
    bet_display = parse_bet_display(order, order_status)
    stake = html.escape(fmt_amount(order.get("stake") or "未知"))
    lines = [
        (
            f"<b>{_order_card_icon(ui_theme, 'title', '🤣')}新注单提醒"
            f"{_order_card_suffix(ui_theme, 'title', '🤣')}</b>"
        )
    ]
    divider = _order_card_divider(ui_theme, fallback=_ORDER_DIVIDER_FALLBACKS)
    if divider:
        lines.append(divider)
    lines.extend(
        [
            _themed_line(_order_card_icon(ui_theme, "league", "🤣"), html.escape(league), spaced=False),
            _themed_line(_order_card_icon(ui_theme, "match", "🤣"), html.escape(sides), spaced=False),
            "",
            _themed_line(
                _order_card_icon(ui_theme, "status", "📍"),
                f"状态：{html.escape(order_status)}",
                spaced=True,
            ),
            _themed_line(
                _order_card_icon(ui_theme, "play", ""),
                f"玩法：{html.escape(bet_display['bet_label'])}",
                spaced=False,
            ),
            _themed_line(
                _order_card_icon(ui_theme, "play", ""),
                f"时段：{html.escape(bet_display['period'])}",
                spaced=False,
            ),
            _themed_line(
                _order_card_icon(ui_theme, "play", ""),
                f"选择：{html.escape(bet_display['selection_text'])}",
                spaced=False,
            ),
            "",
            _themed_line(_order_card_icon(ui_theme, "stake", "💰"), f"金额：{stake}", spaced=True),
        ]
    )
    return "\n".join(lines)


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


def tg_send(
    bot_token: str,
    chat_id: str,
    html: str,
    timeout_s: int = 10,
    proxy: str = "",
    reply_markup: Optional[Dict[str, Any]] = None,
    disable_web_page_preview: bool = True,
) -> Optional[int]:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    proxies = None
    if proxy:
        proxies = {"http": proxy, "https": proxy}
    payload: Dict[str, Any] = {
        "chat_id": chat_id,
        "text": html,
        "parse_mode": "HTML",
        "disable_web_page_preview": bool(disable_web_page_preview),
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        r = requests.post(url, json=payload, timeout=timeout_s, proxies=proxies)
    except Exception as e:
        # 与 WebUI/跟单引擎保持一致：代理异常时自动降级直连一次，
        # 避免“测试推送正常，但订单首发静默失败”。
        if proxy:
            try:
                r = requests.post(url, json=payload, timeout=timeout_s, proxies=None)
            except Exception:
                raise e
        else:
            raise
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"telegram error: {data}")
    try:
        return int((data.get("result") or {}).get("message_id"))
    except Exception:
        return None


def tg_send_photo(
    bot_token: str,
    chat_id: str,
    photo_ref: str,
    caption_html: str = "",
    reply_markup: Optional[Dict[str, Any]] = None,
    timeout_s: int = 10,
    proxy: str = "",
) -> Optional[int]:
    bot_token = str(bot_token or "").strip()
    chat_id = str(chat_id or "").strip()
    photo_ref = str(photo_ref or "").strip()
    if not bot_token or not chat_id or not photo_ref:
        return None

    url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
    proxies = {"http": proxy, "https": proxy} if proxy else None
    is_url = _is_http_url(photo_ref)
    local_path = None if is_url else _resolve_order_preview_local_path(photo_ref)
    if (not is_url) and (not local_path):
        return None

    def _post_once(px: Optional[Dict[str, str]]) -> requests.Response:
        if is_url:
            payload: Dict[str, Any] = {"chat_id": chat_id, "photo": photo_ref}
            if caption_html:
                payload["caption"] = caption_html
                payload["parse_mode"] = "HTML"
            if reply_markup:
                payload["reply_markup"] = reply_markup
            return requests.post(
                url,
                json=payload,
                timeout=timeout_s,
                proxies=px,
            )
        assert local_path is not None
        payload2: Dict[str, Any] = {"chat_id": chat_id}
        if caption_html:
            payload2["caption"] = caption_html
            payload2["parse_mode"] = "HTML"
        if reply_markup:
            payload2["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
        with local_path.open("rb") as fh:
            return requests.post(
                url,
                data=payload2,
                files={"photo": (local_path.name, fh)},
                timeout=timeout_s,
                proxies=px,
            )

    try:
        r = _post_once(proxies)
    except Exception as e:
        if proxy:
            try:
                r = _post_once(None)
            except Exception:
                raise e
        else:
            raise
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"telegram error: {data}")
    try:
        return int((data.get("result") or {}).get("message_id"))
    except Exception:
        return None


def send_order_push_to_telegram(
    *,
    bot_token: str,
    chat_id: str,
    html: str,
    preview_photo_ref: str,
    preview_url: str,
    reply_markup: Optional[Dict[str, Any]] = None,
    timeout_s: int = 10,
    proxy: str = "",
) -> Optional[int]:
    """
    订单首发消息：
    - 优先图片卡片（sendPhoto）
    - 图片发送失败时，自动降级为纯文字消息（sendMessage）
    这样可避免“测试推送正常，但真实订单一条都发不出来”。
    """
    photo_ref = str(preview_photo_ref or "").strip()
    html2 = _attach_order_preview_image(html, preview_url)
    if photo_ref:
        try:
            mid = tg_send_photo(
                bot_token,
                chat_id,
                photo_ref,
                caption_html=html,
                reply_markup=reply_markup,
                timeout_s=timeout_s,
                proxy=proxy,
            )
            if mid:
                return mid
        except Exception:
            pass
    return tg_send(
        bot_token,
        chat_id,
        html2,
        timeout_s=timeout_s,
        proxy=proxy,
        reply_markup=reply_markup,
        disable_web_page_preview=(not bool(preview_url)),
    )


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
    login2_watch_members: List[str] = field(default_factory=list)
    login2_watch_downlines: List[str] = field(default_factory=list)
    login2_downline_alias_map: Dict[str, str] = field(default_factory=dict)
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
                login2_watch_members=normalize_member_names(t.get("login2_watch_members")),
                login2_watch_downlines=normalize_member_names(t.get("login2_watch_downlines")),
                login2_downline_alias_map=normalize_downline_alias_map(t.get("login2_downline_alias_map")),
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
    dst.login2_watch_members = normalize_member_names(src.login2_watch_members)
    dst.login2_watch_downlines = normalize_member_names(src.login2_watch_downlines)
    dst.login2_downline_alias_map = normalize_downline_alias_map(src.login2_downline_alias_map)
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
            "last_relogin_success_at": 0,
            "last_wmc_permission_denied_at": 0,
            "cookie": "",
            "last_poll_at": 0,
            "last_ok_at": 0,
            "last_status": "",
            "last_error_code": "",
            "last_error_msg": "",
            "last_relogin_error_code": "",
            "last_relogin_error_msg": "",
            "last_relogin_failed_at": 0,
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
        obj.setdefault("last_relogin_error_code", "")
        obj.setdefault("last_relogin_error_msg", "")
        obj.setdefault("last_relogin_failed_at", 0)
        obj.setdefault("relogin_in_progress", 0)
        obj.setdefault("relogin_started_at", 0)
        obj.setdefault("last_relogin_success_at", 0)
        obj.setdefault("last_wmc_permission_denied_at", 0)
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
            "last_relogin_success_at": 0,
            "last_wmc_permission_denied_at": 0,
            "cookie": "",
            "last_poll_at": 0,
            "last_ok_at": 0,
            "last_status": "",
            "last_error_code": "",
            "last_error_msg": "",
            "last_relogin_error_code": "",
            "last_relogin_error_msg": "",
            "last_relogin_failed_at": 0,
            "relogin_in_progress": 0,
            "relogin_started_at": 0,
            "initialized": 0,
        }


def save_state(state_dir: Path, target_name: str, state: Dict[str, Any]) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    p = state_path(state_dir, target_name)
    p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def make_poll_session() -> requests.Session:
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
    return sess


def resolve_poll_workers(requested_workers: int, target_count: int) -> int:
    target_count = max(0, int(target_count or 0))
    if target_count <= 0:
        return 1
    requested_workers = int(requested_workers or 0)
    if requested_workers <= 0:
        return target_count
    return max(1, min(requested_workers, target_count))


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
        ctx = _launch_persistent_context_with_fallback(
            self._p,
            user_data_dir=user_data_dir,
            headless=headless,
            proxy=proxy,
            target_name=target.name,
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


def _launch_persistent_context_with_fallback(
    pw: Any,
    *,
    user_data_dir: Path,
    headless: bool,
    proxy: Optional[Dict[str, Any]],
    target_name: str,
    prefer_system_chrome: bool = False,
) -> Any:
    force_system_chrome_only = _to_bool(os.environ.get("HDZZ_PLAYWRIGHT_SYSTEM_CHROME_ONLY"), False)
    bundled_chromium_only = _to_bool(os.environ.get("HDZZ_PLAYWRIGHT_BUNDLED_ONLY"), False)
    prefer_bundled_chromium = _to_bool(os.environ.get("HDZZ_PLAYWRIGHT_PREFER_BUNDLED"), False)
    launch_kwargs = {
        "headless": bool(headless),
        "viewport": {"width": 1280, "height": 860},
        "ignore_https_errors": True,
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--ignore-certificate-errors",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--disable-crash-reporter",
            "--disable-crashpad",
        ],
        "proxy": proxy,
    }
    candidates: List[Tuple[str, Dict[str, Any]]] = [("bundled_chromium", {})]
    if sys.platform == "darwin":
        if force_system_chrome_only:
            candidates = [("channel_chrome", {"channel": "chrome"})]
        elif bundled_chromium_only:
            candidates = [("bundled_chromium", {})]
        elif prefer_bundled_chromium:
            candidates = [("bundled_chromium", {}), ("channel_chrome", {"channel": "chrome"})]
        elif prefer_system_chrome:
            candidates = [("channel_chrome", {"channel": "chrome"}), ("bundled_chromium", {})]
        else:
            # macOS 默认优先系统 Chrome，规避内置 Chromium 偶发崩溃。
            candidates = [("channel_chrome", {"channel": "chrome"}), ("bundled_chromium", {})]

    launch_errors: List[Tuple[str, Exception]] = []
    for idx, (name, extra) in enumerate(candidates):
        try:
            ctx = pw.chromium.launch_persistent_context(str(user_data_dir), **launch_kwargs, **extra)
            if name == "channel_chrome":
                if force_system_chrome_only:
                    print(
                        f"[{now_cn()}] [{target_name}] 已启用强制系统 Chrome 模式（HDZZ_PLAYWRIGHT_SYSTEM_CHROME_ONLY=1）",
                        flush=True,
                    )
                elif idx == 0:
                    print(f"[{now_cn()}] [{target_name}] macOS 环境已优先使用本机 Chrome（channel=chrome）", flush=True)
                else:
                    print(
                        f"[{now_cn()}] [{target_name}] 检测到内置 Chromium 启动异常，已自动回退本机 Chrome（channel=chrome）",
                        flush=True,
                    )
            return ctx
        except Exception as e:
            launch_errors.append((name, e))
            continue

    e = launch_errors[0][1] if launch_errors else RuntimeError("unknown")
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
    if launch_errors:
        launch_chain = "；".join(f"{name}: {type(err).__name__}" for name, err in launch_errors)
        hint.append(f"启动链路：{launch_chain}")
    hint.append("可移植包请用 Start_AG_Matrix.bat 启动，并确认包根目录存在 ms-playwright/")
    raise RuntimeError(f"Playwright 启动浏览器失败：{type(e).__name__}: {e}；{'；'.join(hint)}") from e


def _is_target_closed_error(err: Exception) -> bool:
    s = str(err or "").lower()
    if not s:
        return False
    keys = (
        "target page, context or browser has been closed",
        "targetclosederror",
        "browser has been closed",
    )
    return any(k in s for k in keys)


def _first_visible_click(page: Any, selectors: List[str], timeout_ms: int = 2_000) -> bool:
    for sel in selectors:
        try:
            loc = page.locator(sel)
        except Exception:
            continue
        try:
            total = int(loc.count())
        except Exception:
            total = 0
        if total <= 0:
            continue
        for i in range(min(total, 8)):
            try:
                node = loc.nth(i)
            except Exception:
                continue
            try:
                if not bool(node.is_visible()):
                    continue
            except Exception:
                continue
            try:
                node.click(timeout=timeout_ms)
                return True
            except Exception:
                continue
    return False


def _context_pages(ctx: Any, fallback_page: Any = None) -> List[Any]:
    pages: List[Any] = []
    try:
        pages.extend(list(getattr(ctx, "pages", []) or []))
    except Exception:
        pass
    if fallback_page is not None and fallback_page not in pages:
        pages.append(fallback_page)
    return pages


def _page_url(page: Any) -> str:
    try:
        return str(getattr(page, "url", "") or "")
    except Exception:
        return ""


def _open_wmc_page_after_login(
    ctx: Any,
    page: Any,
    base: str,
    *,
    debug: bool = False,
    target_name: str = "",
) -> Any:
    base = str(base or "").rstrip("/")

    for pg in _context_pages(ctx, page):
        if "/app/wmc" in _page_url(pg).lower():
            return pg

    selectors = [
        "#t_wmc",
        "li#t_wmc",
        "a[href*='wmc']",
        "[onclick*='wmc']",
        "[id*='wmc']",
        "text=注单监视视窗",
        "text=注單監視視窗",
        "text=即时注单",
        "text=即時注單",
        "text=注单",
        "text=注單",
    ]

    deadline = time.time() + 8.0
    while time.time() < deadline:
        for pg in _context_pages(ctx, page):
            for sel in selectors:
                try:
                    loc = pg.locator(sel)
                except Exception:
                    continue
                try:
                    total = int(loc.count())
                except Exception:
                    total = 0
                if total <= 0:
                    continue
                for i in range(min(total, 4)):
                    try:
                        node = loc.nth(i)
                    except Exception:
                        continue
                    try:
                        if not bool(node.is_visible()):
                            continue
                    except Exception:
                        continue
                    try:
                        with pg.expect_popup(timeout=4_000) as pop_info:
                            node.click(timeout=2_000)
                        popup = pop_info.value
                        popup.wait_for_load_state("domcontentloaded", timeout=30_000)
                        popup.wait_for_timeout(2_000)
                        if debug:
                            print(f"[{now_cn()}] [{target_name or '-'}] 已通过弹窗进入 WMC 页面", flush=True)
                        return popup
                    except Exception:
                        try:
                            node.click(timeout=2_000)
                            pg.wait_for_timeout(2_000)
                            for cand in _context_pages(ctx, pg):
                                if "/app/wmc" in _page_url(cand).lower():
                                    if debug:
                                        print(f"[{now_cn()}] [{target_name or '-'}] 已点击进入 WMC 页面", flush=True)
                                    return cand
                            return pg
                        except Exception:
                            continue
        try:
            page.wait_for_timeout(500)
        except Exception:
            time.sleep(0.5)

    if base:
        try:
            page.goto(f"{base}/app/wmc/index.php", wait_until="domcontentloaded", timeout=30_000)
        except Exception:
            try:
                page.goto(f"{base}/app/wmc/index.php", wait_until="commit", timeout=30_000)
            except Exception:
                pass
        try:
            page.wait_for_timeout(2_000)
        except Exception:
            pass
        if debug:
            print(f"[{now_cn()}] [{target_name or '-'}] 已直接打开 WMC 地址", flush=True)
    return page


def _iter_page_and_frame_roots(page: Any) -> List[Any]:
    roots: List[Any] = [page]
    try:
        frames = list(getattr(page, "frames", []) or [])
    except Exception:
        frames = []
    roots.extend(frames)
    return roots


def _locator_nodes(loc_all: Any, limit: int = 12) -> List[Any]:
    try:
        total = int(loc_all.count())
    except Exception:
        total = 0
    if total <= 0:
        return []
    if hasattr(loc_all, "nth"):
        out: List[Any] = []
        for i in range(min(total, limit)):
            try:
                out.append(loc_all.nth(i))
            except Exception:
                continue
        return out
    try:
        first = getattr(loc_all, "first")
    except Exception:
        first = None
    return [first] if first is not None else []


def _fill_visible_locator_nodes(loc_all: Any, value: str, timeout_ms: int = 10_000) -> bool:
    for loc in _locator_nodes(loc_all):
        try:
            if not bool(loc.is_visible()):
                continue
        except Exception:
            continue
        try:
            loc.fill(value, timeout=timeout_ms)
            return True
        except Exception:
            try:
                loc.click(timeout=min(2_000, timeout_ms))
                loc.fill(value, timeout=timeout_ms)
                return True
            except Exception:
                continue
    return False


def _click_visible_locator_nodes(loc_all: Any, timeout_ms: int = 10_000) -> bool:
    for loc in _locator_nodes(loc_all):
        try:
            if not bool(loc.is_visible()):
                continue
        except Exception:
            continue
        try:
            loc.click(timeout=timeout_ms)
            return True
        except Exception:
            continue
    return False


def _complete_login_form(page: Any, *, username: str, password: str, security_code: str = "") -> None:
    try:
        page.bring_to_front()
    except Exception:
        pass

    roots = _iter_page_and_frame_roots(page)

    def fill_first(selectors: str, value: str, timeout_ms: int = 10_000) -> bool:
        for root in roots:
            try:
                loc_all = root.locator(selectors)
            except Exception:
                continue
            if _fill_visible_locator_nodes(loc_all, value, timeout_ms=timeout_ms):
                return True
        return False

    def fill_accessible(labels: List[str], placeholders: List[str], value: str, timeout_ms: int = 10_000) -> bool:
        for root in roots:
            for label in labels:
                try:
                    loc_all = root.get_by_label(label, exact=False)
                except Exception:
                    continue
                if _fill_visible_locator_nodes(loc_all, value, timeout_ms=timeout_ms):
                    return True
            for placeholder in placeholders:
                try:
                    loc_all = root.get_by_placeholder(placeholder, exact=False)
                except Exception:
                    continue
                if _fill_visible_locator_nodes(loc_all, value, timeout_ms=timeout_ms):
                    return True
        return False

    def click_first(selectors: str, timeout_ms: int = 10_000) -> bool:
        for root in roots:
            try:
                loc_all = root.locator(selectors)
            except Exception:
                continue
            if _click_visible_locator_nodes(loc_all, timeout_ms=timeout_ms):
                return True
        return False

    def click_accessible_button(names: List[str], timeout_ms: int = 10_000) -> bool:
        for root in roots:
            for name in names:
                for role in ("button", "link"):
                    try:
                        loc_all = root.get_by_role(role, name=name)
                    except Exception:
                        continue
                    if _click_visible_locator_nodes(loc_all, timeout_ms=timeout_ms):
                        return True
        return False

    user_ok = fill_first(
        "#username, #userName, #account, input[name='username'], input[name='account'], input[name='userName'], input[id*='user'], input[id*='account'], input[placeholder*='账号'], input[placeholder*='帳號'], input[placeholder*='用户名']",
        username,
        timeout_ms=10_000,
    ) or fill_accessible(
        labels=["账号", "帳號", "用户名", "用戶名", "Username", "Account"],
        placeholders=["账号", "帳號", "用户名", "Username", "Account"],
        value=username,
        timeout_ms=10_000,
    )

    pwd_ok = fill_first(
        "#pwd, #password, input[name='pwd'], input[name='password'], input[id*='pwd'], input[id*='pass'], input[type='password'], input[placeholder*='密码'], input[placeholder*='密碼']",
        password,
        timeout_ms=10_000,
    ) or fill_accessible(
        labels=["密码", "密碼", "Password"],
        placeholders=["密码", "密碼", "Password"],
        value=password,
        timeout_ms=10_000,
    )

    if not (user_ok and pwd_ok):
        raise RuntimeError("自动填充账号或密码失败")

    if security_code:
        sc_ok = fill_first(
            "#pwd_safe, input[name='pwd_safe'], #scode, #security_code, input[name='scode'], input[name='security_code'], input[name='code'], input[id*='safe'], input[placeholder*='安全'], input[placeholder*='验证码'], input[placeholder*='驗證碼']",
            security_code,
            timeout_ms=6_000,
        ) or fill_accessible(
            labels=["安全码", "安全碼", "验证码", "驗證碼", "Security Code"],
            placeholders=["安全码", "安全碼", "验证码", "驗證碼", "Security Code"],
            value=security_code,
            timeout_ms=6_000,
        )
        if not sc_ok:
            raise RuntimeError("已配置安全码，但未找到可填写的安全码输入框，已停止自动提交")

    btn_ok = click_first(
        "#loginBtn, .lg_login_btn, button:has-text('登录'), button:has-text('登入'), input[value*='登录'], input[value*='登入']",
        timeout_ms=10_000,
    ) or click_accessible_button(
        names=["登录", "登入", "Login", "Sign in"],
        timeout_ms=10_000,
    )
    if not btn_ok:
        raise RuntimeError("未找到登录按钮")


def _login_form_still_visible(page: Any) -> bool:
    selectors = [
        "#username, input[name='username'], input[name='account'], input[name='userName']",
        "#pwd, #password, input[name='pwd'], input[name='password'], input[type='password']",
    ]
    for root in _iter_page_and_frame_roots(page):
        for sel in selectors:
            try:
                loc_all = root.locator(sel)
            except Exception:
                continue
            for loc in _locator_nodes(loc_all, limit=4):
                try:
                    if bool(loc.is_visible()):
                        return True
                except Exception:
                    continue
    return False


def _extract_lpsid_values(cookies: List[Dict[str, Any]]) -> List[str]:
    values: List[str] = []
    for c in cookies or []:
        try:
            name = str(c.get("name") or "")
            value = str(c.get("value") or "")
        except Exception:
            continue
        if "lpsid" in name.lower() and value:
            values.append(value)
    return values


def _read_top_vars_from_context(ctx: Any, fallback_page: Any) -> Tuple[str, str]:
    uid_val = ""
    layer_val = ""
    pages: List[Any] = []
    try:
        pages.extend(list(getattr(ctx, "pages", []) or []))
    except Exception:
        pass
    if fallback_page is not None and fallback_page not in pages:
        pages.append(fallback_page)

    seen_pages: set = set()
    for pg in pages:
        page_key = id(pg)
        if page_key in seen_pages:
            continue
        seen_pages.add(page_key)
        try:
            frames = list(getattr(pg, "frames", []) or [])
        except Exception:
            frames = []
        roots = [pg] + frames
        for fr in roots:
            try:
                uid = fr.evaluate(
                    """
                    () => {
                      const vals = [];
                      try { vals.push(window.uid); } catch (e) {}
                      try { vals.push(window.top && window.top.uid); } catch (e) {}
                      try { vals.push(window.parent && window.parent.uid); } catch (e) {}
                      return vals.map(v => v == null ? '' : String(v)).find(Boolean) || '';
                    }
                    """
                )
                if uid and len(str(uid)) > len(uid_val):
                    uid_val = str(uid)
            except Exception:
                pass
            try:
                layer = fr.evaluate(
                    """
                    () => {
                      const vals = [];
                      try { vals.push(window.login_layer); } catch (e) {}
                      try { vals.push(window.top && window.top.login_layer); } catch (e) {}
                      try { vals.push(window.parent && window.parent.login_layer); } catch (e) {}
                      return vals.map(v => v == null ? '' : String(v)).find(Boolean) || '';
                    }
                    """
                )
                if layer and len(str(layer)) > len(layer_val):
                    layer_val = str(layer)
            except Exception:
                pass
    return uid_val, layer_val


def _is_relogin_session_ready(
    page: Any,
    *,
    cookies: List[Dict[str, Any]],
    last_uid: str,
    initial_lpsid_values: set,
) -> bool:
    current_lpsid_values = set(_extract_lpsid_values(cookies))
    if not current_lpsid_values:
        return False
    if current_lpsid_values - set(initial_lpsid_values or set()):
        return True
    if str(last_uid or "").strip():
        return True
    return not _login_form_still_visible(page)


def _is_relogin_probe_ready(data: Dict[str, Any]) -> bool:
    if not isinstance(data, dict):
        return False
    if data.get("status") != "error":
        return True

    code = str(data.get("code") or "").strip()
    msg = str(data.get("msg") or "").strip()
    msg_l = msg.lower().replace(" ", "")
    if _is_wmc_permission_denied_response(data):
        return False
    if code in {"4X014", "4X013", "logout", "login_required"}:
        return False
    if "gotohome" in msg_l:
        return False
    if looks_like_logged_out(data):
        return False
    return True


def _normalize_downline_alias_name(raw_name: str) -> str:
    s = re.sub(r"\s+", " ", str(raw_name or "").strip())
    if not s:
        return ""
    m = re.search(r"[A-Za-z0-9_.@-]+", s)
    if m:
        return str(m.group(0)).strip()
    return s


def _extract_downline_alias_map_from_wmc_page(page: Any, debug: bool = False, target_name: str = "") -> Dict[str, str]:
    out: Dict[str, str] = {}
    rows: List[Dict[str, Any]] = []
    for _ in range(4):
        try:
            _first_visible_click(
                page,
                [
                    "#downline_show",
                    "text=下线",
                    "text=下線",
                    "text=Downline",
                ],
                timeout_ms=2_000,
            )
        except Exception:
            pass
        try:
            page.wait_for_timeout(1_000)
        except Exception:
            pass
        try:
            rows = page.evaluate(
                """() => {
                    const list = [];
                    const lis = document.querySelectorAll("li[id^='downline_'][id$='_f']");
                    lis.forEach((li) => {
                        const id = String(li.id || "").replace(/^downline_/, "").replace(/_f$/, "");
                        const inp = li.querySelector("input[name='downlineID']");
                        const name = String((inp && inp.value) || "").trim();
                        const txt = String(li.textContent || "").trim();
                        if (id) list.push({ id, name, txt });
                    });
                    return list;
                }"""
            )
        except Exception:
            rows = []
        if rows:
            break

    if not isinstance(rows, list):
        rows = []
    for it in rows:
        if not isinstance(it, dict):
            continue
        did = str(it.get("id") or "").strip()
        if not did:
            continue
        name = _normalize_downline_alias_name(str(it.get("name") or ""))
        if not name:
            name = _normalize_downline_alias_name(str(it.get("txt") or ""))
        if not name:
            continue
        out[name.casefold()] = did

    if debug:
        print(f"[{now_cn()}] [{target_name or '-'}] 下线映射抓取数量={len(out)}", flush=True)
    return out


def _extract_downline_alias_map_after_login(page: Any, debug: bool = False, target_name: str = "") -> Dict[str, str]:
    popup = None
    wmc_page = None
    try:
        deadline = time.time() + 12.0
        while (time.time() < deadline) and (wmc_page is None):
            for sel in ("#t_wmc", "li#t_wmc", "text=注单监视视窗", "text=注單監視視窗"):
                try:
                    loc = page.locator(sel)
                except Exception:
                    continue
                try:
                    total = int(loc.count())
                except Exception:
                    total = 0
                if total <= 0:
                    continue
                clicked = False
                for i in range(min(total, 3)):
                    try:
                        node = loc.nth(i)
                    except Exception:
                        continue
                    try:
                        if not bool(node.is_visible()):
                            continue
                    except Exception:
                        continue
                    try:
                        with page.expect_popup(timeout=5_000) as pop_info:
                            node.click(timeout=3_000)
                        popup = pop_info.value
                        popup.wait_for_load_state("domcontentloaded", timeout=30_000)
                        popup.wait_for_timeout(2_000)
                        wmc_page = popup
                        clicked = True
                        break
                    except Exception:
                        try:
                            node.click(timeout=3_000)
                            page.wait_for_timeout(2_000)
                            wmc_page = page
                            clicked = True
                            break
                        except Exception:
                            continue
                if clicked:
                    break
            if wmc_page is None:
                try:
                    page.wait_for_timeout(1_000)
                except Exception:
                    pass
        if wmc_page is None:
            return {}
        return _extract_downline_alias_map_from_wmc_page(wmc_page, debug=debug, target_name=target_name)
    finally:
        try:
            if popup:
                popup.close()
        except Exception:
            pass


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
        ctx = _launch_persistent_context_with_fallback(
            p,
            user_data_dir=user_data_dir,
            headless=bool(target.relogin_headless),
            proxy=proxy,
            target_name=target.name,
        )
        try:
            try:
                page = ctx.new_page()
            except Exception as e:
                if sys.platform == "darwin" and _is_target_closed_error(e):
                    try:
                        ctx.close()
                    except Exception:
                        pass
                    # 某些机器上会出现“浏览器拉起后马上关闭”，这里强制切到系统 Chrome 再试一次。
                    ctx = _launch_persistent_context_with_fallback(
                        p,
                        user_data_dir=user_data_dir,
                        headless=bool(target.relogin_headless),
                        proxy=proxy,
                        target_name=target.name,
                        prefer_system_chrome=True,
                    )
                    page = ctx.new_page()
                else:
                    raise
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
                    _complete_login_form(
                        page,
                        username=target.username,
                        password=target.password,
                        security_code=target.security_code,
                    )
                    if target.security_code and debug:
                        print(f"[{now_cn()}] [{target.name}] 已自动填入安全码", flush=True)
                except Exception as e:
                    print(
                        f"[{now_cn()}] [{target.name}] 自动填充失败：{str(e)[:300]}；请手动完成登录/验证码",
                        flush=True,
                    )

            deadline = time.time() + max(30, int(target.relogin_timeout_seconds))
            session_ready = False
            last_uid = ""
            last_layer = ""
            last_probe_error = ""
            wmc_opened = False
            wmc_permission_denied = False
            initial_lpsid_values = set(_extract_lpsid_values(ctx.cookies()))

            while time.time() < deadline:
                cookies = ctx.cookies()
                uid, layer = _read_top_vars_from_context(ctx, page)
                if uid:
                    last_uid = uid
                if layer:
                    last_layer = layer
                has_cookie = _is_relogin_session_ready(
                    page,
                    cookies=cookies,
                    last_uid=last_uid,
                    initial_lpsid_values=initial_lpsid_values,
                )
                if has_cookie and not wmc_opened:
                    page = _open_wmc_page_after_login(
                        ctx,
                        page,
                        base,
                        debug=debug,
                        target_name=target.name,
                    )
                    wmc_opened = True
                    cookies = ctx.cookies()
                    uid, layer = _read_top_vars_from_context(ctx, page)
                    if uid:
                        last_uid = uid
                    if layer:
                        last_layer = layer
                probe_uid = last_uid or target.uid
                if has_cookie and probe_uid:
                    cookie_str = dedupe_cookies(cookies, host=host)
                    probe_target = replace(
                        target,
                        cookie=cookie_str,
                        uid=probe_uid,
                        login_layer=last_layer or target.login_layer,
                    )
                    try:
                        probe = request_wagers_playwright(
                            ctx,
                            probe_target,
                            sel_maxid=target.sel_maxid or "0",
                            timeout_s=min(15, max(5, int(target.relogin_timeout_seconds))),
                        )
                        if _is_wmc_permission_denied_response(probe):
                            wmc_permission_denied = True
                            session_ready = True
                            last_probe_error = str(probe)[:300]
                            break
                        if _is_relogin_probe_ready(probe):
                            session_ready = True
                            break
                        last_probe_error = str(probe)[:300]
                    except Exception as e:
                        last_probe_error = f"{type(e).__name__}: {str(e)[:260]}"
                time.sleep(1)

            if not session_ready:
                if last_probe_error:
                    raise RuntimeError(f"重登超时：登录后接口仍未就绪（{last_probe_error}）")
                raise RuntimeError("重登超时：未检测到可用登录会话（可能需要手动验证码）")

            cookie_str = dedupe_cookies(cookies, host=host)
            downline_alias_map: Dict[str, str] = {}
            if (
                not wmc_permission_denied
                and int(target.relogin_login_tab or 0) in (2, 3)
                and bool(target.login2_watch_downlines)
            ):
                try:
                    downline_alias_map = _extract_downline_alias_map_after_login(
                        page,
                        debug=debug,
                        target_name=target.name,
                    )
                except Exception:
                    downline_alias_map = {}
            return {
                "cookie": cookie_str,
                "uid": last_uid,
                "login_layer": last_layer or target.login_layer,
                "downline_alias_map": downline_alias_map,
                "wmc_permission_denied": bool(wmc_permission_denied),
            }
        finally:
            ctx.close()

    with sync_playwright() as p:
        return do_login(p)


_BACKGROUND_RELOGINS: Dict[str, threading.Thread] = {}
_BACKGROUND_RELOGINS_LOCK = threading.Lock()


def is_background_relogin_running(target_name: str) -> bool:
    with _BACKGROUND_RELOGINS_LOCK:
        th = _BACKGROUND_RELOGINS.get(target_name)
        return bool(th is not None and th.is_alive())


def start_background_relogin(
    target: Target,
    *,
    state_dir: Path,
    config_path: Path,
    debug: bool,
    state_prepared: bool = False,
) -> bool:
    with _BACKGROUND_RELOGINS_LOCK:
        existing = _BACKGROUND_RELOGINS.get(target.name)
        if existing is not None and existing.is_alive():
            return False

        if not state_prepared:
            st = load_state(state_dir, target.name)
            now = now_ts()
            st["last_relogin_at"] = now
            st["last_status"] = "relogin"
            st["relogin_in_progress"] = 1
            st["relogin_started_at"] = now
            save_state(state_dir, target.name, st)

        def worker() -> None:
            try:
                res = relogin_via_playwright(target, state_dir=state_dir, debug=debug)
                new_cookie = str(res.get("cookie") or "").strip()
                new_uid = str(res.get("uid") or "").strip()
                new_layer = str(res.get("login_layer") or "").strip()
                downline_alias_map = normalize_downline_alias_map(res.get("downline_alias_map"))
                wmc_permission_denied = bool(res.get("wmc_permission_denied"))
                if not new_cookie:
                    raise RuntimeError("重新登录未获得 Cookie")

                target.cookie = new_cookie
                if new_uid:
                    target.uid = new_uid
                if new_layer:
                    target.login_layer = new_layer
                if downline_alias_map:
                    target.login2_downline_alias_map = downline_alias_map

                st2 = load_state(state_dir, target.name)
                now_ok = now_ts()
                st2["cookie"] = new_cookie
                st2["initialized"] = 0
                st2["seen"] = []
                st2["last_relogin_success_at"] = now_ok
                st2["relogin_in_progress"] = 0
                st2["relogin_started_at"] = 0
                if wmc_permission_denied:
                    st2["last_ok_at"] = 0
                    _mark_wmc_permission_denied_state(st2, now=now_ok)
                else:
                    st2["last_status"] = "online"
                    st2["last_ok_at"] = now_ok
                    st2["last_error_code"] = ""
                    st2["last_error_msg"] = ""
                    st2["last_relogin_error_code"] = ""
                    st2["last_relogin_error_msg"] = ""
                    st2["last_relogin_failed_at"] = 0
                save_state(state_dir, target.name, st2)

                if target.write_cookie_to_config:
                    patch_cfg: Dict[str, Any] = {
                        "cookie": new_cookie,
                        "uid": (new_uid or None),
                        "login_layer": (new_layer or None),
                    }
                    if downline_alias_map:
                        patch_cfg["login2_downline_alias_map"] = downline_alias_map
                    update_config_target(config_path, target.name, patch_cfg)
                if wmc_permission_denied:
                    print(f"[{now_cn()}] [{target.name}] 后台登录完成，但账号无注单监视权限，已停止自动重登", flush=True)
                else:
                    print(f"[{now_cn()}] [{target.name}] 后台重新登录完成，已标记在线并等待下一轮拉单", flush=True)
            except Exception as e:
                st2 = load_state(state_dir, target.name)
                err_msg = str(e)
                st2["last_status"] = "offline"
                st2["last_error_code"] = "relogin_failed"
                st2["last_error_msg"] = err_msg
                st2["last_relogin_error_code"] = "relogin_failed"
                st2["last_relogin_error_msg"] = err_msg
                st2["last_relogin_failed_at"] = now_ts()
                st2["relogin_in_progress"] = 0
                save_state(state_dir, target.name, st2)
                print(f"[{now_cn()}] [{target.name}] 后台重新登录失败: {e}", flush=True)
            finally:
                with _BACKGROUND_RELOGINS_LOCK:
                    cur = _BACKGROUND_RELOGINS.get(target.name)
                    if cur is threading.current_thread():
                        _BACKGROUND_RELOGINS.pop(target.name, None)

        th = threading.Thread(target=worker, name=f"relogin-{target.name}", daemon=True)
        _BACKGROUND_RELOGINS[target.name] = th
        th.start()
        return True


def poll_once(
    session: requests.Session,
    target: Target,
    state_dir: Path,
    config_path: Path,
    bot_token: str,
    default_chat_id: str,
    order_forward_enabled: bool,
    timeout_s: int,
    tg_timeout_s: int,
    tg_proxy: str,
    order_preview_image: str,
    ui_theme: Optional[Dict[str, Dict[str, str]]],
    debug: bool,
    only_orders: bool,
    orders_dir: Path,
    interval_s: int,
    backend_order_push_enabled: bool = False,
    backend_order_bot_token: str = "",
    backend_order_chat_id: str = "",
    backend_order_proxy: str = "",
) -> None:
    state = load_state(state_dir, target.name)
    poll_mode = (target.poll_mode or "requests").lower()
    interval_s = max(1, int(interval_s))
    timeout_s = max(1, int(timeout_s))
    if is_background_relogin_running(target.name):
        state["last_status"] = "relogin"
        state["relogin_in_progress"] = 1
        state.setdefault("relogin_started_at", now_ts())
        save_state(state_dir, target.name, state)
        return

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
                ctx = _launch_persistent_context_with_fallback(
                    p,
                    user_data_dir=user_data_dir,
                    headless=True,
                    proxy=proxy,
                    target_name=target.name,
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
    # 平台维护是平台级状态，不把所有账号的 goHome/no_systemtime 误判成账号掉线。
    # 探针只读取结构化维护接口，避免会员 service_main 页面内置的历史隐藏模板造成误报。
    platform_state = refresh_backend_maintenance_state(
        session=session,
        base_url=target.base_url,
        data_dir_or_path=data_dir,
        cookie=target.cookie,
        proxy=target.proxy,
        timeout_s=min(8, timeout_s),
    )
    if is_platform_maintenance(platform_state):
        state["last_status"] = "maintenance"
        state["last_error_code"] = PLATFORM_MAINTENANCE_CODE
        state["last_error_msg"] = maintenance_status_message(platform_state)
        state["platform_status"] = "maintenance"
        state["platform_maintenance_until"] = float(platform_state.get("maintenance_until") or 0.0)
        state["platform_maintenance_start_at"] = float(platform_state.get("maintenance_start_at") or 0.0)
        state["relogin_in_progress"] = 0
        state["relogin_started_at"] = 0
        # 维护期间不递增错误、不更新冷却、不启动重登。
        save_state(state_dir, target.name, state)
        if debug and not only_orders:
            print(f"[{now_cn()}] [{target.name}] {maintenance_status_message(platform_state)}，暂停本轮请求", flush=True)
        return
    state["platform_status"] = str(platform_state.get("status") or "unknown")
    state["platform_maintenance_until"] = float(platform_state.get("maintenance_until") or 0.0)
    state["platform_maintenance_start_at"] = float(platform_state.get("maintenance_start_at") or 0.0)
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
        need_relogin = looks_like_logged_out(data)
        wmc_permission_denied = _is_wmc_permission_denied_response(data)
        # 非登录态错误不能遗留黄灯；只有明确掉线并即将重登时才进入 relogin。
        if not need_relogin:
            state["relogin_in_progress"] = 0
            state["last_status"] = "offline"
        if debug and not only_orders:
            print(f"[{now_cn()}] [{target.name}] 错误响应: code={code} msg={msg}", flush=True)
        force_any = bool(getattr(target, "force_relogin_on_offline", False))
        # 只在“明确被踢/掉线”时才重登；网络抖动/服务端忙碌不应该触发重登（避免把其它正常账号也拉去重登）
        if need_relogin:
            # 1) 优先尝试自动重新登录
            mode = (target.relogin_mode or "none").lower()
            now = now_ts()
            last_relogin = float(state.get("last_relogin_at") or 0)
            min_gap = max(5, int(target.relogin_min_interval_seconds))
            if wmc_permission_denied and (
                str(state.get("last_error_code") or "") == WMC_PERMISSION_DENIED_CODE
                or _recent_relogin_success_cooldown_active(state, now=now, min_gap=min_gap)
            ):
                _mark_wmc_permission_denied_state(state, now=now)
                save_state(state_dir, target.name, state)
                if debug and not only_orders:
                    print(f"[{now_cn()}] [{target.name}] 已登录但无注单监视权限，跳过自动重登", flush=True)
                return

            state["last_status"] = "offline"
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
                        state["last_tg_relogin_start_at"] = now
                        save_state(state_dir, target.name, state)
                except Exception as e:
                    print(f"[{now_cn()}] [{target.name}] 重登状态记录失败: {e}", flush=True)
                started = start_background_relogin(
                    target,
                    state_dir=state_dir,
                    config_path=config_path,
                    debug=debug,
                    state_prepared=True,
                )
                if debug and not only_orders:
                    action = "已启动" if started else "已存在"
                    print(f"[{now_cn()}] [{target.name}] 后台重新登录{action}，主轮询继续处理其它账号", flush=True)
                save_state(state_dir, target.name, state)
                return
                try:
                    res = relogin_via_playwright(
                        target,
                        state_dir=state_dir,
                        debug=debug,
                    )
                    new_cookie = str(res.get("cookie") or "").strip()
                    new_uid = str(res.get("uid") or "").strip()
                    new_layer = str(res.get("login_layer") or "").strip()
                    downline_alias_map = normalize_downline_alias_map(res.get("downline_alias_map"))
                    if downline_alias_map:
                        target.login2_downline_alias_map = downline_alias_map
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
                        # 已拿到有效登录态：先标记在线，避免 UI 长时间停留“重登中”。
                        # 后续若拉单失败会在错误分支回落到 offline。
                        state["last_status"] = "online"
                        state["last_ok_at"] = now_ts()
                        state["relogin_started_at"] = 0
                        state["last_error_code"] = ""
                        state["last_error_msg"] = ""
                        save_state(state_dir, target.name, state)
                        if target.write_cookie_to_config:
                            patch_cfg: Dict[str, Any] = {
                                "cookie": new_cookie,
                                "uid": (new_uid or None),
                                "login_layer": (new_layer or None),
                            }
                            if downline_alias_map:
                                patch_cfg["login2_downline_alias_map"] = downline_alias_map
                            update_config_target(
                                config_path,
                                target.name,
                                patch_cfg,
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
                                state["last_tg_relogin_ok_at"] = now_ok
                                save_state(state_dir, target.name, state)
                        except Exception as e:
                            print(f"[{now_cn()}] [{target.name}] 重登成功状态记录失败: {e}", flush=True)
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
                    err_msg = str(e)
                    state["last_status"] = "offline"
                    state["last_error_code"] = "relogin_failed"
                    state["last_error_msg"] = err_msg
                    state["last_relogin_error_code"] = "relogin_failed"
                    state["last_relogin_error_msg"] = err_msg
                    state["last_relogin_failed_at"] = now_ts()
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
                            state["last_tg_relogin_fail_at"] = now_fail
                            save_state(state_dir, target.name, state)
                    except Exception as e2:
                        print(f"[{now_cn()}] [{target.name}] 重登失败状态记录失败: {e2}", flush=True)
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
                        print(f"[{now_cn()}] [{target.name}] 掉线状态记录失败: {e}", flush=True)
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
    record_platform_business_success(data_dir, kind="backend")

    # 首次初始化：只同步 maxid，不把返回列表当作新注单（避免启动/新增账号时把历史单刷出来）
    if not int(state.get("initialized") or 0):
        if maxid is not None:
            state["sel_maxid"] = str(maxid)
        state["seen"] = []
        state["initialized"] = 1
        save_state(state_dir, target.name, state)
        return

    runtime_downline_alias_map = normalize_downline_alias_map(getattr(target, "login2_downline_alias_map", {}))
    need_alias_guess = (
        int(target.relogin_login_tab or 0) in (2, 3)
        and bool(target.login2_watch_downlines)
        and (not runtime_downline_alias_map)
    )
    if need_alias_guess:
        has_alias_name = False
        for item in (target.login2_watch_downlines or []):
            s = str(item or "").strip()
            if not s:
                continue
            if not _looks_like_downline_id_token(s):
                has_alias_name = True
                break
        if has_alias_name:
            sid = _infer_single_downline_id_from_orders(orders_dir, target.name, sample_size=300)
            if sid:
                for item in (target.login2_watch_downlines or []):
                    key = str(item or "").strip().casefold()
                    if key and (not _looks_like_downline_id_token(key)):
                        runtime_downline_alias_map[key] = sid
                if runtime_downline_alias_map:
                    target.login2_downline_alias_map = runtime_downline_alias_map
                    try:
                        update_config_target(
                            config_path,
                            target.name,
                            {"login2_downline_alias_map": runtime_downline_alias_map},
                        )
                    except Exception:
                        pass

    new_orders: List[Dict[str, Any]] = []
    filtered_count = 0
    for w in wagers:
        if not isinstance(w, dict):
            continue
        order = normalize_wager(w)
        oid = str(order["orderId"])
        if oid in seen_set:
            continue
        seen_set.add(oid)
        seen.append(oid)
        if not should_forward_order_for_target(
            relogin_login_tab=int(target.relogin_login_tab or 0),
            login2_watch_members=target.login2_watch_members,
            login2_watch_downlines=target.login2_watch_downlines,
            login2_downline_alias_map=runtime_downline_alias_map,
            order=order,
        ):
            filtered_count += 1
            continue
        new_orders.append(order)

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

    follow_settings = load_follow_settings_for_order_push(data_dir)
    push_this_target = should_push_order_to_telegram_for_target(follow_settings, target.name)
    if order_forward_enabled and push_this_target and bot_token and chat_id:
        preview_url = _preview_image_url_for_attach(order_preview_image)
        preview_photo_ref = _resolve_order_preview_photo_ref(order_preview_image)
        for order in reversed(new_orders):  # 从旧到新推送
            html = format_telegram_message(target.name, order, ui_theme=ui_theme)
            keyboard = _order_inline_keyboard(order.get("orderId"), ui_theme=ui_theme)
            try:
                mid = send_order_push_to_telegram(
                    bot_token=bot_token,
                    chat_id=chat_id,
                    html=html,
                    preview_photo_ref=str(preview_photo_ref or ""),
                    preview_url=preview_url,
                    reply_markup=keyboard,
                    timeout_s=tg_timeout_s,
                    proxy=(tg_proxy or target.proxy or ""),
                )
                if mid:
                    order["_tg_message_id"] = mid
                    order["_tg_chat_id"] = chat_id
            except Exception as e:
                print(f"[{now_cn()}] [{target.name}] Telegram 推送失败: {e}", flush=True)

    # 独立的后台原单通道只发送一次简版原始订单，不保存 message_id，跟单引擎不会编辑这条消息。
    if backend_order_push_enabled and backend_order_bot_token and backend_order_chat_id:
        for order in reversed(new_orders):
            try:
                tg_send(
                    backend_order_bot_token,
                    backend_order_chat_id,
                    format_backend_order_telegram_message(order, ui_theme=ui_theme),
                    timeout_s=tg_timeout_s,
                    proxy=backend_order_proxy,
                    reply_markup=None,
                    disable_web_page_preview=True,
                )
            except Exception as e:
                print(f"[{now_cn()}] [{target.name}] 后台原单推送失败: {e}", flush=True)

    # 持久化订单：供 WebUI/回溯使用（包含 _tg_message_id/_tg_chat_id 以支持合并推送）
    if new_orders:
        persist_orders(orders_dir, target.name, new_orders)

    if debug and not only_orders and filtered_count:
        print(
            f"[{now_cn()}] [{target.name}] 已按登入2监控会员过滤 {filtered_count} 条（未推送/未落盘）",
            flush=True,
        )
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
        mode = (t.relogin_mode or "none").lower()
        # 手动触发(force=True)时，不再被 relogin_mode 限制，避免“点了登按钮却不拉浏览器”。
        if (not force) and mode not in ("playwright", "auto"):
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
            st["relogin_in_progress"] = 0
            st["relogin_started_at"] = now_ts()
            st["last_relogin_error_code"] = ""
            st["last_relogin_error_msg"] = ""
            st["last_relogin_failed_at"] = 0
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
                err_msg = str(e)
                st["last_status"] = "offline"
                st["last_error_code"] = "relogin_failed"
                st["last_error_msg"] = err_msg
                st["last_relogin_error_code"] = "relogin_failed"
                st["last_relogin_error_msg"] = err_msg
                st["last_relogin_failed_at"] = now_ts()
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
    parser.add_argument(
        "--workers",
        type=int,
        default=0,
        help="并发抓单账号数（0=自动=账号数；>0=限制最大并发，避免账号多时拖垮本机/代理）",
    )
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
    order_forward_enabled = bool(raw_cfg.get("telegram_order_forward_enabled", True))
    tg_proxy = str(raw_cfg.get("telegram_proxy") or os.getenv("TELEGRAM_PROXY") or "")
    backend_order_push_enabled = bool(raw_cfg.get("telegram_backend_order_push_enabled", False))
    backend_order_bot_token = str(raw_cfg.get("telegram_backend_order_bot_token") or "").strip()
    backend_order_chat_id = str(raw_cfg.get("telegram_backend_order_chat_id") or "").strip()
    backend_order_proxy = str(raw_cfg.get("telegram_backend_order_proxy") or "").strip()
    order_preview_image = str(raw_cfg.get("telegram_order_preview_image") or "").strip()
    tg_ui_theme = _load_tg_ui_theme(raw_cfg)
    if not order_preview_image:
        order_preview_image = _detect_default_order_preview_image_ref()

    state_dir = Path(args.state_dir)
    orders_dir = Path(args.orders_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    orders_dir.mkdir(parents=True, exist_ok=True)

    lock_path = state_dir / "ag_wmc_watch.lock"
    if not acquire_single_instance_lock(lock_path):
        print(f"[{now_cn()}] ag_wmc_watch 已在运行，退出 lock={lock_path}", flush=True)
        return 2

    cfg_mtime = file_mtime(cfg_path)
    targets_by_name: Dict[str, Target] = {t.name: t for t in targets}

    # 在任何启动重登之前先确认平台是否维护，防止维护期间弹出多个登录窗口。
    maintenance_session = make_poll_session()
    try:
        first_target = targets[0]
        refresh_backend_maintenance_state(
            session=maintenance_session,
            base_url=first_target.base_url,
            data_dir_or_path=cfg_path.parent / "data",
            cookie=first_target.cookie,
            proxy=first_target.proxy,
            timeout_s=min(8, max(1, int(args.timeout))),
            min_probe_interval_s=1,
        )
    finally:
        maintenance_session.close()

    # 启动时可选：先执行一次自动登录/刷新 Cookie（你即使有 Cookie，也想“启动就自动登录”时用）
    if not is_platform_maintenance(read_platform_state(cfg_path.parent / "data")):
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
                    order_forward_enabled = bool(raw_cfg2.get("telegram_order_forward_enabled", True))
                    tg_proxy = str(raw_cfg2.get("telegram_proxy") or os.getenv("TELEGRAM_PROXY") or "")
                    backend_order_push_enabled = bool(raw_cfg2.get("telegram_backend_order_push_enabled", False))
                    backend_order_bot_token = str(raw_cfg2.get("telegram_backend_order_bot_token") or "").strip()
                    backend_order_chat_id = str(raw_cfg2.get("telegram_backend_order_chat_id") or "").strip()
                    backend_order_proxy = str(raw_cfg2.get("telegram_backend_order_proxy") or "").strip()
                    order_preview_image = str(raw_cfg2.get("telegram_order_preview_image") or "").strip()
                    tg_ui_theme = _load_tg_ui_theme(raw_cfg2)
                    if not order_preview_image:
                        order_preview_image = _detect_default_order_preview_image_ref()

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

            workers = resolve_poll_workers(args.workers, len(targets))
            if args.debug:
                print(f"[{now_cn()}] 并发抓单：workers={workers} targets={len(targets)}", flush=True)

            def run_one(target: Target) -> Tuple[str, Optional[str]]:
                # 每个任务独立 session，避免多线程共享 requests.Session 引发不可预期行为。
                sess = make_poll_session()
                try:
                    # WebUI 手动触发登录：点击“登”后会在 state/<name>.json 写入 manual_relogin=1
                    # 这里检测到就立刻弹浏览器登录一次（你可手动完成验证码），成功后会写回 config.json。
                    try:
                        st0 = load_state(state_dir, target.name)
                        if int(st0.get("manual_relogin") or 0) == 1:
                            if is_platform_maintenance(read_platform_state(cfg_path.parent / "data")):
                                st0["manual_relogin"] = 0
                                st0["manual_relogin_requested_at"] = 0
                                st0["last_status"] = "maintenance"
                                st0["last_error_code"] = PLATFORM_MAINTENANCE_CODE
                                st0["last_error_msg"] = maintenance_status_message(
                                    read_platform_state(cfg_path.parent / "data")
                                )
                                st0["relogin_in_progress"] = 0
                                st0["relogin_started_at"] = 0
                                save_state(state_dir, target.name, st0)
                                return target.name, None
                            st0["manual_relogin"] = 0
                            st0["manual_relogin_requested_at"] = 0
                            # last_relogin_at 由 WebUI 置 0 用于绕过间隔，这里保持不改动
                            save_state(state_dir, target.name, st0)
                            start_background_relogin(
                                target,
                                state_dir=state_dir,
                                config_path=cfg_path,
                                debug=bool(args.debug),
                                state_prepared=False,
                            )
                            return target.name, None
                    except Exception:
                        pass

                    poll_once(
                        sess,
                        target,
                        state_dir=state_dir,
                        config_path=cfg_path,
                        bot_token=bot_token,
                        default_chat_id=default_chat_id,
                        order_forward_enabled=order_forward_enabled,
                        timeout_s=args.timeout,
                        tg_timeout_s=args.tg_timeout,
                        tg_proxy=tg_proxy,
                        order_preview_image=order_preview_image,
                        ui_theme=tg_ui_theme,
                        debug=args.debug,
                        only_orders=args.only_orders,
                        orders_dir=orders_dir,
                        interval_s=args.interval,
                        backend_order_push_enabled=backend_order_push_enabled,
                        backend_order_bot_token=backend_order_bot_token,
                        backend_order_chat_id=backend_order_chat_id,
                        backend_order_proxy=backend_order_proxy,
                    )
                    return target.name, None
                except Exception as e:
                    return target.name, str(e)
                finally:
                    try:
                        sess.close()
                    except Exception:
                        pass

            errors: List[Tuple[str, str]] = []
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="poll") as ex:
                futures = [ex.submit(run_one, t) for t in list(targets)]
                for fut in as_completed(futures):
                    name, err = fut.result()
                    if not err:
                        continue
                    msg = str(err)
                    hint = ""
                    if "Read timed out" in msg or "timed out" in msg:
                        hint = "（提示：检查本机代理是否开启；或把 --timeout 调大，例如 30）"
                    elif (
                        "err_proxy_connection_failed" in msg.lower()
                        or "unable to connect to proxy" in msg.lower()
                        or "proxyerror" in msg.lower()
                    ):
                        hint = "（提示：检测到代理连接失败。若你不需要代理，请清空 config.json 的 proxy；若需要代理，请先开启代理软件并确认端口。）"

                    t0 = targets_by_name.get(name)
                    if t0 is None:
                        continue

                    # 把异常写入 state，便于 WebUI 显示原因；并在特定条件下触发自动重登
                    st = load_state(state_dir, t0.name)
                    st["last_poll_at"] = now_ts()
                    st["heartbeat_ttl_s"] = float(max(6, (3 * max(1, int(args.interval))) + 2))
                    st["last_status"] = "offline"
                    st["last_error_code"] = "timeout" if ("timed out" in msg.lower()) else "exception"
                    st["last_error_msg"] = msg + (hint or "")
                    st["consecutive_errors"] = int(st.get("consecutive_errors") or 0) + 1
                    # 异常路径也不要遗留“重登中”标记（否则 UI 会长期黄灯）
                    st["relogin_in_progress"] = 0
                    save_state(state_dir, t0.name, st)

                    # 默认不自动重登（避免代理不通/验证码等导致疯狂拉浏览器）。
                    # 只有当 force_relogin_on_offline=true 时才允许在连续失败后自动重登；否则请用 WebUI 的“登”按钮手动触发。
                    try:
                        mode = (t0.relogin_mode or "none").lower()
                        if mode in ("playwright", "auto") and t0.username and t0.password and bool(t0.force_relogin_on_offline):
                            threshold = auto_relogin_threshold_for_error(
                                st.get("last_error_code") or "",
                                st.get("last_error_msg") or "",
                            )
                            can_try = int(st.get("consecutive_errors") or 0) >= int(threshold)
                            last_relogin = float(st.get("last_relogin_at") or 0)
                            now = now_ts()
                            if can_try and (now - last_relogin) >= max(5, int(t0.relogin_min_interval_seconds)):
                                st["last_relogin_at"] = now
                                st["last_status"] = "relogin"
                                st["relogin_in_progress"] = 1
                                st["relogin_started_at"] = now
                                save_state(state_dir, t0.name, st)
                                res = relogin_via_playwright(
                                    t0,
                                    state_dir=state_dir,
                                    debug=args.debug,
                                )
                                new_cookie = str(res.get("cookie") or "").strip()
                                new_uid = str(res.get("uid") or "").strip()
                                new_layer = str(res.get("login_layer") or "").strip()
                                if new_cookie:
                                    t0.cookie = new_cookie
                                    if new_uid:
                                        t0.uid = new_uid
                                    if new_layer:
                                        t0.login_layer = new_layer
                                    st["cookie"] = new_cookie
                                    # 重登成功后结束“重登中”锁，避免 UI 长时间停留黄灯。
                                    st["relogin_in_progress"] = 0
                                    st["last_status"] = "relogin"
                                    st["relogin_started_at"] = now_ts()
                                    st["last_error_code"] = ""
                                    st["last_error_msg"] = ""
                                    st["last_relogin_error_code"] = ""
                                    st["last_relogin_error_msg"] = ""
                                    st["last_relogin_failed_at"] = 0
                                    st["consecutive_errors"] = 0
                                    save_state(state_dir, t0.name, st)
                                    if t0.write_cookie_to_config:
                                        update_config_target(
                                            cfg_path,
                                            t0.name,
                                            {"cookie": new_cookie, "uid": (new_uid or None), "login_layer": (new_layer or None)},
                                        )
                    except Exception as _re:
                        err_msg = str(_re)
                        st = load_state(state_dir, t0.name)
                        st["last_status"] = "offline"
                        st["last_error_code"] = "relogin_failed"
                        st["last_error_msg"] = err_msg
                        st["last_relogin_error_code"] = "relogin_failed"
                        st["last_relogin_error_msg"] = err_msg
                        st["last_relogin_failed_at"] = now_ts()
                        st["relogin_in_progress"] = 0
                        save_state(state_dir, t0.name, st)

                    errors.append((t0.name, msg + hint))

            for name, msg in errors:
                print(f"[{now_cn()}] [{name}] 错误: {msg}", file=sys.stderr, flush=True)
            if args.once:
                return 0
            time.sleep(max(1, int(args.interval)))
    finally:
        pass


if __name__ == "__main__":
    raise SystemExit(main())
