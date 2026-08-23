import json
import stat
import tempfile
import unittest
from pathlib import Path


class TelegramSignalStoreTest(unittest.TestCase):
    def test_defaults_to_stopped_dry_run(self) -> None:
        from hdzz.core.telegram_signal.store import default_settings

        settings = default_settings()

        self.assertFalse(settings["enabled"])
        self.assertTrue(settings["dry_run"])
        self.assertEqual(settings["source_chat_ids"], [])
        self.assertEqual(settings["target_name"], "telegram_signal")

    def test_normalizes_source_chat_ids_and_allowed_settings(self) -> None:
        from hdzz.core.telegram_signal.store import apply_settings_patch, default_settings

        settings = apply_settings_patch(
            default_settings(),
            {
                "enabled": True,
                "dry_run": False,
                "source_chat_ids": [" -1002 ", -1001, "-1002", "bad"],
                "target_name": " tg-orders ",
                "api_hash": "must-not-leak-into-settings",
            },
        )

        self.assertTrue(settings["enabled"])
        self.assertFalse(settings["dry_run"])
        self.assertEqual(settings["source_chat_ids"], ["-1002", "-1001"])
        self.assertEqual(settings["target_name"], "tg-orders")
        self.assertNotIn("api_hash", settings)

    def test_private_json_is_mode_0600_and_sanitized(self) -> None:
        from hdzz.core.telegram_signal.store import sanitize_secret_status, write_private_json

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "secrets.json"
            secret = {
                "api_id": "12345678",
                "api_hash": "0123456789abcdef0123456789abcdef",
                "phone": "+8613812345678",
                "session": "private-session-value",
            }
            write_private_json(path, secret)
            saved = json.loads(path.read_text(encoding="utf-8"))
            status = sanitize_secret_status(saved)

            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertTrue(status["credentials_configured"])
            self.assertTrue(status["session_configured"])
            self.assertEqual(status["phone_masked"], "+86138****5678")
            self.assertNotIn("api_hash", status)
            self.assertNotIn("session", status)
            self.assertNotIn("12345678", json.dumps(status))

    def test_remember_message_deduplicates_and_bounds_state(self) -> None:
        from hdzz.core.telegram_signal.store import remember_message

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            self.assertTrue(remember_message(path, "-1001:1", max_seen=3))
            self.assertFalse(remember_message(path, "-1001:1", max_seen=3))
            self.assertTrue(remember_message(path, "-1001:2", max_seen=3))
            self.assertTrue(remember_message(path, "-1001:3", max_seen=3))
            self.assertTrue(remember_message(path, "-1001:4", max_seen=3))
            state = json.loads(path.read_text(encoding="utf-8"))

            self.assertEqual(state["seen_message_keys"], ["-1001:2", "-1001:3", "-1001:4"])

    def test_event_log_returns_latest_rows(self) -> None:
        from hdzz.core.telegram_signal.store import append_event, read_recent_events

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            for index in range(5):
                append_event(path, {"id": index})

            rows = read_recent_events(path, limit=2)

            self.assertEqual([row["id"] for row in rows], [4, 3])


if __name__ == "__main__":
    unittest.main()
