import unittest
from pathlib import Path


class TestGameMoreGidResolver(unittest.TestCase):
    def _fixture_game_more_xml(self) -> str:
        repo_root = Path(__file__).resolve().parents[1]
        p = (
            repo_root
            / "cc"
            / "captures"
            / "20260208-030747"
            / "responses"
            / "transform--p-get_game_more--r184.txt"
        )
        return p.read_text(encoding="utf-8", errors="ignore")

    def test_match_handicap_prefers_ft_gid_priority(self) -> None:
        import hdzz.core.follower.game_more_resolver as gr

        xml_text = self._fixture_game_more_xml()
        parsed = gr.parse_game_more_games_and_ft_gids(xml_text)
        got = gr.match_handicap_from_game_more_parsed(
            games=parsed["games"],
            ft_gids=parsed["ft_gids"],
            candidates=["RE"],
            want_handicap_line=0.25,
            side="H",
        )
        self.assertIsNotNone(got)
        self.assertEqual(got["gid"], "8513895")
        self.assertEqual(got["wtype"], "RE")

    def test_match_handicap_away_plus_quarter(self) -> None:
        import hdzz.core.follower.game_more_resolver as gr

        xml_text = self._fixture_game_more_xml()
        parsed = gr.parse_game_more_games_and_ft_gids(xml_text)
        got = gr.match_handicap_from_game_more_parsed(
            games=parsed["games"],
            ft_gids=parsed["ft_gids"],
            candidates=["RE"],
            want_handicap_line=0.25,
            side="C",
        )
        self.assertIsNotNone(got)
        self.assertEqual(got["gid"], "8513897")
        self.assertEqual(got["wtype"], "RE")

    def test_match_total_rou_over_2_75(self) -> None:
        import hdzz.core.follower.game_more_resolver as gr

        xml_text = self._fixture_game_more_xml()
        parsed = gr.parse_game_more_games_and_ft_gids(xml_text)
        got = gr.match_total_from_game_more_parsed(
            games=parsed["games"],
            ft_gids=parsed["ft_gids"],
            candidates=["ROU"],
            want_total_abs=2.75,
            is_over=True,
        )
        self.assertIsNotNone(got)
        self.assertEqual(got["gid"], "8513895")
        self.assertEqual(got["wtype"], "ROU")


if __name__ == "__main__":
    unittest.main()
