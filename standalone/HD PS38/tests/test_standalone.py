import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from ps38.client import (Ps3838Client, Ps3838SessionError, Ps3838LoginInterrupted,
                        Ps3838LoginWindowClosed, DEFAULT_BASE_URL, AUTH_PATH, BET_MONITOR_PATH)
from ps38.cli import load_config, run_watch
from ps38.orders import extract_orders, normalize_order, build_poll_body, has_order_list
from ps38.storage import OrderStore, InstanceLock, is_running


def order(oid="test-1", status="accepted"):
    return {"playerWagerId": oid, "status": status, "stake": "150.00", "toRisk": 150,
            "currency": "THB", "choices": [{"eventName": "主队-vs-客队", "btName": "HDP", "selection": "HOME", "handicap": 0, "odds": 2}]}


class TempCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def store(self):
        store = OrderStore(self.root / "demo", "demo")
        self.addCleanup(store.close)
        return store


class StoreTests(TempCase):
    def test_first_poll_is_saved_snapshot_and_later_new_and_update(self):
        s = self.store()
        events, count = s.ingest({"data": [order()]})
        self.assertEqual((events[0]["event"], count), ("snapshot", 1))
        self.assertEqual(s.ingest({"data": [order()]})[0], [])
        events, _ = s.ingest({"data": [order(status="settled"), order("test-2")]})
        self.assertEqual([e["event"] for e in events], ["update", "new"])
        self.assertEqual(events[0]["changes"]["status"], ("accepted", "settled"))
        self.assertEqual(len((self.root / "demo/events.jsonl").read_text().splitlines()), 3)

    def test_empty_window_and_restart_do_not_forget_old_order(self):
        s = self.store()
        s.ingest({"data": [order()]})
        s.ingest({"data": []})
        reopened = self.store()
        self.assertEqual(reopened.ingest({"data": [order()]})[0], [])
        self.assertEqual(reopened.ingest({"data": [order("test-2")]})[0][0]["event"], "new")

    def test_export_recovers_after_committed_database_then_write_failure(self):
        s = self.store()
        with patch.object(s, "export", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                s.ingest({"data": [order()]})
        reopened = self.store()
        event = json.loads((self.root / "demo/events.jsonl").read_text())
        self.assertEqual(event["orderId"], "test-1")
        self.assertEqual(reopened.ingest({"data": [order()]})[0], [])

    def test_bad_response_does_not_clear_snapshot(self):
        s = self.store()
        s.ingest({"data": [order()]})
        old = (self.root / "demo/snapshot.json").read_text()
        with self.assertRaises(ValueError):
            s.ingest({"success": False, "data": []})
        self.assertEqual((self.root / "demo/snapshot.json").read_text(), old)

    def test_multi_instance_lock_and_release(self):
        with InstanceLock(self.root / "worker.lock"):
            self.assertTrue(is_running(self.root))
            with self.assertRaises(RuntimeError):
                with InstanceLock(self.root / "worker.lock"):
                    pass
        self.assertFalse(is_running(self.root))


class ParserTests(unittest.TestCase):
    def test_parse_keeps_real_id_and_raw(self):
        raw = order()
        parsed = extract_orders({"body": {"data": [raw]}}, "demo")[0]
        self.assertEqual(parsed["orderId"], "test-1")
        self.assertEqual(parsed["stake"], 150)
        self.assertEqual(parsed["raw"], raw)

    def test_non_orders_do_not_gain_invented_ids(self):
        self.assertEqual(extract_orders({"data": [order(), {"total": 1}]}, "demo")[0]["orderId"], "test-1")
        with self.assertRaises(ValueError):
            normalize_order({"stake": 1}, "demo")

    def test_poll_body_and_validity(self):
        self.assertEqual(build_poll_body()["historyTime"], 60)
        self.assertEqual(build_poll_body()["groupType"], "OWN_DOWNLINE")
        self.assertTrue(has_order_list({"data": []}))
        self.assertFalse(has_order_list({"message": "login"}))
        self.assertFalse(has_order_list({"success": False, "data": []}))


class LoginTests(TempCase):
    def client(self):
        page = Mock()
        page.url = DEFAULT_BASE_URL + AUTH_PATH
        page.elapsed = 0
        page.otp_visible = True
        page.is_closed.return_value = False
        page.evaluate.return_value = {"status": 200, "isJson": True, "data": {"data": [order()]}}
        body = Mock()
        body.inner_text.return_value = ""
        inputs = Mock()
        inputs.count.side_effect = lambda: int(page.otp_visible)
        inputs.nth.return_value.is_visible.side_effect = lambda: page.otp_visible
        page.locator.side_effect = lambda selector: body if selector == "body" else inputs
        def advance(ms):
            page.elapsed += ms / 1000
        page.wait_for_timeout.side_effect = advance
        ctx = Mock()
        client = Ps3838Client({"name": "demo"}, self.root / "profile")
        client.page, client.ctx = page, ctx
        return client, page, ctx

    def test_2fa_waits_over_four_minutes_then_retains_first_payload(self):
        client, page, ctx = self.client()
        def advance(ms):
            page.elapsed += ms / 1000
            if page.elapsed == 300:
                page.url = DEFAULT_BASE_URL + "/partner/"
                page.otp_visible = False
        page.wait_for_timeout.side_effect = advance
        with patch("ps38.client.time.monotonic", side_effect=lambda: page.elapsed):
            client.wait_for_login()
        self.assertEqual(page.elapsed, 300)
        self.assertTrue(client.session_ready)
        self.assertEqual(client.initial_payload["data"][0]["playerWagerId"], "test-1")
        ctx.close.assert_not_called()
        self.assertEqual(page.evaluate.call_count, 1)

    def test_403_does_not_close_or_resubmit_login(self):
        client, page, ctx = self.client()
        page.url = DEFAULT_BASE_URL + "/partner/"
        page.otp_visible = False
        page.evaluate.side_effect = [{"status": 403}, {"status": 200, "isJson": True, "data": {"data": []}}]
        with patch("ps38.client.time.monotonic", side_effect=lambda: page.elapsed):
            client.wait_for_login()
        self.assertEqual(page.elapsed, 5)
        ctx.close.assert_not_called()
        page.goto.assert_not_called()

    def test_empty_credentials_never_submit(self):
        client, page, _ = self.client()
        client._fill_login()
        page.locator.assert_not_called()

    def test_localized_login_submits_password_only_once(self):
        client, page, _ = self.client()
        page.url = DEFAULT_BASE_URL + "/partner/en/login"
        client._fill_login = Mock()
        client.wait_for_login = Mock()
        client.login()
        client._fill_login.assert_called_once()
        client.wait_for_login.assert_called_once()

    def test_secret_is_only_read_from_expected_host(self):
        client, _, _ = self.client()
        client._capture_request(Mock(url="https://www.ps38ag.com.other.invalid/", headers={"x-intellifend-secret": "ignore"}))
        self.assertEqual(client.secret, "")
        client._capture_request(Mock(url=DEFAULT_BASE_URL + BET_MONITOR_PATH, headers={"x-intellifend-secret": "local-test-secret"}))
        self.assertEqual(client.secret, "local-test-secret")

    def test_closed_window_stops_without_reopening(self):
        client, page, ctx = self.client()
        page.is_closed.return_value = True
        with self.assertRaises(Ps3838LoginWindowClosed):
            client.wait_for_login()
        page.goto.assert_not_called()
        ctx.close.assert_not_called()

    def test_cancel_keeps_wait_loop_interruptible(self):
        client, page, _ = self.client()
        with self.assertRaises(Ps3838LoginInterrupted):
            client.wait_for_login(should_continue=lambda: page.elapsed < 3)
        page.evaluate.assert_not_called()

    def test_only_read_endpoints_and_verified_sessions(self):
        client, page, _ = self.client()
        with self.assertRaises(ValueError):
            client._evaluate_fetch("/place-bet", {})
        with self.assertRaises(Ps3838SessionError):
            client.poll()
        page.evaluate.assert_not_called()


class CliTests(TempCase):
    def test_credential_environment_and_config_validation(self):
        p = self.root / "config.json"
        p.write_text(json.dumps({"name": "demo", "interval_seconds": 15}))
        with patch.dict("os.environ", {"PS38_USERNAME": "test-user", "PS38_PASSWORD": "test-only"}):
            self.assertEqual(load_config(p)["username"], "test-user")
        for bad in [{"name": "../unsafe"}, {"interval_seconds": 0}, {"interval_seconds": float("nan")}, {"base_url": "https://elsewhere.invalid"}]:
            p.write_text(json.dumps(bad))
            with self.assertRaises(ValueError):
                load_config(p)

    def test_once_saves_login_verification_response_and_closes(self):
        client = Mock(initial_payload={"data": [order()]})
        with redirect_stdout(io.StringIO()):
            code = run_watch({"name": "demo", "interval_seconds": 15}, self.root, once=True, client_factory=lambda *_: client)
        self.assertEqual(code, 0)
        client.poll.assert_not_called()
        client.close.assert_called_once()
        self.assertEqual(json.loads((self.root / "orders.jsonl").read_text())["orderId"], "test-1")
        self.assertFalse(is_running(self.root))


if __name__ == "__main__":
    unittest.main()
