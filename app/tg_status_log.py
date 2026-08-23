import html
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import requests


def now_ts() -> float:
    return time.time()


def now_cn() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

_GEO_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}


def _isp_to_cn(raw: str) -> str:
    s = str(raw or "").strip()
    if not s:
        return ""
    low = s.lower()
    mapping = [
        ("huawei cloud service data center", "华为云数据中心"),
        ("huawei cloud", "华为云"),
        ("china telecom", "中国电信"),
        ("chinanet", "中国电信"),
        ("china unicom", "中国联通"),
        ("china mobile", "中国移动"),
        ("alibaba", "阿里云"),
        ("aliyun", "阿里云"),
        ("tencent", "腾讯云"),
        ("baidu", "百度云"),
        ("amazon", "AWS"),
        ("microsoft", "微软 Azure"),
        ("google", "Google"),
    ]
    for k, v in mapping:
        if k in low:
            return v
    return s


def _cn_norm(raw: str) -> str:
    s = str(raw or "").strip()
    if not s:
        return ""
    fixes = {
        "中華人民共和國": "中华人民共和国",
        "臺灣": "台湾",
        "香港特別行政區": "香港特别行政区",
        "澳門特別行政區": "澳门特别行政区",
    }
    return fixes.get(s, s)


def _ip_geo_cached(ip: str, *, ttl_s: int = 86400) -> Dict[str, Any]:
    ip = str(ip or "").strip()
    if not ip:
        return {"ok": False, "error": "empty_ip"}
    now = now_ts()
    hit = _GEO_CACHE.get(ip)
    if hit:
        ts, val = hit
        if (now - ts) < max(10, int(ttl_s)):
            return val
    try:
        r = requests.get(
            f"http://ip-api.com/json/{ip}",
            params={"lang": "zh-CN"},
            timeout=8,
        )
        r.raise_for_status()
        obj = r.json()
        if not isinstance(obj, dict):
            val = {"ok": False, "error": "bad_geo_response"}
        elif str(obj.get("status") or "").lower() != "success":
            val = {"ok": False, "error": str(obj.get("message") or "geo_failed")}
        else:
            val = {
                "ok": True,
                "ip": str(obj.get("query") or ip),
                "country": _cn_norm(str(obj.get("country") or "")),
                "region": _cn_norm(str(obj.get("regionName") or "")),
                "city": _cn_norm(str(obj.get("city") or "")),
                "isp": _isp_to_cn(str(obj.get("isp") or "")),
                "org": str(obj.get("org") or ""),
                "as": str(obj.get("as") or ""),
            }
    except Exception as e:
        val = {"ok": False, "error": str(e)}
    _GEO_CACHE[ip] = (now, val)
    return val


def _proxy_host_ip(proxy: str) -> str:
    proxy = str(proxy or "").strip()
    if not proxy:
        return ""
    if "|" in proxy:
        parts = [p.strip() for p in proxy.split("|")]
        while parts and not parts[-1]:
            parts.pop()
        if len(parts) >= 1:
            return str(parts[0] or "").strip()
    try:
        u = urlparse(proxy)
        return str(u.hostname or "").strip()
    except Exception:
        return ""


def _proxy_line_for_message(proxy: str) -> str:
    proxy = str(proxy or "").strip()
    if not proxy:
        return ""
    ip = _proxy_host_ip(proxy)
    if not ip:
        return ""
    geo = _ip_geo_cached(ip)
    if geo.get("ok"):
        area = " ".join([x for x in [geo.get("country"), geo.get("region"), geo.get("city")] if x])
        isp = str(geo.get("isp") or geo.get("org") or geo.get("as") or "").strip()
        tail = " / ".join([x for x in [area, isp] if x])
        return f"代理：{html.escape(ip)}{('（' + html.escape(tail) + '）') if tail else ''}"
    return f"代理：{html.escape(ip)}"


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8-sig")
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def load_tg_push_config(config_path: Path) -> Tuple[bool, str, str, str]:
    cfg = _read_json(config_path)
    enabled = bool(cfg.get("telegram_status_push_enabled") or False)
    # 状态推送使用独立的 bot/chat（与订单推送分开）
    bot_token = str(cfg.get("telegram_status_bot_token") or "").strip()
    chat_id = str(cfg.get("telegram_status_chat_id") or "").strip()
    proxy = str(cfg.get("telegram_status_proxy") or "").strip()
    if not proxy:
        proxy = str(cfg.get("telegram_proxy") or "").strip()
    return enabled, bot_token, chat_id, proxy


def tg_send_html(bot_token: str, chat_id: str, html_text: str, *, timeout_s: int = 10, proxy: str = "") -> None:
    bot_token = str(bot_token or "").strip()
    chat_id = str(chat_id or "").strip()
    if not bot_token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    proxies = {"http": proxy, "https": proxy} if proxy else None
    r = requests.post(
        url,
        json={"chat_id": chat_id, "text": html_text, "parse_mode": "HTML", "disable_web_page_preview": True},
        timeout=timeout_s,
        proxies=proxies,
    )
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, dict) or not data.get("ok"):
        raise RuntimeError(f"telegram error: {data}")


def append_status_event(data_dir: Path, event: Dict[str, Any]) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / "telegram_status_log.jsonl"
    try:
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception:
        # 日志失败不应影响主流程
        pass


def log_and_maybe_push(
    *,
    config_path: Path,
    data_dir: Path,
    kind: str,  # backend | member
    account: str,
    status: str,  # offline | relogin | online | failed | alert | waiting
    message: str,
    chat_id_override: str = "",
    proxy_override: str = "",
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    ts = int(now_ts())
    ev: Dict[str, Any] = {
        "ts": ts,
        "time": now_cn(),
        "kind": str(kind or ""),
        "account": str(account or ""),
        "status": str(status or ""),
        "message": str(message or ""),
    }
    if isinstance(extra, dict) and extra:
        ev["extra"] = extra
    append_status_event(data_dir, ev)

    enabled, bot_token, default_chat_id, proxy = load_tg_push_config(config_path)
    if not enabled:
        return

    chat_id = str(chat_id_override or default_chat_id).strip()
    if not (bot_token and chat_id):
        return
    proxy_use = str(proxy_override or proxy).strip()

    kind_cn = "登3" if str(kind).lower() in ("backend", "target", "ag") else "会员"
    status_low = str(status or "").lower()
    if status_low in ("online", "ok", "success", "relogin_ok"):
        emoji = "✅"
        title = "在线/登录成功"
    elif status_low in ("relogin", "relogin_start", "logging_in"):
        emoji = "🔄"
        title = "开始重新登录"
    elif status_low in ("offline", "down", "logout"):
        emoji = "🔌"
        title = "掉线"
    elif status_low in ("failed", "relogin_failed", "error"):
        emoji = "❌"
        title = "重新登录失败"
    elif status_low in ("alert", "warning"):
        emoji = "⚠️"
        title = "掉线提示"
    else:
        emoji = "ℹ️"
        title = status

    proxy_line = ""
    if isinstance(extra, dict):
        proxy_line = _proxy_line_for_message(str(extra.get("account_proxy") or ""))
    if proxy_line:
        text = (
            f"{emoji} <b>{html.escape(kind_cn)}状态</b>\n账号：{html.escape(str(account or '-'))}\n{proxy_line}\n"
            f"事件：{html.escape(title)}\n{html.escape(str(message or ''))}\n⏰ {html.escape(now_cn())}"
        )
    else:
        text = f"{emoji} <b>{html.escape(kind_cn)}状态</b>\n账号：{html.escape(str(account or '-'))}\n事件：{html.escape(title)}\n{html.escape(str(message or ''))}\n⏰ {html.escape(now_cn())}"
    try:
        tg_send_html(bot_token, chat_id, text, timeout_s=12, proxy=proxy_use)
    except Exception as e:
        # 推送失败不应影响主流程（否则会导致 member_arsenal_watch 等进程崩溃）
        try:
            print(f"[{now_cn()}] telegram_push_failed: {e}", flush=True)
        except Exception:
            pass
