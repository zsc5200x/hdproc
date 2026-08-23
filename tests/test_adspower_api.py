import unittest


class TestAdsPowerApiHelpers(unittest.TestCase):
    def test_normalize_api_base(self) -> None:
        from hdzz.core.member.adspower_api import normalize_adspower_api_base

        self.assertEqual(normalize_adspower_api_base("127.0.0.1:50325"), "http://127.0.0.1:50325")
        self.assertEqual(normalize_adspower_api_base("http://127.0.0.1:50325/"), "http://127.0.0.1:50325")

    def test_profile_proxy_url(self) -> None:
        from hdzz.core.member.adspower_api import adspower_profile_proxy_url

        row = {
            "user_proxy_config": {
                "proxy_type": "socks5",
                "proxy_host": "1.2.3.4",
                "proxy_port": "8899",
                "proxy_user": "u1",
                "proxy_password": "p1",
            }
        }
        self.assertEqual(adspower_profile_proxy_url(row), "socks5h://u1:p1@1.2.3.4:8899")

    def test_extract_ver_from_mygamever_cookie(self) -> None:
        from hdzz.core.member.adspower_api import adspower_extract_ver_candidates_from_cookies

        cookies = [{"name": "myGameVer_40500999", "value": "XzIxMTIyOA=="}]  # _211228
        out = adspower_extract_ver_candidates_from_cookies(cookies)
        self.assertIn("_211228", out)

    def test_infer_base_url_from_cookie_domain(self) -> None:
        from hdzz.core.member.adspower_api import adspower_infer_base_url_from_cookies

        cookies = [
            {"name": "twk_uuid_xxx", "value": "v", "domain": ".tawk.to"},
            {"name": "myGameVer_40500999", "value": "XzIxMTIyOA==", "domain": ".hga027.com"},
        ]
        self.assertEqual(adspower_infer_base_url_from_cookies(cookies), "https://hga027.com")


if __name__ == "__main__":
    unittest.main()
