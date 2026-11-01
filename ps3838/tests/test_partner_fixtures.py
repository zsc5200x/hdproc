import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import partner_orders as po


def _raw(teams, event_ms, *, market="OU 3.00", selection="UNDER", handicap="3.00",
         odds=0.9, stake=100.0, ip="10.0.0.1"):
    return json.dumps({
        "choices": [{
            "eventName": teams,
            "eventDate": event_ms,
            "odds": odds,
            "finalOdds": odds,
            "handicap": handicap,
            "selection": selection,
            "sportId": 29,
            "sportName": "足球",
            "leagueName": "测试联赛",
        }],
        "stake": stake,
        "ipAddress": ip,
        "wagerType": "NORMAL",
    }, ensure_ascii=False)


class FixtureQueryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "partner_orders.sqlite3"
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(po.SCHEMA)

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def _insert(self, member, order_id, teams, event_ms, created_ms, **kwargs):
        raw = _raw(teams, event_ms, **kwargs)
        self.conn.execute(
            """INSERT INTO partner_orders (
                   source, agent_code, member_identity, member_user_code, member_name,
                   order_id, status, inplay, sport, league, teams, market, selection,
                   handicap, odds, stake, to_win, currency, wager_status, created_ms,
                   created_at, content_hash, raw_json, first_seen_at, last_seen_at,
                   changed_at, status_changed_at, result, win_loss
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                po.SOURCE, "TLH0R02", member, member, member, order_id, "SETTLED", 0,
                "足球", "测试联赛", teams, kwargs.get("market", "OU 3.00").replace("OU", "大小"),
                kwargs.get("selection", "UNDER").replace("UNDER", "小"),
                kwargs.get("handicap", "3.00"), kwargs.get("odds", 0.9),
                kwargs.get("stake", 100.0), 0, "THB", "WIN", created_ms, "",
                f"hash-{member}-{order_id}", raw, "", "", "", "", "WIN", 0,
            ),
        )

    def test_fixture_aggregates_orders_and_members(self):
        event_ms = 1_780_000_000_000
        for index in range(3):
            self._insert(f"m{index}", f"o{index}", "A-vs-B", event_ms, 1_700_000_000_000)
        self.conn.commit()

        payload = po.query_fixtures(self.conn, status="ALL")
        self.assertEqual(payload["total"], 1)
        row = payload["rows"][0]
        self.assertEqual(row["orders"], 3)
        self.assertEqual(row["members"], 3)
        self.assertEqual(row["lines"], 1)

        detail = po.query_fixture_orders(self.conn, "A-vs-B", event_ms, status="ALL")
        self.assertEqual(detail["total"], 3)
        self.assertEqual([item["order_id"] for item in detail["rows"]], ["o0", "o1", "o2"])

    def test_rematch_on_another_date_is_a_separate_fixture(self):
        self._insert("m1", "o1", "A-vs-B", 1_780_000_000_000, 1_700_000_000_000)
        self._insert("m2", "o2", "A-vs-B", 1_790_000_000_000, 1_700_000_100_000)
        self.conn.commit()

        payload = po.query_fixtures(self.conn, status="ALL")
        self.assertEqual(payload["total"], 2)
        self.assertEqual({row["event_ms"] for row in payload["rows"]},
                         {1_780_000_000_000, 1_790_000_000_000})

    def test_date_filter_uses_order_created_time(self):
        self._insert("m1", "o1", "A-vs-B", 1_780_000_000_000, 1_600_000_000_000)
        self._insert("m2", "o2", "A-vs-B", 1_780_000_000_000, 1_700_000_000_000)
        self.conn.commit()

        payload = po.query_fixtures(self.conn, status="ALL",
                                    from_ms=1_650_000_000_000)
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["rows"][0]["orders"], 1)

    def test_each_direction_and_line_counts_as_one_handicap(self):
        event_ms = 1_780_000_000_000
        kwargs = dict(market="HDP 0.50", handicap="0.50")
        self._insert("m1", "o1", "A-vs-B", event_ms, 1_700_000_000_000,
                     selection="主", **kwargs)
        self._insert("m2", "o2", "A-vs-B", event_ms, 1_700_000_000_100,
                     selection="客", **kwargs)
        self._insert("m3", "o3", "A-vs-B", event_ms, 1_700_000_000_200,
                     selection="主", **kwargs)
        self.conn.commit()

        row = po.query_fixtures(self.conn, status="ALL")["rows"][0]
        self.assertEqual(row["orders"], 3)
        self.assertEqual(row["lines"], 2)

    def test_event_cursor_gap_is_reported_after_pruning(self):
        self.conn.executemany(
            "INSERT INTO partner_order_events (event_id,source,agent_code,member_identity,order_id,observed_at) VALUES(?,?,?,?,?,?)",
            [(event_id, po.SOURCE, "A1", f"m{event_id}", f"o{event_id}", "2026-11-02T00:00:00Z")
             for event_id in (3, 4, 5)],
        )
        self.conn.commit()

        payload = po.query_events(self.conn, cursor=1)

        self.assertTrue(payload["reset"])
        self.assertTrue(payload["gap"])
        self.assertEqual(payload["pruned_through"], 2)
        self.assertEqual(payload["cursor"], 5)
        self.assertEqual(payload["events"], [])


if __name__ == "__main__":
    unittest.main()
