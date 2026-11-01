import json
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from local_signal_push import (
    LocalSignalPushError,
    LocalSignalPusher,
    _base_key,
    build_payload,
)


def row(**overrides):
    value = {
        "agent_code": "TLH0R03",
        "member_user_code": "TLH0R03MH3",
        "order_id": "3831870304",
        "league": "墨西哥 - 足球甲级联赛",
        "teams": "蒙特雷-vs-蒂华纳国民足球俱乐部",
        "market": "大小 6.00",
        "selection": "小",
        "handicap": "6.00",
        "odds": 1.16,
        "stake": 2569,
        "sport": "足球",
        "inplay": 1,
        "created_ms": 1793500285000,
        "event_ms": 1793495100000,
        "score": "3-2",
        "raw_json": json.dumps({"choices": [{"eventDate": 1793495100000}]}),
    }
    value.update(overrides)
    return value


class LocalSignalPushTests(unittest.TestCase):
    def test_missing_gid_is_fail_closed(self):
        with self.assertRaisesRegex(LocalSignalPushError, "missing HD/HGA gid"):
            build_payload(row())

    def test_gid_resolver_is_used_and_event_date_is_not_gid(self):
        payload = build_payload(row(), gid_resolver=lambda _row: "HGA-123")
        self.assertEqual(payload["raw"]["_EVENT"], "HGA-123")
        self.assertEqual(payload["raw"]["GID"], "HGA-123")
        self.assertNotEqual(payload["raw"]["GID"], str(row()["event_ms"]))
        self.assertEqual(payload["betType"], "(滚球) 大小")
        self.assertEqual(payload["line"], "小 6")

    def test_order_id_keeps_agent_member_and_source_id(self):
        payload = build_payload(row(), gid_resolver=lambda _row: "HGA-123")
        self.assertEqual(payload["orderId"], "ps3838:TLH0R03:TLH0R03MH3:3831870304")
        self.assertEqual(payload["raw"]["source_order_id"], "3831870304")

    def test_group_key_matches_dashboard_20_second_semantics(self):
        first = row()
        second = row(order_id="3831870305", created_ms=1793500295000)
        self.assertEqual(_base_key(first), _base_key(second))
        self.assertNotEqual(_base_key(first), _base_key(row(score="4-2")))

    def test_out_of_order_old_event_does_not_join_newer_group(self):
        first = row(event_id=1, created_ms=1793500300000)
        delayed = row(order_id="old", event_id=2, created_ms=1793500200000)
        with TemporaryDirectory() as td:
            pusher = LocalSignalPusher()
            pusher._open_state(Path(td) / "state.sqlite3")
            pusher._enqueue(first)
            pusher._enqueue(delayed)
            self.assertEqual(len(pusher._active), 2)
            pusher._db.close()

    def test_disabled_bridge_does_not_advance_or_discard_partner_cursor(self):
        with TemporaryDirectory() as td:
            pusher = LocalSignalPusher()
            pusher._open_state(Path(td) / "state.sqlite3")
            source = row(event_id=27)
            pusher._cursor = 9
            with patch.object(pusher, "_config", return_value={
                "enabled": False, "state_db": str(Path(td) / "state.sqlite3"),
                "gid_map_file": "", "partner_db": "unused",
            }), patch.object(pusher, "_fetch", return_value=[source]):
                pusher.poll_once()
            self.assertEqual(pusher._cursor, 9)
            self.assertEqual(pusher._db.execute("SELECT COUNT(*) FROM pending_events").fetchone()[0], 0)
            pusher._db.close()

    def test_mapping_key_resolves_hga_gid(self):
        source = row()
        mapping = {
            "TLH0R03:TLH0R03MH3:3831870304": {"gid": "HGA-456"}
        }
        gid = LocalSignalPusher._mapping_gid(source, mapping)
        self.assertEqual(gid, "HGA-456")

    def test_pending_events_survive_restart_with_stable_signal_id(self):
        source = row(event_id=17)
        with TemporaryDirectory() as td:
            db_path = Path(td) / "state.sqlite3"
            first = LocalSignalPusher()
            first._open_state(db_path)
            first._enqueue(source)
            first._stage_event(source)
            signal_id = next(iter(first._active.values()))["signal_id"]
            first._save_cursor()
            first._db.close()
            first._db = None

            restarted = LocalSignalPusher()
            restarted._open_state(db_path)
            restarted._load_pending_events()
            self.assertEqual(restarted._cursor, 0)  # staging precedes cursor persistence
            self.assertEqual(len(restarted._active), 1)
            self.assertEqual(next(iter(restarted._active.values()))["signal_id"], signal_id)
            self.assertEqual(next(iter(restarted._active.values()))["rows"][0]["event_id"], 17)
            restarted._db.close()

    def test_written_order_jsonl_repairs_missing_sqlite_dedupe_row(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            orders = root / "orders"
            orders.mkdir()
            order_path = orders / "ps3838.jsonl"
            payload = {
                "target": "ps3838", "orderId": "signal-1", "gid": "HGA-1",
                "betType": "让球", "line": "主 +0.5", "odds": "0.9",
                "league": "测试联赛", "sides": "主队 vs 客队", "stake": "100",
            }
            internal = LocalSignalPusher._internal_order(payload)
            order_path.write_text(json.dumps(internal) + "\n", encoding="utf-8")

            pusher = LocalSignalPusher()
            pusher._open_state(root / "state.sqlite3")
            result = pusher._write_order({"orders_dir": str(orders)}, payload)

            self.assertTrue(result["duplicate"])
            self.assertEqual(len(order_path.read_text(encoding="utf-8").splitlines()), 1)
            pusher._db.close()


if __name__ == "__main__":
    unittest.main()
