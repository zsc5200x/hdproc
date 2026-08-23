import unittest


class TestLogin2OrderFilter(unittest.TestCase):
    def test_normalize_downline_alias_map(self) -> None:
        from hdzz.core.watcher.order_filter import normalize_downline_alias_map

        out = normalize_downline_alias_map({"zk1115": "9025869", "": "x", "bad": ""})
        self.assertEqual(out, {"zk1115": "9025869"})

    def test_normalize_member_names_from_string(self) -> None:
        from hdzz.core.watcher.order_filter import normalize_member_names

        out = normalize_member_names("  abc001,ABC001\nabc002， abc003  ")
        self.assertEqual(out, ["abc001", "abc002", "abc003"])

    def test_should_forward_always_true_for_non_login2(self) -> None:
        from hdzz.core.watcher.order_filter import should_forward_order_for_target

        order = {"account": "no-in-list"}
        self.assertTrue(
            should_forward_order_for_target(
                relogin_login_tab=3,
                login2_watch_members=["abc001"],
                order=order,
            )
        )

    def test_should_forward_login3_downline_filter_when_configured(self) -> None:
        from hdzz.core.watcher.order_filter import should_forward_order_for_target

        matching_order = {"account": "abc999", "raw": {"DOWNLINE": "9025869"}}
        other_order = {"account": "abc999", "raw": {"DOWNLINE": "1111111"}}

        self.assertTrue(
            should_forward_order_for_target(
                relogin_login_tab=3,
                login2_watch_members=["ignored-for-login3"],
                login2_watch_downlines=["9025869"],
                order=matching_order,
            )
        )
        self.assertFalse(
            should_forward_order_for_target(
                relogin_login_tab=3,
                login2_watch_members=["ignored-for-login3"],
                login2_watch_downlines=["9025869"],
                order=other_order,
            )
        )

    def test_should_forward_true_when_login2_filter_empty(self) -> None:
        from hdzz.core.watcher.order_filter import should_forward_order_for_target

        order = {"account": "anyone"}
        self.assertTrue(
            should_forward_order_for_target(
                relogin_login_tab=2,
                login2_watch_members=[],
                order=order,
            )
        )

    def test_should_forward_true_when_order_account_matches(self) -> None:
        from hdzz.core.watcher.order_filter import should_forward_order_for_target

        order = {"account": "AbC001"}
        self.assertTrue(
            should_forward_order_for_target(
                relogin_login_tab=2,
                login2_watch_members=["abc001", "abc002"],
                order=order,
            )
        )

    def test_should_forward_true_when_raw_name0_matches(self) -> None:
        from hdzz.core.watcher.order_filter import should_forward_order_for_target

        order = {"account": "", "raw": {"NAME0": "abc002"}}
        self.assertTrue(
            should_forward_order_for_target(
                relogin_login_tab=2,
                login2_watch_members=["abc001", "abc002"],
                order=order,
            )
        )

    def test_should_forward_false_when_not_in_filter(self) -> None:
        from hdzz.core.watcher.order_filter import should_forward_order_for_target

        order = {"account": "abc999"}
        self.assertFalse(
            should_forward_order_for_target(
                relogin_login_tab=2,
                login2_watch_members=["abc001", "abc002"],
                order=order,
            )
        )

    def test_should_forward_false_when_filter_enabled_but_order_missing_account(self) -> None:
        from hdzz.core.watcher.order_filter import should_forward_order_for_target

        order = {"account": "", "raw": {}}
        self.assertFalse(
            should_forward_order_for_target(
                relogin_login_tab=2,
                login2_watch_members=["abc001"],
                order=order,
            )
        )

    def test_should_forward_true_when_downline_matches(self) -> None:
        from hdzz.core.watcher.order_filter import should_forward_order_for_target

        order = {"account": "abc999", "raw": {"DOWNLINE": "9025869"}}
        self.assertTrue(
            should_forward_order_for_target(
                relogin_login_tab=2,
                login2_watch_members=[],
                login2_watch_downlines=["9025869"],
                order=order,
            )
        )

    def test_should_forward_true_when_downline_filter_contains_dom_style_value(self) -> None:
        from hdzz.core.watcher.order_filter import should_forward_order_for_target

        order = {"account": "abc999", "raw": {"DOWNLINE": "9025869"}}
        self.assertTrue(
            should_forward_order_for_target(
                relogin_login_tab=2,
                login2_watch_members=[],
                login2_watch_downlines=["downline_9025869"],
                order=order,
            )
        )

    def test_should_forward_true_when_downline_alias_map_matches(self) -> None:
        from hdzz.core.watcher.order_filter import should_forward_order_for_target

        order = {"account": "abc999", "raw": {"DOWNLINE": "9025869"}}
        self.assertTrue(
            should_forward_order_for_target(
                relogin_login_tab=2,
                login2_watch_members=[],
                login2_watch_downlines=["zk1115"],
                login2_downline_alias_map={"zk1115": "9025869"},
                order=order,
            )
        )

    def test_should_forward_false_when_downline_alias_not_resolved(self) -> None:
        from hdzz.core.watcher.order_filter import should_forward_order_for_target

        order = {"account": "abc999", "raw": {"DOWNLINE": "9025869"}}
        self.assertFalse(
            should_forward_order_for_target(
                relogin_login_tab=2,
                login2_watch_members=[],
                login2_watch_downlines=["zk1115"],
                login2_downline_alias_map={},
                order=order,
            )
        )

    def test_should_forward_false_when_alias_contains_digits_but_not_resolved(self) -> None:
        from hdzz.core.watcher.order_filter import should_forward_order_for_target

        order = {"account": "abc999", "raw": {"DOWNLINE": "1115"}}
        self.assertFalse(
            should_forward_order_for_target(
                relogin_login_tab=2,
                login2_watch_members=[],
                login2_watch_downlines=["zk1115"],
                login2_downline_alias_map={},
                order=order,
            )
        )

    def test_should_forward_false_when_downline_not_match(self) -> None:
        from hdzz.core.watcher.order_filter import should_forward_order_for_target

        order = {"account": "abc001", "raw": {"DOWNLINE": "1111111"}}
        self.assertFalse(
            should_forward_order_for_target(
                relogin_login_tab=2,
                login2_watch_members=[],
                login2_watch_downlines=["9025869"],
                order=order,
            )
        )

    def test_should_forward_need_both_member_and_downline_when_both_filters_set(self) -> None:
        from hdzz.core.watcher.order_filter import should_forward_order_for_target

        order = {"account": "abc001", "raw": {"DOWNLINE": "9025869"}}
        self.assertFalse(
            should_forward_order_for_target(
                relogin_login_tab=2,
                login2_watch_members=["abc001"],
                login2_watch_downlines=["9999999"],
                order=order,
            )
        )
        self.assertTrue(
            should_forward_order_for_target(
                relogin_login_tab=2,
                login2_watch_members=["abc001"],
                login2_watch_downlines=["9025869"],
                order=order,
            )
        )


if __name__ == "__main__":
    unittest.main()
