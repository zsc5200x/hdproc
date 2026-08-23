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


class TestFollowOddsLimits(unittest.TestCase):
    def test_skips_when_odds_below_min(self) -> None:
        from hdzz.core.follower.engine import evaluate_follow_odds_limit

        skipped, message, meta = evaluate_follow_odds_limit(
            settings={"follow_min_odds": 0.6, "follow_max_odds": 1.6},
            ioratio="0.59",
        )

        self.assertTrue(skipped)
        self.assertIn("低于", message)
        self.assertEqual(meta["current_odds"], 0.59)
        self.assertEqual(meta["min_odds"], 0.6)

    def test_skips_when_odds_above_max(self) -> None:
        from hdzz.core.follower.engine import evaluate_follow_odds_limit

        skipped, message, meta = evaluate_follow_odds_limit(
            settings={"follow_min_odds": 0.6, "follow_max_odds": 1.6},
            ioratio="1.61",
        )

        self.assertTrue(skipped)
        self.assertIn("高于", message)
        self.assertEqual(meta["current_odds"], 1.61)
        self.assertEqual(meta["max_odds"], 1.6)

    def test_allows_when_odds_within_range(self) -> None:
        from hdzz.core.follower.engine import evaluate_follow_odds_limit

        skipped, message, meta = evaluate_follow_odds_limit(
            settings={"follow_min_odds": 0.6, "follow_max_odds": 1.6},
            ioratio="0.88",
        )

        self.assertFalse(skipped)
        self.assertEqual(message, "")
        self.assertEqual(meta["current_odds"], 0.88)

    def test_allows_when_limits_not_configured(self) -> None:
        from hdzz.core.follower.engine import evaluate_follow_odds_limit

        skipped, message, meta = evaluate_follow_odds_limit(
            settings={},
            ioratio="0.52",
        )

        self.assertFalse(skipped)
        self.assertEqual(message, "")
        self.assertEqual(meta["min_odds"], None)
        self.assertEqual(meta["max_odds"], None)
