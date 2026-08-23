import unittest


class TestWebuiAccountWatchFields(unittest.TestCase):
    def test_login3_preserves_order_source_downlines_only(self) -> None:
        from hdzz.api.webui.server import normalize_account_watch_fields

        members, downlines = normalize_account_watch_fields(
            3,
            {
                "login2_watch_members": ["abc001"],
                "login2_watch_downlines": "9025869, zk1115",
            },
        )

        self.assertEqual(members, [])
        self.assertEqual(downlines, ["9025869", "zk1115"])


if __name__ == "__main__":
    unittest.main()
