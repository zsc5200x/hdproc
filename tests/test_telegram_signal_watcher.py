import json
import logging
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from test_telegram_signal_parser import HANDICAP_SIGNAL, ORDER_CARD_SIGNAL


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

    def _process(self, *, settings=None, message=None, games=None, games_refresher=None):
        from hdzz.core.telegram_signal.watcher import process_signal_message

        return process_signal_message(
            settings=settings or self.settings,
            message=message or self.message,
            games_loader=lambda _signal: list(GAME if False else (games if games is not None else [GAME])),
            state_path=self.state_path,
            event_log_path=self.event_log_path,
            orders_dir=self.orders_dir,
            games_refresher=games_refresher,
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

    def test_native_order_card_reuses_recent_match_without_provider_numbers(self) -> None:
        self.orders_dir.mkdir(parents=True, exist_ok=True)
        prior = {
            "ts": 1787739000,
            "target": "source_backend",
            "orderId": "25172900001",
            "league": "中美洲杯",
            "gid": "9000001",
            "raw": {
                "GID": "9000001",
                "GNUM_H": "712001",
                "GNUM_C": "712002",
                "TEAM_H": "希尔迪奥诺",
                "TEAM_C": "安提瓜",
            },
        }
        (self.orders_dir / "telegram_signal.jsonl").write_text(
            json.dumps(prior, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        message = dict(
            self.message,
            message_id="428501",
            text=ORDER_CARD_SIGNAL,
            received_ts=1787739075,
        )

        result = self._process(message=message, games=[])

        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(result["order"]["gid"], "9000001")
        self.assertEqual(result["order"]["raw"]["GNUM_H"], "712001")

    def test_dry_run_resolves_but_does_not_write_order(self) -> None:
        result = self._process()
        event = json.loads(self.event_log_path.read_text(encoding="utf-8").strip())

        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(result["order"]["gid"], "8923001")
        self.assertGreaterEqual(result["processing_ms"], 0)
        self.assertGreaterEqual(result["parse_ms"], 0)
        self.assertGreaterEqual(result["games_load_ms"], 0)
        self.assertEqual(event["processing_ms"], result["processing_ms"])
        self.assertFalse((self.orders_dir / "telegram_signal.jsonl").exists())

    def test_refreshes_fixture_identities_after_cached_match_miss(self) -> None:
        refreshed = []

        def refresh(_signal):
            refreshed.append(True)
            return [GAME]

        result = self._process(games=[], games_refresher=refresh)

        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(result["order"]["gid"], GAME["gid"])
        self.assertEqual(refreshed, [True])

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

    def test_native_order_card_can_resolve_by_team_names(self) -> None:
        game = {
            "gid": "9000001",
            "league": "中美洲杯",
            "teamH": "希尔迪奥诺",
            "teamC": "安提瓜",
        }
        result = self._process(
            message=dict(self.message, message_id="428500", text=ORDER_CARD_SIGNAL),
            games=[game],
        )

        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(result["order"]["gid"], "9000001")
        self.assertEqual(result["order"]["line"], "小 2")

    def test_load_games_uses_local_snapshot_identity(self) -> None:
        from hdzz.core.telegram_signal import watcher

        data_dir = self.orders_dir.parent / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "odds_snapshot_cache.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "matches": {
                        "live|9000001": {
                            "gid": "9000001",
                            "meta": {
                                "masterGid": "9000001",
                                "league": "中美洲杯",
                                "teamH": "希尔迪奥诺",
                                "teamC": "安提瓜",
                            },
                        }
                    },
                    "aliases": {},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        with mock.patch.object(watcher, "_webui_games") as remote:
            games = watcher.load_games_from_webui(
                {
                    "mode": "live",
                    "home_number": "",
                    "away_number": "",
                    "league": "中美洲杯",
                    "home": "希尔迪奥诺",
                    "away": "安提瓜",
                },
                base_url="http://127.0.0.1:2299",
                data_dir=data_dir,
                orders_dir=self.orders_dir,
            )

        self.assertEqual(games[0]["gid"], "9000001")
        self.assertEqual(games[0]["teamH"], "希尔迪奥诺")
        self.assertEqual(games[0]["_identity_source"], "snapshot_cache")
        remote.assert_not_called()

    def test_webui_fixture_identity_cache_avoids_repeated_http_request(self) -> None:
        from hdzz.core.telegram_signal import watcher

        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps(
            {"ok": True, "games": [{"gid": "9000001", "teamH": "主队", "teamC": "客队"}]}
        ).encode("utf-8")
        with watcher._WEBUI_GAMES_CACHE_LOCK:
            watcher._WEBUI_GAMES_CACHE.clear()
            watcher._WEBUI_GAMES_INFLIGHT.clear()
            watcher._WEBUI_GAMES_LAST_ATTEMPT.clear()
        with mock.patch.object(watcher, "urlopen", return_value=response) as open_url:
            first = watcher._webui_games({"mode": "live"}, "http://127.0.0.1:9999", force_refresh=True)
            second = watcher._webui_games({"mode": "live"}, "http://127.0.0.1:9999")

        self.assertEqual(first, second)
        self.assertEqual(first[0]["_identity_source"], "webui_member_cache")
        open_url.assert_called_once()
        self.assertEqual(open_url.call_args.kwargs["timeout"], watcher.WEBUI_GAMES_REQUEST_TIMEOUT_S)

    def test_webui_fixture_identity_cache_suppresses_immediate_forced_retry(self) -> None:
        from hdzz.core.telegram_signal import watcher

        with watcher._WEBUI_GAMES_CACHE_LOCK:
            watcher._WEBUI_GAMES_CACHE.clear()
            watcher._WEBUI_GAMES_INFLIGHT.clear()
            watcher._WEBUI_GAMES_LAST_ATTEMPT.clear()
        with mock.patch.object(watcher, "urlopen", side_effect=TimeoutError("slow member")) as open_url:
            first = watcher._webui_games({"mode": "live"}, "http://127.0.0.1:9999")
            calls_after_first = open_url.call_count
            second = watcher._webui_games({"mode": "live"}, "http://127.0.0.1:9999", force_refresh=True)

        self.assertEqual(first, [])
        self.assertEqual(second, [])
        self.assertGreater(calls_after_first, 0)
        self.assertEqual(open_url.call_count, calls_after_first)

    def test_main_reconnects_after_transient_telethon_error(self) -> None:
        from hdzz.core.telegram_signal import watcher

        errors = iter([RuntimeError("Could not find a matching Constructor ID"), KeyboardInterrupt()])

        def fake_run(coroutine):
            coroutine.close()
            raise next(errors)

        with mock.patch.object(watcher.asyncio, "run", side_effect=fake_run) as run, mock.patch.object(
            watcher.time, "sleep"
        ) as sleep:
            result = watcher.main(["--events", str(self.event_log_path)])

        self.assertEqual(result, 0)
        self.assertEqual(run.call_count, 2)
        sleep.assert_called_once_with(3)

    def test_main_does_not_retry_terminal_configuration_error(self) -> None:
        from hdzz.core.telegram_signal import watcher

        def fake_run(coroutine):
            coroutine.close()
            raise RuntimeError("telegram_login_required")

        with mock.patch.object(watcher.asyncio, "run", side_effect=fake_run) as run, mock.patch.object(
            watcher.time, "sleep"
        ) as sleep:
            result = watcher.main(["--events", str(self.event_log_path)])

        self.assertEqual(result, 1)
        run.assert_called_once()
        sleep.assert_not_called()

    def test_poll_window_baselines_existing_history_then_returns_only_new_messages(self) -> None:
        from hdzz.core.telegram_signal.watcher import _poll_message_window

        messages = [SimpleNamespace(id=103), SimpleNamespace(id=101), SimpleNamespace(id=102)]

        baseline_pending, baseline = _poll_message_window(messages, None)
        pending, newest = _poll_message_window(messages, 101)

        self.assertEqual(baseline_pending, [])
        self.assertEqual(baseline, 103)
        self.assertEqual([message.id for message in pending], [102, 103])
        self.assertEqual(newest, 103)

    def test_telethon_protocol_log_filter_removes_raw_payload(self) -> None:
        from hdzz.core.telegram_signal.watcher import _TelethonUpdatesLogFilter

        record = logging.LogRecord(
            "telethon.client.updates",
            logging.WARNING,
            __file__,
            1,
            "Cannot get difference since the account is likely misusing the session: %s",
            ("Remaining bytes: sensitive",),
            None,
        )

        self.assertTrue(_TelethonUpdatesLogFilter().filter(record))
        self.assertEqual(record.getMessage(), "telegram_protocol_sync_error")


if __name__ == "__main__":
    unittest.main()
