"""复用独立浏览器会话登录、等待 2FA，仅查询会话和订单。"""
from __future__ import annotations
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import urlparse
from .orders import _text, build_poll_body, has_order_list

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

DEFAULT_BASE_URL = "https://www.ps38ag.com"
LOGIN_PATH = "/partner/login"
AUTH_PATH = "/partner/login/authentication"
BET_MONITOR_PATH = "/partner-reporting/ddd/total-bets/bet-monitor"
KEEPALIVE_PATH = "/partner-account/keepAlive"

class Ps3838SessionError(RuntimeError):
    """订单接口尚未确认完整登录会话，包括未完成 2FA 的情况。"""


class Ps3838LoginInterrupted(RuntimeError):
    """账号停用、删除或用户明确发起另一次登录。"""


class Ps3838LoginWindowClosed(RuntimeError):
    """用户已关闭登录窗口，需要明确发起登录才能再次打开。"""


class Ps3838Client:
    def __init__(self, account: Dict[str, Any], profile_dir: Path, *, debug: bool = False) -> None:
        self.account = account
        self.profile_dir = profile_dir
        self.debug = debug
        self.base_url = _text(account.get("base_url")) or DEFAULT_BASE_URL
        self.secret = _text(account.get("x_intellifend_secret"))
        self._pw = None
        self.ctx = None
        self.page = None
        self.last_auth_at = 0.0
        self.session_ready = False
        self.browser_hidden = False
        self.initial_payload = None

    def _capture_request(self, request: Any) -> None:
        try:
            # 平博接口分布在 /partner、/partner-account、/partner-reporting
            # 多个路径下，不能只匹配带斜杠的 /partner/。
            if urlparse(str(request.url)).netloc != urlparse(self.base_url).netloc:
                return
            headers = request.headers or {}
            secret = headers.get("x-intellifend-secret") or headers.get("X-Intellifend-Secret")
            if secret:
                self.secret = str(secret)
                self.account["x_intellifend_secret"] = self.secret
        except Exception:
            pass

    def _ensure_context(self) -> None:
        if self.ctx is not None:
            return
        if sync_playwright is None:
            raise RuntimeError("未安装 Playwright，无法建立平博登录会话")
        self._pw = sync_playwright().start()
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.ctx = self._pw.chromium.launch_persistent_context(
            str(self.profile_dir),
            headless=bool(self.account.get("headless", False)),
            viewport={"width": 1440, "height": 900},
            channel=self.account.get("browser_channel") or None,
        )
        self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
        self.page.on("request", self._capture_request)

    def close(self) -> None:
        try:
            if self.ctx is not None:
                self.ctx.close()
        finally:
            self.ctx = None
            self.page = None
            self.session_ready = False
            self.browser_hidden = False
            if self._pw is not None:
                try:
                    self._pw.stop()
                except Exception:
                    pass
                self._pw = None

    def _fill_login(self) -> None:
        page = self.page
        assert page is not None
        user = _text(self.account.get("username"))
        password = str(self.account.get("password") or "")
        if not user or not password:
            return  # 未配置凭据时由用户在原生登录窗口手动填写。
        selectors_user = "input[name='username'], input[name='userName'], input[id*='user'], input[id*='account'], input[type='text']"
        selectors_pwd = "input[name='password'], input[name='pwd'], input[id*='password'], input[id*='pwd'], input[type='password']"
        password_input = page.locator(selectors_pwd).first
        password_input.wait_for(state="visible", timeout=10_000)
        page.locator(selectors_user).first.fill(user, timeout=10_000)
        password_input.fill(password, timeout=10_000)
        button = page.locator(
            "button:has-text('Login'), button:has-text('登录'), button:has-text('登入'), "
            "button[type='submit'], input[type='submit']"
        ).first
        button.click(timeout=10_000)

    def login(
        self, *, force: bool = False,
        should_continue: Optional[Callable[[], bool]] = None,
        on_wait: Optional[Callable[[str], None]] = None,
    ) -> None:
        if should_continue is not None and not should_continue():
            raise Ps3838LoginInterrupted()
        self._ensure_context()
        page = self.page
        assert page is not None
        if force:
            try:
                self.ctx.clear_cookies()
            except Exception:
                pass
            self.secret = ""
            self.account["x_intellifend_secret"] = ""
        page.goto(self.base_url.rstrip("/") + LOGIN_PATH, wait_until="domcontentloaded", timeout=60_000)
        if should_continue is not None and not should_continue():
            raise Ps3838LoginInterrupted()
        try:
            # 账号密码每次明确登录请求只提交一次，绝不填写/提交二次验证码。
            if urlparse(str(page.url)).path.rstrip("/").lower().endswith("/login"):
                self._fill_login()
        except Exception as exc:
            if self.debug:
                print("自动填充未完成，请在浏览器里手动完成登录。", flush=True)
        self.wait_for_login(should_continue=should_continue, on_wait=on_wait)

    def _login_pending_message(self) -> str:
        page = self.page
        assert page is not None
        try:
            text = page.locator("body").inner_text(timeout=1_000).lower()
            if "account temporarily disabled" in text or "your account has been locked" in text:
                return "账号暂时锁定，请等待网站解除锁定；不会自动重新提交登录"
        except Exception:
            pass
        return "等待你在浏览器完成登录/2FA，窗口不会自动关闭"

    def _can_verify_session(self) -> bool:
        page = self.page
        if page is None:
            return False
        current = urlparse(str(page.url))
        expected = urlparse(self.base_url)
        path = current.path.lower()
        # Cookie 可以在密码验证或 2FA 之前出现，不能单独作为登录成功依据。
        if (current.scheme, current.netloc) != (expected.scheme, expected.netloc):
            return False
        if "/login" in path or "/authentication" in path:
            return False
        inputs = page.locator("input[type='password'], input[autocomplete='one-time-code']")
        return not any(inputs.nth(i).is_visible() for i in range(inputs.count()))

    def wait_for_login(
        self, *, should_continue: Optional[Callable[[], bool]] = None,
        on_wait: Optional[Callable[[str], None]] = None,
    ) -> None:
        """持续保留原窗口，直到受保护订单接口验证成功或用户主动停止。"""
        self.session_ready = False
        self.restore_window()
        next_check = 0.0
        last_message = ""
        while True:
            if should_continue is not None and not should_continue():
                raise Ps3838LoginInterrupted()
            page = self.page
            if page is None or page.is_closed():
                raise Ps3838LoginWindowClosed("登录窗口已关闭；已暂停自动登录，请重新运行 login 或 watch 命令继续")
            message = self._login_pending_message()
            if on_wait is not None and message != last_message:
                on_wait(message)
                last_message = message
            if self.secret:
                self.account["x_intellifend_secret"] = self.secret
            try:
                if "账号暂时锁定" not in message and self._can_verify_session() and time.monotonic() >= next_check:
                    next_check = time.monotonic() + 5.0
                    payload = self._evaluate_fetch(BET_MONITOR_PATH, build_poll_body())
                    if not has_order_list(payload):
                        raise Ps3838SessionError("订单接口尚未返回有效订单列表")
                    self.initial_payload = payload
                    self.last_auth_at = time.time()
                    self.session_ready = True
                    return
            except Exception:
                # 包括 403、页面跳转和网络错误；不关闭窗口，也不重填账号密码。
                pass
            # Playwright 的等待保持浏览器事件可处理，不设置自动关窗的登录超时。
            page.wait_for_timeout(1_000)

    def _evaluate_fetch(self, path: str, body: Optional[Dict[str, Any]] = None) -> Any:
        if path not in {KEEPALIVE_PATH, BET_MONITOR_PATH}:
            raise ValueError("仅允许读取会话和订单接口")
        page = self.page
        assert page is not None
        url = self.base_url.rstrip("/") + path
        headers = {"Accept": "application/json, text/plain, */*"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if self.secret:
            headers["x-intellifend-secret"] = self.secret
        script = """
        async ({url, body, headers}) => {
          const controller = new AbortController();
          const timer = setTimeout(() => controller.abort(), 30000);
          try {
          const response = await fetch(url, {
            method: 'POST',
            credentials: 'include',
            headers,
            signal: controller.signal,
            body: body == null ? null : JSON.stringify(body)
          });
          const text = await response.text();
          let data = null;
          let isJson = false;
          try { data = JSON.parse(text); isJson = true; } catch (_) { data = {text}; }
          return {status: response.status, data, isJson};
          } finally { clearTimeout(timer); }
        }
        """
        result = page.evaluate(script, {"url": url, "body": body, "headers": headers})
        if not isinstance(result, dict):
            raise RuntimeError("平博接口返回格式异常")
        status = int(result.get("status") or 0)
        if status in (401, 403):
            raise Ps3838SessionError(f"平博会话尚未通过验证（HTTP {status}）")
        if status < 200 or status >= 300:
            raise RuntimeError(f"平博接口失败（HTTP {status}）")
        data = result.get("data")
        if result.get("isJson") is not True or not isinstance(data, (dict, list)):
            raise Ps3838SessionError("平博接口未返回 JSON（可能仍在登录/2FA 页面）")
        return data

    def wait(self, seconds: float) -> None:
        """让 Playwright 持续处理请求事件，包括会话标头轮换。"""
        if self.page is None or self.page.is_closed():
            raise Ps3838LoginWindowClosed("登录窗口已关闭，请重新运行 watch")
        self.page.wait_for_timeout(max(0, seconds) * 1000)

    def poll(self) -> Tuple[Any, Any]:
        if self.page is None or self.page.is_closed():
            raise Ps3838LoginWindowClosed("登录窗口已关闭；已暂停自动登录，请重新运行 login 或 watch 命令继续")
        if not self.session_ready:
            raise Ps3838SessionError("尚未完成登录/2FA，不能开始扫描订单")
        keepalive = self._evaluate_fetch(KEEPALIVE_PATH, None)
        payload = self._evaluate_fetch(BET_MONITOR_PATH, build_poll_body())
        if not has_order_list(payload):
            raise Ps3838SessionError("订单接口未返回有效订单列表，请在原窗口确认登录状态")
        return keepalive, payload

    def minimize_window(self) -> bool:
        """认证并扫描成功后收起窗口，保留原会话和同源 fetch 所需的页面。"""
        if self.browser_hidden:
            return True
        if not self.session_ready or self.ctx is None or self.page is None:
            return False
        cdp = None
        try:
            cdp = self.ctx.new_cdp_session(self.page)
            window = cdp.send("Browser.getWindowForTarget")
            cdp.send("Browser.setWindowBounds", {"windowId": window["windowId"], "bounds": {"windowState": "minimized"}})
            self.browser_hidden = True
            return True
        except Exception:
            # 窗口管理失败不影响已登录会话，更不能触发重登。
            return False
        finally:
            if cdp is not None:
                try:
                    cdp.detach()
                except Exception:
                    pass

    def restore_window(self) -> None:
        """认证失效时把已收起的窗口恢复，供用户继续完成 2FA。"""
        if not self.browser_hidden or self.ctx is None or self.page is None:
            return
        cdp = None
        try:
            cdp = self.ctx.new_cdp_session(self.page)
            window = cdp.send("Browser.getWindowForTarget")
            cdp.send("Browser.setWindowBounds", {"windowId": window["windowId"], "bounds": {"windowState": "normal"}})
            self.browser_hidden = False
        except Exception:
            pass
        finally:
            if cdp is not None:
                try:
                    cdp.detach()
                except Exception:
                    pass

