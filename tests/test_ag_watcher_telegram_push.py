import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
APP = ROOT / "app"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))


from hdzz.core.watcher import ag_watcher  # noqa: E402


class TestAgWatcherTelegramPush(unittest.TestCase):
    def test_order_inline_keyboard_matches_6699_actions(self) -> None:
        keyboard = ag_watcher._order_inline_keyboard(
            "OID-23817049323",
            ui_theme={"button_custom_emoji_ids": {"res": "111", "amt": "222"}},
        )

        self.assertEqual(
            keyboard,
            {
                "inline_keyboard": [
                    [
                        {
                            "text": "跟单结果",
                            "style": "primary",
                            "callback_data": "odr|res|23817049323",
                            "icon_custom_emoji_id": "111",
                        },
                        {
                            "text": "跟单日志",
                            "style": "primary",
                            "callback_data": "odr|log|23817049323",
                        },
                    ],
                    [
                        {
                            "text": "输赢结果",
                            "style": "danger",
                            "callback_data": "odr|pnl|23817049323",
                        },
                        {
                            "text": "跟单报表",
                            "style": "success",
                            "callback_data": "odr|amt|23817049323",
                            "icon_custom_emoji_id": "222",
                        },
                    ],
                ]
            },
        )

    def test_tg_send_falls_back_to_direct_when_proxy_ssl_fails(self) -> None:
        proxy_ssl_error = ag_watcher.requests.exceptions.SSLError(
            "CERTIFICATE_VERIFY_FAILED: self-signed certificate in certificate chain"
        )
        ok_resp = Mock()
        ok_resp.raise_for_status.return_value = None
        ok_resp.json.return_value = {"ok": True, "result": {"message_id": 7788}}

        with patch("hdzz.core.watcher.ag_watcher.requests.post", side_effect=[proxy_ssl_error, ok_resp]) as post_mock:
            mid = ag_watcher.tg_send(
                bot_token="token",
                chat_id="-100123",
                html="<b>hello</b>",
                timeout_s=10,
                proxy="http://127.0.0.1:8888",
            )

        self.assertEqual(mid, 7788)
        self.assertEqual(post_mock.call_count, 2)
        self.assertEqual(
            post_mock.call_args_list[0].kwargs.get("proxies"),
            {"http": "http://127.0.0.1:8888", "https": "http://127.0.0.1:8888"},
        )
        self.assertIsNone(post_mock.call_args_list[1].kwargs.get("proxies"))

    def test_send_order_push_falls_back_to_text_when_send_photo_fails(self) -> None:
        with (
            patch("hdzz.core.watcher.ag_watcher.tg_send_photo", side_effect=RuntimeError("sendPhoto_http_error: 400")),
            patch("hdzz.core.watcher.ag_watcher.tg_send", return_value=5566) as tg_send_mock,
        ):
            mid = ag_watcher.send_order_push_to_telegram(
                bot_token="token",
                chat_id="-100123",
                html="<b>新注单提醒</b>",
                preview_photo_ref="/tmp/preview.jpg",
                preview_url="",
                reply_markup={"inline_keyboard": []},
                timeout_s=10,
                proxy="",
            )

        self.assertEqual(mid, 5566)
        self.assertEqual(tg_send_mock.call_count, 1)

    def test_initial_order_message_uses_6699_pending_template(self) -> None:
        txt = ag_watcher.format_telegram_message(
            "zxcv226699",
            {
                "orderId": "23817049323",
                "league": "埃及超级联赛-附加赛",
                "sides": "现代体育 v 塔拉雅 (0 - 0)",
                "line": "大 0.5",
                "odds": "0.86",
                "stake": "100",
                "betType": "(滚球) 大 / 小",
                "date": "04-29-2026",
                "time": "14:17:57",
            },
        )

        self.assertIn("🤣新注单提醒 🤣", txt)
        self.assertIn("🤣埃及超级联赛-附加赛", txt)
        self.assertIn("🤣现代体育 v 塔拉雅 (0 - 0)", txt)
        self.assertIn("📍 状态：滚球", txt)
        self.assertIn("玩法：进球大小", txt)
        self.assertIn("时段：全场", txt)
        self.assertIn("选择：大 0.5 @0.86", txt)
        self.assertIn("💰 原注金额：100", txt)
        self.assertIn("💲 跟单金额：等待结果", txt)
        self.assertIn("🐦‍⬛跟单成功：处理中", txt)
        self.assertIn("来源后台：zxcv226699", txt)
        self.assertIn("🧠单号：23817049323", txt)
        self.assertIn("🗓时间：04-29-2026 14:17:57", txt)
        self.assertNotIn("🐸", txt)

    def test_initial_order_message_separates_corner_market_period_and_selection(self) -> None:
        txt = ag_watcher.format_telegram_message(
            "zxcv226699",
            {
                "orderId": "23888880004",
                "league": "测试联赛",
                "sides": "主队 v 客队 (0 - 0)",
                "line": "大 10.5",
                "odds": "0.88",
                "stake": "100",
                "betType": "(滚球) 角球数 - 大 / 小",
                "date": "05-19-2026",
                "time": "14:17:57",
            },
        )

        self.assertIn("📍 状态：滚球", txt)
        self.assertIn("玩法：角球大小", txt)
        self.assertIn("时段：全场", txt)
        self.assertIn("选择：大 10.5 @0.88", txt)

    def test_backend_order_message_contains_only_source_order_fields(self) -> None:
        txt = ag_watcher.format_backend_order_telegram_message(
            {
                "league": "联赛杯(美加墨)",
                "sides": "托拉卡 v 奥斯丁 (4 - 4)",
                "line": "小 11",
                "odds": "0.70",
                "stake": "100.0",
                "betType": "(滚球) 角球数 - 大 / 小",
            }
        )

        self.assertIn("🤣新注单提醒 🤣", txt)
        self.assertIn("🤣联赛杯(美加墨)", txt)
        self.assertIn("🤣托拉卡 v 奥斯丁 (4 - 4)", txt)
        self.assertIn("状态：滚球", txt)
        self.assertIn("玩法：角球大小", txt)
        self.assertIn("时段：全场", txt)
        self.assertIn("选择：小 11 @0.70", txt)
        self.assertIn("金额：100", txt)
        self.assertNotIn("跟单金额", txt)
        self.assertNotIn("跟单成功", txt)
        self.assertNotIn("会员", txt)

    def test_initial_order_message_names_nth_corner_market(self) -> None:
        txt = ag_watcher.format_telegram_message(
            "8hu557555",
            {
                "orderId": "23935601855",
                "league": "亚足联女子冠军联赛",
                "sides": "水原(女) v 纳戈扬(女) (3 - 2)",
                "line": "水原(女)",
                "odds": "0.85",
                "stake": "1000",
                "betType": "(滚球) 角球数 - 第六个角球",
                "date": "05-20-2026",
                "time": "07:22:16",
            },
        )

        self.assertIn("玩法：第六个角球", txt)
        self.assertIn("时段：全场", txt)
        self.assertIn("选择：水原(女) @0.85", txt)

    def test_initial_order_message_separates_first_half_card_market_and_selection(self) -> None:
        txt = ag_watcher.format_telegram_message(
            "zxcv226699",
            {
                "orderId": "23888880005",
                "league": "测试联赛",
                "sides": "主队 v 客队 (0 - 0)",
                "line": "小 1.5",
                "odds": "1.01",
                "stake": "100",
                "betType": "罚牌数 大 / 小-上半场",
                "date": "05-19-2026",
                "time": "14:17:57",
            },
        )

        self.assertIn("📍 状态：早盘", txt)
        self.assertIn("玩法：罚牌大小", txt)
        self.assertIn("时段：上半场", txt)
        self.assertIn("选择：小 1.5 @1.01", txt)

    def test_poll_once_sends_initial_order_push_before_follow_result(self) -> None:
        target = ag_watcher.Target(
            name="zxcv226699",
            base_url="https://ag.example.test",
            ver="version-test",
            uid="uid",
            cookie="sid=1",
        )
        first_seen_state = {
            "initialized": 1,
            "sel_maxid": "100",
            "seen": [],
        }
        response = {
            "maxid": "101",
            "wagers": [
                {
                    "TID": "OID-101",
                    "NAME0": "member01",
                    "GOLD": "50",
                    "GT": "足球",
                    "SHOWTEXT_LEAGUE": "测试联赛",
                    "SHOWTEXT_TEAM": "主队 v 客队 (0 - 0)",
                    "WAGERSTYPE": "(滚球) 角球数 - 大 / 小",
                    "ORDER_TYPE": "大",
                    "ORDER_CON": "10.5",
                    "IORATIO": "0.88",
                    "DATE": "05-19-2026",
                    "TIME": "14:17:57",
                }
            ],
        }

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            state_dir = root / "state"
            orders_dir = root / "orders"
            config_path = root / "config.json"
            config_path.write_text('{"targets":[]}', encoding="utf-8")
            ag_watcher.save_state(state_dir, target.name, first_seen_state)

            with (
                patch("hdzz.core.watcher.ag_watcher.request_wagers", return_value=response),
                patch("hdzz.core.watcher.ag_watcher.send_order_push_to_telegram", return_value=7788) as push_mock,
            ):
                ag_watcher.poll_once(
                    session=Mock(),
                    target=target,
                    state_dir=state_dir,
                    config_path=config_path,
                    bot_token="token",
                    default_chat_id="-100123",
                    order_forward_enabled=True,
                    timeout_s=10,
                    tg_timeout_s=10,
                    tg_proxy="",
                    order_preview_image="",
                    ui_theme=None,
                    debug=False,
                    only_orders=True,
                    orders_dir=orders_dir,
                    interval_s=3,
                )

            self.assertEqual(push_mock.call_count, 1)
            kwargs = push_mock.call_args.kwargs
            self.assertEqual(kwargs["chat_id"], "-100123")
            self.assertIn("玩法：角球大小", kwargs["html"])
            self.assertIn("选择：大 10.5 @0.88", kwargs["html"])

            order_lines = (orders_dir / "zxcv226699.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(order_lines), 1)
            saved = ag_watcher.json.loads(order_lines[0])
            self.assertEqual(saved["_tg_message_id"], 7788)
            self.assertEqual(saved["_tg_chat_id"], "-100123")

    def test_poll_once_sends_independent_backend_order_without_persisting_message_id(self) -> None:
        target = ag_watcher.Target(
            name="source-backend",
            base_url="https://ag.example.test",
            ver="version-test",
            uid="uid",
            cookie="sid=1",
        )
        response = {
            "maxid": "101",
            "wagers": [
                {
                    "TID": "OID-BACKEND-101",
                    "NAME0": "member01",
                    "GOLD": "100.0",
                    "GT": "足球",
                    "SHOWTEXT_LEAGUE": "联赛杯(美加墨)",
                    "SHOWTEXT_TEAM": "托拉卡 v 奥斯丁 (4 - 4)",
                    "WAGERSTYPE": "(滚球) 角球数 - 大 / 小",
                    "ORDER_TYPE": "小",
                    "ORDER_CON": "11",
                    "IORATIO": "0.70",
                }
            ],
        }

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            state_dir = root / "state"
            orders_dir = root / "orders"
            config_path = root / "config.json"
            config_path.write_text('{"targets":[]}', encoding="utf-8")
            ag_watcher.save_state(
                state_dir,
                target.name,
                {"initialized": 1, "sel_maxid": "100", "seen": []},
            )

            with (
                patch("hdzz.core.watcher.ag_watcher.request_wagers", return_value=response),
                patch("hdzz.core.watcher.ag_watcher.tg_send", return_value=9901) as send_mock,
            ):
                ag_watcher.poll_once(
                    session=Mock(),
                    target=target,
                    state_dir=state_dir,
                    config_path=config_path,
                    bot_token="",
                    default_chat_id="",
                    order_forward_enabled=False,
                    timeout_s=10,
                    tg_timeout_s=10,
                    tg_proxy="",
                    order_preview_image="",
                    ui_theme=None,
                    debug=False,
                    only_orders=True,
                    orders_dir=orders_dir,
                    interval_s=3,
                    backend_order_push_enabled=True,
                    backend_order_bot_token="backend-token",
                    backend_order_chat_id="-100backend",
                    backend_order_proxy="socks5://127.0.0.1:7892",
                )

            self.assertEqual(send_mock.call_count, 1)
            args = send_mock.call_args.args
            kwargs = send_mock.call_args.kwargs
            self.assertEqual(args[0], "backend-token")
            self.assertEqual(args[1], "-100backend")
            self.assertIn("金额：100", args[2])
            self.assertEqual(kwargs["proxy"], "socks5://127.0.0.1:7892")
            saved = ag_watcher.json.loads(
                (orders_dir / "source-backend.jsonl").read_text(encoding="utf-8").splitlines()[0]
            )
            self.assertNotIn("_tg_message_id", saved)
            self.assertNotIn("_tg_chat_id", saved)

    def test_poll_once_skips_initial_order_push_for_disabled_follow_target(self) -> None:
        target = ag_watcher.Target(
            name="ccvv445566",
            base_url="https://ag.example.test",
            ver="version-test",
            uid="uid",
            cookie="sid=1",
        )
        first_seen_state = {
            "initialized": 1,
            "sel_maxid": "100",
            "seen": [],
        }
        response = {
            "maxid": "101",
            "wagers": [
                {
                    "TID": "OID-101",
                    "NAME0": "member01",
                    "GOLD": "50",
                    "GT": "足球",
                    "SHOWTEXT_LEAGUE": "测试联赛",
                    "SHOWTEXT_TEAM": "主队 v 客队 (0 - 0)",
                    "WAGERSTYPE": "(滚球) 角球数 - 大 / 小",
                    "ORDER_TYPE": "大",
                    "ORDER_CON": "10.5",
                    "IORATIO": "0.88",
                    "DATE": "05-19-2026",
                    "TIME": "14:17:57",
                }
            ],
        }

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            state_dir = root / "state"
            orders_dir = root / "orders"
            data_dir = root / "data"
            config_path = root / "config.json"
            data_dir.mkdir(parents=True)
            config_path.write_text('{"targets":[]}', encoding="utf-8")
            (data_dir / "follow_settings.json").write_text(
                '{"targets":[{"name":"ccvv445566","enabled":false,"ratio_percent":0}]}',
                encoding="utf-8",
            )
            ag_watcher.save_state(state_dir, target.name, first_seen_state)

            with (
                patch("hdzz.core.watcher.ag_watcher.request_wagers", return_value=response),
                patch("hdzz.core.watcher.ag_watcher.send_order_push_to_telegram", return_value=7788) as push_mock,
            ):
                ag_watcher.poll_once(
                    session=Mock(),
                    target=target,
                    state_dir=state_dir,
                    config_path=config_path,
                    bot_token="token",
                    default_chat_id="-100123",
                    order_forward_enabled=True,
                    timeout_s=10,
                    tg_timeout_s=10,
                    tg_proxy="",
                    order_preview_image="",
                    ui_theme=None,
                    debug=False,
                    only_orders=True,
                    orders_dir=orders_dir,
                    interval_s=3,
                )

            self.assertEqual(push_mock.call_count, 0)
            order_lines = (orders_dir / "ccvv445566.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(order_lines), 1)
            saved = ag_watcher.json.loads(order_lines[0])
            self.assertNotIn("_tg_message_id", saved)
            self.assertNotIn("_tg_chat_id", saved)


if __name__ == "__main__":
    unittest.main()
