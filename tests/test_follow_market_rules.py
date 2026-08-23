import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
for candidate in (ROOT / "src", ROOT / "app"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))


class TestFollowMarketRules(unittest.TestCase):
    def test_compute_follow_stake_uses_target_ratio_override(self):
        from hdzz.core.follower.engine import compute_follow_stake

        stake = compute_follow_stake(
            {"target": "qax228", "stake": "1000"},
            {"mode": "ratio", "ratio_percent": 46, "targets": [{"name": "qax228", "ratio_percent": 30}]},
        )
        self.assertEqual(stake, 300.0)

    def test_classify_supported_markets(self):
        from hdzz.core.follower.engine import classify_order_market

        cases = [
            ({"betType": "足球 (滚球) 让球", "line": "主队 -0.5"}, "handicap"),
            ({"betType": "足球 (滚球) 大 / 小", "raw": {"ORDER_TYPE": "小"}, "line": "小 3.5"}, "total_under"),
            ({"betType": "足球 独赢", "raw": {"ORDER_TYPE": "主队"}, "line": "主队 独赢"}, "moneyline"),
        ]
        for order, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(classify_order_market(order)[0], expected)

    def test_market_rule_can_disable_or_scale_stake(self):
        from hdzz.core.follower.engine import apply_market_rule_to_stake

        under = {"betType": "足球 大 / 小", "raw": {"ORDER_TYPE": "小"}, "line": "小 3.5"}
        disabled, message, _ = apply_market_rule_to_stake(
            order=under,
            settings={"market_rules": {"total_under": {"enabled": False, "ratio_percent": 100}}},
            base_stake=120,
        )
        self.assertEqual(disabled, 0.0)
        self.assertIn("禁跟", message)

        over = {"betType": "足球 大 / 小", "raw": {"ORDER_TYPE": "大"}, "line": "大 3.5"}
        scaled, _, meta = apply_market_rule_to_stake(
            order=over,
            settings={"market_rules": {"total_over": {"enabled": True, "ratio_percent": 40}}},
            base_stake=101,
        )
        self.assertEqual(scaled, 41.0)
        self.assertEqual(meta["rule_ratio_percent"], 40.0)

    def test_reverse_strategy_changes_market_intent_not_member_params(self):
        from hdzz.core.follower.engine import _snapshot_intent_for_settings
        from hdzz.core.follower.snapshot_direct import MarketIntent

        order = {
            "orderId": "reverse-1", "target": "source", "line": "主队 -0 / 0.5",
            "betType": "(滚球) 让球",
            "raw": {
                "_EVENT": "gid-1", "GTYPE": "FT", "MARKET": "rb",
                "WAGERSTYPE": "(滚球) 让球", "ORDER_TYPE": "主队",
                "TEAM_H": "主队", "TEAM_C": "客队",
            },
        }
        intent = MarketIntent.from_order(order)
        reversed_intent = _snapshot_intent_for_settings(
            intent, order=order,
            settings={"reverse_rules": {"all": False, "handicap": True, "total": False}},
        )
        self.assertEqual(reversed_intent.side, "AWAY")
        self.assertEqual(reversed_intent.target_line, 0.25)


if __name__ == "__main__":
    unittest.main()
