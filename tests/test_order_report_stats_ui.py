import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = ROOT / "app" / "webui" / "index.html"


class OrderReportStatsUITest(unittest.TestCase):
    def test_orders_filter_panel_contains_report_metric_cards_before_filters(self) -> None:
        html = INDEX_HTML.read_text(encoding="utf-8")
        panel = re.search(
            r'<section class="od-panel-card od-filter-panel">(?P<body>.*?)</section>',
            html,
            flags=re.S,
        )
        self.assertIsNotNone(panel)
        body = panel.group("body")

        stats_pos = body.find('id="od-report-stats-grid"')
        filters_pos = body.find('class="od-filter-rows"')
        self.assertGreaterEqual(stats_pos, 0)
        self.assertGreater(filters_pos, stats_pos)

        for metric_id in (
            "od-rpt-pnl",
            "od-rpt-order-count",
            "od-rpt-settled-orders",
            "od-rpt-pending-tickets",
            "od-rpt-win-rate",
        ):
            self.assertIn(f'id="{metric_id}"', body)


if __name__ == "__main__":
    unittest.main()
