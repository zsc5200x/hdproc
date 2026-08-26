import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


class _FakeProc:
    def __init__(self, pid: int) -> None:
        self.pid = pid


class TestOddsSnapshotWorkerBootstrap(unittest.TestCase):
    def test_core_workers_include_shared_snapshot_worker(self) -> None:
        root = Path(__file__).resolve().parents[1]
        app_dir = root / "app"
        if str(app_dir) not in sys.path:
            sys.path.insert(0, str(app_dir))
        spec = importlib.util.spec_from_file_location("hdzz_app_main_snapshot_worker", app_dir / "app.py")
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)

        workers = module.workers("127.0.0.1", 9999, 10, 5)
        worker = next((item for item in workers if item.name == "odds_snapshot_watch"), None)
        self.assertIsNotNone(worker)
        self.assertIn("odds_snapshot_watch.py", worker.args)
        self.assertIn("--data-dir", worker.args)
        self.assertIn("--orders-dir", worker.args)

    def test_full_launcher_records_snapshot_worker_dedicated_pid(self) -> None:
        root = Path(__file__).resolve().parents[1]
        app_dir = root / "app"
        if str(app_dir) not in sys.path:
            sys.path.insert(0, str(app_dir))
        spec = importlib.util.spec_from_file_location("hdzz_app_main_snapshot_pid", app_dir / "app.py")
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        specs = [
            module.WorkerSpec("odds_snapshot_watch", ["odds_snapshot_watch.py"]),
            module.WorkerSpec("follow_engine", ["follow_engine.py"]),
        ]
        snapshot_pid_writes = []
        follow_pid_writes = []
        fake_pids = iter((41001, 41002))

        def fake_popen(_cmd, **kwargs):
            for stream_name in ("stdout", "stderr"):
                stream = kwargs.get(stream_name)
                if stream is not None:
                    stream.close()
            return _FakeProc(next(fake_pids))

        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp) / "logs"
            log_dir.mkdir()
            with patch.object(module, "LOG_DIR", log_dir), patch.object(
                module, "ROOT", Path(tmp)
            ), patch.object(module, "ensure_dirs", return_value=None), patch.object(
                module, "workers", return_value=specs
            ), patch.object(module, "read_pid_info", return_value=None), patch.object(
                module, "pick_bindable_port", return_value=9999
            ), patch.object(
                module, "build_worker_process_command", return_value=["python3", "worker.py"]
            ), patch.object(
                module, "_alive_snapshot_watch_pids", return_value=[]
            ), patch.object(
                module, "_alive_global_worker_pids", return_value=[]
            ), patch.object(
                module, "_alive_follow_engine_pids", return_value=[]
            ), patch.object(
                module.subprocess, "Popen", side_effect=fake_popen
            ), patch.object(module, "write_pid_info"), patch.object(
                module, "write_snapshot_watch_pid_info", side_effect=snapshot_pid_writes.append
            ), patch.object(
                module, "write_follow_engine_pid_info", side_effect=follow_pid_writes.append
            ), patch.object(module, "apply_playwright_browsers_path"):
                rc = module.start_all("127.0.0.1", 9999, 10, 5)

        self.assertEqual(rc, 0)
        self.assertEqual(snapshot_pid_writes[-1]["pids"], [41001])
        self.assertEqual(follow_pid_writes[-1]["pids"], [41002])

    def test_full_launcher_reuses_existing_snapshot_worker(self) -> None:
        root = Path(__file__).resolve().parents[1]
        app_dir = root / "app"
        if str(app_dir) not in sys.path:
            sys.path.insert(0, str(app_dir))
        spec = importlib.util.spec_from_file_location("hdzz_app_main_snapshot_reuse", app_dir / "app.py")
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        specs = [
            module.WorkerSpec("odds_snapshot_watch", ["odds_snapshot_watch.py"]),
            module.WorkerSpec("follow_engine", ["follow_engine.py"]),
        ]
        pid_writes = []
        snapshot_pid_writes = []
        follow_pid_writes = []

        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp) / "logs"
            log_dir.mkdir()
            with patch.object(module, "LOG_DIR", log_dir), patch.object(
                module, "ROOT", Path(tmp)
            ), patch.object(module, "ensure_dirs", return_value=None), patch.object(
                module, "workers", return_value=specs
            ), patch.object(module, "read_pid_info", return_value=None), patch.object(
                module, "pick_bindable_port", return_value=9999
            ), patch.object(
                module, "build_worker_process_command", return_value=["python3", "worker.py"]
            ), patch.object(
                module, "_alive_snapshot_watch_pids", return_value=[40001]
            ), patch.object(
                module, "_alive_global_worker_pids", return_value=[]
            ), patch.object(
                module, "_alive_follow_engine_pids", return_value=[]
            ), patch.object(
                module.subprocess, "Popen", return_value=_FakeProc(41002)
            ) as popen, patch.object(
                module, "write_pid_info", side_effect=pid_writes.append
            ), patch.object(
                module, "write_snapshot_watch_pid_info", side_effect=snapshot_pid_writes.append
            ), patch.object(
                module, "write_follow_engine_pid_info", side_effect=follow_pid_writes.append
            ), patch.object(module, "apply_playwright_browsers_path"):
                rc = module.start_all("127.0.0.1", 9999, 10, 5)

        self.assertEqual(rc, 0)
        self.assertEqual(popen.call_count, 1)
        self.assertEqual(pid_writes[-1]["pids"], [41002, 40001])
        self.assertEqual(snapshot_pid_writes[-1]["pids"], [40001])
        self.assertEqual(follow_pid_writes[-1]["pids"], [41002])

    def test_webui_recovers_snapshot_pid_from_aggregate_pid_files(self) -> None:
        root = Path(__file__).resolve().parents[1]
        app_dir = root / "app"
        src_dir = root / "src"
        for path in (app_dir, src_dir):
            if str(path) not in sys.path:
                sys.path.insert(0, str(path))
        from hdzz.api.webui import server

        with tempfile.TemporaryDirectory() as tmp:
            dedicated = Path(tmp) / "odds_snapshot_watch.pid.json"
            with patch.object(server, "SNAPSHOT_WATCH_PID_FILE", dedicated), patch.object(
                server, "_snapshot_watch_candidate_pids", return_value=[40001]
            ), patch.object(server, "_is_snapshot_watch_pid", return_value=True):
                running, pid = server.snapshot_watch_running_flag()

            payload = json.loads(dedicated.read_text(encoding="utf-8"))

        self.assertTrue(running)
        self.assertEqual(pid, 40001)
        self.assertEqual(payload["pids"], [40001])

    def test_webui_snapshot_deadline_covers_two_phase_fetch(self) -> None:
        root = Path(__file__).resolve().parents[1]
        app_dir = root / "app"
        src_dir = root / "src"
        for path in (app_dir, src_dir):
            if str(path) not in sys.path:
                sys.path.insert(0, str(path))
        from hdzz.api.webui import server

        # Context resolution (2s) and game_more retrieval (2.5s) are
        # sequential; the launcher must leave scheduling margin for both.
        deadline_s = float(server.DEFAULT_SNAPSHOT_MARKET_DEADLINE_MS) / 1000.0
        self.assertGreaterEqual(deadline_s, 5.5)

    def test_stop_lifecycle_merges_follow_and_snapshot_dedicated_pids(self) -> None:
        root = Path(__file__).resolve().parents[1]
        app_dir = root / "app"
        if str(app_dir) not in sys.path:
            sys.path.insert(0, str(app_dir))
        spec = importlib.util.spec_from_file_location("hdzz_app_main_pid_merge", app_dir / "app.py")
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)

        with tempfile.TemporaryDirectory() as tmp:
            snapshot_pid_file = Path(tmp) / "odds_snapshot_watch.pid.json"
            snapshot_pid_file.write_text(json.dumps({"pids": [40002]}), encoding="utf-8")
            with patch.object(module, "SNAPSHOT_WATCH_PID_FILE", snapshot_pid_file), patch.object(
                module, "read_follow_engine_pid_info", return_value={"pids": [40001]}
            ):
                merged = module._merge_dedicated_worker_pids([40000, 40001])

        self.assertEqual(merged, [40000, 40001, 40002])


if __name__ == "__main__":
    unittest.main()
