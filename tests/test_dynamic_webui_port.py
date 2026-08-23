import socket
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "app"

if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


import app as launcher  # noqa: E402


class TestDynamicWebUiPort(unittest.TestCase):
    def test_pick_bindable_port_keeps_requested_port_when_available(self) -> None:
        host = "127.0.0.1"
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind((host, 0))
            requested = int(probe.getsockname()[1])

        selected = launcher.pick_bindable_port(host, requested)

        self.assertEqual(selected, requested)

    def test_pick_bindable_port_falls_back_when_requested_port_is_busy(self) -> None:
        host = "127.0.0.1"
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as busy:
            busy.bind((host, 0))
            busy.listen(1)
            requested = int(busy.getsockname()[1])

            selected = launcher.pick_bindable_port(host, requested)

        self.assertNotEqual(selected, requested)
        self.assertGreater(selected, 0)


if __name__ == "__main__":
    unittest.main()
