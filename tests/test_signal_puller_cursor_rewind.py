import json
import runpy
import tempfile
import unittest
from pathlib import Path


MOD = runpy.run_path("ook/signal_puller.py", run_name="__puller_rewind_test__")
SignalPuller = MOD["SignalPuller"]
load_runtime_config = MOD["load_runtime_config"]


class TestSignalPullerCursorRewind(unittest.TestCase):
    def test_tick_recovers_when_remote_signal_ids_rewind(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            orders_dir = root / "orders"
            orders_dir.mkdir(parents=True, exist_ok=True)
            config_path = root / "config.json"
            state_path = root / ".run" / "ook_signal_puller_state.json"
            state_path.parent.mkdir(parents=True, exist_ok=True)

            config_path.write_text(
                json.dumps(
                    {
                        "signal_pull_enabled": True,
                        "signal_pull_url": "http://example.com/signals/json",
                        "signal_pull_live_only": False,
                        "signal_pull_push_history_on_start": False,
                    }
                ),
                encoding="utf-8",
            )
            state_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "last_signal_id": 1519,
                        "seen_keys": [],
                    }
                ),
                encoding="utf-8",
            )

            puller = SignalPuller(
                orders_dir=orders_dir,
                config_path=config_path,
                state_path=state_path,
                interval_default=1.0,
                debug_default=False,
            )
            rc = load_runtime_config(config_path, 1.0, False)

            entries = [
                {
                    "id": 674,
                    "backend_name": "remote_a",
                    "order_id": "rewind-674",
                    "league": "英超",
                    "match": "A v B",
                    "bet_type": "让球",
                    "line": "主 -0.5",
                    "amount": 1000,
                },
                {
                    "id": 675,
                    "backend_name": "remote_a",
                    "order_id": "rewind-675",
                    "league": "英超",
                    "match": "C v D",
                    "bet_type": "大小",
                    "line": "大 2.5",
                    "amount": 1000,
                },
            ]

            globals_dict = SignalPuller.tick.__globals__
            original_fetch = globals_dict["_fetch_json"]
            try:
                globals_dict["_fetch_json"] = lambda *args, **kwargs: (True, entries, "")
                puller.tick(rc)
            finally:
                globals_dict["_fetch_json"] = original_fetch

            out_file = orders_dir / "remote_a.jsonl"
            self.assertTrue(out_file.is_file())
            rows = [json.loads(line) for line in out_file.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertEqual([row["orderId"] for row in rows], ["rewind-674", "rewind-675"])
            self.assertEqual(puller.last_signal_id, 675)


if __name__ == "__main__":
    unittest.main()
