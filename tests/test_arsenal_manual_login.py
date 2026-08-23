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


class TestArsenalManualLogin(unittest.TestCase):
    def test_normalize_member_base_url_adds_https_for_bare_domain(self) -> None:
        from hdzz.core.member.keeper import normalize_member_base_url

        self.assertEqual(normalize_member_base_url("mos055.com"), "https://mos055.com")
        self.assertEqual(normalize_member_base_url(" https://mos055.com/ "), "https://mos055.com")

    def test_queue_arsenal_manual_login_marks_member_for_visible_relogin(self) -> None:
        from hdzz.api.webui.server import queue_arsenal_manual_login

        with tempfile.TemporaryDirectory() as td:
            doc_path = Path(td) / "arsenal.json"
            doc_path.write_text(
                json.dumps(
                    {
                        "members": [
                            {
                                "id": "m1",
                                "label": "summer022",
                                "base_url": "https://205.201.0.120",
                                "username": "summer022",
                                "password": "Qwe123123",
                                "session_provider": "native",
                                "status": "online",
                                "last_relogin_at": 123,
                                "last_error": "old error",
                                "relogin_headless": True,
                                "uid": "u1",
                                "cookie": "c1",
                                "ver": "v1",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            ok, err = queue_arsenal_manual_login(doc_path, "m1")
            self.assertTrue(ok)
            self.assertEqual(err, "")

            doc = json.loads(doc_path.read_text(encoding="utf-8"))
            member = doc["members"][0]
            self.assertEqual(member["status"], "relogin")
            self.assertEqual(member["last_relogin_at"], 0)
            self.assertEqual(member["last_error"], "")
            self.assertEqual(member["relogin_headless"], False)
            self.assertGreater(float(member["manual_login_requested_at"]), 0.0)

    def test_queue_arsenal_manual_login_rejects_missing_member(self) -> None:
        from hdzz.api.webui.server import queue_arsenal_manual_login

        with tempfile.TemporaryDirectory() as td:
            doc_path = Path(td) / "arsenal.json"
            doc_path.write_text(json.dumps({"members": []}), encoding="utf-8")
            ok, err = queue_arsenal_manual_login(doc_path, "missing")
            self.assertFalse(ok)
            self.assertIn("member_not_found", err)


if __name__ == "__main__":
    unittest.main()
