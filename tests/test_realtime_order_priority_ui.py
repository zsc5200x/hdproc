import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = ROOT / "app" / "webui" / "index.html"
APP_JS = ROOT / "app" / "webui" / "app.js"


class RealtimeOrderPriorityUITest(unittest.TestCase):
    def test_page_brand_matches_hd_follow_system(self) -> None:
        html = INDEX_HTML.read_text(encoding="utf-8")

        self.assertIn("<title>HD跟单系统 v1.0.0</title>", html)
        self.assertIn('aria-label="HD跟单系统 v1.0.0"', html)
        self.assertIn('<span class="tn-name">HD跟单系统</span>', html)
        self.assertIn('<span class="tn-version">v1.0.0</span>', html)

    def test_order_stream_is_the_last_dashboard_panel(self) -> None:
        html = INDEX_HTML.read_text(encoding="utf-8")

        stream_pos = html.index('id="matches-grid"')
        queue_pos = html.index('data-view="queue"', stream_pos)
        self.assertLess(stream_pos, queue_pos)
        self.assertNotIn('id="od-insights"', html)
        self.assertNotIn('id="od-toggle-charts"', html)


if __name__ == "__main__":
    unittest.main()
