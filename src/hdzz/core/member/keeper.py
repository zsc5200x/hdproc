import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import ipaddress
import json
import os
import re
import shutil
import sys
import threading
import time
import uuid
import warnings

# 避免 macOS 系统 Python（LibreSSL）触发 urllib3 v2 的提示刷屏（需在 import requests 前设置）
warnings.filterwarnings("ignore", message=r"urllib3 v2 only supports OpenSSL.*")

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse, urlencode

import requests
from tg_status_log import log_and_maybe_push
from playwright_proxy import build_playwright_proxy
from portable_env import ensure_playwright_browsers_path
from hdzz.core.member.adspower_api import (
    DEFAULT_ADSPOWER_API_BASE,
    adspower_cookie_header_from_cookies,
    adspower_extract_uid_candidates_from_cookies,
    adspower_extract_ver_candidates_from_cookies,
    adspower_find_profile,
    adspower_get_profile_cookies,
    adspower_infer_base_url_from_cookies,
    adspower_profile_proxy_url,
    adspower_start_profile,
    adspower_stop_profile,
    normalize_adspower_api_base,
)
from hdzz.core.member.session_state import (
    cookie_fingerprint as session_cookie_fingerprint,
    cookie_names as session_cookie_names,
    session_fingerprint as member_session_fingerprint,
)

try:
    import urllib3  # type: ignore

    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:
    pass

_APP_DIR = Path(os.environ.get("HDZZ_APP_DIR") or Path.cwd())
ensure_playwright_browsers_path(_APP_DIR)

_KEEPER_LOCK_FH = None
_MEMBER_DOC_LOCK = threading.RLock()


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def now_ts() -> float:
    return time.time()


ONLINE_GRACE_SECONDS = 180
REQUEST_BACKOFF_BASE_SECONDS = 20
REQUEST_BACKOFF_MAX_SECONDS = 180
# 会员端掉线后要尽快恢复；重登失败退避上限保持较小，避免长时间“卡死”在离线状态。
RELOGIN_BACKOFF_MAX_SECONDS = 30
# 登录超时后尽快切换可视登录，避免长时间停留在“重登中”。
RELOGIN_TIMEOUT_SWITCH_VISIBLE_FAILS = 1
# 非请求型探活异常（空响应/非 XML/服务端业务错误）达到阈值后才允许重登。
TRANSIENT_FAILS_BEFORE_RELOGIN = 3
NETWORK_COOLDOWN_SECONDS = 30

# 登录响应返回成功后，站点仍可能通过异步脚本继续写入 Cookie。只用一个
# 固定 sleep 会在不同机器/网络上产生竞态，因此使用短窗口内的连续采样。
SESSION_STABILIZATION_INTERVAL_MS = 250
SESSION_STABILIZATION_CONSECUTIVE_SAMPLES = 3
SESSION_STABILIZATION_TIMEOUT_MS = 5000


def _login_form_wait_timeout_ms(timeout_s: int) -> int:
    """给动态登录页足够加载时间，同时保留至少 15 秒的下限。"""
    try:
        configured_ms = int(timeout_s) * 1000
    except Exception:
        configured_ms = 0
    return max(15_000, configured_ms)


def normalize_member_base_url(value: Any) -> str:
    text = str(value or "").strip().rstrip("/")
    if not text:
        return ""
    if text.startswith("//"):
        return ("https:" + text).rstrip("/")
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", text):
        return text
    return "https://" + text.lstrip("/")


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


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


def _calc_backoff_seconds(*, fail_count: int, base_s: int, max_s: int) -> int:
    n = max(1, int(fail_count))
    base = max(1, int(base_s))
    cap = max(base, int(max_s))
    factor = 2 ** max(0, min(6, n - 1))
    return int(min(cap, base * factor))


def _is_transient_request_failure(err_msg: str) -> bool:
    s = str(err_msg or "").lower()
    if not s:
        return False
    keys = (
        "502",
        "bad gateway",
        "remote disconnected",
        "connection aborted",
        "connection reset",
        "read timed out",
        "timed out",
        "max retries exceeded",
        "newconnectionerror",
        "proxyerror",
    )
    return any(k in s for k in keys)


def _network_error_class(err_msg: str) -> str:
    """Classify read-only transport failures separately from session state."""

    s = str(err_msg or "").lower()
    if "502" in s or "bad gateway" in s:
        return "UPSTREAM_502"
    if "read timed out" in s or "readtimeout" in s:
        return "READ_TIMEOUT"
    if "ssl" in s and ("eof" in s or "error" in s):
        return "SSL_EOF"
    if "proxyerror" in s:
        return "PROXY_ERROR"
    if "connection reset" in s or "connection aborted" in s or "remote disconnected" in s:
        return "CONNECTION_ERROR"
    return ""


def _force_system_chrome_only() -> bool:
    return _to_bool(os.environ.get("HDZZ_PLAYWRIGHT_SYSTEM_CHROME_ONLY"), False)


def _playwright_launch_args(platform_name: str) -> list[str]:
    args = [
        "--disable-blink-features=AutomationControlled",
        "--ignore-certificate-errors",
        "--disable-gpu",
        "--disable-dev-shm-usage",
    ]
    if str(platform_name or "").startswith("win"):
        args.extend(
            [
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-features=RendererCodeIntegrity",
            ]
        )
    return args


def _playwright_launch_candidates(
    *,
    platform_name: str,
    prefer_system_chrome: bool,
    force_system_chrome_only: bool,
) -> list[tuple[str, dict[str, Any]]]:
    candidates: list[tuple[str, dict[str, Any]]] = [("bundled_chromium", {})]
    platform_name = str(platform_name or "")

    if force_system_chrome_only:
        return [("channel_chrome", {"channel": "chrome"})]

    if platform_name == "darwin":
        if prefer_system_chrome:
            return [("channel_chrome", {"channel": "chrome"}), ("bundled_chromium", {})]
        return [("channel_chrome", {"channel": "chrome"}), ("bundled_chromium", {})]

    if platform_name.startswith("win"):
        if prefer_system_chrome:
            return [("channel_chrome", {"channel": "chrome"}), ("bundled_chromium", {})]
        return [("bundled_chromium", {}), ("channel_chrome", {"channel": "chrome"})]

    return candidates


def _is_relogin_timeout_error(err_msg: str) -> bool:
    s = str(err_msg or "").lower()
    if not s:
        return False
    if "timeout" in s and "waiting for event \"response\"" in s:
        return True
    if "等待登录响应超时" in str(err_msg or ""):
        return True
    return False


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


def _trigger_and_wait_for_response(
    page: Any,
    predicate: Callable[[Any], bool],
    trigger: Callable[[], None],
    *,
    timeout_ms: int,
) -> Any:
    """Arm the response listener before triggering a fast login request."""
    expect_response = getattr(page, "expect_response", None)
    if callable(expect_response):
        with expect_response(predicate, timeout=timeout_ms) as info:
            trigger()
        return info.value

    matched: list[Any] = []

    def on_response(response: Any) -> None:
        try:
            if predicate(response):
                matched.append(response)
        except Exception:
            pass

    add_listener = getattr(page, "on", None)
    if not callable(add_listener):
        raise RuntimeError("当前 Playwright 版本不支持 response 监听")

    add_listener("response", on_response)
    try:
        trigger()
        deadline = time.time() + max(0.001, int(timeout_ms) / 1000.0)
        while time.time() < deadline:
            if matched:
                return matched[0]
            remain_ms = max(1, int((deadline - time.time()) * 1000))
            page.wait_for_timeout(min(100, remain_ms))
        raise TimeoutError(f"Timeout {int(timeout_ms)}ms exceeded while waiting for event response")
    finally:
        remove_listener = getattr(page, "remove_listener", None)
        if callable(remove_listener):
            try:
                remove_listener("response", on_response)
            except Exception:
                pass


def _is_recoverable_profile_launch_error(err: Exception) -> bool:
    if _is_target_closed_error(err):
        return True
    s = str(err or "").lower()
    if not s:
        return False
    keys = (
        "processsingleton",
        "singletonlock",
        "profile is already in use",
        "profile directory is already in use",
        "user data directory is already in use",
    )
    return any(k in s for k in keys)


def _reset_playwright_profile_dir(user_data_dir: Path) -> bool:
    try:
        if user_data_dir.exists():
            shutil.rmtree(user_data_dir, ignore_errors=False)
        user_data_dir.mkdir(parents=True, exist_ok=True)
        return True
    except Exception:
        try:
            user_data_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        return False


def _launch_persistent_context_with_recovery(
    pw: Any,
    *,
    user_data_dir: Path,
    headless: bool,
    proxy_cfg: Optional[Dict[str, Any]],
    log: Callable[[str], None],
    platform_name: str,
    prefer_system_chrome: bool = False,
) -> Any:
    launch_kwargs = {
        "headless": bool(headless),
        "viewport": {"width": 1280, "height": 860},
        "ignore_https_errors": True,
        "args": _playwright_launch_args(platform_name),
        "proxy": proxy_cfg,
    }
    force_system_chrome_only = _force_system_chrome_only()
    candidates = _playwright_launch_candidates(
        platform_name=platform_name,
        prefer_system_chrome=prefer_system_chrome,
        force_system_chrome_only=force_system_chrome_only,
    )

    launch_errors: list[tuple[str, Exception]] = []
    for attempt in range(2):
        launch_errors = []
        for idx, (name, extra) in enumerate(candidates):
            try:
                ctx_local = pw.chromium.launch_persistent_context(
                    str(user_data_dir),
                    **launch_kwargs,
                    **extra,
                )
                if name == "channel_chrome":
                    if force_system_chrome_only:
                        log("已启用强制系统 Chrome 模式（HDZZ_PLAYWRIGHT_SYSTEM_CHROME_ONLY=1）")
                    elif idx == 0:
                        log("macOS 环境已优先使用本机 Chrome（channel=chrome）")
                    else:
                        log("检测到内置 Chromium 启动异常，已自动回退本机 Chrome（channel=chrome）")
                return ctx_local
            except Exception as e_launch:
                launch_errors.append((name, e_launch))
                continue

        if attempt == 0:
            recoverable = any(_is_recoverable_profile_launch_error(err) for _, err in launch_errors)
            has_existing_profile = False
            try:
                has_existing_profile = user_data_dir.exists() and any(user_data_dir.iterdir())
            except Exception:
                has_existing_profile = user_data_dir.exists()
            if recoverable and has_existing_profile and _reset_playwright_profile_dir(user_data_dir):
                log("检测到浏览器 profile 可能来自旧机器或已损坏，已清理该会员的本地浏览器缓存后重试一次")
                continue
        break

    e = launch_errors[0][1] if launch_errors else RuntimeError("unknown")
    browsers = (os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or "").strip()
    hint = []
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


def _is_transient_keepalive_error(err_msg: str) -> bool:
    s = str(err_msg or "").lower()
    if not s:
        return False
    keys = (
        "server_error",
        "double_login",
        "empty_response",
        "non_xml_response",
        "xml_parse_failed",
        "unexpected_response",
        "request_failed",
    )
    return any(k in s for k in keys)


def _is_login_invalid_keepalive_error(err_msg: str, data: Optional[Dict[str, Any]] = None) -> bool:
    s = str(err_msg or "").strip().lower()
    data_text = ""
    if isinstance(data, dict):
        data_text = " ".join(
            str(data.get(k) or "")
            for k in ("code", "status", "msg", "message", "error")
        ).strip().lower()
    haystack = f"{s} {data_text}".strip()
    if not haystack:
        return False
    if s.startswith("double_login") or "doublelogin" in haystack:
        return True
    keys = (
        "login_required",
        "logged_out",
        "logout",
        "4x014",
        "4x013",
        "checkemnu",
        "goToHome".lower(),
        "please login",
        "login first",
        "not login",
        "not logged in",
        "session expired",
        "请重新登录",
        "請重新登入",
        "请先登录",
        "未登录",
        "登录超时",
        "登入超时",
    )
    return any(k in haystack for k in keys)


def acquire_single_instance_lock(lock_path: Path) -> bool:
    """
    防止同一个 data_dir 下同时跑多个 member_arsenal_watch：
    - 多实例会同时操作同一个 Playwright profile（SingletonLock），导致自动重登频繁失败；
    - 会造成会员状态抖动（online/offline 来回）、跟单时出现 1X014（登录失效）。
    """
    global _KEEPER_LOCK_FH
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fh = lock_path.open("a+", encoding="utf-8")
    except Exception:
        return False

    try:
        fh.seek(0)
        fh.truncate(0)
        fh.write(f"pid={os.getpid()}\nstarted_at={now_iso()}\n")
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

    _KEEPER_LOCK_FH = fh
    return True


def safe_name(name: str) -> str:
    s = "".join(c for c in str(name) if c.isalnum() or c in ("-", "_")).strip()
    return s or "default"

def _is_ip_host(host: str) -> bool:
    h = str(host or "").strip()
    if not h:
        return False
    try:
        ipaddress.ip_address(h)
        return True
    except Exception:
        return False


def _origin_from_url(url: str) -> str:
    u = urlparse(str(url or "").strip())
    if str(u.scheme or "").lower() not in {"http", "https"}:
        return ""
    netloc = str(u.netloc or "").strip()
    host = str(u.hostname or "").strip().lower()
    if not netloc or not host:
        return ""
    if host in {"localhost", "0.0.0.0"}:
        return ""
    if host.startswith("127."):
        return ""
    if host.endswith(".local"):
        return ""
    if "adspower" in host:
        return ""
    return f"{u.scheme}://{netloc}"


def _infer_base_url_from_page_urls(urls: list[str]) -> str:
    for raw in urls or []:
        origin = _origin_from_url(raw)
        if origin:
            return origin.rstrip("/")
    return ""


def _should_navigate_ads_page(current_url: str, base_url: str) -> bool:
    base_origin = _origin_from_url(base_url)
    if not base_origin:
        return False
    cur = str(current_url or "").strip()
    if not cur:
        return True
    u = urlparse(cur)
    scheme = str(u.scheme or "").lower()
    if scheme not in {"http", "https"}:
        return True
    host = str(u.hostname or "").strip().lower()
    if not host:
        return True
    if host in {"localhost", "0.0.0.0"}:
        return True
    if host.startswith("127."):
        return True
    if host.endswith(".local"):
        return True
    if "adspower" in host:
        return True

    base_host = str(urlparse(base_origin).hostname or "").strip().lower()
    if base_host and host == base_host:
        return False
    # 已经在任意业务站页面时，不强制跳转，避免人工输入账号时被刷新打断
    return False


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
    if len(parts) >= 4 and parts[0] and parts[1]:
        ip, port, user, pwd = parts[:4]
        if user and pwd:
            return f"socks5h://{user}:{pwd}@{ip}:{port}"
        return f"socks5h://{ip}:{port}"
    return proxy


def _parse_proxy_for_playwright(proxy: str) -> Optional[Dict[str, Any]]:
    # 统一走桥接器：socks5 带鉴权会自动桥接为本地无鉴权 socks5
    return build_playwright_proxy(proxy)


def atomic_write_json(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # IMPORTANT: 多进程同时写同一个 *.tmp 会导致文件内容互相覆盖/交错，最终把 JSON 写坏。
    tmp = path.with_suffix(path.suffix + f".tmp.{uuid.uuid4().hex}")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")

    # Windows 上偶发：目标文件被短暂占用（杀软/索引/并发读写），os.replace 会抛 PermissionError
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


def parse_xml_kv(text: str) -> Dict[str, str]:
    root = ET.fromstring(text)
    out: Dict[str, str] = {}
    for child in list(root):
        if child.tag:
            out[str(child.tag)] = (child.text or "").strip()
    return out


def _uniq_keep_order(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for x in items:
        s = str(x or "").strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _extract_ver_candidates_from_text(text: str) -> list[str]:
    s = str(text or "")
    vals = re.findall(r"transform\.php\?ver=([^\"'&<>\s]+)", s, flags=re.IGNORECASE)
    vals.extend(re.findall(r"[?&]ver=([0-9A-Za-z._-]{4,})", s, flags=re.IGNORECASE))
    vals.extend(re.findall(r"\b(?:mygamever|gamever|ver)\s*[:=]\s*['\"]?([0-9A-Za-z._-]{4,})", s, flags=re.IGNORECASE))
    return _uniq_keep_order(vals)


def _extract_ver_candidates_from_cookies(cookies: list[Dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for c in cookies or []:
        name = str(c.get("name") or "")
        value = str(c.get("value") or "").strip()
        if not value:
            continue
        if re.search(r"ver", name, flags=re.IGNORECASE):
            out.append(value)
        out.extend(re.findall(r"(?:^|[;,&\\s])(?:mygamever|gamever|ver)=([0-9A-Za-z._-]{4,})", value, flags=re.IGNORECASE))
    return _uniq_keep_order(out)


def _extract_uid_candidates_from_cookies(cookies: list[Dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for c in cookies or []:
        name = str(c.get("name") or "")
        value = str(c.get("value") or "")
        m = re.match(r"login_([0-9A-Za-z_-]{4,})$", name)
        if m:
            out.append(m.group(1))
        if re.fullmatch(r"(?:uid|userid|user_id|loginuid)", name, flags=re.IGNORECASE):
            out.append(value)
        out.extend(re.findall(r"(?:^|[;,&\\s])(?:uid|userid|user_id|loginuid)=([0-9A-Za-z_-]{4,})", value, flags=re.IGNORECASE))
    return _uniq_keep_order(out)


def _extract_uid_candidates_from_text(text: str) -> list[str]:
    s = str(text or "")
    out = re.findall(r"[?&]uid=([0-9A-Za-z_-]{4,})", s, flags=re.IGNORECASE)
    out.extend(re.findall(r"\b(?:uid|userid|user_id|loginuid)\s*[:=]\s*['\"]?([0-9A-Za-z_-]{4,})", s, flags=re.IGNORECASE))
    return _uniq_keep_order(out)


def dedupe_cookie_pairs(pairs: list[Tuple[str, str]]) -> str:
    seen = set()
    out = []
    for k, v in pairs:
        k = str(k or "").strip()
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(f"{k}={v}")
    return "; ".join(out)


def cookies_to_header(cookies: list[Dict[str, Any]], host: str) -> str:
    host = (host or "").lower().lstrip(".")
    host_is_ip = _is_ip_host(host)
    pairs: list[Tuple[str, str]] = []
    for c in cookies:
        name = str(c.get("name") or "")
        value = str(c.get("value") or "")
        domain = str(c.get("domain") or "").lower().lstrip(".")
        if not name:
            continue
        # base_url 为 IP 时，domain 往往是站点域名（例如 mos077.com），如果按 host 过滤会把关键 cookie 丢掉，
        # 导致后续接口出现 code_type is null / CheckEMNU 等“看起来有盘但抓不到”的问题。
        if host_is_ip:
            pairs.append((name, value))
            continue
        # Playwright 可能返回 host-only cookie 的 domain 为空；此时也应保留
        if not domain:
            pairs.append((name, value))
            continue
        if host and (domain == host or host.endswith(domain) or domain.endswith(host)):
            pairs.append((name, value))
    return dedupe_cookie_pairs(pairs)


def _cookie_capture_fingerprint(cookies: list[Dict[str, Any]]) -> Tuple[list[str], str]:
    """Return cookie names and a value fingerprint without exposing values."""

    pairs: list[Tuple[str, str]] = []
    for item in cookies or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        pairs.append((name, str(item.get("value") or "")))
    pairs.sort(key=lambda pair: pair[0].casefold())
    names = sorted({name for name, _ in pairs}, key=str.casefold)
    digest = hashlib.sha256(
        "\n".join(f"{name}={value}" for name, value in pairs).encode("utf-8")
    ).hexdigest()
    return names, digest


def stabilize_browser_session(
    context: Any,
    page: Any,
    *,
    uid: str = "",
    ver: str = "",
    base_url: str = "",
    host: str = "",
    timeout_ms: int = SESSION_STABILIZATION_TIMEOUT_MS,
    interval_ms: int = SESSION_STABILIZATION_INTERVAL_MS,
    consecutive_samples: int = SESSION_STABILIZATION_CONSECUTIVE_SAMPLES,
    require_uid: bool = True,
) -> Dict[str, Any]:
    """Capture a browser session only after its identity remains stable.

    The helper intentionally keeps raw cookie values in memory only. The
    returned metadata is safe to persist in ``arsenal.json`` and diagnostics.
    ``require_uid=False`` is used while discovering a UID from an already
    logged-in page; the final capture always requires the verified UID.
    """

    timeout_eff = max(1, int(timeout_ms or SESSION_STABILIZATION_TIMEOUT_MS))
    interval_eff = max(1, int(interval_ms or SESSION_STABILIZATION_INTERVAL_MS))
    samples_needed = max(2, int(consecutive_samples or SESSION_STABILIZATION_CONSECUTIVE_SAMPLES))
    deadline = time.monotonic() + (timeout_eff / 1000.0)
    previous_fingerprint = ""
    stable_count = 0
    last_capture: Dict[str, Any] = {}

    while True:
        try:
            cookies = list(context.cookies() or [])
        except Exception as exc:
            raise RuntimeError(f"SESSION_NOT_STABLE: cookies unavailable ({type(exc).__name__})") from exc

        page_url = ""
        try:
            page_url = str(page.url or "")
        except Exception:
            page_url = ""
        host_eff = str(host or "").strip()
        try:
            host_eff = urlparse(page_url).hostname or host_eff
        except Exception:
            pass
        base_eff = _infer_base_url_from_page_urls([page_url]) or normalize_member_base_url(base_url)
        cookie_str = cookies_to_header(cookies, host=host_eff or (urlparse(base_eff).hostname or ""))
        cookie_names, cookie_fingerprint = _cookie_capture_fingerprint(cookies)
        uid_text = str(uid or "").strip()
        ver_text = str(ver or "").strip()
        session_fingerprint = hashlib.sha256(
            "|".join((base_eff, uid_text, ver_text, cookie_fingerprint)).encode("utf-8")
        ).hexdigest()
        last_capture = {
            "cookie": cookie_str,
            "uid": uid_text,
            "ver": ver_text,
            "base_url": base_eff,
            "cookie_names": cookie_names,
            "cookie_fingerprint": cookie_fingerprint,
            "session_fingerprint": session_fingerprint,
        }

        valid = bool(cookie_str and ver_text and (uid_text or not require_uid))
        if valid and session_fingerprint == previous_fingerprint:
            stable_count += 1
        else:
            stable_count = 1 if valid else 0
        previous_fingerprint = session_fingerprint
        if valid and stable_count >= samples_needed:
            last_capture["stable_samples"] = stable_count
            last_capture["session_stable"] = True
            last_capture["session_ready_reason"] = "SESSION_STABLE"
            return last_capture

        if time.monotonic() >= deadline:
            raise RuntimeError(
                "SESSION_NOT_STABLE: identity did not remain stable within "
                f"{timeout_eff}ms (cookie_count={len(cookie_names)})"
            )
        try:
            page.wait_for_timeout(min(interval_eff, max(1, int((deadline - time.monotonic()) * 1000))))
        except Exception:
            time.sleep(min(interval_eff, 50) / 1000.0)


def member_ping_system_time(
    *,
    base_url: str,
    ver: str,
    uid: str,
    cookie: str,
    proxy: str = "",
    timeout_s: int = 15,
) -> Dict[str, Any]:
    base_url = normalize_member_base_url(base_url)
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
        # 禁用系统/环境代理，避免误走 127.0.0.1
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


def member_ping_member_data(
    *,
    base_url: str,
    ver: str,
    uid: str,
    cookie: str,
    proxy: str = "",
    timeout_s: int = 15,
) -> Dict[str, Any]:
    """
    判断“是否真的已登录”。
    注意：get_systemTime 在部分站点不需要完整登录态，会误判 online。
    """
    base_url = normalize_member_base_url(base_url)
    url = f"{base_url}/transform.php?ver={ver}"
    form = {"ver": ver, "p": "get_member_data", "uid": uid, "langx": "zh-cn", "change": "all"}
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

    text = ""
    proxies = {"http": proxy, "https": proxy} if proxy else {}
    last_exc: Optional[Exception] = None
    for i in range(2):
        try:
            sess = requests.Session()
            sess.trust_env = False
            r = sess.post(url, data=form, headers=headers, timeout=timeout_s, proxies=proxies, verify=False)
            r.raise_for_status()
            text = r.text or ""
            last_exc = None
            break
        except Exception as e:
            last_exc = e
            if i == 0 and _is_transient_request_failure(str(e)):
                try:
                    time.sleep(0.2)
                except Exception:
                    pass
                continue
            break
        finally:
            try:
                sess.close()
            except Exception:
                pass
    if last_exc is not None:
        return {"ok": False, "error": f"request_failed: {last_exc}"}

    text_strip = (text or "").strip()
    if not text_strip:
        return {"ok": False, "error": "empty_response"}
    if not text_strip.startswith("<"):
        return {"ok": False, "error": "non_xml_response", "raw": text_strip[:200]}

    try:
        data = parse_xml_kv(text_strip)
    except Exception as e:
        return {"ok": False, "error": f"xml_parse_failed: {e}", "raw": text_strip[:200]}

    if str(data.get("code") or "").strip() == "error":
        msg = str(data.get("msg") or "").strip()
        if msg.lower() == "doublelogin":
            return {"ok": False, "error": "double_login", "data": data}
        return {"ok": False, "error": "server_error", "data": data}

    # 成功时通常会有 enable/currency/maxcredit 等字段
    if data.get("enable") or data.get("currency") or data.get("maxcredit"):
        return {"ok": True, "data": data}

    return {"ok": False, "error": "unexpected_response", "data": data}


@dataclass
class ReloginResult:
    cookie: str
    uid: str
    mid: str
    ver: str
    base_url: str = ""
    proxy: str = ""
    cookie_names: tuple[str, ...] = ()
    cookie_fingerprint: str = ""
    session_fingerprint: str = ""
    session_stable: bool = False
    session_ready_reason: str = "SESSION_NOT_STABLE"


def _relogin_session_patch(result: ReloginResult) -> Dict[str, Any]:
    """Convert a stabilized login result into non-sensitive arsenal fields."""

    return {
        "cookie_names": list(result.cookie_names),
        "cookie_fingerprint": str(result.cookie_fingerprint or ""),
        "session_fingerprint": str(result.session_fingerprint or ""),
        "session_stable": bool(result.session_stable),
        "session_ready": bool(result.session_stable),
        "session_ready_reason": str(result.session_ready_reason or "SESSION_NOT_STABLE"),
    }


def relogin_member_via_playwright(
    *,
    member_id: str,
    base_url: str,
    username: str,
    password: str,
    ver_hint: str,
    proxy: str,
    user_data_root: Path,
    login_tab: int,
    headless: bool,
    timeout_s: int,
    debug: bool,
) -> ReloginResult:
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except Exception as e:
        raise RuntimeError(
            "未安装 playwright，无法自动登录会员端。请先执行："
            "python -m pip install -r requirements.txt 以及 python -m playwright install chromium"
        ) from e

    base = normalize_member_base_url(base_url)
    if not base:
        raise RuntimeError("缺少 base_url")

    host0 = urlparse(base).hostname or ""
    user_data_dir = user_data_root / safe_name(member_id or username or uuid.uuid4().hex)
    user_data_dir.mkdir(parents=True, exist_ok=True)

    def log(msg: str) -> None:
        print(f"[{now_iso()}] [member:{member_id}] {msg}", flush=True)

    if debug:
        log(f"Playwright 自动登录：{username}@{base}")

    def first_visible(locator):
        try:
            n = int(locator.count())
        except Exception:
            return None
        n = min(n, 30)
        for i in range(n):
            item = locator.nth(i)
            try:
                if item.is_visible():
                    return item
            except Exception:
                continue
        try:
            return locator.first if n > 0 else None
        except Exception:
            return None

    def try_select_login_tab(page) -> None:
        tab = int(login_tab or 0)
        if tab <= 0:
            return
        candidates = [
            f"text=/登(录|入)\\s*{tab}/",
            f"text=/Login\\s*{tab}/i",
            f"#login{tab}",
            f"#login_tab_{tab}",
            f"[data-tab='{tab}']",
            f"[data-login='{tab}']",
        ]
        for sel in candidates:
            try:
                btn = first_visible(page.locator(sel))
                if btn:
                    btn.click(timeout=3_000)
                    if debug:
                        log(f"已尝试切换到登录{tab}（{sel}）")
                    return
            except Exception:
                continue

    def try_recover_existing_session(ctx, page, fallback_ver: str = "") -> Optional[ReloginResult]:
        # 有些站点/场景下已经是“登录后页面”，不会再出现登录表单，也不会触发 chk_login 响应。
        # 这时尝试直接从当前页面 cookie + ver/uid 候选恢复会话。
        try:
            cookies = ctx.cookies()
        except Exception:
            cookies = []
        try:
            host_eff = urlparse(str(page.url or "")).hostname or host0
        except Exception:
            host_eff = host0
        cookie_str = cookies_to_header(cookies, host=host_eff or host0)
        if not cookie_str:
            return None

        try:
            html = page.content() or ""
        except Exception:
            html = ""
        try:
            page_url = str(page.url or "")
        except Exception:
            page_url = ""

        ver_candidates: list[str] = []
        uid_candidates: list[str] = []
        if fallback_ver:
            ver_candidates.append(str(fallback_ver).strip())
        try:
            qs = parse_qs(urlparse(page_url).query)
            ver_candidates.extend([str(v).strip() for v in (qs.get("ver") or [])])
            uid_candidates.extend([str(v).strip() for v in (qs.get("uid") or [])])
        except Exception:
            pass
        try:
            for fr in page.frames:
                fu = str(fr.url or "")
                if not fu:
                    continue
                ver_candidates.extend(re.findall(r"[?&]ver=([0-9A-Za-z._-]{4,})", fu, flags=re.IGNORECASE))
                uid_candidates.extend(re.findall(r"[?&]uid=([0-9A-Za-z_-]{4,})", fu, flags=re.IGNORECASE))
        except Exception:
            pass
        try:
            stash = page.evaluate(
                """() => {
                    const out = {ls: {}, ss: {}, vars: {}};
                    try {
                        for (let i = 0; i < localStorage.length; i++) {
                            const k = localStorage.key(i);
                            if (k) out.ls[k] = localStorage.getItem(k) || '';
                        }
                    } catch (e) {}
                    try {
                        for (let i = 0; i < sessionStorage.length; i++) {
                            const k = sessionStorage.key(i);
                            if (k) out.ss[k] = sessionStorage.getItem(k) || '';
                        }
                    } catch (e) {}
                    const keys = ['uid', 'UID', 'userId', 'userid', 'loginUid', 'ver', 'myGameVer', 'gameVer'];
                    for (const k of keys) {
                        try {
                            const v = window[k];
                            if (v !== undefined && v !== null && String(v)) out.vars[k] = String(v);
                        } catch (e) {}
                    }
                    return out;
                }"""
            )
            if isinstance(stash, dict):
                merged = []
                for grp in ("ls", "ss", "vars"):
                    obj = stash.get(grp) or {}
                    if isinstance(obj, dict):
                        for k, v in obj.items():
                            merged.append(f"{k}={v}")
                merged_text = "\n".join(merged)
                ver_candidates.extend(_extract_ver_candidates_from_text(merged_text))
                uid_candidates.extend(_extract_uid_candidates_from_text(merged_text))
        except Exception:
            pass
        ver_candidates.extend(_extract_ver_candidates_from_text(html))
        ver_candidates.extend(_extract_ver_candidates_from_cookies(cookies))
        uid_candidates.extend(_extract_uid_candidates_from_cookies(cookies))
        uid_candidates.extend(_extract_uid_candidates_from_text(html))

        ver_candidates = _uniq_keep_order(ver_candidates)
        uid_candidates = _uniq_keep_order(uid_candidates)
        if not ver_candidates or not uid_candidates:
            return None

        timeout_probe = min(15, max(5, int(timeout_s)))
        for ver_try in ver_candidates[:8]:
            for uid_try in uid_candidates[:12]:
                try:
                    chk = member_ping_member_data(
                        base_url=base,
                        ver=ver_try,
                        uid=uid_try,
                        cookie=cookie_str,
                        proxy=proxy,
                        timeout_s=timeout_probe,
                    )
                    if chk.get("ok"):
                        data = chk.get("data") or {}
                        mid_try = str(data.get("mid") or data.get("MID") or "").strip()
                        capture = stabilize_browser_session(
                            ctx,
                            page,
                            uid=uid_try,
                            ver=ver_try,
                            base_url=base,
                            host=host0,
                        )
                        stable_cookie = str(capture.get("cookie") or "")
                        if not stable_cookie:
                            continue
                        stable_chk = member_ping_member_data(
                            base_url=str(capture.get("base_url") or base),
                            ver=ver_try,
                            uid=uid_try,
                            cookie=stable_cookie,
                            proxy=proxy,
                            timeout_s=timeout_probe,
                        )
                        if not stable_chk.get("ok"):
                            continue
                        log(f"检测到已登录会话（uid={uid_try} ver={ver_try}），直接复用")
                        return ReloginResult(
                            cookie=stable_cookie,
                            uid=uid_try,
                            mid=mid_try,
                            ver=ver_try,
                            base_url=str(capture.get("base_url") or base),
                            cookie_names=tuple(capture.get("cookie_names") or ()),
                            cookie_fingerprint=str(capture.get("cookie_fingerprint") or ""),
                            session_fingerprint=str(capture.get("session_fingerprint") or ""),
                            session_stable=bool(capture.get("session_stable")),
                            session_ready_reason=str(capture.get("session_ready_reason") or "SESSION_NOT_STABLE"),
                        )
                except Exception:
                    continue
        return None

    with sync_playwright() as p:
        proxy_cfg = _parse_proxy_for_playwright(proxy)

        def launch_context(*, prefer_system_chrome: bool = False):
            return _launch_persistent_context_with_recovery(
                p,
                user_data_dir=user_data_dir,
                headless=bool(headless),
                proxy_cfg=proxy_cfg,
                log=log,
                platform_name=sys.platform,
                prefer_system_chrome=prefer_system_chrome,
            )

        ctx = launch_context()
        try:
            try:
                page = ctx.new_page()
            except Exception as e:
                if sys.platform in {"darwin", "win32"} and _is_target_closed_error(e):
                    try:
                        ctx.close()
                    except Exception:
                        pass
                    try:
                        ctx = launch_context(prefer_system_chrome=True)
                        log("检测到浏览器上下文异常关闭，已自动回退本机 Chrome（channel=chrome）")
                        page = ctx.new_page()
                    except Exception as e2:
                        raise RuntimeError(
                            "Playwright 创建页面失败："
                            f"{type(e).__name__}: {e}；"
                            f"回退 channel=chrome 仍失败：{type(e2).__name__}: {e2}"
                        ) from e2
                else:
                    raise
            goto_timeout_ms = max(30_000, int(timeout_s) * 1000)
            page.goto(base + "/", wait_until="domcontentloaded", timeout=goto_timeout_ms)

            try_select_login_tab(page)

            # 站点登录表单可能在 domcontentloaded 后异步注入；固定 15 秒会把慢页面误判为“无输入框”。
            # 等待结束后再次切换登录页签，兼容登录3按钮晚于首屏出现的情况。
            form_wait_timeout_ms = _login_form_wait_timeout_ms(timeout_s)
            try:
                page.wait_for_selector("input[type='password']", timeout=form_wait_timeout_ms)
            except Exception:
                pass
            try_select_login_tab(page)

            user_sel = (
                "#username, #usr, input[name='username'], input[name='usr'], input[autocomplete='username'], "
                "input[placeholder*='账号'], input[placeholder*='帳號'], input[placeholder*='帐号'], input[placeholder*='會員'], input[placeholder*='用户名'], "
                "input[id*='user' i], input[name*='user' i], input[id*='account' i], input[name*='account' i]"
            )
            pwd_sel = (
                "#pwd, input[name='pwd'], input[type='password'], input[autocomplete='current-password'], "
                "input[placeholder*='密码'], input[placeholder*='密碼']"
            )
            btn_sel = (
                "#loginBtn, #btn_login, .lg_login_btn, button:has-text('登录'), button:has-text('登入'), button:has-text('登陆'), "
                "input[type='submit'], input[value*='登录'], input[value*='登入'], input[value*='登陆']"
            )

            user_loc = None
            pwd_loc = None
            for fr in page.frames:
                try:
                    u = first_visible(fr.locator(user_sel))
                    p2 = first_visible(fr.locator(pwd_sel))
                    if u and p2:
                        user_loc = u
                        pwd_loc = p2
                        break
                except Exception:
                    continue

            if not user_loc or not pwd_loc:
                existing = try_recover_existing_session(ctx, page, fallback_ver=ver_hint)
                if existing:
                    return existing
                if debug:
                    try:
                        html = page.content()
                        (user_data_dir / f"debug_login_{int(now_ts())}.html").write_text(html, encoding="utf-8")
                    except Exception:
                        pass
                raise RuntimeError("未找到登录输入框（可能需要切换到登录3/站点改版/表单在 iframe 内）")

            user_loc.fill(username, timeout=20_000)
            pwd_loc.fill(password, timeout=20_000)

            btn = None
            for fr in page.frames:
                try:
                    b = first_visible(fr.locator(btn_sel))
                    if b:
                        btn = b
                        break
                except Exception:
                    continue

            # 监听真正的登录请求返回（p=chk_login）
            def is_chk_login(resp) -> bool:
                try:
                    if "transform.php" not in (resp.url or ""):
                        return False
                    req = resp.request
                    if (req.method or "").upper() != "POST":
                        return False
                    post = req.post_data or ""
                    # 兼容：部分站点会变成 p=chk_login2 / chk_login_v3 等（原来严格匹配会导致一直等到超时）
                    if "chk_login" in post:
                        return True
                    # 兜底：如果站点改名但仍然把账号写进 POST，可以用用户名命中（避免完全等不到 response）
                    u = str(username or "").strip()
                    if u and (u in post) and ("pwd=" in post or "password=" in post or "pass=" in post):
                        return True
                    return False
                except Exception:
                    return False

            def submit_login() -> None:
                if btn:
                    btn.click(timeout=15_000)
                else:
                    pwd_loc.press("Enter")

            try:
                resp = _trigger_and_wait_for_response(
                    page,
                    is_chk_login,
                    submit_login,
                    timeout_ms=max(15_000, int(timeout_s) * 1000),
                )
            except Exception as e:
                existing = try_recover_existing_session(ctx, page, fallback_ver=ver_hint)
                if existing:
                    return existing
                raise RuntimeError(f"等待登录响应超时/失败（站点登录接口可能已变更或被验证码拦截）：{e}")
            text = (resp.text() or "").strip()
            if not text.startswith("<"):
                # 兼容：部分站点会要求验证码（返回 JSON 且带 img_data/base64），不能把 base64 写到日志/last_error（太大且无意义）
                if text.startswith("{") and text.endswith("}"):
                    try:
                        obj = json.loads(text)
                        code = str(obj.get("code") or "").strip()
                        msg = str(obj.get("msg") or "").strip()
                        is_check = str(obj.get("is_check") or "").strip()
                        if msg.lower() == "checkneed" or is_check.upper() == "Y" or code == "201":
                            if headless:
                                raise RuntimeError("需要验证码：站点要求验证码，当前为 headless 无法完成；请改为可视登录一次")
                            # 非 headless：允许用户在弹出的浏览器里输入验证码后再次点击登录
                            log("检测到需要验证码：请在弹出的浏览器输入验证码并再次点击登录（等待最多 300 秒）")
                            wait_ms = max(goto_timeout_ms, 300_000)
                            deadline = time.time() + (wait_ms / 1000.0)
                            last_hint = ""
                            while True:
                                remaining_ms = max(1, int((deadline - time.time()) * 1000))
                                if remaining_ms <= 1:
                                    raise RuntimeError("等待验证码登录超时（请在浏览器里输入验证码并点击登录）")
                                resp2 = _trigger_and_wait_for_response(
                                    page,
                                    is_chk_login,
                                    lambda: None,
                                    timeout_ms=remaining_ms,
                                )
                                t2 = (resp2.text() or "").strip()
                                # 1) XML：尝试解析并验证 status/uid
                                if t2.startswith("<"):
                                    try:
                                        d2 = parse_xml_kv(t2)
                                        if d2.get("status") == "200" and str(d2.get("uid") or "").strip():
                                            resp = resp2
                                            text = t2
                                            break
                                        last_hint = f"xml_status={d2.get('status')}"
                                    except Exception as e_xml:
                                        last_hint = f"xml_parse_failed={type(e_xml).__name__}"
                                # 2) JSON：可能还是 checkNeed（用户还没输入验证码），继续等
                                if t2.startswith("{") and t2.endswith("}"):
                                    try:
                                        o2 = json.loads(t2)
                                        msg2 = str(o2.get("msg") or "").strip()
                                        is_check2 = str(o2.get("is_check") or "").strip().upper()
                                        if msg2.lower() == "checkneed" or is_check2 == "Y":
                                            continue
                                        last_hint = f"json_msg={msg2}"
                                    except Exception:
                                        pass
                                # 其他：继续等下一次
                                if last_hint:
                                    continue
                        else:
                            raise RuntimeError(f"登录返回非 XML：code={code} msg={msg} is_check={is_check}")
                    except Exception as e:
                        raise RuntimeError(f"登录返回非 XML（可能需要验证码/站点策略变更）：{type(e).__name__}: {e}")
                else:
                    raise RuntimeError("登录返回非 XML（可能需要验证码/站点策略变更）")
            data = parse_xml_kv(text)
            if data.get("status") != "200":
                raise RuntimeError(f"登录失败：{data}")

            uid = data.get("uid", "").strip()
            mid = data.get("mid", "").strip()

            # ver 从 transform.php?ver=... 自动提取
            qs = parse_qs(urlparse(resp.url).query)
            ver = (qs.get("ver") or [""])[0].strip()

            # 触发登录后初始化请求（抓包里常见：memSet/check、service_mainget/login=Y、get_version）
            # 目的：让站点补齐一批关键 cookie / server-side session 状态，避免后续接口报 doubleLogin。
            try:
                api = ctx.request
                # 若输入的是 IP，但页面实际已跳转到域名，则以页面当前 origin 为准，避免风控/上下文异常。
                origin = base
                try:
                    pu = urlparse(str(page.url or ""))
                    if pu.scheme and pu.netloc:
                        origin = f"{pu.scheme}://{pu.netloc}"
                except Exception:
                    origin = base
                transform_url = f"{origin}/transform.php?ver={ver}"
                api.post(transform_url, form={"p": "memSet", "ver": ver, "uid": uid, "langx": "zh-cn", "action": "check"})
                api.post(transform_url, form={"p": "service_mainget", "ver": ver, "langx": "zh-cn", "uid": uid, "login": "Y"})
                api.post(transform_url, form={"p": "get_version", "uid": uid, "langx": "zh-cn"})
            except Exception:
                pass
            if not uid:
                raise RuntimeError("登录后未拿到 uid（响应里没有 uid）")

            capture = stabilize_browser_session(
                ctx,
                page,
                uid=uid,
                ver=ver,
                base_url=base,
                host=host0,
            )
            cookie_str = str(capture.get("cookie") or "")
            if not cookie_str:
                raise RuntimeError("登录后未拿到 cookie（可能登录未成功或站点策略变化）")
            base_eff = str(capture.get("base_url") or base).strip()

            log("已重新登录并刷新 Cookie/UID，准备继续在线检测")
            return ReloginResult(
                cookie=cookie_str,
                uid=uid,
                mid=mid,
                ver=ver,
                base_url=base_eff,
                cookie_names=tuple(capture.get("cookie_names") or ()),
                cookie_fingerprint=str(capture.get("cookie_fingerprint") or ""),
                session_fingerprint=str(capture.get("session_fingerprint") or ""),
                session_stable=bool(capture.get("session_stable")),
                session_ready_reason=str(capture.get("session_ready_reason") or "SESSION_NOT_STABLE"),
            )
        finally:
            try:
                ctx.close()
            except Exception:
                pass


def relogin_member_via_adspower(
    *,
    member_id: str,
    base_url: str,
    ver_hint: str,
    proxy: str,
    adspower_api_base: str,
    adspower_api_key: str,
    adspower_profile_id: str,
    adspower_profile_no: str,
    auto_start: bool,
    auto_form_login: bool,
    close_after_sync: bool,
    sync_proxy: bool,
    timeout_s: int,
    debug: bool,
) -> ReloginResult:
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except Exception as e:
        raise RuntimeError(
            "未安装 playwright，无法通过 AdsPower 读取会话。请先执行："
            "python -m pip install -r requirements.txt 以及 python -m playwright install chromium"
        ) from e

    api_base = normalize_adspower_api_base(adspower_api_base or DEFAULT_ADSPOWER_API_BASE)
    profile_id = str(adspower_profile_id or "").strip()
    profile_no = str(adspower_profile_no or "").strip()
    if not (profile_id or profile_no):
        raise RuntimeError("缺少 AdsPower profile_id/profile_no")

    def log(msg: str) -> None:
        print(f"[{now_iso()}] [member:{member_id}] {msg}", flush=True)

    profile: Dict[str, Any] = {}
    profile_obj, profile_err = adspower_find_profile(
        api_base=api_base,
        profile_id=profile_id,
        profile_no=profile_no,
        api_key=adspower_api_key,
        timeout_s=max(6, int(timeout_s)),
    )
    if profile_obj:
        profile = profile_obj
    elif debug and profile_err:
        log(f"AdsPower 查询 profile 失败（继续尝试 cookies）：{profile_err}")

    proxy_eff = _normalize_proxy_input(str(proxy or "").strip())
    profile_proxy = adspower_profile_proxy_url(profile)
    if sync_proxy and profile_proxy:
        proxy_eff = _normalize_proxy_input(profile_proxy)

    cookies, cookie_err = adspower_get_profile_cookies(
        api_base=api_base,
        profile_id=profile_id,
        profile_no=profile_no,
        api_key=adspower_api_key,
        timeout_s=max(6, int(timeout_s)),
    )
    if cookie_err:
        if debug:
            log(f"AdsPower cookies 读取失败（将尝试 auto_start）：{cookie_err}")
        cookies = []

    base_eff = normalize_member_base_url(base_url)
    if not base_eff:
        base_eff = adspower_infer_base_url_from_cookies(cookies)
    if not base_eff and isinstance(profile, dict):
        dom = str(profile.get("domain_name") or "").strip().lstrip(".")
        if dom:
            base_eff = f"https://{dom}"
    host0 = urlparse(base_eff).hostname or ""

    def verify_candidates(cookie_str: str, ver_candidates: list[str], uid_candidates: list[str]) -> Optional[ReloginResult]:
        if not cookie_str:
            return None
        vers = _uniq_keep_order(ver_candidates)[:10]
        uids = _uniq_keep_order(uid_candidates)[:20]
        if not vers or not uids:
            return None
        probe_timeout = min(15, max(5, int(timeout_s)))
        for ver_try in vers:
            for uid_try in uids:
                try:
                    chk = member_ping_member_data(
                        base_url=base_eff,
                        ver=ver_try,
                        uid=uid_try,
                        cookie=cookie_str,
                        proxy=proxy_eff,
                        timeout_s=probe_timeout,
                    )
                    if chk.get("ok"):
                        data = chk.get("data") or {}
                        mid_try = str(data.get("mid") or data.get("MID") or "").strip()
                        cookie_items = [
                            {"name": key, "value": value}
                            for key, value in parse_cookie_header(cookie_str).items()
                        ]
                        cookie_names, cookie_fingerprint = _cookie_capture_fingerprint(cookie_items)
                        session_fingerprint = hashlib.sha256(
                            "|".join((base_eff, uid_try, ver_try, cookie_fingerprint)).encode("utf-8")
                        ).hexdigest()
                        return ReloginResult(
                            cookie=cookie_str,
                            uid=uid_try,
                            mid=mid_try,
                            ver=ver_try,
                            base_url=base_eff,
                            proxy=proxy_eff,
                            cookie_names=tuple(cookie_names),
                            cookie_fingerprint=cookie_fingerprint,
                            session_fingerprint=session_fingerprint,
                            session_stable=True,
                            session_ready_reason="ADSPOWER_COOKIE_SNAPSHOT",
                        )
                except Exception:
                    continue
        return None

    cookie_str = adspower_cookie_header_from_cookies(cookies)
    ver_from_cookie = adspower_extract_ver_candidates_from_cookies(cookies)
    uid_from_cookie = adspower_extract_uid_candidates_from_cookies(cookies)
    direct = None
    if base_eff:
        direct = verify_candidates(
            cookie_str,
            ([str(ver_hint).strip()] if str(ver_hint or "").strip() else []) + ver_from_cookie,
            uid_from_cookie,
        )
    if direct:
        if debug:
            log(f"已通过 AdsPower cookies 直接恢复会话（uid={direct.uid} ver={direct.ver}）")
        return direct

    if not auto_start:
        if cookie_err:
            raise RuntimeError(f"{cookie_err}；且已关闭 auto_start")
        if not base_eff:
            raise RuntimeError("AdsPower 环境里未识别到站点域名，请先在该环境打开会员站并登录一次")
        raise RuntimeError("AdsPower cookies 未恢复到可用会话，且已关闭 auto_start")

    profile_started = False

    def maybe_close_started_profile() -> None:
        if not (profile_started and close_after_sync):
            return
        stop_err = adspower_stop_profile(
            api_base=api_base,
            profile_id=profile_id,
            profile_no=profile_no,
            api_key=adspower_api_key,
            timeout_s=max(6, int(timeout_s)),
        )
        if debug:
            if stop_err:
                log(f"AdsPower 环境自动关闭失败：{stop_err}")
            else:
                log("AdsPower 环境已自动关闭（会话已同步）")

    started, start_err = adspower_start_profile(
        api_base=api_base,
        profile_id=profile_id,
        profile_no=profile_no,
        api_key=adspower_api_key,
        timeout_s=max(20, int(timeout_s)),
    )
    if start_err:
        if cookie_err:
            raise RuntimeError(f"{cookie_err}；随后启动 AdsPower 环境也失败：{start_err}")
        raise RuntimeError(start_err)
    profile_started = True
    ws_url = str(started.get("ws_puppeteer") or "").strip()
    if not ws_url:
        raise RuntimeError("AdsPower 未返回可用 ws 地址（ws.puppeteer）")

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(ws_url)
        except Exception as e:
            raise RuntimeError(f"连接 AdsPower CDP 失败：{e}") from e

        contexts = list(getattr(browser, "contexts", []) or [])
        open_urls: list[str] = []
        for c in contexts:
            for pg in list(getattr(c, "pages", []) or []):
                try:
                    open_urls.append(str(pg.url or ""))
                except Exception:
                    continue
        page_base = _infer_base_url_from_page_urls(open_urls)
        if page_base:
            base_eff = page_base
            host0 = urlparse(base_eff).hostname or host0

        if not contexts:
            contexts = [browser.new_context(ignore_https_errors=True)]

        page_targets: list[Tuple[Any, Any]] = []
        for ctx in contexts:
            pages = list(getattr(ctx, "pages", []) or [])
            if not pages:
                try:
                    pages = [ctx.new_page()]
                except Exception:
                    pages = []
            for page in pages:
                page_targets.append((ctx, page))
        if not page_targets:
            raise RuntimeError("AdsPower 未找到可用页面，请先在该环境打开会员站页面")

        def first_visible(locator):
            try:
                n = int(locator.count())
            except Exception:
                return None
            n = min(n, 30)
            for i in range(n):
                item = locator.nth(i)
                try:
                    if item.is_visible():
                        return item
                except Exception:
                    continue
            return None

        def try_recover_from_page(ctx, page) -> Optional[ReloginResult]:
            nonlocal base_eff, host0
            try:
                cookies2 = ctx.cookies()
            except Exception:
                cookies2 = cookies

            host_eff = host0
            try:
                host_eff = urlparse(str(page.url or "")).hostname or host_eff
            except Exception:
                pass

            base_try = str(base_eff or "").strip()
            page_base2 = _infer_base_url_from_page_urls([str(getattr(page, "url", "") or "")])
            if page_base2:
                base_try = page_base2
            if not base_try:
                base_try = adspower_infer_base_url_from_cookies(cookies2) or adspower_infer_base_url_from_cookies(cookies)
            if not base_try and isinstance(profile, dict):
                dom = str(profile.get("domain_name") or "").strip().lstrip(".")
                if dom:
                    base_try = f"https://{dom}"
            if not base_try:
                return None

            base_eff = base_try
            host0 = urlparse(base_eff).hostname or host0
            host_eff = urlparse(base_eff).hostname or host_eff

            cookie_str2 = cookies_to_header(cookies2, host=host_eff or host0)
            if not cookie_str2:
                cookie_str2 = cookie_str

            ver_candidates: list[str] = []
            uid_candidates: list[str] = []
            if ver_hint:
                ver_candidates.append(str(ver_hint).strip())
            try:
                page_url = str(page.url or "")
                qs = parse_qs(urlparse(page_url).query)
                ver_candidates.extend([str(v).strip() for v in (qs.get("ver") or [])])
                uid_candidates.extend([str(v).strip() for v in (qs.get("uid") or [])])
            except Exception:
                pass
            try:
                for fr in (page.frames or []):
                    try:
                        fu = str(fr.url or "")
                    except Exception:
                        fu = ""
                    if not fu:
                        continue
                    ver_candidates.extend(re.findall(r"[?&]ver=([0-9A-Za-z._-]{4,})", fu, flags=re.IGNORECASE))
                    uid_candidates.extend(re.findall(r"[?&]uid=([0-9A-Za-z_-]{4,})", fu, flags=re.IGNORECASE))
            except Exception:
                pass
            try:
                storage_dump = page.evaluate(
                    """() => {
                        const out = { ls: {}, ss: {}, keys: {} };
                        try {
                            for (let i = 0; i < localStorage.length; i++) {
                                const k = localStorage.key(i);
                                if (k) out.ls[k] = localStorage.getItem(k) || '';
                            }
                        } catch (e) {}
                        try {
                            for (let i = 0; i < sessionStorage.length; i++) {
                                const k = sessionStorage.key(i);
                                if (k) out.ss[k] = sessionStorage.getItem(k) || '';
                            }
                        } catch (e) {}
                        const keys = ['uid', 'UID', 'userId', 'userid', 'loginUid', 'ver', 'myGameVer', 'gameVer'];
                        keys.forEach(k => {
                            try { out.keys[k] = String(window[k] || ''); } catch (e) {}
                        });
                        try {
                            out.keys.location = String(location.href || '');
                        } catch (e) {}
                        return out;
                    }"""
                )
                merged_text = json.dumps(storage_dump, ensure_ascii=False)
                ver_candidates.extend(_extract_ver_candidates_from_text(merged_text))
                uid_candidates.extend(_extract_uid_candidates_from_text(merged_text))
            except Exception:
                pass
            try:
                html = page.content() or ""
            except Exception:
                html = ""

            ver_candidates.extend(_extract_ver_candidates_from_text(html))
            ver_candidates.extend(_extract_ver_candidates_from_cookies(cookies2))
            ver_candidates.extend(adspower_extract_ver_candidates_from_cookies(cookies2))
            uid_candidates.extend(_extract_uid_candidates_from_cookies(cookies2))
            uid_candidates.extend(adspower_extract_uid_candidates_from_cookies(cookies2))
            uid_candidates.extend(_extract_uid_candidates_from_text(html))

            # AdsPower 页面也可能在登录成功后异步补写 fingerprint Cookie；
            # 在候选 UID/ver 验证前先做一次短窗口稳定采样。
            try:
                capture = stabilize_browser_session(
                    ctx,
                    page,
                    ver=(ver_candidates[0] if ver_candidates else str(ver_hint or "")),
                    base_url=base_eff,
                    host=host_eff,
                    require_uid=False,
                )
                if capture.get("cookie"):
                    cookie_str2 = str(capture["cookie"])
            except Exception:
                return None

            return verify_candidates(cookie_str2, ver_candidates, uid_candidates)

        def try_click_login_submit(page) -> bool:
            pwd_sel = "input[type='password'], input[name*='pwd' i], input[id*='pwd' i]"
            btn_sel = (
                "#loginBtn, #btn_login, .lg_login_btn, button:has-text('登录'), button:has-text('登入'), "
                "button:has-text('登陆'), button:has-text('Login'), input[type='submit'], "
                "input[value*='登录'], input[value*='登入'], input[value*='登陆']"
            )
            scopes = [page]
            try:
                scopes.extend(list(page.frames or []))
            except Exception:
                pass

            def _scan_login_fill_state(sc: Any) -> Dict[str, bool]:
                try:
                    out = sc.evaluate(
                        """() => {
                            const isVisible = (el) => {
                                if (!el) return false;
                                const st = window.getComputedStyle(el);
                                if (!st || st.visibility === 'hidden' || st.display === 'none') return false;
                                const r = el.getBoundingClientRect();
                                return !!(r && r.width > 0 && r.height > 0);
                            };
                            const valueOf = (el) => String((el && el.value) || '').trim();
                            const pwdSel = "input[type='password'], input[name*='pwd' i], input[id*='pwd' i]";
                            const pwdInputs = Array.from(document.querySelectorAll(pwdSel)).filter(isVisible);
                            if (!pwdInputs.length) {
                                return {
                                    has_password: false,
                                    password_filled: false,
                                    has_user_field: false,
                                    user_filled: false,
                                };
                            }

                            const userSel = "input[type='text'], input[type='email'], input:not([type]), "
                                + "input[name*='user' i], input[id*='user' i], input[name*='account' i], "
                                + "input[id*='account' i], input[name*='login' i], input[id*='login' i], "
                                + "input[placeholder*='账号'], input[placeholder*='帳號'], "
                                + "input[placeholder*='用户名'], input[placeholder*='賬號'], input[placeholder*='帳戶']";

                            const allUsers = Array.from(document.querySelectorAll(userSel))
                                .filter(isVisible)
                                .filter((el) => pwdInputs.indexOf(el) === -1);
                            const userInputs = allUsers.filter((el) => {
                                const k = [
                                    String(el.getAttribute('name') || ''),
                                    String(el.getAttribute('id') || ''),
                                    String(el.getAttribute('placeholder') || ''),
                                ].join(' ').toLowerCase();
                                if (!k) return true;
                                if (k.includes('captcha') || k.includes('verify') || k.includes('otp')) return false;
                                if (k.includes('code') && !k.includes('account')) return false;
                                if (k.includes('验证码') || k.includes('驗證碼') || k.includes('安全码')) return false;
                                return true;
                            });

                            const hasUserField = userInputs.length > 0;
                            return {
                                has_password: true,
                                password_filled: pwdInputs.some((el) => valueOf(el).length > 0),
                                has_user_field: hasUserField,
                                user_filled: hasUserField ? userInputs.some((el) => valueOf(el).length > 0) : false,
                            };
                        }"""
                    )
                except Exception:
                    out = {}
                if not isinstance(out, dict):
                    out = {}
                return {
                    "has_password": bool(out.get("has_password")),
                    "password_filled": bool(out.get("password_filled")),
                    "has_user_field": bool(out.get("has_user_field")),
                    "user_filled": bool(out.get("user_filled")),
                }

            deadline = time.time() + 8.0
            saw_login_form = False
            while time.time() < deadline:
                wait_next = False
                for sc in scopes:
                    fill_state = _scan_login_fill_state(sc)
                    if not fill_state.get("has_password"):
                        continue
                    saw_login_form = True

                    pwd_ok = bool(fill_state.get("password_filled"))
                    has_user = bool(fill_state.get("has_user_field"))
                    user_ok = bool(fill_state.get("user_filled"))
                    if (not pwd_ok) or (has_user and not user_ok):
                        wait_next = True
                        continue

                    try:
                        btn = first_visible(sc.locator(btn_sel))
                    except Exception:
                        btn = None
                    if not btn:
                        continue
                    try:
                        btn.click(timeout=5_000)
                        return True
                    except Exception:
                        continue
                if not wait_next:
                    break
                try:
                    page.wait_for_timeout(350)
                except Exception:
                    time.sleep(0.35)

            if debug and saw_login_form:
                log("检测到登录页，但账号/密码尚未自动填充完成，已跳过本轮自动点击登录")
            return False

        goto_timeout_ms = max(30_000, int(timeout_s) * 1000)
        for ctx, page in page_targets:
            current_page_url = ""
            try:
                current_page_url = str(getattr(page, "url", "") or "")
            except Exception:
                current_page_url = ""

            page_origin = _origin_from_url(current_page_url).rstrip("/")
            if page_origin:
                if page_origin != base_eff:
                    base_eff = page_origin
                    host0 = urlparse(base_eff).hostname or host0
                    if debug:
                        log(f"优先使用当前标签页域名作为登录入口：{base_eff}")

            nav_base = str(base_eff or "").strip().rstrip("/")
            if not nav_base and page_origin:
                nav_base = page_origin

            if nav_base and _should_navigate_ads_page(current_page_url, nav_base):
                try:
                    page.goto(nav_base + "/", wait_until="domcontentloaded", timeout=goto_timeout_ms)
                except Exception:
                    pass

            recovered = try_recover_from_page(ctx, page)
            if recovered:
                if debug:
                    log(f"已通过 AdsPower 浏览器会话恢复登录态（uid={recovered.uid} ver={recovered.ver}）")
                maybe_close_started_profile()
                return recovered

            if not auto_form_login:
                continue
            clicked = False
            try:
                clicked = try_click_login_submit(page)
            except Exception:
                clicked = False
            if not clicked:
                continue
            if debug:
                log("检测到登录表单，已触发登录按钮（Ads 自动填充账号密码）")
            for _ in range(6):
                try:
                    page.wait_for_timeout(1200)
                except Exception:
                    pass
                recovered2 = try_recover_from_page(ctx, page)
                if recovered2:
                    if debug:
                        log(f"已通过 Ads 自动点击登录恢复会话（uid={recovered2.uid} ver={recovered2.ver}）")
                    maybe_close_started_profile()
                    return recovered2

        if not base_eff:
            raise RuntimeError("AdsPower 环境里未识别到站点域名，请先在该环境打开会员站并停留在该页面后再试")

    raise RuntimeError("AdsPower 环境未检测到可用登录态，请先在 Ads 浏览器里完成一次登录")


def patch_member(doc_path: Path, member_id: str, patch: Dict[str, Any]) -> None:
    # 同一轮并发探活/重登录会同时更新 arsenal.json；锁住读-改-写，避免后写覆盖先写字段。
    with _MEMBER_DOC_LOCK:
        doc = read_json_file(doc_path, {"members": []})
        members = doc.get("members") or []
        changed = False
        out = []
        cooldown_enabled = _to_bool(doc.get("request_cooldown_enabled"), True)
        for m in members:
            if isinstance(m, dict) and str(m.get("id") or "") == member_id:
                nm = dict(m)
                old_session_fp = str(nm.get("session_fingerprint") or "") or member_session_fingerprint(nm)
                old_generation = max(0, _to_int(nm.get("session_generation"), 0))
                nm.update(patch)
                if "cookie" in patch:
                    nm["cookie_fingerprint"] = session_cookie_fingerprint(str(nm.get("cookie") or ""))
                    nm["cookie_names"] = session_cookie_names(str(nm.get("cookie") or ""))
                new_session_fp = member_session_fingerprint(nm)
                identity_changed = bool(old_session_fp and new_session_fp and old_session_fp != new_session_fp)
                session_refreshed = bool(
                    patch.get("session_stable") is True
                    and any(key in patch for key in ("cookie", "uid", "ver", "base_url"))
                )
                if new_session_fp:
                    nm["session_fingerprint"] = new_session_fp
                    if "cookie" in patch or not nm.get("cookie_fingerprint"):
                        nm["cookie_fingerprint"] = session_cookie_fingerprint(str(nm.get("cookie") or ""))
                    if "cookie" in patch or not nm.get("cookie_names"):
                        nm["cookie_names"] = session_cookie_names(str(nm.get("cookie") or ""))
                    if old_generation <= 0:
                        nm["session_generation"] = 1
                    elif identity_changed or session_refreshed:
                        nm["session_generation"] = old_generation + 1
                    else:
                        nm["session_generation"] = old_generation
                    # A watcher-side identity update must not be considered
                    # bet-ready until the stabilized login result is persisted.
                    if identity_changed and not any(
                        key in patch for key in ("session_ready", "session_stable", "session_ready_reason")
                    ):
                        nm["session_stable"] = False
                        nm["session_ready"] = False
                        nm["session_ready_reason"] = "SESSION_CHANGED"
                if not cooldown_enabled:
                    nm["request_cooldown_enabled"] = False
                out.append(nm)
                changed = True
            else:
                out.append(m)
        if not changed:
            return
        doc["members"] = out
        atomic_write_json(doc_path, doc)


def process_members_concurrently(
    *,
    doc_path: Path,
    members: list[Dict[str, Any]],
    user_data_root: Path,
    ping_timeout_s: int,
    debug: bool,
    request_cooldown_enabled: Optional[bool] = None,
) -> None:
    """并行处理一轮会员，避免一个慢重登录阻塞整个账号池的探活。"""
    if not members:
        return
    worker_count = min(8, len(members))
    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="member") as pool:
        futures = [
            pool.submit(
                process_member,
                doc_path=doc_path,
                m=member,
                user_data_root=user_data_root,
                ping_timeout_s=ping_timeout_s,
                debug=debug,
                request_cooldown_enabled=request_cooldown_enabled,
            )
            for member in members
        ]
        for future in futures:
            try:
                future.result()
            except Exception as exc:
                # 单个会员异常不能终止本轮其他会员处理；process_member 自身已负责正常状态落盘。
                print(f"[{now_iso()}] 会员处理线程异常：{type(exc).__name__}: {exc}", flush=True)


def ensure_member_defaults(m: Dict[str, Any]) -> Dict[str, Any]:
    x = dict(m)
    x.setdefault("id", uuid.uuid4().hex)
    x.setdefault("label", x.get("username") or "")
    x.setdefault("proxy", "")
    x.setdefault("uid", "")
    x.setdefault("mid", "")
    x.setdefault("cookie", "")
    x.setdefault("ver", "")
    if x.get("cookie_names") is None:
        x["cookie_names"] = session_cookie_names(str(x.get("cookie") or ""))
    if x.get("cookie_fingerprint") is None:
        x["cookie_fingerprint"] = session_cookie_fingerprint(str(x.get("cookie") or ""))
    if x.get("session_fingerprint") is None:
        x["session_fingerprint"] = member_session_fingerprint(x)
    if x.get("session_generation") is None:
        x["session_generation"] = 1 if x.get("session_fingerprint") else 0
    if x.get("session_stable") is None:
        x["session_stable"] = bool(x.get("uid") and x.get("cookie") and x.get("ver"))
    if x.get("session_ready") is None:
        x["session_ready"] = bool(x.get("uid") and x.get("cookie") and x.get("ver"))
    if x.get("session_ready_reason") is None:
        x["session_ready_reason"] = "LEGACY_SESSION" if x.get("session_ready") else "SESSION_MISSING"
    x.setdefault("status", "unknown")
    x.setdefault("last_check_at", 0)
    x.setdefault("last_ok_at", 0)
    x.setdefault("last_relogin_at", 0)
    x.setdefault("relogin_fail_count", 0)
    x.setdefault("relogin_backoff_until", 0)
    x.setdefault("last_error", "")
    x.setdefault("last_systemtime", "")
    x.setdefault("last_systemtime_at", 0)
    x.setdefault("manual_login_requested_at", 0)
    x.setdefault("request_fail_count", 0)
    x.setdefault("request_cooldown_until", 0)
    x.setdefault("network_health", "unknown")
    x.setdefault("network_last_error", "")
    x.setdefault("network_cooldown_until", 0)
    x.setdefault("request_cooldown_enabled", True)
    x.setdefault("headful_online_since", 0)
    x.setdefault("auto_switch_back_headless_seconds", 600)
    x.setdefault("auto_relogin", True)
    x.setdefault("relogin_headless", True)
    x.setdefault("relogin_login_tab", 3)
    x.setdefault("session_provider", "native")
    x.setdefault("adspower_profile_id", "")
    x.setdefault("adspower_profile_no", "")
    x.setdefault("adspower_api_base", DEFAULT_ADSPOWER_API_BASE)
    x.setdefault("adspower_api_key", "")
    x.setdefault("adspower_auto_start", True)
    x.setdefault("adspower_auto_form_login", True)
    x.setdefault("adspower_close_after_sync", True)
    x.setdefault("adspower_sync_proxy", True)
    x.setdefault("adspower_last_sync_at", 0)
    x.setdefault("adspower_sync_interval_seconds", 5)
    x.setdefault("relogin_timeout_seconds", 30)
    x.setdefault("relogin_min_interval_seconds", 5)
    # 在线检测节流：5 秒内发现掉线并进入重登；system_time 仍低频拉取，避免额外往返。
    x.setdefault("ping_min_interval_seconds", 5)
    # system_time 仅用于展示/记录：默认 5 分钟拉一次即可
    x.setdefault("systemtime_min_interval_seconds", 300)
    # 余额/额度（来自 get_member_data）
    x.setdefault("currency", "")
    x.setdefault("cash", "")
    x.setdefault("maxcredit", "")
    x.setdefault("balance_updated_at", 0)
    if x.get("relogin_fail_count") is None:
        x["relogin_fail_count"] = 0
    if x.get("relogin_backoff_until") is None:
        x["relogin_backoff_until"] = 0
    if x.get("request_fail_count") is None:
        x["request_fail_count"] = 0
    if x.get("request_cooldown_until") is None:
        x["request_cooldown_until"] = 0
    if x.get("headful_online_since") is None:
        x["headful_online_since"] = 0
    if x.get("auto_switch_back_headless_seconds") is None:
        x["auto_switch_back_headless_seconds"] = 600
    if x.get("relogin_headless") is None:
        x["relogin_headless"] = True
    if x.get("auto_relogin") is None:
        x["auto_relogin"] = True
    if x.get("session_provider") is None:
        x["session_provider"] = "native"
    x["session_provider"] = (str(x.get("session_provider") or "native").strip().lower() or "native")
    if x.get("adspower_profile_id") is None:
        x["adspower_profile_id"] = ""
    if x.get("adspower_profile_no") is None:
        x["adspower_profile_no"] = ""
    if x.get("adspower_api_base") is None:
        x["adspower_api_base"] = DEFAULT_ADSPOWER_API_BASE
    x["adspower_api_base"] = normalize_adspower_api_base(str(x.get("adspower_api_base") or DEFAULT_ADSPOWER_API_BASE))
    if x.get("adspower_api_key") is None:
        x["adspower_api_key"] = ""
    if x.get("adspower_auto_start") is None:
        x["adspower_auto_start"] = True
    if x.get("adspower_auto_form_login") is None:
        x["adspower_auto_form_login"] = True
    if x.get("adspower_close_after_sync") is None:
        x["adspower_close_after_sync"] = True
    if x.get("adspower_sync_proxy") is None:
        x["adspower_sync_proxy"] = True
    if x.get("adspower_last_sync_at") is None:
        x["adspower_last_sync_at"] = 0
    if x.get("adspower_sync_interval_seconds") is None:
        x["adspower_sync_interval_seconds"] = 5
    # 旧版本 native/AdsPower 默认 60 秒，掉线后 UI 会长时间停留在线/离线，统一收敛到 5 秒。
    try:
        if int(x.get("ping_min_interval_seconds") or 0) >= 60:
            x["ping_min_interval_seconds"] = 5
    except Exception:
        x["ping_min_interval_seconds"] = 5
    # 迁移旧默认值：历史版本新增账号默认 30 秒重登间隔，现统一下调到 5 秒以提升恢复速度。
    try:
        if int(x.get("relogin_min_interval_seconds") or 0) == 30:
            x["relogin_min_interval_seconds"] = 5
    except Exception:
        x["relogin_min_interval_seconds"] = 5
    # 迁移旧默认值：历史默认 60 秒超时，现下调到 30 秒，避免“长时间登录中”。
    try:
        if int(x.get("relogin_timeout_seconds") or 0) == 60:
            x["relogin_timeout_seconds"] = 30
    except Exception:
        x["relogin_timeout_seconds"] = 30
    return x


def extract_balance_from_member_data(data: Dict[str, Any]) -> Dict[str, Any]:
    # data 来自 parse_xml_kv，字段通常为：currency/maxcredit/cash/pay_type 等
    if not isinstance(data, dict):
        return {}
    currency = str(data.get("currency") or data.get("Currency") or "").strip()
    cash = str(data.get("cash") or data.get("Cash") or "").strip()
    maxcredit = str(data.get("maxcredit") or data.get("Maxcredit") or "").strip()
    out: Dict[str, Any] = {}
    if currency:
        out["currency"] = currency
    if cash:
        out["cash"] = cash
    if maxcredit:
        out["maxcredit"] = maxcredit
    if out:
        out["balance_updated_at"] = int(now_ts())
    return out


def normalize_doc(doc_path: Path) -> None:
    doc = read_json_file(doc_path, {"members": []})
    members = doc.get("members") or []
    request_cooldown_enabled = _to_bool(doc.get("request_cooldown_enabled"), True)
    out = []
    for m in members:
        if not isinstance(m, dict):
            continue
        normalized = ensure_member_defaults(m)
        normalized["request_cooldown_enabled"] = request_cooldown_enabled
        out.append(normalized)
    doc["members"] = out
    doc["request_cooldown_enabled"] = request_cooldown_enabled
    atomic_write_json(doc_path, doc)


def process_member(
    *,
    doc_path: Path,
    m: Dict[str, Any],
    user_data_root: Path,
    ping_timeout_s: int,
    debug: bool,
    request_cooldown_enabled: Optional[bool] = None,
) -> None:
    member_id = str(m.get("id") or "").strip()
    base_url = str(m.get("base_url") or "").strip()
    username = str(m.get("username") or "").strip()
    password = str(m.get("password") or "").strip()
    proxy = _normalize_proxy_input(str(m.get("proxy") or "").strip())
    uid = str(m.get("uid") or "").strip()
    cookie = str(m.get("cookie") or "").strip()
    ver = str(m.get("ver") or "").strip()
    label = str(m.get("label") or username or member_id or "").strip() or username or member_id or "-"
    prev_status = str(m.get("status") or "").strip().lower()
    session_provider = str(m.get("session_provider") or "native").strip().lower() or "native"
    adspower_profile_id = str(m.get("adspower_profile_id") or "").strip()
    adspower_profile_no = str(m.get("adspower_profile_no") or "").strip()
    adspower_api_base = normalize_adspower_api_base(str(m.get("adspower_api_base") or DEFAULT_ADSPOWER_API_BASE))
    adspower_api_key = str(m.get("adspower_api_key") or "").strip()
    adspower_auto_start = _to_bool(m.get("adspower_auto_start", True), True)
    adspower_auto_form_login = _to_bool(m.get("adspower_auto_form_login", True), True)
    adspower_close_after_sync = _to_bool(m.get("adspower_close_after_sync", True), True)
    adspower_sync_proxy = _to_bool(m.get("adspower_sync_proxy", True), True)
    adspower_last_sync_at = _to_float(m.get("adspower_last_sync_at"), 0.0)
    adspower_sync_interval = max(1, int(m.get("adspower_sync_interval_seconds") or 5))

    auto_relogin = _to_bool(m.get("auto_relogin", True), True)
    headless = _to_bool(m.get("relogin_headless", True), True)
    relogin_login_tab = int(m.get("relogin_login_tab") or 3)
    relogin_timeout = int(m.get("relogin_timeout_seconds") or 30)
    relogin_min_interval = int(m.get("relogin_min_interval_seconds") or 5)
    ping_min_interval = int(m.get("ping_min_interval_seconds") or 0)
    systemtime_min_interval = int(m.get("systemtime_min_interval_seconds") or 0)
    relogin_fail_count = _to_int(m.get("relogin_fail_count"), 0)
    relogin_backoff_until = _to_float(m.get("relogin_backoff_until"), 0.0)
    cooldown_enabled = _to_bool(
        m.get("request_cooldown_enabled") if request_cooldown_enabled is None else request_cooldown_enabled,
        True,
    )
    manual_login_requested_at = _to_float(m.get("manual_login_requested_at"), 0.0)
    force_manual_login = manual_login_requested_at > 0
    last_ok_at_initial = _to_float(m.get("last_ok_at"), 0.0)
    has_session = bool(uid and cookie and ver)
    first_login_force_headful = (not has_session) and (last_ok_at_initial <= 0)
    headless_eff = False if first_login_force_headful else headless
    force_adspower_relogin = bool(force_manual_login and session_provider == "adspower")
    force_session_invalid_relogin = False

    now = now_ts()

    if force_manual_login:
        try:
            patch_member(doc_path, member_id, {"manual_login_requested_at": 0})
        except Exception:
            pass
        if session_provider != "adspower":
            headless_eff = False

    if session_provider == "adspower" and ping_min_interval > 5:
        ping_min_interval = 5

    # 如果用户保存的是“ip|port|user|pass|备注”格式，这里统一归一化一次，避免 Playwright 报 Invalid URI
    try:
        raw_proxy = str(m.get("proxy") or "").strip()
        if raw_proxy and proxy and raw_proxy != proxy:
            patch_member(doc_path, member_id, {"proxy": proxy})
    except Exception:
        pass

    if session_provider == "adspower":
        need_ads_sync = False
        if not (uid and cookie and ver):
            need_ads_sync = True
        elif prev_status in {"offline", "relogin", "unknown"}:
            need_ads_sync = True
        elif prev_status != "online" and (now - adspower_last_sync_at) >= float(adspower_sync_interval):
            need_ads_sync = True

        if need_ads_sync:
            try:
                rr_ads = relogin_member_via_adspower(
                    member_id=member_id,
                    base_url=base_url,
                    ver_hint=ver,
                    proxy=proxy,
                    adspower_api_base=adspower_api_base,
                    adspower_api_key=adspower_api_key,
                    adspower_profile_id=adspower_profile_id,
                    adspower_profile_no=adspower_profile_no,
                    auto_start=adspower_auto_start,
                    auto_form_login=adspower_auto_form_login,
                    close_after_sync=adspower_close_after_sync,
                    sync_proxy=adspower_sync_proxy,
                    timeout_s=relogin_timeout,
                    debug=debug,
                )
                patch_ads: Dict[str, Any] = {
                    "cookie": rr_ads.cookie,
                    "uid": rr_ads.uid,
                    "mid": rr_ads.mid,
                    "ver": rr_ads.ver or ver,
                    "status": "online",
                    "last_ok_at": now,
                    "last_error": "",
                    "adspower_last_sync_at": now,
                    "relogin_fail_count": 0,
                    "relogin_backoff_until": 0,
                    "request_fail_count": 0,
                    "request_cooldown_until": 0,
                }
                patch_ads.update(_relogin_session_patch(rr_ads))
                if rr_ads.base_url:
                    patch_ads["base_url"] = rr_ads.base_url
                    base_url = str(rr_ads.base_url)
                if adspower_sync_proxy and rr_ads.proxy:
                    patch_ads["proxy"] = rr_ads.proxy
                    proxy = _normalize_proxy_input(str(rr_ads.proxy))
                patch_member(doc_path, member_id, patch_ads)
                uid = rr_ads.uid
                cookie = rr_ads.cookie
                ver = rr_ads.ver or ver
                prev_status = "online"
            except Exception as e_ads:
                err_ads = f"adspower_sync_failed: {str(e_ads)[:160]}"
                if uid and cookie and ver:
                    patch_member(
                        doc_path,
                        member_id,
                        {
                            "last_error": err_ads[:200],
                            "adspower_last_sync_at": now,
                        },
                    )
                else:
                    patch_member(
                        doc_path,
                        member_id,
                        {
                            "status": "offline",
                            "last_error": err_ads[:200],
                            "adspower_last_sync_at": now,
                            "headful_online_since": 0,
                        },
                    )
                    if prev_status != "offline":
                        log_and_maybe_push(
                            config_path=Path("config.json"),
                            data_dir=doc_path.parent,
                            kind="member",
                            account=label,
                            status="offline",
                            message=f"掉线：{err_ads[:160]}",
                            extra={"member_id": member_id, "account_proxy": proxy},
                        )
                    return

    # 1) 若已有 uid/cookie/ver，先做在线检测（带节流，避免会员多时卡顿）
    if uid and cookie and ver and not force_manual_login:
        last_check_at = _to_float(m.get("last_check_at"), 0.0)
        request_cooldown_until = _to_float(m.get("request_cooldown_until"), 0.0)
        if not cooldown_enabled and request_cooldown_until:
            patch_member(doc_path, member_id, {"request_cooldown_until": 0})
            request_cooldown_until = 0.0
        if cooldown_enabled and request_cooldown_until and now < request_cooldown_until:
            return
        if prev_status == "online" and ping_min_interval > 0 and last_check_at and (now - last_check_at) < float(ping_min_interval):
            return

        patch: Dict[str, Any] = {"last_check_at": now}
        # systemTime 仅用于展示/记录：无需每轮都拉（减少一次 HTTP 往返，提升响应）
        last_systemtime_at = _to_float(m.get("last_systemtime_at"), 0.0)
        need_systemtime = True
        if systemtime_min_interval > 0 and last_systemtime_at and (now - last_systemtime_at) < float(systemtime_min_interval):
            need_systemtime = False
        timeout_eff = int(ping_timeout_s)
        if proxy:
            timeout_eff = max(timeout_eff, 15)

        if need_systemtime:
            res_sys = member_ping_system_time(
                base_url=base_url,
                ver=ver,
                uid=uid,
                cookie=cookie,
                proxy=proxy,
                timeout_s=timeout_eff,
            )
            if res_sys.get("ok"):
                patch["last_systemtime"] = res_sys.get("systemtime") or ""
                patch["last_systemtime_at"] = now

        # 登录态校验：必须走需要登录的接口
        res = member_ping_member_data(
            base_url=base_url,
            ver=ver,
            uid=uid,
            cookie=cookie,
            proxy=proxy,
            timeout_s=timeout_eff,
        )
        if res.get("ok"):
            bal = extract_balance_from_member_data(res.get("data") or {})
            patch.update(bal)
            auto_switch_secs = _to_int(m.get("auto_switch_back_headless_seconds"), 600)
            headful_online_since = _to_float(m.get("headful_online_since"), 0.0)
            if not headless:
                if prev_status != "online" or headful_online_since <= 0:
                    headful_online_since = now
                patch["headful_online_since"] = headful_online_since
                if auto_switch_secs > 0 and (now - headful_online_since) >= float(auto_switch_secs):
                    patch["relogin_headless"] = True
                    patch["headful_online_since"] = 0
            else:
                patch["headful_online_since"] = 0
            patch.update(
                {
                    "status": "online",
                    "last_ok_at": now,
                    "last_error": "",
                    "network_health": "healthy",
                    "network_last_error": "",
                    "network_cooldown_until": 0,
                    "request_fail_count": 0,
                    "request_cooldown_until": 0,
                    "relogin_fail_count": 0,
                    "relogin_backoff_until": 0,
                }
            )
            patch_member(doc_path, member_id, patch)
            if prev_status != "online":
                log_and_maybe_push(
                    config_path=Path("config.json"),
                    data_dir=doc_path.parent,
                    kind="member",
                    account=label,
                    status="online",
                    message="在线检测通过",
                    extra={"member_id": member_id, "account_proxy": proxy},
                )
            return

        err = str(res.get("error") or res.get("data") or "offline")
        data_obj = res.get("data") if isinstance(res.get("data"), dict) else {}
        if err in {"server_error", "double_login", "unexpected_response"}:
            dmsg = str(data_obj.get("msg") or data_obj.get("status") or "").strip()
            if dmsg:
                err = f"{err}:{dmsg}"
        err = err[:200]
        err_lower = err.lower()
        is_double_login = err_lower.startswith("double_login")
        is_login_invalid = _is_login_invalid_keepalive_error(err, data_obj)
        if is_login_invalid:
            force_session_invalid_relogin = True
        if session_provider == "adspower" and (is_double_login or is_login_invalid):
            force_adspower_relogin = True
        is_request_failed = err_lower.startswith("request_failed:")
        is_transient_request = is_request_failed and _is_transient_request_failure(err_lower)
        is_transient_keepalive = _is_transient_keepalive_error(err_lower) and not (
            force_session_invalid_relogin or (session_provider == "adspower" and is_double_login)
        )

        if is_request_failed:
            request_fail_count = _to_int(m.get("request_fail_count"), 0) + 1
            network_error_class = _network_error_class(err)
            request_backoff_base = max(REQUEST_BACKOFF_BASE_SECONDS, min(60, max(5, int(ping_min_interval or 0))))
            if session_provider == "adspower":
                request_backoff_base = min(request_backoff_base, 5)
            request_cooldown = _calc_backoff_seconds(
                fail_count=request_fail_count,
                base_s=request_backoff_base,
                max_s=REQUEST_BACKOFF_MAX_SECONDS,
            )
            if session_provider == "adspower":
                request_cooldown = min(request_cooldown, 8)
            patch.update(
                {
                    "request_fail_count": request_fail_count,
                    "request_cooldown_until": now + request_cooldown if cooldown_enabled else 0,
                }
            )
            if network_error_class:
                patch.update(
                    {
                        "network_health": "cooldown",
                        "network_last_error": network_error_class,
                        "network_cooldown_until": now + NETWORK_COOLDOWN_SECONDS,
                    }
                )

            last_ok_at = _to_float(m.get("last_ok_at"), 0.0)
            keep_online = is_transient_request and last_ok_at > 0 and (now - last_ok_at) <= float(ONLINE_GRACE_SECONDS)
            if keep_online:
                if cooldown_enabled and session_provider == "adspower":
                    patch["request_fail_count"] = min(request_fail_count, 2)
                    patch["request_cooldown_until"] = now + min(3, request_cooldown)
                patch.update({"status": "online", "last_error": f"network_transient: {err}"[:200]})
                patch_member(doc_path, member_id, patch)
                return

            # 旧会话可能因为站点/链路暂时不可达而长时间没有成功探活。
            # 网络错误不能证明 Cookie/UID 已失效；始终保留现有会话，等链路恢复后继续探活。
            # 只有明确的 login_required/double_login 等业务响应才允许进入自动重登流程。
            if is_transient_request:
                patch.update(
                    {
                        "status": "online" if prev_status == "online" else "offline",
                        "last_error": f"network_transient: {err}"[:200],
                        "headful_online_since": 0 if prev_status != "online" else patch.get("headful_online_since", 0),
                    }
                )
                patch_member(doc_path, member_id, patch)
                return

            patch.update({"status": "offline", "last_error": err, "headful_online_since": 0})
            patch_member(doc_path, member_id, patch)
            if prev_status != "offline":
                log_and_maybe_push(
                    config_path=Path("config.json"),
                    data_dir=doc_path.parent,
                    kind="member",
                    account=label,
                    status="offline",
                    message=f"掉线：{err}",
                    extra={"member_id": member_id, "account_proxy": proxy},
                )
            return

        if is_transient_keepalive:
            transient_fail_count = _to_int(m.get("request_fail_count"), 0) + 1
            network_error_class = _network_error_class(err)
            transient_backoff_base = max(REQUEST_BACKOFF_BASE_SECONDS, min(60, max(5, int(ping_min_interval or 0))))
            if session_provider == "adspower":
                transient_backoff_base = min(transient_backoff_base, 5)
            transient_cooldown = _calc_backoff_seconds(
                fail_count=transient_fail_count,
                base_s=transient_backoff_base,
                max_s=REQUEST_BACKOFF_MAX_SECONDS,
            )
            if session_provider == "adspower":
                transient_cooldown = min(transient_cooldown, 8)
            patch.update(
                {
                    "request_fail_count": transient_fail_count,
                    "request_cooldown_until": now + transient_cooldown if cooldown_enabled else 0,
                }
            )
            if network_error_class:
                patch.update(
                    {
                        "network_health": "cooldown",
                        "network_last_error": network_error_class,
                        "network_cooldown_until": now + NETWORK_COOLDOWN_SECONDS,
                    }
                )
            last_ok_at = _to_float(m.get("last_ok_at"), 0.0)
            if last_ok_at > 0 and (now - last_ok_at) <= float(ONLINE_GRACE_SECONDS):
                if cooldown_enabled and session_provider == "adspower":
                    patch["request_fail_count"] = min(transient_fail_count, 2)
                    patch["request_cooldown_until"] = now + min(3, transient_cooldown)
                patch.update({"status": "online", "last_error": f"keepalive_transient: {err}"[:200]})
                patch_member(doc_path, member_id, patch)
                return
            if transient_fail_count < int(TRANSIENT_FAILS_BEFORE_RELOGIN):
                patch.update({"status": "offline", "last_error": err, "headful_online_since": 0})
                patch_member(doc_path, member_id, patch)
                if prev_status != "offline":
                    log_and_maybe_push(
                        config_path=Path("config.json"),
                        data_dir=doc_path.parent,
                        kind="member",
                        account=label,
                        status="offline",
                        message=f"掉线：{err}",
                        extra={"member_id": member_id, "account_proxy": proxy},
                    )
                return
            patch.update({"request_fail_count": 0, "request_cooldown_until": 0})

        patch.update(
            {
                "status": "offline",
                "last_error": err,
                "request_fail_count": 0,
                "request_cooldown_until": 0,
                "headful_online_since": 0,
            }
        )
        if force_adspower_relogin or force_session_invalid_relogin:
            patch["relogin_backoff_until"] = 0
            patch["last_relogin_at"] = 0
        patch_member(doc_path, member_id, patch)
        if prev_status != "offline":
            log_and_maybe_push(
                config_path=Path("config.json"),
                data_dir=doc_path.parent,
                kind="member",
                account=label,
                status="offline",
                message=f"掉线：{err}",
                extra={"member_id": member_id, "account_proxy": proxy},
            )

    # 2) 掉线/缺字段：自动重登
    if (not auto_relogin) and (not force_manual_login):
        return
    if session_provider == "adspower":
        if not (adspower_profile_id or adspower_profile_no):
            patch_member(
                doc_path,
                member_id,
                {
                    "status": "unknown",
                    "last_error": "缺少 AdsPower profile_id/profile_no：请在 WebUI 从 Ads 环境导入",
                    "headful_online_since": 0,
                },
            )
            return
        if (not force_adspower_relogin) and relogin_backoff_until and now < relogin_backoff_until:
            return
        last_relogin_at = _to_float(m.get("last_relogin_at"), 0.0)
        if (not force_adspower_relogin) and last_relogin_at and (now - last_relogin_at) < max(5, relogin_min_interval):
            return

        print(f"[{now_iso()}] [member:{member_id}] 掉线/缺字段，开始自动重登（AdsPower）", flush=True)
        patch_member(
            doc_path,
            member_id,
            {
                "status": "relogin",
                "last_relogin_at": now,
                "last_error": "",
                "headful_online_since": 0,
            },
        )
        log_and_maybe_push(
            config_path=Path("config.json"),
            data_dir=doc_path.parent,
            kind="member",
            account=label,
            status="relogin",
            message="开始自动重登（AdsPower）",
            extra={"member_id": member_id, "account_proxy": proxy},
        )
        try:
            rr_ads2 = relogin_member_via_adspower(
                member_id=member_id,
                base_url=base_url,
                ver_hint=ver,
                proxy=proxy,
                adspower_api_base=adspower_api_base,
                adspower_api_key=adspower_api_key,
                adspower_profile_id=adspower_profile_id,
                adspower_profile_no=adspower_profile_no,
                auto_start=adspower_auto_start,
                auto_form_login=adspower_auto_form_login,
                close_after_sync=adspower_close_after_sync,
                sync_proxy=adspower_sync_proxy,
                timeout_s=relogin_timeout,
                debug=debug,
            )
            base_url_eff = str(rr_ads2.base_url or base_url).strip()
            proxy_eff = _normalize_proxy_input(str(rr_ads2.proxy or proxy))
            patch_ads2: Dict[str, Any] = {
                "cookie": rr_ads2.cookie,
                "uid": rr_ads2.uid,
                "mid": rr_ads2.mid,
                "ver": rr_ads2.ver or ver,
                "status": "online",
                "last_ok_at": now_ts(),
                "last_error": "",
                "relogin_fail_count": 0,
                "relogin_backoff_until": 0,
                "request_fail_count": 0,
                "request_cooldown_until": 0,
                "headful_online_since": 0,
                "adspower_last_sync_at": now_ts(),
            }
            patch_ads2.update(_relogin_session_patch(rr_ads2))
            if base_url_eff:
                patch_ads2["base_url"] = base_url_eff
            if adspower_sync_proxy and proxy_eff:
                patch_ads2["proxy"] = proxy_eff
            try:
                res2 = member_ping_member_data(
                    base_url=base_url_eff or base_url,
                    ver=patch_ads2.get("ver") or ver,
                    uid=rr_ads2.uid,
                    cookie=rr_ads2.cookie,
                    proxy=proxy_eff,
                    timeout_s=min(15, max(5, int(ping_timeout_s))),
                )
                if res2.get("ok"):
                    patch_ads2.update(extract_balance_from_member_data(res2.get("data") or {}))
            except Exception:
                pass
            patch_member(doc_path, member_id, patch_ads2)
            log_and_maybe_push(
                config_path=Path("config.json"),
                data_dir=doc_path.parent,
                kind="member",
                account=label,
                status="online",
                message="AdsPower 会话恢复成功",
                extra={"member_id": member_id, "account_proxy": (proxy_eff or proxy)},
            )
            return
        except Exception as e_ads2:
            msg_ads2 = str(e_ads2)
            fail_count = relogin_fail_count + 1
            backoff_s = _calc_backoff_seconds(
                fail_count=fail_count,
                base_s=max(5, int(relogin_min_interval)),
                max_s=RELOGIN_BACKOFF_MAX_SECONDS,
            )
            err_brief = msg_ads2[:140]
            patch_member(
                doc_path,
                member_id,
                {
                    "status": "offline",
                    "last_error": f"adspower_relogin_failed: {err_brief} (第 {fail_count} 次，约 {backoff_s}s 后重试)"[:200],
                    "relogin_fail_count": fail_count,
                    "relogin_backoff_until": now_ts() + backoff_s,
                    "adspower_last_sync_at": now_ts(),
                    "headful_online_since": 0,
                },
            )
            log_and_maybe_push(
                config_path=Path("config.json"),
                data_dir=doc_path.parent,
                kind="member",
                account=label,
                status="failed",
                message=f"AdsPower 重登失败：{err_brief}（第 {fail_count} 次，约 {backoff_s}s 后重试）",
                extra={"member_id": member_id, "account_proxy": proxy},
            )
            return

    if not (base_url and username and password):
        patch_member(
            doc_path,
            member_id,
            {
                "status": "unknown",
                "last_error": "缺少登录信息：请在 WebUI 的“添加会员”填写平台网址/会员账号/登录密码",
                "headful_online_since": 0,
            },
        )
        return

    if (not force_manual_login) and (not force_session_invalid_relogin) and relogin_backoff_until and now < relogin_backoff_until:
        return

    last_relogin_at = _to_float(m.get("last_relogin_at"), 0.0)
    if (
        (not force_manual_login)
        and (not force_session_invalid_relogin)
        and last_relogin_at
        and (now - last_relogin_at) < max(5, relogin_min_interval)
    ):
        return

    print(f"[{now_iso()}] [member:{member_id}] 掉线/缺字段，开始自动重登（Playwright）", flush=True)
    relogin_patch: Dict[str, Any] = {"status": "relogin", "last_relogin_at": now, "last_error": "", "headful_online_since": 0}
    if first_login_force_headful:
        relogin_patch["relogin_headless"] = False
    patch_member(doc_path, member_id, relogin_patch)
    log_and_maybe_push(
        config_path=Path("config.json"),
        data_dir=doc_path.parent,
        kind="member",
        account=label,
        status="relogin",
        message="开始自动重登（Playwright）",
        extra={"member_id": member_id, "account_proxy": proxy},
    )
    try:
        rr = relogin_member_via_playwright(
            member_id=member_id,
            base_url=base_url,
            username=username,
            password=password,
            ver_hint=ver,
            proxy=proxy,
            user_data_root=user_data_root,
            login_tab=relogin_login_tab,
            headless=headless_eff,
            timeout_s=relogin_timeout,
            debug=debug,
        )
        patch = {
            "cookie": rr.cookie,
            "uid": rr.uid,
            "mid": rr.mid,
            "ver": rr.ver or ver,
            "status": "online",
            "last_ok_at": now_ts(),
            "last_error": "",
            "relogin_fail_count": 0,
            "relogin_backoff_until": 0,
            "request_fail_count": 0,
            "request_cooldown_until": 0,
            "headful_online_since": now_ts() if (not headless_eff) else 0,
            "relogin_headless": bool(headless_eff),
        }
        patch.update(_relogin_session_patch(rr))
        if rr.base_url:
            patch["base_url"] = rr.base_url
        # 重登成功后立即拉一次 get_member_data，尽快填充余额/额度
        try:
            res2 = member_ping_member_data(
                base_url=base_url,
                ver=patch.get("ver") or ver,
                uid=rr.uid,
                cookie=rr.cookie,
                proxy=proxy,
                timeout_s=min(15, max(5, int(ping_timeout_s))),
            )
            if res2.get("ok"):
                patch.update(extract_balance_from_member_data(res2.get("data") or {}))
        except Exception:
            pass
        patch_member(doc_path, member_id, patch)
        log_and_maybe_push(
            config_path=Path("config.json"),
            data_dir=doc_path.parent,
            kind="member",
            account=label,
            status="online",
            message="重新登录成功",
            extra={"member_id": member_id, "account_proxy": proxy},
        )
    except Exception as e:
        msg = str(e)
        # 站点要求验证码：headless 无法完成时，自动切换为可视（非 headless）再尝试一次，
        # 并把重登间隔拉长，避免每 20~30 秒狂刷触发更严格风控。
        if ("需要验证码" in msg or "checkNeed" in msg) and headless_eff:
            print(f"[{now_iso()}] [member:{member_id}] 检测到验证码要求，自动切换为可视登录重试一次（需要你在弹出的浏览器完成验证码）", flush=True)
            try:
                rr2 = relogin_member_via_playwright(
                    member_id=member_id,
                    base_url=base_url,
                    username=username,
                    password=password,
                    ver_hint=ver,
                    proxy=proxy,
                    user_data_root=user_data_root,
                    login_tab=relogin_login_tab,
                    headless=False,
                    timeout_s=max(int(relogin_timeout), 300),
                    debug=debug,
                )
                patch = {
                    "cookie": rr2.cookie,
                    "uid": rr2.uid,
                    "mid": rr2.mid,
                    "ver": rr2.ver or ver,
                    "status": "online",
                    "last_ok_at": now_ts(),
                    "last_error": "",
                    "relogin_fail_count": 0,
                    "relogin_backoff_until": 0,
                    "request_fail_count": 0,
                    "request_cooldown_until": 0,
                    "headful_online_since": now_ts(),
                    # 后续继续用可视/无头由你自己在 arsenal.json 里控制；这里不强制改回去
                    "relogin_headless": False,
                }
                patch.update(_relogin_session_patch(rr2))
                if rr2.base_url:
                    patch["base_url"] = rr2.base_url
                patch_member(doc_path, member_id, patch)
                log_and_maybe_push(
                    config_path=Path("config.json"),
                    data_dir=doc_path.parent,
                    kind="member",
                    account=label,
                    status="online",
                    message="可视登录完成，已刷新会话",
                    extra={"member_id": member_id, "account_proxy": proxy},
                )
                return
            except Exception as e2:
                msg = str(e2)
                fail_count = relogin_fail_count + 1
                backoff_s = _calc_backoff_seconds(
                    fail_count=fail_count,
                    base_s=max(5, int(relogin_min_interval)),
                    max_s=RELOGIN_BACKOFF_MAX_SECONDS,
                )
                patch_member(
                    doc_path,
                    member_id,
                    {
                        "status": "offline",
                        "last_error": f"captcha_required: {msg[:140]} (下次重试约 {backoff_s}s)"[:200],
                        "relogin_headless": False,
                        "relogin_min_interval_seconds": max(int(relogin_min_interval), 5),
                        "relogin_fail_count": fail_count,
                        "relogin_backoff_until": now_ts() + backoff_s,
                        "headful_online_since": 0,
                    },
                )
                log_and_maybe_push(
                    config_path=Path("config.json"),
                    data_dir=doc_path.parent,
                    kind="member",
                    account=label,
                    status="failed",
                    message=f"需要验证码：{msg[:140]}（下次重试约 {backoff_s}s）",
                    extra={"member_id": member_id, "account_proxy": proxy},
                )
                return

        print(f"[{now_iso()}] [member:{member_id}] 自动重登失败：{e}", flush=True)
        fail_count = relogin_fail_count + 1
        backoff_s = _calc_backoff_seconds(
            fail_count=fail_count,
            base_s=max(5, int(relogin_min_interval)),
            max_s=RELOGIN_BACKOFF_MAX_SECONDS,
        )
        err_brief = msg[:140]
        patch: Dict[str, Any] = {
            "status": "offline",
            "last_error": f"relogin_failed: {err_brief} (第 {fail_count} 次，约 {backoff_s}s 后重试)"[:200],
            "relogin_fail_count": fail_count,
            "relogin_backoff_until": now_ts() + backoff_s,
            "headful_online_since": 0,
        }
        if headless_eff and fail_count >= RELOGIN_TIMEOUT_SWITCH_VISIBLE_FAILS and _is_relogin_timeout_error(msg):
            patch["relogin_headless"] = False
            patch["last_error"] = f"relogin_failed: 连续超时，已切换可视登录；{err_brief}"[:200]
        patch_member(doc_path, member_id, patch)
        log_and_maybe_push(
            config_path=Path("config.json"),
            data_dir=doc_path.parent,
            kind="member",
            account=label,
            status="failed",
            message=f"重新登录失败：{err_brief}（第 {fail_count} 次，约 {backoff_s}s 后重试）",
            extra={"member_id": member_id, "account_proxy": proxy},
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Member Arsenal Watcher (auto login + keepalive)")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--interval", type=int, default=3)
    parser.add_argument("--ping-timeout", type=int, default=10)
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    lock_path = data_dir / "member_arsenal_watch.lock"
    if not acquire_single_instance_lock(lock_path):
        print(f"[{now_iso()}] member_arsenal_watch 已在运行，退出 lock={lock_path}", flush=True)
        return 2
    doc_path = data_dir / "arsenal.json"
    if not doc_path.exists():
        atomic_write_json(doc_path, {"members": []})

    user_data_root = data_dir / "_pw_member"
    user_data_root.mkdir(parents=True, exist_ok=True)

    normalize_doc(doc_path)

    while True:
        doc = read_json_file(doc_path, {"members": []})
        request_cooldown_enabled = _to_bool(doc.get("request_cooldown_enabled"), True)
        members = []
        for raw_member in (doc.get("members") or []):
            if not isinstance(raw_member, dict):
                continue
            normalized_member = ensure_member_defaults(raw_member)
            normalized_member["request_cooldown_enabled"] = request_cooldown_enabled
            members.append(normalized_member)

        # 优先处理“需要重登/缺会话”的会员，避免新增会员排队过久导致“登录很慢”
        def _sort_key(x: Dict[str, Any]) -> Any:
            status = str(x.get("status") or "").strip().lower()
            has_session = bool(str(x.get("uid") or "").strip()) and bool(str(x.get("cookie") or "").strip()) and bool(str(x.get("ver") or "").strip())
            if status == "relogin" or not has_session:
                pri = 0
            elif status == "offline":
                pri = 1
            elif status == "unknown":
                pri = 2
            else:
                pri = 3  # online / others
            try:
                last_relogin_at = float(x.get("last_relogin_at") or 0)
            except Exception:
                last_relogin_at = 0.0
            try:
                last_check_at = float(x.get("last_check_at") or 0)
            except Exception:
                last_check_at = 0.0
            return (pri, last_relogin_at, last_check_at)

        members.sort(key=_sort_key)

        process_members_concurrently(
            doc_path=doc_path,
            members=members,
            user_data_root=user_data_root,
            ping_timeout_s=int(args.ping_timeout),
            debug=bool(args.debug),
            request_cooldown_enabled=request_cooldown_enabled,
        )

        if args.once:
            return 0
        time.sleep(max(3, int(args.interval)))


if __name__ == "__main__":
    raise SystemExit(main())
