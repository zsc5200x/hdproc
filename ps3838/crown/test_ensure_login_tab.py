import sys
import unittest
from unittest.mock import patch

from ensure_login_tab import main


ACCOUNT = {
    "id": "crown-account",
    "kind": "crown_odds",
    "base_url": "https://hga038.com",
    "login_path": "/member/login",
    "role": "primary",
    "enabled": True,
}


class EnsureLoginTabTests(unittest.TestCase):
    def run_main(self):
        return main()

    def setUp(self):
        self.argv_patcher = patch.object(
            sys,
            "argv",
            [
                "ensure_login_tab.py",
                "--cdp-url", "http://127.0.0.1:9222",
                "--service-url", "http://127.0.0.1:9223",
            ],
        )
        self.argv_patcher.start()
        self.addCleanup(self.argv_patcher.stop)

    def test_existing_crown_tab_is_not_duplicated(self):
        targets = [{
            "type": "page",
            "url": "https://hga038.com/",
        }]
        with patch(
            "ensure_login_tab.request_json",
            side_effect=[{"accounts": [ACCOUNT]}, targets],
        ) as request_json:
            self.assertEqual(self.run_main(), 0)

        self.assertEqual(request_json.call_count, 2)

    def test_existing_dynamic_member_tab_is_not_duplicated(self):
        targets = [
            {
                "type": "page",
                "url": "https://www.ps3838.com/zh-cn/sports/soccer",
            },
            {
                "type": "page",
                "url": "https://m473.mos077.com/",
            },
        ]
        with patch(
            "ensure_login_tab.request_json",
            side_effect=[{"accounts": [ACCOUNT]}, targets],
        ) as request_json:
            self.assertEqual(self.run_main(), 0)

        self.assertEqual(request_json.call_count, 2)

    def test_only_pinnacle_tab_opens_crown_entry(self):
        targets = [{
            "type": "page",
            "url": "https://www.ps3838.com/zh-cn/sports/soccer",
        }]
        with (
            patch(
                "ensure_login_tab.request_json",
                side_effect=[{"accounts": [ACCOUNT]}, targets, {}],
            ) as request_json,
            patch("ensure_login_tab.time.monotonic", side_effect=[0, 20]),
            patch("ensure_login_tab.time.sleep"),
        ):
            self.assertEqual(self.run_main(), 1)

        open_url, = request_json.call_args_list[2].args
        self.assertEqual(
            open_url,
            "http://127.0.0.1:9222/json/new?https%3A%2F%2Fhga038.com%2Fmember%2Flogin",
        )
        self.assertEqual(request_json.call_args_list[2].kwargs["method"], "PUT")

    def test_missing_crown_tab_opens_url_directly_without_login_api(self):
        targets = [{
            "type": "page",
            "url": "https://hga038.com/member/login",
        }]
        with (
            patch(
                "ensure_login_tab.request_json",
                side_effect=[{"accounts": [ACCOUNT]}, [], {}, targets],
            ) as request_json,
            patch("ensure_login_tab.time.monotonic", side_effect=[0, 0.1]),
            patch("ensure_login_tab.time.sleep"),
        ):
            self.assertEqual(self.run_main(), 0)

        open_url, = request_json.call_args_list[2].args
        self.assertEqual(
            open_url,
            "http://127.0.0.1:9222/json/new?https%3A%2F%2Fhga038.com%2Fmember%2Flogin",
        )
        self.assertEqual(request_json.call_args_list[2].kwargs["method"], "PUT")
        self.assertTrue(all("/api/crown/accounts/" not in call.args[0]
                            for call in request_json.call_args_list))


if __name__ == "__main__":
    unittest.main()
