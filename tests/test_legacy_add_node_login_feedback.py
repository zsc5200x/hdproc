import unittest
from pathlib import Path


class TestLegacyAddNodeLoginFeedback(unittest.TestCase):
    def test_app_js_contains_browser_login_success_message(self) -> None:
        script = (Path(__file__).resolve().parents[1] / "app" / "webui" / "app.js").read_text(encoding="utf-8")
        self.assertIn("已发起浏览器登录", script)


if __name__ == "__main__":
    unittest.main()
