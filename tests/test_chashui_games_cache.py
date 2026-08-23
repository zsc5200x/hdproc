import unittest
from unittest.mock import patch


class ChashuiGamesCacheTest(unittest.TestCase):
    def setUp(self) -> None:
        from hdzz.api.webui import server

        with server.CHASHUI_GAMES_CACHE_LOCK:
            server.CHASHUI_GAMES_CACHE.clear()

    def test_fresh_cache_hit_returns_snapshot(self) -> None:
        from hdzz.api.webui.server import _get_chashui_games_cache, _set_chashui_games_cache

        _set_chashui_games_cache("m1", "live", [{"gid": "8729941", "score": "2:0"}], now=100.0)
        hit = _get_chashui_games_cache("m1", "live", now=101.0, max_age_s=2.0)

        self.assertIsNotNone(hit)
        self.assertEqual(hit["games"][0]["gid"], "8729941")
        self.assertTrue(hit["cached"])
        self.assertFalse(hit["stale"])

    def test_stale_cache_is_only_returned_when_allowed(self) -> None:
        from hdzz.api.webui.server import _get_chashui_games_cache, _set_chashui_games_cache

        _set_chashui_games_cache("m1", "live", [{"gid": "8729941"}], now=100.0)

        self.assertIsNone(_get_chashui_games_cache("m1", "live", now=110.0, max_age_s=2.0))
        hit = _get_chashui_games_cache("m1", "live", now=110.0, max_age_s=2.0, allow_stale_s=30.0)

        self.assertIsNotNone(hit)
        self.assertTrue(hit["cached"])
        self.assertTrue(hit["stale"])

    def test_early_game_list_uses_all_date_and_future_filter(self) -> None:
        from hdzz.api.webui import server

        forms = []
        with patch.object(server, "_chashui_warmup"), patch.object(
            server,
            "_member_post_session",
            side_effect=lambda **kwargs: forms.append(kwargs["form"]) or "<original>{}</original>",
        ):
            server._chashui_get_game_list_xml(
                session=object(),
                base_url="https://example.test",
                ver="v1",
                uid="u1",
                mode="early",
                timeout_s=1,
            )

        self.assertEqual(len(forms), 1)
        self.assertEqual(forms[0]["showtype"], "early")
        self.assertEqual(forms[0]["date"], "all")
        self.assertEqual(forms[0]["filter"], "FU")

    def test_simplified_games_keep_provider_team_numbers(self) -> None:
        from hdzz.api.webui.server import _chashui_simplify_games

        games = _chashui_simplify_games([
            {
                "GID": "8918001",
                "LEAGUE": "挪威超级联赛",
                "TEAM_H": "奥勒松",
                "TEAM_C": "维京",
                "GNUM_H": "700336",
                "GNUM_C": "700335",
            }
        ])

        self.assertEqual(games[0]["gnumH"], "700336")
        self.assertEqual(games[0]["gnumC"], "700335")

    def test_xml_fallback_keeps_provider_team_numbers(self) -> None:
        from hdzz.api.webui.server import _chashui_parse_game_list_xml_games

        rows = _chashui_parse_game_list_xml_games(
            "<serverresponse><ec id='ec123'><game><GID>8918001</GID>"
            "<TEAM_H>奥勒松</TEAM_H><TEAM_C>维京</TEAM_C>"
            "<GNUM_H>700336</GNUM_H><GNUM_C>700335</GNUM_C>"
            "</game></ec></serverresponse>"
        )

        self.assertEqual(rows[0]["GNUM_H"], "700336")
        self.assertEqual(rows[0]["GNUM_C"], "700335")

    def test_xml_fallback_preserves_half_gid_for_parent_resolution(self) -> None:
        from hdzz.api.webui.server import _chashui_parse_game_list_xml_games

        rows = _chashui_parse_game_list_xml_games(
            "<serverresponse><game><GID>8994661</GID><HGID>8994662</HGID></game></serverresponse>"
        )

        self.assertEqual(rows[0]["GID"], "8994661")
        self.assertEqual(rows[0]["HGID"], "8994662")

    def test_resolve_game_by_half_gid_uses_parent_game(self) -> None:
        from hdzz.api.webui import server

        xml = (
            "<serverresponse><game><GID>8994661</GID><HGID>8994662</HGID>"
            "<ECID>ec-1</ECID><LID>lid-1</LID></game></serverresponse>"
        )
        with patch.object(server, "_chashui_get_game_list_xml", return_value=xml):
            resolved = server._chashui_resolve_game_by_gid(
                session=object(),
                member={"base_url": "https://example.test", "ver": "v", "uid": "u"},
                requested_gid="8994662",
                mode="live",
                timeout_s=1,
            )

        self.assertEqual(resolved["requestedGid"], "8994662")
        self.assertEqual(resolved["gid"], "8994661")
        self.assertTrue(resolved["resolvedFromHalfGid"])

    def test_resolve_game_falls_back_between_live_and_today(self) -> None:
        from hdzz.api.webui import server

        live_xml = "<serverresponse><game><GID>other</GID></game></serverresponse>"
        today_xml = (
            "<serverresponse><game><GID>8994661</GID><ECID>ec-1</ECID>"
            "<LID>lid-1</LID></game></serverresponse>"
        )
        modes = []

        def fetch(**kwargs):
            modes.append(kwargs["mode"])
            return live_xml if kwargs["mode"] == "live" else today_xml

        with patch.object(server, "_chashui_get_game_list_xml", side_effect=fetch):
            resolved = server._chashui_resolve_game_by_gid(
                session=object(),
                member={"base_url": "https://example.test", "ver": "v", "uid": "u"},
                requested_gid="8994661",
                mode="live",
                timeout_s=1,
            )

        self.assertEqual(modes, ["live", "today"])
        self.assertEqual(resolved["mode"], "today")
        self.assertEqual(resolved["requestedMode"], "live")
        self.assertTrue(resolved["modeFallback"])

    def test_resolve_corner_child_gid_via_exact_parent_fixture(self) -> None:
        from hdzz.api.webui import server

        # Real corner orders carry the CN child gid, while get_game_list can
        # expose only the ordinary parent fixture. game_more later proves that
        # the requested child gid belongs to this parent.
        xml = (
            "<serverresponse><game><GID>8980053</GID><HGID>8980054</HGID>"
            "<ECID>11379304</ECID><LID>100146</LID>"
            "<LEAGUE>瑞典超级联赛</LEAGUE>"
            "<TEAM_H>布洛马波卡纳</TEAM_H><TEAM_C>戴戈福斯</TEAM_C>"
            "</game></serverresponse>"
        )
        with patch.object(server, "_chashui_get_game_list_xml", return_value=xml):
            resolved = server._chashui_resolve_game_by_gid(
                session=object(),
                member={"base_url": "https://example.test", "ver": "v", "uid": "u"},
                requested_gid="8980060",
                mode="live",
                timeout_s=1,
                fixture_locator={
                    "league": "瑞典超级联赛",
                    "team_h": "布洛马波卡纳",
                    "team_c": "戴戈福斯",
                    "gnum_h": "701572",
                    "gnum_c": "701571",
                },
            )

        self.assertEqual(resolved["requestedGid"], "8980060")
        self.assertEqual(resolved["gid"], "8980053")
        self.assertTrue(resolved["resolvedByFixture"])
        self.assertFalse(resolved["resolvedFromHalfGid"])

    def test_fixture_resolved_snapshot_requires_requested_child_gid(self) -> None:
        from hdzz.api.webui import server

        game_more = (
            '<serverresponse><code>617</code><game id="gid8980053" master="Y" mode="FT">'
            "<gid>8980053</gid><hgid>8980054</hgid><team_h>布洛马波卡纳</team_h>"
            "<team_c>戴戈福斯</team_c></game></serverresponse>"
        )
        with patch.object(server, "_chashui_get_game_more_xml", return_value=game_more):
            with self.assertRaisesRegex(RuntimeError, "requested_market_board_not_found"):
                server._chashui_get_odds_snapshot_by_resolved(
                    session=object(),
                    member={"base_url": "https://example.test", "ver": "v", "uid": "u"},
                    resolved={
                        "requestedGid": "8980060",
                        "gid": "8980053",
                        "mode": "live",
                        "resolvedByFixture": True,
                    },
                    include_raw=False,
                    confirm_odds=False,
                    timeout_s=1,
                )


if __name__ == "__main__":
    unittest.main()
