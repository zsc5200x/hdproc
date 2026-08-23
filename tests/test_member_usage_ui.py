import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TestMemberUsageUi(unittest.TestCase):
    def test_member_modal_has_usage_select(self) -> None:
        page = (ROOT / "app" / "webui" / "index.html").read_text(encoding="utf-8")

        self.assertIn('id="mem-usage"', page)
        self.assertIn("账号用途", page)
        self.assertIn("跟单用", page)
        self.assertIn("拉数据用", page)

    def test_member_app_submits_usage_and_supports_update_endpoint(self) -> None:
        script = (ROOT / "app" / "webui" / "app.js").read_text(encoding="utf-8")

        self.assertIn('"/api/arsenal"', script)
        self.assertIn("usage", script)
        self.assertIn('"/api/arsenal/update"', script)


if __name__ == "__main__":
    unittest.main()
