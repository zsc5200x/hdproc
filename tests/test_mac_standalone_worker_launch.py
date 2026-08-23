import os
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "app"

if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import app as launcher  # noqa: E402


class TestMacStandaloneWorkerLaunch(unittest.TestCase):
    def test_build_worker_process_command_uses_same_executable_when_frozen(self) -> None:
        spec = launcher.WorkerSpec(
            name="ag_wmc_watch",
            args=["-u", "ag_wmc_watch.py", "--config", "config.json", "--interval", "3"],
        )

        cmd = launcher.build_worker_process_command(
            spec,
            executable="/Applications/AG Matrix Standalone.app/Contents/MacOS/AG Matrix Standalone",
            frozen=True,
        )

        self.assertEqual(
            cmd[:3],
            [
                "/Applications/AG Matrix Standalone.app/Contents/MacOS/AG Matrix Standalone",
                "--hdzz-worker",
                "ag_wmc_watch",
            ],
        )
        self.assertEqual(cmd[3:], ["--config", "config.json", "--interval", "3"])

    def test_build_worker_process_command_keeps_python_script_in_source_mode(self) -> None:
        spec = launcher.WorkerSpec(
            name="webui_server",
            args=["-u", "webui_server.py", "--host", "127.0.0.1", "--port", "9999"],
        )

        cmd = launcher.build_worker_process_command(spec, executable="/usr/bin/python3", frozen=False)

        self.assertEqual(
            cmd,
            [
                "/usr/bin/python3",
                "-u",
                str((APP_DIR / "webui_server.py").resolve()),
                "--host",
                "127.0.0.1",
                "--port",
                "9999",
            ],
        )

    def test_resolve_app_root_prefers_environment_override(self) -> None:
        fallback = ROOT / "app" / "app.py"
        with mock.patch.dict(os.environ, {"HDZZ_APP_DIR": "/tmp/standalone-app-root"}, clear=False):
            resolved = launcher.resolve_app_root(fallback)

        self.assertEqual(resolved, Path("/tmp/standalone-app-root").resolve())

    def test_telegram_signal_worker_uses_independent_settings_and_pid_file(self) -> None:
        spec = launcher._telegram_signal_worker_spec()

        self.assertEqual(spec.name, "telegram_signal_watch")
        self.assertIn("telegram_signal_watch.py", spec.args)
        self.assertIn("data/telegram_signal.json", spec.args)
        self.assertEqual(launcher.TELEGRAM_SIGNAL_PID_FILE.name, "telegram_signal.pid.json")

    def test_runtime_playwright_browser_matcher_only_matches_current_app_profiles(self) -> None:
        root = Path("/Users/demo/Library/Application Support/AG Matrix Clean 6622/app")
        current_profile = (
            "/Applications/Chromium.app/Contents/MacOS/Chromium "
            "--user-data-dir=/Users/demo/Library/Application Support/AG Matrix Clean 6622/app/state/_pw/login3"
        )
        current_member_profile = (
            "/Applications/Chromium.app/Contents/MacOS/Chromium "
            "--user-data-dir=/Users/demo/Library/Application Support/AG Matrix Clean 6622/app/data/_pw_member/member1"
        )
        normal_chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        other_app_profile = (
            "/Applications/Chromium.app/Contents/MacOS/Chromium "
            "--user-data-dir=/Users/demo/Library/Application Support/AG Matrix Clean 6633/app/state/_pw/login3"
        )

        self.assertTrue(launcher._is_runtime_playwright_browser_command(current_profile, root))
        self.assertTrue(launcher._is_runtime_playwright_browser_command(current_member_profile, root))
        self.assertFalse(launcher._is_runtime_playwright_browser_command(normal_chrome, root))
        self.assertFalse(launcher._is_runtime_playwright_browser_command(other_app_profile, root))


if __name__ == "__main__":
    unittest.main()
