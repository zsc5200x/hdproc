import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
APP = ROOT / "app"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))


class TestMemberKeeperBrowserLaunch(unittest.TestCase):
    def test_windows_launch_args_include_sandbox_workarounds(self) -> None:
        from hdzz.core.member.keeper import _playwright_launch_args

        args = _playwright_launch_args("win32")
        self.assertIn("--no-sandbox", args)
        self.assertIn("--disable-setuid-sandbox", args)
        self.assertIn("--disable-features=RendererCodeIntegrity", args)

    def test_windows_launch_candidates_include_chrome_fallback(self) -> None:
        from hdzz.core.member.keeper import _playwright_launch_candidates

        candidates = _playwright_launch_candidates(
            platform_name="win32",
            prefer_system_chrome=False,
            force_system_chrome_only=False,
        )
        self.assertEqual(candidates[0][0], "bundled_chromium")
        self.assertEqual(candidates[1][0], "channel_chrome")
        self.assertEqual(candidates[1][1], {"channel": "chrome"})

    def test_macos_keeps_system_chrome_priority(self) -> None:
        from hdzz.core.member.keeper import _playwright_launch_candidates

        candidates = _playwright_launch_candidates(
            platform_name="darwin",
            prefer_system_chrome=False,
            force_system_chrome_only=False,
        )
        self.assertEqual(candidates[0][0], "channel_chrome")

    def test_macos_bundled_only_mode_never_adds_system_chrome(self) -> None:
        from hdzz.core.member.keeper import _playwright_launch_candidates

        candidates = _playwright_launch_candidates(
            platform_name="darwin",
            prefer_system_chrome=True,
            force_system_chrome_only=False,
            bundled_chromium_only=True,
        )
        self.assertEqual(candidates, [("bundled_chromium", {})])

    def test_launch_retries_with_clean_profile_after_target_closed(self) -> None:
        from hdzz.core.member.keeper import _launch_persistent_context_with_recovery

        with tempfile.TemporaryDirectory() as tmpdir:
            user_data_dir = Path(tmpdir) / "member-profile"
            user_data_dir.mkdir(parents=True, exist_ok=True)
            stale = user_data_dir / "SingletonLock"
            stale.write_text("stale", encoding="utf-8")

            expected_ctx = object()
            launch_mock = Mock(
                side_effect=[
                    RuntimeError("TargetClosedError: BrowserType.launch_persistent_context: Target page, context or browser has been closed"),
                    RuntimeError("ProcessSingleton: profile is already in use"),
                    expected_ctx,
                ]
            )
            pw = Mock()
            pw.chromium.launch_persistent_context = launch_mock

            ctx = _launch_persistent_context_with_recovery(
                pw,
                user_data_dir=user_data_dir,
                headless=False,
                proxy_cfg=None,
                log=lambda _msg: None,
                platform_name="win32",
            )

            self.assertIs(ctx, expected_ctx)
            self.assertEqual(launch_mock.call_count, 3)
            self.assertFalse(stale.exists())


if __name__ == "__main__":
    unittest.main()
