import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_JS = ROOT / "app" / "webui" / "app.js"
INDEX_HTML = ROOT / "app" / "webui" / "index.html"


class OrderTradeTicketUITest(unittest.TestCase):
    def test_realtime_order_list_surfaces_market_money_status_and_actions(self) -> None:
        app_js = APP_JS.read_text(encoding="utf-8")
        card_source = app_js[
            app_js.index("function renderRealtimeOrderListRow"):app_js.index("function appendRealtimeOrderListHeader")
        ]
        html = INDEX_HTML.read_text(encoding="utf-8")

        for marker in (
            "hg-order-list-row",
            "hg-cell-market",
            "rowData.marketText",
            "rowData.betAmountText",
            "rowData.followAmountText",
            "rowData.followStatusText",
            "rowData.settlementText",
        ):
            self.assertIn(marker, card_source)

        self.assertIn("fa-chart-line", card_source)
        self.assertIn("fa-receipt", card_source)
        self.assertIn("fa-futbol", card_source)
        self.assertIn(".hg-order-list-row", html)


if __name__ == "__main__":
    unittest.main()
