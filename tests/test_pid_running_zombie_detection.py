import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
SRC = ROOT / "src"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import app as launcher  # noqa: E402
from hdzz.api.webui import server  # noqa: E402


class TestPidRunningZombieDetection(unittest.TestCase):
    @patch("app.os.kill", return_value=None)
    @patch("app.subprocess.check_output", return_value=b"Z\n")
    def test_launcher_is_pid_running_treats_zombie_as_not_running(self, _ps, _kill) -> None:
        self.assertFalse(launcher.is_pid_running(12345))

    @patch("hdzz.api.webui.server.os.kill", return_value=None)
    @patch("hdzz.api.webui.server.subprocess.check_output", return_value=b"Z\n")
    def test_server_is_pid_running_treats_zombie_as_not_running(self, _ps, _kill) -> None:
        self.assertFalse(server.is_pid_running(12345))


if __name__ == "__main__":
    unittest.main()
