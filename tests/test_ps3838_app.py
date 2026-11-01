import json
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
APP = ROOT / "app"
for path in (SRC, APP):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        body = json.dumps({"ok": True, "serverTime": "2026-11-02T01:00:00+08:00"}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args) -> None:
        return


class TestPs3838App(unittest.TestCase):
    def test_defaults_and_missing_directory_status(self) -> None:
        from hdzz.api.webui.ps3838_app import ensure_ps3838_app_defaults, status

        cfg = ensure_ps3838_app_defaults({})
        self.assertTrue(cfg["ps3838_app_dir"])
        self.assertEqual(cfg["ps3838_app_url"], "http://127.0.0.1:8787")

        with tempfile.TemporaryDirectory() as td:
            cfg["ps3838_app_dir"] = str(Path(td) / "missing")
            cfg["ps3838_app_url"] = "http://127.0.0.1:9"
            out = status(cfg)
        self.assertTrue(out["ok"])
        self.assertFalse(out["running"])
        self.assertFalse(out["dir_exists"])
        self.assertFalse(out["start_script_exists"])
        self.assertEqual(out["embed_url"], "/ps3838/")

    def test_health_check_against_local_server(self) -> None:
        from hdzz.api.webui.ps3838_app import check_health

        server = ThreadingHTTPServer(("127.0.0.1", 0), _HealthHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = check_health(f"http://127.0.0.1:{server.server_port}")
        finally:
            server.shutdown()
            server.server_close()
        self.assertTrue(result.get("ok"))
        self.assertEqual(result.get("serverTime"), "2026-11-02T01:00:00+08:00")

    def test_start_rejects_missing_directory(self) -> None:
        from hdzz.api.webui.ps3838_app import start

        with tempfile.TemporaryDirectory() as td:
            cfg = {"ps3838_app_dir": str(Path(td) / "missing"), "ps3838_app_url": "http://127.0.0.1:9"}
            with self.assertRaises(FileNotFoundError):
                start(cfg, data_dir=Path(td) / "data")

    def test_start_spawns_project_script_and_writes_log(self) -> None:
        from hdzz.api.webui.ps3838_app import start

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            project = root / "ps3838-wss"
            data = root / "data"
            project.mkdir()
            started_marker = project / "started.txt"
            (project / "start.sh").write_text(
                "#!/bin/bash\nprintf 'started\\n' > started.txt\n",
                encoding="utf-8",
            )
            cfg = {"ps3838_app_dir": str(project), "ps3838_app_url": "http://127.0.0.1:9"}
            out = start(cfg, data_dir=data)
            self.assertTrue(out.get("ok"))
            self.assertTrue(out.get("started"))
            deadline = time.time() + 5
            while time.time() < deadline and not started_marker.exists():
                time.sleep(0.05)
            self.assertTrue(started_marker.exists())
            self.assertTrue((data / "ps3838_app_start.log").exists())

    def test_proxy_path_mapping(self) -> None:
        from hdzz.api.webui.server import Handler

        self.assertEqual(Handler._ps3838_upstream_path("/ps3838"), "/")
        self.assertEqual(Handler._ps3838_upstream_path("/ps3838/"), "/")
        self.assertEqual(Handler._ps3838_upstream_path("/ps3838/api/health"), "/api/health")
        self.assertEqual(Handler._ps3838_upstream_path("/ps3838/static/app.js"), "/static/app.js")
        self.assertEqual(Handler._ps3838_upstream_path("/crown"), "/crown")
        self.assertEqual(Handler._ps3838_upstream_path("/api/crown/events"), "/api/crown/events")
        self.assertEqual(Handler._ps3838_upstream_path("/api/merge/confirmed"), "/api/merge/confirmed")
        self.assertIsNone(Handler._ps3838_upstream_path("/api/status"))

    def test_webui_exposes_ps3838_app_button_and_routes(self) -> None:
        page = (ROOT / "app" / "webui" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "app" / "webui" / "app.js").read_text(encoding="utf-8")
        server = (ROOT / "src" / "hdzz" / "api" / "webui" / "server.py").read_text(encoding="utf-8")

        self.assertIn('data-view="ps3838"', page)
        self.assertIn("PS38赛事", page)
        self.assertIn('id="ps3838-app-frame"', page)
        self.assertIn('href="ps3838_app.css', page)
        self.assertIn("loadPs3838AppStatus", script)
        self.assertIn("startPs3838App", script)
        self.assertIn('"/api/ps3838-app/status"', server)
        self.assertIn('"/api/ps3838-app/start"', server)
        self.assertIn('"/api/ps3838-app/stop"', server)
        self.assertIn('PS3838_PROXY_PREFIX = "/ps3838"', server)
        self.assertIn('def _proxy_ps3838', server)
        self.assertIn('embed_url', script)
        launcher = (ROOT / "app" / "app.py").read_text(encoding="utf-8")
        self.assertIn("def start_ps3838_stack", launcher)
        self.assertIn("def stop_ps3838_stack", launcher)
        self.assertIn("start-ps3838", launcher)


if __name__ == "__main__":
    unittest.main()
