import sys
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
for candidate in (ROOT / "src", ROOT / "app"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))


class TestFollowMarketRules(unittest.TestCase):
    @staticmethod
    def _order(*, gid="game-1", side="大", wager="足球 大 / 小"):
        return {
            "orderId": f"order-{gid}-{side}",
            "target": "source",
            "stake": 100,
            "betType": wager,
            "line": f"{side} 2.5" if side in {"大", "小"} else side,
            "raw": {
                "_EVENT": gid,
                "GTYPE": "FT",
                "WAGERSTYPE": wager,
                "ORDER_TYPE": side,
                "TEAM_H": "主队",
                "TEAM_C": "客队",
            },
        }

    def test_compute_follow_stake_uses_target_ratio_override(self):
        from hdzz.core.follower.engine import compute_follow_stake

        stake = compute_follow_stake(
            {"target": "qax228", "stake": "1000"},
            {"mode": "ratio", "ratio_percent": 46, "targets": [{"name": "qax228", "ratio_percent": 30}]},
        )
        self.assertEqual(stake, 300.0)

    def test_compute_follow_stake_ignores_removed_max_amount(self):
        from hdzz.core.follower.engine import compute_follow_stake

        stake = compute_follow_stake(
            {"stake": 1000},
            {"mode": "ratio", "ratio_percent": 100, "max_amount": 10},
        )
        self.assertEqual(stake, 1000.0)

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

    def test_limit_identity_groups_match_and_separates_market_buckets(self):
        from hdzz.core.follower.engine import follow_limit_identity

        over = follow_limit_identity(self._order(side="大"), {})
        under = follow_limit_identity(self._order(side="小"), {})
        handicap = follow_limit_identity(self._order(side="主队", wager="足球 让球"), {})

        self.assertEqual(over["match_key"], under["match_key"])
        self.assertEqual(over["match_key"], handicap["match_key"])
        self.assertEqual(over["market_code"], "total_over")
        self.assertEqual(under["market_code"], "total_under")
        self.assertEqual(handicap["market_code"], "handicap")
        self.assertEqual(len({over["market_key"], under["market_key"], handicap["market_key"]}), 3)

    def test_reverse_total_charges_actual_under_bucket(self):
        from hdzz.core.follower.engine import follow_limit_identity

        identity = follow_limit_identity(
            self._order(side="大"),
            {"reverse_rules": {"all": False, "total": True, "handicap": False}},
        )
        self.assertEqual(identity["market_code"], "total_under")
        self.assertTrue(identity["market_key"].endswith("|total_under"))

    def test_match_limit_reduces_amount_across_different_markets(self):
        from hdzz.core.follower.engine import apply_follow_amount_limits, follow_limit_identity

        order = self._order(side="小")
        identity = follow_limit_identity(order, {})
        allowed, message, meta = apply_follow_amount_limits(
            order=order,
            settings={"match_limit_amount": 500, "market_limit_amount": 0},
            requested_stake=200,
            match_committed={identity["match_key"]: 250},
            market_committed={},
            match_reserved={identity["match_key"]: 100},
            market_reserved={},
            min_stake=50,
        )
        self.assertEqual(message, "")
        self.assertEqual(allowed, 150.0)
        self.assertTrue(meta["limited"])

    def test_market_limit_reduces_same_market_but_not_other_market(self):
        from hdzz.core.follower.engine import apply_follow_amount_limits, follow_limit_identity

        over = self._order(side="大")
        over_identity = follow_limit_identity(over, {})
        under_identity = follow_limit_identity(self._order(side="小"), {})
        allowed, message, _ = apply_follow_amount_limits(
            order=over,
            settings={"match_limit_amount": 1000, "market_limit_amount": 300},
            requested_stake=200,
            match_committed={over_identity["match_key"]: 100},
            market_committed={over_identity["market_key"]: 120, under_identity["market_key"]: 250},
            match_reserved={},
            market_reserved={over_identity["market_key"]: 80},
            min_stake=50,
        )
        self.assertEqual(message, "")
        self.assertEqual(allowed, 100.0)

    def test_limit_skips_when_remaining_is_below_minimum_stake(self):
        from hdzz.core.follower.engine import apply_follow_amount_limits, follow_limit_identity

        order = self._order()
        identity = follow_limit_identity(order, {})
        allowed, message, _ = apply_follow_amount_limits(
            order=order,
            settings={"match_limit_amount": 200, "market_limit_amount": 0},
            requested_stake=100,
            match_committed={identity["match_key"]: 170},
            market_committed={},
            match_reserved={},
            market_reserved={},
            min_stake=50,
        )
        self.assertEqual(allowed, 0.0)
        self.assertIn("不足最小下注金额 50 元", message)

    def test_active_split_jobs_reserve_each_remaining_part_once(self):
        from hdzz.core.follower.engine import collect_active_limit_reservations, follow_limit_identity
        from hdzz.core.follower.retry_scheduler import build_independent_split_jobs

        order = self._order()
        identity = follow_limit_identity(order, {})
        jobs = build_independent_split_jobs(
            parent_key="source|order-1",
            order_id="order-1",
            target="source",
            total_stake=180,
            order=order,
            parts=[60, 60, 60],
            created_at=1,
        )
        match_reserved, market_reserved = collect_active_limit_reservations(
            {job.key: job for job in jobs}, settings={}
        )
        self.assertEqual(match_reserved[identity["match_key"]], 180.0)
        self.assertEqual(market_reserved[identity["market_key"]], 180.0)

    def test_limit_metadata_survives_retry_job_serialization(self):
        from hdzz.core.follower.engine import follow_limit_identity, store_follow_limit_metadata
        from hdzz.core.follower.retry_scheduler import build_independent_split_jobs, retry_job_from_dict, retry_job_to_dict

        order = self._order()
        identity = follow_limit_identity(order, {})
        store_follow_limit_metadata(order, {**identity, "requested_stake": 100, "allowed_stake": 80})
        job = build_independent_split_jobs(
            parent_key="source|order-1",
            order_id="order-1",
            target="source",
            total_stake=80,
            order=order,
            parts=[80],
            created_at=1,
        )[0]
        restored = retry_job_from_dict(retry_job_to_dict(job), now_value=2)
        self.assertIsNotNone(restored)
        self.assertEqual(restored.order["_follow_limit"]["match_key"], identity["match_key"])
        self.assertEqual(restored.order["_follow_limit"]["allowed_stake"], 80)

    def test_load_usage_supports_new_and_historical_results_and_deduplicates_ticket(self):
        from hdzz.core.follower.engine import load_follow_limit_usage

        rows = [
            {
                "ok": True, "stake": 60, "ticket_id": "t1",
                "limit_match_key": "FT:g1", "limit_market_key": "FT:g1|total_over",
                "limit_market_code": "total_over",
            },
            {
                "ok": True, "stake": 60, "ticket_id": "t1",
                "limit_match_key": "FT:g1", "limit_market_key": "FT:g1|total_over",
            },
            {
                "ok": True, "stake": 40, "ticket_id": "t2",
                "snapshot_direct_intent": {"gid": "g1", "gtype": "FT", "market_type": "TOTAL", "side": "UNDER"},
            },
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "follow_results.jsonl"
            path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
            match_usage, market_usage, success_keys = load_follow_limit_usage(path)

        self.assertEqual(match_usage["FT:g1"], 100.0)
        self.assertEqual(market_usage["FT:g1|total_over"], 60.0)
        self.assertEqual(market_usage["FT:g1|total_under"], 40.0)
        self.assertEqual(success_keys, {"ticket:t1", "ticket:t2"})

    def test_record_success_is_idempotent_by_ticket(self):
        from hdzz.core.follower.engine import record_follow_limit_success

        result = {
            "ok": True,
            "stake": 80,
            "ticket_id": "ticket-1",
            "limit_match_key": "FT:g1",
            "limit_market_key": "FT:g1|handicap",
        }
        match_usage, market_usage, keys = {}, {}, set()
        self.assertTrue(record_follow_limit_success(result, match_committed=match_usage, market_committed=market_usage, success_keys=keys))
        self.assertFalse(record_follow_limit_success(result, match_committed=match_usage, market_committed=market_usage, success_keys=keys))
        self.assertEqual(match_usage["FT:g1"], 80.0)
        self.assertEqual(market_usage["FT:g1|handicap"], 80.0)


if __name__ == "__main__":
    unittest.main()
