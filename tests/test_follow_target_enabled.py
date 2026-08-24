import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


class TestFollowTargetEnabled(unittest.TestCase):
    def test_global_only_live_switch_overrides_target_rules(self) -> None:
        from hdzz.core.follower.engine import target_only_live

        target = "ccvv445566"
        self.assertTrue(target_only_live({"only_live": True, "targets": [{"name": target, "only_live": False}]}, target))
        self.assertFalse(target_only_live({"only_live": False, "targets": [{"name": target, "only_live": True}]}, target))

    def test_legacy_missing_global_switch_keeps_target_rule(self) -> None:
        from hdzz.core.follower.engine import target_only_live

        self.assertTrue(target_only_live({"targets": [{"name": "legacy", "only_live": True}]}, "legacy"))

    def test_target_enabled_defaults_to_true(self) -> None:
        from hdzz.core.follower.engine import target_follow_enabled

        self.assertTrue(target_follow_enabled({}, "ccvv445566"))
        self.assertTrue(target_follow_enabled({"targets": [{"name": "ccvv445566"}]}, "ccvv445566"))

    def test_target_enabled_false_blocks_target(self) -> None:
        from hdzz.core.follower.engine import target_follow_enabled

        settings = {"targets": [{"name": "ccvv445566", "enabled": False}]}

        self.assertFalse(target_follow_enabled(settings, "ccvv445566"))
        self.assertTrue(target_follow_enabled(settings, "sg121212"))


if __name__ == "__main__":
    unittest.main()
