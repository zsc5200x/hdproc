import unittest
from pathlib import Path


class TestLegacyWebuiConfigShortcuts(unittest.TestCase):
    def test_page_uses_the_public_system_center_navigation(self) -> None:
        page = (Path(__file__).resolve().parents[1] / "app" / "webui" / "index.html").read_text(encoding="utf-8")
        self.assertIn('data-view="system-center" id="tn-system-center"', page)
        self.assertIn('<span class="tn-label">系统中心</span>', page)
        self.assertNotIn('<div class="top-config-shortcuts"', page)
        self.assertNotIn('id="config-popup"', page)
        self.assertNotRegex(page, r'<(?:button|a)[^>]*\bdata-config-view=')
        self.assertNotIn("<div class=\"sb-section-label\">系统配置</div>", page)


if __name__ == "__main__":
    unittest.main()
