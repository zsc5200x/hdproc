import unittest


class TestWebuiOrderDisplayLine(unittest.TestCase):
    def test_follow_display_line_uses_submit_handicap_side_and_line(self) -> None:
        from hdzz.api.webui.server import compose_follow_display_line

        order = {
            "betType": "(滚球) 让球",
            "line": "阿兰亚体育 -0.5",
            "raw": {
                "TEAM_H": "阿兰亚体育 ",
                "TEAM_C": "高卡尔利士邦 ",
            },
        }
        follow_row = {
            "submit_params": {
                "wtype": "RE",
                "chose_team": "C",
                "con": "-1",
                "ratio": "-100",
            },
            "reverse_rule": {"applied": True},
        }
        line, reversed_applied = compose_follow_display_line(order, follow_row)
        self.assertEqual(line, "高卡尔利士邦 +0.5")
        self.assertTrue(reversed_applied)

    def test_follow_display_line_uses_submit_total_side_and_line(self) -> None:
        from hdzz.api.webui.server import compose_follow_display_line

        order = {
            "betType": "(滚球) 大 / 小",
            "line": "大 2.5",
        }
        follow_row = {
            "submit_params": {
                "wtype": "ROU",
                "chose_team": "H",
                "con": "-3",
                "ratio": "-100",
            },
            "reverse_rule": {"applied": True},
        }
        line, reversed_applied = compose_follow_display_line(order, follow_row)
        self.assertEqual(line, "小 2.5")
        self.assertTrue(reversed_applied)

    def test_follow_display_line_falls_back_to_backend_line(self) -> None:
        from hdzz.api.webui.server import compose_follow_display_line

        order = {
            "betType": "(滚球) 让球",
            "line": "杰志 -0.5",
        }
        line, reversed_applied = compose_follow_display_line(order, None)
        self.assertEqual(line, "杰志 -0.5")
        self.assertFalse(reversed_applied)


if __name__ == "__main__":
    unittest.main()
