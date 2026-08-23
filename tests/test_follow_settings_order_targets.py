import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
APP_DIR = ROOT / "app"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


from hdzz.api.webui import server  # noqa: E402


class TestFollowSettingsOrderTargets(unittest.TestCase):
    def test_adds_order_file_targets_without_overwriting_saved_ratios(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            orders_dir = Path(td)
            (orders_dir / "8hu557555.jsonl").write_text("", encoding="utf-8")
            (orders_dir / "sg121212.jsonl").write_text("", encoding="utf-8")

            doc = {
                "targets": [
                    {"name": "sg121212", "only_live": True, "ratio_percent": 160.0},
                ]
            }

            out = server.merge_order_targets_into_follow_settings(doc, orders_dir)

        targets = {item["name"]: item for item in out["targets"]}
        self.assertEqual(targets["sg121212"]["enabled"], True)
        self.assertEqual(targets["sg121212"]["only_live"], True)
        self.assertEqual(targets["sg121212"]["ratio_percent"], 160.0)
        self.assertIn("8hu557555", targets)
        self.assertEqual(targets["8hu557555"]["enabled"], True)
        self.assertEqual(targets["8hu557555"]["only_live"], False)
        self.assertIsNone(targets["8hu557555"]["ratio_percent"])


if __name__ == "__main__":
    unittest.main()
