import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
APP_DIR = ROOT / "app"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


from hdzz.api.webui import server  # noqa: E402


class TestWebUiTelegramTest(unittest.TestCase):
    def test_apply_telegram_config_update_clear_config_removes_current_robot(self) -> None:
        cfg = {
            "telegram_bot_token": "old-token",
            "telegram_chat_id": "-100old",
            "telegram_allow_chat": "-100old",
            "telegram_proxy": "socks5://127.0.0.1:7890",
            "telegram_status_push_enabled": True,
            "telegram_status_bot_token": "status-token",
            "telegram_status_chat_id": "-100status",
            "telegram_status_proxy": "socks5://127.0.0.1:7891",
            "telegram_backend_order_push_enabled": True,
            "telegram_backend_order_bot_token": "backend-token",
            "telegram_backend_order_chat_id": "-100backend",
            "telegram_backend_order_proxy": "socks5://127.0.0.1:7892",
        }

        updated = server.apply_telegram_config_update(cfg, {"clear_config": True})

        self.assertEqual(updated["telegram_bot_token"], "")
        self.assertEqual(updated["telegram_chat_id"], "")
        self.assertEqual(updated["telegram_allow_chat"], "")
        self.assertEqual(updated["telegram_proxy"], "")
        self.assertNotIn("telegram_status_push_enabled", updated)
        self.assertNotIn("telegram_status_bot_token", updated)
        self.assertNotIn("telegram_status_chat_id", updated)
        self.assertNotIn("telegram_status_proxy", updated)
        self.assertFalse(updated["telegram_backend_order_push_enabled"])
        self.assertEqual(updated["telegram_backend_order_bot_token"], "")
        self.assertEqual(updated["telegram_backend_order_chat_id"], "")
        self.assertEqual(updated["telegram_backend_order_proxy"], "")

    def test_apply_telegram_config_update_empty_token_still_keeps_existing_token(self) -> None:
        cfg = {
            "telegram_bot_token": "old-token",
            "telegram_chat_id": "-100old",
            "telegram_proxy": "socks5://127.0.0.1:7890",
        }

        updated = server.apply_telegram_config_update(
            cfg,
            {"bot_token": "", "chat_id": "-100new", "proxy": ""},
        )

        self.assertEqual(updated["telegram_bot_token"], "old-token")
        self.assertEqual(updated["telegram_chat_id"], "-100new")
        self.assertEqual(updated["telegram_proxy"], "")

    def test_apply_telegram_config_update_persists_order_forward_enabled(self) -> None:
        updated = server.apply_telegram_config_update(
            {"telegram_order_forward_enabled": False},
            {"order_forward_enabled": True},
        )

        self.assertTrue(updated["telegram_order_forward_enabled"])

    def test_apply_telegram_config_update_persists_independent_backend_order_robot(self) -> None:
        updated = server.apply_telegram_config_update(
            {
                "telegram_backend_order_bot_token": "old-backend-token",
                "telegram_status_push_enabled": True,
            },
            {
                "backend_order_push_enabled": True,
                "backend_order_bot_token": "new-backend-token",
                "backend_order_chat_id": "-100backend",
                "backend_order_proxy": "socks5://127.0.0.1:7892",
            },
        )

        self.assertTrue(updated["telegram_backend_order_push_enabled"])
        self.assertEqual(updated["telegram_backend_order_bot_token"], "new-backend-token")
        self.assertEqual(updated["telegram_backend_order_chat_id"], "-100backend")
        self.assertEqual(updated["telegram_backend_order_proxy"], "socks5://127.0.0.1:7892")
        self.assertNotIn("telegram_status_push_enabled", updated)

    def test_telegram_config_page_has_clear_button_binding(self) -> None:
        html = (APP_DIR / "webui" / "index.html").read_text(encoding="utf-8")
        js = (APP_DIR / "webui" / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="tg-clear"', html)
        self.assertIn('id="tg-order-forward"', html)
        self.assertIn('id="tg-backend-order-push"', html)
        self.assertIn('id="tg-backend-order-token"', html)
        self.assertIn('id="tg-backend-order-chat"', html)
        self.assertIn("清除当前配置", html)
        self.assertIn("async function clearTelegramConfig", js)
        self.assertIn('"clear_config": true', js)
        self.assertIn('$("tg-clear")?.addEventListener("click", clearTelegramConfig)', js)
        self.assertIn('$("tg-backend-order-test")?.addEventListener("click", testBackendOrderTelegram)', js)

    def test_tg_send_message_falls_back_to_direct_when_proxy_ssl_fails(self) -> None:
        proxy_ssl_error = server.requests.exceptions.SSLError(
            "CERTIFICATE_VERIFY_FAILED: self-signed certificate in certificate chain"
        )
        ok_resp = Mock()
        ok_resp.json.return_value = {"ok": True}
        ok_resp.raise_for_status.return_value = None

        with patch("hdzz.api.webui.server.requests.post", side_effect=[proxy_ssl_error, ok_resp]) as post_mock:
            res = server.tg_send_message(
                bot_token="token",
                chat_id="-100123",
                text="hello",
                proxy="http://127.0.0.1:8888",
                timeout_s=15,
            )

        self.assertEqual(post_mock.call_count, 2)
        self.assertTrue(res.get("ok"))
        first_call = post_mock.call_args_list[0]
        second_call = post_mock.call_args_list[1]
        self.assertEqual(first_call.kwargs.get("proxies"), {"http": "http://127.0.0.1:8888", "https": "http://127.0.0.1:8888"})
        self.assertIsNone(second_call.kwargs.get("proxies"))


if __name__ == "__main__":
    unittest.main()
