import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


class FakeSession:
    def __init__(self, value="initial-session") -> None:
        self.value = value

    def save(self):
        return self.value


class FakeClient:
    def __init__(self, session_value="initial-session") -> None:
        self.session = FakeSession(session_value)
        self.connected = False
        self.authorized = False
        self.sign_in_calls = []

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False

    async def send_code_request(self, phone):
        self.session.value = "pending-session"
        return SimpleNamespace(phone_code_hash="code-hash-1")

    async def sign_in(self, **kwargs):
        self.sign_in_calls.append(kwargs)
        self.authorized = True
        self.session.value = "authorized-session"

    async def is_user_authorized(self):
        return self.authorized

    async def get_dialogs(self):
        return [
            SimpleNamespace(id=-1001, title="来源群A", is_group=True, is_channel=False),
            SimpleNamespace(id=-1002, title="来源频道B", is_group=False, is_channel=True),
            SimpleNamespace(id=42, title="私人对话", is_group=False, is_channel=False),
        ]


class TelegramSignalClientTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.secrets_path = root / "secrets.json"
        self.pending_path = root / "pending.json"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _seed_credentials(self) -> None:
        from hdzz.core.telegram_signal.client import save_api_credentials

        save_api_credentials(
            self.secrets_path,
            api_id="12345678",
            api_hash="0123456789abcdef0123456789abcdef",
        )

    def test_rejects_invalid_api_credentials(self) -> None:
        from hdzz.core.telegram_signal.client import TelegramSignalClientError, save_api_credentials

        with self.assertRaisesRegex(TelegramSignalClientError, "invalid_api_id"):
            save_api_credentials(self.secrets_path, api_id="abc", api_hash="x" * 32)
        with self.assertRaisesRegex(TelegramSignalClientError, "invalid_api_hash"):
            save_api_credentials(self.secrets_path, api_id="12345678", api_hash="short")
        with self.assertRaisesRegex(TelegramSignalClientError, "invalid_api_id"):
            save_api_credentials(self.secrets_path, api_id="", api_hash="a" * 32)
        with self.assertRaisesRegex(TelegramSignalClientError, "invalid_api_hash"):
            save_api_credentials(self.secrets_path, api_id="12345678", api_hash="")

    def test_send_code_saves_pending_session_without_exposing_secrets(self) -> None:
        from hdzz.core.telegram_signal.client import send_login_code

        self._seed_credentials()
        fake = FakeClient()
        result = send_login_code(
            self.secrets_path,
            self.pending_path,
            phone="+8613812345678",
            client_factory=lambda *_args: fake,
        )
        pending = json.loads(self.pending_path.read_text(encoding="utf-8"))

        self.assertTrue(result["ok"])
        self.assertEqual(result["phone_masked"], "+86138****5678")
        self.assertNotIn("phone_code_hash", result)
        self.assertEqual(pending["phone_code_hash"], "code-hash-1")
        self.assertEqual(pending["session"], "pending-session")

    def test_send_code_uses_built_in_credentials_when_secret_file_has_none(self) -> None:
        from hdzz.core.telegram_signal.client import send_login_code
        from hdzz.core.telegram_signal.credentials import DEFAULT_TELEGRAM_API_HASH, DEFAULT_TELEGRAM_API_ID

        captured = {}
        fake = FakeClient()
        result = send_login_code(
            self.secrets_path,
            self.pending_path,
            phone="+8613812345678",
            client_factory=lambda session, api_id, api_hash: captured.update(
                {"session": session, "api_id": api_id, "api_hash": api_hash}
            ) or fake,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(captured, {
            "session": "",
            "api_id": DEFAULT_TELEGRAM_API_ID,
            "api_hash": DEFAULT_TELEGRAM_API_HASH,
        })
        self.assertFalse(self.secrets_path.exists())

    def test_verify_code_saves_authorized_session_and_clears_pending(self) -> None:
        from hdzz.core.telegram_signal.client import send_login_code, verify_login_code

        self._seed_credentials()
        send_login_code(
            self.secrets_path,
            self.pending_path,
            phone="+8613812345678",
            client_factory=lambda *_args: FakeClient(),
        )
        fake = FakeClient("pending-session")
        result = verify_login_code(
            self.secrets_path,
            self.pending_path,
            code="12345",
            client_factory=lambda *_args: fake,
        )
        secrets = json.loads(self.secrets_path.read_text(encoding="utf-8"))

        self.assertTrue(result["ok"])
        self.assertTrue(result["authorized"])
        self.assertEqual(secrets["session"], "authorized-session")
        self.assertEqual(secrets["phone"], "+8613812345678")
        self.assertFalse(self.pending_path.exists())
        self.assertEqual(fake.sign_in_calls[0]["code"], "12345")

    def test_lists_only_group_and_channel_dialogs(self) -> None:
        from hdzz.core.telegram_signal.client import list_source_dialogs

        self._seed_credentials()
        secret = json.loads(self.secrets_path.read_text(encoding="utf-8"))
        secret["session"] = "authorized-session"
        self.secrets_path.write_text(json.dumps(secret), encoding="utf-8")
        fake = FakeClient("authorized-session")
        fake.authorized = True

        result = list_source_dialogs(self.secrets_path, client_factory=lambda *_args: fake)

        self.assertEqual([row["id"] for row in result], ["-1001", "-1002"])
        self.assertEqual([row["type"] for row in result], ["group", "channel"])


if __name__ == "__main__":
    unittest.main()
