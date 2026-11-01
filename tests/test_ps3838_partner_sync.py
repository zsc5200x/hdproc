import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from hdzz.core.watcher.ps3838_partner import PartnerSync, business_today


def _row(order_id, stake=100):
    return {
        "playerWagerId": order_id,
        "agent": "A1",
        "player": "member-one",
        "firstName": "member-one",
        "playerUserId": 1001,
        "userCode": "M1001",
        "status": "OPEN",
        "stake": stake,
        "toWin": 90,
        "currencyCode": "THB",
        "createdDate": 1793558400000,
        "choices": [{
            "leagueName": "Test League",
            "eventName": "Home-vs-Away",
            "handicap": "-0.5",
            "odds": 1.9,
            "sportName": "足球",
            "selection": "HOME",
            "selectionName": "",
            "btName": "HDP",
            "eventDate": 1793562000000,
            "inplay": False,
        }],
    }


class FakeClient:
    def __init__(self, rows):
        self.rows = rows

    def fetch_partner_json(self, path, body=None):
        if path.endswith("/profile"):
            return {"httpStatus": 200, "json": {"profile": {"userId": 100, "userCode": "OWNER"}}}
        if path.endswith("/win-loss-detail"):
            return {"httpStatus": 200, "json": {"body": {"data": [
                {"userCode": "A1", "userId": 11, "userLevel": "AGENT", "firstName": "Agent One"},
            ]}}}
        if path.endswith("/bet-list/v2"):
            rows = [row for row in self.rows if row.get("status") == (body or {}).get("status")]
            return {"httpStatus": 200, "json": {"body": {
                "data": rows,
                "paging": {
                    "totalItems": len(rows), "totalPages": 1 if rows else 0,
                    "rowsPerPage": 50, "beginIndex": 0, "pageNumber": 1,
                },
            }}}
        raise AssertionError(path)


class PartnerSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "partner.sqlite3"

    def test_resolves_agents_and_writes_open_orders(self):
        sync = PartnerSync(FakeClient([_row("o-1")]), {"name": "node", "partner_agents": ["A1"]}, self.db)
        self.addCleanup(sync.close)
        agents = sync.refresh_agents()
        self.assertEqual([agent["code"] for agent in agents], ["A1"])
        result = sync.poll_open()
        self.assertEqual(result["new"], 1)
        self.assertEqual(result["events"], 0)  # first snapshot is baseline
        con = sqlite3.connect(self.db)
        try:
            row = con.execute(
                "SELECT agent_code, member_name, league, teams, market, selection, odds, stake "
                "FROM partner_orders WHERE order_id='o-1'"
            ).fetchone()
            self.assertEqual(row[0], "A1")
            self.assertEqual(row[1], "member-one")
            self.assertEqual(row[2], "Test League")
            self.assertEqual(row[3], "Home-vs-Away")
            self.assertEqual(row[5], "主")
            self.assertAlmostEqual(row[6], 1.9)
            self.assertAlmostEqual(row[7], 100.0)
            agent = con.execute("SELECT code, user_id FROM partner_agents").fetchone()
            self.assertEqual(agent, ("A1", 11))
        finally:
            con.close()

    def test_business_today_uses_gmt_plus_8_even_when_host_timezone_differs(self):
        from datetime import datetime, timezone
        from unittest.mock import patch
        from hdzz.core.watcher import ps3838_partner

        fixed = datetime(2026, 1, 1, 18, 30, tzinfo=timezone.utc)
        with patch.object(ps3838_partner, "datetime") as mock_datetime:
            mock_datetime.now.return_value = fixed.astimezone(ps3838_partner.BUSINESS_TIMEZONE)
            self.assertEqual(business_today().isoformat(), "2026-01-02")

    def test_missing_account_agent_list_does_not_read_machine_config(self):
        from unittest.mock import patch
        from hdzz.core.watcher import ps3838_partner

        sync = PartnerSync(FakeClient([_row("o-1")]), {"name": "node"}, self.db)
        self.addCleanup(sync.close)
        with patch.object(ps3838_partner, "_config_agents", side_effect=AssertionError("machine config read")):
            self.assertEqual([agent["code"] for agent in sync.refresh_agents()], ["A1"])

    def test_new_order_after_baseline_creates_event(self):
        client = FakeClient([_row("o-1")])
        sync = PartnerSync(client, {"name": "node", "partner_agents": ["A1"]}, self.db)
        self.addCleanup(sync.close)
        sync.refresh_agents()
        sync.poll_open()
        client.rows.append(_row("o-2", stake=50))
        result = sync.poll_open()
        self.assertEqual(result["new"], 1)
        self.assertEqual(result["events"], 1)
        con = sqlite3.connect(self.db)
        try:
            events = con.execute(
                "SELECT order_id FROM partner_order_events ORDER BY event_id"
            ).fetchall()
            self.assertEqual([row[0] for row in events], ["o-2"])
        finally:
            con.close()


if __name__ == "__main__":
    unittest.main()
