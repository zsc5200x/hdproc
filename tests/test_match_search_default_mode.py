import re
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class MatchSearchDefaultModeTest(unittest.TestCase):
    def test_realtime_order_match_search_defaults_to_all(self) -> None:
        app_js = (ROOT / "app" / "webui" / "app.js").read_text(encoding="utf-8")
        html = (ROOT / "app" / "webui" / "index.html").read_text(encoding="utf-8")

        self.assertRegex(app_js, r'matchSearch:\s*\{[^}]*mode:\s*"all"', "match search state should default to all")
        select_match = re.search(r'<select[^>]+id="hg-match-mode"[\s\S]*?</select>', html)
        self.assertIsNotNone(select_match, "hg-match-mode select should exist")
        self.assertRegex(select_match.group(0), r'<option\s+value="all"\s+selected>全部</option>', "all option should be selected by default")


if __name__ == "__main__":
    unittest.main()
