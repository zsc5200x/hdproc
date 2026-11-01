import io
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
APP_DIR = ROOT / "app"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


from hdzz.api.webui import server  # noqa: E402
from hdzz.core.telegram_signal.store import save_settings, write_private_json  # noqa: E402


class TelegramSignalWebUiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data_dir = self.root / "data"
        self.orders_dir = self.root / "orders"
        self.data_dir.mkdir()
        self.orders_dir.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _handler(self, path: str, payload=None):
        handler = object.__new__(server.Handler)
        handler.path = path
        handler._data_dir = self.data_dir
        handler._arsenal_path = self.data_dir / "arsenal.json"
        handler._orders_dir = self.orders_dir
        handler._state_dir = self.root / "state"
        handler._config_path = self.root / "config.json"
        body = json.dumps(payload or {}).encode("utf-8")
        handler.headers = {"content-length": str(len(body))}
        handler.rfile = io.BytesIO(body)
        handler.responses = []
        handler._send_json = lambda status, data: handler.responses.append((status, data))
        handler.send_error = lambda status: handler.responses.append((status, None))
        return handler

    def test_status_is_sanitized_and_never_exposes_credentials(self) -> None:
        save_settings(
            self.data_dir / "telegram_signal.json",
            {"enabled": False, "dry_run": True, "source_chat_ids": ["-1001"]},
        )
        write_private_json(
            self.data_dir / "telegram_signal_secrets.json",
            {
                "api_id": "12345678",
                "api_hash": "0123456789abcdef0123456789abcdef",
                "session": "very-secret-session",
                "phone": "+8613812345678",
            },
        )
        handler = self._handler("/api/telegram-signal/status")

        with mock.patch.object(server, "telegram_signal_worker_running_flag", return_value=(True, 4321)):
            server.Handler.do_GET(handler)

        status, payload = handler.responses[-1]
        encoded = json.dumps(payload)
        self.assertEqual(status, 200)
        self.assertTrue(payload["credentials_configured"])
        self.assertTrue(payload["session_configured"])
        self.assertEqual(payload["phone_masked"], "+86138****5678")
        self.assertEqual(payload["worker_pid"], 4321)
        self.assertNotIn("12345678", encoded)
        self.assertNotIn("0123456789abcdef", encoded)
        self.assertNotIn("very-secret-session", encoded)

    def test_settings_accept_only_supported_fields(self) -> None:
        handler = self._handler(
            "/api/telegram-signal/settings",
            {
                "dry_run": False,
                "source_chat_ids": ["-1001"],
                "source_chats": [{"id": "-1001", "title": "来源群", "type": "group"}],
                "api_hash": "must-not-be-written",
                "webui_url": "https://external.invalid",
            },
        )

        server.Handler.do_POST(handler)

        status, payload = handler.responses[-1]
        saved = json.loads((self.data_dir / "telegram_signal.json").read_text(encoding="utf-8"))
        self.assertEqual(status, 200)
        self.assertFalse(saved["dry_run"])
        self.assertEqual(saved["source_chat_ids"], ["-1001"])
        self.assertNotIn("api_hash", saved)
        self.assertEqual(saved["webui_url"], "http://127.0.0.1:2299")
        self.assertTrue(payload["ok"])

    def test_status_marks_stale_listener_as_alive_but_not_healthy(self) -> None:
        run_dir = self.root / ".run"
        run_dir.mkdir()
        (run_dir / "telegram_signal_health.json").write_text(
            json.dumps(
                {
                    "pid": 4321,
                    "state": "healthy",
                    "heartbeat_at_ms": int(time.time() * 1000) - 20000,
                }
            ),
            encoding="utf-8",
        )
        handler = self._handler("/api/telegram-signal/status")

        with mock.patch.object(server, "telegram_signal_worker_running_flag", return_value=(True, 4321)):
            server.Handler.do_GET(handler)

        status, payload = handler.responses[-1]
        self.assertEqual(status, 200)
        self.assertTrue(payload["worker_alive"])
        self.assertFalse(payload["worker_running"])
        self.assertFalse(payload["worker_healthy"])
        self.assertEqual(payload["worker_state"], "stale")

    def test_chats_returns_saved_sources_without_opening_second_client_while_listener_is_alive(self) -> None:
        save_settings(
            self.data_dir / "telegram_signal.json",
            {
                "source_chat_ids": ["-1001"],
                "source_chats": [{"id": "-1001", "title": "来源群", "type": "group"}],
            },
        )
        handler = self._handler("/api/telegram-signal/chats")

        with mock.patch.object(server, "telegram_signal_worker_running_flag", return_value=(True, 4321)), mock.patch.object(
            server, "list_source_dialogs"
        ) as list_dialogs:
            server.Handler.do_GET(handler)

        status, payload = handler.responses[-1]
        self.assertEqual(status, 200)
        self.assertTrue(payload["cached"])
        self.assertEqual(payload["chats"][0]["title"], "来源群")
        list_dialogs.assert_not_called()

    def test_start_requires_login_and_source_group(self) -> None:
        write_private_json(
            self.data_dir / "telegram_signal_secrets.json",
            {"api_id": "12345678", "api_hash": "0123456789abcdef0123456789abcdef"},
        )
        handler = self._handler("/api/telegram-signal/start")

        server.Handler.do_POST(handler)

        status, payload = handler.responses[-1]
        self.assertEqual(status, 409)
        self.assertEqual(payload["message"], "telegram_login_required")

    def test_start_enables_listener_and_uses_independent_launcher_action(self) -> None:
        write_private_json(
            self.data_dir / "telegram_signal_secrets.json",
            {
                "api_id": "12345678",
                "api_hash": "0123456789abcdef0123456789abcdef",
                "session": "authorized-session",
            },
        )
        save_settings(self.data_dir / "telegram_signal.json", {"source_chat_ids": ["-1001"], "dry_run": True})
        handler = self._handler("/api/telegram-signal/start")

        with mock.patch.object(server, "_run_app_command", return_value=(0, "started")) as run:
            server.Handler.do_POST(handler)

        status, payload = handler.responses[-1]
        settings = json.loads((self.data_dir / "telegram_signal.json").read_text(encoding="utf-8"))
        self.assertEqual(status, 200)
        self.assertTrue(settings["enabled"])
        self.assertEqual(payload["status"], "started")
        run.assert_called_once_with(["start-telegram-signal"], timeout_s=20)

    def test_games_route_returns_only_tg_fixture_payload(self) -> None:
        handler = self._handler("/api/telegram-signal/games?mode=live")
        fixture_payload = {
            "ok": True,
            "mode": "live",
            "games": [{"gid": "9000001", "teamH": "主队", "teamC": "客队"}],
        }

        with mock.patch.object(server, "_telegram_signal_load_games", return_value=fixture_payload) as load:
            server.Handler.do_GET(handler)

        status, payload = handler.responses[-1]
        self.assertEqual(status, 200)
        self.assertEqual(payload["games"][0]["gid"], "9000001")
        self.assertNotIn("uid", json.dumps(payload))
        self.assertNotIn("cookie", json.dumps(payload))
        load.assert_called_once_with(self.data_dir / "arsenal.json", mode="live")


if __name__ == "__main__":
    unittest.main()
