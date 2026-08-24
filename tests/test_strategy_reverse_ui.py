import re
import unittest
from pathlib import Path


class TestStrategyReverseUi(unittest.TestCase):
    def test_strategy_page_contains_reverse_controls(self) -> None:
        page = (Path(__file__).resolve().parents[1] / "app" / "webui" / "index.html").read_text(encoding="utf-8")
        self.assertIn("反单规则", page)
        self.assertIn("全局反打", page)
        self.assertIn("反打大小球", page)
        self.assertIn("反打让球", page)
        self.assertIn("玩法比例 / 禁跟控制", page)

    def test_strategy_default_state_contains_reverse_rules(self) -> None:
        page = (Path(__file__).resolve().parents[1] / "app" / "webui" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="rev-all"', page)
        self.assertIn('id="rev-total"', page)
        self.assertIn('id="rev-handicap"', page)
        self.assertIn("玩法比例 / 禁跟控制", page)
        self.assertIn("单账号比例", page)

    def test_strategy_page_contains_source_ratio_controls(self) -> None:
        page = (Path(__file__).resolve().parents[1] / "app" / "webui" / "index.html").read_text(encoding="utf-8")
        self.assertIn("单账号比例", page)
        self.assertIn("留空使用全局比例", page)
        self.assertIn('id="target-ratios-container"', page)
        self.assertIn('id="target-filters-container"', page)

    def test_strategy_page_contains_follow_odds_limit_controls(self) -> None:
        page = (Path(__file__).resolve().parents[1] / "app" / "webui" / "index.html").read_text(encoding="utf-8")
        self.assertIn("最低跟单水位", page)
        self.assertIn("最高跟单水位", page)
        self.assertIn("低于该水位跳过", page)
        self.assertIn("高于该水位跳过", page)
        self.assertIn('id="follow-min-odds"', page)
        self.assertIn('id="follow-max-odds"', page)


if __name__ == "__main__":
    unittest.main()
