import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
APP = ROOT / "app"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))


class TestFollowGameListTimeout(unittest.TestCase):
    def test_fetch_game_list_game_returns_timeout_message_instead_of_raising(self) -> None:
        from hdzz.common.http import HardTimeout
        from hdzz.core.follower.engine import fetch_game_list_game

        with patch("hdzz.core.follower.engine._xml_cache_get", return_value=""):
            with patch("hdzz.core.follower.engine.member_post_session", side_effect=HardTimeout("hard_timeout")):
                game, msg = fetch_game_list_game(
                    session=object(),
                    base_url="https://example.com",
                    ver="v1",
                    uid="u1",
                    gtype="ft",
                    showtype="live",
                    rtype="rb",
                    timeout_s=8,
                    gid="12345",
                )

        self.assertIsNone(game)
        self.assertEqual(msg, "game_list_request_failed: hard_timeout")


if __name__ == "__main__":
    unittest.main()
