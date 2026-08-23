import unittest


class TestWebuiSettlementPending(unittest.TestCase):
    def test_today_closed_is_always_pending(self) -> None:
        from hdzz.api.webui.server import _is_settlement_pending

        rec = {
            "source": "today_closed",
            "is_final": True,
            "status": "settled",
            "profit": 39.5,
            "ball_act_ret": "",
        }
        self.assertTrue(_is_settlement_pending(rec))

    def test_history_switch_can_be_settled(self) -> None:
        from hdzz.api.webui.server import _is_settlement_pending

        rec = {
            "source": "history_switch",
            "is_final": True,
            "status": "settled",
            "profit": 39.5,
            "ball_act_ret": "",
        }
        self.assertFalse(_is_settlement_pending(rec))


if __name__ == "__main__":
    unittest.main()
