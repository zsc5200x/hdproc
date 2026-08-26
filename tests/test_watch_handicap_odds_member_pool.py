from __future__ import annotations

import json
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
SRC = ROOT / "src"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tools import watch_handicap_odds as watch  # noqa: E402


def _member(label: str, *, status: str = "online", complete: bool = True) -> dict:
    row = {
        "id": f"id-{label}",
        "label": label,
        "username": label,
        "status": status,
    }
    if complete:
        row.update(
            {
                "base_url": "https://example.test",
                "ver": "ver",
                "uid": f"uid-{label}",
                "cookie": f"sid={label}",
            }
        )
    return row


class WatchHandicapOddsMemberPoolTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write_pool(self, name: str, members: list[dict]) -> Path:
        path = self.root / name / "arsenal.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"members": members}), encoding="utf-8")
        return path

    def test_default_path_uses_first_existing_runtime_candidate(self) -> None:
        runtime = self._write_pool("runtime", [_member("current")])
        stale = self._write_pool("source", [_member("deleted-old")])

        selected = watch._resolve_arsenal_path("", default_candidates=[runtime, stale])

        self.assertEqual(selected, runtime.resolve())

    def test_explicit_missing_path_fails_instead_of_using_default(self) -> None:
        fallback = self._write_pool("runtime", [_member("current")])

        with self.assertRaisesRegex(SystemExit, "指定的号池文件不存在"):
            watch._resolve_arsenal_path(
                str(self.root / "missing.json"),
                default_candidates=[fallback],
            )

    def test_missing_named_member_never_falls_back_to_first_online_member(self) -> None:
        path = self._write_pool("runtime", [_member("current-a"), _member("current-b")])

        with self.assertRaisesRegex(SystemExit, "指定会员不存在：deleted-old"):
            watch._load_first_online_member(path, member_query="deleted-old")

    def test_named_offline_member_is_rejected(self) -> None:
        path = self._write_pool(
            "runtime",
            [_member("offline-old", status="offline"), _member("current")],
        )

        with self.assertRaisesRegex(SystemExit, "指定会员当前不是 online"):
            watch._load_first_online_member(path, member_query="offline-old")

    def test_unnamed_selection_skips_incomplete_and_offline_members(self) -> None:
        path = self._write_pool(
            "runtime",
            [
                _member("incomplete", complete=False),
                _member("offline-old", status="offline"),
                _member("current"),
            ],
        )

        selected = watch._load_first_online_member(path)

        self.assertEqual(selected["label"], "current")

    def test_fuzzy_member_query_must_be_unique(self) -> None:
        path = self._write_pool("runtime", [_member("claude-a"), _member("claude-b")])

        with self.assertRaisesRegex(SystemExit, "指定会员匹配不唯一"):
            watch._load_first_online_member(path, member_query="claude")

    def test_ft_group_includes_master_and_all_additional_boards(self) -> None:
        root = ET.fromstring(
            '<serverresponse><game id="gid100" master="Y" mode="FT">'
            '<gid>100</gid><strong>H</strong><ratio_re>0</ratio_re><ior_REH>1.01</ior_REH><ior_REC>0.89</ior_REC>'
            '<ratio_rouo>2.5</ratio_rouo><ratio_rouu>2.5</ratio_rouu><ior_ROUC>0.91</ior_ROUC><ior_ROUH>0.99</ior_ROUH>'
            '</game><game id="gid101" master="N" mode="FT">'
            '<gid>101</gid><strong>H</strong><ratio_re>0 / 0.5</ratio_re><ior_REH>1.20</ior_REH><ior_REC>0.70</ior_REC>'
            '<ratio_rouo>3</ratio_rouo><ratio_rouu>3</ratio_rouu><ior_ROUC>1.10</ior_ROUC><ior_ROUH>0.80</ior_ROUH>'
            '</game><game id="gid102" master="N" mode="FT">'
            '<gid>102</gid><strong>C</strong><ratio_re>0 / 0.5</ratio_re><ior_REH>0.66</ior_REH><ior_REC>1.24</ior_REC>'
            '<ratio_rouo>2</ratio_rouo><ratio_rouu>2</ratio_rouu><ior_ROUC>0.75</ior_ROUC><ior_ROUH>1.15</ior_ROUH>'
            '</game><FT_gid><gid id="100" master="Y"/><gid id="101" master="N"/>'
            '<gid id="102" master="N"/></FT_gid></serverresponse>'
        )

        group = watch._parse_game_more_ft_group(root, requested_gid="100")
        snapshot = watch._parse_handicap_market_group(group)
        total_snapshot = watch._parse_total_market_group(group)

        self.assertEqual(snapshot["ft_gids"], [
            {"gid": "100", "master": "Y"},
            {"gid": "101", "master": "N"},
            {"gid": "102", "master": "N"},
        ])
        self.assertEqual([row["gid"] for row in snapshot["markets"]], ["100", "101", "102"])
        self.assertEqual([row["master"] for row in snapshot["markets"]], ["Y", "N", "N"])
        self.assertEqual([row["ratio_raw"] for row in snapshot["markets"]], ["0", "0 / 0.5", "0 / 0.5"])
        self.assertEqual([row["gid"] for row in total_snapshot["markets"]], ["100", "101", "102"])
        self.assertEqual([row["master"] for row in total_snapshot["markets"]], ["Y", "N", "N"])
        self.assertEqual([row["ratio_raw"] for row in total_snapshot["markets"]], ["2.5", "3", "2"])

    def test_ft_group_rejects_unrelated_requested_gid(self) -> None:
        root = ET.fromstring(
            '<serverresponse><game master="Y" mode="FT"><gid>100</gid></game>'
            '<FT_gid><gid id="100" master="Y"/></FT_gid></serverresponse>'
        )

        self.assertEqual(watch._parse_game_more_ft_group(root, requested_gid="999"), [])


if __name__ == "__main__":
    unittest.main()
