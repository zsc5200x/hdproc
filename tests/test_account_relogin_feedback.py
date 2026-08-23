import unittest
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
APP_DIR = ROOT / "app"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


class TestAccountReloginFeedback(unittest.TestCase):
    def test_manual_relogin_state_clears_stale_error_and_marks_visible_login(self) -> None:
        from hdzz.api.webui.server import ACCOUNT_RELOGIN_PENDING_MSG, prepare_account_manual_relogin_state

        state = {
            "last_status": "offline",
            "last_error_code": "non_json",
            "last_error_msg": "old polling error",
            "last_relogin_error_code": "relogin_failed",
            "last_relogin_error_msg": "old relogin error",
        }

        patched = prepare_account_manual_relogin_state(state, now=123.0)

        self.assertEqual(patched["manual_relogin"], 1)
        self.assertEqual(patched["manual_relogin_requested_at"], 123.0)
        self.assertEqual(patched["last_relogin_at"], 0)
        self.assertEqual(patched["last_status"], "relogin")
        self.assertEqual(patched["relogin_in_progress"], 1)
        self.assertEqual(patched["relogin_started_at"], 123.0)
        self.assertEqual(patched["last_error_code"], "")
        self.assertEqual(patched["last_error_msg"], ACCOUNT_RELOGIN_PENDING_MSG)
        self.assertEqual(patched["last_relogin_error_code"], "")
        self.assertEqual(patched["last_relogin_error_msg"], "")

    def test_account_status_entry_preserves_relogin_failure_hint(self) -> None:
        from hdzz.api.webui.server import build_account_status_entry

        entry = build_account_status_entry(
            {"name": "summer001", "uid": "", "cookie": ""},
            {
                "last_status": "offline",
                "last_ok_at": 0,
                "last_poll_at": 100.0,
                "last_error_code": "non_json",
                "last_error_msg": "",
                "last_relogin_error_code": "relogin_failed",
                "last_relogin_error_msg": "重登超时：未检测到可用登录会话（可能需要手动验证码）",
                "last_relogin_failed_at": 120.0,
            },
            now=130.0,
        )

        self.assertEqual(entry["status"], "offline")
        self.assertTrue(entry["needs_manual_login"])
        self.assertEqual(entry["last_relogin_error_code"], "relogin_failed")
        self.assertIn("手动验证码", entry["last_relogin_error_msg"])
        self.assertIn("点击重登", entry["status_message"])


if __name__ == "__main__":
    unittest.main()
