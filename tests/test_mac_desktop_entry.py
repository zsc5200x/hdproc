import json
import sys
import tempfile
import unittest
import plistlib
import tarfile
from unittest import mock
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "app"

if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


import mac_standalone_entry as desktop_entry  # noqa: E402


class TestMacDesktopEntry(unittest.TestCase):
    def _write_macos_chromium_marker(self, root: Path, revision: str = "1208") -> Path:
        exe = (
            root
            / f"chromium-{revision}"
            / "chrome-mac-arm64"
            / "Google Chrome for Testing.app"
            / "Contents"
            / "MacOS"
            / "Google Chrome for Testing"
        )
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.write_text("mac chromium", encoding="utf-8")
        return exe

    def _write_windows_chromium_marker(self, root: Path) -> Path:
        exe = root / "chromium-1200" / "chrome-win64" / "chrome.exe"
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.write_text("windows chromium", encoding="utf-8")
        return exe

    def test_build_service_url_normalizes_host_and_port(self) -> None:
        self.assertEqual(desktop_entry.build_service_url("127.0.0.1", 9999), "http://127.0.0.1:9999/")

    def test_should_show_desktop_window_only_for_successful_start_actions(self) -> None:
        self.assertTrue(desktop_entry.should_show_desktop_window("start", 0))
        self.assertTrue(desktop_entry.should_show_desktop_window("restart", 0))
        self.assertTrue(desktop_entry.should_show_desktop_window("start", 2))
        self.assertFalse(desktop_entry.should_show_desktop_window("status", 0))
        self.assertFalse(desktop_entry.should_show_desktop_window("start", 3))

    def test_worker_shutdown_controller_stops_workers_only_once(self) -> None:
        calls = []

        class FakeLauncher:
            @staticmethod
            def stop_all(force: bool) -> int:
                calls.append(force)
                return 0

        controller = desktop_entry.WorkerShutdownController(FakeLauncher, enabled=True)
        controller.shutdown()
        controller.shutdown()

        self.assertEqual(calls, [False])

    def test_worker_shutdown_controller_builds_stop_command_for_frozen_runtime(self) -> None:
        class FakeLauncher:
            __file__ = "/tmp/app.py"

            @staticmethod
            def stop_all(force: bool) -> int:
                return 0

        with mock.patch("mac_standalone_entry.is_frozen_runtime", return_value=True):
            with mock.patch.object(desktop_entry.sys, "executable", "/Applications/AG Matrix Standalone.app/Contents/MacOS/AG Matrix Standalone"):
                controller = desktop_entry.WorkerShutdownController(FakeLauncher, enabled=True)
                cmd = controller.build_stop_command(force=True)

        self.assertEqual(
            cmd,
            ["/Applications/AG Matrix Standalone.app/Contents/MacOS/AG Matrix Standalone", "stop", "--force"],
        )

    def test_run_launcher_action_can_capture_console_output_to_log_file(self) -> None:
        class FakeLauncher:
            @staticmethod
            def main(argv):
                print("hello from launcher")
                return 0

        with tempfile.TemporaryDirectory() as td:
            log_path = Path(td) / "desktop-launcher.log"
            code = desktop_entry.run_launcher_action(
                ["start"],
                FakeLauncher,
                capture_output=True,
                log_path=log_path,
            )

            self.assertEqual(code, 0)
            self.assertIn("hello from launcher", log_path.read_text(encoding="utf-8"))

    def test_prepare_playwright_browsers_replaces_invalid_runtime_cache(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bundle_root = root / "bundle"
            runtime_app_dir = root / "runtime" / "app"
            bundled = bundle_root / "ms-playwright"
            runtime = runtime_app_dir / ".run" / "ms-playwright"
            self._write_macos_chromium_marker(bundled)
            self._write_windows_chromium_marker(runtime)

            detected = desktop_entry.prepare_playwright_browsers(bundle_root, runtime_app_dir)

            self.assertEqual(detected, runtime)
            self.assertTrue((runtime / "chromium-1208" / "chrome-mac-arm64").exists())
            self.assertFalse((runtime / "chromium-1200" / "chrome-win64").exists())

    def test_prepare_playwright_browsers_extracts_browser_archive(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bundle_root = root / "bundle"
            runtime_app_dir = root / "runtime" / "app"
            staged = root / "staged" / "ms-playwright"
            runtime = runtime_app_dir / ".run" / "ms-playwright"
            archive = bundle_root / "ms-playwright.tar.gz"
            self._write_macos_chromium_marker(staged)
            self._write_windows_chromium_marker(runtime)
            bundle_root.mkdir(parents=True, exist_ok=True)
            with tarfile.open(archive, "w:gz") as tf:
                tf.add(str(staged), arcname="ms-playwright")

            detected = desktop_entry.prepare_playwright_browsers(bundle_root, runtime_app_dir)

            self.assertEqual(detected, runtime)
            self.assertTrue((runtime / "chromium-1208" / "chrome-mac-arm64").exists())
            self.assertFalse((runtime / "chromium-1200" / "chrome-win64").exists())

    def test_prepare_playwright_browsers_replaces_mismatched_runtime_revision_from_archive(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bundle_root = root / "bundle"
            runtime_app_dir = root / "runtime" / "app"
            staged = root / "staged" / "ms-playwright"
            runtime = runtime_app_dir / ".run" / "ms-playwright"
            archive = bundle_root / "ms-playwright.tar.gz"
            self._write_macos_chromium_marker(staged, revision="1208")
            self._write_macos_chromium_marker(runtime, revision="1217")
            bundle_root.mkdir(parents=True, exist_ok=True)
            with tarfile.open(archive, "w:gz") as tf:
                tf.add(str(staged), arcname="ms-playwright")

            detected = desktop_entry.prepare_playwright_browsers(bundle_root, runtime_app_dir)

            self.assertEqual(detected, runtime)
            self.assertTrue((runtime / "chromium-1208" / "chrome-mac-arm64").exists())
            self.assertFalse((runtime / "chromium-1217" / "chrome-mac-arm64").exists())

    def test_resolve_runtime_service_url_prefers_actual_running_port(self) -> None:
        class FakeLauncher:
            @staticmethod
            def read_pid_info():
                return {"host": "127.0.0.1", "port": 18888}

        self.assertEqual(
            desktop_entry.resolve_runtime_service_url(FakeLauncher, default_host="127.0.0.1", default_port=9999),
            "http://127.0.0.1:18888/",
        )

    def test_invoke_worker_main_uses_argv_when_supported(self) -> None:
        seen = {}

        def fake_main(argv):
            seen["argv"] = list(argv)
            return 7

        result = desktop_entry.invoke_worker_main(fake_main, ["--port", "9999"], worker_name="webui_server")

        self.assertEqual(result, 7)
        self.assertEqual(seen["argv"], ["--port", "9999"])

    def test_invoke_worker_main_sets_sys_argv_for_noarg_main(self) -> None:
        seen = {}

        def fake_main():
            seen["argv"] = list(sys.argv)
            return 9

        original_argv = list(sys.argv)
        result = desktop_entry.invoke_worker_main(fake_main, ["--port", "9999"], worker_name="webui_server")

        self.assertEqual(result, 9)
        self.assertEqual(seen["argv"], ["webui_server", "--port", "9999"])
        self.assertEqual(sys.argv, original_argv)

    def test_frozen_bundle_uses_bundle_metadata_for_runtime_root_and_default_port(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            bundle_contents = Path(td) / "HD Crown Sports系统.app" / "Contents"
            macos_dir = bundle_contents / "MacOS"
            macos_dir.mkdir(parents=True, exist_ok=True)
            exe_path = macos_dir / "HD Crown Sports系统"
            exe_path.write_text("", encoding="utf-8")
            with (bundle_contents / "Info.plist").open("wb") as fh:
                plistlib.dump(
                    {
                        "HDZZRuntimeAppName": "HD Crown Sports系统",
                        "HDZZDefaultPort": 10099,
                    },
                    fh,
                )

            with mock.patch("mac_standalone_entry.is_frozen_runtime", return_value=True):
                with mock.patch.object(desktop_entry.sys, "executable", str(exe_path)):
                    action, host, port = desktop_entry.parse_launch_request([])
                    runtime_root = desktop_entry.resolve_runtime_root()

        self.assertEqual(action, "start")
        self.assertEqual(host, "127.0.0.1")
        self.assertEqual(port, 10099)
        self.assertEqual(
            runtime_root,
            (Path.home() / "Library" / "Application Support" / "HD Crown Sports系统").resolve(),
        )

    def test_inject_default_port_adds_bundle_default_for_start_when_missing(self) -> None:
        with mock.patch("mac_standalone_entry.resolve_default_port", return_value=10099):
            args = desktop_entry.inject_default_port_args([])

        self.assertEqual(args, ["--port", "10099"])

    def test_inject_default_port_keeps_explicit_port(self) -> None:
        with mock.patch("mac_standalone_entry.resolve_default_port", return_value=10099):
            args = desktop_entry.inject_default_port_args(["start", "--port", "9999"])

        self.assertEqual(args, ["start", "--port", "9999"])

    def test_build_startup_html_uses_splash_visual_when_available(self) -> None:
        html = desktop_entry.build_startup_html(
            "HD Sports后台系统",
            splash_data_url="data:image/jpeg;base64,abc123",
        )

        self.assertIn("HD Sports后台系统", html)
        self.assertIn("data:image/jpeg;base64,abc123", html)
        self.assertIn("正在连接后台服务", html)

    def test_seed_runtime_app_copies_tg_auth_seed_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            bundle_root = td_path / "bundle"
            runtime_app_dir = td_path / "runtime" / "app"
            (bundle_root / "app" / "webui").mkdir(parents=True, exist_ok=True)
            (bundle_root / "app" / "data").mkdir(parents=True, exist_ok=True)
            (bundle_root / "app" / "config.example.json").write_text("{}", encoding="utf-8")
            (bundle_root / "app" / "README.md").write_text("readme", encoding="utf-8")
            (bundle_root / "app" / "data" / "tg_auth.json").write_text(
                '{"admins":{"ids":["7585028453"]},"grants":{"ids":[],"usernames":[]}}',
                encoding="utf-8",
            )

            desktop_entry.seed_runtime_app(bundle_root, runtime_app_dir)

            tg_auth_dst = runtime_app_dir / "data" / "tg_auth.json"
            self.assertTrue(tg_auth_dst.is_file())
            self.assertIn("7585028453", tg_auth_dst.read_text(encoding="utf-8"))

    def test_seed_runtime_app_adds_api_credentials_without_replacing_session(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            bundle_root = td_path / "bundle"
            runtime_app_dir = td_path / "runtime" / "app"
            (bundle_root / "app" / "webui").mkdir(parents=True, exist_ok=True)
            (bundle_root / "app" / "runtime_seeds").mkdir(parents=True, exist_ok=True)
            (bundle_root / "app" / "config.example.json").write_text("{}", encoding="utf-8")
            (bundle_root / "app" / "README.md").write_text("readme", encoding="utf-8")
            (bundle_root / "app" / "runtime_seeds" / "telegram_signal_credentials.json").write_text(
                '{"api_id":"12345678","api_hash":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}',
                encoding="utf-8",
            )
            runtime_secret = runtime_app_dir / "data" / "telegram_signal_secrets.json"
            runtime_secret.parent.mkdir(parents=True, exist_ok=True)
            runtime_secret.write_text('{"session":"existing-session","phone":"+8613800000000"}', encoding="utf-8")

            desktop_entry.seed_runtime_app(bundle_root, runtime_app_dir)
            payload = json.loads(runtime_secret.read_text(encoding="utf-8"))

        self.assertEqual(payload.get("api_id"), "12345678")
        self.assertEqual(payload.get("api_hash"), "a" * 32)
        self.assertEqual(payload["session"], "existing-session")
        self.assertEqual(payload["phone"], "+8613800000000")

    def test_seed_runtime_app_merges_tg_auth_seed_into_existing_runtime_auth(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            bundle_root = td_path / "bundle"
            runtime_app_dir = td_path / "runtime" / "app"
            (bundle_root / "app" / "webui").mkdir(parents=True, exist_ok=True)
            (bundle_root / "app" / "data").mkdir(parents=True, exist_ok=True)
            (bundle_root / "app" / "config.example.json").write_text("{}", encoding="utf-8")
            (bundle_root / "app" / "README.md").write_text("readme", encoding="utf-8")
            (bundle_root / "app" / "data" / "tg_auth.json").write_text(
                '{"admins":{"ids":["7585028453"],"usernames":[]},"grants":{"ids":[],"usernames":[]}}',
                encoding="utf-8",
            )
            (runtime_app_dir / "data").mkdir(parents=True, exist_ok=True)
            (runtime_app_dir / "data" / "tg_auth.json").write_text(
                '{"admins":{"ids":["6100826428"],"usernames":[]},"grants":{"ids":[],"usernames":["abc"]}}',
                encoding="utf-8",
            )

            desktop_entry.seed_runtime_app(bundle_root, runtime_app_dir)

            tg_auth_dst = runtime_app_dir / "data" / "tg_auth.json"
            text = tg_auth_dst.read_text(encoding="utf-8")
            self.assertIn("7585028453", text)
            self.assertIn("6100826428", text)
            self.assertIn("abc", text)

    def test_seed_runtime_app_migrates_legacy_remote_signal_config_when_runtime_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            bundle_root = td_path / "bundle"
            runtime_app_dir = td_path / "runtime" / "app"
            (bundle_root / "app" / "webui").mkdir(parents=True, exist_ok=True)
            (bundle_root / "app" / "data").mkdir(parents=True, exist_ok=True)
            (bundle_root / "app" / "config.example.json").write_text("{}", encoding="utf-8")
            (bundle_root / "app" / "README.md").write_text("readme", encoding="utf-8")
            (bundle_root / "app" / "config.json").write_text(
                '{"signal_pull_enabled": true, "signal_pull_url": "http://example.com/signals/json", "signal_pull_live_only": true, "signal_webhook_enabled": true, "signal_webhook_url": "http://example.com/signals"}',
                encoding="utf-8",
            )
            runtime_app_dir.mkdir(parents=True, exist_ok=True)
            (runtime_app_dir / "config.json").write_text(
                '{"signal_pull_enabled": false, "signal_pull_url": "", "targets": [], "telegram_bot_token": ""}',
                encoding="utf-8",
            )

            desktop_entry.seed_runtime_app(bundle_root, runtime_app_dir)

            cfg = (runtime_app_dir / "config.json").read_text(encoding="utf-8")
            self.assertIn('"signal_pull_enabled": true', cfg)
            self.assertIn('http://example.com/signals/json', cfg)

    def test_seed_runtime_app_copies_signal_puller_state_seed_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            bundle_root = td_path / "bundle"
            runtime_app_dir = td_path / "runtime" / "app"
            (bundle_root / "app" / "webui").mkdir(parents=True, exist_ok=True)
            (bundle_root / "app" / "runtime_seeds").mkdir(parents=True, exist_ok=True)
            (bundle_root / "app" / "config.example.json").write_text("{}", encoding="utf-8")
            (bundle_root / "app" / "README.md").write_text("readme", encoding="utf-8")
            (bundle_root / "app" / "runtime_seeds" / "ook_signal_puller_state.json").write_text(
                '{"version":1,"last_signal_id":0,"seen_keys":[]}',
                encoding="utf-8",
            )

            desktop_entry.seed_runtime_app(bundle_root, runtime_app_dir)

            state_dst = runtime_app_dir / ".run" / "ook_signal_puller_state.json"
            self.assertTrue(state_dst.is_file())
            import json

            obj = json.loads(state_dst.read_text(encoding="utf-8"))
            self.assertEqual(obj.get("last_signal_id"), 0)


if __name__ == "__main__":
    unittest.main()
