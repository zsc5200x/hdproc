import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
APP = ROOT / "app"
for path in (SRC, APP):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


class TestManualSignalPush(unittest.TestCase):
    def test_main_webui_replaces_manual_signal_shortcut_with_tg_intake(self) -> None:
        page = (ROOT / "app" / "webui" / "index.html").read_text(encoding="utf-8")

        self.assertIn('data-view="tg-signal"', page)
        self.assertIn('TG 信号接入', page)
        self.assertNotIn('href="/manual-signal/"', page)

    def test_manual_signal_page_logs_member_in_directly(self) -> None:
        page = (ROOT / "app" / "webui" / "manual-signal" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "app" / "webui" / "manual-signal" / "app.js").read_text(encoding="utf-8")

        for expected in ("inBaseUrl", "inUsername", "inPassword", "btnLoginMember", "inBackendName", "inOrderId"):
            self.assertIn(expected, page)
        self.assertNotIn("selMember", page)
        self.assertNotIn("selectedMemberId", script)
        self.assertIn("/api/manual-signal/login", script)
        self.assertNotIn("/api/arsenal/add", script)
        self.assertNotIn("/api/arsenal/login", script)
        self.assertIn("resetLoginStateForInputChange", script)
        self.assertIn('"inUsername"', script)
        self.assertIn('"inPassword"', script)
        self.assertIn("检查心跳/会话", page)
        self.assertIn("statusLabel", script)
        self.assertIn("在线，可读取比赛", script)
        self.assertIn("会话失效，请点击登录会员重新登录", script)
        self.assertIn("实时比赛查水已移除，请使用正式跟单 Snapshot 数据。", script)
        self.assertIn("实时查水已移除，请使用正式跟单 Snapshot 数据。", script)
        self.assertNotIn("/api/chashui/games", script)
        self.assertNotIn("/api/chashui/odds", script)
        self.assertIn('fetchJson("/api/arsenal")', script)
        self.assertIn("上半场让球", page)
        self.assertIn("上半场大小", page)
        self.assertIn("halfHandicapBox", page)
        self.assertIn("halfOverUnderBox", page)
        self.assertIn("manualSignalSession", script)
        self.assertIn("saveSessionState", script)
        self.assertIn("restoreSessionState", script)
        self.assertIn("document.cookie", script)
        self.assertIn("/api/manual-signal/session", script)
        self.assertIn("已恢复上次会员会话，正在检查心跳", script)
        self.assertIn('backend_name: $("inBackendName").value', script)
        self.assertIn('order_id: trimValue("inOrderId")', script)

    def test_prepare_manual_signal_login_disables_old_manual_members(self) -> None:
        from hdzz.api.webui.server import prepare_manual_signal_member_login

        with tempfile.TemporaryDirectory() as td:
            doc_path = Path(td) / "arsenal.json"
            doc_path.write_text(
                json.dumps(
                    {
                        "members": [
                            {
                                "id": "old",
                                "label": "manual-signal-zxcvv226699",
                                "base_url": "https://member.test",
                                "username": "zxcvv226699",
                                "password": "old-pass",
                                "status": "relogin",
                                "manual_login_requested_at": 123,
                                "auto_relogin": True,
                                "last_error": "old login",
                            },
                            {
                                "id": "other",
                                "label": "normal-member",
                                "base_url": "https://member.test",
                                "username": "normal",
                                "password": "normal-pass",
                                "status": "relogin",
                                "manual_login_requested_at": 456,
                                "auto_relogin": True,
                            },
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            ok, err, member, disabled = prepare_manual_signal_member_login(
                doc_path,
                base_url="member.test",
                username="zxcvv777",
                password="new-pass",
                backend_name="manual",
                now_value=1779123456,
            )

            self.assertTrue(ok)
            self.assertEqual(err, "")
            self.assertEqual(member["label"], "manual-signal-zxcvv777")
            self.assertEqual(member["username"], "zxcvv777")
            self.assertEqual(member["status"], "relogin")
            self.assertEqual(member["manual_login_requested_at"], 1779123456)
            self.assertEqual(member["auto_relogin"], False)
            self.assertEqual(disabled, 1)

            doc = json.loads(doc_path.read_text(encoding="utf-8"))
            by_id = {m["id"]: m for m in doc["members"]}
            self.assertEqual(by_id["old"]["status"], "unknown")
            self.assertEqual(by_id["old"]["manual_login_requested_at"], 0)
            self.assertEqual(by_id["old"]["auto_relogin"], False)
            self.assertEqual(by_id["other"]["status"], "relogin")
            self.assertEqual(by_id["other"]["manual_login_requested_at"], 456)

    def test_find_manual_signal_session_prefers_online_latest_member(self) -> None:
        from hdzz.api.webui.server import find_manual_signal_session

        members = [
            {
                "id": "old",
                "label": "manual-signal-zxcvv226699",
                "username": "zxcvv226699",
                "base_url": "https://member.test",
                "status": "offline",
                "updated_at": 1779000000,
                "uid": "",
                "cookie": "",
                "ver": "",
            },
            {
                "id": "new",
                "label": "manual-signal-zxcvv777",
                "username": "zxcvv777",
                "base_url": "https://member.test",
                "status": "online",
                "updated_at": 1779123456,
                "uid": "uid-1234567890",
                "cookie": "cookie",
                "ver": "top.ver",
            },
        ]

        member = find_manual_signal_session(members)

        self.assertIsNotNone(member)
        self.assertEqual(member["id"], "new")
        self.assertEqual(member["username"], "zxcvv777")

    def test_chashui_odds_snapshot_retries_when_first_snapshot_is_empty(self) -> None:
        from hdzz.api.webui import server

        empty = {
            "simple": {
                "handicap": {"lines": []},
                "overUnder": {"lines": []},
                "moneyLine": {"empty": True},
            }
        }
        filled = {
            "simple": {
                "handicap": {"lines": [{"gid": "g1"}]},
                "overUnder": {"lines": [{"gid": "g1"}]},
                "moneyLine": {"empty": False, "home": {"odd": "1.1"}},
            }
        }

        calls = []

        def fake_snapshot(**kwargs):
            calls.append(kwargs)
            return empty if len(calls) == 1 else filled

        with mock.patch("hdzz.api.webui.server._chashui_get_odds_snapshot_by_resolved", side_effect=fake_snapshot):
            with mock.patch("hdzz.api.webui.server.time.sleep") as sleep_mock:
                out = server._chashui_get_odds_snapshot_with_empty_retry(
                    session=object(),
                    member={"id": "m1"},
                    resolved={"gid": "g1"},
                    include_raw=True,
                    confirm_odds=True,
                    timeout_s=10,
                    attempts=2,
                    sleep_s=0.01,
                )

        self.assertIs(out, filled)
        self.assertEqual(len(calls), 2)
        sleep_mock.assert_called_once()

    def test_chashui_simple_markets_include_first_half_handicap_and_total(self) -> None:
        from hdzz.api.webui.server import _chashui_build_simple_markets

        simple = _chashui_build_simple_markets(
            [
                {
                    "attrs": {"mode": "FT", "master": "Y", "ptype": ""},
                    "meta": {"gid": "8773439"},
                    "raw": {
                        "hgid": "8773440",
                        "hstrong": "H",
                        "sw_HRE": "Y",
                        "ratio_hre": "0",
                        "ior_HREH": "0.61",
                        "ior_HREC": "1.28",
                        "sw_HROU": "Y",
                        "ratio_hrouo": "1.5",
                        "ratio_hrouu": "1.5",
                        "ior_HROUH": "0.41",
                        "ior_HROUC": "1.46",
                    },
                }
            ],
            {"teamH": "邦迪比达SP", "teamC": "隆迪那PR"},
        )

        self.assertEqual(simple["halfHandicap"]["title"], "上半场让球")
        self.assertEqual(simple["halfHandicap"]["lines"][0]["wtype"], "HRE")
        self.assertEqual(simple["halfHandicap"]["lines"][0]["gid"], "8773440")
        self.assertEqual(simple["halfHandicap"]["lines"][0]["home"]["line"], "0")
        self.assertEqual(simple["halfHandicap"]["lines"][0]["home"]["odd"], "0.61")
        self.assertEqual(simple["halfOverUnder"]["title"], "上半场大小")
        self.assertEqual(simple["halfOverUnder"]["lines"][0]["wtype"], "HROU")
        self.assertEqual(simple["halfOverUnder"]["lines"][0]["over"]["line"], "1.5")
        self.assertEqual(simple["halfOverUnder"]["lines"][0]["over"]["odd"], "1.46")

    def test_chashui_snapshot_lines_include_ready_to_submit_params(self) -> None:
        from hdzz.api.webui.server import _chashui_build_simple_markets

        simple = _chashui_build_simple_markets(
            [
                {
                    "attrs": {"mode": "FT", "master": "N", "ptype": ""},
                    "meta": {"gid": "8994729"},
                    "raw": {
                        "strong": "H",
                        "sw_RE": "Y",
                        "ratio_re": "0.5",
                        "ior_REH": "0.91",
                        "ior_REC": "0.99",
                        "sw_ROU": "Y",
                        "ratio_rouo": "2/2.5",
                        "ratio_rouu": "2/2.5",
                        "ior_ROUH": "1.03",
                        "ior_ROUC": "0.85",
                    },
                }
            ],
            {"teamH": "主队", "teamC": "客队"},
        )

        total = simple["overUnder"]["lines"][0]
        self.assertEqual(
            total["over"]["params"],
            {
                "gid": "8994729",
                "wtype": "ROU",
                "rtype": "ROUC",
                "chose_team": "C",
                "con": "2",
                "ratio": "-50",
                "ioratio": "0.85",
            },
        )
        self.assertEqual(total["under"]["params"]["rtype"], "ROUH")
        self.assertEqual(total["under"]["params"]["con"], "-2")
        self.assertEqual(total["under"]["params"]["ratio"], "50")

        handicap = simple["handicap"]["lines"][0]
        self.assertEqual(handicap["home"]["params"]["gid"], "8994729")
        self.assertEqual(handicap["home"]["params"]["rtype"], "REH")
        self.assertEqual(handicap["home"]["params"]["ioratio"], "0.91")

    def test_chashui_snapshot_includes_extra_total_market_board(self) -> None:
        from hdzz.api.webui.server import _chashui_build_simple_markets

        simple = _chashui_build_simple_markets(
            [
                {
                    "attrs": {"mode": "FT", "master": "N", "ptype": ""},
                    "meta": {"gid": "8994730"},
                    "raw": {
                        "sw_AROU": "Y",
                        "ratio_arouo": "3.5",
                        "ratio_arouu": "3.5",
                        "ior_AROUH": "1.04",
                        "ior_AROUC": "0.82",
                    },
                }
            ],
            {"teamH": "主队", "teamC": "客队"},
        )

        self.assertEqual(len(simple["overUnder"]["lines"]), 1)
        line = simple["overUnder"]["lines"][0]
        self.assertEqual(line["wtype"], "AROU")
        self.assertEqual(line["over"]["params"]["rtype"], "AROUC")
        self.assertEqual(line["over"]["odd"], "0.82")

    def test_build_manual_signal_payload_uses_stable_order_shape(self) -> None:
        from hdzz.api.webui.manual_signal import build_manual_signal_payload

        payload = build_manual_signal_payload(
            {
                "league": "英超",
                "match": "阿森纳 v 切尔西",
                "sport": "足球",
                "bet_type": "(滚球) 大 / 小",
                "line": "大 2.5",
                "odds": "0.92",
                "amount": "1000",
                "mode": "live",
                "wtype": "ROU",
                "side": "C",
            },
            now_ts=1779123456,
        )

        self.assertEqual(payload["backend_name"], "manual")
        self.assertEqual(payload["order_id"], "1779123456")
        self.assertEqual(payload["play"], "大 2.5 @0.92")
        self.assertEqual(payload["amount"], 1000.0)
        self.assertEqual(payload["source"], "manual_local")
        self.assertEqual(payload["source_order"]["orderId"], "1779123456")
        self.assertEqual(payload["source_order"]["stake"], "1000")
        self.assertEqual(payload["source_order"]["sides"], "阿森纳 v 切尔西")
        self.assertEqual(payload["source_order"]["raw"]["GTYPE"], "FT")
        self.assertEqual(payload["source_order"]["raw"]["MARKET"], "rb")
        self.assertEqual(payload["source_order"]["raw"]["WAGERSTYPE"], "(滚球) 大 / 小")
        self.assertEqual(payload["source_order"]["raw"]["WTYPE"], "ROU")
        self.assertEqual(payload["source_order"]["raw"]["SIDE"], "C")

    def test_manual_signal_payload_uses_push_time_not_match_clock(self) -> None:
        from hdzz.api.webui.manual_signal import build_manual_signal_payload

        payload = build_manual_signal_payload(
            {
                "league": "巴西乙组联赛",
                "match": "乌贝蓝迪亚 MG v 贝庭 MG",
                "sport": "足球",
                "bet_type": "(滚球) 让球",
                "line": "乌贝蓝迪亚 MG -0/0.5",
                "odds": "0.94",
                "amount": "50",
                "mode": "live",
                "match_time": "1H^15:32",
                "time": "1H^15:32",
            },
            now_ts=1779144398,
        )

        self.assertEqual(payload["time"], "05-19-2026 06:46:38")
        self.assertEqual(payload["source_order"]["date"], "05-19-2026")
        self.assertEqual(payload["source_order"]["time"], "06:46:38")
        self.assertEqual(payload["source_order"]["raw"]["MATCH_TIME"], "1H^15:32")

    def test_manual_signal_payload_uses_beijing_time_even_when_host_tz_is_utc(self) -> None:
        from hdzz.api.webui.manual_signal import build_manual_signal_payload

        payload = build_manual_signal_payload(
            {
                "league": "巴西乙组联赛",
                "match": "康菲安卡 SE v 马拉巴 PA",
                "sport": "足球",
                "bet_type": "(滚球) 让球",
                "line": "马拉巴 PA +0.5",
                "odds": "1.02",
                "amount": "50",
                "mode": "live",
            },
            now_ts=1779146244,
        )

        self.assertEqual(payload["time"], "05-19-2026 07:17:24")
        self.assertEqual(payload["source_order"]["date"], "05-19-2026")
        self.assertEqual(payload["source_order"]["time"], "07:17:24")

    def test_send_manual_signal_uses_configured_webhook_header(self) -> None:
        from hdzz.api.webui.manual_signal import send_manual_signal

        calls = []

        class FakeResponse:
            status_code = 200
            text = '{"ok":true,"id":1}'

            def json(self):
                return {"ok": True, "id": 1}

        def fake_post(url, json, headers, timeout):
            calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
            return FakeResponse()

        with mock.patch("hdzz.api.webui.manual_signal.requests.post", side_effect=fake_post):
            result = send_manual_signal(
                {
                    "signal_webhook_url": "http://remote.test/signals",
                    "signal_webhook_token": "secret-token",
                    "signal_webhook_timeout_seconds": 9,
                },
                {
                    "league": "英超",
                    "match": "阿森纳 v 切尔西",
                    "bet_type": "(滚球) 大 / 小",
                    "line": "大 2.5",
                    "odds": "0.92",
                    "amount": "1000",
                },
                now_ts=1779123456,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(calls[0]["url"], "http://remote.test/signals")
        self.assertEqual(calls[0]["headers"]["X-TGGD-Token"], "secret-token")
        self.assertEqual(calls[0]["timeout"], 9)
        self.assertEqual(calls[0]["json"]["source_order"]["orderId"], "1779123456")

    def test_manual_login_starts_core_from_config_app_dir(self) -> None:
        from hdzz.api.webui import server

        calls = []

        def fake_run(cmd, cwd, env, capture_output, text, timeout):
            calls.append({"cmd": cmd, "cwd": cwd, "env": env, "capture_output": capture_output, "text": text, "timeout": timeout})
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        handler = object.__new__(server.Handler)
        handler._config_path = APP / "config.json"

        with mock.patch("hdzz.api.webui.server.core_running_flag", return_value=(False, 0)):
            with mock.patch("hdzz.api.webui.server.subprocess.run", side_effect=fake_run):
                ok, started, err = server.Handler._ensure_core_running(handler)

        self.assertTrue(ok)
        self.assertTrue(started)
        self.assertEqual(err, "")
        self.assertEqual(calls[0]["cwd"], str(APP))


if __name__ == "__main__":
    unittest.main()
