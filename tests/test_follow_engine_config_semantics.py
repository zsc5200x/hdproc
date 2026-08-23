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


class TestFollowEngineConfigSemantics(unittest.TestCase):
    def test_setting_or_default_preserves_explicit_zero(self) -> None:
        from hdzz.core.follower.engine import _setting_or_default

        settings = {
            "retry_max_attempts": 0,
            "retry_delay_seconds": 0,
            "retry_max_seconds": 0,
        }

        self.assertEqual(_setting_or_default(settings, "retry_max_attempts", 5), 0)
        self.assertEqual(_setting_or_default(settings, "retry_delay_seconds", 20.0), 0)
        self.assertEqual(_setting_or_default(settings, "retry_max_seconds", 25.0), 0)

    def test_setting_or_default_uses_default_for_missing_or_blank_values(self) -> None:
        from hdzz.core.follower.engine import _setting_or_default

        settings = {"blank": "   "}

        self.assertEqual(_setting_or_default(settings, "missing", 5), 5)
        self.assertEqual(_setting_or_default(settings, "blank", 20.0), 20.0)

