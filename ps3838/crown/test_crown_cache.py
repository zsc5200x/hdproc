import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import crown_runtime


class CrownCacheTests(unittest.TestCase):
    def setUp(self):
        self.previous_state = copy.deepcopy(crown_runtime.state)
        self.addCleanup(self.restore_state)

    def restore_state(self):
        with crown_runtime.lock:
            crown_runtime.state.clear()
            crown_runtime.state.update(copy.deepcopy(self.previous_state))

    def test_snapshot_round_trip_uses_owner_only_atomic_file(self):
        event = {
            "id": "123",
            "league": "测试联赛",
            "home": "主队",
            "away": "客队",
            "markets": {"handicap": [], "total": [], "moneyline": []},
        }
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            cache_path = data_dir / "events-cache.json"
            with patch.object(crown_runtime, "DATA_DIR", data_dir), \
                    patch.object(crown_runtime, "EVENTS_CACHE_PATH", cache_path):
                crown_runtime._write_event_cache(
                    [event], "2026-10-28T20:00:00+08:00"
                )
                self.assertEqual(cache_path.stat().st_mode & 0o777, 0o600)
                self.assertFalse(list(data_dir.glob(".events-cache-*.tmp")))

                with crown_runtime.lock:
                    crown_runtime.state.update({
                        "events": [], "leagues": [], "updated_at": None,
                        "status": "未登录", "error": None, "cached": False,
                    })
                self.assertTrue(crown_runtime._load_event_cache())

        self.assertEqual(crown_runtime.state["events"], [event])
        self.assertEqual(crown_runtime.state["leagues"], ["测试联赛"])
        self.assertEqual(crown_runtime.state["updated_at"], "2026-10-28T20:00:00+08:00")
        self.assertEqual(crown_runtime.state["status"], "缓存数据")
        self.assertTrue(crown_runtime.state["cached"])

    def test_corrupt_cache_is_ignored_without_crashing_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "events-cache.json"
            cache_path.write_text("{not-json", encoding="utf-8")
            os.chmod(cache_path, 0o600)
            with crown_runtime.lock:
                crown_runtime.state.update({
                    "events": [], "leagues": [], "updated_at": None,
                    "status": "未登录", "error": None, "cached": False,
                    "diagnostics": {},
                })
            with patch.object(crown_runtime, "EVENTS_CACHE_PATH", cache_path):
                self.assertFalse(crown_runtime._load_event_cache())

        self.assertEqual(crown_runtime.state["events"], [])
        self.assertFalse(crown_runtime.state["cached"])
        self.assertTrue(crown_runtime.state["diagnostics"]["cache_load_failed"])

    def test_cache_rejects_invalid_event_shape(self):
        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "events-cache.json"
            cache_path.write_text(json.dumps({
                "version": 1,
                "updated_at": "2026-10-28T20:00:00+08:00",
                "events": [{"id": "123", "league": "测试联赛"}],
            }), encoding="utf-8")
            with patch.object(crown_runtime, "EVENTS_CACHE_PATH", cache_path):
                self.assertFalse(crown_runtime._load_event_cache())

        self.assertFalse(crown_runtime.state["cached"])


if __name__ == "__main__":
    unittest.main()
