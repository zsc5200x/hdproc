import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_JS = ROOT / "app" / "webui" / "app.js"
INDEX_HTML = ROOT / "app" / "webui" / "index.html"


class OrderTradeTicketUITest(unittest.TestCase):
    def test_realtime_order_card_surfaces_trade_ticket_and_clear_actions(self) -> None:
        app_js = APP_JS.read_text(encoding="utf-8")
        card_source = app_js[
            app_js.index("function renderTable(orders)"):app_js.index("// ── KPIs", app_js.index("function renderTable(orders)"))
        ]
        html = INDEX_HTML.read_text(encoding="utf-8")

        for marker in (
            'class="hg-trade-ticket"',
            'class="hg-trade-market"',
            'class="hg-trade-selection"',
            'class="hg-trade-metric hg-trade-odds"',
            'class="hg-trade-metric hg-trade-stake"',
            "model.marketText",
            "model.selectionText",
            "model.oddsText",
            "model.stakeStr",
        ):
            self.assertIn(marker, card_source)

        self.assertIn("fa-chart-line", card_source)
        self.assertIn("fa-receipt", card_source)
        self.assertIn("fa-futbol", card_source)
        self.assertIn(".hg-trade-ticket", html)


if __name__ == "__main__":
    unittest.main()
