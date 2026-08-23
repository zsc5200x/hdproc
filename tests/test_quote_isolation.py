import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
APP = ROOT / "app"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))


class TestQuoteIsolation(unittest.TestCase):
    def test_quote_member_is_never_bet_eligible_even_if_flag_true(self) -> None:
        from hdzz.core.follower import engine

        quote_member = {
            "id": "m-quote",
            "role": "quote",
            "status": "online",
            "maxcredit": "99999",
            "base_url": "https://quote.example",
            "username": "quote-user",
            "password": "quote-pass",
            "ver": "v", "uid": "quote-uid", "cookie": "q=1",
        }
        normal = {
            "id": "m-bet",
            "role": "",
            "status": "online",
            "maxcredit": "20000",
            "base_url": "https://bet.example",
            "username": "bet-user",
            "password": "bet-pass",
            "ver": "v", "uid": "bet-uid", "cookie": "b=1",
        }

        got = engine.pick_random_member_for_stake(
            [quote_member, normal],
            1000.0,
            target="",
            mappings={},
            allow_quote_members=True,
        )

        self.assertEqual(str((got or {}).get("id") or ""), "m-bet")

    def test_quote_member_not_counted_as_capacity_for_bet(self) -> None:
        from hdzz.core.follower import engine

        cap = engine.inspect_member_capacity_for_stake(
            [
                {"id": "m-quote", "role": "quote", "status": "online", "maxcredit": "50000", "base_url": "https://q", "ver": "v", "uid": "q", "cookie": "q=1"},
                {"id": "m-bet", "role": "", "status": "online", "maxcredit": "0", "base_url": "https://b", "ver": "v", "uid": "b", "cookie": "b=1"},
            ],
            1000.0,
            target="",
            mappings={},
            allow_quote_members=True,
        )

        self.assertEqual(int(cap.get("pickable") or 0), 0)


if __name__ == "__main__":
    unittest.main()
