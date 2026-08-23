import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_JS = ROOT / "app" / "webui" / "app.js"
INDEX_HTML = ROOT / "app" / "webui" / "index.html"


class OrderBetInfoFollowUITest(unittest.TestCase):
    def _bet_info_view_source(self, js: str) -> str:
        start = js.index("function renderOrderBetInfoView")
        end = js.index("// ── Match search", start)
        return js[start:end]

    def test_bet_info_view_loads_and_renders_follow_execution_results(self) -> None:
        js = APP_JS.read_text(encoding="utf-8")
        html = INDEX_HTML.read_text(encoding="utf-8")

        self.assertIn("/api/follow-results?limit=1000", js)
        self.assertIn("renderFollowExecutionPanel", js)
        self.assertIn("buildFollowExecutionModel", js)
        self.assertIn("bet-ticket-follow-panel", html)
        self.assertIn("bet-ticket-follow-row", html)

    def test_bet_info_view_uses_full_order_card_and_removes_old_ticket_sections(self) -> None:
        js = APP_JS.read_text(encoding="utf-8")
        html = INDEX_HTML.read_text(encoding="utf-8")
        view_source = self._bet_info_view_source(js)

        self.assertIn("function renderOrderBetInfoCard", js)
        self.assertIn("renderOrderBetInfoCard(model, order)", view_source)
        self.assertIn("bet-ticket-card-shell", js)
        self.assertIn(".bet-ticket-card-shell", html)

        self.assertNotIn("bet-ticket-main", view_source)
        self.assertNotIn("bet-ticket-amount", view_source)
        self.assertNotIn("bet-ticket-list", view_source)


if __name__ == "__main__":
    unittest.main()
