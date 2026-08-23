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


class TestMemberUsageMapping(unittest.TestCase):
    def test_usage_defaults_to_bet(self) -> None:
        from hdzz.api.webui.server import member_usage_from_row

        self.assertEqual(member_usage_from_row({}), "bet")

    def test_quote_role_maps_to_read_usage(self) -> None:
        from hdzz.api.webui.server import member_usage_from_row

        self.assertEqual(member_usage_from_row({"role": "quote"}), "read")

    def test_apply_usage_to_patch_sets_quote_role_for_read(self) -> None:
        from hdzz.api.webui.server import apply_member_usage_patch

        patch = {}
        apply_member_usage_patch(patch, "read")

        self.assertEqual(patch["usage"], "read")
        self.assertEqual(patch["role"], "quote")

    def test_apply_usage_to_patch_clears_quote_role_for_bet(self) -> None:
        from hdzz.api.webui.server import apply_member_usage_patch

        patch = {}
        apply_member_usage_patch(patch, "bet")

        self.assertEqual(patch["usage"], "bet")
        self.assertEqual(patch["role"], "")


if __name__ == "__main__":
    unittest.main()
