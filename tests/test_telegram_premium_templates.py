import unittest

from hdzz.api.telegram.premium_bot import (
    build_full_message,
    build_group_broadcast_message,
    build_teaser_message,
    parse_callback_data,
)


class TestTelegramPremiumTemplates(unittest.TestCase):
    def test_teaser_template_masks_content(self) -> None:
        txt = build_teaser_message({"mode": "滚球"})
        self.assertIn("⚡ 出货提醒｜滚球", txt)
        self.assertIn("🏆 ***", txt)
        self.assertIn("⚽ *** vs ***", txt)
        self.assertIn("仅限已开通权限账户查看完整内容", txt)


    def test_full_template_contains_order_fields(self) -> None:
        txt = build_full_message(
            {
                "mode": "早盘",
                "league": "西班牙杯",
                "home": "马竞",
                "away": "巴萨",
                "time": "02-15 10:00:00",
                "play": "大 2.5 @1.01",
                "amount": "8",
            },
            buy_contact="@sgty888",
            business_contact="@sgty888",
        )
        self.assertIn("📣 新注单提醒｜早盘", txt)
        self.assertIn("🏆 联赛：西班牙杯", txt)
        self.assertIn("⚽ 对阵：马竞 vs 巴萨", txt)
        self.assertIn("充值请联系 @sgty888", txt)


    def test_group_broadcast_template_contains_required_fields(self) -> None:
        txt = build_group_broadcast_message(
            {
                "mode": "滚球",
                "league": "秘鲁甲组联赛",
                "sides": "库斯科 v 科梅次安特斯尤尼杜斯 (1 - 1)",
                "play": "小 4 / 4.5 @0.80",
                "amount": "20000",
                "backend_source": "ff66889",
                "order_id": "23456455769",
                "time": "02-22-2026 19:58:43",
            }
        )
        self.assertIn("📣 新注单提醒", txt)
        self.assertIn("📍 状态：滚球", txt)
        self.assertIn("🎯 玩法：小 4 / 4.5 @0.80", txt)
        self.assertIn("💰 原注金额：20000", txt)
        self.assertIn("💲 跟单金额：0", txt)
        self.assertIn("📌 跟单成功：0", txt)
        self.assertIn("👑 来源后台：ff66889", txt)
        self.assertIn("🆔 单号：23456455769", txt)
        self.assertIn("⏰ 时间：02-22-2026 19:58:43", txt)


    def test_parse_callback_data(self) -> None:
        self.assertEqual(parse_callback_data("pm|buy|234"), ("buy", "234"))
        self.assertEqual(parse_callback_data("pm|view|234"), ("view", "234"))
        self.assertEqual(parse_callback_data("pm|check|VIP001"), ("check", "VIP001"))
        self.assertIsNone(parse_callback_data("bad|buy|234"))


if __name__ == "__main__":
    unittest.main()
