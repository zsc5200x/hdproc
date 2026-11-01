import unittest


HANDICAP_SIGNAL = """10215c监控 会员：cpb3k（D1）单号：***88407
时间：07-26-2026 13:32:24
会员：cpb3k
单号：24929088407
足球：(滚球) 让球
内容：
斯洛伐克超级联赛
[701356] vs [701355]
拜斯迪卡   v  布拉迪斯拉发   (0 - 2)
布拉迪斯拉发   -0.5 / 1 @ 0.99

￥：5270"""


TOTAL_SIGNAL = """10215c监控 会员：cpb3k（D1）单号：***65904
时间：07-26-2026 13:20:08
会员：cpb3k
单号：24928965904
足球：(滚球) 大 / 小
内容：
挪威超级联赛
[700336] vs [700335]
奥勒松   v  维京   (0 - 0)
大 3 / 3.5 @ 0.93

￥：12000"""


ORDER_CARD_SIGNAL = """🤣新注单提醒 🤣
🤣中美洲杯
🤣希尔迪奥诺 v 安提瓜 (1 - 0)

📍 状态：滚球
玩法：进球大小
时段：全场
选择：小 2 @0.98

💰 原注金额：20000
💲 跟单金额：5601
🐦‍⬛跟单成功：5601 🤣

来源后台：uq20mvvl4t
🧠单号：25172905787
🗓时间：08-26-2026 21:51:11"""


EARLY_ORDER_CARD_SIGNAL = """🤣新注单提醒 🤣
🇪🇸🇪🇸🇪🇸🏴󠁧󠁢󠁥󠁮󠁧󠁿🏴󠁧󠁢󠁥󠁮󠁧󠁿🏴󠁧󠁢󠁥󠁮󠁧󠁿🏴󠁧󠁢󠁥󠁮󠁧󠁿🇩🇪🇫🇷🇩🇪
🤣西班牙甲组联赛
🤣巴塞罗那 v 毕尔巴鄂竞技

📍 状态：早盘
玩法：让球
时段：全场
选择：毕尔巴鄂竞技 +1.5 / 2 @-0.91

💰 原注金额：100
💲 跟单金额：100
🐦‍⬛跟单成功：100 🤣

来源后台：claude66
🧠单号：25173544649
🗓时间：08-27-2026 01:22:18"""


class TelegramSignalParserTest(unittest.TestCase):
    def test_parses_live_handicap_signal(self) -> None:
        from hdzz.core.telegram_signal.parser import parse_telegram_bet_signal

        signal = parse_telegram_bet_signal(HANDICAP_SIGNAL)

        self.assertEqual(signal["monitor"], "10215c")
        self.assertEqual(signal["member"], "cpb3k")
        self.assertEqual(signal["order_id"], "24929088407")
        self.assertEqual(signal["date"], "07-26-2026")
        self.assertEqual(signal["time"], "13:32:24")
        self.assertEqual(signal["sport"], "足球")
        self.assertEqual(signal["mode"], "live")
        self.assertEqual(signal["bet_type"], "(滚球) 让球")
        self.assertEqual(signal["market"], "handicap")
        self.assertEqual(signal["league"], "斯洛伐克超级联赛")
        self.assertEqual(signal["home_number"], "701356")
        self.assertEqual(signal["away_number"], "701355")
        self.assertEqual(signal["home"], "拜斯迪卡")
        self.assertEqual(signal["away"], "布拉迪斯拉发")
        self.assertEqual(signal["score_home"], 0)
        self.assertEqual(signal["score_away"], 2)
        self.assertEqual(signal["selection"], "布拉迪斯拉发")
        self.assertEqual(signal["line_text"], "-0.5 / 1")
        self.assertEqual(signal["line"], "布拉迪斯拉发 -0.5 / 1")
        self.assertAlmostEqual(signal["line_value"], -0.75)
        self.assertEqual(signal["odds"], "0.99")
        self.assertEqual(signal["amount"], 5270.0)

    def test_parses_live_total_signal(self) -> None:
        from hdzz.core.telegram_signal.parser import parse_telegram_bet_signal

        signal = parse_telegram_bet_signal(TOTAL_SIGNAL)

        self.assertEqual(signal["market"], "total_over")
        self.assertEqual(signal["selection"], "大")
        self.assertEqual(signal["line_text"], "3 / 3.5")
        self.assertEqual(signal["line"], "大 3 / 3.5")
        self.assertAlmostEqual(signal["line_value"], 3.25)
        self.assertEqual(signal["home_number"], "700336")
        self.assertEqual(signal["away_number"], "700335")
        self.assertEqual(signal["amount"], 12000.0)

    def test_accepts_crlf_and_ascii_amount_prefix(self) -> None:
        from hdzz.core.telegram_signal.parser import parse_telegram_bet_signal

        text = HANDICAP_SIGNAL.replace("\n", "\r\n").replace("￥：5270", "¥: 5,270")
        signal = parse_telegram_bet_signal(text)

        self.assertEqual(signal["amount"], 5270.0)
        self.assertEqual(signal["order_id"], "24929088407")

    def test_parses_native_order_card_format(self) -> None:
        from hdzz.core.telegram_signal.parser import parse_telegram_bet_signal

        signal = parse_telegram_bet_signal(ORDER_CARD_SIGNAL)

        self.assertEqual(signal["order_id"], "25172905787")
        self.assertEqual(signal["league"], "中美洲杯")
        self.assertEqual(signal["home"], "希尔迪奥诺")
        self.assertEqual(signal["away"], "安提瓜")
        self.assertEqual(signal["score_home"], 1)
        self.assertEqual(signal["score_away"], 0)
        self.assertEqual(signal["mode"], "live")
        self.assertEqual(signal["bet_type"], "(滚球) 大 / 小")
        self.assertEqual(signal["market"], "total_under")
        self.assertEqual(signal["selection"], "小")
        self.assertEqual(signal["line_text"], "2")
        self.assertEqual(signal["odds"], "0.98")
        self.assertEqual(signal["amount"], 20000.0)

    def test_native_order_card_keeps_separate_half_time_period(self) -> None:
        from hdzz.core.telegram_signal.parser import parse_telegram_bet_signal

        text = ORDER_CARD_SIGNAL.replace("时段：全场", "时段：上半场")
        signal = parse_telegram_bet_signal(text)

        self.assertEqual(signal["period"], "上半场")
        self.assertEqual(signal["bet_type"], "(滚球) 大 / 小 - 上半场")

    def test_parses_early_order_card_without_score_and_with_negative_odds(self) -> None:
        from hdzz.core.telegram_signal.parser import parse_telegram_bet_signal

        signal = parse_telegram_bet_signal(EARLY_ORDER_CARD_SIGNAL)

        self.assertEqual(signal["order_id"], "25173544649")
        self.assertEqual(signal["league"], "西班牙甲组联赛")
        self.assertEqual(signal["home"], "巴塞罗那")
        self.assertEqual(signal["away"], "毕尔巴鄂竞技")
        self.assertEqual(signal["score_home"], 0)
        self.assertEqual(signal["score_away"], 0)
        self.assertEqual(signal["mode"], "early")
        self.assertEqual(signal["market"], "handicap")
        self.assertEqual(signal["selection"], "毕尔巴鄂竞技")
        self.assertEqual(signal["line_text"], "+1.5 / 2")
        self.assertAlmostEqual(signal["line_value"], 1.75)
        self.assertEqual(signal["odds"], "-0.91")
        self.assertEqual(signal["amount"], 100.0)

    def test_rejects_unrelated_group_message(self) -> None:
        from hdzz.core.telegram_signal.parser import SignalParseError, parse_telegram_bet_signal

        with self.assertRaisesRegex(SignalParseError, "unsupported_signal"):
            parse_telegram_bet_signal("今晚比赛大家注意风险")

    def test_rejects_missing_amount(self) -> None:
        from hdzz.core.telegram_signal.parser import SignalParseError, parse_telegram_bet_signal

        text = HANDICAP_SIGNAL.replace("￥：5270", "")
        with self.assertRaisesRegex(SignalParseError, "missing_amount"):
            parse_telegram_bet_signal(text)


if __name__ == "__main__":
    unittest.main()
