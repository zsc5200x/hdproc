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


from hdzz.core.watcher import ag_watcher  # noqa: E402


class TestAgWatcherReloginPolicy(unittest.TestCase):
    def test_go_to_home_response_triggers_relogin(self) -> None:
        self.assertTrue(
            ag_watcher.looks_like_logged_out(
                {
                    "status": "error",
                    "code": "",
                    "msg": "goToHome",
                }
            )
        )

    def test_go_to_home_code_field_triggers_relogin(self) -> None:
        self.assertTrue(
            ag_watcher.looks_like_logged_out(
                {
                    "status": "error",
                    "code": "goToHome",
                    "msg": "",
                }
            )
        )

    def test_transport_exception_threshold_is_aggressive(self) -> None:
        threshold = ag_watcher.auto_relogin_threshold_for_error(
            "exception",
            "HTTPSConnectionPool(host='ag.hga030.com', port=443): Max retries exceeded (Caused by SSLError(SSLEOFError(8, 'EOF occurred in violation of protocol')))",
        )
        self.assertEqual(threshold, 2)

    def test_timeout_threshold_is_lower_than_legacy_value(self) -> None:
        threshold = ag_watcher.auto_relogin_threshold_for_error(
            "timeout",
            "Read timed out. (read timeout=10)",
        )
        self.assertEqual(threshold, 3)


if __name__ == "__main__":
    unittest.main()
