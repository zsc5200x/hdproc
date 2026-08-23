import unittest
from pathlib import Path


class TestArsenalLoginUi(unittest.TestCase):
    def test_arsenal_cards_contain_login_button_and_api_call(self) -> None:
        root = Path(__file__).resolve().parents[1] / "app" / "webui"
        page = (root / "index.html").read_text(encoding="utf-8")
        script = (root / "app.js").read_text(encoding="utf-8")
        combined = page + "\n" + script

        self.assertIn("arsenal-cards", page)
        self.assertIn("arsenal_cards.js", page)
        self.assertIn("loginArsenalMember", script)
        self.assertIn("fa-right-to-bracket", script)
        self.assertIn("登录", script)
        self.assertIn("/api/arsenal/login", combined)


if __name__ == "__main__":
    unittest.main()
