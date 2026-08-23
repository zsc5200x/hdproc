import json
import os
import re
import time
import gzip
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Set
from urllib.parse import parse_qs


def _now_ms() -> int:
    return int(time.time() * 1000)


def _safe_write_jsonl(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def _redact_form(form: Dict[str, Any]) -> Dict[str, Any]:
    sensitive_keys = {
        "password",
        "pwd",
        "pass",
        "passwd",
        "cookie",
        "uid",
        "blackbox",
        "token",
        "authorization",
        "auth",
        "login_mid",
        "mid",
        "session",
    }
    out: Dict[str, Any] = {}
    for k, v in (form or {}).items():
        k2 = str(k or "")
        low = k2.lower()
        if low in sensitive_keys or any(x in low for x in ["pass", "pwd", "token", "blackbox", "cookie"]):
            out[k2] = "***"
            continue
        if isinstance(v, list):
            out[k2] = v[0] if v else ""
        else:
            out[k2] = v
    return out


def _parse_form_encoded(post_data: str) -> Dict[str, Any]:
    try:
        qs = parse_qs(post_data, keep_blank_values=True)
        return {k: (v[0] if isinstance(v, list) and v else "") for k, v in qs.items()}
    except Exception:
        return {"_raw": post_data}


def _mask_text(s: str, *, max_len: int = 260) -> str:
    t = (s or "").strip().replace("\n", "\\n")
    if len(t) > max_len:
        return t[:max_len] + "…"
    return t


def _extract_xml_text(t: str, tag: str) -> str:
    try:
        root = ET.fromstring((t or "").strip())
    except Exception:
        return ""
    x = ""
    try:
        x = str(root.findtext(tag) or "").strip()
    except Exception:
        x = ""
    return x


def _extract_game_ctx_from_game_list(xml_text: str) -> Dict[str, str]:
    """
    从 get_game_list 的 XML 里提取一场比赛的 gid/lid/ecid（用于 get_game_more）。
    只挑第一场（用于抓包/分析，不做业务逻辑）。
    """
    t = (xml_text or "").strip()
    if not t.startswith("<"):
        return {}
    try:
        root = ET.fromstring(t)
    except Exception:
        return {}

    # 优先：ec 包裹结构，便于取 ecid
    for ec in root.iter("ec"):
        ecid_attr = ""
        try:
            ecid_attr = str(ec.get("id") or ec.get("ID") or "").strip()
        except Exception:
            ecid_attr = ""
        ecid_num = ""
        if ecid_attr:
            m = re.search(r"(\\d+)$", ecid_attr)
            if m:
                ecid_num = m.group(1)
        for g in ec.iter("game"):
            gid = str((g.findtext("GID") or g.findtext("gid") or "")).strip()
            lid = str((g.findtext("LID") or g.findtext("lid") or "")).strip()
            if gid and lid and ecid_num:
                return {"gid": gid, "lid": lid, "ecid": ecid_num}

    # fallback：没有 ec 节点时，尽量从 game 内取
    for g in root.iter("game"):
        gid = str((g.findtext("GID") or g.findtext("gid") or "")).strip()
        lid = str((g.findtext("LID") or g.findtext("lid") or "")).strip()
        ecid_num = str((g.findtext("ecid") or g.findtext("ECID") or "")).strip()
        if gid and lid and ecid_num:
            return {"gid": gid, "lid": lid, "ecid": ecid_num}
        if gid and lid:
            return {"gid": gid, "lid": lid, "ecid": ""}
    return {}


@dataclass
class CaptureConfig:
    base_url: str
    username: str
    password: str
    headless: bool = True
    timeout_s: int = 120
    user_data_dir: str = ""
    skip_login: bool = False


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except Exception:
        raise SystemExit("未安装 playwright，请先运行：python -m pip install -r requirements.txt && python -m playwright install chromium")

    base_url = os.environ.get("HGA_BASE_URL", "https://mos077.com").strip()
    username = os.environ.get("HGA_USERNAME", "").strip()
    password = os.environ.get("HGA_PASSWORD", "").strip()
    headless = str(os.environ.get("HGA_HEADLESS", "1")).strip() not in {"0", "false", "False"}
    timeout_s = int(float(os.environ.get("HGA_TIMEOUT", "180")) or 180)
    user_data_dir = os.environ.get("HGA_PW_USER_DATA_DIR", "").strip()
    skip_login = str(os.environ.get("HGA_SKIP_LOGIN", "0")).strip() in {"1", "true", "True"}
    interactive = str(os.environ.get("HGA_INTERACTIVE", "")).strip() in {"1", "true", "True"} or (not headless)
    auto_stop_seconds = int(float(os.environ.get("HGA_AUTO_STOP_SECONDS", "0")) or 0)
    auto_stop_seconds = max(0, min(3600, auto_stop_seconds))
    hold_seconds = float(os.environ.get("HGA_HOLD_SECONDS", "8") or 8)
    hold_seconds = max(0.0, min(600.0, hold_seconds))

    # 允许复用已有的 Playwright 持久化 profile（不必在命令行暴露账号密码）
    # - 若 profile 已登录：可不提供 username/password
    # - 若未登录：仍需要 username/password
    if user_data_dir:
        try:
            if not Path(user_data_dir).exists():
                raise SystemExit(f"HGA_PW_USER_DATA_DIR 不存在：{user_data_dir}")
        except Exception:
            raise SystemExit(f"HGA_PW_USER_DATA_DIR 不可访问：{user_data_dir}")

    cap_dir = Path("app/data/captures")
    ts = _now_ms()
    out_jsonl = cap_dir / f"hga027_capture_{ts}.jsonl"
    out_png = cap_dir / f"hga027_after_login_{ts}.png"

    def log(obj: Dict[str, Any]) -> None:
        _safe_write_jsonl(out_jsonl, obj)

    with sync_playwright() as p:
        if user_data_dir:
            context = p.chromium.launch_persistent_context(
                user_data_dir,
                headless=headless,
                viewport={"width": 1400, "height": 900},
                ignore_https_errors=True,
                args=["--disable-blink-features=AutomationControlled", "--ignore-certificate-errors"],
            )
            page = context.new_page()
        else:
            browser = p.chromium.launch(
                headless=headless,
                args=["--disable-blink-features=AutomationControlled", "--ignore-certificate-errors"],
            )
            context = browser.new_context(ignore_https_errors=True, viewport={"width": 1400, "height": 900})
            page = context.new_page()

        # 捕获 transform.php 请求/响应
        pending: Dict[str, Dict[str, Any]] = {}
        seen_nonempty_p3type_game_list: Set[str] = set()
        capture_full_for_p = {
            "get_page_count",
            "get_game_list",
            "get_game_more",
            "FT_order_view",
            "FT_bet",
            "game_list_FT",
            "right_score",
            "chk_login",
        }
        max_capture_bytes = int(float(os.environ.get("HGA_CAPTURE_MAX_BYTES", "5000000")) or 5_000_000)

        def on_request(req) -> None:
            try:
                url = req.url or ""
                if "transform.php" not in url:
                    return
                post_data = req.post_data or ""
                form = _parse_form_encoded(post_data) if post_data else {}
                form2 = _redact_form(form)
                rid = str(id(req))
                p_val = str(form2.get("p") or "")
                if p_val == "get_game_list":
                    p3 = str(form.get("p3type") or "").strip()
                    if p3:
                        seen_nonempty_p3type_game_list.add(p3)
                rec = {
                    "ts": int(time.time()),
                    "ts_ms": _now_ms(),
                    "event": "request",
                    "rid": rid,
                    "method": req.method,
                    "url": url,
                    "p": p_val,
                    "form": form2,
                }
                pending[rid] = rec
                log(rec)
            except Exception:
                return

        def on_response(resp) -> None:
            try:
                req = resp.request
                url = req.url or ""
                if "transform.php" not in url:
                    return
                rid = str(id(req))
                req_rec = pending.get(rid) or {}
                p_val = str(req_rec.get("p") or "")
                ct = ""
                try:
                    ct = (resp.headers or {}).get("content-type") or ""
                except Exception:
                    ct = ""
                body_head = ""
                body_path = ""
                try:
                    body_text = resp.text() or ""
                    body_head = _mask_text(body_text, max_len=320)
                    # 针对关键接口保存完整响应，便于离线分析盘口线/水位字段
                    if p_val in capture_full_for_p and body_text:
                        suffix = "xml" if body_text.lstrip().startswith("<") else "txt"
                        raw_bytes = body_text.encode("utf-8", errors="ignore")
                        # 防止单文件过大：默认上限 5MB（可通过 HGA_CAPTURE_MAX_BYTES 调整）
                        if len(raw_bytes) <= max_capture_bytes:
                            out_body = cap_dir / f"hga027_{ts}_{p_val}_{rid}.{suffix}"
                            # 1MB 以上用 gzip，避免 captures 目录膨胀过快
                            if len(raw_bytes) > 1_000_000:
                                out_gz = Path(str(out_body) + ".gz")
                                with gzip.open(out_gz, "wb") as gf:
                                    gf.write(raw_bytes)
                                body_path = str(out_gz)
                            else:
                                out_body.write_bytes(raw_bytes)
                                body_path = str(out_body)
                except Exception:
                    body_head = ""
                rec = {
                    "ts": int(time.time()),
                    "ts_ms": _now_ms(),
                    "event": "response",
                    "rid": rid,
                    "status": resp.status,
                    "url": resp.url,
                    "content_type": ct,
                    "p": p_val,
                    "body_path": body_path,
                    "response_head": body_head,
                }
                log(rec)
            except Exception:
                return

        page.on("request", on_request)
        page.on("response", on_response)

        # 1) 打开登录页
        page.goto(base_url + "/", wait_until="domcontentloaded", timeout=max(30_000, timeout_s * 1000))

        # 该站点登录表单由 JS 异步渲染：domcontentloaded 后立刻查找输入框会拿不到元素。
        # 这里做一次短等待，并尝试等待密码框出现（已登录则会超时并继续）。
        try:
            page.wait_for_timeout(800)
            page.wait_for_selector(
                "input[type='password'], input[id*='pass' i], input[name*='pass' i], #password, #pwd",
                timeout=12_000,
            )
        except Exception:
            pass

        # 2) 若当前未登录，则填写账号密码（尽量通用）
        user_loc = page.locator(
            "#username, #usr, input[name='username'], input[name='usr'], input[autocomplete='username'], "
            "input[placeholder*='账号'], input[placeholder*='帳號'], input[placeholder*='帐号'], input[placeholder*='會員'], input[placeholder*='用户名'], "
            "input[id*='user' i], input[name*='user' i], input[id*='account' i], input[name*='account' i]"
        )
        pwd_loc = page.locator(
            "#password, #pwd, input[name='password'], input[name='pwd'], input[type='password'], input[autocomplete='current-password'], "
            "input[placeholder*='密码'], input[placeholder*='密碼'], input[id*='pass' i], input[name*='pass' i]"
        )

        need_login = False
        if not skip_login:
            try:
                # 不依赖 is_visible：该站点有时先渲染 DOM，再做动画/样式切换
                need_login = pwd_loc.count() > 0
            except Exception:
                need_login = False

        if need_login and (not interactive):
            if not username or not password:
                raise SystemExit("当前 profile 未登录且缺少账号密码：请设置环境变量 HGA_USERNAME / HGA_PASSWORD，或提供已登录的 HGA_PW_USER_DATA_DIR")

            # 等待输入框出现
            try:
                pwd_loc.first.wait_for(timeout=20_000)
            except Exception:
                pass

            try:
                user_loc.first.fill(username, timeout=10_000)
            except Exception:
                # 兜底：尝试聚焦第一个文本框
                try:
                    page.locator("input[type='text']").first.fill(username, timeout=10_000)
                except Exception:
                    pass
            try:
                pwd_loc.first.fill(password, timeout=10_000)
            except Exception:
                pass

        def is_chk_login(resp) -> bool:
            try:
                if "transform.php" not in (resp.url or ""):
                    return False
                req = resp.request
                if (req.method or "").upper() != "POST":
                    return False
                post = (req.post_data or "").lower()
                if "chk_login" in post:
                    return True
                # 兜底：命中用户名 + password 字段
                u = username.strip().lower()
                if u and u in post and ("pwd=" in post or "password=" in post or "pass=" in post):
                    return True
                return False
            except Exception:
                return False

        # 3) 点击登录（不做下注），并等待登录响应（便于判断是否被验证码拦截）
        login_resp_text = ""
        if need_login and (not interactive):
            btn = page.locator(
                "button:has-text('登录'), button:has-text('登入'), button:has-text('Login'), "
                "input[type='submit'], button[type='submit'], a:has-text('登录'), a:has-text('登入')"
            )
            try:
                with page.expect_response(is_chk_login, timeout=max(30_000, timeout_s * 1000)) as resp_info:
                    clicked = False
                    try:
                        if btn.first.is_visible():
                            btn.first.click(timeout=15_000)
                            clicked = True
                    except Exception:
                        clicked = False
                    if not clicked:
                        try:
                            pwd_loc.first.press("Enter")
                        except Exception:
                            pass
                resp = resp_info.value
                try:
                    login_resp_text = (resp.text() or "").strip()
                except Exception:
                    login_resp_text = ""
            except Exception:
                login_resp_text = ""

        if login_resp_text:
            head = login_resp_text[:400]
            head_low = head.lower()
            if (not head.startswith("<")) and ("checkneed" in head_low or "is_check" in head_low):
                log({"ts": int(time.time()), "ts_ms": _now_ms(), "event": "login_hint", "hint": "captcha_required_or_checkneed"})

        if interactive:
            print("[INFO] 交互模式：请在弹出的浏览器里手动登录并进入到赛事列表/更多盘口页面。", flush=True)
            print("[INFO] 登录完成后，回到这个终端窗口按回车继续开始抓包…", flush=True)
            try:
                input()
            except Exception:
                pass

        # 4) 等待接口请求一段时间 + 截图
        page.wait_for_timeout(6000 if not interactive else 1500)
        try:
            page.screenshot(path=str(out_png), full_page=True)
        except Exception:
            pass

        def dismiss_quick_login_prompt(frame) -> bool:
            # 常见弹窗：“你要设置一个简易密码以便快速登入吗？”
            try:
                if frame.get_by_text("简易密码", exact=False).count() <= 0:
                    return False
            except Exception:
                return False

            # 该站点弹窗有两组按钮：#no_btn/#yes_btn（可能 hidden/collapse）与 #C_no_btn/#C_yes_btn（可点击）
            preferred = ["#C_no_btn", "#C_yes_btn", "#no_btn", "#yes_btn"]
            for sel in preferred:
                try:
                    loc = frame.locator(sel)
                    if loc.count() > 0:
                        try:
                            loc.first.click(timeout=2_000)
                            frame.page.wait_for_timeout(600)
                            return True
                        except Exception:
                            # JS click 兜底（避免 Playwright 判定 not visible/outside viewport）
                            try:
                                frame.page.evaluate(
                                    "(sel) => { const el = document.querySelector(sel); if (el) el.click(); }",
                                    sel,
                                )
                                frame.page.wait_for_timeout(600)
                                return True
                            except Exception:
                                pass
                except Exception:
                    continue

            # 兜底：按文本点击“否”
            for sel in ["#C_alert_confirm >> text=否", "text=否", "button:has-text('否')", "text=不再提示"]:
                try:
                    loc = frame.locator(sel)
                    if loc.count() > 0 and loc.first.is_visible():
                        loc.first.click(timeout=3_000)
                        frame.page.wait_for_timeout(600)
                        return True
                except Exception:
                    continue
            # 最后尝试 ESC
            try:
                frame.page.keyboard.press("Escape")
                frame.page.wait_for_timeout(300)
                return True
            except Exception:
                return False

        dismissed = False
        # main page
        try:
            dismissed = dismiss_quick_login_prompt(page)
        except Exception:
            dismissed = False
        # iframes
        if not dismissed:
            for fr in page.frames:
                try:
                    if fr == page.main_frame:
                        continue
                    if dismiss_quick_login_prompt(fr):
                        dismissed = True
                        break
                except Exception:
                    continue
        if dismissed:
            log({"ts": int(time.time()), "ts_ms": _now_ms(), "event": "ui_hint", "hint": "dismissed_quick_login_prompt"})
            try:
                page.screenshot(path=str(out_png).replace(".png", "_after_dismiss.png"), full_page=True)
            except Exception:
                pass

        # 4.5) 自动“接口探针”：不依赖页面点击，直接用当前登录态触发关键接口，确保抓到主盘/附加盘相关数据。
        # 目的：解决某些情况下页面 UI 文本不可点击/被弹窗遮挡，导致脚本只登录但没触发 get_game_list/get_game_more。
        try:
            ver_js = ""
            uid_js = ""
            try:
                ver_js = str(page.evaluate("() => (top && top.ver) ? top.ver : (window.ver || '')") or "").strip()
            except Exception:
                ver_js = ""
            try:
                uid_js = str(page.evaluate("() => (top && top.uid) ? top.uid : (window.uid || '')") or "").strip()
            except Exception:
                uid_js = ""

            if not ver_js:
                ver_js = str(_extract_xml_text(login_resp_text, "ver") or "").strip()
            if not uid_js:
                uid_js = str(_extract_xml_text(login_resp_text, "uid") or "").strip()

            if ver_js and uid_js:
                def post_via_fetch(form: Dict[str, Any]) -> str:
                    return str(
                        page.evaluate(
                            """async ({baseUrl, ver, form}) => {
                              const url = `${String(baseUrl||'').replace(/\\/$/,'')}/transform.php?ver=${encodeURIComponent(ver)}`;
                              const body = new URLSearchParams(form).toString();
                              const res = await fetch(url, {
                                method: 'POST',
                                headers: { 'content-type': 'application/x-www-form-urlencoded' },
                                body,
                                credentials: 'include',
                              });
                              return await res.text();
                            }""",
                            {"baseUrl": base_url, "ver": ver_js, "form": form},
                        )
                        or ""
                    )

                # page_count + game_list
                post_via_fetch(
                    {
                        "p": "get_page_count",
                        "uid": uid_js,
                        "ver": ver_js,
                        "langx": "zh-cn",
                        "gtype": "ft",
                        "showtype": "live",
                        "filter": "",
                        "ltype": "4",
                        "team_id": "",
                        "specialClick": "",
                        "p3type": "",
                        "sorttype": "L",
                        "date": "",
                        "from": "game_list",
                    }
                )
                gl_xml = post_via_fetch(
                    {
                        "p": "get_game_list",
                        "uid": uid_js,
                        "ver": ver_js,
                        "langx": "zh-cn",
                        "p3type": "",
                        "date": "",
                        "gtype": "ft",
                        "showtype": "live",
                        "rtype": "rb",
                        "ltype": "4",
                        "filter": "",
                        "cupFantasy": "N",
                        "sorttype": "L",
                        "specialClick": "",
                        "isFantasy": "N",
                        "ts": str(int(time.time() * 1000)),
                        "chgSortTS": str(int(time.time() * 1000)),
                    }
                )
                ctx = _extract_game_ctx_from_game_list(gl_xml)
                gid0 = str(ctx.get("gid") or "").strip()
                lid0 = str(ctx.get("lid") or "").strip()
                ecid0 = str(ctx.get("ecid") or "").strip()
                if gid0 and lid0 and ecid0:
                    for flt in ["Main", "All"]:
                        post_via_fetch(
                            {
                                "p": "get_game_more",
                                "uid": uid_js,
                                "ver": ver_js,
                                "langx": "zh-cn",
                                "gtype": "ft",
                                "showtype": "live",
                                "ltype": "4",
                                "isRB": "Y",
                                "lid": lid0,
                                "specialClick": "",
                                "mode": "NORMAL",
                                "from": "game_more",
                                "filter": flt,
                                "ts": str(int(time.time() * 1000)),
                                "ecid": ecid0,
                            }
                        )
                    log({"ts": int(time.time()), "ts_ms": _now_ms(), "event": "probe_hint", "hint": "fired_game_list_and_game_more", "gid": gid0, "lid": lid0, "ecid": ecid0})
        except Exception:
            pass

        if interactive:
            print("[INFO] 已开始抓包：现在请在浏览器里打开你要复现的比赛（主盘/附加盘/更多盘口/热门等），等待 10~20 秒让接口跑完。", flush=True)
            if auto_stop_seconds > 0:
                print(f"[INFO] 将在 {auto_stop_seconds}s 后自动停止并保存。", flush=True)
                page.wait_for_timeout(auto_stop_seconds * 1000)
            else:
                print("[INFO] 完成后回到终端按回车结束并保存…", flush=True)
                try:
                    input()
                except Exception:
                    pass
            try:
                page.screenshot(path=str(out_png).replace(".png", "_manual_end.png"), full_page=True)
            except Exception:
                pass
            context.close()
            if not user_data_dir:
                browser.close()
            print(str(out_jsonl.resolve()))
            print(str(out_png.resolve()))
            return 0

        # 5) （自动模式）尝试进入“足球/今日/滚球”等列表页，触发 get_page_count/get_game_list
        nav_keywords = [
            "足球",
            "今日",
            "滚球",
            "热门",
            "让球&大小",
            "让球 & 大小",
            "让球大小",
            "让球",
            "大小",
        ]

        def try_nav_in_frame(frame) -> None:
            for kw in nav_keywords:
                try:
                    loc = frame.get_by_text(kw, exact=False)
                    if loc.count() > 0 and loc.first.is_visible():
                        loc.first.click(timeout=3_000)
                        frame.page.wait_for_timeout(1500)
                except Exception:
                    continue

        try_nav_in_frame(page)
        for fr in page.frames:
            try:
                if fr == page.main_frame:
                    continue
                try_nav_in_frame(fr)
            except Exception:
                continue

        # 6) 重点：尝试切换到“让球&大小”等板块（有机会触发 p3type != "" 的 get_game_list）
        market_tabs = ["让球&大小", "让球大小", "更多盘口", "附加盘", "所有盘口", "更多盤口", "所有盤口"]

        def try_click_text_anywhere(texts) -> None:
            for fr in [page] + [f for f in page.frames if f != page.main_frame]:
                for kw in texts:
                    try:
                        loc = fr.get_by_text(kw, exact=False)
                        if loc.count() > 0 and loc.first.is_visible():
                            loc.first.click(timeout=3_000)
                            page.wait_for_timeout(1200)
                    except Exception:
                        continue

        try_click_text_anywhere(market_tabs)

        # 7) 尝试点击某一场比赛的“更多(如 105>)”入口，进入详情页/展开更多盘
        def try_click_regex(pattern: str) -> None:
            rx = re.compile(pattern)
            for fr in [page] + [f for f in page.frames if f != page.main_frame]:
                try:
                    loc = fr.get_by_text(rx)
                    if loc.count() <= 0:
                        continue
                    # 只点一个，避免误操作
                    for i in range(min(10, loc.count())):
                        item = loc.nth(i)
                        try:
                            if item.is_visible():
                                item.click(timeout=3_000)
                                page.wait_for_timeout(1500)
                                return
                        except Exception:
                            continue
                except Exception:
                    continue

        # 常见“更多盘口数”展示：105> / 76> 等
        try_click_regex(r"\\b\\d{2,3}\\s*>\\s*$")

        # 8) 再次尝试触发“更多盘口/附加盘”按钮（部分站点需先进入比赛详情）
        try_click_text_anywhere(["更多盘口", "所有盘口", "附加盘", "更多", "盘口"])

        # 9) 如果已经出现过 p3type != '' 的 get_game_list，就打点日志，方便确认已命中
        if seen_nonempty_p3type_game_list:
            log(
                {
                    "ts": int(time.time()),
                    "ts_ms": _now_ms(),
                    "event": "capture_hint",
                    "hint": "seen_nonempty_p3type_get_game_list",
                    "p3types": sorted(seen_nonempty_p3type_game_list)[:20],
                }
            )

        page.wait_for_timeout(int(hold_seconds * 1000))

        try:
            page.screenshot(path=str(out_png).replace(".png", "_after_more.png"), full_page=True)
        except Exception:
            pass

        context.close()
        if not user_data_dir:
            browser.close()

    print(str(out_jsonl.resolve()))
    print(str(out_png.resolve()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
