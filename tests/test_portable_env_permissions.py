import stat
import tempfile
import unittest
from pathlib import Path


class TestPortableEnvPermissions(unittest.TestCase):
    def test_repair_playwright_permissions_for_macos_chromium_binary(self) -> None:
        from portable_env import repair_playwright_browsers_permissions

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bin_path = (
                root
                / "chromium-1208"
                / "chrome-mac-arm64"
                / "Google Chrome for Testing.app"
                / "Contents"
                / "MacOS"
                / "Google Chrome for Testing"
            )
            bin_path.parent.mkdir(parents=True, exist_ok=True)
            bin_path.write_text("stub", encoding="utf-8")
            bin_path.chmod(0o644)

            changed = repair_playwright_browsers_permissions(root)

            mode = stat.S_IMODE(bin_path.stat().st_mode)
            self.assertTrue(bool(mode & stat.S_IXUSR))
            self.assertTrue(changed)

    def test_repair_playwright_permissions_for_macos_crashpad_helper(self) -> None:
        from portable_env import repair_playwright_browsers_permissions

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            helper_path = (
                root
                / "chromium-1208"
                / "chrome-mac-arm64"
                / "Google Chrome for Testing.app"
                / "Contents"
                / "Frameworks"
                / "Google Chrome for Testing Framework.framework"
                / "Versions"
                / "145.0.7632.6"
                / "Helpers"
                / "chrome_crashpad_handler"
            )
            helper_path.parent.mkdir(parents=True, exist_ok=True)
            helper_path.write_text("stub", encoding="utf-8")
            helper_path.chmod(0o644)

            changed = repair_playwright_browsers_permissions(root)

            mode = stat.S_IMODE(helper_path.stat().st_mode)
            self.assertTrue(bool(mode & stat.S_IXUSR))
            self.assertTrue(changed)

    def test_repair_playwright_permissions_for_macos_framework_and_dylib(self) -> None:
        from portable_env import repair_playwright_browsers_permissions

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            framework_bin = (
                root
                / "chromium-1208"
                / "chrome-mac-arm64"
                / "Google Chrome for Testing.app"
                / "Contents"
                / "Frameworks"
                / "Google Chrome for Testing Framework.framework"
                / "Versions"
                / "145.0.7632.6"
                / "Google Chrome for Testing Framework"
            )
            dylib = framework_bin.parent / "Libraries" / "libEGL.dylib"
            framework_bin.parent.mkdir(parents=True, exist_ok=True)
            dylib.parent.mkdir(parents=True, exist_ok=True)
            framework_bin.write_text("stub", encoding="utf-8")
            dylib.write_text("stub", encoding="utf-8")
            framework_bin.chmod(0o644)
            dylib.chmod(0o644)

            changed = repair_playwright_browsers_permissions(root)

            framework_mode = stat.S_IMODE(framework_bin.stat().st_mode)
            dylib_mode = stat.S_IMODE(dylib.stat().st_mode)
            self.assertTrue(bool(framework_mode & stat.S_IXUSR))
            self.assertTrue(bool(dylib_mode & stat.S_IXUSR))
            self.assertTrue(changed)


if __name__ == "__main__":
    unittest.main()
