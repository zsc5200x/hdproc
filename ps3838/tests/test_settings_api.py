import json
import os
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, ProxyHandler, build_opener
from unittest.mock import patch

from dashboard_server import DashboardHandler, OddsStore


class SettingsApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config_path = self.root / "config.json"
        self.config_path.write_text(
            json.dumps(
                {
                    "cdp_url": "http://127.0.0.1:9222",
                    "chrome_profile_dir": "~/.test-profile",
                    "start_url": "https://www.ps3838.com/zh-cn/sports/soccer",
                    "target_hosts": ["ps3838.com"],
                    "capture_path": str(self.root / "capture.jsonl"),
                    "live_snapshot_file": str(self.root / "latest.csv"),
                    "dashboard_db_path": str(self.root / "history.sqlite3"),
                    "backup_dir": str(self.root / "backups"),
                    "presence_interval_seconds": 5,
                    "snapshot_interval_seconds": 2,
                    "capture_rotation_mb": 512,
                    "capture_compress_after_hours": 24,
                    "capture_compress_min_mb": 16,
                    "backup_keep": 7,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.config = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.store = OddsStore(self.config)
        self.addCleanup(self.store.close)
        self.metadata_runner = SimpleNamespace(
            status=lambda: {
                "running": False,
                "current": {"events": 0, "missing": 0},
            }
        )

        self.old_project_dir = __import__("dashboard_server").PROJECT_DIR
        self.old_handler_values = (
            getattr(DashboardHandler, "store", None),
            getattr(DashboardHandler, "metadata_runner", None),
            getattr(DashboardHandler, "config", None),
        )
        self.env_patch = patch.dict(
            os.environ,
            {"PS3838_CONFIG": str(self.config_path)},
            clear=False,
        )
        self.env_patch.start()
        # Config backups are intentionally written beneath PROJECT_DIR; point
        # that location at the isolated test directory.
        import dashboard_server

        dashboard_server.PROJECT_DIR = self.root
        DashboardHandler.store = self.store
        DashboardHandler.metadata_runner = self.metadata_runner
        DashboardHandler.config = self.config
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), DashboardHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        time.sleep(0.05)
        self.addCleanup(self._close_server)

    def _close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        import dashboard_server

        dashboard_server.PROJECT_DIR = self.old_project_dir
        DashboardHandler.store, DashboardHandler.metadata_runner, DashboardHandler.config = (
            self.old_handler_values
        )
        self.env_patch.stop()

    def _request(self, method, path, payload=None):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(
            f"http://127.0.0.1:{self.server.server_port}{path}",
            data=data,
            method=method,
            headers={"Content-Type": "application/json"} if data else {},
        )
        try:
            with build_opener(ProxyHandler({})).open(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_get_settings_returns_schema_and_paths_without_writable_connection_fields(self):
        status, payload = self._request("GET", "/api/settings")

        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["effective"]["presence_interval_seconds"], 5)
        self.assertEqual(payload["effective"]["capture_rotation_mb"], 512)
        self.assertTrue(payload["effective"]["crown_extra_markets_enabled"])
        self.assertIn("presence_interval_seconds", payload["schema"])
        self.assertEqual(payload["schema"]["crown_extra_markets_enabled"]["type"], "boolean")
        self.assertIn("capture_path", payload["readonly"])
        self.assertEqual(payload["readonly"]["crown_service_url"], "http://127.0.0.1:9223")
        self.assertNotIn("cdp_url", payload["effective"])
        self.assertNotIn("password", json.dumps(payload, ensure_ascii=False).lower())

    def test_put_validates_whitelist_and_ranges_then_writes_atomically(self):
        status, payload = self._request(
            "PUT",
            "/api/settings",
            {"presence_interval_seconds": 10, "backup_keep": 9},
        )

        self.assertEqual(status, 200)
        self.assertEqual(payload["changed"], {"presence_interval_seconds": 10, "backup_keep": 9})
        self.assertEqual(payload["restartRequired"], ["presence_interval_seconds"])
        saved = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["presence_interval_seconds"], 10)
        self.assertEqual(saved["backup_keep"], 9)
        self.assertTrue(payload["backupPath"])
        self.assertTrue(Path(payload["backupPath"]).is_file())
        self.assertEqual(list(self.root.glob(".config.json.*.tmp")), [])

        status, unchanged = self._request(
            "PUT",
            "/api/settings",
            {"presence_interval_seconds": 10},
        )
        self.assertEqual(status, 200)
        self.assertEqual(unchanged["changed"], {})
        self.assertIsNone(unchanged["backupPath"])

    def test_put_rejects_readonly_and_out_of_range_fields(self):
        status, payload = self._request(
            "PUT", "/api/settings", {"cdp_url": "http://evil.invalid"}
        )
        self.assertEqual(status, 400)
        self.assertIn("不允许修改", payload["error"])

        status, payload = self._request(
            "PUT", "/api/settings", {"presence_interval_seconds": 4}
        )
        self.assertEqual(status, 400)
        self.assertIn("5 到 60", payload["error"])

    def test_crown_extra_market_switch_persists_without_restart(self):
        status, payload = self._request(
            "PUT", "/api/settings", {"crown_extra_markets_enabled": False}
        )

        self.assertEqual(status, 200)
        self.assertEqual(payload["changed"], {"crown_extra_markets_enabled": False})
        self.assertEqual(payload["restartRequired"], [])
        self.assertFalse(payload["effective"]["crown_extra_markets_enabled"])
        saved = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertIs(saved["crown_extra_markets_enabled"], False)

    def test_crown_extra_market_switch_rejects_non_boolean_values(self):
        status, payload = self._request(
            "PUT", "/api/settings", {"crown_extra_markets_enabled": "false"}
        )

        self.assertEqual(status, 400)
        self.assertIn("必须是布尔值", payload["error"])

    def test_backup_and_integrity_endpoints_return_predictable_results(self):
        self.store.flush_history()

        status, payload = self._request("POST", "/api/settings/backup", {})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertTrue(Path(payload["backup"]["path"]).is_file())

        status, payload = self._request("POST", "/api/settings/integrity", {})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["result"], "ok")

    def test_integrity_endpoint_checks_live_wal_instead_of_immutable_snapshot(self):
        # The dashboard database has uncheckpointed WAL pages while collection
        # is running; immutable=1 would produce false corruption errors.
        with patch("dashboard_server.verify_database", return_value="ok") as verify:
            status, payload = self._request("POST", "/api/settings/integrity", {})

        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        verify.assert_called_once()
        self.assertIs(verify.call_args.kwargs.get("immutable"), False)
        self.assertIs(verify.call_args.kwargs.get("quick"), True)


if __name__ == "__main__":
    unittest.main()
