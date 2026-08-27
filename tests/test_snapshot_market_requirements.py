import sys
import unittest
from pathlib import Path
from unittest.mock import patch
import json
import tempfile


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
SRC = ROOT / "src"
for path in (APP, SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


class SnapshotMarketRequirementsTest(unittest.TestCase):
    def test_market_demand_keeps_total_line_and_side(self) -> None:
        from odds_snapshot_watch import market_demand

        demand = market_demand(
            {
                "orderId": "25131528919",
                "betType": "(滚球) 大 / 小",
                "line": "小 2",
                "raw": {"_EVENT": "8990839", "MARKET": "rb", "GTYPE": "FT"},
            }
        )
        self.assertIsNotNone(demand)
        self.assertEqual(demand["market_type"], "TOTAL")
        self.assertEqual(demand["period"], "FULL_TIME")
        self.assertEqual(demand["side"], "UNDER")
        self.assertEqual(demand["target_line"], 2.0)
        self.assertEqual(demand["wtype"], "ROU")

    def test_corner_half_market_demand_marks_display_line_alias(self) -> None:
        from odds_snapshot_watch import market_demand

        demand = market_demand({
            "orderId": "corner-half-1",
            "betType": "(滚球) 角球数 - 大 / 小-上半场",
            "line": "小 5",
            "raw": {
                "_EVENT": "8996432",
                "MARKET": "rb",
                "GTYPE": "FT",
                "WAGERSTYPE": "(滚球) 角球数 - 大 / 小-上半场",
                "ORDER_TYPE": "小",
            },
        })
        self.assertIsNotNone(demand)
        self.assertTrue(demand["is_corner"])
        self.assertEqual(demand["period"], "HALF_TIME")
        self.assertEqual(demand["target_line"], 5.0)

    def test_market_demand_maps_live_full_time_handicap_to_re(self) -> None:
        from odds_snapshot_watch import market_demand

        demand = market_demand(
            {
                "orderId": "live-handicap",
                "betType": "(滚球) 让球",
                "line": "奥达斯 -0.5",
                "raw": {"_EVENT": "8990839", "MARKET": "rb", "GTYPE": "FT"},
            }
        )
        self.assertIsNotNone(demand)
        self.assertEqual(demand["market_type"], "HANDICAP")
        self.assertEqual(demand["period"], "FULL_TIME")
        self.assertEqual(demand["wtype"], "RE")

    def test_market_match_accepts_live_full_time_re(self) -> None:
        import hdzz.api.webui.server as server

        snapshot = {
            "simple": {
                "handicap": {
                    "lines": [
                        {
                            "gid": "8990839",
                            "rawLine": "0.5",
                            "wtype": "RE",
                            "home": {"line": "-0.5", "params": {"wtype": "RE"}},
                            "away": {"line": "+0.5", "params": {"wtype": "RE"}},
                        }
                    ]
                }
            }
        }
        demand = {
            "gid": "8990839",
            "mode": "live",
            "market_type": "HANDICAP",
            "period": "FULL_TIME",
            "side": "HOME",
            "target_line": -0.5,
        }
        self.assertTrue(server._snapshot_market_matches(snapshot, demand))

    def test_market_match_uses_selected_side_signed_prematch_handicap(self) -> None:
        import hdzz.api.webui.server as server

        snapshot = {
            "simple": {
                "handicap": {
                    "lines": [
                        {
                            "gid": "9018529",
                            "rawLine": "0.5",
                            "wtype": "R",
                            "strong": "H",
                            "home": {"line": "-0.5", "params": {"wtype": "R"}},
                            "away": {"line": "+0.5", "params": {"wtype": "R"}},
                        }
                    ]
                }
            }
        }
        demand = {
            "gid": "9018529",
            "mode": "today",
            "market_type": "HANDICAP",
            "period": "FULL_TIME",
            "side": "HOME",
            "target_line": -0.5,
        }

        self.assertTrue(server._snapshot_market_matches(snapshot, demand))
        demand["target_line"] = 0.5
        self.assertFalse(server._snapshot_market_matches(snapshot, demand))

        demand["target_line"] = -0.5
        filters = []

        def fake_fetch(**kwargs):
            filters.append(kwargs["market_filter"])
            result = dict(snapshot)
            result["_snapshot_diagnostics"] = server._snapshot_market_diagnostics(
                result, [demand]
            )
            return result

        with patch.object(
            server,
            "_chashui_get_odds_snapshot_by_resolved",
            side_effect=fake_fetch,
        ):
            result = server._chashui_get_odds_snapshot_with_empty_retry(
                session=object(),
                member={},
                resolved={"gid": "9018529", "mode": "today"},
                include_raw=False,
                confirm_odds=False,
                timeout_s=1,
                attempts=3,
                required_markets=[demand],
            )

        self.assertEqual(filters, ["Main"])
        self.assertEqual(result["snapshot_fetch_filter"], "Main")

    def test_market_match_accepts_corner_half_integer_display_line(self) -> None:
        import hdzz.api.webui.server as server

        snapshot = {
            "simple": {
                "halfOverUnder": {
                    "lines": [{
                        "gid": "8996432",
                        "rawLine": "4.5",
                        "under": {"line": "4.5", "params": {"wtype": "HROU"}},
                    }]
                }
            }
        }
        demand = {
            "gid": "8996432",
            "mode": "live",
            "market_type": "TOTAL",
            "period": "HALF_TIME",
            "side": "UNDER",
            "target_line": 5.0,
            "is_corner": True,
        }
        self.assertTrue(server._snapshot_market_matches(snapshot, demand))
        demand["is_corner"] = False
        self.assertFalse(server._snapshot_market_matches(snapshot, demand))

    def test_empty_retry_checks_all_when_main_has_other_market(self) -> None:
        import hdzz.api.webui.server as server

        requirement = {
            "gid": "8990839",
            "mode": "live",
            "market_type": "TOTAL",
            "period": "FULL_TIME",
            "side": "UNDER",
            "target_line": 2.0,
        }
        main = {
            "simple": {"handicap": {"lines": [{"gid": "8990839"}]}, "overUnder": {"lines": []}},
            "_snapshot_diagnostics": {"target_line_found": False},
        }
        all_markets = {
            "simple": {
                "handicap": {"lines": []},
                "overUnder": {
                    "lines": [
                        {
                            "gid": "8990839",
                            "rawLine": "2",
                            "wtype": "ROU",
                            "under": {"params": {"wtype": "ROU"}},
                        }
                    ]
                },
            },
            "_snapshot_diagnostics": {"target_line_found": True},
        }
        filters = []

        def fake_fetch(**kwargs):
            filters.append(kwargs["market_filter"])
            return main if kwargs["market_filter"] == "Main" else all_markets

        with patch.object(server, "_chashui_get_odds_snapshot_by_resolved", side_effect=fake_fetch):
            result = server._chashui_get_odds_snapshot_with_empty_retry(
                session=object(),
                member={},
                resolved={"gid": "8990839", "mode": "live"},
                include_raw=False,
                confirm_odds=False,
                timeout_s=1,
                attempts=1,
                required_markets=[requirement],
            )

        self.assertEqual(filters, ["Main", "All"])
        self.assertIs(result, all_markets)

    def test_real_market_fields_are_normalized_to_rou_line(self) -> None:
        import hdzz.api.webui.server as server

        board = {
            "attrs": {"mode": "FT", "master": "Y", "ptype": ""},
            "meta": {"gid": "8990839"},
            "raw": {
                "gid": "8990839",
                "sw_ROU": "Y",
                "ratio_rouo": "2",
                "ratio_rouu": "2",
                "ior_ROUC": "0.92",
                "ior_ROUH": "0.83",
            },
        }
        simple = server._chashui_build_simple_markets([board], {"teamH": "H", "teamC": "C"})
        self.assertEqual(len(simple["overUnder"]["lines"]), 1)
        row = simple["overUnder"]["lines"][0]
        self.assertEqual(row["wtype"], "ROU")
        self.assertEqual(row["rawLine"], "2")
        self.assertEqual(row["under"]["params"]["rtype"], "ROUH")
        self.assertEqual(row["under"]["params"]["con"], "-2")
        self.assertEqual(row["under"]["params"]["ratio"], "0")
        self.assertEqual(row["under"]["params"]["ioratio"], "0.83")

    def test_corner_board_contributes_full_and_first_half_totals(self) -> None:
        import hdzz.api.webui.server as server

        simple = server._chashui_build_simple_markets(
            [{
                "attrs": {"mode": "CN", "master": "Y", "ptype": "-角球数"},
                "meta": {"gid": "8996431"},
                "raw": {
                    "hgid": "8996432",
                    "sw_ROU": "Y", "ratio_rouo": "10", "ratio_rouu": "10",
                    "ior_ROUH": "0.78", "ior_ROUC": "1.04",
                    "sw_RE": "Y", "ratio_re": "0 / 0.5",
                    "ior_REH": "1.06", "ior_REC": "0.66",
                    "sw_HRE": "Y", "ratio_hre": "0",
                    "ior_HREH": "1.16", "ior_HREC": "0.56",
                    "sw_HROU": "Y", "ratio_hrouo": "4.5", "ratio_hrouu": "4.5",
                    "ior_HROUH": "0.96", "ior_HROUC": "0.86",
                    "sw_RM": "Y", "ior_RMH": "9.0", "ior_RMN": "5.0", "ior_RMC": "1.1",
                },
            }],
            {"teamH": "H", "teamC": "C"},
        )

        self.assertEqual([(row["gid"], row["wtype"]) for row in simple["overUnder"]["lines"]], [("8996431", "ROU")])
        self.assertEqual([(row["gid"], row["wtype"]) for row in simple["halfOverUnder"]["lines"]], [("8996432", "HROU")])
        self.assertEqual([(row["gid"], row["wtype"]) for row in simple["handicap"]["lines"]], [("8996431", "RE")])
        self.assertEqual([(row["gid"], row["wtype"]) for row in simple["halfHandicap"]["lines"]], [("8996432", "HRE")])
        self.assertEqual(simple["halfHandicap"]["lines"][0]["home"]["params"]["rtype"], "HREH")
        self.assertTrue(simple["moneyLine"].get("empty"))

    def test_corner_and_standard_same_line_are_not_deduplicated(self) -> None:
        import hdzz.api.webui.server as server

        def board(gid, hgid, mode):
            return {
                "attrs": {"mode": mode, "master": "Y"},
                "meta": {"gid": gid},
                "raw": {
                    "hgid": hgid,
                    "sw_ROU": "Y", "ratio_rouo": "4.5", "ratio_rouu": "4.5",
                    "ior_ROUH": "0.90", "ior_ROUC": "0.90",
                    "sw_HROU": "Y", "ratio_hrouo": "4.5", "ratio_hrouu": "4.5",
                    "ior_HROUH": "0.90", "ior_HROUC": "0.90",
                },
            }

        simple = server._chashui_build_simple_markets(
            [board("standard-full", "standard-half", "FT"), board("corner-full", "corner-half", "CN")],
            {"teamH": "H", "teamC": "C"},
        )

        self.assertEqual(
            [row["gid"] for row in simple["overUnder"]["lines"]],
            ["standard-full", "corner-full"],
        )
        self.assertEqual(
            [row["gid"] for row in simple["halfOverUnder"]["lines"]],
            ["standard-half", "corner-half"],
        )

    def test_moneyline_full_and_half_rows_cover_live_and_today_protocols(self) -> None:
        import hdzz.api.webui.server as server
        from hdzz.core.follower.market_rows import extract_market_rows

        for mode, full_wtype, half_wtype in (("live", "RM", "HRM"), ("today", "M", "HM")):
            raw = {
                "sw_" + full_wtype: "Y",
                "ior_" + full_wtype + "H": "1.20",
                "ior_" + full_wtype + "N": "3.30",
                "ior_" + full_wtype + "C": "2.10",
                "sw_" + half_wtype: "Y",
                "ior_" + half_wtype + "H": "1.50",
                "ior_" + half_wtype + "N": "2.80",
                "ior_" + half_wtype + "C": "2.50",
            }
            simple = server._chashui_build_simple_markets(
                [{"attrs": {"mode": "FT", "master": "Y"}, "meta": {"gid": "gid-ml"}, "raw": raw}],
                {"teamH": "H", "teamC": "C"},
            )
            self.assertEqual(simple["moneyLine"]["wtype"], full_wtype)
            self.assertEqual(simple["halfMoneyLine"]["wtype"], half_wtype)
            rows = extract_market_rows(
                {"simple": simple, "meta": {"masterGid": "gid-ml"}},
                mode=mode,
                requested_gid="gid-ml",
            )
            self.assertEqual(
                {(row["period"], row["wtype"]) for row in rows if row["market_type"] == "MONEYLINE"},
                {("FULL_TIME", full_wtype), ("HALF_TIME", half_wtype)},
            )

    def test_moneyline_market_match_checks_period_side_wtype_and_gid(self) -> None:
        import hdzz.api.webui.server as server

        snapshot = {
            "simple": {
                "moneyLine": {
                    "gid": "g1", "wtype": "RM", "home": {"params": {"wtype": "RM"}},
                    "away": {"params": {"wtype": "RM"}}, "draw": {"params": {"wtype": "RM"}},
                },
                "halfMoneyLine": {
                    "gid": "h1", "wtype": "HRM", "away": {"params": {"wtype": "HRM"}},
                },
            }
        }
        self.assertTrue(server._snapshot_market_matches(snapshot, {
            "gid": "g1", "mode": "live", "market_type": "MONEYLINE", "period": "FULL_TIME", "side": "AWAY",
        }))
        self.assertTrue(server._snapshot_market_matches(snapshot, {
            "gid": "h1", "mode": "live", "market_type": "MONEYLINE", "period": "HALF_TIME", "side": "AWAY",
        }))
        self.assertFalse(server._snapshot_market_matches(snapshot, {
            "gid": "g1", "mode": "live", "market_type": "MONEYLINE", "period": "HALF_TIME", "side": "AWAY",
        }))

    def test_collect_market_demands_groups_market_by_gid(self) -> None:
        from odds_snapshot_watch import collect_market_demands

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            orders = root / "orders"
            orders.mkdir()
            order = {
                "ts": 1000,
                "orderId": "25131528919",
                "betType": "(滚球) 大 / 小",
                "line": "小 2",
                "raw": {"_EVENT": "8990839", "MARKET": "rb", "GTYPE": "FT"},
            }
            (orders / "source.jsonl").write_text(json.dumps(order, ensure_ascii=False) + "\n", encoding="utf-8")
            state = root / "follow_state.json"
            state.write_text("{}", encoding="utf-8")
            rows = collect_market_demands(orders_dir=orders, state_path=state, now=1001, active_seconds=120)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["gid"], "8990839")
        self.assertEqual(rows[0]["requirements"][0]["target_line"], 2.0)


if __name__ == "__main__":
    unittest.main()
