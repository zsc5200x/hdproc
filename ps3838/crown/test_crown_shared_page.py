import unittest
import os
from types import SimpleNamespace
from unittest.mock import Mock, patch

from crown_local.backend.auth import AuthService


class _Store:
    def __init__(self, account):
        self.account = account

    def get_account(self, account_id):
        return dict(self.account, id=account_id)

    def load_session(self, _account_id):
        return None


class _Page:
    def __init__(self, url):
        self.url = url
        self.goto_calls = []
        self.front_count = 0
        self.handlers = []

    def is_closed(self):
        return False

    def set_default_timeout(self, _timeout):
        pass

    def set_default_navigation_timeout(self, _timeout):
        pass

    def wait_for_timeout(self, _timeout):
        pass

    def route(self, *_args):
        pass

    def bring_to_front(self):
        self.front_count += 1

    def on(self, *args):
        self.handlers.append(args)

    def goto(self, url, **_kwargs):
        self.goto_calls.append(url)


class CrownSharedPageTests(unittest.TestCase):
    def make_service(self, pages):
        account = {
            "kind": "crown_odds",
            "base_url": "https://hga038.com",
            "login_path": "/member/login",
            "enabled": True,
            "revision": 1,
        }
        context = SimpleNamespace(pages=pages)
        service = AuthService.__new__(AuthService)
        service.store = _Store(account)
        service._jobs = {}
        service._visible_pages = {}
        service._browser_shared = True
        service._destinations = {}
        service._screenshots = {}
        service._launch = Mock(return_value=object())
        service._acquire_context = Mock(return_value=(context, False))
        service._dispose = Mock()
        service._set = Mock()
        service._check_saved_member = Mock(return_value=False)
        service._check_page = Mock()
        service._signed_member_origin = lambda _page: ""
        return service, context

    def test_manual_begin_reuses_signed_crown_tab_before_pinnacle(self):
        pinnacle = _Page("https://www.ps3838.com/zh-cn/sports/soccer")
        crown = _Page("https://m473.mos077.com/")
        service, context = self.make_service([crown, pinnacle])
        service._signed_member_origin = lambda page: (
            "https://m473.mos077.com" if page is crown else ""
        )

        service._begin("account", background=False)

        self.assertIs(service._jobs["account"].page, crown)
        self.assertEqual(crown.goto_calls, [])
        self.assertEqual(context.pages, [crown, pinnacle])
        self.assertEqual(crown.front_count, 1)

    def test_manual_begin_creates_second_tab_when_missing(self):
        pinnacle = _Page("https://www.ps3838.com/zh-cn/sports/soccer")
        created = _Page("about:blank")
        context = SimpleNamespace(pages=[pinnacle], new_page=Mock(return_value=created))
        service, _ = self.make_service([pinnacle])
        service._acquire_context = Mock(return_value=(context, False))

        service._begin("account", background=False)

        context.new_page.assert_called_once_with()
        self.assertIs(service._jobs["account"].page, created)
        self.assertEqual(created.goto_calls, ["https://hga038.com/member/login"])

    def test_shared_second_page_does_not_take_over_pinnacle(self):
        first = _Page("https://www.ps3838.com/zh-cn/sports/soccer")
        second_pinnacle = _Page("https://www.ps3838.com/zh-cn/sports/tennis")
        context = SimpleNamespace(pages=[first, second_pinnacle], new_page=Mock())
        service, _ = self.make_service([first, second_pinnacle])
        service._acquire_context = Mock(return_value=(context, False))
        created = _Page("about:blank")
        context.new_page.return_value = created

        service._begin("account", background=False)

        context.new_page.assert_called_once_with()
        self.assertIs(service._jobs["account"].page, created)

    def test_background_check_scans_tabs_when_retained_page_is_unusable(self):
        for current, outcome in [(True, "absent"), (True, "login_form"),
                                 (True, "not_signed_in"), (False, "absent")]:
            with self.subTest(current=current, outcome=outcome), patch.dict(
                os.environ, {"CROWN_CDP_URL": "http://127.0.0.1:9222"}
            ):
                service, context = self.make_service([])
                retained = SimpleNamespace(context=context, page=object())
                service._visible_pages["account"] = retained
                service._is_current = Mock(return_value=current)
                service._sync_member_page = Mock(return_value=outcome)
                service._sync_shared_member_tabs = Mock(return_value="synced")

                service._begin("account", background=True)

                service._sync_shared_member_tabs.assert_called_once()
                service._dispose.assert_not_called()
                self.assertIs(service._visible_pages["account"], retained)

    def test_successful_retained_sync_does_not_rescan_other_tabs(self):
        with patch.dict(os.environ, {"CROWN_CDP_URL": "http://127.0.0.1:9222"}):
            service, context = self.make_service([])
            service._visible_pages["account"] = SimpleNamespace(context=context, page=object())
            service._is_current = Mock(return_value=True)
            service._sync_member_page = Mock(return_value="synced")
            service._sync_shared_member_tabs = Mock()

            service._begin("account", background=True)

            service._sync_shared_member_tabs.assert_not_called()
            service._dispose.assert_not_called()


if __name__ == "__main__":
    unittest.main()
