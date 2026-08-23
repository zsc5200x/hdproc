import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "app" / "webui" / "index.html"
APP_JS = ROOT / "app" / "webui" / "app.js"


class WebuiOrderListLayoutTest(unittest.TestCase):
    def test_realtime_orders_use_list_header(self) -> None:
        source = APP_JS.read_text(encoding="utf-8")
        html = INDEX.read_text(encoding="utf-8")
        self.assertIn('className = "hg-match-list-head"', source)
        self.assertIn('className = `hg-order-list-row', source)
        self.assertIn(".hg-match-list-head", html)
        self.assertIn(".hg-order-list-row", html)

    def test_follow_report_page_is_not_exposed(self) -> None:
        html = INDEX.read_text(encoding="utf-8")
        self.assertIsNone(re.search(r'data-view=["\']report["\']', html))
        self.assertNotIn("跟单报表</div>", html)

    def test_short_window_order_kpis_are_not_exposed(self) -> None:
        html = INDEX.read_text(encoding="utf-8")
        source = APP_JS.read_text(encoding="utf-8")
        self.assertNotIn('id="kpi-10m"', html)
        self.assertNotIn('id="kpi-1h"', html)
        self.assertNotIn('$("kpi-10m")', source)
        self.assertNotIn('$("kpi-1h")', source)


if __name__ == "__main__":
    unittest.main()
