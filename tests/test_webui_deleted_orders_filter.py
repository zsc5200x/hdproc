import json
import tempfile
import time
import unittest
from pathlib import Path


class TestWebuiDeletedOrdersFilter(unittest.TestCase):
    def test_save_and_load_deleted_orders(self) -> None:
        from hdzz.api.webui.server import load_deleted_order_ids, save_deleted_order_ids

        with tempfile.TemporaryDirectory() as td:
            data_dir = Path(td) / "data"
            data_dir.mkdir(parents=True, exist_ok=True)

            save_deleted_order_ids(data_dir, {"234001", "234002", "234001"})
            loaded = load_deleted_order_ids(data_dir)
            self.assertEqual(loaded, {"234001", "234002"})

    def test_metrics_and_settlement_skip_deleted_orders(self) -> None:
        from hdzz.api.webui.server import build_follow_report_metrics, build_follow_settlement_status_map

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            orders_dir = root / "orders"
            data_dir = root / "data"
            settlements_dir = data_dir / "settlements"
            orders_dir.mkdir(parents=True, exist_ok=True)
            settlements_dir.mkdir(parents=True, exist_ok=True)

            now_ts = int(time.time())
            (orders_dir / "hx.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps({"orderId": "A100", "ts": now_ts}),
                        json.dumps({"orderId": "B200", "ts": now_ts}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            (data_dir / "follow_results.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps({"orderId": "A100", "ok": True, "ticket_id": "9001", "stake": 50, "ts": now_ts}),
                        json.dumps({"orderId": "B200", "ok": True, "ticket_id": "9002", "stake": 50, "ts": now_ts}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            (settlements_dir / "wagers_2026-02-13.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "ticket_id": "9001",
                                "status": "settled",
                                "profit": 10,
                                "is_final": True,
                                "source": "history_switch",
                                "ts": now_ts,
                            }
                        ),
                        json.dumps(
                            {
                                "ticket_id": "9002",
                                "status": "settled",
                                "profit": -10,
                                "is_final": True,
                                "source": "history_switch",
                                "ts": now_ts,
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            metrics_all = build_follow_report_metrics(orders_dir, data_dir)
            self.assertEqual(metrics_all["total_order_count"], 2)
            self.assertEqual(metrics_all["total_settled_orders"], 2)
            self.assertAlmostEqual(float(metrics_all["total_pnl"]), 0.0, places=6)

            deleted = {"B200"}
            metrics_filtered = build_follow_report_metrics(
                orders_dir,
                data_dir,
                deleted_order_ids=deleted,
            )
            self.assertEqual(metrics_filtered["total_order_count"], 1)
            self.assertEqual(metrics_filtered["total_settled_orders"], 1)
            self.assertEqual(metrics_filtered["settled_ticket_count"], 1)
            self.assertAlmostEqual(float(metrics_filtered["total_pnl"]), 10.0, places=6)

            status_map = build_follow_settlement_status_map(
                data_dir,
                ["A100", "B200"],
                deleted_order_ids=deleted,
            )
            self.assertIn("A100", status_map)
            self.assertNotIn("B200", status_map)


if __name__ == "__main__":
    unittest.main()
