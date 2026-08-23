import json
import tempfile
import unittest
from pathlib import Path

from test_telegram_signal_parser import HANDICAP_SIGNAL


GAME = {
    "gid": "8923001",
    "league": "斯洛伐克超级联赛",
    "teamH": "拜斯迪卡",
    "teamC": "布拉迪斯拉发",
    "gnumH": "701356",
    "gnumC": "701355",
}

HALF_TIME_TOTAL_SIGNAL = """10215b（广2）监控 会员：cpb3C 单号：***65681
时间：07-26-2026 17:42:14
会员：cpb3C
单号：24930565681
足球：(滚球) 大 / 小 - 上半场
内容：
玻利维亚甲组联赛
[701154] vs [701153]
玻利瓦尔 v 皇家帕托斯 (1 - 0)
大 1.5 / 2 @ 0.85

￥：10000"""


class TelegramSignalWatcherTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.state_path = root / "state.json"
        self.event_log_path = root / "events.jsonl"
        self.orders_dir = root / "orders"
        self.settings = {
            "enabled": True,
            "dry_run": True,
            "source_chat_ids": ["-100778899"],
            "target_name": "telegram_signal",
        }
        self.message = {
            "chat_id": "-100778899",
            "message_id": "88407",
            "chat_title": "10215c监控",
            "text": HANDICAP_SIGNAL,
            "received_ts": 1785112345,
        }

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _process(self, *, settings=None, message=None, games=None):
        from hdzz.core.telegram_signal.watcher import process_signal_message

        return process_signal_message(
            settings=settings or self.settings,
            message=message or self.message,
            games_loader=lambda _signal: list(GAME if False else (games if games is not None else [GAME])),
            state_path=self.state_path,
            event_log_path=self.event_log_path,
            orders_dir=self.orders_dir,
        )

    def test_ignores_when_listener_is_disabled(self) -> None:
        result = self._process(settings=dict(self.settings, enabled=False))

        self.assertEqual(result["status"], "ignored_disabled")
        self.assertFalse(self.event_log_path.exists())

    def test_ignores_messages_outside_configured_source_chats(self) -> None:
        result = self._process(message=dict(self.message, chat_id="-100000001"))

        self.assertEqual(result["status"], "ignored_source")
        self.assertFalse(self.event_log_path.exists())

    def test_deduplicates_same_telegram_message(self) -> None:
        first = self._process()
        second = self._process()

        self.assertEqual(first["status"], "dry_run")
        self.assertEqual(second["status"], "duplicate_message")

    def test_logs_parse_rejection_for_unrelated_text(self) -> None:
        result = self._process(message=dict(self.message, text="普通聊天消息"))

        self.assertEqual(result["status"], "rejected_parse")
        event = json.loads(self.event_log_path.read_text(encoding="utf-8").strip())
        self.assertEqual(event["status"], "rejected_parse")
        self.assertNotIn("text", event)

    def test_rejects_when_match_cannot_be_resolved(self) -> None:
        result = self._process(games=[])

        self.assertEqual(result["status"], "rejected_match")
        self.assertFalse((self.orders_dir / "telegram_signal.jsonl").exists())

    def test_reuses_recent_confirmed_match_when_live_list_temporarily_drops_game(self) -> None:
        self.orders_dir.mkdir(parents=True, exist_ok=True)
        prior = {
            "ts": 1785112300,
            "target": "telegram_signal",
            "orderId": "24930540016",
            "league": "玻利维亚甲组联赛",
            "gid": "8933111",
            "raw": {
                "GID": "8933111",
                "GNUM_H": "701154",
                "GNUM_C": "701153",
                "TEAM_H": "玻利瓦尔",
                "TEAM_C": "皇家帕托斯",
            },
        }
        (self.orders_dir / "telegram_signal.jsonl").write_text(
            json.dumps(prior, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        message = dict(self.message, message_id="65681", text=HALF_TIME_TOTAL_SIGNAL)

        result = self._process(message=message, games=[])

        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(result["order"]["gid"], "8933111")
        self.assertEqual(result["order"]["betType"], "(滚球) 大 / 小 - 上半场")
        self.assertEqual(result["order"]["line"], "大 1.5 / 2")

    def test_dry_run_resolves_but_does_not_write_order(self) -> None:
        result = self._process()

        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(result["order"]["gid"], "8923001")
        self.assertFalse((self.orders_dir / "telegram_signal.jsonl").exists())

    def test_automatic_mode_appends_one_order(self) -> None:
        result = self._process(settings=dict(self.settings, dry_run=False))
        out_path = self.orders_dir / "telegram_signal.jsonl"
        row = json.loads(out_path.read_text(encoding="utf-8").strip())

        self.assertEqual(result["status"], "injected")
        self.assertEqual(row["orderId"], "24929088407")
        self.assertEqual(row["raw"]["_EVENT"], "8923001")

    def test_deduplicates_same_order_from_different_message(self) -> None:
        first = self._process(settings=dict(self.settings, dry_run=False))
        second = self._process(
            settings=dict(self.settings, dry_run=False),
            message=dict(self.message, message_id="88408"),
        )

        self.assertEqual(first["status"], "injected")
        self.assertEqual(second["status"], "duplicate_order")
        lines = (self.orders_dir / "telegram_signal.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)


if __name__ == "__main__":
    unittest.main()
