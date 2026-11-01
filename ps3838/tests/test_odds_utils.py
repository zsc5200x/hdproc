import unittest

from odds_utils import normalize_odds


class NormalizeOddsTests(unittest.TestCase):
    def test_decimal_market(self):
        result = normalize_odds("1", "1.170")
        self.assertEqual(result["odds_format"], "decimal")
        self.assertEqual(result["decimal_odds"], "1.1700")
        self.assertEqual(result["hong_kong_odds"], "0.1700")
        self.assertEqual(result["报价状态"], "有报价")

    def test_hong_kong_market(self):
        result = normalize_odds("2", "0.850")
        self.assertEqual(result["odds_format"], "hong_kong")
        self.assertEqual(result["decimal_odds"], "1.8500")
        self.assertEqual(result["hong_kong_odds"], "0.8500")

    def test_invalid_and_unknown_values(self):
        for market, value in (("1", ""), ("1", "0"), ("9", "1.2")):
            result = normalize_odds(market, value)
            self.assertEqual(result["报价状态"], "空值或暂停")
            self.assertEqual(result["decimal_odds"], "")


if __name__ == "__main__":
    unittest.main()
