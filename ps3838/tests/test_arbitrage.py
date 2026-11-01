import unittest
from datetime import datetime, timedelta

from src.services.arbitrage import (
    find_opportunities,
    format_line_text,
    parse_line,
    tier_for,
)


class LineParsingTests(unittest.TestCase):
    def test_crown_split_lines_keep_the_leading_sign(self):
        self.assertEqual(parse_line("0.5 / 1"), 0.75)
        self.assertEqual(parse_line("-0/0.5"), -0.25)
        self.assertEqual(parse_line("+0.5/1"), 0.75)
        self.assertEqual(parse_line("2 / 2.5"), 2.25)
        self.assertEqual(parse_line("-1"), -1.0)
        self.assertEqual(parse_line("0"), 0.0)

    def test_invalid_lines_are_rejected(self):
        self.assertIsNone(parse_line(None))
        self.assertIsNone(parse_line(""))
        self.assertIsNone(parse_line("平手"))
        self.assertIsNone(parse_line("0.5/1/1.5"))

    def test_line_text_uses_crown_quarter_notation(self):
        self.assertEqual(format_line_text(-0.25, "2"), "-0/0.5")
        self.assertEqual(format_line_text(0.75, "2"), "+0.5/1")
        self.assertEqual(format_line_text(-1.25, "2"), "-1/1.5")
        self.assertEqual(format_line_text(2.25, "3"), "2/2.5")
        self.assertEqual(format_line_text(0, "2"), "0")

    def test_tiers_follow_the_documented_thresholds(self):
        self.assertEqual(tier_for(0.02), "arb")
        self.assertEqual(tier_for(0.002), "critical")
        self.assertEqual(tier_for(-0.01), "near")
        self.assertEqual(tier_for(-0.04), "watch")


def _crown_event(**overrides):
    event = {
        "id": "c1",
        "league": "测试联赛",
        "home": "主队",
        "away": "客队",
        "is_live": True,
        "score": "0 - 0",
        "minute": "10",
        "start_label": "10-30 08:00a",
        "markets": {"handicap": [], "total": []},
        "extra_markets": {"status": "not_requested"},
    }
    event.update(overrides)
    return event


def _row(market, option, line, water, **overrides):
    row = {
        "market": market,
        "option": option,
        "line": line,
        "hongKong": water,
        "quoted": True,
        "capturedAt": None,
    }
    row.update(overrides)
    return row


def _scan(crown, rows, *, now=None, min_edge=-0.05, limit=50):
    return find_opportunities(
        [crown],
        {"c1": {"event": {"id": "p1", "kickoff": "2026-10-30 20:00:00"}, "rows": rows}},
        [{"crownId": "c1", "pinnacleId": "p1"}],
        now=now or datetime(2026, 10, 30, 12, 0, tzinfo=None).astimezone(),
        crown_updated_at=None,
        min_edge=min_edge,
        limit=limit,
    )


class FixtureIntegrityTests(unittest.TestCase):
    def _scan_pair(self, crown, event, rows):
        return find_opportunities(
            [crown],
            {"c1": {"event": event, "rows": rows}},
            [{"crownId": "c1", "pinnacleId": "p1"}],
            now=datetime(2026, 10, 30, 12, 0, tzinfo=None).astimezone(),
            crown_updated_at=None,
            min_edge=-0.05,
            limit=50,
        )

    def test_namesake_competitions_never_produce_a_pair(self):
        crown = _crown_event(
            league="土耳其超级联赛",
            markets={"handicap": [], "total": [{"line": "3", "a": "1.02", "b": "0.85"}]},
        )
        rows = [
            _row("3", "3", "3", 1.12),
            _row("3", "4", "3", 1.20),
        ]
        result = self._scan_pair(
            crown,
            {
                "id": "p1",
                "league": "土耳其 - 超级联赛U19",
                "kickoff": "2026-10-30 20:00:00",
            },
            rows,
        )
        self.assertEqual(result["items"], [])
        self.assertEqual(result["stats"]["pairs"], 0)
        self.assertEqual(result["stats"]["mismatch"], 1)
        self.assertEqual(result["stats"]["mismatchReasons"], {"competition": 1})

    def test_same_league_at_a_different_kickoff_is_rejected(self):
        crown = _crown_event(
            league="土耳其超级联赛",
            markets={"handicap": [], "total": [{"line": "3", "a": "1.02", "b": "0.85"}]},
        )
        rows = [
            _row("3", "3", "3", 1.12),
            _row("3", "4", "3", 1.20),
        ]
        result = self._scan_pair(
            crown,
            {
                "id": "p1",
                "league": "土耳其 - 超级联赛",
                "kickoff": "2026-10-31 01:00:00",
            },
            rows,
        )
        self.assertEqual(result["items"], [])
        self.assertEqual(result["stats"]["mismatchReasons"], {"kickoff": 1})

    def test_live_pair_with_a_stale_pinnacle_quote_is_dropped(self):
        now = datetime(2026, 10, 30, 20, 0, 0).astimezone()
        crown = _crown_event(
            is_live=True,
            league="中国U20联赛",
            markets={"handicap": [], "total": [{"line": "3", "a": "1.02", "b": "0.85"}]},
        )
        old = (now - timedelta(minutes=69)).isoformat()
        rows = [
            _row("3", "3", "3", 1.56, capturedAt=old),
            _row("3", "4", "3", 0.43, capturedAt=old),
        ]
        result = find_opportunities(
            [crown],
            {"c1": {"event": {
                "id": "p1", "league": "中国 - U20联赛",
                "kickoff": "2026-10-30 20:00:00",
            }, "rows": rows}},
            [{"crownId": "c1", "pinnacleId": "p1"}],
            now=now,
            crown_updated_at=now.isoformat(),
        )
        self.assertEqual(result["items"], [])
        self.assertEqual(result["stats"]["stale"], 2)

    def test_live_pair_with_a_fresh_pinnacle_quote_is_kept(self):
        now = datetime(2026, 10, 30, 20, 0, 0).astimezone()
        crown = _crown_event(
            is_live=True,
            league="中国U20联赛",
            markets={"handicap": [], "total": [{"line": "3", "a": "1.02", "b": "0.85"}]},
        )
        fresh = (now - timedelta(seconds=10)).isoformat()
        rows = [
            _row("3", "3", "3", 1.10, capturedAt=fresh),
            _row("3", "4", "3", 1.20, capturedAt=fresh),
        ]
        result = find_opportunities(
            [crown],
            {"c1": {"event": {
                "id": "p1", "league": "中国 - U20联赛",
                "kickoff": "2026-10-30 20:00:00",
            }, "rows": rows}},
            [{"crownId": "c1", "pinnacleId": "p1"}],
            now=now,
            crown_updated_at=now.isoformat(),
        )
        self.assertEqual(result["stats"]["stale"], 0)
        self.assertEqual(len(result["items"]), 2)

    def test_matching_fixture_still_pairs_normally(self):
        crown = _crown_event(
            league="土耳其超级联赛",
            markets={"handicap": [], "total": [{"line": "3", "a": "1.02", "b": "0.85"}]},
        )
        rows = [
            _row("3", "3", "3", 1.12),
            _row("3", "4", "3", 1.20),
        ]
        result = self._scan_pair(
            crown,
            {
                "id": "p1",
                "league": "土耳其 - 超级联赛",
                "kickoff": "2026-10-30 20:00:00",
            },
            rows,
        )
        self.assertEqual(result["stats"]["mismatch"], 0)
        self.assertEqual(result["stats"]["pairs"], 2)
        self.assertEqual(len(result["items"]), 2)


class OpportunityPairingTests(unittest.TestCase):
    def test_home_line_pairs_with_the_opposite_pinnacle_selection(self):
        crown = _crown_event(markets={"handicap": [{"line": "-1", "a": "1.05", "b": "0.90"}], "total": []})
        rows = [
            _row("2", "0", "-1", 1.02),
            _row("2", "1", "+1", 1.03),
        ]
        result = _scan(crown, rows)
        self.assertEqual(result["stats"]["pairs"], 2)
        best, other = result["items"]
        # Crown home -1 @1.05 vs Pinnacle away +1 @1.03.
        self.assertEqual((best["crownSide"], best["pinnacleSide"]), ("home", "away"))
        self.assertEqual(best["line"], -1.0)
        self.assertEqual(best["lineText"], "-1")
        self.assertAlmostEqual(best["edge"], 0.0196, places=4)
        self.assertEqual(best["tier"], "arb")
        self.assertAlmostEqual(best["waterSum"], 2.08, places=3)
        # The mirrored pair is a normal market, not an arbitrage.
        self.assertEqual((other["crownSide"], other["pinnacleSide"]), ("away", "home"))
        self.assertLess(other["edge"], 0)

    def test_totals_pair_over_with_under_on_the_same_split_line(self):
        crown = _crown_event(markets={
            "handicap": [],
            "total": [{"line": "2 / 2.5", "a": "0.95", "b": "1.00"}],
        })
        rows = [
            _row("3", "3", "2.25", 1.00),
            _row("3", "4", "2.25", 0.95),
        ]
        result = _scan(crown, rows)
        self.assertEqual(result["stats"]["pairs"], 2)
        prices = {item["crownSide"]: item for item in result["items"]}
        self.assertEqual(prices["over"]["lineText"], "2/2.5")
        self.assertEqual(prices["over"]["marketLabel"], "大小球")
        self.assertAlmostEqual(prices["over"]["edge"], -0.0256, places=4)
        self.assertAlmostEqual(prices["under"]["edge"], 0.0, places=4)
        self.assertEqual(prices["under"]["tier"], "critical")

    def test_unquoted_or_zero_rows_are_ignored(self):
        crown = _crown_event(markets={"handicap": [{"line": "-0.5", "a": "1.20", "b": "1.00"}], "total": []})
        rows = [
            _row("2", "1", "+0.5", 1.03, quoted=False),
            _row("2", "1", "+0.5", 0),
        ]
        result = _scan(crown, rows)
        self.assertEqual(result["items"], [])
        self.assertEqual(result["stats"]["pairs"], 0)

    def test_alternate_crown_lines_are_scanned_when_loaded(self):
        crown = _crown_event(
            markets={"handicap": [{"line": "-0.5", "a": "0.90", "b": "1.00"}], "total": []},
            extra_markets={
                "status": "loaded",
                "handicap": [{"line": "-1", "a": "1.06", "b": "0.88"}],
                "total": [],
            },
        )
        rows = [
            _row("2", "0", "-0.5", 0.80),
            _row("2", "1", "+0.5", 0.90),
            _row("2", "0", "-1", 0.90),
            _row("2", "1", "+1", 1.04),
        ]
        result = _scan(crown, rows, min_edge=-0.1)
        lines = {item["line"] for item in result["items"]}
        self.assertEqual(lines, {-0.5, -1.0})
        # The alternate -1 line is an arbitrage: 1.06 + 1.04.
        arb = [item for item in result["items"] if item["line"] == -1.0 and item["crownSide"] == "home"][0]
        self.assertEqual(arb["tier"], "arb")

    def test_results_are_sorted_and_limited(self):
        crown = _crown_event(markets={"handicap": [{"line": "-0.5", "a": "1.00", "b": "1.10"}], "total": []})
        rows = [
            _row("2", "1", "+0.5", 1.06),
            _row("2", "0", "-0.5", 0.70),
        ]
        result = _scan(crown, rows, limit=1)
        self.assertEqual(len(result["items"]), 1)
        self.assertGreater(result["items"][0]["edge"], 0)

    def test_ages_use_the_older_of_the_two_quotes(self):
        now = datetime(2026, 10, 30, 12, 0, 0).astimezone()
        crown = _crown_event(markets={"handicap": [{"line": "-0.5", "a": "1.00", "b": "1.00"}], "total": []})
        rows = [
            _row("2", "0", "-0.5", 1.00, capturedAt=(now - timedelta(minutes=3)).isoformat()),
            _row("2", "1", "+0.5", 1.00, capturedAt=(now - timedelta(seconds=20)).isoformat()),
        ]
        result = find_opportunities(
            [crown],
            {"c1": {"event": {"id": "p1"}, "rows": rows}},
            [{"crownId": "c1", "pinnacleId": "p1"}],
            crown_updated_at=(now - timedelta(seconds=5)).isoformat(),
            now=now,
        )
        old_row = [item for item in result["items"] if item["pinnacleCapturedAt"] == rows[0]["capturedAt"]][0]
        self.assertEqual(old_row["ageSeconds"], 180)
        fresh_row = [item for item in result["items"] if item["pinnacleCapturedAt"] == rows[1]["capturedAt"]][0]
        self.assertEqual(fresh_row["ageSeconds"], 20)


if __name__ == "__main__":
    unittest.main()
