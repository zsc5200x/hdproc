import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
APP_DIR = ROOT / "app"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


from hdzz.api.webui import server  # noqa: E402


class TestWebUiWatchRestart(unittest.TestCase):
    def test_ensure_ag_wmc_watch_running_spawns_when_missing(self) -> None:
        with (
            patch("hdzz.api.webui.server._is_named_worker_running", return_value=False),
            patch("hdzz.api.webui.server.apply_playwright_browsers_path"),
            patch("hdzz.api.webui.server.subprocess.Popen") as popen_mock,
        ):
            ok, started, err = server.ensure_ag_wmc_watch_running()

        self.assertTrue(ok)
        self.assertTrue(started)
        self.assertEqual(err, "")
        self.assertEqual(popen_mock.call_count, 1)


if __name__ == "__main__":
    unittest.main()
