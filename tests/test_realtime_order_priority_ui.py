import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = ROOT / "app" / "webui" / "index.html"
APP_JS = ROOT / "app" / "webui" / "app.js"


class RealtimeOrderPriorityUITest(unittest.TestCase):
    def test_page_brand_matches_hd_push_system(self) -> None:
        html = INDEX_HTML.read_text(encoding="utf-8")
        app_js = APP_JS.read_text(encoding="utf-8")

        self.assertIn("<title>HD 推送系统 — 实时订单终端</title>", html)
        self.assertIn('class="sb-name">HD 推送系统</div>', html)
        self.assertIn("HD 推送系统测试消息", app_js)

    def test_order_stream_precedes_collapsed_insights(self) -> None:
        html = INDEX_HTML.read_text(encoding="utf-8")

        stream_pos = html.index('id="matches-grid"')
        insights_pos = html.index('id="od-insights"')
        self.assertLess(stream_pos, insights_pos)

        toggle_start = html.index('id="od-toggle-charts"')
        toggle = html[toggle_start:toggle_start + 260]
        self.assertIn('aria-expanded="false"', toggle)
        self.assertIn('展开分析', toggle)


if __name__ == "__main__":
    unittest.main()
