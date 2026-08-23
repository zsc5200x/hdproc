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


class TestAgWatcherOrderParse(unittest.TestCase):
    def test_extract_odds_from_text_supports_negative_malay_odds(self) -> None:
        from hdzz.core.watcher.ag_watcher import extract_odds_from_text

        text = "米耶尔库雷亚丘克(女) -1.5 / 2 @ -0.96"
        self.assertEqual(extract_odds_from_text(text), "-0.96")


if __name__ == "__main__":
    unittest.main()
