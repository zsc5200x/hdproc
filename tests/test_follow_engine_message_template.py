import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
APP = ROOT / "app"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))


class TestFollowEngineMessageTemplate(unittest.TestCase):
    def test_order_inline_keyboard_matches_6699_actions(self) -> None:
        from hdzz.core.follower.engine import _order_inline_keyboard

        kb = _order_inline_keyboard("OID-23410986818")
        self.assertEqual(
            kb,
            {
                "inline_keyboard": [
                    [
                        {"text": "跟单结果", "style": "primary", "callback_data": "odr|res|23410986818"},
                        {"text": "跟单日志", "style": "primary", "callback_data": "odr|log|23410986818"},
                    ],
                    [
                        {"text": "输赢结果", "style": "danger", "callback_data": "odr|pnl|23410986818"},
                        {"text": "跟单报表", "style": "success", "callback_data": "odr|amt|23410986818"},
                    ],
                ]
            },
        )

    def test_order_inline_keyboard_applies_custom_emoji_theme(self) -> None:
        from hdzz.core.follower.engine import _order_inline_keyboard

        kb = _order_inline_keyboard(
            "OID-23410986818",
            ui_theme={
                "button_custom_emoji_ids": {
                    "res": "5936176756246452306",
                    "log": "5863955137407490963",
                    "pnl": "5936235940895792238",
                    "amt": "5422676844363149011",
                }
            },
        )
        rows = kb["inline_keyboard"]
        self.assertEqual(rows[0][0]["icon_custom_emoji_id"], "5936176756246452306")
        self.assertEqual(rows[0][1]["icon_custom_emoji_id"], "5863955137407490963")
        self.assertEqual(rows[1][0]["icon_custom_emoji_id"], "5936235940895792238")
        self.assertEqual(rows[1][1]["icon_custom_emoji_id"], "5422676844363149011")

    def test_combined_message_supports_custom_emoji_tags(self) -> None:
        from hdzz.core.follower.engine import build_order_follow_combined_message

        order = {
            "orderId": "23621175314",
            "league": "巴拉圭甲组联赛",
            "sides": "雷科莱塔 v 鲁毕奥 (0 - 0)",
            "line": "大 1 / 1.5",
            "odds": "0.91",
            "stake": "50",
            "target": "zxcv226699",
            "betType": "足球 (滚球) 大 / 小",
            "date": "03-26-2026",
            "time": "19:53:55",
        }
        follow_result = {
            "split_total_plan": 50,
            "split_part_results": {
                "1": {"ok": True, "stake": 50},
            },
        }
        ui_theme = {
            "divider_custom_emoji_ids": [
                "5422676844363149011",
                "5424760337293402339",
                "5424892691005595203",
                "5424765306570563109",
                "5425144049671627237",
                "5424955857089616771",
                "5424813272765330422",
                "5424953443317998789",
                "5422556907401407999",
                "5422464664388790603",
            ],
            "text_custom_emoji_ids": {
                "title": "5837208434730077905",
                "title_suffix": "5837208434730077905",
                "league": "5861703685486159871",
                "match": "5942739401850232016",
                "status": "5936176756246452306",
                "stake": "5224257782013769471",
                "follow_stake": "5298682684715840924",
                "follow_success": "5298731965170597296",
                "follow_success_suffix": "5373033855714357270",
                "order_id": "6034918793115868355",
                "time": "5859649269484492604",
            }
        }

        txt = build_order_follow_combined_message(order=order, follow_result=follow_result, ui_theme=ui_theme)
        self.assertIn('<tg-emoji emoji-id="5837208434730077905">🤣</tg-emoji>新注单提醒 <tg-emoji emoji-id="5837208434730077905">🤣</tg-emoji>', txt)
        self.assertIn('<tg-emoji emoji-id="5422676844363149011">🇪🇸</tg-emoji>', txt)
        self.assertIn('<tg-emoji emoji-id="5422464664388790603">🇩🇪</tg-emoji>', txt)
        self.assertIn('<tg-emoji emoji-id="5861703685486159871">🤣</tg-emoji>巴拉圭甲组联赛', txt)
        self.assertIn('<tg-emoji emoji-id="5942739401850232016">🤣</tg-emoji>雷科莱塔 v 鲁毕奥 (0 - 0)', txt)
        self.assertIn('<tg-emoji emoji-id="5936176756246452306">📍</tg-emoji> 状态：滚球', txt)
        self.assertIn('玩法：进球大小', txt)
        self.assertIn('时段：全场', txt)
        self.assertIn('选择：大 1 / 1.5 @0.91', txt)
        self.assertIn('<tg-emoji emoji-id="5224257782013769471">💰</tg-emoji> 原注金额：50', txt)
        self.assertIn('<tg-emoji emoji-id="5298682684715840924">💲</tg-emoji> 跟单金额：50', txt)
        self.assertIn('<tg-emoji emoji-id="5298731965170597296">🐦‍⬛</tg-emoji>跟单成功：50', txt)
        self.assertIn("来源后台：zxcv226699", txt)
        self.assertIn("单号：23621175314", txt)
        self.assertIn("时间：03-26-2026 19:53:55", txt)

    def test_combined_message_summarizes_planned_and_successful_amounts(self) -> None:
        from hdzz.core.follower.engine import build_order_follow_combined_message

        order = {
            "orderId": "23410986818",
            "league": "德国甲组联赛",
            "sides": "多特蒙德 v 美因茨05",
            "line": "美因茨05 +1",
            "odds": "0.89",
            "stake": "100",
            "target": "hxjk06wc1y",
            "betType": "足球 (滚球) 让球",
            "date": "02-13-2026",
            "time": "01:44:55",
        }
        follow_result = {
            "split_total_plan": 200,
            "split_part_results": {
                "1": {"ok": True, "stake": 100},
                "2": {"ok": False, "stake": 100},
                "3": {"ok": True, "stake": 50},
            },
        }

        txt = build_order_follow_combined_message(order=order, follow_result=follow_result)
        self.assertIn("🤣新注单提醒", txt)
        self.assertIn("📍 状态：滚球", txt)
        self.assertIn("💰 原注金额：100", txt)
        self.assertIn("💲 跟单金额：200", txt)
        self.assertIn("🐦‍⬛跟单成功：150", txt)
        self.assertIn("来源后台：hxjk06wc1y", txt)
        self.assertIn("单号：23410986818", txt)
        self.assertIn("时间：02-13-2026 01:44:55", txt)

    def test_combined_message_summarizes_terminal_split_status_records(self) -> None:
        from hdzz.core.follower.engine import build_order_follow_combined_message

        order = {
            "orderId": "25131120113",
            "league": "厄瓜多尔乙组联赛",
            "sides": "波图维耶霍 v 七月二十二 (2 - 0)",
            "line": "小 3 / 3.5",
            "odds": "0.79",
            "stake": "100",
            "target": "claude66",
            "betType": "足球 (滚球) 大 / 小",
            "date": "08-21-2026",
            "time": "17:42:16",
        }
        follow_result = {
            "status": "ok",
            "split_total_plan": 100,
            "split_parts": [
                {"status": "ok", "stake": 50, "ticket_id": "25131121066"},
                {"status": "ok", "stake": 50, "ticket_id": "25131121535"},
            ],
        }

        txt = build_order_follow_combined_message(order=order, follow_result=follow_result)

        self.assertIn("💲 跟单金额：100", txt)
        self.assertIn("🐦‍⬛跟单成功：100", txt)

    def test_combined_message_marks_reverse_order_and_success_amount(self) -> None:
        from hdzz.core.follower.engine import build_order_follow_combined_message

        order = {
            "orderId": "23583642796",
            "league": "乌克兰超级联赛",
            "sides": "梅塔利斯特 v 波尔塔瓦 (1 - 0)",
            "line": "小 2.5",
            "odds": "0.95",
            "stake": "100",
            "target": "zxcv226699",
            "betType": "足球 (滚球) 大 / 小",
            "date": "03-19-2026",
            "time": "13:04:02",
        }
        follow_result = {
            "stake": 150,
            "ok": True,
            "reverse_rule": {
                "applied": True,
                "source": "all",
            },
        }

        txt = build_order_follow_combined_message(order=order, follow_result=follow_result)
        self.assertIn("💰 原注金额：100", txt)
        self.assertIn("💲 跟单金额：150（反打订单）", txt)
        self.assertIn("🐦‍⬛跟单成功：150", txt)

    def test_combined_message_separates_corner_market_period_and_selection(self) -> None:
        from hdzz.core.follower.engine import build_order_follow_combined_message

        order = {
            "orderId": "23888880006",
            "league": "测试联赛",
            "sides": "主队 v 客队 (0 - 0)",
            "line": "大 10.5",
            "odds": "0.88",
            "stake": "100",
            "target": "zxcv226699",
            "betType": "(滚球) 角球数 - 大 / 小",
            "date": "05-19-2026",
            "time": "14:17:57",
        }
        follow_result = {"stake": 100, "ok": True}

        txt = build_order_follow_combined_message(order=order, follow_result=follow_result)
        self.assertIn("📍 状态：滚球", txt)
        self.assertIn("玩法：角球大小", txt)
        self.assertIn("时段：全场", txt)
        self.assertIn("选择：大 10.5 @0.88", txt)

    def test_combined_message_names_nth_corner_market(self) -> None:
        from hdzz.core.follower.engine import build_order_follow_combined_message

        order = {
            "orderId": "23935601855",
            "league": "亚足联女子冠军联赛",
            "sides": "水原(女) v 纳戈扬(女) (3 - 2)",
            "line": "水原(女)",
            "odds": "0.85",
            "stake": "1000",
            "target": "8hu557555",
            "betType": "(滚球) 角球数 - 第六个角球",
            "date": "05-20-2026",
            "time": "07:22:16",
        }
        follow_result = {"stake": 1000, "ok": False, "message": "已收到信号，等待跟单结果。"}

        txt = build_order_follow_combined_message(order=order, follow_result=follow_result)
        self.assertIn("玩法：第六个角球", txt)
        self.assertIn("时段：全场", txt)
        self.assertIn("选择：水原(女) @0.85", txt)

    def test_combined_message_separates_first_half_card_market_and_selection(self) -> None:
        from hdzz.core.follower.engine import build_order_follow_combined_message

        order = {
            "orderId": "23888880007",
            "league": "测试联赛",
            "sides": "主队 v 客队 (0 - 0)",
            "line": "小 1.5",
            "odds": "1.01",
            "stake": "100",
            "target": "zxcv226699",
            "betType": "罚牌数 大 / 小-上半场",
            "date": "05-19-2026",
            "time": "14:17:57",
        }
        follow_result = {"stake": 100, "ok": True}

        txt = build_order_follow_combined_message(order=order, follow_result=follow_result)
        self.assertIn("📍 状态：早盘", txt)
        self.assertIn("玩法：罚牌大小", txt)
        self.assertIn("时段：上半场", txt)
        self.assertIn("选择：小 1.5 @1.01", txt)


if __name__ == "__main__":
    unittest.main()
