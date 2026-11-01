"""Regression checks for provider boundaries, source freshness and ownership."""
import importlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import dashboard_server
from dashboard_server import DashboardHandler, build_server
from src.providers.pinnacle import parser
from src.services.source_freshness import crown_freshness, pinnacle_freshness


ROOT = Path(__file__).resolve().parent.parent


class SharedParserTests(unittest.TestCase):
    def test_live_snapshot_import_does_not_load_configuration_or_touch_files(self):
        with tempfile.TemporaryDirectory() as directory:
            env = {**os.environ, "PS3838_CONFIG": str(Path(directory) / "missing.json")}
            result = subprocess.run(
                [sys.executable, "-c", "import live_snapshot; assert live_snapshot.CAPTURE is None"],
                cwd=ROOT, env=env, capture_output=True, text=True, timeout=5,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "")
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_consumers_share_complete_parser_and_decode_nested_json(self):
        import live_snapshot
        import metadata_sync
        self.assertIs(dashboard_server.iter_event_records, parser.iter_event_records)
        self.assertIs(metadata_sync.iter_event_records, parser.iter_event_records)
        self.assertIs(live_snapshot.iter_event_records, parser.iter_event_records)
        event = [123, "Home", "Away", 0, 1790000000000, None, None, None, None, [2, 1], None, None, None, None, None, 12, "2H", "O"]
        container = [[29, "Soccer", [[7, "League", [event]]]]]
        message = {"type": "UPDATE_ODDS", "odds": {"l": container}}
        decoded = list(parser.iter_messages({"payload": json.dumps({"nested": [json.dumps(message)]})}))
        self.assertEqual(decoded, [message])
        result = list(parser.iter_event_records(decoded[0]["odds"]["l"]))[0]
        self.assertEqual((result["scoreHome"], result["scoreAway"], result["matchMinute"], result["matchPeriod"]), ("2", "1", "12", "2H"))
        self.assertEqual(list(live_snapshot.iter_events(container))[0][:3], ("123", "Home", "Away"))

    def test_service_tools_do_not_import_http_entrypoint(self):
        for relative in ("metadata_sync.py", "backfill_history.py", "src/services/odds_store.py"):
            self.assertNotIn("from dashboard_server import", (ROOT / relative).read_text())


class SourceFreshnessTests(unittest.TestCase):
    def test_upstream_failure_preserves_actual_cache_timestamp(self):
        source = crown_freshness({
            "updated_at": "2026-10-28T03:00:00+08:00", "cached": True,
            "status": "会话同步中", "error": "旧会话已失效",
            "diagnostics": {"consecutive_failures": 2},
        })
        self.assertEqual(source["dataUpdatedAt"], "2026-10-28T03:00:00+08:00")
        self.assertTrue(source["cached"])
        self.assertEqual(source["error"], "旧会话已失效")
        self.assertEqual(source["diagnostics"]["consecutive_failures"], 2)
        unavailable = crown_freshness(error="unreachable")
        self.assertIsNone(unavailable["dataUpdatedAt"])
        self.assertEqual(unavailable["status"], "unavailable")

    def test_pinnacle_timestamp_is_capture_time_not_server_response_time(self):
        source = pinnacle_freshness({"serverTime": "new", "freshness": "stale", "lagSeconds": 120, "source": {"lastCapturedAt": "old"}})
        self.assertEqual(source["dataUpdatedAt"], "old")
        self.assertTrue(source["cached"])
        self.assertEqual(source["diagnostics"]["lagSeconds"], 120)

    def test_merge_endpoints_surface_upstream_metadata_and_clear_missing_markets(self):
        handler = object.__new__(DashboardHandler)
        handler.config = {"crown_host": "127.0.0.1", "crown_port": 12345}
        handler.store = Mock()
        handler.store.snapshot.return_value = {"events": [], "freshness": "no-data", "source": {}}
        handler.store.merge_events.return_value = []
        handler.store.event_details.return_value = None
        handler.merge_store = Mock()
        handler.merge_store.list_mappings.return_value = []
        handler.merge_store.list_league_mappings.return_value = []
        upstream = {"events": [], "updated_at": "2026-10-28T03:00:00+08:00", "cached": True, "status": "会话同步中", "error": "旧会话", "diagnostics": {"last_failure_reason": "session_conflict"}}
        response = Mock(status=200)
        response.read.return_value = json.dumps(upstream).encode()
        connection = Mock()
        connection.getresponse.return_value = response
        with patch("dashboard_server.http.client.HTTPConnection", return_value=connection) as connection_factory:
            payload = handler._merge_payload()
        connection_factory.assert_called_once_with("127.0.0.1", 12345, timeout=4)
        self.assertEqual(payload["sources"]["crown"]["dataUpdatedAt"], upstream["updated_at"])
        self.assertEqual(payload["crownError"], upstream["error"])
        self.assertEqual(payload["generatedAt"], payload["updatedAt"])
        self.assertNotEqual(payload["generatedAt"], upstream["updated_at"])
        handler.merge_store.list_mappings.return_value = [{"pinnacleId": "p1", "crownId": "c1", "crownSnapshot": {"name": "saved", "markets": {"moneyline": [1, 2, 3]}, "extra_markets": {"total": [4]}}}]
        with patch.object(handler, "_crown_fixture_events", return_value=([], "unreachable")):
            result = handler._merge_confirmed_payload({})
        self.assertEqual(result["items"][0]["crown"]["markets"], {})
        self.assertEqual(result["items"][0]["crown"]["extra_markets"], {})
        self.assertEqual(result["items"][0]["pinnacle"]["odds"], [])
        self.assertIn("sources", result)


class DashboardLifecycleTests(unittest.TestCase):
    def config(self, directory):
        root = Path(directory)
        return {"capture_path": str(root / "capture.jsonl"), "live_snapshot_file": str(root / "snapshot.csv"), "dashboard_db_path": str(root / "history.sqlite3"), "merge_db_path": str(root / "merge.sqlite3"), "run_dir": str(root / "run"), "log_dir": str(root / "logs")}

    def test_servers_own_separate_dependencies_and_join_workers_on_close(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            a = build_server("127.0.0.1", 0, self.config(first))
            b = build_server("127.0.0.1", 0, self.config(second))
            try:
                self.assertIsNot(a.odds_store, b.odds_store)
                self.assertIsNot(a.config_lock, b.config_lock)
                self.assertNotEqual(a.merge_store.path, b.merge_store.path)
                self.assertTrue(a.capture_tailer.is_alive())
                a.server_close()
                self.assertFalse(a.capture_tailer.is_alive())
                self.assertFalse(a.odds_store.history_writer.is_alive())
                self.assertTrue(b.capture_tailer.is_alive())
                self.assertTrue(b.odds_store.history_writer.is_alive())
            finally:
                a.server_close()
                b.server_close()
            self.assertFalse(b.capture_tailer.is_alive())
            self.assertFalse(b.odds_store.history_writer.is_alive())

    def test_failed_startup_closes_any_started_history_writer(self):
        from src.services.odds_store import OddsStore
        created = []
        def make_store(config):
            store = OddsStore(config)
            created.append(store)
            return store
        with tempfile.TemporaryDirectory() as directory:
            with patch("dashboard_server.OddsStore", side_effect=make_store), patch.object(OddsStore, "load_seed", side_effect=RuntimeError("broken seed")):
                with self.assertRaisesRegex(RuntimeError, "broken seed"):
                    build_server("127.0.0.1", 0, self.config(directory))
        self.assertEqual(len(created), 1)
        self.assertFalse(created[0].history_writer.is_alive())
