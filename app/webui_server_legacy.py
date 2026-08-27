import argparse
import json
import os
import time
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import uuid
import xml.etree.ElementTree as ET
from urllib.parse import parse_qs, urlparse, urlencode
from urllib.request import ProxyHandler, Request, build_opener

import requests
import subprocess
import signal
import sys
import socket
import threading

from portable_env import apply_playwright_browsers_path, detect_playwright_browsers_path, ensure_playwright_browsers_path


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


SERVER_START_TIME = time.time()
WATCH_PROCESS = None
WATCH_LOG_FILE = Path("ag_wmc_watch.log")
TARGET_ONLINE_TTL_S = 20.0
APP_ROOT = Path(__file__).resolve().parent
ensure_playwright_browsers_path(APP_ROOT)


def safe_name(name: str) -> str:
    s = "".join(c for c in str(name) if c.isalnum() or c in ("-", "_")).strip()
    return s or "default"


def is_pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except Exception:
            return False
    try:
        import ctypes  # type: ignore

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32  # type: ignore
        h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, int(pid))
        if not h:
            return False
        code = ctypes.c_ulong(0)
        ok = kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        kernel32.CloseHandle(h)
        if not ok:
            return False
        return int(code.value) == STILL_ACTIVE
    except Exception:
        return False


def core_running_flag() -> Tuple[bool, Optional[int]]:
    """
    Return (running, pid_count) for core workers managed by app.py start-core/stop-core.
    """
    try:
        p = Path(".run/pids_core.json")
        if not p.exists():
            return False, 0
        obj = json.loads(p.read_text(encoding="utf-8-sig") or "{}")
        if not isinstance(obj, dict):
            return False, 0
        pids = [int(x) for x in (obj.get("pids") or [])]
        alive = [pid for pid in pids if is_pid_running(pid)]
        return bool(alive), len(alive)
    except Exception:
        return False, 0

LAST_LATENCY = 0
LAST_LATENCY_CHECK = 0

def get_latency(target="www.baidu.com", port=80):
    global LAST_LATENCY, LAST_LATENCY_CHECK
    now = time.time()
    if now - LAST_LATENCY_CHECK < 5:  # Cache for 5 seconds
        return LAST_LATENCY
        
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2.0)
        t1 = time.time()
        s.connect((target, port))
        t2 = time.time()
        s.close()
        lat = int((t2 - t1) * 1000)
        LAST_LATENCY = lat
        LAST_LATENCY_CHECK = now
        return lat
    except Exception:
        LAST_LATENCY = 999
        LAST_LATENCY_CHECK = now
        return 999

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


def read_json_file(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8-sig")
        try:
            obj = json.loads(raw)
            return obj if isinstance(obj, dict) else dict(default)
        except json.JSONDecodeError:
            # 兼容：文件末尾被误追加了多余内容（常见于并发写入临时文件冲突）
            try:
                dec = json.JSONDecoder()
                obj, _ = dec.raw_decode(raw.lstrip("\ufeff").lstrip())
                return obj if isinstance(obj, dict) else dict(default)
            except Exception:
                return dict(default)
    except Exception:
        return dict(default)


def ensure_json_file(path: Path, default: Dict[str, Any]) -> None:
    if path.exists():
        return
    atomic_write_json(path, default)


def mask_secret(s: str, keep: int = 3) -> str:
    s = str(s or "")
    if not s:
        return ""
    if len(s) <= keep:
        return "*" * len(s)
    return ("*" * (len(s) - keep)) + s[-keep:]


def redact_secret_in_text(text: str, secret: str) -> str:
    s = str(text or "")
    sec = str(secret or "")
    if not s or not sec:
        return s
    try:
        return s.replace(sec, "***")
    except Exception:
        return s

def tg_send_message(
    *,
    bot_token: str,
    chat_id: str,
    text: str,
    proxy: str = "",
    timeout_s: int = 15,
) -> Dict[str, Any]:
    bot_token = str(bot_token or "").strip()
    chat_id = str(chat_id or "").strip()
    if not bot_token or not chat_id:
        return {"ok": False, "error": "missing_bot_token_or_chat_id"}

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = json.dumps(
        {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    headers = {"Content-Type": "application/json"}

    opener = build_opener(ProxyHandler({"http": proxy, "https": proxy})) if proxy else build_opener()
    req = Request(url, data=payload, headers=headers, method="POST")
    try:
        with opener.open(req, timeout=max(5, int(timeout_s))) as resp:
            data = resp.read().decode("utf-8", "ignore")
        obj = json.loads(data)
        if isinstance(obj, dict) and obj.get("ok"):
            return {"ok": True}
        return {"ok": False, "error": str(obj)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _normalize_proxy_input(proxy: str) -> str:
    proxy = str(proxy or "").strip()
    if not proxy:
        return ""
    # 优先使用 socks5h（代理端解析 DNS），对部分环境更稳定
    if proxy.lower().startswith("socks5://"):
        return "socks5h://" + proxy[len("socks5://") :]
    if "|" not in proxy:
        return proxy
    parts = [p.strip() for p in proxy.split("|")]
    while parts and not parts[-1]:
        parts.pop()
    if len(parts) == 2 and parts[0] and parts[1]:
        ip, port = parts
        return f"socks5h://{ip}:{port}"
    # 兼容：你可能会把到期日等备注拼在后面（例如 ip|port|user|pass|2026.1.22|）
    # 这里仅取前4段，多余段忽略
    if len(parts) >= 4 and parts[0] and parts[1]:
        ip, port, user, pwd = parts[:4]
        if user and pwd:
            return f"socks5h://{user}:{pwd}@{ip}:{port}"
        return f"socks5h://{ip}:{port}"
    return proxy


def _describe_proxy_exc(e: Exception) -> str:
    msg = str(e) or e.__class__.__name__
    try:
        if isinstance(e, requests.exceptions.ConnectTimeout):
            return f"connect_timeout: {msg}"
        if isinstance(e, requests.exceptions.ReadTimeout):
            return f"read_timeout: {msg}"
        if isinstance(e, requests.exceptions.ProxyError):
            return f"proxy_error: {msg}"
        if isinstance(e, requests.exceptions.SSLError):
            return f"ssl_error: {msg}"
        if isinstance(e, requests.exceptions.ConnectionError):
            return f"connection_error: {msg}"
    except Exception:
        pass
    return msg


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
    # ip-api 的中文字段偶尔是繁体，这里做少量高频归一化到简体。
    fixes = {
        "中華人民共和國": "中华人民共和国",
        "臺灣": "台湾",
        "香港特別行政區": "香港特别行政区",
        "澳門特別行政區": "澳门特别行政区",
    }
    return fixes.get(s, s)


def _ip_geo(*, ip: str, timeout_s: int = 8) -> Dict[str, Any]:
    ip = str(ip or "").strip()
    if not ip:
        return {"ok": False, "error": "empty_ip"}
    try:
        # ip-api 免费接口（无需 token）；注意其字段名
        url = f"http://ip-api.com/json/{ip}"
        params = {"fields": "status,message,country,regionName,city,isp,org,as,query", "lang": "zh-CN"}
        r = requests.get(url, params=params, timeout=max(3, int(timeout_s)))
        r.raise_for_status()
        obj = r.json()
        if not isinstance(obj, dict):
            return {"ok": False, "error": "bad_geo_response"}
        if str(obj.get("status") or "").lower() != "success":
            return {"ok": False, "error": str(obj.get("message") or "geo_failed")}
        return {
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
        return {"ok": False, "error": _describe_proxy_exc(e)}


def _proxy_host_ip(proxy: str) -> str:
    try:
        u = urlparse(str(proxy or "").strip())
        host = u.hostname or ""
        # hostname 只有在 URL 解析成功时才会有；如果用户没写 scheme，忽略
        return str(host or "").strip()
    except Exception:
        return ""


_GEO_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}


def _geo_cached(ip: str, *, ttl_s: int = 86400) -> Dict[str, Any]:
    ip = str(ip or "").strip()
    if not ip:
        return {"ok": False, "error": "empty_ip"}
    now = time.time()
    hit = _GEO_CACHE.get(ip)
    if hit:
        ts, val = hit
        if (now - ts) < max(10, int(ttl_s)):
            return val
    val = _ip_geo(ip=ip, timeout_s=6)
    _GEO_CACHE[ip] = (now, val)
    return val


def _proxy_display(proxy_raw: str) -> Dict[str, Any]:
    proxy_norm = _normalize_proxy_input(str(proxy_raw or "").strip())
    if not proxy_norm:
        return {"label": "本地代理", "proxy_ip": "", "proxy_geo": None}
    ip = _proxy_host_ip(proxy_norm)
    geo = None
    if ip and ip.replace(".", "").isdigit():
        g = _geo_cached(ip)
        if g.get("ok"):
            geo = g
    return {"label": ip or "已配置代理", "proxy_ip": ip, "proxy_geo": geo}


def proxy_test(*, proxy: str, timeout_s: int = 10, base_url: str = "") -> Dict[str, Any]:
    """
    测试代理可用性（支持 http/https；socks5 需要 PySocks 依赖）。
    优先访问 ipify 获取出口 IP；失败则回退到 baidu 做连通性判断。
    """
    proxy = _normalize_proxy_input(proxy)
    if not proxy:
        return {"ok": False, "error": "empty_proxy"}

    sess = requests.Session()
    sess.trust_env = False

    # 返回“代理服务器 IP”的归属地（不依赖通过代理访问外网）
    proxy_ip = _proxy_host_ip(proxy)
    proxy_geo: Optional[Dict[str, Any]] = None
    if proxy_ip and proxy_ip.replace(".", "").isdigit():
        g = _ip_geo(ip=proxy_ip, timeout_s=min(10, max(3, int(timeout_s))))
        if g.get("ok"):
            proxy_geo = g

    # socks5:// 对域名默认本地解析；很多环境需要 socks5h:// 走代理解析 DNS
    candidates = [proxy]
    if proxy.lower().startswith("socks5://"):
        candidates.insert(0, "socks5h://" + proxy[len("socks5://") :])

    def try_get(url: str, *, verify: bool) -> Dict[str, Any]:
        last_err = ""
        for pxy in candidates:
            proxies = {"http": pxy, "https": pxy}
            t0 = time.time()
            try:
                r = sess.get(url, timeout=max(3, int(timeout_s)), proxies=proxies, verify=verify, allow_redirects=True)
                elapsed_ms = int((time.time() - t0) * 1000)
                if int(r.status_code) >= 400:
                    last_err = f"http_{int(r.status_code)}"
                    continue
                ip = ""
                if "ipify" in url:
                    try:
                        obj = r.json()
                        if isinstance(obj, dict) and obj.get("ip"):
                            ip = str(obj.get("ip") or "")
                    except Exception:
                        pass
                return {"ok": True, "test_url": url, "status": int(r.status_code), "elapsed_ms": elapsed_ms, "ip": ip}
            except Exception as e:
                last_err = _describe_proxy_exc(e)
                continue
        return {"ok": False, "error": last_err or "request_failed"}

    base_url = str(base_url or "").strip().rstrip("/")
    base_err = ""
    if base_url.startswith("http://") or base_url.startswith("https://"):
        base_res = try_get(base_url + "/", verify=False)
        if base_res.get("ok"):
            # 目标站点可访问 ≠ 一定能访问外网；但用户希望能看到出口 IP/地区，继续尝试 ipify 获取。
            ip_res = try_get("https://api.ipify.org?format=json", verify=True)
            if ip_res.get("ok") and ip_res.get("ip"):
                geo = _ip_geo(ip=str(ip_res.get("ip") or ""), timeout_s=min(10, max(3, int(timeout_s))))
                base_res["ip"] = str(ip_res.get("ip") or "")
                if geo.get("ok"):
                    base_res["geo"] = geo
            if proxy_ip:
                base_res["proxy_ip"] = proxy_ip
            if proxy_geo:
                base_res["proxy_geo"] = proxy_geo
            return base_res
        base_err = str(base_res.get("error") or "")

    err_by_url: Dict[str, str] = {}
    for url, verify in [
        ("https://api.ipify.org?format=json", True),
        ("https://www.baidu.com", True),
        ("http://www.baidu.com", False),
    ]:
        r1 = try_get(url, verify=verify)
        if r1.get("ok"):
            if r1.get("ip"):
                geo = _ip_geo(ip=str(r1.get("ip") or ""), timeout_s=min(10, max(3, int(timeout_s))))
                if geo.get("ok"):
                    r1["geo"] = geo
            if proxy_ip:
                r1["proxy_ip"] = proxy_ip
            if proxy_geo:
                r1["proxy_geo"] = proxy_geo
            return r1
        err_by_url[url] = str(r1.get("error") or "")

    # 给前端更明确的失败原因（连接/认证/DNS/超时/证书等）
    parts: List[str] = []
    if base_url:
        parts.append(f"base={base_err or 'failed'}")
    parts.append(f"ipify={err_by_url.get('https://api.ipify.org?format=json') or 'failed'}")
    parts.append(f"baidu_https={err_by_url.get('https://www.baidu.com') or 'failed'}")
    parts.append(f"baidu_http={err_by_url.get('http://www.baidu.com') or 'failed'}")
    detail = " | ".join([p for p in parts if p])
    out: Dict[str, Any] = {"ok": False, "error": f"all_tests_failed: {detail}"}
    if proxy_ip:
        out["proxy_ip"] = proxy_ip
    if proxy_geo:
        out["proxy_geo"] = proxy_geo
    return out


def parse_xml_kv(text: str) -> Dict[str, str]:
    root = ET.fromstring(text)
    out: Dict[str, str] = {}
    for child in list(root):
        if child.tag:
            out[str(child.tag)] = (child.text or "").strip()
    return out


def member_ping_system_time(
    *,
    base_url: str,
    ver: str,
    uid: str,
    cookie: str,
    proxy: str = "",
    timeout_s: int = 15,
) -> Dict[str, Any]:
    """
    会员端在线检测（不下注）：p=get_systemTime
    成功：返回 XML <systemtime>...
    """
    base_url = str(base_url or "").rstrip("/")
    url = f"{base_url}/transform.php?ver={ver}"
    form = {"ver": ver, "p": "get_systemTime", "uid": uid, "langx": "zh-cn"}
    body = urlencode(form).encode("utf-8")
    headers = {
        "accept": "*/*",
        "accept-language": "zh-CN,zh;q=0.9",
        "cache-control": "no-cache",
        "pragma": "no-cache",
        "origin": base_url,
        "referer": base_url + "/",
        "content-type": "application/x-www-form-urlencoded",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36",
    }
    if cookie:
        headers["cookie"] = cookie

    try:
        sess = requests.Session()
        sess.trust_env = False
        proxies = {"http": proxy, "https": proxy} if proxy else {}
        r = sess.post(url, data=form, headers=headers, timeout=timeout_s, proxies=proxies, verify=False)
        r.raise_for_status()
        text = r.text or ""
    except Exception as e:
        return {"ok": False, "error": f"request_failed: {e}"}

    text_strip = (text or "").strip()
    if not text_strip:
        return {"ok": False, "error": "empty_response"}
    if not text_strip.startswith("<"):
        return {"ok": False, "error": "non_xml_response", "raw": text_strip[:200]}

    try:
        data = parse_xml_kv(text_strip)
    except Exception as e:
        return {"ok": False, "error": f"xml_parse_failed: {e}", "raw": text_strip[:200]}

    systemtime = data.get("systemtime") or ""
    if systemtime:
        return {"ok": True, "systemtime": systemtime, "data": data}
    code = data.get("code") or data.get("status") or ""
    return {"ok": False, "error": "no_systemtime", "code": code, "data": data}


def read_orders_file(path: Path, since: int = 0, limit: int = 200) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    # 简化：直接顺序读取，忽略最后一行可能的半写入
    try:
        # utf-8-sig 兼容某些工具写入的 BOM
        with path.open("r", encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                ts = int(obj.get("ts") or 0)
                if since and ts <= since:
                    continue
                out.append(obj)
    except Exception:
        return []
    # 最新在前
    out.sort(key=lambda x: int(x.get("ts") or 0), reverse=True)
    return out[: max(1, int(limit))]


def read_jsonl_file(path: Path, limit: int = 200) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if isinstance(obj, dict):
                    out.append(obj)
    except Exception:
        return []
    out.sort(key=lambda x: int(x.get("ts") or 0), reverse=True)
    return out[: max(1, int(limit))]


def list_targets(orders_dir: Path) -> List[str]:
    if not orders_dir.exists():
        return []
    names = []
    for p in orders_dir.glob("*.jsonl"):
        names.append(p.stem)
    return sorted(names)


class Handler(SimpleHTTPRequestHandler):
    def __init__(
        self,
        *args,
        directory: Optional[str] = None,
        orders_dir: Optional[Path] = None,
        state_dir: Optional[Path] = None,
        config_path: Optional[Path] = None,
        data_dir: Optional[Path] = None,
        **kwargs,
    ):
        self._orders_dir = orders_dir or Path("orders")
        self._state_dir = state_dir or Path("state")
        self._config_path = config_path or Path("config.json")
        self._data_dir = data_dir or Path("data")
        self._arsenal_path = self._data_dir / "arsenal.json"
        self._follow_settings_path = self._data_dir / "follow_settings.json"
        self._mappings_path = self._data_dir / "mappings.json"
        super().__init__(*args, directory=directory, **kwargs)

    def _send_json(self, status: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self) -> None:
        # 避免浏览器缓存旧版 webui/app.js 导致“订单不显示/逻辑不生效”看似没修复。
        try:
            if not str(self.path or "").startswith("/api/"):
                self.send_header("Cache-Control", "no-store")
                self.send_header("Pragma", "no-cache")
        except Exception:
            pass
        super().end_headers()

    def do_GET(self) -> None:
        u = urlparse(self.path)
        if u.path == "/api/targets":
            self._send_json(200, {"ok": True, "targets": list_targets(self._orders_dir), "server_time": now_iso()})
            return

        if u.path == "/api/orders":
            qs = parse_qs(u.query)
            limit = int((qs.get("limit") or ["200"])[0])
            since = int((qs.get("since") or ["0"])[0])
            target = (qs.get("target") or [""])[0].strip()

            orders: List[Dict[str, Any]] = []
            if target:
                path = self._orders_dir / f"{safe_name(target)}.jsonl"
                orders = read_orders_file(path, since=since, limit=limit)
            else:
                # 汇总所有 target
                for p in self._orders_dir.glob("*.jsonl"):
                    orders.extend(read_orders_file(p, since=since, limit=limit))
                orders.sort(key=lambda x: int(x.get("ts") or 0), reverse=True)
                orders = orders[: max(1, int(limit))]

            self._send_json(
                200,
                {
                    "ok": True,
                    "orders": orders,
                    "targets": list_targets(self._orders_dir),
                    "server_time": now_iso(),
                },
            )
            return

        if u.path == "/api/accounts":
            cfg = self._read_config()
            targets = cfg.get("targets") or []
            accounts = []
            for t in targets:
                accounts.append(
                    {
                        "name": t.get("name") or "",
                        "base_url": t.get("base_url") or "",
                        "username": t.get("username") or "",
                        "enabled": bool(t.get("enabled", True)),
                        "proxy": t.get("proxy") or "",
                        "telegram_chat_id": t.get("telegram_chat_id") or "",
                        "relogin_login_tab": t.get("relogin_login_tab") or 0,
                        "relogin_mode": t.get("relogin_mode") or "none",
                        "time_offset_hours": t.get("time_offset_hours") or 0,
                    }
                )
            self._send_json(200, {"ok": True, "accounts": accounts, "server_time": now_iso()})
            return

        if u.path == "/api/telegram-config":
            cfg = self._read_config()
            bot_token = str(cfg.get("telegram_bot_token") or "").strip()
            chat_id = str(cfg.get("telegram_chat_id") or "").strip()
            allow_chat = str(cfg.get("telegram_allow_chat") or "").strip()
            proxy = str(cfg.get("telegram_proxy") or "").strip()
            status_push_enabled = bool(cfg.get("telegram_status_push_enabled") or False)
            status_bot_token = str(cfg.get("telegram_status_bot_token") or "").strip()
            status_chat_id = str(cfg.get("telegram_status_chat_id") or "").strip()
            status_proxy = str(cfg.get("telegram_status_proxy") or "").strip()
            self._send_json(
                200,
                {
                    "ok": True,
                    "configured": bool(bot_token and chat_id),
                    "token_mask": mask_secret(bot_token, keep=4) if bot_token else "",
                    "chat_id": chat_id,
                    "allow_chat": allow_chat,
                    "proxy": proxy,
                    "status_push_enabled": status_push_enabled,
                    "status_configured": bool(status_bot_token and status_chat_id),
                    "status_token_mask": mask_secret(status_bot_token, keep=4) if status_bot_token else "",
                    "status_chat_id": status_chat_id,
                    "status_proxy": status_proxy,
                    "server_time": now_iso(),
                },
            )
            return

        if u.path == "/api/telegram-status-log":
            qs = parse_qs(u.query)
            limit = int((qs.get("limit") or ["200"])[0])
            path = self._data_dir / "telegram_status_log.jsonl"
            rows = read_jsonl_file(path, limit=max(1, min(1000, int(limit))))
            self._send_json(200, {"ok": True, "rows": rows, "server_time": now_iso()})
            return

        if u.path == "/api/arsenal":
            doc = read_json_file(self._arsenal_path, {"members": []})
            members = doc.get("members") or []
            out: List[Dict[str, Any]] = []
            for m in members:
                if not isinstance(m, dict):
                    continue
                disp = _proxy_display(m.get("proxy") or "")
                out.append(
                    {
                        "id": m.get("id") or "",
                        "label": m.get("label") or (m.get("username") or ""),
                        "base_url": m.get("base_url") or "",
                        "username": m.get("username") or "",
                        "proxy_label": disp.get("label") or "",
                        "proxy_ip": disp.get("proxy_ip") or "",
                        "proxy_geo": disp.get("proxy_geo"),
                        "status": m.get("status") or "unknown",
                        "last_check_at": float(m.get("last_check_at") or 0),
                        "last_error": m.get("last_error") or "",
                        "last_systemtime": m.get("last_systemtime") or "",
                    }
                )
            self._send_json(200, {"ok": True, "members": out, "server_time": now_iso()})
            return

        if u.path == "/api/follow-settings":
            doc = read_json_file(
                self._follow_settings_path,
                {
                    "enabled": False,
                    "mode": "fixed",
                    "fixed_amount": 50,
                    "ratio_percent": 100,
                    "match_limit_amount": 0,
                    "market_limit_amount": 0,
                    "split_count": 1,
                    "split_delay_seconds": 0,
                    "targets": [],
                    "members": [],
                    "updated_at": "",
                },
            )
            # 兼容旧文件：补齐累计限额字段，同时不再回传已废弃的单注封顶字段。
            if isinstance(doc, dict):
                doc.pop("max_amount", None)
                doc.setdefault("match_limit_amount", 0)
                doc.setdefault("market_limit_amount", 0)
                doc.setdefault("split_count", 1)
                doc.setdefault("split_delay_seconds", 0)
                doc.setdefault("targets", [])
            self._send_json(200, {"ok": True, "settings": doc, "server_time": now_iso()})
            return

        if u.path == "/api/mapping":
            doc = read_json_file(self._mappings_path, {})
            self._send_json(200, doc) # doc is directly the mappings object {mid: target}
            return

        if u.path == "/api/follow-results":
            qs = parse_qs(u.query)
            limit = int((qs.get("limit") or ["80"])[0])
            path = self._data_dir / "follow_results.jsonl"
            results = read_jsonl_file(path, limit=limit)
            self._send_json(200, {"ok": True, "results": results, "server_time": now_iso()})
            return

        if u.path.rstrip("/") == "/api/diagnostics":
            browsers = (os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or "").strip()
            browsers_exists = False
            if browsers and browsers not in ("0", "1"):
                try:
                    browsers_exists = Path(browsers).is_dir()
                except Exception:
                    browsers_exists = False
            detected = detect_playwright_browsers_path(APP_ROOT)
            core_running, core_pid_count = core_running_flag()
            self._send_json(
                200,
                {
                    "ok": True,
                    "server_time": now_iso(),
                    "cwd": os.getcwd(),
                    "python": sys.executable,
                    "app_root": str(APP_ROOT),
                    "playwright_browsers_path": browsers or "",
                    "playwright_browsers_path_exists": bool(browsers_exists),
                    "playwright_browsers_path_detected": detected or "",
                    "core_running": bool(core_running),
                    "core_pid_count": int(core_pid_count or 0),
                },
            )
            return

        if u.path == "/api/status":
            cfg = self._read_config()
            targets = cfg.get("targets") or []
            now = time.time()
            statuses: Dict[str, Any] = {}
            for t in targets:
                name = str(t.get("name") or "").strip()
                if not name:
                    continue
                st = self._read_state(name)
                last_ok_at = float(st.get("last_ok_at") or 0)
                last_poll_at = float(st.get("last_poll_at") or 0)
                last_status = str(st.get("last_status") or "").strip().lower()
                last_error_code = str(st.get("last_error_code") or "").strip()
                last_error_msg = str(st.get("last_error_msg") or "").strip()
                relogin_in_progress = bool(st.get("relogin_in_progress") or False)
                relogin_started_at = float(st.get("relogin_started_at") or 0)

                # UI 判定在线/离线使用固定阈值（用户要求：20秒）
                ttl_s = float(TARGET_ONLINE_TTL_S)
                status = "offline"
                # 只要最近成功拉到数据，就认为在线（绿灯），即使状态文件里还残留 relogin 标记。
                if last_ok_at and (now - last_ok_at) <= ttl_s:
                    status = "online"
                elif relogin_in_progress or last_status == "relogin":
                    # 黄灯：正在重登/等待重登后的首次成功拉单
                    # 若卡住过久则降级为离线（避免永远黄灯）
                    if relogin_started_at and (now - relogin_started_at) < 600:
                        status = "relogin"
                    else:
                        status = "offline"
                elif last_status == "offline" or last_error_code in {"4X014", "4X013", "logout", "login_required"}:
                    status = "offline"
                elif last_poll_at and (now - last_poll_at) <= ttl_s:
                    status = "offline"

                statuses[name] = {
                    "status": status,
                    "last_ok_at": last_ok_at,
                    "last_poll_at": last_poll_at,
                    "last_error_code": last_error_code,
                    "last_error_msg": last_error_msg,
                }

            core_running, core_pid_count = core_running_flag()
            self._send_json(200, {
                "ok": True,
                "statuses": statuses,
                "server_time": now_iso(),
                "uptime": int(time.time() - SERVER_START_TIME),
                "latency": get_latency(), 
                # 总开关：core worker 是否在跑（ag_wmc_watch/member_arsenal_watch/follow_engine/telegram_bot）
                "running": bool(core_running),
                "pid": None,
                "core_pid_count": int(core_pid_count or 0),
                # 用于确认当前运行的 WebUI 版本/路径（避免启动到别的目录的旧代码）
                "server_file": str(Path(__file__).resolve()),
                "server_mtime": int(Path(__file__).stat().st_mtime),
            })
            return



        return super().do_GET()

    def _read_config(self) -> Dict[str, Any]:
        try:
            raw = self._config_path.read_text(encoding="utf-8-sig")
            obj = json.loads(raw)
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass
        return {"targets": []}

    def _read_state(self, name: str) -> Dict[str, Any]:
        try:
            p = self._state_dir / f"{safe_name(name)}.json"
            if not p.exists():
                return {}
            raw = p.read_text(encoding="utf-8-sig")
            obj = json.loads(raw)
            return obj if isinstance(obj, dict) else {}
        except Exception:
            return {}

    def _make_unique_name(self, cfg: Dict[str, Any], base: str) -> str:
        targets = cfg.get("targets") or []
        existing = {str(t.get("name") or "") for t in targets}
        if base not in existing:
            return base
        i = 2
        while True:
            cand = f"{base}-{i}"
            if cand not in existing:
                return cand
            i += 1

    def _queue_manual_relogin(self, name: str) -> Tuple[bool, str]:
        try:
            p = self._state_dir / f"{safe_name(name)}.json"
            st = read_json_file(p, {})
            if not isinstance(st, dict):
                st = {}
            now = time.time()
            st["manual_relogin"] = 1
            st["manual_relogin_requested_at"] = now
            st["last_relogin_at"] = 0
            st["last_status"] = "relogin"
            st["relogin_in_progress"] = 1
            st["relogin_started_at"] = now
            atomic_write_json(p, st)
            return True, ""
        except Exception as e:
            return False, str(e)

    def do_POST(self) -> None:
        global WATCH_PROCESS
        u = urlparse(self.path)
        if u.path not in (
            "/api/accounts",
            "/api/accounts/relogin",
            "/api/accounts/delete",
            "/api/accounts/update",
            "/api/arsenal",
            "/api/arsenal/delete",
            "/api/arsenal/check",
            "/api/arsenal/update",
            "/api/proxy-test",
            "/api/follow-settings",
            "/api/telegram-config",
            "/api/telegram-test",
            "/api/telegram-status-test",
            "/api/telegram-test",
            "/api/telegram-status-test",
            "/api/mapping",
            "/api/accounts/add",
            "/api/start",
            "/api/stop",
            "/api/arsenal/add",
        ):
            self.send_error(404)
            return

        try:
            length = int(self.headers.get("content-length") or "0")
            body = self.rfile.read(length) if length > 0 else b"{}"
            data = json.loads(body.decode("utf-8"))
        except Exception:
            self._send_json(400, {"ok": False, "message": "无效JSON"})
            return

        if u.path == "/api/start":
            # 总开关：启动 core（不包含 WebUI），并对离线 target 排队“登入3”重登
            core_running, _n = core_running_flag()
            if core_running:
                self._send_json(200, {"ok": True, "status": "already_running"})
                return

            # 1) 智能排队重登：在线（20秒内 OK）就不重登；否则触发一次“登入3”
            try:
                cfg = self._read_config()
                targets = cfg.get("targets") or []
                now = time.time()
                queued = 0
                for t in targets:
                    if not isinstance(t, dict):
                        continue
                    if not bool(t.get("enabled", True)):
                        continue
                    name = str(t.get("name") or "").strip()
                    if not name:
                        continue
                    st = self._read_state(name)
                    last_ok_at = float(st.get("last_ok_at") or 0)
                    cookie = str(t.get("cookie") or "").strip()
                    if cookie and last_ok_at and (now - last_ok_at) <= float(TARGET_ONLINE_TTL_S):
                        continue
                    ok, _err = self._queue_manual_relogin(name)
                    if ok:
                        queued += 1
            except Exception:
                queued = 0

            # 2) 启动 core workers
            try:
                cmd = [sys.executable, "-u", "app.py", "start-core"]
                env = dict(os.environ)
                apply_playwright_browsers_path(env, APP_ROOT)
                p = subprocess.run(cmd, cwd=os.getcwd(), env=env, capture_output=True, text=True, timeout=20)
                if p.returncode in (0, 2):
                    self._send_json(200, {"ok": True, "status": "started", "queued_relogin": int(queued)})
                else:
                    err = (p.stderr or p.stdout or "").strip()
                    self._send_json(500, {"ok": False, "error": err[:500]})
            except Exception as e:
                self._send_json(500, {"ok": False, "error": str(e)})
            return

        if u.path == "/api/stop":
            core_running, _n = core_running_flag()
            if not core_running:
                self._send_json(200, {"ok": True, "status": "not_running"})
                return
            try:
                cmd = [sys.executable, "-u", "app.py", "stop-core", "--force"]
                env = dict(os.environ)
                apply_playwright_browsers_path(env, APP_ROOT)
                p = subprocess.run(cmd, cwd=os.getcwd(), env=env, capture_output=True, text=True, timeout=30)
                if p.returncode in (0, 1):
                    self._send_json(200, {"ok": True, "status": "stopped"})
                else:
                    err = (p.stderr or p.stdout or "").strip()
                    self._send_json(500, {"ok": False, "error": err[:500]})
            except Exception as e:
                self._send_json(500, {"ok": False, "error": str(e)})
            return

        if u.path == "/api/accounts/add":
            base_url = str(data.get("base_url") or "").strip()
            username = str(data.get("username") or "").strip()
            password = str(data.get("password") or "").strip()
            tab_raw = data.get("tab")

            if not base_url or not username or not password:
                self._send_json(400, {"ok": False, "message": "Missing required fields"})
                return

            cfg = self._read_config()
            cfg.setdefault("targets", [])
            
            # Generate unique name (username based)
            safe_user = safe_name(username)
            name = self._make_unique_name(cfg, safe_user)

            try:
                tab = int(tab_raw) if tab_raw is not None else 3
            except Exception:
                tab = 3
            if tab not in (0, 1, 2, 3):
                tab = 3
            
            # Default template
            new_target = {
                "name": name,
                "base_url": base_url,
                "username": username,
                "password": password,
                "enabled": True,
                "interval": 20,
                "timeout": 30,
                "proxy": "",
                "telegram_chat_id": "",
                "relogin_login_tab": tab,
                "relogin_mode": "auto",
                "time_offset_hours": 0
            }
            
            cfg["targets"].append(new_target)
            
            try:
                atomic_write_json(self._config_path, cfg)
                queued, err = self._queue_manual_relogin(name)
                payload: Dict[str, Any] = {"ok": True, "name": name, "relogin_queued": bool(queued)}
                if not queued:
                    payload["warning"] = f"queue relogin failed: {err}"
                self._send_json(200, payload)
            except Exception as e:
                self._send_json(500, {"ok": False, "message": f"Save failed: {e}"})
            return

        if u.path == "/api/accounts/update":
            name = str((data.get("name") or "")).strip()
            if not name:
                self._send_json(400, {"ok": False, "message": "缺少 name"})
                return

            patch = data.get("patch")
            if not isinstance(patch, dict):
                patch = {}

            # Allow-list fields only (avoid exposing passwords through UI).
            allowed: Dict[str, Any] = {}
            if "telegram_chat_id" in patch:
                allowed["telegram_chat_id"] = str(patch.get("telegram_chat_id") or "").strip()
            if "proxy" in patch:
                allowed["proxy"] = str(patch.get("proxy") or "").strip()
            if "enabled" in patch:
                allowed["enabled"] = bool(patch.get("enabled"))

            if not allowed:
                self._send_json(400, {"ok": False, "message": "no_patch"})
                return

            cfg = self._read_config()
            targets = cfg.get("targets") or []
            updated = False
            for t in targets:
                if not isinstance(t, dict):
                    continue
                if str(t.get("name") or "").strip() != name:
                    continue
                t.update(allowed)
                updated = True
                break

            if not updated:
                self._send_json(404, {"ok": False, "message": "未找到该账号"})
                return

            cfg["targets"] = targets
            try:
                atomic_write_json(self._config_path, cfg)
            except Exception as e:
                self._send_json(500, {"ok": False, "message": f"保存失败: {e}"})
                return

            self._send_json(200, {"ok": True})
            return

        if u.path == "/api/accounts/delete":
            name = str((data.get("name") or "")).strip()
            if not name:
                self._send_json(400, {"ok": False, "message": "缺少 name"})
                return
            cfg = self._read_config()
            targets = cfg.get("targets") or []
            before = len(targets)
            targets = [t for t in targets if str(t.get("name") or "") != name]
            if len(targets) == before:
                self._send_json(404, {"ok": False, "message": "未找到该账号"})
                return
            cfg["targets"] = targets
            try:
                atomic_write_json(self._config_path, cfg)
            except Exception as e:
                self._send_json(500, {"ok": False, "message": f"写入配置失败: {e}"})
                return
            self._send_json(200, {"ok": True, "removed": name})
            return

        if u.path == "/api/mapping":
            member_id = str(data.get("member_id") or "").strip()
            target = str(data.get("target") or "").strip()
            if not member_id:
                self._send_json(400, {"ok": False, "message": "missing member_id"})
                return
            
            doc = read_json_file(self._mappings_path, {})
            if not isinstance(doc, dict):
                doc = {}
            
            if target:
                doc[member_id] = target
            else:
                # empty target means remove mapping (follow all)
                doc.pop(member_id, None)
            
            try:
                atomic_write_json(self._mappings_path, doc)
            except Exception as e:
                self._send_json(500, {"ok": False, "message": f"save failed: {e}"})
                return
            
            self._send_json(200, {"ok": True})
            return

        if u.path == "/api/accounts/relogin":
            name = str((data.get("name") or "")).strip()
            if not name:
                self._send_json(400, {"ok": False, "message": "缺少 name"})
                return
            cfg = self._read_config()
            targets = cfg.get("targets") or []
            if not any(str(t.get("name") or "") == name for t in targets):
                self._send_json(404, {"ok": False, "message": "未找到该账号"})
                return

            # 若 core worker 未启动，则“重登”只会写 state，不会被执行（没有进程去消费）。
            # 这里 best-effort 拉起 core，避免用户误以为“点了没反应”。
            core_running, _n = core_running_flag()
            core_started = False
            if not core_running:
                try:
                    cmd = [sys.executable, "-u", "app.py", "start-core"]
                    env = dict(os.environ)
                    apply_playwright_browsers_path(env, APP_ROOT)
                    p = subprocess.run(cmd, cwd=os.getcwd(), env=env, capture_output=True, text=True, timeout=20)
                    if p.returncode in (0, 2):
                        core_started = True
                    else:
                        err = (p.stderr or p.stdout or "").strip()
                        self._send_json(500, {"ok": False, "message": (err[:500] or "start-core failed")})
                        return
                except Exception as e:
                    self._send_json(500, {"ok": False, "message": f"start-core failed: {e}"})
                    return

            # 通过 state 文件给 ag_wmc_watch 发信号：下一轮强制弹浏览器登录一次（你可手动点验证码）。
            try:
                p = self._state_dir / f"{safe_name(name)}.json"
                st = read_json_file(p, {})
                if not isinstance(st, dict):
                    st = {}
                st["manual_relogin"] = 1
                st["manual_relogin_requested_at"] = time.time()
                # 绕过最小间隔：用户点了就希望立刻弹窗登录
                st["last_relogin_at"] = 0
                st["last_status"] = "relogin"
                st["relogin_in_progress"] = 1
                st["relogin_started_at"] = time.time()
                atomic_write_json(p, st)
            except Exception as e:
                self._send_json(500, {"ok": False, "message": f"写入 state 失败: {e}"})
                return

            self._send_json(200, {"ok": True, "name": name, "core_started": bool(core_started)})
            return

        if u.path == "/api/arsenal/delete":
            member_id = str((data.get("id") or "")).strip()
            if not member_id:
                self._send_json(400, {"ok": False, "message": "缺少 id"})
                return
            doc = read_json_file(self._arsenal_path, {"members": []})
            members = [m for m in (doc.get("members") or []) if isinstance(m, dict)]
            before = len(members)
            members = [m for m in members if str(m.get("id") or "") != member_id]
            if len(members) == before:
                self._send_json(404, {"ok": False, "message": "未找到该会员账号"})
                return
            doc["members"] = members
            try:
                atomic_write_json(self._arsenal_path, doc)
            except Exception as e:
                self._send_json(500, {"ok": False, "message": f"写入失败: {e}"})
                return
            self._send_json(200, {"ok": True, "removed": member_id})
            return

        if u.path == "/api/arsenal/add":
            label = str(data.get("label") or "").strip()
            base_url = str(data.get("base_url") or "").strip().rstrip("/")
            username = str(data.get("username") or "").strip()
            password = str(data.get("password") or "").strip()
            if not base_url or not username:
                self._send_json(400, {"ok": False, "message": "缺少 base_url 或 username"})
                return

            doc = read_json_file(self._arsenal_path, {"members": []})
            members = doc.get("members")
            if not isinstance(members, list):
                members = []
                doc["members"] = members

            uid = str(data.get("uid") or "").strip()
            cookie = str(data.get("cookie") or "").strip()
            proxy = str(data.get("proxy") or "").strip()

            def _same_member(x: Any) -> bool:
                if not isinstance(x, dict):
                    return False
                x_base = str(x.get("base_url") or "").strip().rstrip("/")
                x_user = str(x.get("username") or "").strip()
                return (x_base == base_url) and (x_user == username)

            existing = next((m for m in members if _same_member(m)), None)
            if isinstance(existing, dict):
                if label:
                    existing["label"] = label
                if proxy:
                    existing["proxy"] = proxy
                if uid:
                    existing["uid"] = uid
                if cookie:
                    existing["cookie"] = cookie
                if password:
                    existing["password"] = password
                    if not (cookie or str(existing.get("cookie") or "").strip()):
                        existing["status"] = "relogin"
                        existing["last_relogin_at"] = 0
                        existing["last_error"] = ""

                existing["base_url"] = base_url
                existing["username"] = username
                existing["updated_at"] = time.time()
                try:
                    atomic_write_json(self._arsenal_path, doc)
                    self._send_json(200, {"ok": True, "id": str(existing.get("id") or ""), "updated": True})
                except Exception as e:
                    self._send_json(500, {"ok": False, "message": f"保存失败: {e}"})
                return

            # 兼容迁移：如果用户之前误把会员 base_url 填成后台域名（如 ag.hga030.com），
            # 允许用“同 username”覆盖更新 base_url（前提：旧记录还没形成有效会话，避免误改已在线账号）。
            def _same_username(x: Any) -> bool:
                if not isinstance(x, dict):
                    return False
                return str(x.get("username") or "").strip() == username

            existing_user = next((m for m in members if _same_username(m)), None)
            if isinstance(existing_user, dict):
                has_session = bool(str(existing_user.get("uid") or "").strip()) and bool(str(existing_user.get("cookie") or "").strip()) and bool(str(existing_user.get("ver") or "").strip())
                if not has_session:
                    existing_user["base_url"] = base_url
                    if label:
                        existing_user["label"] = label
                    if proxy:
                        existing_user["proxy"] = proxy
                    if uid:
                        existing_user["uid"] = uid
                    if cookie:
                        existing_user["cookie"] = cookie
                    if password:
                        existing_user["password"] = password
                    existing_user["status"] = ("online" if has_session else ("relogin" if (password and not cookie) else "unknown"))
                    existing_user["last_relogin_at"] = 0
                    existing_user["last_error"] = ""
                    existing_user["updated_at"] = time.time()
                    try:
                        atomic_write_json(self._arsenal_path, doc)
                        self._send_json(200, {"ok": True, "id": str(existing_user.get("id") or ""), "updated": True, "migrated_base_url": True})
                    except Exception as e:
                        self._send_json(500, {"ok": False, "message": f"保存失败: {e}"})
                    return

            mid = uuid.uuid4().hex

            new_member = {
                "id": mid,
                "label": label or username,
                "base_url": base_url,
                "username": username,
                "password": password,
                "proxy": proxy,
                "uid": uid,
                "mid": "",
                "cookie": cookie,
                "ver": "",
                "status": ("relogin" if (password and not cookie) else "unknown"),
                "last_check_at": 0,
                "last_ok_at": 0,
                "last_relogin_at": 0,
                "last_error": "",
                "last_systemtime": "",
                "auto_relogin": True,
                "relogin_headless": True,
                "relogin_login_tab": 3,
                "relogin_timeout_seconds": 120,
                "relogin_min_interval_seconds": 30,
                "currency": "",
                "cash": "",
                "maxcredit": "",
                "balance_updated_at": 0,
                "added_at": time.time(),
            }
            members.append(new_member)

            try:
                atomic_write_json(self._arsenal_path, doc)
                self._send_json(200, {"ok": True, "id": mid, "updated": False})
            except Exception as e:
                self._send_json(500, {"ok": False, "message": f"保存失败: {e}"})
            return

        if u.path == "/api/arsenal/update":
            member_id = str((data.get("id") or "")).strip()
            if not member_id:
                self._send_json(400, {"ok": False, "message": "missing id"})
                return

            doc = read_json_file(self._arsenal_path, {"members": []})
            members = [m for m in (doc.get("members") or []) if isinstance(m, dict)]
            m = next((x for x in members if str(x.get("id") or "") == member_id), None)
            if not m:
                self._send_json(404, {"ok": False, "message": "member not found"})
                return

            patch: Dict[str, Any] = {}
            for k in ("label", "base_url", "username", "password", "proxy", "uid", "cookie", "ver"):
                if k in data:
                    patch[k] = str(data.get(k) or "").strip()

            if patch.get("password") and not (patch.get("cookie") or str(m.get("cookie") or "").strip()):
                patch.setdefault("status", "relogin")
                patch.setdefault("last_relogin_at", 0)
                patch.setdefault("last_error", "")

            m.update(patch)
            doc["members"] = members
            try:
                atomic_write_json(self._arsenal_path, doc)
            except Exception as e:
                self._send_json(500, {"ok": False, "message": f"save failed: {e}"})
                return
            self._send_json(200, {"ok": True})
            return

        if u.path == "/api/arsenal/check":
            member_id = str((data.get("id") or "")).strip()
            if not member_id:
                self._send_json(400, {"ok": False, "message": "缺少 id"})
                return
            doc = read_json_file(self._arsenal_path, {"members": []})
            members = [m for m in (doc.get("members") or []) if isinstance(m, dict)]
            m = next((x for x in members if str(x.get("id") or "") == member_id), None)
            if not m:
                self._send_json(404, {"ok": False, "message": "未找到该会员账号"})
                return

            base_url = str(m.get("base_url") or "").strip().rstrip("/")
            username = str(m.get("username") or "").strip()
            password = str(m.get("password") or "").strip()
            ver = str(m.get("ver") or "").strip()
            uid = str(m.get("uid") or "").strip()
            cookie = str(m.get("cookie") or "").strip()
            proxy_raw = str(m.get("proxy") or "").strip()
            proxy = _normalize_proxy_input(proxy_raw)
            if proxy and proxy_raw != proxy:
                m["proxy"] = proxy

            m["last_check_at"] = time.time()

            if not (base_url and ver and uid and cookie):
                core_running, _core_pid_count = core_running_flag()
                if base_url and username and password:
                    if core_running:
                        m["status"] = "relogin"
                        m["last_error"] = "等待后台自动登录刷新会话"
                        m["last_relogin_at"] = 0
                    else:
                        m["status"] = "unknown"
                        m["last_error"] = "核心后台未启动：请先点击右上角“启动”，再自动登录刷新会话"
                else:
                    m["status"] = "unknown"
                    if not base_url:
                        m["last_error"] = "缺少 base_url"
                    elif not username:
                        m["last_error"] = "缺少 username"
                    else:
                        m["last_error"] = "缺少登录密码：请在“添加会员”里填写密码（或填入 uid/cookie/ver）"
                try:
                    atomic_write_json(self._arsenal_path, doc)
                except Exception:
                    pass
                self._send_json(200, {"ok": True, "status": m["status"], "message": m["last_error"]})
                return

            # WebUI 手动“检查”应尽量快返回；但走代理时网络抖动更常见，超时过短会误判离线
            timeout_s = 6 if not proxy else 15
            res = member_ping_system_time(base_url=base_url, ver=ver, uid=uid, cookie=cookie, proxy=proxy, timeout_s=timeout_s)
            if res.get("ok"):
                m["status"] = "online"
                m["last_error"] = ""
                m["last_systemtime"] = res.get("systemtime") or ""
            else:
                err = str(res.get("error") or res.get("code") or "offline")
                # 对“刚刚还在线”的会员：若只是短暂超时/代理抖动，不要立刻把状态从在线打成离线（避免 UI 抖动）
                try:
                    last_ok_at = float(m.get("last_ok_at") or 0)
                except Exception:
                    last_ok_at = 0.0
                now2 = time.time()
                if str(m.get("status") or "") == "online" and last_ok_at and (now2 - last_ok_at) <= 180 and err.startswith("request_failed:"):
                    m["last_error"] = err
                else:
                    m["status"] = "offline"
                    m["last_error"] = err

            try:
                atomic_write_json(self._arsenal_path, doc)
            except Exception as e:
                self._send_json(500, {"ok": False, "message": f"写入失败: {e}"})
                return

            self._send_json(
                200,
                {
                    "ok": True,
                    "status": m.get("status") or "unknown",
                    "systemtime": m.get("last_systemtime") or "",
                    "message": m.get("last_error") or "",
                },
            )
            return

        if u.path == "/api/follow-settings":
            # 兼容两种前端 payload：
            # 1) { "settings": {...} }（推荐）
            # 2) 直接提交 settings 对象（旧前端/误提交）
            settings = data.get("settings") if isinstance(data, dict) else None
            if not isinstance(settings, dict) and isinstance(data, dict):
                settings = data
            if not isinstance(settings, dict):
                self._send_json(400, {"ok": False, "message": "缺少 settings"})
                return

            doc = read_json_file(
                self._follow_settings_path,
                {
                    "enabled": False,
                    "mode": "fixed",
                    "fixed_amount": 50,
                    "ratio_percent": 100,
                    "match_limit_amount": 0,
                    "market_limit_amount": 0,
                    "split_count": 1,
                    "split_delay_seconds": 0,
                    "targets": [],
                    "members": [],
                    "updated_at": "",
                },
            )
            # 重要：只在请求显式携带字段时才更新，避免旧版 UI 缺字段覆盖已有设置。
            if "enabled" in settings:
                doc["enabled"] = bool(settings.get("enabled") or False)
            if "mode" in settings:
                doc["mode"] = str(settings.get("mode") or "fixed")
            if "fixed_amount" in settings:
                doc["fixed_amount"] = float(settings.get("fixed_amount") or 0)
            if "ratio_percent" in settings:
                doc["ratio_percent"] = float(settings.get("ratio_percent") or 0)
            if "match_limit_amount" in settings:
                doc["match_limit_amount"] = max(0.0, float(settings.get("match_limit_amount") or 0))
            if "market_limit_amount" in settings:
                doc["market_limit_amount"] = max(0.0, float(settings.get("market_limit_amount") or 0))
            doc.pop("max_amount", None)
            if "split_count" in settings:
                doc["split_count"] = int(settings.get("split_count") or 1)
            if "split_delay_seconds" in settings:
                doc["split_delay_seconds"] = float(settings.get("split_delay_seconds") or 0)
            # per-target 策略（白名单字段，避免前端误传敏感信息写入文件）
            if "targets" in settings:
                targets_in = settings.get("targets") or []
                targets_out: List[Dict[str, Any]] = []
                if isinstance(targets_in, list):
                    for t in targets_in:
                        if not isinstance(t, dict):
                            continue
                        name = str(t.get("name") or "").strip()
                        if not name:
                            continue
                        targets_out.append(
                            {
                                "name": name,
                                "only_live": bool(t.get("only_live") or False),
                            }
                        )
                doc["targets"] = targets_out
            if "members" in settings:
                doc["members"] = list(settings.get("members") or [])
            doc["updated_at"] = now_iso()

            try:
                atomic_write_json(self._follow_settings_path, doc)
            except Exception as e:
                self._send_json(500, {"ok": False, "message": f"写入失败: {e}"})
                return
            self._send_json(200, {"ok": True, "settings": doc})
            return

        if u.path == "/api/telegram-config":
            bot_token = str(data.get("bot_token") or "").strip()
            chat_id = str(data.get("chat_id") or "").strip()
            allow_chat = (str(data.get("allow_chat") or "").strip() if "allow_chat" in data else None)
            proxy = str(data.get("proxy") or "").strip()
            clear_token = bool(data.get("clear_token") or False)
            status_push_enabled = data.get("status_push_enabled", None)
            # status_* 仅在请求显式携带字段时才更新，避免被其它保存动作“误清空”
            status_bot_token = (str(data.get("status_bot_token") or "").strip() if "status_bot_token" in data else None)
            status_chat_id = (str(data.get("status_chat_id") or "").strip() if "status_chat_id" in data else None)
            status_proxy = (str(data.get("status_proxy") or "").strip() if "status_proxy" in data else None)
            clear_status_token = bool(data.get("clear_status_token") or False) if "clear_status_token" in data else False

            cfg = self._read_config()
            if clear_token:
                cfg["telegram_bot_token"] = ""
            elif bot_token:
                cfg["telegram_bot_token"] = bot_token
            if chat_id:
                cfg["telegram_chat_id"] = chat_id
            if allow_chat is not None:
                cfg["telegram_allow_chat"] = allow_chat
            if proxy or proxy == "":
                cfg["telegram_proxy"] = proxy
            if status_push_enabled is not None:
                cfg["telegram_status_push_enabled"] = bool(status_push_enabled)
            if clear_status_token:
                cfg["telegram_status_bot_token"] = ""
            elif status_bot_token is not None and status_bot_token:
                cfg["telegram_status_bot_token"] = status_bot_token
            if status_chat_id is not None:
                cfg["telegram_status_chat_id"] = status_chat_id
            if status_proxy is not None:
                cfg["telegram_status_proxy"] = status_proxy
            try:
                atomic_write_json(self._config_path, cfg)
            except Exception as e:
                self._send_json(500, {"ok": False, "message": f"写入配置失败: {e}"})
                return
            bt = str(cfg.get("telegram_bot_token") or "").strip()
            cid = str(cfg.get("telegram_chat_id") or "").strip()
            allow_chat2 = str(cfg.get("telegram_allow_chat") or "").strip()
            sbt = str(cfg.get("telegram_status_bot_token") or "").strip()
            scid = str(cfg.get("telegram_status_chat_id") or "").strip()
            self._send_json(
                200,
                {
                    "ok": True,
                    "configured": bool(bt and cid),
                    "token_mask": mask_secret(bt, keep=4) if bt else "",
                    "chat_id": cid,
                    "allow_chat": allow_chat2,
                    "proxy": str(cfg.get("telegram_proxy") or "").strip(),
                    "status_push_enabled": bool(cfg.get("telegram_status_push_enabled") or False),
                    "status_configured": bool(sbt and scid),
                    "status_token_mask": mask_secret(sbt, keep=4) if sbt else "",
                    "status_chat_id": scid,
                    "status_proxy": str(cfg.get("telegram_status_proxy") or "").strip(),
                },
            )
            return

        if u.path == "/api/telegram-status-test":
            cfg = self._read_config()
            # 允许测试时使用前端“当前输入值”（未保存也能测试），否则回退到 config.json
            bot_token = str(data.get("bot_token") or cfg.get("telegram_status_bot_token") or "").strip()
            chat_id = str(data.get("chat_id") or cfg.get("telegram_status_chat_id") or "").strip()
            proxy = str(data.get("proxy") or cfg.get("telegram_status_proxy") or cfg.get("telegram_proxy") or "").strip()
            msg = str(data.get("message") or "测试消息：TG 状态推送正常").strip()
            if not (bot_token and chat_id):
                self._send_json(400, {"ok": False, "message": "请先配置：状态 Bot Token + 状态 Chat ID/频道"})
                return
            res = tg_send_message(bot_token=bot_token, chat_id=chat_id, text=msg, proxy=proxy, timeout_s=15)
            if not res.get("ok"):
                self._send_json(500, {"ok": False, "message": f"发送失败: {redact_secret_in_text(str(res.get('error') or ''), bot_token)}"})
                return
            self._send_json(200, {"ok": True})
            return

        if u.path == "/api/telegram-test":
            cfg = self._read_config()
            # 允许测试时使用前端“当前输入值”（未保存也能测试），否则回退到 config.json
            bot_token = str(data.get("bot_token") or cfg.get("telegram_bot_token") or "").strip()
            chat_id = str(data.get("chat_id") or cfg.get("telegram_chat_id") or "").strip()
            proxy = str(data.get("proxy") or cfg.get("telegram_proxy") or "").strip()
            msg = str(data.get("message") or "测试消息：TG 推送正常").strip()
            if not (bot_token and chat_id):
                self._send_json(400, {"ok": False, "message": "请先在 Telegram 页面配置 bot_token + chat_id"})
                return
            res = tg_send_message(bot_token=bot_token, chat_id=chat_id, text=msg, proxy=proxy, timeout_s=15)
            if not res.get("ok"):
                self._send_json(500, {"ok": False, "message": f"发送失败: {redact_secret_in_text(str(res.get('error') or ''), bot_token)}"})
                return
            self._send_json(200, {"ok": True})
            return

        if u.path == "/api/proxy-test":
            proxy = str(data.get("proxy") or "").strip()
            timeout_s = int(data.get("timeout_s") or 10)
            base_url = str(data.get("base_url") or "").strip()
            if not proxy:
                self._send_json(200, {"ok": False, "message": "缺少 proxy"})
                return
            res = proxy_test(proxy=proxy, timeout_s=timeout_s, base_url=base_url)
            if not res.get("ok"):
                self._send_json(200, {"ok": False, "message": str(res.get("error") or "proxy_test_failed")})
                return
            self._send_json(200, res)
            return

        if u.path == "/api/arsenal":
            base_url = str((data.get("base_url") or "")).strip()
            ver = str((data.get("ver") or "")).strip()
            username = str((data.get("username") or "")).strip()
            password = str((data.get("password") or "")).strip()
            proxy = _normalize_proxy_input(str((data.get("proxy") or "")).strip())
            uid = str((data.get("uid") or "")).strip()
            mid = str((data.get("mid") or "")).strip()
            cookie = str((data.get("cookie") or "")).strip()
            label = str((data.get("label") or "")).strip() or username

            # ver/uid/cookie 可后续由 member_arsenal_watch.py 自动登录补齐
            if not base_url or not username or not password:
                self._send_json(400, {"ok": False, "message": "缺少 base_url/username/password"})
                return

            doc = read_json_file(self._arsenal_path, {"members": []})
            doc.setdefault("members", [])
            members = [m for m in (doc.get("members") or []) if isinstance(m, dict)]
            member_id = uuid.uuid4().hex
            members.append(
                {
                    "id": member_id,
                    "label": label,
                    "base_url": base_url,
                    "ver": ver,
                    "username": username,
                    "password": password,
                    "proxy": proxy,
                    "uid": uid,
                    "mid": mid,
                    "cookie": cookie,
                    "status": "unknown",
                    "last_check_at": 0,
                    "last_ok_at": 0,
                    "last_relogin_at": 0,
                    "last_error": "",
                    "last_systemtime": "",
                    "auto_relogin": True,
                    "relogin_headless": True,
                    "relogin_timeout_seconds": 120,
                    "relogin_min_interval_seconds": 30,
                }
            )
            doc["members"] = members
            try:
                atomic_write_json(self._arsenal_path, doc)
            except Exception as e:
                self._send_json(500, {"ok": False, "message": f"写入失败: {e}"})
                return

            self._send_json(200, {"ok": True, "member": {"id": member_id, "label": label, "username": username}})
            return

        base_url = str((data.get("base_url") or "")).strip()
        username = str((data.get("username") or "")).strip()
        password = str((data.get("password") or "")).strip()
        security_code = str((data.get("security_code") or "")).strip()
        login_tab = int(data.get("relogin_login_tab") or 3)
        time_offset_hours = int(data.get("time_offset_hours") or 0)
        if not base_url or not username or not password:
            self._send_json(400, {"ok": False, "message": "缺少 base_url/username/password"})
            return

        cfg = self._read_config()
        cfg.setdefault("targets", [])

        # 新账号：先让 watcher 自动登录生成 cookie/uid
        base_name = safe_name(f"{username}")
        name = self._make_unique_name(cfg, base_name)
        target = {
            "name": name,
            "base_url": base_url,
            "ver": "version-10-23",
            "login_layer": "ag",
            "uid": "",
            "langx": "zh-cn",
            "totalBets": "wmc",
            "gtype": "ALL",
            "sel_maxid": "0",
            "cookie": "",
            "cooldown_on_m_rep": False,
            "offline_alert_seconds": 30,
            "relogin_mode": "playwright",
            "relogin_headless": False,
            "relogin_timeout_seconds": 300,
            "relogin_min_interval_seconds": 30,
            # 默认不在启动/新增时自动弹窗登录：由 WebUI 的“登”按钮手动触发（便于你自己点验证码）。
            "relogin_on_start": False,
            "relogin_login_tab": login_tab,
            "write_cookie_to_config": True,
            "username": username,
            "password": password,
            "security_code": security_code,
            "time_offset_hours": time_offset_hours,
            "telegram_chat_id": "",
        }

        cfg["targets"].append(target)
        try:
            atomic_write_json(self._config_path, cfg)
        except Exception as e:
            self._send_json(500, {"ok": False, "message": f"写入配置失败: {e}"})
            return

        self._send_json(200, {"ok": True, "account": {"name": name, "username": username, "base_url": base_url}})


def main() -> int:
    parser = argparse.ArgumentParser(description="AG Orders WebUI (no deps)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9999)
    parser.add_argument("--orders-dir", default="orders")
    parser.add_argument("--webui-dir", default="webui")
    parser.add_argument("--state-dir", default="state")
    parser.add_argument("--config", default="config.json", help="监听脚本使用的 config.json（用于添加账号）")
    parser.add_argument("--data-dir", default="data", help="WebUI 本地数据目录（弹药库/跟单设置）")
    args = parser.parse_args()

    orders_dir = Path(args.orders_dir)
    webui_dir = Path(args.webui_dir)
    state_dir = Path(args.state_dir)
    config_path = Path(args.config)
    data_dir = Path(args.data_dir)
    if not webui_dir.exists():
        raise SystemExit(f"WebUI 目录不存在: {webui_dir}")

    data_dir.mkdir(parents=True, exist_ok=True)
    ensure_json_file(data_dir / "arsenal.json", {"members": []})
    ensure_json_file(data_dir / "mappings.json", {})
    ensure_json_file(
        data_dir / "follow_settings.json",
        {
            "enabled": False,
            "mode": "fixed",
            "fixed_amount": 50,
            "ratio_percent": 100,
            "match_limit_amount": 0,
            "market_limit_amount": 0,
            "split_count": 1,
            "split_delay_seconds": 0,
            "targets": [],
            "members": [],
            "updated_at": "",
        },
    )

    def handler_factory(*h_args, **h_kwargs):
        return Handler(
            *h_args,
            directory=str(webui_dir),
            orders_dir=orders_dir,
            state_dir=state_dir,
            config_path=config_path,
            data_dir=data_dir,
            **h_kwargs,
        )

    httpd = ThreadingHTTPServer((args.host, args.port), handler_factory)
    print(f"[{now_iso()}] WebUI: http://{args.host}:{args.port}  orders_dir={orders_dir}")
    httpd.serve_forever()


if __name__ == "__main__":
    raise SystemExit(main())
