import unittest

from crown_runtime import _normalize_event, _normalize_more_markets


class CrownOddsDisplayTests(unittest.TestCase):
    def test_crown_paired_ior_is_converted_using_both_prices(self):
        event = _normalize_event({
            "GID": "1", "TEAM_H": "主队", "TEAM_C": "客队",
            "RATIO_R": "0.5", "IOR_RH": "0.750", "IOR_RC": "1.000",
        }, "today")
        quote = event["markets"]["handicap"][0]
        self.assertEqual((quote["a_hk"], quote["b_hk"]), ("0.75", "1.00"))
        self.assertEqual(quote["odds_format"], "CROWN_HK")

    def test_crown_total_is_exported_as_over_then_under(self):
        event = _normalize_event({
            "GID": "1", "TEAM_H": "主队", "TEAM_C": "客队",
            "RATIO_OUO": "2.5", "IOR_OUH": "0.820", "IOR_OUC": "1.020",
        }, "today")
        quote = event["markets"]["total"][0]
        self.assertEqual((quote["a_hk"], quote["b_hk"]), ("1.02", "0.82"))

    def test_live_moneyline_uses_live_rm_fields(self):
        event = _normalize_event({
            "GID": "9145487", "TEAM_H": "波尔图", "TEAM_C": "维塞乌",
            "IS_RB": "Y", "RATIO_RE": "0 / 0.5",
            "IOR_REH": "1.02", "IOR_REC": "0.86",
            "IOR_RMH": "1.07", "IOR_RMN": "8.70", "IOR_RMC": "36.00",
        }, "live")
        # Crown's 1X2 feed is decimal; the public list is HK water.
        self.assertEqual(event["markets"]["moneyline"], ["0.07", "7.70", "35.00"])

    def test_handicap_line_direction_uses_strong_field(self):
        home_gives = _normalize_event({
            "GID": "1", "TEAM_H": "主队", "TEAM_C": "客队", "STRONG": "H",
            "RATIO_R": "0.5 / 1", "IOR_RH": "1.12", "IOR_RC": "0.77",
        }, "today")
        self.assertEqual(home_gives["markets"]["handicap"][0]["line"], "-0.5 / 1")
        away_gives = _normalize_event({
            "GID": "1", "TEAM_H": "主队", "TEAM_C": "客队", "STRONG": "C",
            "RATIO_R": "0.5", "IOR_RH": "1.07", "IOR_RC": "0.81",
        }, "today")
        self.assertEqual(away_gives["markets"]["handicap"][0]["line"], "+0.5")
        live = _normalize_event({
            "GID": "1", "TEAM_H": "主队", "TEAM_C": "客队", "STRONG": "C",
            "IS_RB": "Y", "RATIO_RE": "0.5", "IOR_REH": "0.88", "IOR_REC": "1.00",
        }, "live")
        self.assertEqual(live["markets"]["handicap"][0]["line"], "+0.5")

    def test_handicap_direction_falls_back_to_three_way_prices(self):
        event = _normalize_event({
            "GID": "1", "TEAM_H": "主队", "TEAM_C": "客队",
            "RATIO_R": "1", "IOR_RH": "1.60", "IOR_RC": "4.00",
            "IOR_MH": "1.55", "IOR_MN": "3.60", "IOR_MC": "5.20",
        }, "today")
        self.assertEqual(event["markets"]["handicap"][0]["line"], "-1")

    def test_crown_three_way_is_exported_as_hong_kong_water(self):
        event = _normalize_event({
            "GID": "1", "TEAM_H": "主队", "TEAM_C": "客队",
            "RATIO_R": "0.5", "IOR_RH": "0.74", "IOR_RC": "1.08",
            "IOR_MH": "1.74", "IOR_MN": "3.40", "IOR_MC": "4.00",
        }, "today")
        self.assertEqual(event["markets"]["moneyline"], ["0.74", "2.40", "3.00"])

    def test_game_more_keeps_full_time_master_and_alternate_lines(self):
        rows = [
            {
                "GID": "100", "GIDM": "200", "HGID": "101", "MASTER": "Y",
                "MODE": "FT", "PTYPE": "", "RATIO": "0", "RATIO_O": "2.5",
                "RATIO_OUCO": "0.5", "RATIO_OUCU": "0.5",
                "IOR_RH": "0.75", "IOR_RC": "1.05",
                "IOR_OUC": "0.95", "IOR_OUH": "0.85",
                "IOR_OUCO": "1.20", "IOR_OUCU": "0.80",
                "IOR_MH": "1.20", "IOR_MN": "3.40", "IOR_MC": "5.60",
            },
            {
                "GID": "102", "GIDM": "200", "HGID": "103", "MASTER": "N",
                "MODE": "FT", "PTYPE": "", "RATIO": "0.5 / 1", "RATIO_O": "3",
                "RATIO_OUCO": "0.5", "RATIO_OUCU": "0.5",
                "IOR_RH": "1.12", "IOR_RC": "0.77",
                "IOR_OUC": "1.19", "IOR_OUH": "0.69",
                "IOR_OUCO": "1.20", "IOR_OUCU": "0.80",
                "IOR_MH": "0", "IOR_MN": "0", "IOR_MC": "0",
            },
        ]
        result = _normalize_more_markets(rows, "CROWN_HK")
        self.assertEqual([item["gid"] for item in result["handicap"]], ["100", "102"])
        self.assertEqual([item["line"] for item in result["handicap"]], ["0", "0.75"])
        self.assertEqual([item["line"] for item in result["total"]], ["2.5", "3"])
        self.assertTrue(all(item["market_type"] in {"handicap", "total"} for item in result["handicap"] + result["total"]))
        self.assertEqual(result["moneyline"][0]["gid"], "100")

    def test_game_more_master_ids_are_attached_to_base_markets(self):
        more = {
            "status": "loaded",
            "handicap": [{"gid": "100", "gidm": "200", "hgid": "101", "market_id": "200", "master": "Y", "line": "0.75"}],
            "total": [{"gid": "110", "gidm": "210", "hgid": "111", "market_id": "210", "master": "Y", "line": "2.5"}],
            "moneyline": [], "rows": [],
        }
        event = _normalize_event({
            "GID": "100", "TEAM_H": "主队", "TEAM_C": "客队",
            "RATIO_R": "0.5 / 1", "IOR_RH": "1.12", "IOR_RC": "0.77",
            "RATIO_OUO": "2.5", "IOR_OUC": "1.19", "IOR_OUH": "0.69",
        }, "today", more_markets=more)
        self.assertEqual(event["markets"]["handicap"][0]["gid"], "100")
        self.assertEqual(event["markets"]["handicap"][0]["market_id"], "200")
        self.assertEqual(event["markets"]["total"][0]["gid"], "110")

    def test_game_more_live_total_uses_rouo_ratio(self):
        result = _normalize_more_markets([{
            "GID": "500", "GIDM": "600", "HGID": "501", "MASTER": "N",
            "MODE": "FT", "PTYPE": "", "RATIO_RE": "0.5",
            "RATIO_ROUO": "2.5", "RATIO_ROUU": "2.5",
            "IOR_REH": "0.80", "IOR_REC": "1.00",
            "IOR_ROUC": "0.90", "IOR_ROUH": "0.80",
        }], "CROWN_HK")
        self.assertEqual(result["total"][0]["line"], "2.5")

    def test_game_more_live_moneyline_uses_rm_fields(self):
        result = _normalize_more_markets([{
            "GID": "500", "GIDM": "600", "HGID": "501", "MASTER": "Y",
            "MODE": "FT", "PTYPE": "",
            "IOR_RMH": "1.07", "IOR_RMN": "8.70", "IOR_RMC": "36.00",
        }], "CROWN_HK", mode="live")
        self.assertEqual(
            result["moneyline"][0]["home"], "0.07"
        )
        self.assertEqual(
            (result["moneyline"][0]["draw"], result["moneyline"][0]["away"]),
            ("7.70", "35.00"),
        )


    def test_alternate_handicap_lines_follow_the_game_strong_side(self):
        result = _normalize_more_markets([{
            "GID": "300", "GIDM": "301", "HGID": "302", "MASTER": "N",
            "MODE": "FT", "PTYPE": "", "RATIO": "0.5 / 1",
            "IOR_RH": "1.12", "IOR_RC": "0.77",
        }], "CROWN_HK", strong="C")
        item = result["handicap"][0]
        self.assertEqual(item["line"], "+0.75")
        self.assertEqual(item["raw_line"], "+0.75")

    def test_second_half_live_game_is_not_treated_as_finished(self):
        # Crown reports NOW_MODEL=FT for every second-half fixture; that is the
        # full-time market model, not the match phase. Using it as a finish
        # signal hid all in-play second-half matches from the page.
        event = _normalize_event({
            "GID": "9140747", "TEAM_H": "切尔西", "TEAM_C": "曼联",
            "IS_RB": "Y", "RETIMESET": "2H^97:20", "NOW_MODEL": "FT",
            "SCORE_H": "2", "SCORE_C": "0",
            "RATIO_RE": "0 / 0.5", "IOR_REH": "1.48", "IOR_REC": "0.40",
            "RATIO_ROUO": "2.5", "IOR_ROUH": "0.07", "IOR_ROUC": "1.81",
        }, "live")
        self.assertTrue(event["is_live"])
        self.assertFalse(event["is_finished"])
        self.assertEqual(event["period"], "2H")
        self.assertEqual(event["minute"], "2H^97:20")

    def test_first_half_and_half_time_live_games_stay_in_play(self):
        first_half = _normalize_event({
            "GID": "1", "TEAM_H": "主队", "TEAM_C": "客队",
            "IS_RB": "Y", "RETIMESET": "1H^26:48", "NOW_MODEL": "HT",
            "RATIO_RE": "0", "IOR_REH": "0.90", "IOR_REC": "0.98",
        }, "live")
        self.assertFalse(first_half["is_finished"])
        self.assertEqual(first_half["period"], "1H")
        pause = _normalize_event({
            "GID": "2", "TEAM_H": "主队", "TEAM_C": "客队",
            "IS_RB": "Y", "RETIMESET": "MTIME^HT", "NOW_MODEL": "FT",
            "RATIO_RE": "0", "IOR_REH": "0.90", "IOR_REC": "0.98",
        }, "live")
        self.assertFalse(pause["is_finished"])
        self.assertEqual(pause["period"], "MTIME")

    def test_explicit_full_time_minute_is_finished(self):
        event = _normalize_event({
            "GID": "3", "TEAM_H": "主队", "TEAM_C": "客队",
            "IS_RB": "Y", "RETIMESET": "FT", "NOW_MODEL": "FT",
        }, "live")
        self.assertTrue(event["is_finished"])
        self.assertEqual(event["period"], "FT")

    def test_prematch_events_never_carry_a_period(self):
        event = _normalize_event({
            "GID": "4", "TEAM_H": "主队", "TEAM_C": "客队",
            "IS_RB": "N", "RETIMESET": "", "NOW_MODEL": "HT",
        }, "today")
        self.assertFalse(event["is_live"])
        self.assertFalse(event["is_finished"])
        self.assertEqual(event["period"], "")


if __name__ == "__main__":
    unittest.main()
