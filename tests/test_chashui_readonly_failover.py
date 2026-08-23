import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
APP = ROOT / "app"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))


def _read_member(doc_path: Path, member_id: str):
    doc = json.loads(doc_path.read_text(encoding="utf-8"))
    for member in doc.get("members") or []:
        if isinstance(member, dict) and str(member.get("id") or "") == member_id:
            return member
    return None


class TestChashuiReadonlyFailover(unittest.TestCase):
    def test_switches_to_backup_member_after_session_failure(self) -> None:
        from hdzz.api.webui.server import _run_readonly_member_with_failover

        with tempfile.TemporaryDirectory() as tmp:
            arsenal_path = Path(tmp) / "arsenal.json"
            arsenal_path.write_text(
                json.dumps(
                    {
                        "members": [
                            {
                                "id": "read-1",
                                "usage": "read",
                                "status": "online",
                                "uid": "u1",
                                "cookie": "c1",
                                "ver": "v1",
                                "base_url": "https://read-1",
                            },
                            {
                                "id": "read-2",
                                "usage": "read",
                                "status": "online",
                                "uid": "u2",
                                "cookie": "c2",
                                "ver": "v2",
                                "base_url": "https://read-2",
                            },
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            members = json.loads(arsenal_path.read_text(encoding="utf-8"))["members"]
            attempts = []

            def runner(member):
                attempts.append(str(member.get("id") or ""))
                if str(member.get("id") or "") == "read-1":
                    raise RuntimeError("1X014: login_failed")
                return {"ok": True, "winner": str(member.get("id") or "")}

            out = _run_readonly_member_with_failover(
                arsenal_path=arsenal_path,
                members=members,
                preferred_member_id="read-1",
                lane="odds",
                runner=runner,
            )

            self.assertEqual(attempts, ["read-1", "read-2"])
            self.assertEqual(str((out.get("member") or {}).get("id") or ""), "read-2")
            self.assertTrue(bool(out.get("fallback_used")))
            self.assertEqual((out.get("result") or {}).get("winner"), "read-2")

            failed = _read_member(arsenal_path, "read-1") or {}
            self.assertEqual(str(failed.get("status") or ""), "offline")
            self.assertIn("1X014", str(failed.get("last_error") or ""))

    def test_capture_token_failover_uses_next_readonly_member(self) -> None:
        from hdzz.api.webui.server import _capture_gismo_token_with_failover

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            arsenal_path = root / "arsenal.json"
            data_dir = root / "data"
            data_dir.mkdir(parents=True, exist_ok=True)
            arsenal_path.write_text(
                json.dumps(
                    {
                        "members": [
                            {
                                "id": "read-1",
                                "usage": "read",
                                "status": "online",
                                "uid": "u1",
                                "cookie": "c1",
                                "ver": "v1",
                                "base_url": "https://read-1",
                            },
                            {
                                "id": "read-2",
                                "usage": "read",
                                "status": "online",
                                "uid": "u2",
                                "cookie": "c2",
                                "ver": "v2",
                                "base_url": "https://read-2",
                            },
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            attempts = []

            def fake_capture(member, _data_dir, *, match_id="", timeout_s=16):
                attempts.append(str(member.get("id") or ""))
                if str(member.get("id") or "") == "read-1":
                    return {"ok": False, "message": "自动采集事件源授权失败，请打开一场带动画/跟球的比赛后重试。", "detail": "doubleLogin"}
                return {"ok": True, "source": "http", "message": "已自动采集比赛事件授权。"}

            out = _capture_gismo_token_with_failover(
                arsenal_path=arsenal_path,
                preferred_member_id="read-1",
                data_dir=data_dir,
                match_id="71059310",
                capture_func=fake_capture,
            )

            self.assertEqual(attempts, ["read-1", "read-2"])
            self.assertEqual(str((out.get("member") or {}).get("id") or ""), "read-2")
            self.assertTrue(bool((out.get("result") or {}).get("ok")))
            self.assertTrue(bool(out.get("fallback_used")))

            failed = _read_member(arsenal_path, "read-1") or {}
            self.assertEqual(str(failed.get("status") or ""), "offline")
            self.assertIn("doubleLogin", str(failed.get("last_error") or ""))


if __name__ == "__main__":
    unittest.main()
