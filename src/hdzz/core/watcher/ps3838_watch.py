"""只读的平博 / PS3838 订单监听器。

这个 worker 与 AG 跟单链路完全隔离：订单只写入 data/ps3838/orders，
不会写入 app/orders，也不会调用任何投注、取消或修改接口。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from hdzz.core.watcher.ag_watcher import (
    _load_tg_ui_theme,
    format_backend_order_telegram_message,
    tg_send,
)

try:
    from playwright.sync_api import sync_playwright  # type: ignore
except Exception:  # pragma: no cover - runtime dependency is optional in unit tests
    sync_playwright = None  # type: ignore


DEFAULT_BASE_URL = "https://www.ps38ag.com"
DEFAULT_LOGIN_URL = "https://www.ps38ag.com/partner/zh-cn/login"
LOGIN_PATH = "/partner/login"
AUTH_PATH = "/partner/login/authentication"
BET_MONITOR_PATH = "/partner-reporting/ddd/total-bets/bet-monitor"
KEEPALIVE_PATH = "/partner-account/keepAlive"
PARTNER_PROFILE_PATH = "/partner-account/ddd/user-info/login-user/profile"
DEFAULT_INTERVAL_SECONDS = 15.0
DEFAULT_TIMEOUT_SECONDS = 30.0
TELEGRAM_TEMPLATE_VERSION = "2026-10-07-custom-emoji-zh"
TELEGRAM_DELIVERY_VERSION = "2026-10-08-durable-retry"


class Ps3838SessionError(RuntimeError):
    """订单接口尚未确认完整登录会话，包括未完成 2FA 的情况。"""


class Ps3838LoginInterrupted(RuntimeError):
    """账号停用、删除或用户明确发起另一次登录。"""


class Ps3838LoginWindowClosed(RuntimeError):
    """用户已关闭登录窗口，需要明确发起登录才能再次打开。"""


def safe_name(value: Any) -> str:
    original = str(value or "").strip()
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", original).strip("._") or "ps3838"
    if re.fullmatch(r"[A-Za-z0-9_.-]+", original):
        return slug
    digest = hashlib.sha256(original.encode("utf-8")).hexdigest()[:10]
    return f"{slug[:80]}-{digest}"


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def read_json(path: Path, default: Any) -> Any:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        return value
    except Exception:
        return default


def append_jsonl(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _first(mapping: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return None


def _number(value: Any) -> Any:
    if value in (None, ""):
        return value
    try:
        number = float(value)
        return int(number) if number.is_integer() else number
    except Exception:
        return value


def _choice_text(choice: Dict[str, Any]) -> str:
    selection = _text(_first(choice, "selection", "selectionName", "betSelection"))
    handicap = _text(_first(choice, "handicap", "line", "points"))
    if selection and handicap:
        return f"{selection} {handicap}"
    return selection or handicap or _text(_first(choice, "eventName", "description"))


def _ps_display_fields(choice: Dict[str, Any], sides: str) -> Tuple[str, str, str]:
    """把 PS3838 的代码字段翻译成与皇冠推送相同的中文展示字段。"""
    code = _text(_first(choice, "btName", "marketType", "market")).upper()
    market = {"HDP": "让球", "OU": "进球大小", "TOTAL": "进球大小", "ML": "独赢", "1X2": "独赢"}.get(code, code)
    if not market:
        market = "玩法未提供"
    inplay = choice.get("inplay")
    kind = "滚球" if inplay is True else "早盘" if inplay is False else "状态未提供"
    period = _text(choice.get("periodName"))
    if not period or period in {"比赛", "全场", "常规赛"}:
        period = "全场"
    elif "上半场" in period:
        period = "上半场"
    elif "下半场" in period:
        period = "下半场"
    bet_type = f"({kind}) {market}" + (f"-{period}" if period != "全场" else "")
    side = _text(_first(choice, "selectionName", "selection", "betSelection"))
    selection_map = {"HOME": 0, "AWAY": 1}
    if side.upper() in selection_map:
        parts = re.split(r"\s*-vs-\s*|\s+vs\s+", sides, maxsplit=1, flags=re.IGNORECASE)
        side = parts[selection_map[side.upper()]] if len(parts) > selection_map[side.upper()] else side
    elif side.upper() == "OVER":
        side = "大"
    elif side.upper() == "UNDER":
        side = "小"
    handicap_value = _first(choice, "handicap", "line", "points")
    handicap = str(handicap_value).strip() if handicap_value is not None else ""
    # NORMAL 是下注单类型，不是玩法；推送模板应使用 HDP/OU 的中文名称。
    return bet_type, side, handicap


def _placed_parts(value: Any) -> Tuple[str, str]:
    text = _text(value)
    if not text:
        return "", ""
    text = text.replace("T", " ").replace("Z", "").strip()
    if " " in text:
        date, clock = text.split(" ", 1)
        return date, clock[:8]
    return text, ""


def normalize_order(raw: Dict[str, Any], target_name: str) -> Dict[str, Any]:
    """将 PS3838 原始订单映射成项目现有的只读展示格式，并保留完整 raw。"""
    choices = raw.get("choices") if isinstance(raw.get("choices"), list) else []
    choice = choices[0] if choices and isinstance(choices[0], dict) else {}
    order_id = _text(_first(raw, "playerWagerId", "betId", "wagerId", "id", "ticketNo", "orderId"))
    if not order_id:
        order_id = hashlib.sha1(json.dumps(raw, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:20]
    placed = _first(raw, "placed", "createdAt", "betTime", "wagerTime")
    date, clock = _placed_parts(placed)
    event_name = _text(_first(choice, "eventName", "description"))
    home = _text(_first(choice, "home", "homeTeam"))
    away = _text(_first(choice, "away", "awayTeam"))
    sides = event_name or " vs ".join(part for part in (home, away) if part)
    bet_type = _text(_first(raw, "wagerType", "betType")) or _text(_first(choice, "btName", "marketType"))
    line = _choice_text(choice)
    odds = _first(choice, "finalOdds", "odds")
    return {
        "target": target_name,
        "orderId": order_id,
        "playerWagerId": order_id,
        "account": _text(_first(raw, "player", "playerName", "nickName", "username", "userName", "customer")),
        "memberId": _first(raw, "memberId", "playerId"),
        "agent": _text(_first(raw, "agent", "agentName", "agentId", "parent", "upline")),
        "stake": _number(_first(raw, "stake", "amount", "riskAmount", "toRisk")),
        "toRisk": _number(_first(raw, "toRisk", "riskAmount", "stake", "amount")),
        "toWin": _number(raw.get("toWin")),
        "risk": _number(_first(raw, "toRisk", "riskAmount", "stake", "amount")),
        "win": _number(_first(raw, "win", "winAmount", "payout", "winLoss")),
        "currency": _text(raw.get("currency")),
        "sport": _text(_first(choice, "sportName", "sport")),
        "league": _text(_first(choice, "leagueName", "league", "competition")),
        "sides": sides,
        "betType": bet_type,
        "line": line,
        "odds": _number(odds),
        "score": _first(choice, "score") or _first(raw, "score"),
        "status": _text(_first(raw, "status", "state", "wagerStatus")),
        "result": _first(choice, "resultEvent", "result", "settlement") or _first(raw, "result", "settlement"),
        "date": date,
        "time": clock,
        "placed": placed,
        "updatedAt": _first(raw, "updatedAt", "updateTime"),
        "choices": choices,
        "raw": raw,
    }


def extract_orders(payload: Any, target_name: str) -> List[Dict[str, Any]]:
    """从接口可能存在的包装层中提取订单列表。

    平博前端版本之间可能使用 data、result、rows 或 bets 包装数组；只接受
    含订单标识的对象，避免把统计、分页或其他列表误当成订单。
    """
    rows: List[Dict[str, Any]] = []
    seen: set[int] = set()

    def walk(value: Any, depth: int = 0) -> None:
        if depth > 6 or id(value) in seen:
            return
        if isinstance(value, (dict, list)):
            seen.add(id(value))
        if isinstance(value, list):
            candidates = [item for item in value if isinstance(item, dict)]
            if candidates and any(_first(item, "playerWagerId", "betId", "wagerId", "ticketNo", "orderId", "id") not in (None, "") for item in candidates):
                rows.extend(candidates)
                return
            for item in value:
                walk(item, depth + 1)
            return
        if isinstance(value, dict):
            for key, child in value.items():
                if str(key).lower() in {"data", "result", "rows", "bets", "wagers", "items", "content", "body"}:
                    walk(child, depth + 1)
            if _first(value, "playerWagerId", "betId", "wagerId", "ticketNo", "orderId") not in (None, ""):
                rows.append(value)

    walk(payload)
    unique: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        order = normalize_order(row, target_name)
        order_id = str(order.get("orderId") or "")
        if order_id:
            unique[order_id] = order
    return list(unique.values())


def order_fingerprint(order: Dict[str, Any]) -> str:
    comparable = {k: v for k, v in order.items() if k not in {"raw", "ts", "event"}}
    return hashlib.sha256(json.dumps(comparable, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def order_changes(previous: Dict[str, Any], current: Dict[str, Any]) -> Dict[str, Tuple[Any, Any]]:
    fields = (
        "status", "toRisk", "toWin", "stake", "risk", "win", "score", "result", "updatedAt",
        "choices", "odds", "line", "currency", "account", "memberId", "agent", "placed",
    )
    return {
        key: (previous.get(key), current.get(key))
        for key in fields
        if previous.get(key) != current.get(key)
    }


def build_poll_body() -> Dict[str, Any]:
    return {
        "downlineIds": None,
        "isInPlay": -1,
        "userIds": None,
        "groupType": "OWN_DOWNLINE",
        "excludeLeagueIds": None,
        "excludeSportIds": None,
        "productCodes": ["SB"],
        "ownerUserId": None,
        "minRisk": 0,
        "historyTime": 60,
    }


def has_order_list(payload: Any, depth: int = 0) -> bool:
    """空订单列表也是有效会话；普通 JSON 错误对象不能证明登录成功。"""
    if depth > 6:
        return False
    if isinstance(payload, list):
        return all(
            isinstance(row, dict)
            and _first(row, "playerWagerId", "betId", "wagerId", "ticketNo", "orderId", "id") not in (None, "")
            for row in payload
        )
    if not isinstance(payload, dict) or payload.get("error") or payload.get("errors") or payload.get("success") is False:
        return False
    return any(
        has_order_list(child, depth + 1)
        for key, child in payload.items()
        if str(key).lower() in {"data", "result", "rows", "bets", "wagers", "items", "content", "body"}
    )


class Ps3838Client:
    def __init__(self, account: Dict[str, Any], profile_dir: Path, *, debug: bool = False) -> None:
        self.account = account
        self.profile_dir = profile_dir
        self.debug = debug
        raw_base = _text(account.get("base_url")) or DEFAULT_BASE_URL
        parsed_base = urlparse(raw_base)
        self.base_url = (
            f"{parsed_base.scheme}://{parsed_base.netloc}"
            if parsed_base.scheme and parsed_base.netloc else DEFAULT_BASE_URL
        )
        account["base_url"] = self.base_url
        self.login_url = _text(account.get("login_url")) or DEFAULT_LOGIN_URL
        self.storage_state_path = self.profile_dir / "storage_state.json"
        self.secret = _text(account.get("x_intellifend_secret"))
        self._pw = None
        self.ctx = None
        self.page = None
        self.last_auth_at = 0.0
        self.session_ready = False
        self.browser_hidden = False

    def _capture_request(self, request: Any) -> None:
        try:
            # 平博接口分布在 /partner、/partner-account、/partner-reporting
            # 多个路径下，不能只匹配带斜杠的 /partner/。
            if "ps38ag.com" not in str(request.url).lower():
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
        )
        self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
        self.page.on("request", self._capture_request)
        self._restore_session_state()

    def _restore_session_state(self) -> None:
        """Restore cookies from a prior login; persistent profiles alone do not
        keep session cookies across a browser restart on this site."""
        if not self.storage_state_path.exists() or self.ctx is None:
            return
        try:
            payload = json.loads(self.storage_state_path.read_text(encoding="utf-8"))
        except Exception:
            return
        cookies = payload.get("cookies") if isinstance(payload, dict) else None
        if isinstance(cookies, list) and cookies:
            try:
                self.ctx.add_cookies(cookies)
            except Exception:
                pass
        origins = payload.get("origins") if isinstance(payload, dict) else None
        if not isinstance(origins, list):
            return
        for entry in origins:
            if not isinstance(entry, dict):
                continue
            origin = _text(entry.get("origin"))
            items = entry.get("localStorage")
            if not origin.startswith("http") or not isinstance(items, list) or not items:
                continue
            page = None
            try:
                page = self.ctx.new_page()
                page.goto(origin, wait_until="domcontentloaded", timeout=15_000)
                page.evaluate(
                    "items => { for (const item of items) localStorage.setItem(item.name, item.value); }",
                    [item for item in items if isinstance(item, dict) and "name" in item],
                )
                page.close()
            except Exception:
                try:
                    page.close()
                except Exception:
                    pass

    def save_session_state(self) -> None:
        if self.ctx is None:
            return
        try:
            self.storage_state_path.parent.mkdir(parents=True, exist_ok=True)
            self.ctx.storage_state(path=str(self.storage_state_path))
        except Exception:
            pass

    def close(self) -> None:
        try:
            self.save_session_state()
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
        selectors_user = (
            "input[placeholder*='用户名'], input[placeholder*='账号'], "
            "input[name='username'], input[name='userName'], input[name='userid'], "
            "input[id*='user'], input[id*='account'], input[type='text']"
        )
        selectors_pwd = (
            "input[placeholder*='密码'], input[name='password'], input[name='pwd'], "
            "input[id*='password'], input[id*='pwd'], input[type='password']"
        )
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
        page.goto(self.login_url, wait_until="domcontentloaded", timeout=60_000)
        if should_continue is not None and not should_continue():
            raise Ps3838LoginInterrupted()
        try:
            # 账号密码每次明确登录请求只提交一次，绝不填写/提交二次验证码。
            if urlparse(str(page.url)).path.rstrip("/").lower() == LOGIN_PATH:
                self._fill_login()
        except Exception as exc:
            if self.debug:
                print(f"[{now_iso()}] [{self.account.get('name')}] 自动填充失败，请在浏览器中完成登录: {exc}", flush=True)
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

    def login_debug(self) -> Dict[str, Any]:
        page = self.page
        if page is None:
            return {"url": "", "title": "", "text": "页面未建立"}
        try:
            text = page.locator("body").inner_text(timeout=1_000)
        except Exception:
            text = ""
        try:
            title = page.title()
        except Exception:
            title = ""
        return {
            "url": str(page.url),
            "title": str(title)[:200],
            "text": str(text)[:500],
        }

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
        next_status = 0.0
        last_message = ""
        while True:
            if should_continue is not None and not should_continue():
                raise Ps3838LoginInterrupted()
            page = self.page
            if page is None or page.is_closed():
                raise Ps3838LoginWindowClosed("登录窗口已关闭；已暂停自动登录，请点击重新登录继续")
            message = self._login_pending_message()
            if on_wait is not None and (message != last_message or time.monotonic() >= next_status):
                on_wait(message)
                last_message = message
                next_status = time.monotonic() + 5.0
            if self.secret:
                self.account["x_intellifend_secret"] = self.secret
            try:
                if "账号暂时锁定" not in message and self._can_verify_session() and time.monotonic() >= next_check:
                    next_check = time.monotonic() + 5.0
                    if self.account.get("partner_sync_enabled") and self.partner_profile_ready():
                        self.last_auth_at = time.time()
                        self.session_ready = True
                        self.save_session_state()
                        return
                    payload = self._evaluate_fetch(BET_MONITOR_PATH, build_poll_body())
                    if not has_order_list(payload):
                        raise Ps3838SessionError("订单接口尚未返回有效订单列表")
                    self.last_auth_at = time.time()
                    self.session_ready = True
                    self.save_session_state()
                    return
            except Exception:
                # 包括 403、页面跳转和网络错误；不关闭窗口，也不重填账号密码。
                pass
            # Playwright 的等待保持浏览器事件可处理，不设置自动关窗的登录超时。
            page.wait_for_timeout(1_000)

    def partner_profile_ready(self) -> bool:
        """A logged-in partner parent can be verified via the profile endpoint."""
        try:
            response = self.fetch_partner_json(PARTNER_PROFILE_PATH)
            if int(response.get("httpStatus") or 0) != 200:
                return False
            payload = response.get("json") or {}
            profile = payload.get("profile") if isinstance(payload, dict) else None
            user_id = profile.get("userId") if isinstance(profile, dict) else None
            return isinstance(user_id, int) and not isinstance(user_id, bool) and user_id > 0
        except Exception:
            return False

    def fetch_partner_json(self, path: str,
                           body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Read a partner-backend JSON endpoint through the logged-in page."""
        page = self.page
        if page is None:
            raise Ps3838SessionError("平博会话尚未建立")
        url = self.base_url.rstrip("/") + path
        script = """
        async ({url, body}) => {
          const options = {
            credentials: 'include',
            headers: {'Accept': 'application/json, text/plain, */*'},
          };
          if (body !== null) {
            options.method = 'POST';
            options.headers['content-type'] = 'application/json';
            options.body = JSON.stringify(body);
          }
          const controller = new AbortController();
          const timer = setTimeout(() => controller.abort(), 20000);
          let res;
          try {
            res = await fetch(url, {...options, signal: controller.signal});
          } finally {
            clearTimeout(timer);
          }
          const text = await res.text();
          let json = null;
          try { json = JSON.parse(text); } catch (error) { json = null; }
          return {httpStatus: res.status, json};
        }
        """
        result = page.evaluate(script, {"url": url, "body": body})
        if not isinstance(result, dict):
            raise Ps3838SessionError("平博后台接口返回格式无效")
        return result

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

    def poll(self) -> Tuple[Any, Any]:
        if self.page is None or self.page.is_closed():
            raise Ps3838LoginWindowClosed("登录窗口已关闭；已暂停自动登录，请点击重新登录继续")
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


def _telegram_config(raw_cfg: Dict[str, Any], account: Dict[str, Any]) -> Tuple[str, str, str]:
    if bool(raw_cfg.get("telegram_backend_order_push_enabled")):
        token = _text(raw_cfg.get("telegram_backend_order_bot_token"))
        chat = _text(account.get("telegram_chat_id")) or _text(raw_cfg.get("telegram_backend_order_chat_id"))
        proxy = _text(raw_cfg.get("telegram_backend_order_proxy"))
        if token and chat:
            return token, chat, proxy
    if bool(raw_cfg.get("telegram_order_forward_enabled", True)):
        return _text(raw_cfg.get("telegram_bot_token")), _text(account.get("telegram_chat_id")) or _text(raw_cfg.get("telegram_chat_id")), _text(raw_cfg.get("telegram_proxy"))
    return "", "", ""


def _telegram_error_details(exc: Exception, token: str) -> Dict[str, Any]:
    """保留 Telegram 的拒绝原因，同时避免 requests 的 URL 泄露 Bot Token。"""
    response = getattr(exc, "response", None)
    data: Dict[str, Any] = {}
    if response is not None:
        try:
            body = response.json()
            if isinstance(body, dict):
                data = body
        except Exception:
            pass
    description = str(data.get("description") or str(exc))
    if token:
        description = description.replace(token, "[REDACTED]")
    description = re.sub(r"bot\d+:[A-Za-z0-9_-]+", "bot[REDACTED]", description)
    parameters = data.get("parameters") if isinstance(data.get("parameters"), dict) else {}
    try:
        retry_after = max(0.0, float(parameters.get("retry_after") or 0))
    except (TypeError, ValueError):
        retry_after = 0.0
    return {
        "ok": False,
        "error": description[:500],
        "error_code": data.get("error_code") or getattr(response, "status_code", None),
        "retry_after": retry_after,
        "migrate_to_chat_id": parameters.get("migrate_to_chat_id"),
    }


def _send_order_telegram(raw_cfg: Dict[str, Any], account: Dict[str, Any], order: Dict[str, Any], event: str, changes: Dict[str, Tuple[Any, Any]]) -> Dict[str, Any]:
    token, chat, proxy = _telegram_config(raw_cfg, account)
    if not token or not chat:
        return {"ok": False, "skipped": True}
    # 平博与皇冠共用同一套机器人和 TG 自定义表情主题；后台订单专用通道
    # 也必须传入主题，否则会退回普通 🤣/📍 等 Unicode 表情。
    display_order = dict(order)
    choices = order.get("choices") if isinstance(order.get("choices"), list) else []
    choice = choices[0] if len(choices) == 1 and isinstance(choices[0], dict) else {}
    if choice:
        bet_type, selection, handicap = _ps_display_fields(choice, _text(order.get("sides")))
        display_order["betType"] = bet_type
        display_order["line"] = " ".join(part for part in (selection, handicap) if part)
        display_order.pop("selection", None)
    message = format_backend_order_telegram_message(
        display_order, ui_theme=_load_tg_ui_theme(raw_cfg),
        source_name=f"平博 · {_text(account.get('username')) or _text(account.get('name'))}",
    )
    if event == "update":
        changed = ", ".join(changes.keys()) or "订单字段"
        message = f"<b>平博订单更新</b>\n变化字段：{changed}\n\n{message}"
    try:
        message_id = tg_send(token, chat, message, timeout_s=10, proxy=proxy)
        if not isinstance(message_id, int) or message_id <= 0:
            raise RuntimeError("Telegram 未返回有效 message_id，无法确认送达")
        return {"ok": True, "message_id": message_id}
    except Exception as exc:
        result = _telegram_error_details(exc, token)
        print(
            f"[{now_iso()}] [{account.get('name')}] Telegram 推送失败 "
            f"单号={order.get('orderId')} HTTP={result['error_code']}: {result['error']}",
            file=sys.stderr, flush=True,
        )
        return result


def _queue_order_telegram(state: Dict[str, Any], row: Dict[str, Any]) -> None:
    # 与采集快照一同保存；推送失败不再因为订单已采集而丢失。
    payload = {key: row.get(key) for key in ("orderId", "ts", "event", "changes")}
    delivery_id = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:24]
    pending = state.setdefault("telegram_outbox", [])
    if any(item.get("delivery_id") == delivery_id for item in pending):
        return
    pending.append({
        "delivery_id": delivery_id,
        "order": {k: v for k, v in row.items() if k not in {"raw", "event", "changes", "ts"}},
        "event": row.get("event") or "new",
        "changes": row.get("changes") or {},
        "attempts": 0,
        "next_retry_at": 0,
    })
    state["telegram_pending_count"] = len(pending)


def _flush_telegram_outbox(
    raw_cfg: Dict[str, Any], account: Dict[str, Any], state: Dict[str, Any],
    save_state: Callable[[], None], delivery_path: Path,
) -> None:
    token, chat, _ = _telegram_config(raw_cfg, account)
    if not token or not chat:
        return
    if time.time() < float(state.get("telegram_retry_not_before") or 0):
        return
    pending = state.setdefault("telegram_outbox", [])
    blocked_orders = set()
    attempted = 0
    for entry in list(pending):
        oid = str(entry["order"].get("orderId") or "")
        if oid in blocked_orders or time.time() < float(entry.get("next_retry_at") or 0):
            blocked_orders.add(oid)
            continue
        if attempted >= 5:
            break
        attempted += 1
        result = _send_order_telegram(raw_cfg, account, entry["order"], entry["event"], entry["changes"])
        if result.get("skipped"):
            break
        entry["attempts"] = int(entry.get("attempts") or 0) + 1
        receipt = {
            "delivery_id": entry["delivery_id"], "orderId": oid,
            "event": entry["event"], "attempt": entry["attempts"],
            "ts": time.time(), **result,
        }
        if result.get("ok"):
            pending.remove(entry)
            state["telegram_last_success_at"] = receipt["ts"]
            state["telegram_last_message_id"] = result["message_id"]
        else:
            delay = min(15 * (2 ** min(entry["attempts"] - 1, 5)), 300)
            entry["next_retry_at"] = time.time() + max(delay, float(result.get("retry_after") or 0))
            entry["last_error"] = result.get("error")
            state["telegram_last_error"] = result.get("error")
            blocked_orders.add(oid)
        state["telegram_pending_count"] = len(pending)
        if not pending:
            state["telegram_last_error"] = ""
        save_state()
        append_jsonl(delivery_path, receipt)
        # 429 对同一个群限流；本轮剩余消息也遵守 retry_after。
        if result.get("error_code") == 429:
            state["telegram_retry_not_before"] = entry["next_retry_at"]
            for remaining in pending:
                remaining["next_retry_at"] = max(float(remaining.get("next_retry_at") or 0), entry["next_retry_at"])
            save_state()
            break


def process_account(account: Dict[str, Any], raw_cfg: Dict[str, Any], data_dir: Path, *, config_path: Optional[Path] = None, debug: bool = False) -> None:
    # 每个线程持有自己的配置副本，避免刷新配置时清空其他账号的配置。
    account = dict(account)
    raw_cfg = dict(raw_cfg)
    name = _text(account.get("name"))
    state_dir = data_dir / "ps3838" / "state"
    order_dir = data_dir / "ps3838" / "orders"
    state_path = state_dir / f"{safe_name(name)}.json"
    order_path = order_dir / f"{safe_name(name)}.jsonl"
    state = read_json(state_path, {})
    if not isinstance(state, dict):
        state = {}
    state.setdefault("initialized", False)
    state.setdefault("orders", {})
    state.setdefault("last_relogin_requested_at", 0.0)
    state["worker_pid"] = os.getpid()
    state["worker_started_at"] = time.time()
    state["telegram_template_version"] = TELEGRAM_TEMPLATE_VERSION
    state["telegram_delivery_version"] = TELEGRAM_DELIVERY_VERSION
    state.setdefault("telegram_outbox", [])
    delivery_path = data_dir / "ps3838" / "telegram_delivery" / f"{safe_name(name)}.jsonl"
    force_login_request = 0.0
    client = Ps3838Client(account, data_dir / "ps3838" / "profiles" / safe_name(name), debug=debug)
    partner_sync = None
    partner_sync_error = ""

    def close_partner_sync() -> None:
        nonlocal partner_sync
        if partner_sync is not None:
            try:
                partner_sync.close()
            except Exception:
                pass
            partner_sync = None

    def ensure_partner_sync():
        nonlocal partner_sync, partner_sync_error
        if not bool(account.get("partner_sync_enabled", False)):
            close_partner_sync()
            try:
                from hdzz.core.watcher.ps3838_partner import PS3838_DIR
                (PS3838_DIR / "var" / "run" / "hd_partner_sync.enabled").unlink(missing_ok=True)
            except (OSError, ImportError):
                pass
            return None
        if partner_sync is not None:
            return partner_sync
        try:
            from hdzz.core.watcher.ps3838_partner import PS3838_DIR, PartnerSync, default_db_path
            partner_sync = PartnerSync(client, account, default_db_path())
            try:
                flag = PS3838_DIR / "var" / "run" / "hd_partner_sync.enabled"
                flag.parent.mkdir(parents=True, exist_ok=True)
                flag.touch()
            except OSError:
                pass
            partner_sync_error = ""
        except Exception as exc:
            partner_sync_error = str(exc)[:500]
            partner_sync = None
        return partner_sync

    def sync_partner_due() -> None:
        if not bool(account.get("partner_sync_enabled", False)):
            return
        sync = ensure_partner_sync()
        if sync is None:
            state["partner_sync"] = {"enabled": True, "last_error": partner_sync_error}
            return
        try:
            result = sync.poll_due()
            state["partner_sync"] = sync.status()
            if result is not None:
                state["partner_sync"]["last_result"] = result
            if time.time() - float(state.get("partner_sync_saved_at") or 0) > 10:
                state["partner_sync_saved_at"] = time.time()
                save_state()
        except Exception as exc:
            state["partner_sync"] = {"enabled": True, "last_error": str(exc)[:500]}
            if time.time() - float(state.get("partner_sync_saved_at") or 0) > 10:
                state["partner_sync_saved_at"] = time.time()
                save_state()

    def wait_between_scans(seconds: float) -> None:
        if not bool(account.get("partner_sync_enabled", False)):
            time.sleep(seconds)
            return
        deadline = time.monotonic() + max(0.0, seconds)
        while time.monotonic() < deadline:
            if client.session_ready:
                sync_partner_due()
            time.sleep(min(0.5, max(0.0, deadline - time.monotonic())))

    def refresh_config() -> bool:
        if config_path is not None:
            latest_cfg = read_json(config_path, {})
            latest_accounts = latest_cfg.get("ps3838_accounts") if isinstance(latest_cfg, dict) else []
            latest = next((item for item in latest_accounts or [] if isinstance(item, dict) and _text(item.get("name")) == name), None)
            if not latest or not bool(latest.get("enabled", True)):
                return False
            account.update(latest)
            raw_cfg.clear()
            raw_cfg.update(latest_cfg)
        return bool(account.get("enabled", True))

    def manual_request() -> float:
        latest_state = read_json(state_path, {})
        return float(latest_state.get("relogin_requested_at") or 0)

    def save_state() -> None:
        # WebUI 可在等待 2FA 时写入请求，不能被 worker 的旧快照覆盖。
        state["relogin_requested_at"] = max(float(state.get("relogin_requested_at") or 0), manual_request())
        atomic_write_json(state_path, state)

    def should_continue_login() -> bool:
        return refresh_config() and manual_request() <= float(state.get("last_relogin_requested_at") or 0)

    last_login_debug_at = 0.0

    def publish_login_wait(message: str) -> None:
        nonlocal last_login_debug_at
        state.update({
            "status": "relogin", "status_message": message,
            "relogin_in_progress": True, "session_ready": False,
        })
        now = time.time()
        if now - last_login_debug_at >= 5.0:
            last_login_debug_at = now
            try:
                state["login_debug"] = client.login_debug()
                state["login_debug"]["at"] = now
            except Exception as exc:
                state["login_debug"] = {"error": str(exc)[:200], "at": now}
            try:
                shot = data_dir.parent / ".run" / "ps3838-login.png"
                shot.parent.mkdir(parents=True, exist_ok=True)
                if client.page is not None:
                    client.page.screenshot(path=str(shot), full_page=False)
            except Exception:
                pass
        save_state()

    try:
        while refresh_config():
            _flush_telegram_outbox(raw_cfg, account, state, save_state, delivery_path)
            requested = manual_request()
            if requested > float(state.get("last_relogin_requested_at") or 0):
                close_partner_sync()
                client.close()
                state["last_relogin_requested_at"] = requested
                force_login_request = requested
                state["login_required"] = False
                publish_login_wait("登录窗口正在打开，请在浏览器完成登录/2FA")
            if state.get("login_required"):
                # 用户关闭窗口或启动失败后，只有新的手动请求才可再提交账号密码。
                time.sleep(1.0)
                continue
            try:
                if not client.session_ready:
                    publish_login_wait("等待你在浏览器完成登录/2FA，窗口不会自动关闭")
                    if client.ctx is None:
                        client.login(
                            force=bool(force_login_request and force_login_request == requested),
                            should_continue=should_continue_login, on_wait=publish_login_wait,
                        )
                    else:
                        client.wait_for_login(should_continue=should_continue_login, on_wait=publish_login_wait)
                    force_login_request = 0.0
                try:
                    keepalive, payload = client.poll()
                except Ps3838SessionError:
                    if not (account.get("partner_sync_enabled") and client.partner_profile_ready()):
                        raise
                    state.update({
                        "status": "online",
                        "status_message": "在线（后台来单）",
                        "last_ok_at": time.time(),
                        "last_poll_at": time.time(),
                        "session_ready": True,
                        "relogin_in_progress": False,
                        "login_required": False,
                        "last_error": "",
                    })
                    client.save_session_state()
                    save_state()
                    sync_partner_due()
                    wait_between_scans(max(5.0, float(account.get("interval_seconds") or DEFAULT_INTERVAL_SECONDS)))
                    continue
                client.save_session_state()
                client.minimize_window()
                orders = extract_orders(payload, name)
                current = {str(o["orderId"]): o for o in orders if o.get("orderId")}
                previous = state.get("orders") if isinstance(state.get("orders"), dict) else {}
                events: List[Tuple[str, Dict[str, Any], Dict[str, Tuple[Any, Any]]]] = []
                if state.get("initialized"):
                    for oid, order in current.items():
                        if oid not in previous:
                            events.append(("new", order, {}))
                        else:
                            changes = order_changes(previous.get(oid) or {}, order)
                            if changes:
                                events.append(("update", order, changes))
                for event, order, changes in events:
                    row = dict(order)
                    row.update({"ts": int(time.time()), "event": event, "changes": changes})
                    append_jsonl(order_path, row)
                    token, chat, _ = _telegram_config(raw_cfg, account)
                    if token and chat:
                        _queue_order_telegram(state, row)
                state.update(
                    {
                        "status": "online",
                        "status_message": "在线",
                        "last_ok_at": time.time(),
                        "last_poll_at": time.time(),
                        "last_keepalive": keepalive,
                        "order_count": len(current),
                        "new_count": int(state.get("new_count") or 0) + sum(1 for e, _, _ in events if e == "new"),
                        "update_count": int(state.get("update_count") or 0) + sum(1 for e, _, _ in events if e == "update"),
                        "orders": current,
                        "initialized": True,
                        "session_ready": True,
                        "relogin_in_progress": False,
                        "login_required": False,
                        "browser_hidden": client.browser_hidden,
                        "last_error": "",
                    }
                )
                save_state()
                _flush_telegram_outbox(raw_cfg, account, state, save_state, delivery_path)
                account["x_intellifend_secret"] = client.secret
                sync_partner_due()
                wait_between_scans(max(5.0, float(account.get("interval_seconds") or DEFAULT_INTERVAL_SECONDS)))
            except Ps3838LoginInterrupted:
                continue
            except Ps3838SessionError as exc:
                client.session_ready = False
                state["last_error"] = str(exc)[:500]
                state["last_error_at"] = time.time()
                publish_login_wait("会话尚未通过验证，请在原浏览器窗口完成登录/2FA；不会自动重试登录")
            except Exception as exc:
                login_required = isinstance(exc, Ps3838LoginWindowClosed) or not client.session_ready
                state.update(
                    {
                        "status": "offline",
                        "status_message": str(exc)[:300],
                        "last_error": str(exc)[:500],
                        "session_ready": False,
                        "last_error_at": time.time(),
                        "relogin_in_progress": False,
                        "login_required": login_required,
                    }
                )
                save_state()
                # 保留窗口；普通网络错误仍可重试读取，但绝不重新提交登录。
                time.sleep(3.0)
    finally:
        close_partner_sync()
        client.close()
        state.update({"status": "offline", "status_message": "已停止登录和扫描", "session_ready": False, "relogin_in_progress": False})
        save_state()


def run(config_path: Path, data_dir: Path, *, debug: bool = False) -> int:
    threads: Dict[str, threading.Thread] = {}
    pid_path = Path(data_dir) / "ps3838" / "worker.pid"
    try:
        while True:
            cfg = read_json(config_path, {})
            if not isinstance(cfg, dict):
                cfg = {}
            accounts = cfg.get("ps3838_accounts") if isinstance(cfg.get("ps3838_accounts"), list) else []
            active = [a for a in accounts if isinstance(a, dict) and bool(a.get("enabled", True))]
            if not active and (not threads or not any(thread.is_alive() for thread in threads.values())):
                return 0
            for account in active:
                name = _text(account.get("name"))
                thread = threads.get(name)
                if thread is None or not thread.is_alive():
                    thread = threading.Thread(
                        target=process_account,
                        args=(account, cfg, data_dir),
                        kwargs={"config_path": config_path, "debug": debug},
                        name=f"ps3838-{safe_name(name)}",
                        daemon=True,
                    )
                    threads[name] = thread
                    thread.start()
            time.sleep(3 if active else 5)
    finally:
        try:
            if pid_path.exists() and pid_path.read_text(encoding="utf-8").strip() == str(os.getpid()):
                pid_path.unlink()
        except Exception:
            pass


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="PS3838 只读订单监听器")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args(argv)
    return run(Path(args.config), Path(args.data_dir), debug=bool(args.debug))


if __name__ == "__main__":
    raise SystemExit(main())
