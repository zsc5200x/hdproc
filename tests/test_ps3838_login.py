"""离线验证 2FA 等待流程，不启动浏览器、不访问平博、不推送 Telegram。"""

import io
import json
from unittest.mock import Mock

import pytest

from hdzz.api.webui import server
from hdzz.core.watcher import ps3838_watch as watch


@pytest.fixture
def login_client(tmp_path, monkeypatch):
    page = Mock()
    page.url = watch.DEFAULT_BASE_URL + watch.LOGIN_PATH
    page.elapsed = 0
    page.otp_visible = False
    page.body_text = ""
    page.is_closed.return_value = False
    page.evaluate.return_value = {"status": 200, "isJson": True, "data": {"data": []}}
    body = Mock()
    body.inner_text.side_effect = lambda **_: page.body_text
    inputs = Mock()
    inputs.count.side_effect = lambda: int(page.otp_visible)
    inputs.nth.return_value.is_visible.side_effect = lambda: page.otp_visible
    page.locator.side_effect = lambda selector: body if selector == "body" else inputs

    def advance(milliseconds):
        page.elapsed += milliseconds / 1000

    page.wait_for_timeout.side_effect = advance
    ctx = Mock()
    # 复现原问题：尚未完成 2FA 时，登录 Cookie 已存在。
    ctx.cookies.return_value = [{"name": "pctag", "value": "pre-auth-cookie"}]
    client = watch.Ps3838Client({"name": "demo", "login_timeout_seconds": 1}, tmp_path / "profile")
    client.ctx = ctx
    client.page = page

    def submit_password():
        page.url = watch.DEFAULT_BASE_URL + watch.AUTH_PATH
        page.otp_visible = True

    client._fill_login = Mock(side_effect=submit_password)
    monkeypatch.setattr(watch.time, "monotonic", lambda: page.elapsed)
    return client, page, ctx


def test_pre_auth_cookie_does_not_finish_login_or_close_2fa_window(login_client):
    client, page, ctx = login_client

    def complete_2fa(milliseconds):
        page.elapsed += milliseconds / 1000
        if page.elapsed == 240:
            page.url = watch.DEFAULT_BASE_URL + "/partner/"
            page.otp_visible = False

    page.wait_for_timeout.side_effect = complete_2fa
    messages = []
    client.login(on_wait=messages.append)

    assert page.elapsed == 240  # 超过旧版 180 秒超时，窗口仍持续等待。
    assert client.session_ready
    assert "2FA" in messages[0]
    client._fill_login.assert_called_once()
    page.goto.assert_called_once()
    ctx.close.assert_not_called()
    assert page.evaluate.call_count == 1  # 2FA 页面不提前轮询。
    assert page.evaluate.call_args.args[1]["url"].endswith(watch.BET_MONITOR_PATH)


@pytest.mark.parametrize("response", [
    {"status": 403, "isJson": True, "data": {}},
    {"status": 200, "isJson": False, "data": {"text": "login"}},
    {"status": 200, "isJson": True, "data": {"success": False, "data": []}},
    {"status": 200, "isJson": True, "data": {"message": "2FA required"}},
])
def test_unverified_response_waits_in_same_window_without_resubmitting(login_client, response):
    client, page, ctx = login_client

    def password_submitted():
        page.url = watch.DEFAULT_BASE_URL + "/partner/"

    client._fill_login.side_effect = password_submitted
    page.evaluate.side_effect = [response, {"status": 200, "isJson": True, "data": {"data": []}}]
    client.login()

    assert client.session_ready
    assert page.elapsed >= 5
    assert page.evaluate.call_count == 2
    client._fill_login.assert_called_once()
    page.goto.assert_called_once()
    ctx.close.assert_not_called()


def test_visible_otp_input_blocks_scanning_even_after_url_changes(login_client):
    client, page, ctx = login_client
    page.url = watch.DEFAULT_BASE_URL + "/partner/"
    page.otp_visible = True
    with pytest.raises(watch.Ps3838LoginInterrupted):
        client.wait_for_login(should_continue=lambda: page.elapsed < 8)
    assert not client.session_ready
    page.evaluate.assert_not_called()
    ctx.close.assert_not_called()


def test_account_lock_stops_probes_and_preserves_window(login_client):
    client, page, ctx = login_client
    page.url = watch.DEFAULT_BASE_URL + "/partner/"
    page.body_text = "Account Temporarily Disabled. Your account has been locked for 5 minutes."
    messages = []
    with pytest.raises(watch.Ps3838LoginInterrupted):
        client.wait_for_login(should_continue=lambda: page.elapsed < 5, on_wait=messages.append)
    assert "暂时锁定" in messages[0]
    page.evaluate.assert_not_called()
    client._fill_login.assert_not_called()
    ctx.close.assert_not_called()


def test_user_closing_login_window_requires_a_manual_login_request(login_client):
    client, page, ctx = login_client
    page.is_closed.side_effect = lambda: page.elapsed >= 2
    with pytest.raises(watch.Ps3838LoginWindowClosed):
        client.login()
    assert not client.session_ready
    page.goto.assert_called_once()
    client._fill_login.assert_called_once()
    ctx.close.assert_not_called()


def test_poll_requires_verified_session(login_client):
    client, page, _ = login_client
    with pytest.raises(watch.Ps3838SessionError):
        client.poll()
    page.evaluate.assert_not_called()


def test_cancel_before_login_does_not_open_or_submit(login_client):
    client, page, _ = login_client
    with pytest.raises(watch.Ps3838LoginInterrupted):
        client.login(should_continue=lambda: False)
    page.goto.assert_not_called()
    client._fill_login.assert_not_called()


def test_cannot_call_write_endpoints(login_client):
    client, page, _ = login_client
    with pytest.raises(ValueError):
        client._evaluate_fetch("/place-bet", {})
    page.evaluate.assert_not_called()


def test_window_only_minimizes_after_verified_login_and_retains_context(login_client):
    client, page, ctx = login_client
    assert not client.minimize_window()
    ctx.new_cdp_session.assert_not_called()
    client.session_ready = True
    ctx.new_cdp_session.return_value.send.side_effect = [{"windowId": 7}, {}]
    assert client.minimize_window()
    assert client.ctx is ctx
    assert client.page is page
    assert client.session_ready
    ctx.close.assert_not_called()
    page.close.assert_not_called()
    ctx.new_cdp_session.return_value.send.assert_called_with(
        "Browser.setWindowBounds", {"windowId": 7, "bounds": {"windowState": "minimized"}}
    )
    assert client.minimize_window()  # 重复轮询不重复干扰窗口。
    ctx.new_cdp_session.assert_called_once()


def test_minimize_failure_does_not_break_the_logged_in_session(login_client):
    client, _, ctx = login_client
    client.session_ready = True
    ctx.new_cdp_session.side_effect = RuntimeError("window unavailable")
    assert not client.minimize_window()
    assert client.session_ready
    ctx.close.assert_not_called()


def test_session_expiry_restores_minimized_window_for_2fa(login_client):
    client, page, ctx = login_client
    client.browser_hidden = True
    ctx.new_cdp_session.return_value.send.side_effect = [{"windowId": 7}, {}]
    with pytest.raises(watch.Ps3838LoginInterrupted):
        client.wait_for_login(should_continue=lambda: page.elapsed < 2)
    assert not client.browser_hidden
    ctx.new_cdp_session.return_value.send.assert_called_with(
        "Browser.setWindowBounds", {"windowId": 7, "bounds": {"windowState": "normal"}}
    )
    ctx.close.assert_not_called()


def setup_worker(tmp_path, monkeypatch):
    account = {"name": "demo", "enabled": True}
    cfg = {"ps3838_accounts": [account]}
    config_path = tmp_path / "config.json"
    watch.atomic_write_json(config_path, cfg)
    client = Mock()
    client.ctx = None
    client.session_ready = False
    client.secret = ""

    def close():
        client.ctx = None
        client.session_ready = False

    client.close.side_effect = close
    monkeypatch.setattr(watch, "Ps3838Client", lambda *_, **__: client)
    return account, cfg, config_path, client


def test_worker_does_not_reopen_login_after_user_closes_window(tmp_path, monkeypatch):
    account, cfg, config_path, client = setup_worker(tmp_path, monkeypatch)
    client.login.side_effect = watch.Ps3838LoginWindowClosed("用户已关闭窗口")
    sleeps = []

    def pause(seconds):
        sleeps.append(seconds)
        # 多轮旧版 3 秒重试周期里，不应再次提交登录。
        if len(sleeps) >= 6:
            cfg["ps3838_accounts"][0]["enabled"] = False
            watch.atomic_write_json(config_path, cfg)

    monkeypatch.setattr(watch.time, "sleep", pause)
    watch.process_account(account, cfg, tmp_path, config_path=config_path)
    client.login.assert_called_once()
    client.poll.assert_not_called()
    assert watch.read_json(tmp_path / "ps3838/state/demo.json", {})["login_required"]


def test_worker_403_reuses_original_window_and_never_repeats_password(tmp_path, monkeypatch):
    account, cfg, config_path, client = setup_worker(tmp_path, monkeypatch)
    window = object()

    def login(**kwargs):
        client.ctx = window
        client.session_ready = True

    client.login.side_effect = login
    client.poll.side_effect = watch.Ps3838SessionError("HTTP 403")

    def wait_for_2fa(**kwargs):
        assert client.ctx is window
        client.close.assert_not_called()
        client.login.assert_called_once()
        kwargs["on_wait"]("等待完成 2FA")
        st = watch.read_json(tmp_path / "ps3838/state/demo.json", {})
        assert st["relogin_in_progress"]
        assert not st["session_ready"]
        cfg["ps3838_accounts"][0]["enabled"] = False
        watch.atomic_write_json(config_path, cfg)
        assert not kwargs["should_continue"]()
        raise watch.Ps3838LoginInterrupted()

    client.wait_for_login.side_effect = wait_for_2fa
    watch.process_account(account, cfg, tmp_path, config_path=config_path)
    client.login.assert_called_once()
    client.wait_for_login.assert_called_once()
    client.close.assert_called_once()  # 只有明确停用账号后才关闭。


def test_worker_reads_new_manual_request_after_previous_failure(tmp_path, monkeypatch):
    account, cfg, config_path, client = setup_worker(tmp_path, monkeypatch)
    state_path = tmp_path / "ps3838/state/demo.json"
    watch.atomic_write_json(state_path, {"login_required": True, "last_relogin_requested_at": 1})

    def request_login(seconds):
        state = watch.read_json(state_path, {})
        state["relogin_requested_at"] = 2
        watch.atomic_write_json(state_path, state)

    def login(**kwargs):
        assert kwargs["force"]
        assert kwargs["should_continue"]()
        cfg["ps3838_accounts"][0]["enabled"] = False
        watch.atomic_write_json(config_path, cfg)
        raise watch.Ps3838LoginInterrupted()

    monkeypatch.setattr(watch.time, "sleep", request_login)
    client.login.side_effect = login
    watch.process_account(account, cfg, tmp_path, config_path=config_path)
    client.login.assert_called_once()
    state = watch.read_json(state_path, {})
    assert state["last_relogin_requested_at"] == 2
    assert state["relogin_requested_at"] == 2


def post_relogin(tmp_path, monkeypatch, enabled, waiting):
    account = {"name": "demo", "enabled": enabled, "login_timeout_seconds": 180}
    config_path = tmp_path / "config.json"
    state_path = tmp_path / "ps3838/state/demo.json"
    watch.atomic_write_json(config_path, {"ps3838_accounts": [account]})
    watch.atomic_write_json(state_path, {"relogin_requested_at": 1, "relogin_in_progress": waiting, "status": "relogin"})
    (tmp_path / "ps3838/worker.pid").write_text("12345")
    monkeypatch.setattr(server, "is_pid_running", lambda pid: True)
    spawn = Mock(return_value=(True, True, ""))
    monkeypatch.setattr(server, "ensure_ps3838_watch_running", spawn)
    handler = object.__new__(server.Handler)
    handler.path = "/api/ps3838/relogin"
    body = json.dumps({"name": "demo"}).encode()
    handler.headers = {"content-length": str(len(body))}
    handler.rfile = io.BytesIO(body)
    handler._config_path = config_path
    handler._data_dir = tmp_path
    handler._send_json = Mock()
    handler.do_POST()
    return handler, spawn, config_path, state_path


def test_repeated_login_click_keeps_pending_2fa_window(tmp_path, monkeypatch):
    handler, spawn, config_path, state_path = post_relogin(tmp_path, monkeypatch, enabled=True, waiting=True)
    assert handler._send_json.call_args.args[0] == 200
    assert watch.read_json(state_path, {})["relogin_requested_at"] == 1
    spawn.assert_not_called()
    assert watch.read_json(config_path, {})["ps3838_accounts"][0]["enabled"]


def test_explicit_relogin_can_resume_paused_account(tmp_path, monkeypatch):
    handler, spawn, config_path, state_path = post_relogin(tmp_path, monkeypatch, enabled=False, waiting=False)
    assert handler._send_json.call_args.args[0] == 200
    saved = watch.read_json(config_path, {})["ps3838_accounts"][0]
    assert saved["enabled"]
    assert saved["login_timeout_seconds"] == 0
    assert watch.read_json(state_path, {})["relogin_in_progress"]
    spawn.assert_called_once()


def test_disabled_account_status_does_not_show_stale_login(tmp_path):
    watch.atomic_write_json(tmp_path / "ps3838/state/demo.json", {"status": "relogin", "relogin_in_progress": True})
    status = server.read_ps3838_status(tmp_path, "demo", enabled=False)
    assert status["status"] == "offline"
    assert "已停用" in status["status_message"]
    assert not status["relogin_in_progress"]
    assert not status["has_session"]


def test_dead_worker_does_not_disable_manual_login_button(tmp_path):
    watch.atomic_write_json(tmp_path / "ps3838/state/demo.json", {"status": "relogin", "relogin_in_progress": True})
    status = server.read_ps3838_status(tmp_path, "demo")
    assert status["status"] == "offline"
    assert not status["relogin_in_progress"]


@pytest.mark.parametrize("enabled", [False, True])
def test_pool_toggle_updates_only_ps3838_and_starts_worker_only_on_resume(tmp_path, monkeypatch, enabled):
    config_path = tmp_path / "config.json"
    cfg = {"targets": [{"name": "ag_demo", "enabled": True}], "ps3838_accounts": [{"name": "demo", "enabled": not enabled}]}
    watch.atomic_write_json(config_path, cfg)
    spawn = Mock(return_value=(True, True, ""))
    monkeypatch.setattr(server, "ensure_ps3838_watch_running", spawn)
    handler = object.__new__(server.Handler)
    handler.path = "/api/ps3838/update"
    body = json.dumps({"name": "demo", "patch": {"enabled": enabled}}).encode()
    handler.headers = {"content-length": str(len(body))}
    handler.rfile = io.BytesIO(body)
    handler._config_path = config_path
    handler._data_dir = tmp_path
    handler._send_json = Mock()
    handler.do_POST()
    assert handler._send_json.call_args.args[0] == 200
    saved = watch.read_json(config_path, {})
    assert saved["ps3838_accounts"][0]["enabled"] is enabled
    assert saved["targets"] == cfg["targets"]
    assert spawn.call_count == int(enabled)
