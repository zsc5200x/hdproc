import tempfile
import unittest
from pathlib import Path

from fixture_merge import (
    FixtureMergeStore,
    MappingConflict,
    build_alias_index,
    competition_conflict,
    competition_profile,
    fixture_pair_problem,
    normalize_league,
    normalize_name,
    prepare_crown_event,
    prepare_pinnacle_event,
    rank_candidates,
    rank_league_candidates,
)


def pinnacle_event(
    event_id="p-100",
    home="十月九",
    away="昆卡青年队",
    league="厄瓜多尔 - 乙级联赛",
    league_id="197248",
    kickoff="2026-10-29 03:00:00",
    live=True,
):
    return {
        "id": event_id,
        "name": f"{home} VS {away}",
        "league": league,
        "sportId": "29",
        "leagueId": league_id,
        "kickoff": kickoff,
        "isLive": live,
    }


def crown_event(
    event_id="c-200",
    home="十月九",
    away="库恩卡青年",
    league="厄瓜多尔乙组联赛-附加赛",
    league_id="108994",
    start="10-28 03:00p",
    live=True,
):
    return {
        "id": event_id,
        "home": home,
        "away": away,
        "league": league,
        "leagueId": league_id,
        "startLabel": start,
        "isLive": live,
    }


class CandidateRankingTests(unittest.TestCase):
    def test_normalization_handles_accents_punctuation_and_club_suffixes(self):
        self.assertEqual(normalize_name(" Atlético FC."), "atletico")
        self.assertEqual(normalize_name("十月九足球俱乐部"), "十月九")
        self.assertEqual(normalize_name("山度士SP"), "山度士")
        self.assertEqual(normalize_name("波图加沙足球俱乐部"), "波图加沙")

    def test_league_normalization_handles_tier_and_translation_synonyms(self):
        self.assertEqual(
            normalize_league("意大利 - 甲级联赛"),
            normalize_league("意大利甲组联赛"),
        )
        self.assertEqual(
            normalize_league("美国 - 美国足球大联盟"),
            normalize_league("美国职业大联盟"),
        )
        self.assertEqual(
            normalize_league("India - I-League 3"),
            normalize_league("印度 - I联赛3"),
        )

    def test_alias_index_boosts_confirmed_translation_to_high_confidence(self):
        aliases = build_alias_index(
            league_pairs=[("巴西 - 甲级联赛", "巴西甲组联赛")],
            team_pairs=[("桑托斯足球俱乐部", "山度士SP"), ("帕尔梅拉斯", "彭美拉斯SP")],
        )
        results = rank_candidates(
            [pinnacle_event(home="桑托斯足球俱乐部", away="帕尔梅拉斯", league="巴西 - 甲级联赛")],
            [crown_event(home="山度士SP", away="彭美拉斯SP", league="巴西甲组联赛")],
            aliases=aliases,
        )
        candidate = results["p-100"][0]
        self.assertTrue(candidate["aliasHit"])
        self.assertEqual(candidate["confidence"], "high")
        self.assertGreater(candidate["score"], 90)
        self.assertIn("命中已确认的名称别名", candidate["reasons"])

    def test_alias_index_does_not_match_unrelated_team(self):
        aliases = build_alias_index(team_pairs=[("桑托斯足球俱乐部", "山度士SP")])
        results = rank_candidates(
            [pinnacle_event(home="桑托斯足球俱乐部", away="帕尔梅拉斯", league="英格兰超级联赛")],
            [crown_event(home="弗拉门戈", away="科林蒂安", league="西班牙甲级联赛")],
            aliases=aliases,
        )
        self.assertEqual(results["p-100"], [])

    def test_candidate_uses_team_and_context_not_equal_provider_ids(self):
        results = rank_candidates(
            [pinnacle_event(event_id="p-100")],
            [crown_event(event_id="c-200")],
        )

        self.assertEqual(len(results["p-100"]), 1)
        candidate = results["p-100"][0]
        self.assertEqual(candidate["crownId"], "c-200")
        self.assertGreater(candidate["score"], 65)
        self.assertIn("球队名称较相似", candidate["reasons"])

    def test_equal_raw_ids_do_not_match_unrelated_fixtures(self):
        results = rank_candidates(
            [pinnacle_event(event_id="same-id", home="阿森纳", away="切尔西", league="英超")],
            [crown_event(event_id="same-id", home="巴塞罗那", away="皇家马德里", league="西甲")],
        )

        self.assertEqual(results["same-id"], [])

    def test_reversed_home_and_away_is_reported_for_review(self):
        results = rank_candidates(
            [pinnacle_event(home="阿森纳", away="切尔西", league="英超")],
            [crown_event(home="切尔西", away="阿森纳", league="Premier League")],
        )

        candidate = results["p-100"][0]
        self.assertTrue(candidate["reversedOrder"])
        self.assertIn("球队名称较接近，但主客顺序相反", candidate["reasons"])
        self.assertNotEqual(candidate["confidence"], "high")

    def test_crown_24_hour_start_label_is_normalized_to_the_same_timezone(self):
        results = rank_candidates(
            [pinnacle_event(kickoff="2026-10-28T20:10:00+08:00")],
            [crown_event(start="10-28 08:10")],
        )

        self.assertIn("开赛日期相同", results["p-100"][0]["reasons"])
        self.assertIn("统一时区后开赛时间吻合", results["p-100"][0]["reasons"])
        self.assertEqual(results["p-100"][0]["kickoffDeltaMinutes"], 0.0)

    def test_timezone_normalization_increases_time_evidence_score(self):
        exact = rank_candidates(
            [pinnacle_event(kickoff="2026-10-29 03:00:00")],
            [crown_event(start="10-28 03:00p")],
        )["p-100"][0]
        far = rank_candidates(
            [pinnacle_event(kickoff="2026-10-29 03:00:00")],
            [crown_event(start="10-28 09:00a")],
        )["p-100"][0]

        self.assertEqual(exact["kickoffDeltaMinutes"], 0.0)
        self.assertGreater(exact["score"], far["score"])

    def test_duplicate_candidates_are_both_returned_for_manual_disambiguation(self):
        results = rank_candidates(
            [pinnacle_event(home="阿森纳", away="切尔西", league="英超")],
            [
                crown_event(event_id="c-200", home="阿森纳", away="切尔西", league="英超"),
                crown_event(event_id="c-201", home="阿森纳", away="切尔西", league="英超"),
            ],
        )

        self.assertEqual([item["crownId"] for item in results["p-100"]], ["c-200", "c-201"])
        self.assertEqual(results["p-100"][0]["score"], results["p-100"][1]["score"])

    def test_prepare_functions_keep_source_ids_and_split_names(self):
        p = prepare_pinnacle_event(pinnacle_event())
        c = prepare_crown_event(crown_event())

        self.assertEqual((p["home"], p["away"]), ("十月九", "昆卡青年队"))
        self.assertEqual((p["id"], p["leagueId"]), ("p-100", "197248"))
        self.assertEqual((c["id"], c["leagueId"]), ("c-200", "108994"))
        self.assertEqual(p["normalizedKickoff"], "2026-10-29 03:00:00")
        self.assertEqual(c["normalizedKickoff"], "2026-10-29 03:00:00")

    def test_league_candidates_use_group_fixture_evidence(self):
        pinnacle = [
            pinnacle_event(
                event_id="p-1", home="阿森纳", away="切尔西",
                league="英格兰超级联赛", league_id="p-league-1",
            ),
            pinnacle_event(
                event_id="p-2", home="巴塞罗那", away="皇家马德里",
                league="西班牙甲级联赛", league_id="p-league-2",
            ),
        ]
        crown = [
            crown_event(
                event_id="c-1", home="阿森纳", away="切尔西",
                league="Premier League", league_id="c-league-1",
            ),
            crown_event(
                event_id="c-2", home="Barcelona", away="Real Madrid",
                league="西班牙甲级联赛", league_id="c-league-2",
            ),
        ]

        ranked = rank_league_candidates(pinnacle, crown)

        self.assertEqual(ranked["c-league-1"][0]["pinnacleLeagueId"], "p-league-1")
        self.assertEqual(ranked["c-league-2"][0]["pinnacleLeagueId"], "p-league-2")
        self.assertIn("组内有 1 场赛事候选吻合", ranked["c-league-1"][0]["reasons"])


    def test_league_candidates_penalize_a_large_event_count_mismatch(self):
        pinnacle = [
            pinnacle_event(
                event_id=f"p-alg-{index}", home=f"P主{index}", away=f"P客{index}",
                league="阿尔及利亚 - 乙级联赛", league_id="p-alg-2",
            )
            for index in range(7)
        ]
        crown = [
            crown_event(
                event_id=f"c-alg-{index}", home=f"C主{index}", away=f"C客{index}",
                league="阿尔及利亚甲组联赛", league_id="c-alg-1",
            )
            for index in range(3)
        ]

        ranked = rank_league_candidates(pinnacle, crown)
        candidate = ranked["c-alg-1"][0]

        self.assertLess(candidate["score"], 85)
        self.assertIn("赛事数量差异较大（3 / 7 场）", candidate["reasons"])

    def test_league_aliases_are_directional(self):
        aliases = build_alias_index(league_pairs=[(
            "阿尔及利亚 - 甲级联赛", "阿尔及利亚乙组联赛",
        )])
        # The confirmed 甲级 <-> 乙组 pair must not make 甲组 <-> 乙级 look like
        # an alias; that crossed pair is exactly the wrong 3-vs-7 suggestion.
        pinnacle = [pinnacle_event(
            event_id="p-alg", league="阿尔及利亚 - 乙级联赛", league_id="p-alg-2",
        )]
        crown = [crown_event(
            event_id="c-alg", league="阿尔及利亚甲组联赛", league_id="c-alg-1",
        )]
        ranked = rank_league_candidates(pinnacle, crown, aliases=aliases)
        candidate = ranked["c-alg-1"][0]
        self.assertNotIn("命中已确认的联赛别名", candidate["reasons"])

    def test_league_candidates_use_country_and_event_count(self):
        pinnacle = []
        crown = []
        for index in range(7):
            pinnacle.append(pinnacle_event(
                event_id=f"p-egy-{index}", home=f"埃及主队{index}", away=f"埃及客队{index}",
                league="埃及 - 第二甲级联赛", league_id="p-egy-2",
            ))
            crown.append(crown_event(
                event_id=f"c-egy-{index}", home=f"另一主队{index}", away=f"另一客队{index}",
                league="埃及乙组联赛A", league_id="c-egy-2",
            ))

        ranked = rank_league_candidates(pinnacle, crown)
        candidate = ranked["c-egy-2"][0]

        self.assertEqual(candidate["pinnacleLeagueId"], "p-egy-2")
        self.assertGreaterEqual(candidate["score"], 85)
        self.assertIn("同一国家（埃及）", candidate["reasons"])
        self.assertIn("赛事数量一致（7 场）", candidate["reasons"])

    def test_league_candidates_skip_a_known_country_mismatch(self):
        pinnacle = [
            pinnacle_event(event_id="p-eg", league="埃及 - 第二甲级联赛", league_id="p-eg"),
            pinnacle_event(event_id="p-cr", league="克罗地亚 - 乙级联赛", league_id="p-cr"),
        ]
        crown = [
            crown_event(event_id="c-eg", league="埃及乙组联赛A", league_id="c-eg"),
            crown_event(event_id="c-cr", league="克罗地亚乙组联赛", league_id="c-cr"),
        ]

        ranked = rank_league_candidates(pinnacle, crown)

        self.assertEqual(ranked["c-eg"][0]["pinnacleLeagueId"], "p-eg")
        self.assertNotIn("p-cr", [item["pinnacleLeagueId"] for item in ranked["c-eg"]])
        self.assertEqual(ranked["c-cr"][0]["pinnacleLeagueId"], "p-cr")


class CompetitionClassTests(unittest.TestCase):
    def test_youth_reserve_and_womens_leagues_are_not_the_senior_league(self):
        self.assertTrue(competition_conflict("土耳其超级联赛", "土耳其 - 超级联赛U19"))
        self.assertTrue(competition_conflict("南非超级联赛", "南非 - 女子超级联赛"))
        self.assertTrue(competition_conflict("波兰乙组联赛", "波兰 - 乙级联赛（女子）"))
        self.assertTrue(competition_conflict("阿根廷后备联赛", "阿根廷 - 甲级联赛"))
        self.assertFalse(competition_conflict("巴西甲组联赛", "巴西 - 甲级联赛"))
        self.assertFalse(competition_conflict("英格兰女子甲组联赛", "英格兰 - 全国女子甲级联赛"))
        self.assertFalse(
            competition_conflict("欧洲青年联赛U19", "欧足联 - U19青年联赛")
        )

    def test_profile_reports_gender_age_and_squad(self):
        profile = competition_profile("土耳其 - 超级联赛U19（女子）")
        self.assertEqual(profile["age"], "youth")
        self.assertEqual(profile["gender"], "women")
        self.assertEqual(competition_profile("土耳其 - 超级联赛")["age"], "senior")

    def test_namesakes_in_a_different_competition_are_not_suggested(self):
        results = rank_candidates(
            [pinnacle_event(
                home="科尼亚体育", away="加拉塔萨雷",
                league="土耳其 - 超级联赛U19", kickoff="2026-10-30 20:00:00",
            )],
            [crown_event(
                home="科尼亚体育", away="加拉塔萨雷",
                league="土耳其超级联赛", start="10-30 01:00p",
            )],
        )
        self.assertEqual(results["p-100"], [])

    def test_pair_guard_names_the_reason(self):
        crown = prepare_crown_event(crown_event(
            home="科尼亚体育", away="加拉塔萨雷",
            league="土耳其超级联赛", start="10-30 01:00p",
        ))
        youth = prepare_pinnacle_event(pinnacle_event(
            home="科尼亚体育", away="加拉塔萨雷",
            league="土耳其 - 超级联赛U19", kickoff="2026-10-30 20:00:00",
        ))
        same_league_late = prepare_pinnacle_event(pinnacle_event(
            home="科尼亚体育", away="加拉塔萨雷",
            league="土耳其 - 超级联赛", kickoff="2026-10-31 01:00:00",
        ))
        self.assertEqual(fixture_pair_problem(crown, youth), "competition")
        self.assertIsNone(fixture_pair_problem(crown, same_league_late))
        early = prepare_pinnacle_event(pinnacle_event(
            home="科尼亚体育", away="加拉塔萨雷",
            league="土耳其 - 超级联赛", kickoff="2026-10-30 18:00:00",
        ))
        self.assertEqual(fixture_pair_problem(crown, early), "kickoff")


class FixtureMergeStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = FixtureMergeStore(Path(self.temp.name) / "merge.sqlite3")

    def map_test_league(self, pinnacle_id="197248", crown_id="108994"):
        return self.store.map_leagues(
            {"id": pinnacle_id, "name": "厄瓜多尔 - 乙级联赛", "eventCount": 1},
            {"id": crown_id, "name": "厄瓜多尔乙组联赛-附加赛", "eventCount": 1},
        )

    def test_manual_league_confirmation_is_appended_to_seed_without_duplicates(self):
        seed = Path(self.temp.name) / "manual_seed.tsv"
        seed.write_text(
            "crown_id\tcrown_name\tpinnacle_id\tpinnacle_name\n",
            encoding="utf-8",
        )
        store = FixtureMergeStore(
            Path(self.temp.name) / "manual_merge.sqlite3", seed_path=seed,
        )
        pinnacle = {"id": "p-manual", "name": "Manual P", "eventCount": 1}
        crown = {"id": "c-manual", "name": "Manual C", "eventCount": 1}
        store.map_leagues(pinnacle, crown)
        self.assertTrue(store.append_league_seed_mapping(pinnacle, crown))
        self.assertTrue(store.append_league_seed_mapping(pinnacle, crown))

        lines = [line for line in seed.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertEqual(lines[0], "crown_id\tcrown_name\tpinnacle_id\tpinnacle_name")
        self.assertEqual(sum(1 for line in lines if line.startswith("c-manual\t")), 1)
        self.assertTrue(store.remove_league_seed_mapping("c-manual"))
        lines = [line for line in seed.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertFalse(any(line.startswith("c-manual\t") for line in lines))

    def test_seed_league_mappings_adds_missing_and_respects_existing(self):
        seed = Path(self.temp.name) / "seed.tsv"
        seed.write_text(
            "crown_id\tcrown_name\tpinnacle_id\tpinnacle_name\n"
            "c-seed\t种子皇冠联赛\tp-seed\t种子平博联赛\n",
            encoding="utf-8",
        )
        self.assertEqual(self.store.seed_league_mappings(seed)["added"], 1)
        self.assertEqual(self.store.list_league_mappings()[0]["crownId"], "c-seed")

        self.map_test_league()
        result = self.store.seed_league_mappings(seed, replace_conflicts=False)
        self.assertEqual(result["skipped"], 1)

    def test_seed_league_mappings_can_replace_a_stale_pair(self):
        self.store.map_leagues(
            {"id": "p-old", "name": "旧平博联赛", "eventCount": 1},
            {"id": "c-1", "name": "皇冠联赛", "eventCount": 1},
        )
        seed = Path(self.temp.name) / "seed.tsv"
        seed.write_text(
            "crown_id\tcrown_name\tpinnacle_id\tpinnacle_name\n"
            "c-1\t皇冠联赛\tp-new\t新平博联赛\n",
            encoding="utf-8",
        )
        result = self.store.seed_league_mappings(seed, replace_conflicts=True)

        self.assertEqual(result["replaced"], 1)
        mappings = self.store.list_league_mappings()
        self.assertEqual(len(mappings), 1)
        self.assertEqual(mappings[0]["pinnacleId"], "p-new")
        self.assertEqual(mappings[0]["crownId"], "c-1")

    def test_league_catalog_keeps_first_seen_and_max_event_count(self):
        self.store.record_leagues("crown", [
            {"id": "c-1", "name": "皇冠联赛", "eventCount": 3},
        ])
        first = self.store.list_league_catalog("crown")[0]
        self.store.record_leagues("crown", [
            {"id": "c-1", "name": "皇冠联赛改名", "eventCount": 1},
        ])
        second = self.store.list_league_catalog("crown")[0]

        self.assertEqual(second["leagueId"], "c-1")
        self.assertEqual(second["leagueName"], "皇冠联赛改名")
        self.assertEqual(second["firstSeen"], first["firstSeen"])
        self.assertEqual(second["lastEventCount"], 1)
        self.assertEqual(second["maxEventCount"], 3)
        self.assertGreaterEqual(second["lastSeen"], first["lastSeen"])

    def test_league_catalog_seeds_from_confirmed_mappings(self):
        self.map_test_league()
        restarted = FixtureMergeStore(Path(self.temp.name) / "merge.sqlite3")
        catalog = restarted.list_league_catalog()
        crown = [item for item in catalog if item["provider"] == "crown"]
        pinnacle = [item for item in catalog if item["provider"] == "pinnacle"]

        self.assertEqual(len(crown), 1)
        self.assertEqual(len(pinnacle), 1)
        self.assertEqual(crown[0]["leagueId"], "108994")
        self.assertEqual(crown[0]["leagueName"], "厄瓜多尔乙组联赛-附加赛")
        self.assertEqual(crown[0]["maxEventCount"], 1)

    def test_league_dictionary_view_exposes_the_confirmed_pair(self):
        self.map_test_league()
        dictionary = self.store.list_league_dictionary()

        self.assertEqual(len(dictionary), 1)
        item = dictionary[0]
        self.assertEqual(item["crownLeagueId"], "108994")
        self.assertEqual(item["pinnacleLeagueId"], "197248")
        self.assertEqual(item["crownLeagueName"], "厄瓜多尔乙组联赛-附加赛")
        self.assertEqual(item["pinnacleLeagueName"], "厄瓜多尔 - 乙级联赛")
        self.assertEqual(item["crownEventCount"], 1)

    def test_saves_source_scoped_ids_and_snapshots(self):
        self.map_test_league()
        saved = self.store.map_events(pinnacle_event(), crown_event())

        self.assertEqual(saved["pinnacleId"], "p-100")
        self.assertEqual(saved["crownId"], "c-200")
        mapping = self.store.list_mappings()[0]
        self.assertEqual(mapping["pinnacleSnapshot"]["leagueId"], "197248")
        self.assertEqual(mapping["crownSnapshot"]["leagueId"], "108994")
        self.assertEqual(mapping["pinnacleSnapshot"]["home"], "十月九")

    def test_same_numeric_id_is_allowed_across_provider_namespaces(self):
        self.map_test_league(pinnacle_id="42", crown_id="42")
        saved = self.store.map_events(
            pinnacle_event(event_id="42", home="阿森纳", away="切尔西", league_id="42"),
            crown_event(event_id="42", home="阿森纳", away="切尔西", league_id="42"),
        )

        self.assertEqual((saved["pinnacleId"], saved["crownId"]), ("42", "42"))

    def test_mapping_is_one_to_one_and_conflicts_are_clear(self):
        self.map_test_league()
        self.store.map_events(pinnacle_event(event_id="p-100"), crown_event(event_id="c-200"))

        self.store.map_events(
            pinnacle_event(event_id="p-100"), crown_event(event_id="c-201")
        )
        with self.assertRaisesRegex(MappingConflict, "皇冠赛事已关联"):
            self.store.map_events(pinnacle_event(event_id="p-101"), crown_event(event_id="c-201"))

        self.assertEqual(len(self.store.list_mappings()), 1)
        self.assertEqual(self.store.list_mappings()[0]["crownId"], "c-201")

    def test_unmap_removes_confirmed_pair(self):
        self.map_test_league()
        self.store.map_events(pinnacle_event(), crown_event())

        self.assertTrue(self.store.unmap("p-100"))
        self.assertFalse(self.store.unmap("p-100"))
        self.assertEqual(self.store.list_mappings(), [])

    def test_event_mapping_requires_explicit_matching_league_mapping(self):
        with self.assertRaisesRegex(MappingConflict, "请先确认"):
            self.store.map_events(pinnacle_event(), crown_event())

        self.map_test_league()
        self.store.map_events(pinnacle_event(), crown_event())
        with self.assertRaisesRegex(MappingConflict, "请先确认"):
            self.store.map_events(
                pinnacle_event(league_id="other-p-league"), crown_event()
            )

    def test_league_mapping_is_one_to_one_and_preserves_namespaced_ids(self):
        saved = self.map_test_league()
        self.assertEqual((saved["pinnacleId"], saved["crownId"]), ("197248", "108994"))
        with self.assertRaisesRegex(MappingConflict, "已关联其他皇冠联赛"):
            self.store.map_leagues(
                {"id": "197248", "name": "其他联赛"},
                {"id": "crown-other", "name": "其他皇冠联赛"},
            )
        self.assertEqual(len(self.store.list_league_mappings()), 1)

    def test_league_cannot_be_unmapped_while_child_fixture_is_linked(self):
        self.map_test_league()
        self.store.map_events(pinnacle_event(), crown_event())
        with self.assertRaisesRegex(MappingConflict, "先解除赛事关联"):
            self.store.unmap_league("108994")
        self.assertEqual(len(self.store.list_league_mappings()), 1)
        self.store.unmap("p-100")
        self.assertTrue(self.store.unmap_league("108994"))
        self.assertEqual(self.store.list_league_mappings(), [])


if __name__ == "__main__":
    unittest.main()
