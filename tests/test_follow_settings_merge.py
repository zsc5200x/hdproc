import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
APP = ROOT / "app"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))


class TestFollowSettingsMerge(unittest.TestCase):
    def test_global_only_live_switch_is_tri_state_and_saves_boolean(self) -> None:
        from hdzz.api.webui.follow_settings import ensure_follow_settings_defaults, merge_follow_settings

        legacy = ensure_follow_settings_defaults({"targets": [{"name": "legacy", "only_live": True}]})
        self.assertIsNone(legacy["only_live"])
        saved_off = merge_follow_settings(legacy, {"only_live": False})
        self.assertIs(saved_off["only_live"], False)
        saved_on = merge_follow_settings(saved_off, {"only_live": True})
        self.assertIs(saved_on["only_live"], True)

    def test_merge_updates_only_explicit_fields(self) -> None:
        from hdzz.api.webui.follow_settings import merge_follow_settings

        existing = {
            "enabled": True,
            "mode": "ratio",
            "fixed_amount": 50,
            "ratio_percent": 150,
            "max_amount": 123,
            "queue_max_per_tick": 7,  # should be preserved
            "allow_quote_members": False,
        }
        incoming = {
            "enabled": False,
            "allow_quote_members": True,
            # max_amount not present -> must not be cleared
        }
        out = merge_follow_settings(existing, incoming)

        self.assertEqual(out["enabled"], False)
        self.assertEqual(out["allow_quote_members"], False)
        self.assertEqual(out["max_amount"], 123)
        self.assertEqual(out["queue_max_per_tick"], 7)

    def test_merge_forces_allow_quote_members_off(self) -> None:
        from hdzz.api.webui.follow_settings import merge_follow_settings

        out = merge_follow_settings(
            {"allow_quote_members": False},
            {"allow_quote_members": True},
        )

        self.assertEqual(out["allow_quote_members"], False)

    def test_merge_sanitizes_targets(self) -> None:
        from hdzz.api.webui.follow_settings import merge_follow_settings

        existing = {"targets": [{"name": "old", "only_live": False}]}
        incoming = {
            "targets": [
                {"name": "a", "only_live": True, "ratio_percent": 66},
                {"name": "  ", "only_live": True},
                "bad",
                {"name": "b"},
                {"name": "a", "only_live": False, "ratio_percent": 0},  # allow duplicates? we keep order, but should keep both? prefer last? keep both? decide in impl
            ]
        }
        out = merge_follow_settings(existing, incoming)
        self.assertEqual(
            out["targets"],
            [
                {"name": "a", "enabled": True, "only_live": True, "ratio_percent": 66.0},
                {"name": "b", "enabled": True, "only_live": False, "ratio_percent": None},
                {"name": "a", "enabled": True, "only_live": False, "ratio_percent": 0.0},
            ],
        )

    def test_merge_sanitizes_target_enabled_flag(self) -> None:
        from hdzz.api.webui.follow_settings import merge_follow_settings

        out = merge_follow_settings(
            {},
            {
                "targets": [
                    {"name": "ccvv445566", "enabled": False, "only_live": False},
                    {"name": "sg121212", "only_live": False},
                ]
            },
        )

        self.assertEqual(out["targets"][0]["enabled"], False)
        self.assertEqual(out["targets"][1]["enabled"], True)

    def test_merge_sanitizes_target_ratio_percent_bounds(self) -> None:
        from hdzz.api.webui.follow_settings import merge_follow_settings

        out = merge_follow_settings(
            {},
            {
                "targets": [
                    {"name": "qax228", "only_live": False, "ratio_percent": 888},
                    {"name": "qax229", "only_live": False, "ratio_percent": -5},
                    {"name": "qax230", "only_live": False, "ratio_percent": ""},
                ]
            },
        )

        self.assertEqual(out["targets"][0]["ratio_percent"], 500.0)
        self.assertEqual(out["targets"][1]["ratio_percent"], 0.0)
        self.assertIsNone(out["targets"][2]["ratio_percent"])

    def test_ensure_defaults_exposes_direct_keys_and_removes_legacy_keys(self) -> None:
        from hdzz.api.webui.follow_settings import ensure_follow_settings_defaults

        doc = {"enabled": True}
        out = ensure_follow_settings_defaults(doc)
        self.assertIn("follow_min_odds", out)
        self.assertIn("follow_max_odds", out)
        self.assertEqual(out["match_limit_amount"], 0.0)
        self.assertEqual(out["market_limit_amount"], 0.0)
        self.assertEqual(out["execution_mode"], "snapshot_direct")
        self.assertTrue(out["snapshot_direct_enabled"])
        self.assertEqual(out["snapshot_max_age_ms"], 500)
        self.assertEqual(out["bet_context_mode"], "diagnostic_order_view")
        self.assertNotIn("game_more_scan_all_gids", out)
        self.assertNotIn("order_view_confirm_before_bet", out)
        self.assertNotIn("order_view_confirm_timeout_s", out)
        self.assertIn("allow_quote_members", out)
        self.assertIn("market_rules", out)
        self.assertIn("reverse_rules", out)
        self.assertIn("handicap", out["market_rules"])
        self.assertIn("total_over", out["market_rules"])
        self.assertIn("total_under", out["market_rules"])
        self.assertIn("moneyline", out["market_rules"])
        self.assertIn("all", out["reverse_rules"])
        self.assertIn("total", out["reverse_rules"])
        self.assertIn("handicap", out["reverse_rules"])

    def test_legacy_client_cannot_switch_execution_path_or_restore_fallback(self) -> None:
        from hdzz.api.webui.follow_settings import merge_follow_settings

        out = merge_follow_settings(
            {},
            {
                "execution_mode": "shadow",
                "snapshot_direct_enabled": False,
                "snapshot_missing_policy": "legacy_fallback",
                "order_view_confirm_before_bet": True,
            },
        )
        self.assertEqual(out["execution_mode"], "snapshot_direct")
        self.assertTrue(out["snapshot_direct_enabled"])
        self.assertNotIn("snapshot_missing_policy", out)
        self.assertNotIn("order_view_confirm_before_bet", out)

    def test_bet_context_mode_always_uses_verified_context_path(self) -> None:
        from hdzz.api.webui.follow_settings import merge_follow_settings

        enabled = merge_follow_settings({}, {"bet_context_mode": "diagnostic_order_view"})
        self.assertEqual(enabled["execution_mode"], "snapshot_direct")
        self.assertEqual(enabled["bet_context_mode"], "diagnostic_order_view")
        disabled = merge_follow_settings({}, {"bet_context_mode": "disabled"})
        self.assertEqual(disabled["execution_mode"], "snapshot_direct")
        self.assertEqual(disabled["bet_context_mode"], "diagnostic_order_view")

    def test_market_reject_retry_settings_preserve_explicit_zero_delay(self) -> None:
        from hdzz.api.webui.follow_settings import ensure_follow_settings_defaults, merge_follow_settings

        merged = merge_follow_settings(
            {},
            {
                "market_reject_max_attempts": 0,
                "market_reject_retry_delay_seconds": 0,
            },
        )
        self.assertEqual(merged["market_reject_max_attempts"], 1)
        self.assertEqual(merged["market_reject_retry_delay_seconds"], 0.0)

        normalized = ensure_follow_settings_defaults(merged)
        self.assertEqual(normalized["market_reject_max_attempts"], 1)
        self.assertEqual(normalized["market_reject_retry_delay_seconds"], 0.0)

    def test_merge_market_rules(self) -> None:
        from hdzz.api.webui.follow_settings import merge_follow_settings

        existing = {
            "market_rules": {
                "handicap": {"enabled": True, "ratio_percent": 100},
                "total_over": {"enabled": True, "ratio_percent": 100},
                "total_under": {"enabled": True, "ratio_percent": 100},
            }
        }
        incoming = {
            "market_rules": {
                "handicap": {"enabled": False, "ratio_percent": 75},
                "total_over": {"enabled": True, "ratio_percent": 60},
                "total_under": {"enabled": True, "ratio_percent": 40},
            }
        }
        out = merge_follow_settings(existing, incoming)
        self.assertEqual(out["market_rules"]["handicap"]["enabled"], False)
        self.assertEqual(out["market_rules"]["handicap"]["ratio_percent"], 75)
        self.assertEqual(out["market_rules"]["total_over"]["ratio_percent"], 60)
        self.assertEqual(out["market_rules"]["total_under"]["ratio_percent"], 40)

    def test_merge_follow_odds_limits(self) -> None:
        from hdzz.api.webui.follow_settings import merge_follow_settings

        out = merge_follow_settings(
            {"follow_min_odds": 0.5, "follow_max_odds": 1.8},
            {"follow_min_odds": "0.6", "follow_max_odds": "1.6"},
        )

        self.assertEqual(out["follow_min_odds"], 0.6)
        self.assertEqual(out["follow_max_odds"], 1.6)

    def test_merge_amount_limits(self) -> None:
        from hdzz.api.webui.follow_settings import merge_follow_settings

        out = merge_follow_settings(
            {"match_limit_amount": 5000, "market_limit_amount": 2000},
            {"match_limit_amount": "8000.5", "market_limit_amount": "3500"},
        )

        self.assertEqual(out["match_limit_amount"], 8000.5)
        self.assertEqual(out["market_limit_amount"], 3500.0)

    def test_merge_amount_limits_rejects_negative_and_nonfinite_values(self) -> None:
        from hdzz.api.webui.follow_settings import merge_follow_settings

        out = merge_follow_settings(
            {},
            {"match_limit_amount": -1, "market_limit_amount": "inf"},
        )

        self.assertEqual(out["match_limit_amount"], 0.0)
        self.assertEqual(out["market_limit_amount"], 0.0)

    def test_merge_clears_invalid_follow_odds_limits(self) -> None:
        from hdzz.api.webui.follow_settings import merge_follow_settings

        out = merge_follow_settings(
            {"follow_min_odds": 0.6, "follow_max_odds": 1.6},
            {"follow_min_odds": "-1", "follow_max_odds": "bad"},
        )

        self.assertIsNone(out["follow_min_odds"])
        self.assertIsNone(out["follow_max_odds"])

    def test_merge_reverse_rules(self) -> None:
        from hdzz.api.webui.follow_settings import merge_follow_settings

        existing = {
            "reverse_rules": {
                "all": False,
                "total": False,
                "handicap": False,
            }
        }
        incoming = {
            "reverse_rules": {
                "all": True,
                "total": True,
                "handicap": False,
            }
        }
        out = merge_follow_settings(existing, incoming)
        self.assertEqual(out["reverse_rules"]["all"], True)
        self.assertEqual(out["reverse_rules"]["total"], True)
        self.assertEqual(out["reverse_rules"]["handicap"], False)


if __name__ == "__main__":
    unittest.main()
