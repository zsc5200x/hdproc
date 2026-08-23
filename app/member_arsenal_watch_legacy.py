import argparse
import json
import os
import time
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse, urlencode

import requests
from tg_status_log import log_and_maybe_push
from playwright_proxy import build_playwright_proxy
from portable_env import ensure_playwright_browsers_path

ensure_playwright_browsers_path(Path(__file__).resolve().parent)


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def now_ts() -> float:
    return time.time()


def safe_name(name: str) -> str:
    s = "".join(c for c in str(name) if c.isalnum() or c in ("-", "_")).strip()
    return s or "default"


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
    pairs: list[Tuple[str, str]] = []
    for c in cookies:
        name = str(c.get("name") or "")
        value = str(c.get("value") or "")
        domain = str(c.get("domain") or "").lower().lstrip(".")
        if not name:
            continue
        # Playwright 可能返回 host-only cookie 的 domain 为空；此时也应保留
        if not domain:
            pairs.append((name, value))
            continue
        if host and (domain == host or host.endswith(domain) or domain.endswith(host)):
            pairs.append((name, value))
    return dedupe_cookie_pairs(pairs)


def member_ping_system_time(
    *,
    base_url: str,
    ver: str,
    uid: str,
    cookie: str,
    proxy: str = "",
    timeout_s: int = 15,
) -> Dict[str, Any]:
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
    base_url = str(base_url or "").rstrip("/")
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

    if str(data.get("code") or "").strip() == "error":
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


def relogin_member_via_playwright(
    *,
    member_id: str,
    base_url: str,
    username: str,
    password: str,
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

    base = str(base_url or "").rstrip("/")
    if not base:
        raise RuntimeError("缺少 base_url")

    host = urlparse(base).hostname or ""
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

    with sync_playwright() as p:
        proxy_cfg = _parse_proxy_for_playwright(proxy)
        try:
            ctx = p.chromium.launch_persistent_context(
                str(user_data_dir),
                headless=bool(headless),
                viewport={"width": 1280, "height": 860},
                ignore_https_errors=True,
                args=["--disable-blink-features=AutomationControlled", "--ignore-certificate-errors"],
                proxy=proxy_cfg,
            )
        except Exception as e:
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
            hint.append("可移植包请用 Start_AG_Matrix.bat 启动，并确认包根目录存在 ms-playwright/")
            raise RuntimeError(f"Playwright 启动浏览器失败：{type(e).__name__}: {e}；{'；'.join(hint)}") from e
        try:
            page = ctx.new_page()
            goto_timeout_ms = max(30_000, int(timeout_s) * 1000)
            page.goto(base + "/", wait_until="domcontentloaded", timeout=goto_timeout_ms)

            try_select_login_tab(page)

            try:
                page.wait_for_selector("input[type='password']", timeout=15_000)
            except Exception:
                pass

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

            try:
                with page.expect_response(is_chk_login, timeout=goto_timeout_ms) as resp_info:
                    if btn:
                        btn.click(timeout=15_000)
                    else:
                        pwd_loc.press("Enter")
                resp = resp_info.value
            except Exception as e:
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
                                resp2 = page.wait_for_response(is_chk_login, timeout=remaining_ms)
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

            # 有些站点会在登录后通过 JS 再写入一批关键 cookie（例如 myGameVer/iorChgSw 等）
            # 这里稍等片刻再取 cookie，提高下注接口通过率
            try:
                page.wait_for_timeout(1200)
            except Exception:
                pass

            # 触发登录后初始化请求（抓包里常见：memSet/check、service_mainget/login=Y、get_version）
            # 目的：让站点补齐一批关键 cookie / server-side session 状态，避免后续接口报 doubleLogin。
            try:
                api = ctx.request
                transform_url = f"{base_url}/transform.php?ver={ver}"
                api.post(transform_url, form={"p": "memSet", "ver": ver, "uid": uid, "langx": "zh-cn", "action": "check"})
                api.post(transform_url, form={"p": "service_mainget", "ver": ver, "langx": "zh-cn", "uid": uid, "login": "Y"})
                api.post(transform_url, form={"p": "get_version", "uid": uid, "langx": "zh-cn"})
            except Exception:
                pass
            try:
                page.wait_for_timeout(800)
            except Exception:
                pass
            cookies = ctx.cookies()
            cookie_str = cookies_to_header(cookies, host=host)
            if not cookie_str:
                raise RuntimeError("登录后未拿到 cookie（可能登录未成功或站点策略变化）")
            if not uid:
                raise RuntimeError("登录后未拿到 uid（响应里没有 uid）")

            log("已重新登录并刷新 Cookie/UID，准备继续在线检测")
            return ReloginResult(cookie=cookie_str, uid=uid, mid=mid, ver=ver)
        finally:
            try:
                ctx.close()
            except Exception:
                pass


def patch_member(doc_path: Path, member_id: str, patch: Dict[str, Any]) -> None:
    doc = read_json_file(doc_path, {"members": []})
    members = doc.get("members") or []
    changed = False
    out = []
    for m in members:
        if isinstance(m, dict) and str(m.get("id") or "") == member_id:
            nm = dict(m)
            nm.update(patch)
            out.append(nm)
            changed = True
        else:
            out.append(m)
    if not changed:
        return
    doc["members"] = out
    atomic_write_json(doc_path, doc)


def ensure_member_defaults(m: Dict[str, Any]) -> Dict[str, Any]:
    x = dict(m)
    x.setdefault("id", uuid.uuid4().hex)
    x.setdefault("label", x.get("username") or "")
    x.setdefault("proxy", "")
    x.setdefault("uid", "")
    x.setdefault("mid", "")
    x.setdefault("cookie", "")
    x.setdefault("ver", "")
    x.setdefault("status", "unknown")
    x.setdefault("last_check_at", 0)
    x.setdefault("last_ok_at", 0)
    x.setdefault("last_relogin_at", 0)
    x.setdefault("last_error", "")
    x.setdefault("last_systemtime", "")
    x.setdefault("last_systemtime_at", 0)
    x.setdefault("auto_relogin", True)
    x.setdefault("relogin_headless", True)
    x.setdefault("relogin_login_tab", 3)
    x.setdefault("relogin_timeout_seconds", 120)
    x.setdefault("relogin_min_interval_seconds", 15)
    # 在线检测节流：避免会员多时每轮都全量 ping 导致“反应很慢”
    x.setdefault("ping_min_interval_seconds", 60)
    # system_time 仅用于展示/记录：默认 5 分钟拉一次即可
    x.setdefault("systemtime_min_interval_seconds", 300)
    # 余额/额度（来自 get_member_data）
    x.setdefault("currency", "")
    x.setdefault("cash", "")
    x.setdefault("maxcredit", "")
    x.setdefault("balance_updated_at", 0)
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
    out = []
    for m in members:
        if not isinstance(m, dict):
            continue
        out.append(ensure_member_defaults(m))
    doc["members"] = out
    atomic_write_json(doc_path, doc)


def process_member(
    *,
    doc_path: Path,
    m: Dict[str, Any],
    user_data_root: Path,
    ping_timeout_s: int,
    debug: bool,
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

    auto_relogin = bool(m.get("auto_relogin", True))
    headless = bool(m.get("relogin_headless", True))
    relogin_login_tab = int(m.get("relogin_login_tab") or 3)
    relogin_timeout = int(m.get("relogin_timeout_seconds") or 120)
    relogin_min_interval = int(m.get("relogin_min_interval_seconds") or 15)
    ping_min_interval = int(m.get("ping_min_interval_seconds") or 0)
    systemtime_min_interval = int(m.get("systemtime_min_interval_seconds") or 0)

    now = now_ts()

    # 如果用户保存的是“ip|port|user|pass|备注”格式，这里统一归一化一次，避免 Playwright 报 Invalid URI
    try:
        raw_proxy = str(m.get("proxy") or "").strip()
        if raw_proxy and proxy and raw_proxy != proxy:
            patch_member(doc_path, member_id, {"proxy": proxy})
    except Exception:
        pass

    # 1) 若已有 uid/cookie/ver，先做在线检测（带节流，避免会员多时卡顿）
    if uid and cookie and ver:
        try:
            last_check_at = float(m.get("last_check_at") or 0)
        except Exception:
            last_check_at = 0.0
        if prev_status == "online" and ping_min_interval > 0 and last_check_at and (now - last_check_at) < float(ping_min_interval):
            return

        patch: Dict[str, Any] = {"last_check_at": now}
        # systemTime 仅用于展示/记录：无需每轮都拉（减少一次 HTTP 往返，提升响应）
        try:
            last_systemtime_at = float(m.get("last_systemtime_at") or 0)
        except Exception:
            last_systemtime_at = 0.0
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
            patch.update({"status": "online", "last_ok_at": now, "last_error": ""})
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

        err = str(res.get("error") or res.get("data") or "offline")[:200]
        patch.update({"status": "offline", "last_error": err})
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
        # 网络/代理抖动（超时/连接失败）不应触发重登：保留会话，下一轮再试即可
        if str(err).lower().startswith("request_failed:"):
            return

    # 2) 掉线/缺字段：自动重登
    if not auto_relogin:
        return
    if not (base_url and username and password):
        patch_member(
            doc_path,
            member_id,
            {
                "status": "unknown",
                "last_error": "缺少登录信息：请在 WebUI 的“添加会员”填写平台网址/会员账号/登录密码",
            },
        )
        return

    last_relogin_at = float(m.get("last_relogin_at") or 0)
    if last_relogin_at and (now - last_relogin_at) < max(5, relogin_min_interval):
        return

    print(f"[{now_iso()}] [member:{member_id}] 掉线/缺字段，开始自动重登（Playwright）", flush=True)
    patch_member(doc_path, member_id, {"status": "relogin", "last_relogin_at": now, "last_error": ""})
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
            proxy=proxy,
            user_data_root=user_data_root,
            login_tab=relogin_login_tab,
            headless=headless,
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
        }
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
        if ("需要验证码" in msg or "checkNeed" in msg) and headless:
            print(f"[{now_iso()}] [member:{member_id}] 检测到验证码要求，自动切换为可视登录重试一次（需要你在弹出的浏览器完成验证码）", flush=True)
            try:
                rr2 = relogin_member_via_playwright(
                    member_id=member_id,
                    base_url=base_url,
                    username=username,
                    password=password,
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
                    # 后续继续用可视/无头由你自己在 arsenal.json 里控制；这里不强制改回去
                    "relogin_headless": False,
                }
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
                patch_member(
                    doc_path,
                    member_id,
                    {
                        "status": "offline",
                        "last_error": f"captcha_required: {msg[:200]}",
                        "relogin_headless": False,
                        "relogin_min_interval_seconds": max(int(relogin_min_interval), 60),
                    },
                )
                log_and_maybe_push(
                    config_path=Path("config.json"),
                    data_dir=doc_path.parent,
                    kind="member",
                    account=label,
                    status="failed",
                    message=f"需要验证码：{msg[:200]}",
                    extra={"member_id": member_id, "account_proxy": proxy},
                )
                return

        print(f"[{now_iso()}] [member:{member_id}] 自动重登失败：{e}", flush=True)
        patch_member(doc_path, member_id, {"status": "offline", "last_error": f"relogin_failed: {msg[:200]}"})
        log_and_maybe_push(
            config_path=Path("config.json"),
            data_dir=doc_path.parent,
            kind="member",
            account=label,
            status="failed",
            message=f"重新登录失败：{msg[:200]}",
            extra={"member_id": member_id, "account_proxy": proxy},
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Member Arsenal Watcher (auto login + keepalive)")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--interval", type=int, default=15)
    parser.add_argument("--ping-timeout", type=int, default=10)
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    doc_path = data_dir / "arsenal.json"
    if not doc_path.exists():
        atomic_write_json(doc_path, {"members": []})

    user_data_root = data_dir / "_pw_member"
    user_data_root.mkdir(parents=True, exist_ok=True)

    normalize_doc(doc_path)

    while True:
        doc = read_json_file(doc_path, {"members": []})
        members = [ensure_member_defaults(m) for m in (doc.get("members") or []) if isinstance(m, dict)]

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

        for m2 in members:
            process_member(
                doc_path=doc_path,
                m=m2,
                user_data_root=user_data_root,
                ping_timeout_s=int(args.ping_timeout),
                debug=bool(args.debug),
            )

        if args.once:
            return 0
        time.sleep(max(3, int(args.interval)))


if __name__ == "__main__":
    raise SystemExit(main())
