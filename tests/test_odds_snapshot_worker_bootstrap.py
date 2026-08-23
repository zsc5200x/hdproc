import importlib.util
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


if __name__ == "__main__":
    unittest.main()
