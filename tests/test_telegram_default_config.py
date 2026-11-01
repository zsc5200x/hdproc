import json
import unittest
from pathlib import Path


class TestTelegramDefaultConfig(unittest.TestCase):
    def test_config_example_keeps_order_forward_and_custom_order_theme_defaults(self) -> None:
        cfg_path = Path(__file__).resolve().parents[1] / "app" / "config.example.json"
        cfg = json.loads(cfg_path.read_text(encoding="utf-8-sig"))

        self.assertIs(cfg.get("telegram_order_forward_enabled"), True)
        self.assertIs(cfg.get("telegram_backend_order_push_enabled"), False)
        self.assertIn("telegram_backend_order_bot_token", cfg)
        self.assertIn("telegram_backend_order_chat_id", cfg)
        self.assertNotIn("telegram_status_push_enabled", cfg)
        ui = cfg.get("telegram_ui") or {}
        text_ids = ui.get("order_text_custom_emoji_ids") or {}
        button_ids = ui.get("order_button_custom_emoji_ids") or {}
        divider_ids = ui.get("order_divider_custom_emoji_ids") or []

        self.assertTrue(text_ids.get("title"))
        self.assertTrue(text_ids.get("match"))
        self.assertTrue(text_ids.get("follow_success"))
        self.assertTrue(button_ids.get("res"))
        self.assertTrue(button_ids.get("log"))
        self.assertTrue(divider_ids)


if __name__ == "__main__":
    unittest.main()
