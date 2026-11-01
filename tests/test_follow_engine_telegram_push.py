import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import requests


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
APP = ROOT / "app"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))


class TestFollowEngineTelegramPush(unittest.TestCase):
    def test_final_independent_split_update_uses_aggregate_amounts(self) -> None:
        from hdzz.core.follower.engine import _build_independent_split_tg_result, _update_follow_tg_message
        from hdzz.core.follower.models import RetryJob

        job = RetryJob(
            key="target|order#part:1",
            parent_key="target|order",
            independent_split=True,
            order_id="order",
            target="target",
            stake=100.0,
            split_stakes=[50.0, 50.0],
            order={
                "orderId": "order",
                "stake": "100",
                "league": "测试联赛",
                "sides": "A v B",
                "line": "大 2.5",
                "odds": "0.90",
                "_tg_message_id": 1234,
                "_tg_chat_id": "-100123",
            },
            created_at=100.0,
            split_count=2,
            split_index=1,
        )
        aggregate = {
            "status": "ok",
            "split_parts": [
                {"status": "ok", "stake": 50.0, "ticket_id": "T1", "split_index": 0},
                {"status": "ok", "stake": 50.0, "ticket_id": "T2", "split_index": 1},
            ],
        }

        final_result = _build_independent_split_tg_result(aggregate, job)
        with (
            patch("hdzz.core.follower.engine.load_tg_config", return_value=("token", "-100123", "", "", {})),
            patch("hdzz.core.follower.engine.tg_upsert_order_message", return_value=1234) as upsert_mock,
        ):
            _update_follow_tg_message(job=job, follow_result=final_result)

        sent_text = upsert_mock.call_args.kwargs["html_text"]
        self.assertIn("跟单金额：100", sent_text)
        self.assertIn("跟单成功：100", sent_text)

    def test_ensure_initial_order_message_sends_when_missing_message_id(self) -> None:
        from hdzz.core.follower.engine import ensure_initial_order_tg_message

        order = {
            "target": "qax228",
            "orderId": "OID-2",
            "league": "测试联赛",
            "sides": "A v B",
            "line": "主队 -0.5",
            "odds": "0.88",
            "stake": "1000",
        }

        with (
            patch("hdzz.core.follower.engine.load_tg_config", return_value=("token", "-100123", "", "", {})),
            patch("hdzz.core.follower.engine.tg_upsert_order_message", return_value=5566) as upsert_mock,
        ):
            mid = ensure_initial_order_tg_message(order=order, target_name="qax228")

        self.assertEqual(mid, 5566)
        self.assertEqual(order["_tg_message_id"], 5566)
        self.assertEqual(order["_tg_chat_id"], "-100123")
        self.assertEqual(upsert_mock.call_count, 1)

    def test_ensure_initial_order_message_skips_when_message_exists(self) -> None:
        from hdzz.core.follower.engine import ensure_initial_order_tg_message

        order = {
            "target": "qax228",
            "orderId": "OID-3",
            "_tg_message_id": 7788,
            "_tg_chat_id": "-100123",
        }

        with patch("hdzz.core.follower.engine.tg_upsert_order_message") as upsert_mock:
            mid = ensure_initial_order_tg_message(order=order, target_name="qax228")

        self.assertEqual(mid, 7788)
        self.assertEqual(upsert_mock.call_count, 0)

    def test_ensure_initial_order_message_skips_disabled_target(self) -> None:
        from hdzz.core.follower.engine import ensure_initial_order_tg_message

        order = {
            "target": "ccvv445566",
            "orderId": "OID-DISABLED",
        }
        settings = {"targets": [{"name": "ccvv445566", "enabled": False}]}

        with patch("hdzz.core.follower.engine.tg_upsert_order_message") as upsert_mock:
            mid = ensure_initial_order_tg_message(
                order=order,
                target_name="ccvv445566",
                settings=settings,
            )

        self.assertIsNone(mid)
        self.assertEqual(upsert_mock.call_count, 0)
        self.assertNotIn("_tg_message_id", order)

    def test_ensure_initial_order_message_skips_zero_ratio_target(self) -> None:
        from hdzz.core.follower.engine import ensure_initial_order_tg_message

        order = {
            "target": "ccvv445566",
            "orderId": "OID-ZERO-RATIO",
        }
        settings = {"targets": [{"name": "ccvv445566", "ratio_percent": 0}]}

        with patch("hdzz.core.follower.engine.tg_upsert_order_message") as upsert_mock:
            mid = ensure_initial_order_tg_message(
                order=order,
                target_name="ccvv445566",
                settings=settings,
            )

        self.assertIsNone(mid)
        self.assertEqual(upsert_mock.call_count, 0)
        self.assertNotIn("_tg_message_id", order)

    def test_remote_signal_backfill_is_eligible_for_initial_telegram_push(self) -> None:
        from hdzz.core.follower.engine import should_send_initial_tg_for_ingested_item

        order = {
            "target": "8hu557555",
            "orderId": "23935601855",
            "ts": 1779276147,
            "_signal_origin": "remote_signal_pull",
            "_skip_signal_forward": True,
        }
        item = {"target": "8hu557555", "orderId": "23935601855", "order": order, "backfill": True}

        self.assertTrue(
            should_send_initial_tg_for_ingested_item(
                item,
                now_ts_value=1779276170,
                max_backfill_age_s=120,
            )
        )

    def test_old_generic_backfill_does_not_send_initial_telegram_push(self) -> None:
        from hdzz.core.follower.engine import should_send_initial_tg_for_ingested_item

        order = {
            "target": "qax228",
            "orderId": "OLD-1",
            "ts": 1779270000,
        }
        item = {"target": "qax228", "orderId": "OLD-1", "order": order, "backfill": True}

        self.assertFalse(
            should_send_initial_tg_for_ingested_item(
                item,
                now_ts_value=1779276170,
                max_backfill_age_s=120,
            )
        )

    def test_target_disabled_branch_does_not_upsert_telegram(self) -> None:
        script = (SRC / "hdzz" / "core" / "follower" / "engine.py").read_text(encoding="utf-8")
        start = script.index("if not target_follow_enabled(settings, target):")
        end = script.index("base_stake = compute_follow_stake(order, settings)", start)
        disabled_branch = script[start:end]

        self.assertNotIn("tg_upsert_order_message(", disabled_branch)

    def test_upsert_falls_back_to_text_when_send_photo_fails(self) -> None:
        from hdzz.core.follower.engine import tg_upsert_order_message

        with (
            patch("hdzz.core.follower.engine.tg_send_photo", side_effect=RuntimeError("sendPhoto_http_error: 400")),
            patch("hdzz.core.follower.engine.tg_send", return_value=7788) as tg_send_mock,
        ):
            message_id = tg_upsert_order_message(
                bot_token="token",
                chat_id="-100123",
                message_id=None,
                html_text="<b>跟单成功</b>",
                order_id="OID-1",
                order_preview_image="https://example.com/preview.jpg",
            )

        self.assertEqual(message_id, 7788)
        self.assertEqual(tg_send_mock.call_count, 1)

    def test_upsert_falls_back_to_new_message_when_edit_target_missing(self) -> None:
        from hdzz.core.follower.engine import tg_upsert_order_message

        err = RuntimeError(
            "editMessageText_http_error: 400 Client Error; "
            'body={"ok":false,"description":"Bad Request: message to edit not found"}'
        )
        with (
            patch("hdzz.core.follower.engine.tg_edit", side_effect=err),
            patch("hdzz.core.follower.engine.tg_send", return_value=8899) as tg_send_mock,
        ):
            message_id = tg_upsert_order_message(
                bot_token="token",
                chat_id="-100123",
                message_id=1234,
                html_text="<b>跟单成功</b>",
                order_id="OID-2",
            )

        self.assertEqual(message_id, 8899)
        self.assertEqual(tg_send_mock.call_count, 1)

    def test_tg_edit_http_error_includes_response_body(self) -> None:
        from hdzz.core.follower.engine import tg_edit

        class FakeResponse:
            text = '{"ok":false,"description":"Bad Request: message to edit not found"}'

            def raise_for_status(self) -> None:
                raise requests.HTTPError("400 Client Error")

            def json(self):
                return {"ok": False}

        with patch("hdzz.core.follower.engine.requests.post", return_value=FakeResponse()):
            with self.assertRaises(RuntimeError) as cm:
                tg_edit("token", "-100123", 1234, "<b>跟单成功</b>")

        self.assertIn("editMessageText_http_error", str(cm.exception))
        self.assertIn("message to edit not found", str(cm.exception))

    def test_load_tg_config_uses_default_preview_image_when_unset(self) -> None:
        from hdzz.core.follower.engine import load_tg_config

        with tempfile.TemporaryDirectory() as td:
            cfg_path = Path(td) / "config.json"
            cfg_path.write_text(
                '{"telegram_order_forward_enabled": true, "telegram_bot_token": "token", "telegram_chat_id": "-100123"}',
                encoding="utf-8",
            )

            with patch("hdzz.core.follower.engine._resolve_order_preview_local_path", return_value=Path("gdcg.jpg")):
                bot_token, chat_id, _proxy, order_preview_image, _ui_theme = load_tg_config(cfg_path)

        self.assertEqual(bot_token, "token")
        self.assertEqual(chat_id, "-100123")
        self.assertEqual(order_preview_image, "gdcg.jpg")


if __name__ == "__main__":
    unittest.main()
