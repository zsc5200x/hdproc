import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


class TestSignalPullConfigMerge(unittest.TestCase):
    def test_defaults_include_signal_pull_keys(self) -> None:
        from hdzz.api.webui.signal_pull_config import ensure_signal_pull_config_defaults

        out = ensure_signal_pull_config_defaults({})
        self.assertIn("signal_pull_enabled", out)
        self.assertIn("signal_pull_url", out)
        self.assertIn("signal_pull_token", out)
        self.assertIn("signal_pull_live_only", out)
        self.assertIn("signal_pull_limit", out)

    def test_merge_updates_only_explicit_fields(self) -> None:
        from hdzz.api.webui.signal_pull_config import merge_signal_pull_config

        existing = {
            "signal_pull_enabled": False,
            "signal_pull_url": "http://old/signals/json",
            "signal_pull_token": "",
            "signal_pull_limit": 150,
            "signal_pull_live_only": True,
        }
        incoming = {
            "signal_pull_enabled": True,
            "signal_pull_url": "http://new/signals/json",
            "signal_pull_live_only": False,
        }

        out = merge_signal_pull_config(existing, incoming)
        self.assertEqual(out["signal_pull_enabled"], True)
        self.assertEqual(out["signal_pull_url"], "http://new/signals/json")
        self.assertEqual(out["signal_pull_live_only"], False)
        self.assertEqual(out["signal_pull_limit"], 150)
        self.assertEqual(out["signal_pull_token"], "")

    def test_reset_clears_signal_pull_fields_back_to_defaults(self) -> None:
        from hdzz.api.webui.signal_pull_config import reset_signal_pull_config

        existing = {
            "signal_pull_enabled": True,
            "signal_pull_url": "http://168.93.214.95:8021/signals/json",
            "signal_pull_token": "abc123",
            "signal_pull_limit": 120,
            "signal_pull_live_only": False,
            "signal_pull_interval_seconds": 2.0,
        }

        out = reset_signal_pull_config(existing)
        self.assertEqual(out["signal_pull_enabled"], False)
        self.assertEqual(out["signal_pull_url"], "")
        self.assertEqual(out["signal_pull_token"], "")
        self.assertEqual(out["signal_pull_live_only"], True)
        self.assertEqual(out["signal_pull_limit"], 200)


if __name__ == "__main__":
    unittest.main()
