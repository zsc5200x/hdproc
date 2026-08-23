import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "app"

if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import app as launcher  # noqa: E402


class _FakeProc:
    def __init__(self, pid: int) -> None:
        self.pid = pid


class TestFollowEngineLauncherControl(unittest.TestCase):
    def test_start_follow_engine_writes_dedicated_pid_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            pid_file = tmp / "follow_engine.pid.json"
            log_dir = tmp / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)

            spec = launcher.WorkerSpec(
                name="follow_engine",
                args=["-u", str((APP_DIR / "follow_engine.py").resolve()), "--orders-dir", "orders", "--data-dir", "data"],
            )

            with patch.object(launcher, "FOLLOW_ENGINE_PID_FILE", pid_file), \
                patch.object(launcher, "LOG_DIR", log_dir), \
                patch.object(launcher, "ROOT", tmp), \
                patch.object(launcher, "ensure_dirs", return_value=None), \
                patch.object(launcher, "_follow_engine_worker_spec", return_value=spec), \
                patch.object(launcher, "read_follow_engine_pid_info", return_value=None), \
                patch.object(launcher, "build_worker_process_command", return_value=["python3", "follow_engine.py"]), \
                patch("app.subprocess.Popen", return_value=_FakeProc(43210)):
                rc = launcher.start_follow_engine()

            self.assertEqual(rc, 0)
            self.assertTrue(pid_file.exists())
            payload = pid_file.read_text(encoding="utf-8")
            self.assertIn("43210", payload)

    def test_stop_all_also_stops_follow_engine_pid_from_dedicated_file(self) -> None:
        stopped = []
        pid_writes = []
        follow_writes = []

        def fake_write_pid_info(payload):
            pid_writes.append(payload)

        def fake_write_follow_pid_info(payload):
            follow_writes.append(payload)

        def fake_stop_pid(pid: int, force: bool) -> None:
            stopped.append((pid, force))
            running.discard(int(pid))

        running = {11111, 22222}

        def fake_is_pid_running(pid: int) -> bool:
            return int(pid) in running

        with patch.object(
            launcher,
            "read_pid_info",
            return_value={"host": "127.0.0.1", "port": 9999, "pids": [11111]},
        ), patch.object(
            launcher,
            "read_follow_engine_pid_info",
            return_value={"pids": [22222]},
        ), patch.object(
            launcher,
            "_is_probably_hdydf_worker_pid",
            return_value=True,
        ), patch.object(
            launcher,
            "is_pid_running",
            side_effect=fake_is_pid_running,
        ), patch.object(
            launcher,
            "stop_pid",
            side_effect=fake_stop_pid,
        ), patch.object(
            launcher,
            "write_pid_info",
            side_effect=fake_write_pid_info,
        ), patch.object(
            launcher,
            "write_follow_engine_pid_info",
            side_effect=fake_write_follow_pid_info,
        ):
            rc = launcher.stop_all(force=True)

        self.assertEqual(rc, 0)
        self.assertEqual(stopped, [(11111, True), (22222, True)])
        self.assertEqual(pid_writes[-1]["pids"], [])
        self.assertEqual(follow_writes[-1]["pids"], [])


if __name__ == "__main__":
    unittest.main()
