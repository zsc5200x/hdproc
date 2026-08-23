import json
import runpy
import tempfile
import time
import unittest
from pathlib import Path
PULLER_MOD = runpy.run_path("ook/signal_puller.py", run_name="__puller_test__")
FORWARDER_MOD = runpy.run_path("ook/signal_forwarder.py", run_name="__forwarder_test__")

signal_to_order = PULLER_MOD["_signal_to_order"]
Forwarder = FORWARDER_MOD["Forwarder"]
load_runtime_config = FORWARDER_MOD["load_runtime_config"]


class TestSignalLoopGuard(unittest.TestCase):
    def test_remote_source_order_is_marked_to_skip_forwarding(self) -> None:
        item = {
            "backend_name": "remote_a",
            "order_id": "12345",
            "raw": {
                "source_order": {
                    "orderId": "12345",
                    "target": "zxcv226699",
                    "betType": "(滚球) 让球",
                    "_tg_chat_id": "-100oldchat",
                    "_tg_message_id": 7788,
                    "raw": {"_EVENT": "8576425", "MARKET": "rb"},
                }
            },
        }

        order, _ = signal_to_order(item)

        self.assertIsNotNone(order)
        self.assertTrue(order.get("_skip_signal_forward"))
        self.assertNotIn("_tg_chat_id", order)
        self.assertNotIn("_tg_message_id", order)

    def test_simplified_remote_signal_is_marked_to_skip_forwarding(self) -> None:
        item = {
            "backend_name": "remote_b",
            "order_id": "23456",
            "league": "英超",
            "match": "A v B",
            "bet_type": "(滚球) 大 / 小",
            "line": "大 3.5",
            "amount": 1000,
        }

        order, _ = signal_to_order(item)

        self.assertIsNotNone(order)
        self.assertTrue(order.get("_skip_signal_forward"))

    def test_forwarder_skips_marked_remote_orders(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            orders_dir = root / "orders"
            orders_dir.mkdir(parents=True, exist_ok=True)
            cfg_path = root / "config.json"
            state_path = root / "state.json"
            order_path = orders_dir / "remote_a.jsonl"
            order_path.write_text(
                json.dumps(
                    {
                        "orderId": "12345",
                        "target": "remote_a",
                        "_skip_signal_forward": True,
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            cfg_path.write_text(
                json.dumps(
                    {
                        "signal_webhook_enabled": True,
                        "signal_webhook_url": "http://example.invalid/signals",
                        "signal_webhook_push_history_on_start": True,
                    }
                ),
                encoding="utf-8",
            )
            forwarder = Forwarder(
                orders_dir=orders_dir,
                config_path=cfg_path,
                state_path=state_path,
                interval_default=0.5,
                debug_default=False,
            )
            rc = load_runtime_config(cfg_path, 0.5, False)

            forwarder._send_with_retry = lambda rc, payload: (_ for _ in ()).throw(AssertionError("should not send"))
            ok, changed = forwarder._process_file(order_path, rc)

            self.assertTrue(ok)
            self.assertTrue(changed)
            meta = forwarder.state["files"][str(order_path.resolve())]
            self.assertEqual(int(meta["offset"]), order_path.stat().st_size)

    def test_forwarder_still_sends_local_orders(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            orders_dir = root / "orders"
            orders_dir.mkdir(parents=True, exist_ok=True)
            cfg_path = root / "config.json"
            state_path = root / "state.json"
            order_path = orders_dir / "zxcv226699.jsonl"
            order_path.write_text(
                json.dumps(
                    {
                        "orderId": "98765",
                        "target": "zxcv226699",
                        "league": "英超",
                        "sides": "A v B",
                        "stake": "1000",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            cfg_path.write_text(
                json.dumps(
                    {
                        "signal_webhook_enabled": True,
                        "signal_webhook_url": "http://example.invalid/signals",
                        "signal_webhook_push_history_on_start": True,
                    }
                ),
                encoding="utf-8",
            )
            forwarder = Forwarder(
                orders_dir=orders_dir,
                config_path=cfg_path,
                state_path=state_path,
                interval_default=0.5,
                debug_default=False,
            )
            rc = load_runtime_config(cfg_path, 0.5, False)
            calls = []

            def fake_send_with_retry(rc, payload):
                calls.append((rc.webhook_url, payload))
                return True, "ok"

            forwarder._send_with_retry = fake_send_with_retry
            ok, changed = forwarder._process_file(order_path, rc)

            self.assertTrue(ok)
            self.assertTrue(changed)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][1]["backend_name"], "zxcv226699")

    def test_forwarder_backfills_recent_orders_for_new_file_when_history_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            orders_dir = root / "orders"
            orders_dir.mkdir(parents=True, exist_ok=True)
            cfg_path = root / "config.json"
            state_path = root / "state.json"
            order_path = orders_dir / "zxcv226699.jsonl"
            now_ts = int(time.time())
            order_path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "orderId": "old-1",
                                "target": "zxcv226699",
                                "league": "英超",
                                "sides": "A v B",
                                "stake": "1000",
                                "ts": now_ts - 7200,
                            },
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            {
                                "orderId": "recent-1",
                                "target": "zxcv226699",
                                "league": "英超",
                                "sides": "A v B",
                                "stake": "1000",
                                "ts": now_ts - 30,
                            },
                            ensure_ascii=False,
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            cfg_path.write_text(
                json.dumps(
                    {
                        "signal_webhook_enabled": True,
                        "signal_webhook_url": "http://example.invalid/signals",
                        "signal_webhook_push_history_on_start": False,
                    }
                ),
                encoding="utf-8",
            )
            forwarder = Forwarder(
                orders_dir=orders_dir,
                config_path=cfg_path,
                state_path=state_path,
                interval_default=0.5,
                debug_default=False,
            )
            rc = load_runtime_config(cfg_path, 0.5, False)
            calls = []

            def fake_send_with_retry(rc, payload):
                calls.append((rc.webhook_url, payload))
                return True, "ok"

            forwarder._send_with_retry = fake_send_with_retry
            ok, changed = forwarder._process_file(order_path, rc)

            self.assertTrue(ok)
            self.assertTrue(changed)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][1]["order_id"], "recent-1")


if __name__ == "__main__":
    unittest.main()
