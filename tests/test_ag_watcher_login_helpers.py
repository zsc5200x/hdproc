import sys
import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
APP = ROOT / "app"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))


from hdzz.core.watcher import ag_watcher  # noqa: E402


class _FakeNode:
    def __init__(self, *, visible: bool = True) -> None:
        self.visible = visible
        self.filled = []
        self.clicked = 0

    def is_visible(self) -> bool:
        return self.visible

    def fill(self, value: str, timeout: int = 0) -> None:
        self.filled.append((value, timeout))

    def click(self, timeout: int = 0) -> None:
        self.clicked += 1


class _FakeLocatorGroup:
    def __init__(self, nodes) -> None:
        self._nodes = list(nodes)

    def count(self) -> int:
        return len(self._nodes)

    def nth(self, index: int):
        return self._nodes[index]

    @property
    def first(self):
        return self._nodes[0]


class _FakeRoleLocator:
    def __init__(self, node) -> None:
        self._node = node

    def count(self) -> int:
        return 1

    @property
    def first(self):
        return self._node


class _FakeRoot:
    def __init__(self, *, labels=None, placeholders=None, role_button=None) -> None:
        self.labels = labels or {}
        self.placeholders = placeholders or {}
        self.role_button = role_button

    def locator(self, selectors: str):
        return _FakeLocatorGroup([])

    def get_by_label(self, text: str, exact: bool = False):
        node = self.labels.get(text)
        return _FakeLocatorGroup([node] if node is not None else [])

    def get_by_placeholder(self, text: str, exact: bool = False):
        node = self.placeholders.get(text)
        return _FakeLocatorGroup([node] if node is not None else [])

    def get_by_role(self, role: str, name=None):
        if role == "button" and self.role_button is not None:
            return _FakeRoleLocator(self.role_button)
        return _FakeRoleLocator(_FakeNode(visible=False))


class _FakePage(_FakeRoot):
    def __init__(self, *, labels=None, placeholders=None, role_button=None, frames=None) -> None:
        super().__init__(labels=labels, placeholders=placeholders, role_button=role_button)
        self.frames = list(frames or [])
        self.bring_to_front_calls = 0

    def bring_to_front(self) -> None:
        self.bring_to_front_calls += 1


class _FakeTopVarPage(_FakePage):
    def __init__(self, *, uid: str = "", layer: str = "", frames=None) -> None:
        super().__init__(frames=frames)
        self.uid = uid
        self.layer = layer

    def evaluate(self, script: str):
        if "uid" in script:
            return self.uid
        if "login_layer" in script:
            return self.layer
        return ""


class _FakeFrame:
    def __init__(self, *, uid: str = "", layer: str = "") -> None:
        self.uid = uid
        self.layer = layer

    def evaluate(self, script: str):
        if "uid" in script:
            return self.uid
        if "login_layer" in script:
            return self.layer
        return ""


class _FakeContext:
    def __init__(self, pages) -> None:
        self.pages = list(pages)


class _FakeLoginPage(_FakePage):
    def __init__(self, *, login_visible: bool) -> None:
        super().__init__()
        self._login_visible = bool(login_visible)

    def locator(self, selectors: str):
        if "#username" in selectors or "input[name='username']" in selectors or "input[type='password']" in selectors:
            return _FakeLocatorGroup([_FakeNode(visible=self._login_visible)])
        return _FakeLocatorGroup([])


class _FakeWmcFallbackPage(_FakePage):
    def __init__(self, url: str = "https://ag.hga030.com/") -> None:
        super().__init__()
        self.url = url
        self.goto_calls = []
        self.wait_calls = []

    def locator(self, selectors: str):
        return _FakeLocatorGroup([])

    def goto(self, url: str, wait_until: str = "", timeout: int = 0) -> None:
        self.goto_calls.append((url, wait_until, timeout))
        self.url = url

    def wait_for_timeout(self, timeout: int) -> None:
        self.wait_calls.append(timeout)


class _FakeContext:
    def __init__(self, pages=None) -> None:
        self.pages = list(pages or [])


class _FakeChromiumLauncher:
    def __init__(self) -> None:
        self.calls = []

    def launch_persistent_context(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return object()


class _FakePlaywright:
    def __init__(self) -> None:
        self.chromium = _FakeChromiumLauncher()


class _AlwaysFailChromiumLauncher:
    def __init__(self) -> None:
        self.calls = []

    def launch_persistent_context(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        raise RuntimeError("browser launch failed")


class _AlwaysFailPlaywright:
    def __init__(self) -> None:
        self.chromium = _AlwaysFailChromiumLauncher()


class TestAgWatcherLoginHelpers(unittest.TestCase):
    def _target_config(self, target: ag_watcher.Target) -> dict:
        return {
            "name": target.name,
            "base_url": target.base_url,
            "ver": target.ver,
            "uid": target.uid,
            "cookie": target.cookie,
            "username": target.username,
            "password": target.password,
            "relogin_mode": target.relogin_mode,
            "relogin_login_tab": target.relogin_login_tab,
        }

    def test_complete_login_form_uses_accessibility_fallbacks_and_brings_page_front(self) -> None:
        username = _FakeNode()
        password = _FakeNode()
        button = _FakeNode()
        page = _FakePage(
            labels={"账号": username, "密码": password},
            role_button=button,
        )

        ag_watcher._complete_login_form(
            page,
            username="demo_user",
            password="demo_pass",
            security_code="",
        )

        self.assertEqual(page.bring_to_front_calls, 1)
        self.assertEqual(username.filled, [("demo_user", 10000)])
        self.assertEqual(password.filled, [("demo_pass", 10000)])
        self.assertEqual(button.clicked, 1)

    def test_existing_lpsid_on_visible_login_form_is_not_treated_as_ready_session(self) -> None:
        page = _FakeLoginPage(login_visible=True)
        cookies = [{"name": "LPSID-9137304", "value": "stale"}]

        ready = ag_watcher._is_relogin_session_ready(
            page,
            cookies=cookies,
            last_uid="",
            initial_lpsid_values={"stale"},
        )

        self.assertFalse(ready)

    def test_changed_lpsid_is_treated_as_ready_session(self) -> None:
        page = _FakeLoginPage(login_visible=True)
        cookies = [{"name": "LPSID-9137304", "value": "fresh"}]

        ready = ag_watcher._is_relogin_session_ready(
            page,
            cookies=cookies,
            last_uid="",
            initial_lpsid_values={"stale"},
        )

        self.assertTrue(ready)

    def test_go_to_home_probe_is_not_treated_as_ready_login(self) -> None:
        self.assertFalse(
            ag_watcher._is_relogin_probe_ready(
                {
                    "status": "error",
                    "code": "",
                    "msg": "goToHome",
                }
            )
        )

    def test_go_to_home_probe_can_be_classified_as_wmc_permission_denied(self) -> None:
        self.assertTrue(
            ag_watcher._is_wmc_permission_denied_response(
                {
                    "status": "error",
                    "code": "",
                    "msg": "goToHome",
                }
            )
        )

    def test_busy_probe_is_treated_as_ready_login(self) -> None:
        self.assertTrue(
            ag_watcher._is_relogin_probe_ready(
                {
                    "status": "error",
                    "code": "m_rep",
                    "msg": "系统忙碌中,请稍候再查询!!",
                }
            )
        )

    def test_open_wmc_page_falls_back_to_direct_wmc_url(self) -> None:
        page = _FakeWmcFallbackPage()
        ctx = _FakeContext([page])

        out = ag_watcher._open_wmc_page_after_login(
            ctx,
            page,
            "https://ag.hga030.com",
            debug=False,
            target_name="ff33667",
        )

        self.assertIs(out, page)
        self.assertEqual(page.goto_calls[-1][0], "https://ag.hga030.com/app/wmc/index.php")

    def test_open_wmc_page_reuses_existing_wmc_page(self) -> None:
        page = _FakeWmcFallbackPage("https://ag.hga030.com/")
        wmc_page = _FakeWmcFallbackPage("https://ag.hga030.com/app/wmc/index.php")
        ctx = _FakeContext([page, wmc_page])

        out = ag_watcher._open_wmc_page_after_login(
            ctx,
            page,
            "https://ag.hga030.com",
            debug=False,
            target_name="ff33667",
        )

        self.assertIs(out, wmc_page)
        self.assertEqual(page.goto_calls, [])

    def test_reads_uid_from_new_context_page_after_login_redirect(self) -> None:
        original_page = _FakePage(frames=[_FakeFrame()])
        logged_in_page = _FakePage(frames=[_FakeFrame(uid="fresh_uid", layer="ag")])
        ctx = _FakeContext([original_page, logged_in_page])

        uid, layer = ag_watcher._read_top_vars_from_context(ctx, original_page)

        self.assertEqual(uid, "fresh_uid")
        self.assertEqual(layer, "ag")

    def test_reads_uid_from_top_page_after_login_redirect(self) -> None:
        original_page = _FakePage(frames=[_FakeFrame()])
        logged_in_page = _FakeTopVarPage(uid="fresh_top_uid", layer="ag")
        ctx = _FakeContext([original_page, logged_in_page])

        uid, layer = ag_watcher._read_top_vars_from_context(ctx, original_page)

        self.assertEqual(uid, "fresh_top_uid")
        self.assertEqual(layer, "ag")

    def test_playwright_launch_args_disable_chrome_crashpad(self) -> None:
        pw = _FakePlaywright()
        with tempfile.TemporaryDirectory() as tmpdir:
            ag_watcher._launch_persistent_context_with_fallback(
                pw,
                user_data_dir=Path(tmpdir),
                headless=False,
                proxy=None,
                target_name="ff33667",
            )

        launch_args = pw.chromium.calls[0][1]["args"]
        self.assertIn("--disable-crash-reporter", launch_args)
        self.assertIn("--disable-crashpad", launch_args)

    def test_macos_app_can_prefer_bundled_chromium_over_system_chrome(self) -> None:
        pw = _FakePlaywright()
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(ag_watcher.sys, "platform", "darwin"):
                with patch.dict("os.environ", {"HDZZ_PLAYWRIGHT_PREFER_BUNDLED": "1"}, clear=False):
                    ag_watcher._launch_persistent_context_with_fallback(
                        pw,
                        user_data_dir=Path(tmpdir),
                        headless=False,
                        proxy=None,
                        target_name="ff33667",
                    )

        self.assertNotIn("channel", pw.chromium.calls[0][1])

    def test_standalone_macos_app_can_disable_system_chrome_fallback(self) -> None:
        pw = _AlwaysFailPlaywright()
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(ag_watcher.sys, "platform", "darwin"):
                with patch.dict(
                    "os.environ",
                    {
                        "HDZZ_PLAYWRIGHT_BUNDLED_ONLY": "1",
                        "PLAYWRIGHT_BROWSERS_PATH": tmpdir,
                    },
                    clear=False,
                ):
                    with self.assertRaises(RuntimeError):
                        ag_watcher._launch_persistent_context_with_fallback(
                            pw,
                            user_data_dir=Path(tmpdir),
                            headless=False,
                            proxy=None,
                            target_name="ff33667",
                        )

        self.assertEqual(len(pw.chromium.calls), 1)
        self.assertNotIn("channel", pw.chromium.calls[0][1])

    def test_background_relogin_success_marks_state_online_immediately(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            state_dir = root / "state"
            config_path = root / "config.json"
            target = ag_watcher.Target(
                name="ff33667",
                base_url="https://ag.hga030.com/",
                ver="version-04-23",
                uid="old_uid",
                cookie="old_cookie",
                username="ff33667",
                password="secret",
                relogin_mode="playwright",
            )
            config_path.write_text(
                json.dumps({"targets": [self._target_config(target)]}),
                encoding="utf-8",
            )

            with patch.object(
                ag_watcher,
                "relogin_via_playwright",
                return_value={"cookie": "fresh_cookie", "uid": "fresh_uid", "login_layer": "ag"},
            ):
                started = ag_watcher.start_background_relogin(
                    target,
                    state_dir=state_dir,
                    config_path=config_path,
                    debug=False,
                )
                self.assertTrue(started)
                thread = ag_watcher._BACKGROUND_RELOGINS["ff33667"]
                thread.join(timeout=2)

            state = json.loads((state_dir / "ff33667.json").read_text(encoding="utf-8"))
            self.assertEqual(state["last_status"], "online")
            self.assertEqual(state["relogin_in_progress"], 0)
            self.assertGreater(state["last_ok_at"], 0)
            self.assertGreater(state["last_relogin_success_at"], 0)

    def test_background_relogin_permission_denied_stops_relogin_loop(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            state_dir = root / "state"
            config_path = root / "config.json"
            target = ag_watcher.Target(
                name="ff33667",
                base_url="https://ag.hga030.com/",
                ver="version-04-23",
                uid="old_uid",
                cookie="old_cookie",
                username="ff33667",
                password="secret",
                relogin_mode="playwright",
            )
            config_path.write_text(
                json.dumps({"targets": [self._target_config(target)]}),
                encoding="utf-8",
            )

            with patch.object(
                ag_watcher,
                "relogin_via_playwright",
                return_value={
                    "cookie": "fresh_cookie",
                    "uid": "fresh_uid",
                    "login_layer": "ag",
                    "wmc_permission_denied": True,
                },
            ):
                started = ag_watcher.start_background_relogin(
                    target,
                    state_dir=state_dir,
                    config_path=config_path,
                    debug=False,
                )
                self.assertTrue(started)
                thread = ag_watcher._BACKGROUND_RELOGINS["ff33667"]
                thread.join(timeout=2)

            state = json.loads((state_dir / "ff33667.json").read_text(encoding="utf-8"))
            self.assertEqual(state["last_status"], "offline")
            self.assertEqual(state["last_error_code"], "wmc_permission_denied")
            self.assertIn("注单监视权限", state["last_error_msg"])
            self.assertEqual(state["relogin_in_progress"], 0)
            self.assertGreater(state["last_relogin_success_at"], 0)

    def test_recent_relogin_success_suppresses_immediate_relogin_loop(self) -> None:
        state = {"last_relogin_success_at": 1000.0}

        self.assertTrue(
            ag_watcher._recent_relogin_success_cooldown_active(
                state,
                now=1020.0,
                min_gap=5,
            )
        )
        self.assertFalse(
            ag_watcher._recent_relogin_success_cooldown_active(
                state,
                now=1100.0,
                min_gap=5,
            )
        )


if __name__ == "__main__":
    unittest.main()
