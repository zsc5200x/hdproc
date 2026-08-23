import unittest

from test_telegram_signal_parser import HANDICAP_SIGNAL


class TelegramSignalIngestTest(unittest.TestCase):
    def setUp(self) -> None:
        from hdzz.core.telegram_signal.parser import parse_telegram_bet_signal

        self.signal = parse_telegram_bet_signal(HANDICAP_SIGNAL)
        self.game = {
            "gid": "8923001",
            "league": "斯洛伐克超级联赛",
            "teamH": "拜斯迪卡",
            "teamC": "布拉迪斯拉发",
            "gnumH": "701356",
            "gnumC": "701355",
        }

    def test_resolves_exact_provider_team_number_pair(self) -> None:
        from hdzz.core.telegram_signal.ingest import resolve_signal_match

        other = dict(self.game, gid="8923002", gnumH="701999", gnumC="701998")
        match = resolve_signal_match(self.signal, [other, self.game])

        self.assertEqual(match["gid"], "8923001")

    def test_rejects_reversed_provider_numbers(self) -> None:
        from hdzz.core.telegram_signal.ingest import MatchResolutionError, resolve_signal_match

        reversed_game = dict(self.game, gnumH="701355", gnumC="701356")
        with self.assertRaisesRegex(MatchResolutionError, "number_pair_not_found"):
            resolve_signal_match(self.signal, [reversed_game])

    def test_uses_exact_team_names_only_when_numbers_are_absent(self) -> None:
        from hdzz.core.telegram_signal.ingest import resolve_signal_match

        signal = dict(self.signal, home_number="", away_number="")
        match = resolve_signal_match(signal, [self.game])

        self.assertEqual(match["gid"], "8923001")

    def test_rejects_ambiguous_name_matches(self) -> None:
        from hdzz.core.telegram_signal.ingest import MatchResolutionError, resolve_signal_match

        signal = dict(self.signal, home_number="", away_number="")
        duplicate = dict(self.game, gid="8923999")
        with self.assertRaisesRegex(MatchResolutionError, "ambiguous_name_match"):
            resolve_signal_match(signal, [self.game, duplicate])

    def test_builds_follow_engine_order_shape_without_forwarding(self) -> None:
        from hdzz.core.telegram_signal.ingest import build_follow_order

        order = build_follow_order(
            self.signal,
            self.game,
            chat_id="-100778899",
            message_id=88407,
            chat_title="10215c监控",
            received_ts=1785112345,
        )

        self.assertEqual(order["target"], "telegram_signal")
        self.assertEqual(order["orderId"], "24929088407")
        self.assertEqual(order["stake"], "5270")
        self.assertEqual(order["odds"], "0.99")
        self.assertEqual(order["sides"], "拜斯迪卡 v 布拉迪斯拉发 (0 - 2)")
        self.assertEqual(order["line"], "布拉迪斯拉发 -0.5 / 1")
        self.assertEqual(order["gid"], "8923001")
        self.assertTrue(order["_skip_signal_forward"])
        self.assertEqual(order["_signal_origin"], "telegram_group")
        self.assertEqual(order["raw"]["_EVENT"], "8923001")
        self.assertEqual(order["raw"]["GID"], "8923001")
        self.assertEqual(order["raw"]["GNUM_H"], "701356")
        self.assertEqual(order["raw"]["GNUM_C"], "701355")
        self.assertEqual(order["raw"]["TEAM_H"], "拜斯迪卡")
        self.assertEqual(order["raw"]["TEAM_C"], "布拉迪斯拉发")
        self.assertEqual(order["raw"]["SCORE_H"], "0")
        self.assertEqual(order["raw"]["SCORE_C"], "2")
        self.assertEqual(order["raw"]["ORDER_TYPE"], "布拉迪斯拉发")
        self.assertEqual(order["raw"]["ORDER_CON"], "-0.5 / 1")
        self.assertEqual(order["raw"]["MARKET"], "rb")
        self.assertEqual(order["raw"]["_TG_SOURCE_CHAT_ID"], "-100778899")
        self.assertEqual(order["raw"]["_TG_SOURCE_MESSAGE_ID"], "88407")


if __name__ == "__main__":
    unittest.main()
