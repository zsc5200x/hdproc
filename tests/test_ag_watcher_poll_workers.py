import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
APP_DIR = ROOT / "app"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


from hdzz.core.watcher.ag_watcher import resolve_poll_workers  # noqa: E402


class TestAgWatcherPollWorkers(unittest.TestCase):
    def test_zero_workers_means_scan_all_targets_together(self) -> None:
        self.assertEqual(resolve_poll_workers(0, 3), 3)

    def test_positive_workers_caps_concurrency(self) -> None:
        self.assertEqual(resolve_poll_workers(2, 5), 2)

    def test_workers_do_not_exceed_target_count(self) -> None:
        self.assertEqual(resolve_poll_workers(10, 3), 3)


if __name__ == "__main__":
    unittest.main()
