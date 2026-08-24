import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TestMemberProxyTestUi(unittest.TestCase):
    def test_member_modal_uses_grouped_accessible_layout(self) -> None:
        page = (ROOT / "app" / "webui" / "index.html").read_text(encoding="utf-8")

        self.assertIn('class="modal-box member-dialog"', page)
        self.assertIn('aria-labelledby="member-dialog-title"', page)
        self.assertIn('class="member-section" aria-labelledby="member-login-title"', page)
        self.assertIn('class="member-section" aria-labelledby="member-settings-title"', page)
        self.assertIn('type="url" id="mem-base-url"', page)
        self.assertIn('for="mem-username"', page)
        self.assertIn('for="mem-password"', page)
        self.assertIn('id="mem-password-toggle"', page)
        self.assertIn('id="mem-submit" type="button" aria-busy="false"', page)

    def test_add_member_modal_has_proxy_test_button(self) -> None:
        page = (ROOT / "app" / "webui" / "index.html").read_text(encoding="utf-8")

        self.assertIn('id="mem-proxy"', page)
        self.assertIn('id="mem-proxy-test"', page)
        self.assertIn("测试代理", page)

    def test_app_binds_member_proxy_test_action(self) -> None:
        script = (ROOT / "app" / "webui" / "app.js").read_text(encoding="utf-8")

        self.assertIn("async function testMemberProxy()", script)
        self.assertIn('fetchJson("/api/proxy-test"', script)
        self.assertIn('$("mem-proxy-test")?.addEventListener("click", testMemberProxy)', script)

        load_initial_start = script.index("async function loadInitial")
        bind_dialog_call = script.index("bindMemberModalControls();", load_initial_start)
        first_network_load = script.index("await Promise.allSettled([", load_initial_start)
        self.assertIn("function bindMemberModalControls()", script)
        self.assertLess(bind_dialog_call, first_network_load)

    def test_member_modal_manages_focus_password_and_loading_states(self) -> None:
        script = (ROOT / "app" / "webui" / "app.js").read_text(encoding="utf-8")

        self.assertIn('$("mem-base-url")?.focus()', script)
        self.assertIn("function toggleMemberPasswordVisibility()", script)
        self.assertIn('$("mem-password-toggle")?.addEventListener("click", toggleMemberPasswordVisibility)', script)
        self.assertIn('btn.setAttribute("aria-busy", "true")', script)
        self.assertIn('btn.setAttribute("aria-busy", "false")', script)


if __name__ == "__main__":
    unittest.main()
