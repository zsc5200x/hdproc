from __future__ import annotations

import io
import json
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


class MemberDeleteTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data_dir = self.root / "data"
        self.orders_dir = self.root / "orders"
        self.state_dir = self.root / "state"
        self.data_dir.mkdir()
        self.orders_dir.mkdir()
        self.state_dir.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _handler(self, payload: dict) -> server.Handler:
        handler = object.__new__(server.Handler)
        handler.path = "/api/arsenal/delete"
        handler._data_dir = self.data_dir
        handler._arsenal_path = self.data_dir / "arsenal.json"
        handler._mappings_path = self.data_dir / "mappings.json"
        handler._orders_dir = self.orders_dir
        handler._state_dir = self.state_dir
        handler._config_path = self.root / "config.json"
        body = json.dumps(payload).encode("utf-8")
        handler.headers = {"content-length": str(len(body))}
        handler.rfile = io.BytesIO(body)
        handler.responses = []
        handler._send_json = lambda status, data: handler.responses.append((status, data))
        handler.send_error = lambda status: handler.responses.append((status, None))
        return handler

    def test_delete_removes_only_member_and_mapping_persistently(self) -> None:
        (self.data_dir / "arsenal.json").write_text(
            json.dumps(
                {
                    "members": [
                        {"id": "member-a", "label": "A"},
                        {"id": "member-b", "label": "B"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        (self.data_dir / "mappings.json").write_text(
            json.dumps({"member-a": "target-a", "member-b": "target-b"}),
            encoding="utf-8",
        )

        handler = self._handler({"id": "member-a"})
        server.Handler.do_POST(handler)

        status, payload = handler.responses[-1]
        self.assertEqual(status, 200)
        self.assertEqual(payload, {"ok": True, "removed": "member-a"})
        self.assertEqual(
            [row["id"] for row in json.loads((self.data_dir / "arsenal.json").read_text())["members"]],
            ["member-b"],
        )
        self.assertEqual(
            json.loads((self.data_dir / "mappings.json").read_text()),
            {"member-b": "target-b"},
        )

        reread = json.loads((self.data_dir / "arsenal.json").read_text())
        self.assertNotIn("member-a", {row["id"] for row in reread["members"]})

    def test_delete_unknown_member_returns_not_found(self) -> None:
        (self.data_dir / "arsenal.json").write_text(json.dumps({"members": []}), encoding="utf-8")

        handler = self._handler({"id": "missing"})
        server.Handler.do_POST(handler)

        status, payload = handler.responses[-1]
        self.assertEqual(status, 404)
        self.assertEqual(payload["message"], "未找到该会员账号")

    def test_ui_binds_delete_button_to_matching_api(self) -> None:
        script = (APP_DIR / "webui" / "app.js").read_text(encoding="utf-8")
        self.assertIn("data-action=\"delete\"", script)
        self.assertIn("addEventListener(\"click\", async (event) => { event.stopPropagation(); await deleteMember(member.id, memberLabel(member)); })", script)
        self.assertIn('fetchJson("/api/arsenal/delete"', script)
        self.assertIn('method: "POST"', script)


if __name__ == "__main__":
    unittest.main()
