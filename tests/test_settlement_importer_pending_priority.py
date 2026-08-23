import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hdzz.core.settlement import importer


class TestSettlementImporterPendingPriority(unittest.TestCase):
    def _prepare_data_dir(self, base: Path) -> Path:
        data_dir = base / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        arsenal = {
            "members": [
                {
                    "label": "m1",
                    "status": "online",
                    "base_url": "https://example.com",
                    "ver": "v1",
                    "uid": "uid1",
                    "cookie": "cookie1",
                    "proxy": "",
                }
            ]
        }
        (data_dir / "arsenal.json").write_text(json.dumps(arsenal, ensure_ascii=False), encoding="utf-8")
        return data_dir

    @staticmethod
    def _fake_today_wagers(*, ls: str = "g", chk_cw: str = "N", **_: object) -> dict:
        wager_open = {
            "w_id": "OU1001",
            "ticket_id": "1001",
            "gold": "50",
            "win_gold": "47.5",
            "ball_act_ret": "",
        }
        if ls == "g" and chk_cw == "N":
            return {"ok": True, "format": "json", "data": {"wagers": [wager_open]}}
        if ls == "g" and chk_cw == "Y":
            return {"ok": True, "format": "json", "data": {"wagers": [wager_open]}}
        if ls == "h" and chk_cw == "Y":
            return {"ok": True, "format": "json", "data": {"wagers": []}}
        return {"ok": False, "error": "unexpected_query"}

    @staticmethod
    def _fake_history_data(**_: object) -> dict:
        return {"ok": False, "error": "skip_history_data"}

    @staticmethod
    def _fake_history_switch(**_: object) -> dict:
        return {"ok": False, "error": "skip_history_switch"}

    def _load_today_settlements(self, data_dir: Path) -> dict:
        out_path = data_dir / "settlements" / f"wagers_{importer.today_ymd()}.jsonl"
        self.assertTrue(out_path.exists())
        out = {}
        with out_path.open("r", encoding="utf-8-sig") as f:
            for line in f:
                line = (line or "").strip()
                if not line:
                    continue
                rec = json.loads(line)
                tid = str(rec.get("ticket_id") or "")
                if tid:
                    out[tid] = rec
        return out

    def test_open_ticket_forces_pending_when_closed_variant_returns_profit(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            data_dir = self._prepare_data_dir(Path(td))
            with patch("hdzz.core.settlement.importer.member_get_today_wagers", side_effect=self._fake_today_wagers), patch(
                "hdzz.core.settlement.importer.member_get_history_data", side_effect=self._fake_history_data
            ), patch("hdzz.core.settlement.importer.member_history_switch", side_effect=self._fake_history_switch):
                importer.import_settlements_once(data_dir=data_dir, verbose=False)

            idx = self._load_today_settlements(data_dir)
            rec = idx.get("1001")
            self.assertIsNotNone(rec)
            self.assertEqual(rec.get("status"), "pending")
            self.assertEqual(rec.get("source"), "today_open")
            self.assertFalse(bool(rec.get("is_final")))

    def test_open_ticket_can_override_stale_settled_record(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            data_dir = self._prepare_data_dir(Path(td))
            out_path = data_dir / "settlements" / f"wagers_{importer.today_ymd()}.jsonl"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            stale = {
                "ts": 1,
                "member": "m1",
                "ticket_id": "1001",
                "w_id": "OU1001",
                "gold": "50",
                "win_gold": "47.5",
                "profit": 47.5,
                "status": "settled",
                "ball_act_ret": "",
                "raw_status": "",
                "source": "history_switch",
                "is_final": True,
            }
            out_path.write_text(json.dumps(stale, ensure_ascii=False) + "\n", encoding="utf-8")

            with patch("hdzz.core.settlement.importer.member_get_today_wagers", side_effect=self._fake_today_wagers), patch(
                "hdzz.core.settlement.importer.member_get_history_data", side_effect=self._fake_history_data
            ), patch("hdzz.core.settlement.importer.member_history_switch", side_effect=self._fake_history_switch):
                importer.import_settlements_once(data_dir=data_dir, verbose=False)

            idx = self._load_today_settlements(data_dir)
            rec = idx.get("1001")
            self.assertIsNotNone(rec)
            self.assertEqual(rec.get("status"), "pending")
            self.assertEqual(rec.get("source"), "today_open")
            self.assertFalse(bool(rec.get("is_final")))


if __name__ == "__main__":
    unittest.main()
