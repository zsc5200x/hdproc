import unittest
import os
import threading
import json
import sqlite3
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

import crown_runtime
from crown_local.backend.auth import (
    AuthService,
    _SessionChanged,
    _allow_insecure_certificate,
    _crown_ip_redirect_origin,
)
from crown_local.backend.storage import Store
from cryptography.fernet import Fernet


class CrownAccountTests(unittest.TestCase):
    class FakeStore:
        def __init__(self, accounts):
            self._accounts = accounts
            self.saved = None
            self.cleared = None
            self.deleted = None

        def accounts(self):
            return self._accounts

        def get_account(self, account_id):
            return next(account for account in self._accounts if account["id"] == account_id)

        def save_account(self, payload, account_id=None):
            self.saved = (payload, account_id)
            return {"id": account_id or "new-account", **payload}

        def clear_session(self, account_id):
            self.cleared = account_id
            account = self.get_account(account_id)
            account["has_session"] = False
            account["status"] = "idle"

        def delete_account(self, account_id):
            self.deleted = account_id
            self._accounts = [account for account in self._accounts if account["id"] != account_id]

    class FakeAuth:
        def __init__(self):
            self.cancelled = None

        def cancel(self, account_id):
            self.cancelled = account_id

    def test_same_domain_and_username_updates_existing_account(self):
        fake = self.FakeStore([{
            "id": "existing-account",
            "kind": "crown_odds",
            "base_url": "https://m321.mos077.com",
            "username": "member",
        }])
        with patch.object(crown_runtime, "initialize"), \
                patch.object(crown_runtime, "store", fake):
            result = crown_runtime.add_account(
                "https://m321.mos077.com", "member"
            )

        self.assertEqual(result["id"], "existing-account")
        payload, account_id = fake.saved
        self.assertEqual(account_id, "existing-account")
        self.assertNotIn("password", payload)
        self.assertNotIn("totp_secret", payload)

    def test_new_domain_or_username_creates_a_new_account(self):
        fake = self.FakeStore([])
        with patch.object(crown_runtime, "initialize"), \
                patch.object(crown_runtime, "store", fake):
            result = crown_runtime.add_account(
                "https://example.com", "new-member"
            )

        self.assertEqual(result["id"], "new-account")
        _payload, account_id = fake.saved
        self.assertIsNone(account_id)

    def test_clear_session_keeps_account(self):
        fake_store = self.FakeStore([{
            "id": "existing-account",
            "kind": "crown_odds",
            "username": "member",
            "status": "authenticated",
            "has_session": True,
        }])
        fake_auth = self.FakeAuth()
        with patch.object(crown_runtime, "initialize"), \
                patch.object(crown_runtime, "store", fake_store), \
                patch.object(crown_runtime, "auth", fake_auth):
            result = crown_runtime.account_action("existing-account", "clear")

        self.assertEqual(fake_auth.cancelled, "existing-account")
        self.assertEqual(fake_store.cleared, "existing-account")
        self.assertFalse(result["has_session"])
        self.assertEqual(len(fake_store.accounts()), 1)

    def test_delete_account_removes_saved_account(self):
        fake_store = self.FakeStore([{
            "id": "existing-account",
            "kind": "crown_odds",
            "username": "member",
            "has_session": True,
        }])
        fake_auth = self.FakeAuth()
        with patch.object(crown_runtime, "initialize"), \
                patch.object(crown_runtime, "store", fake_store), \
                patch.object(crown_runtime, "auth", fake_auth):
            result = crown_runtime.account_action("existing-account", "delete")

        self.assertEqual(fake_auth.cancelled, "existing-account")
        self.assertEqual(fake_store.deleted, "existing-account")
        self.assertIsNone(result)
        self.assertEqual(fake_store.accounts(), [])

    def test_crown_ip_endpoint_allows_legacy_certificate(self):
        self.assertTrue(_allow_insecure_certificate({
            "kind": "crown_odds", "base_url": "https://64.188.47.32",
        }))

    def test_crown_domain_keeps_strict_certificate_validation(self):
        self.assertFalse(_allow_insecure_certificate({
            "kind": "crown_odds", "base_url": "https://example.com",
        }))

    def test_crown_private_ip_keeps_strict_certificate_validation(self):
        self.assertFalse(_allow_insecure_certificate({
            "kind": "crown_odds", "base_url": "https://127.0.0.1",
        }))

    def test_crown_public_ip_entry_can_adopt_public_ip_redirect(self):
        self.assertEqual(
            _crown_ip_redirect_origin(
                {"kind": "crown_odds", "base_url": "https://64.188.47.32"},
                "https://43.229.3.127/login?from=entry",
            ),
            "https://43.229.3.127",
        )

    def test_crown_redirect_does_not_adopt_private_or_domain_targets(self):
        account = {"kind": "crown_odds", "base_url": "https://64.188.47.32"}
        self.assertEqual(_crown_ip_redirect_origin(account, "https://127.0.0.1/"), "")
        self.assertEqual(_crown_ip_redirect_origin(account, "https://example.com/"), "")
        self.assertEqual(
            _crown_ip_redirect_origin(
                {"kind": "crown_odds", "base_url": "https://entry.example.com"},
                "https://43.229.3.127/",
            ),
            "",
        )

    def test_adopted_crown_ip_redirect_is_a_trusted_page_origin(self):
        job = SimpleNamespace(
            account={"kind": "crown_odds", "base_url": "https://64.188.47.32"},
            member_origin="https://43.229.3.127",
        )
        self.assertTrue(AuthService._trusted_page(job, "https://43.229.3.127/login"))

    def test_crown_launches_through_shared_cdp_browser(self):
        class FakeBrowser:
            def is_connected(self):
                return True

        class FakeChromium:
            def __init__(self):
                self.connect_urls = []
                self.launch_options = []

            def connect_over_cdp(self, url):
                self.connect_urls.append(url)
                return FakeBrowser()

            def launch(self, **options):
                self.launch_options.append(options)
                return FakeBrowser()

        chromium = FakeChromium()
        service = AuthService.__new__(AuthService)
        service._browser = None
        service._background_browser = None
        service._browser_shared = False
        service._playwright = SimpleNamespace(chromium=chromium)

        with patch.dict(os.environ, {"CROWN_CDP_URL": "http://127.0.0.1:9222"}):
            browser = service._launch()

        self.assertIsNotNone(browser)
        self.assertEqual(chromium.connect_urls, ["http://127.0.0.1:9222"])
        self.assertEqual(chromium.launch_options, [])
        self.assertTrue(service._browser_shared)

    def test_shared_cdp_uses_default_context_without_creating_another(self):
        class FakeContext:
            pass

        class FakeBrowser:
            def __init__(self):
                self.contexts = [FakeContext()]

            def new_context(self, **_options):
                raise AssertionError("shared Chrome must not create a new context")

        service = AuthService.__new__(AuthService)
        service._browser_shared = True
        browser = FakeBrowser()

        context, owns_context = service._acquire_context(browser, {"kind": "crown_odds"}, None)

        self.assertIs(context, browser.contexts[0])
        self.assertFalse(owns_context)

    def test_manual_login_page_identity_uses_only_non_secret_bootstrap_fields(self):
        class FakeFrame:
            url = "https://m829.crw066.com/"

            def evaluate(self, _script):
                return {
                    "uid": "member-uid",
                    "ver": "4.1.2",
                    "langx": "zh-cn",
                    "ltype": "1",
                    "odd_f_type": "H",
                    "mid": "12345678",
                    "passwd": "must-not-be-read",
                    "passwd_safe": "must-not-be-read",
                }

        service = AuthService.__new__(AuthService)
        job = SimpleNamespace(
            uid="", member_authenticated=False, member_origin="https://m829.crw066.com",
            member_protocol={}, member_resume_query={}, resume_url="",
            account={"kind": "crown_odds", "base_url": "https://hga038.com", "username": "member"},
        )

        adopted = service._adopt_member_page_identity(job, FakeFrame())

        self.assertTrue(adopted)
        self.assertEqual(job.uid, "member-uid")
        self.assertEqual(job.member_protocol, {
            "origin": "https://m829.crw066.com", "ver": "4.1.2", "langx": "zh-cn",
        })
        self.assertEqual(job.member_resume_query["odd_f_type"], "H")
        self.assertEqual(job.member_resume_query["ltype"], "1")
        self.assertNotIn("passwd", job.member_resume_query)
        self.assertNotIn("passwd_safe", job.member_resume_query)

    def test_manual_login_page_identity_rejects_uid_conflict(self):
        class FakeFrame:
            url = "https://m829.crw066.com/"

            def evaluate(self, _script):
                return {"uid": "new-uid", "ver": "4.1.2", "langx": "zh-cn", "ltype": "1"}

        service = AuthService.__new__(AuthService)
        job = SimpleNamespace(
            uid="existing-uid", member_authenticated=True,
            member_origin="https://m829.crw066.com", member_protocol={},
            member_resume_query={}, resume_url="",
            account={"kind": "crown_odds", "base_url": "https://hga038.com", "username": "member"},
        )

        self.assertFalse(service._adopt_member_page_identity(job, FakeFrame()))
        self.assertEqual(job.uid, "existing-uid")

    def test_visible_tab_sync_can_adopt_a_new_login_uid(self):
        class FakeFrame:
            url = "https://m829.crw066.com/"

            def evaluate(self, _script):
                return {"uid": "new-uid", "ver": "4.1.2", "langx": "zh-cn", "ltype": "1"}

        service = AuthService.__new__(AuthService)
        job = SimpleNamespace(
            uid="existing-uid", member_authenticated=True,
            member_origin="https://m829.crw066.com", member_protocol={},
            member_resume_query={}, resume_url="",
            account={"kind": "crown_odds", "base_url": "https://hga038.com", "username": "member"},
        )

        self.assertTrue(service._adopt_member_page_identity(
            job, FakeFrame(), allow_uid_change=True,
        ))
        self.assertEqual(job.uid, "new-uid")
        self.assertEqual(job.member_protocol["origin"], "https://m829.crw066.com")

    def test_saved_storage_state_excludes_pinnacle(self):
        class FakePage:
            url = "https://hga038.com/login"
            frames = []

        class FakeContext:
            def storage_state(self):
                return {
                    "cookies": [
                        {"name": "crown", "domain": ".hga038.com", "value": "c"},
                        {"name": "pinnacle", "domain": ".ps3838.com", "value": "p"},
                    ],
                    "origins": [
                        {"origin": "https://hga038.com", "localStorage": []},
                        {"origin": "https://www.ps3838.com", "localStorage": []},
                    ],
                }

        service = AuthService.__new__(AuthService)
        job = SimpleNamespace(
            account={"kind": "crown_odds", "base_url": "https://hga038.com"},
            member_origin="", resume_url="", page=FakePage(), context=FakeContext(),
        )

        state = service._scoped_storage_state(job)

        self.assertEqual([cookie["name"] for cookie in state["cookies"]], ["crown"])
        self.assertEqual([origin["origin"] for origin in state["origins"]], ["https://hga038.com"])

    def test_visible_member_page_saves_new_session_with_cas_baseline(self):
        class FakeFrame:
            url = "https://m829.crw066.com/"

            def evaluate(self, _script):
                return {
                    "uid": "new-uid", "ver": "4.1.2", "langx": "zh-cn",
                    "ltype": "1", "odd_f_type": "H",
                }

        class FakePage:
            url = "https://m829.crw066.com/"
            frames = [FakeFrame()]

            def is_closed(self):
                return False

            def set_default_timeout(self, _timeout):
                return None

        stored = {
            "uid": "old-uid",
            "member_origin": "https://m829.crw066.com",
            "member_authenticated": True,
            "member_protocol": {
                "origin": "https://m829.crw066.com", "ver": "4.1.2", "langx": "zh-cn",
            },
            "member_ui_verified": True,
            "member_resume_query": {"ltype": "1", "odd_f_type": "H"},
            "resume_url": "https://m829.crw066.com/",
            "storage_state": {"cookies": [], "origins": []},
            "saved_at": "old-saved-at",
        }
        account = {
            "id": "account", "kind": "crown_odds", "base_url": "https://hga038.com",
            "username": "member", "revision": 4,
        }
        saved = []
        service = AuthService.__new__(AuthService)
        service._visible_locator = lambda _frame, _selectors: None
        service._manual_challenge = lambda _frame: False
        service._member_signed_in = lambda _frame: True
        service._save = lambda job: saved.append((job.uid, job.session_baseline))
        service._set = lambda *_args, **_kwargs: None
        service._log = lambda *_args, **_kwargs: None

        outcome = service._sync_member_page(account, object(), FakePage(), stored)

        self.assertEqual(outcome, "synced")
        self.assertEqual(saved[0][0], "new-uid")
        self.assertIs(saved[0][1], stored)

    def test_visible_login_form_never_overwrites_saved_session(self):
        class FakePage:
            url = "https://m829.crw066.com/"
            frames = [SimpleNamespace(url="https://m829.crw066.com/")]

            def is_closed(self):
                return False

            def set_default_timeout(self, _timeout):
                return None

        stored = {
            "uid": "old-uid", "member_origin": "https://m829.crw066.com",
            "member_authenticated": True, "member_ui_verified": True,
            "member_protocol": {
                "origin": "https://m829.crw066.com", "ver": "4.1.2", "langx": "zh-cn",
            },
            "storage_state": {"cookies": [], "origins": []},
        }
        service = AuthService.__new__(AuthService)
        service._visible_locator = lambda _frame, _selectors: object()
        service._manual_challenge = lambda _frame: False
        service._member_signed_in = lambda _frame: self.fail("login form must not be adopted")
        service._save = lambda _job: self.fail("saved session must not be overwritten")

        outcome = service._sync_member_page(
            {"id": "account", "kind": "crown_odds", "base_url": "https://hga038.com"},
            object(), FakePage(), stored,
        )

        self.assertEqual(outcome, "login_form")

    def test_shared_tab_scan_reuses_crown_tab_without_touching_pinnacle(self):
        crown_page = SimpleNamespace(
            url="https://m829.crw066.com/", is_closed=lambda: False,
        )
        pinnacle_page = SimpleNamespace(
            url="https://www.ps3838.com/zh-cn/sports/soccer", is_closed=lambda: False,
        )
        context = SimpleNamespace(pages=[pinnacle_page, crown_page])
        service = AuthService.__new__(AuthService)
        service._launch = lambda background=True: object()
        service._acquire_context = lambda *_args: (context, False)
        inspected = []
        service._sync_member_page = lambda _account, _context, page, _stored, **_kwargs: (
            inspected.append(page) or "synced"
        )
        stored = {"member_origin": "https://m829.crw066.com"}
        account = {"id": "account", "kind": "crown_odds", "base_url": "https://hga038.com"}

        outcome = service._sync_shared_member_tabs(account, stored)

        self.assertEqual(outcome, "synced")
        self.assertEqual(inspected, [crown_page])

    def test_manual_shared_login_reuses_signed_crown_page_before_pinnacle(self):
        crown_page = SimpleNamespace(
            url="https://m473.mos077.com/", is_closed=lambda: False,
        )
        pinnacle_page = SimpleNamespace(
            url="https://www.ps3838.com/zh-cn/sports/soccer", is_closed=lambda: False,
        )
        context = SimpleNamespace(pages=[crown_page, pinnacle_page])
        service = AuthService.__new__(AuthService)
        service._signed_member_origin = lambda page: (
            "https://m473.mos077.com" if page is crown_page else ""
        )
        account = {"kind": "crown_odds", "base_url": "https://hga038.com"}

        self.assertIs(service._shared_second_page(context, account, None), crown_page)

    def test_manual_shared_login_does_not_reuse_unverified_or_pinnacle_pages(self):
        pinnacle_page = SimpleNamespace(
            url="https://www.ps3838.com/zh-cn/sports/soccer", is_closed=lambda: False,
        )
        unrelated_page = SimpleNamespace(
            url="https://unrelated.example/", is_closed=lambda: False,
        )
        service = AuthService.__new__(AuthService)
        service._signed_member_origin = lambda _page: ""
        account = {"kind": "crown_odds", "base_url": "https://hga038.com"}

        self.assertIsNone(service._shared_second_page(
            SimpleNamespace(pages=[pinnacle_page]), account, None,
        ))
        self.assertIsNone(service._shared_second_page(
            SimpleNamespace(pages=[pinnacle_page, unrelated_page]), account, None,
        ))

    def test_shared_tab_scan_finds_signed_crown_page_before_pinnacle(self):
        """Crown sync is independent of tab order and never inspects Pinnacle."""
        crown_page = SimpleNamespace(
            url="https://rotating-member.example/", is_closed=lambda: False,
        )
        pinnacle_page = SimpleNamespace(
            url="https://www.ps3838.com/zh-cn/sports/soccer", is_closed=lambda: False,
        )
        context = SimpleNamespace(pages=[crown_page, pinnacle_page])
        service = AuthService.__new__(AuthService)
        service._launch = lambda background=True: object()
        service._acquire_context = lambda *_args: (context, False)
        service._page_matches_account = lambda *_args: False
        signed_checks = []

        def signed_origin(page):
            signed_checks.append(page)
            return "https://rotating-member.example" if page is crown_page else ""

        service._signed_member_origin = signed_origin
        inspected = []

        def inspect(_account, _context, page, _stored, **kwargs):
            inspected.append((page, kwargs.get("adopted_origin")))
            return "synced"

        service._sync_member_page = inspect
        stored = {"member_origin": "https://old-member.example/"}
        account = {
            "id": "account",
            "kind": "crown_odds",
            "base_url": "https://entry.example/",
            "username": "a-different-account",
        }

        outcome = service._sync_shared_member_tabs(account, stored)

        self.assertEqual(outcome, "synced")
        self.assertEqual(signed_checks, [crown_page])
        self.assertEqual(inspected, [(crown_page, "https://rotating-member.example")])

    def test_shared_tab_scan_does_not_adopt_unverified_non_crown_pages(self):
        unrelated_page = SimpleNamespace(
            url="https://unrelated.example/", is_closed=lambda: False,
        )
        pinnacle_page = SimpleNamespace(
            url="https://www.ps3838.com/zh-cn/sports/soccer", is_closed=lambda: False,
        )
        context = SimpleNamespace(pages=[pinnacle_page, unrelated_page])
        service = AuthService.__new__(AuthService)
        service._launch = lambda background=True: object()
        service._acquire_context = lambda *_args: (context, False)
        service._page_matches_account = lambda *_args: False
        service._signed_member_origin = lambda _page: ""
        service._sync_member_page = lambda *_args, **_kwargs: self.fail(
            "unverified pages must not reach the Crown session synchronizer"
        )

        outcome = service._sync_shared_member_tabs(
            {"id": "account", "kind": "crown_odds", "base_url": "https://entry.example/"},
            {"member_origin": "https://old-member.example/"},
        )

        self.assertEqual(outcome, "not_signed_in")

    def test_shared_tab_scan_adopts_rotating_member_origin(self):
        class FakeFrame:
            url = "https://m473.mos077.com/"

        crown_page = SimpleNamespace(
            url="https://m473.mos077.com/", frames=[FakeFrame()],
            is_closed=lambda: False,
            set_default_timeout=lambda _timeout: None,
        )
        pinnacle_page = SimpleNamespace(
            url="https://www.ps3838.com/zh-cn/sports/soccer", is_closed=lambda: False,
        )
        context = SimpleNamespace(pages=[pinnacle_page, crown_page])
        service = AuthService.__new__(AuthService)
        service._launch = lambda background=True: object()
        service._acquire_context = lambda *_args: (context, False)
        service._member_signed_in = lambda _frame: True
        inspected = []

        def inspect(_account, _context, page, _stored, adopted_origin=""):
            inspected.append((page, adopted_origin))
            return "synced"

        service._sync_member_page = inspect
        stored = {"member_origin": "https://m829.crw066.com"}
        account = {"id": "account", "kind": "crown_odds", "base_url": "https://hga038.com"}

        outcome = service._sync_shared_member_tabs(account, stored)

        self.assertEqual(outcome, "synced")
        self.assertEqual(inspected, [(crown_page, "https://m473.mos077.com")])

    def test_visible_sync_conflict_does_not_replace_newer_session(self):
        class FakeFrame:
            url = "https://m829.crw066.com/"

            def evaluate(self, _script):
                return {"uid": "new-uid", "ver": "4.1.2", "langx": "zh-cn", "ltype": "1"}

        class FakePage:
            url = "https://m829.crw066.com/"
            frames = [FakeFrame()]

            def is_closed(self):
                return False

            def set_default_timeout(self, _timeout):
                return None

        stored = {
            "uid": "old-uid", "member_origin": "https://m829.crw066.com",
            "member_authenticated": True, "member_ui_verified": True,
            "member_protocol": {
                "origin": "https://m829.crw066.com", "ver": "4.1.2", "langx": "zh-cn",
            },
            "storage_state": {"cookies": [], "origins": []},
        }
        service = AuthService.__new__(AuthService)
        service._visible_locator = lambda _frame, _selectors: None
        service._manual_challenge = lambda _frame: False
        service._member_signed_in = lambda _frame: True
        service._save = lambda _job: (_ for _ in ()).throw(_SessionChanged("changed"))
        service._log = lambda *_args, **_kwargs: None

        outcome = service._sync_member_page(
            {"id": "account", "kind": "crown_odds", "base_url": "https://hga038.com"},
            object(), FakePage(), stored,
        )

        self.assertEqual(outcome, "conflict")

    def test_completed_login_keeps_visible_tab_until_disposed(self):
        class FakePage:
            def __init__(self):
                self.front_count = 0
                self.close_count = 0
                self.closed = False

            def bring_to_front(self):
                self.front_count += 1

            def is_closed(self):
                return self.closed

            def close(self):
                self.close_count += 1
                self.closed = True

        class FakeContext:
            def __init__(self):
                self.close_count = 0

            def close(self):
                self.close_count += 1

        context = FakeContext()
        page = FakePage()
        service = AuthService.__new__(AuthService)
        service._jobs = {"account": SimpleNamespace(context=context, page=page, owns_context=False)}
        service._screenshots = {}
        service._visible_pages = {}
        service._lock = threading.RLock()

        service._retain_visible("account")

        self.assertNotIn("account", service._jobs)
        self.assertIn("account", service._visible_pages)
        self.assertEqual(context.close_count, 0)
        self.assertEqual(page.front_count, 1)
        self.assertTrue(service.browser_open("account"))

        service._dispose("account")

        self.assertEqual(context.close_count, 0)
        self.assertEqual(page.close_count, 1)
        self.assertFalse(service.browser_open("account"))

    def test_background_check_does_not_replace_visible_tab(self):
        class FakeStore:
            def get_account(self, account_id):
                return {
                    "id": account_id, "kind": "crown_odds", "enabled": True,
                    "revision": 1, "base_url": "https://hga038.com",
                    "login_path": "/", "username": "member",
                }

            def load_session(self, _account_id):
                return None

        retained = SimpleNamespace(
            account_id="account",
            account={
                "kind": "crown_odds", "base_url": "https://hga038.com",
                "login_path": "/", "username": "member",
            },
            context=object(), page=object(),
        )
        service = AuthService.__new__(AuthService)
        service.store = FakeStore()
        service._jobs = {}
        service._visible_pages = {"account": retained}
        service._sync_member_page = lambda *_args, **_kwargs: "not_signed_in"

        service._begin("account", background=True)

        self.assertIs(service._visible_pages["account"], retained)
        self.assertEqual(service._jobs, {})

    def test_startup_replaces_legacy_credentials_with_empty_encrypted_object(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"REPORT_MASTER_KEY": Fernet.generate_key().decode()}
        ):
            store = Store(directory)
            account = store.save_account({
                "kind": "crown_odds",
                "base_url": "https://hga038.com",
                "username": "member",
                "enabled": True,
                "interval": 30,
            })
            legacy = store.vault.encrypt({
                "password": "legacy-password",
                "safe_password": "legacy-safe-password",
                "totp_secret": "LEGACYSECRET",
            })
            with sqlite3.connect(os.path.join(directory, "crown.sqlite3")) as connection:
                connection.execute("UPDATE accounts SET credentials=? WHERE id=?", (legacy, account["id"]))

            restarted = Store(directory)
            with sqlite3.connect(os.path.join(directory, "crown.sqlite3")) as connection:
                stored = connection.execute(
                    "SELECT credentials FROM accounts WHERE id=?", (account["id"],)
                ).fetchone()[0]

            self.assertEqual(restarted.vault.decrypt(stored), {})
            self.assertNotIn("has_password", restarted.get_account(account["id"]))
            self.assertNotIn("has_totp_secret", restarted.get_account(account["id"]))


if __name__ == "__main__":
    unittest.main()
