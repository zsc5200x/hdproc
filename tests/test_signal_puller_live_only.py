import json
import runpy
import tempfile
import unittest
from pathlib import Path


MOD = runpy.run_path("ook/signal_puller.py", run_name="__not_main__")
is_live_signal = MOD["is_live_signal"]
load_runtime_config = MOD["load_runtime_config"]


class TestSignalPullerLiveOnly(unittest.TestCase):
    def test_is_live_signal_with_market_text(self) -> None:
        item = {"market": "(滚球) 让球"}
        self.assertTrue(is_live_signal(item))

    def test_is_live_signal_with_raw_market_code(self) -> None:
        item = {"raw": {"MARKET": "rb"}}
        self.assertTrue(is_live_signal(item))

    def test_is_live_signal_false_for_non_live(self) -> None:
        item = {
            "market": "早盘 让球",
            "bet_type": "让球",
            "raw": {"MARKET": "ft", "WAGERSTYPE": "让球"},
        }
        self.assertFalse(is_live_signal(item))

    def test_signal_pull_live_only_default_true(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "config.json"
            cfg.write_text(json.dumps({"signal_pull_enabled": True}), encoding="utf-8")
            rc = load_runtime_config(cfg, 1.0, False)
            self.assertTrue(rc.live_only)

    def test_signal_pull_live_only_can_disable(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "config.json"
            cfg.write_text(json.dumps({"signal_pull_enabled": True, "signal_pull_live_only": False}), encoding="utf-8")
            rc = load_runtime_config(cfg, 1.0, False)
            self.assertFalse(rc.live_only)


if __name__ == "__main__":
    unittest.main()
