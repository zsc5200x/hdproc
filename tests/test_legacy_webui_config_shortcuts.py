import unittest
from pathlib import Path


class TestLegacyWebuiConfigShortcuts(unittest.TestCase):
    def test_page_contains_top_config_shortcuts_and_popup_shell(self) -> None:
        page = (Path(__file__).resolve().parents[1] / "app" / "webui" / "index.html").read_text(encoding="utf-8")
        self.assertIn("top-config-shortcuts", page)
        self.assertIn("config-popup", page)
        self.assertIn("config-popup-body", page)
        self.assertIn("config-popup-box.compact", page)
        self.assertIn("data-config-view=\"follow-settings\"", page)
        self.assertIn("data-config-view=\"telegram\"", page)
        self.assertIn("data-config-view=\"signal-pull\"", page)
        self.assertNotIn("<div class=\"sb-section-label\">系统配置</div>", page)


if __name__ == "__main__":
    unittest.main()
