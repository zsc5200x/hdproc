import ast
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = ROOT / "app" / "tools"

if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))


class TestMacStandaloneBundleBuilder(unittest.TestCase):
    def test_actual_pyinstaller_spec_includes_every_standalone_worker(self) -> None:
        def assignment_value(path: Path, name: str):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in tree.body:
                if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                    continue
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if any(isinstance(target, ast.Name) and target.id == name for target in targets):
                    return node.value
            self.fail(f"{path} missing assignment: {name}")

        worker_node = assignment_value(ROOT / "app" / "mac_standalone_entry.py", "WORKER_MODULES")
        worker_modules = set(ast.literal_eval(worker_node).values())

        hiddenimports_node = assignment_value(TOOLS_DIR / "hdzz_mac_standalone.spec", "hiddenimports")
        packed_modules = {
            node.value
            for node in ast.walk(hiddenimports_node)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }

        self.assertEqual(worker_modules - packed_modules, set())

    def test_prepare_telegram_credentials_seed_strips_account_session(self) -> None:
        import build_mac_standalone_app as builder

        self.assertTrue(hasattr(builder, "prepare_telegram_credentials_seed"))
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "telegram_signal_secrets.json"
            source.write_text(
                json.dumps(
                    {
                        "api_id": "12345678",
                        "api_hash": "a" * 32,
                        "session": "private-session",
                        "phone": "+8613800000000",
                    }
                ),
                encoding="utf-8",
            )

            seed = builder.prepare_telegram_credentials_seed(root / "build", source)
            payload = json.loads(seed.read_text(encoding="utf-8"))

        self.assertEqual(payload, {"api_id": "12345678", "api_hash": "a" * 32})

    def test_runtime_requirements_include_telegram_client(self) -> None:
        requirements = (ROOT / "app" / "requirements.txt").read_text(encoding="utf-8").lower()

        self.assertIn("telethon", requirements)

    def _write_macos_chromium_marker(self, root: Path, revision: str = "1208") -> None:
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

    def _write_windows_chromium_marker(self, root: Path) -> None:
        exe = root / "chromium-1200" / "chrome-win64" / "chrome.exe"
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.write_text("windows chromium", encoding="utf-8")

    def test_build_bundle_info_plist_includes_runtime_metadata(self) -> None:
        from build_mac_standalone_app import build_bundle_info_plist

        info = build_bundle_info_plist(
            bundle_name="HDDD系统",
            runtime_app_name="HDDD系统",
            default_port=10299,
            splash_asset_rel_path="bundle_assets/splash.icns",
        )

        self.assertEqual(info["CFBundleDisplayName"], "HDDD系统")
        self.assertEqual(info["CFBundleName"], "HDDD系统")
        self.assertEqual(info["HDZZRuntimeAppName"], "HDDD系统")
        self.assertEqual(info["HDZZDefaultPort"], 10299)
        self.assertEqual(info["HDZZSplashAssetRelPath"], "bundle_assets/splash.icns")
        self.assertTrue(info["NSHighResolutionCapable"])

    def test_detect_playwright_browsers_dir_prefers_repo_level_bundle(self) -> None:
        from build_mac_standalone_app import detect_playwright_browsers_dir

        with tempfile.TemporaryDirectory() as td:
            source_root = Path(td)
            self._write_macos_chromium_marker(source_root / "ms-playwright")
            self._write_macos_chromium_marker(source_root / "app" / ".run" / "ms-playwright")

            detected = detect_playwright_browsers_dir(source_root, env={})

            self.assertEqual(detected, (source_root / "ms-playwright").resolve())

    def test_detect_playwright_browsers_dir_skips_windows_bundle_and_uses_runtime_copy(self) -> None:
        from build_mac_standalone_app import detect_playwright_browsers_dir

        with tempfile.TemporaryDirectory() as td:
            source_root = Path(td)
            self._write_windows_chromium_marker(source_root / "ms-playwright")
            runtime_browsers = source_root / "app" / ".run" / "ms-playwright"
            self._write_macos_chromium_marker(runtime_browsers)

            detected = detect_playwright_browsers_dir(source_root, env={})

            self.assertEqual(detected, runtime_browsers.resolve())

    def test_detect_playwright_browsers_dir_skips_mismatched_revision(self) -> None:
        from build_mac_standalone_app import detect_playwright_browsers_dir

        with tempfile.TemporaryDirectory() as td:
            source_root = Path(td)
            self._write_macos_chromium_marker(source_root / "app" / ".run" / "ms-playwright", revision="1217")
            matching = source_root / "matching-ms-playwright"
            self._write_macos_chromium_marker(matching, revision="1208")

            detected = detect_playwright_browsers_dir(
                source_root,
                env={"PLAYWRIGHT_BROWSERS_PATH": str(matching)},
                expected_revision="1208",
            )

            self.assertEqual(detected, matching.resolve())

    def test_stage_playwright_cache_keeps_only_required_revision_and_runtime_roots(self) -> None:
        from build_mac_standalone_app import stage_macos_playwright_browsers, validate_macos_playwright_staging

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "full-cache"
            target = root / "staging"
            self._write_macos_chromium_marker(source, revision="1217")
            self._write_macos_chromium_marker(source, revision="1234")
            headless = source / "chromium_headless_shell-1217" / "chrome-headless-shell"
            headless.parent.mkdir(parents=True, exist_ok=True)
            headless.write_text("headless", encoding="utf-8")
            (source / "ffmpeg-1011" / "ffmpeg").parent.mkdir(parents=True, exist_ok=True)
            (source / "ffmpeg-1011" / "ffmpeg").write_text("ffmpeg", encoding="utf-8")
            profile = source / "mcp-chrome-secret" / "Default"
            profile.mkdir(parents=True, exist_ok=True)
            (profile / "Cookies").write_text("profile", encoding="utf-8")

            staged = stage_macos_playwright_browsers(source, target, expected_revision="1217")
            stats = validate_macos_playwright_staging(staged, expected_revision="1217")

            self.assertEqual(stats["revision"], "1217")
            self.assertTrue((staged / "chromium-1217").is_dir())
            self.assertTrue((staged / "chromium_headless_shell-1217").is_dir())
            self.assertTrue((staged / "ffmpeg-1011").is_dir())
            self.assertFalse((staged / "chromium-1234").exists())
            self.assertFalse((staged / "mcp-chrome-secret").exists())

    def test_validate_playwright_staging_rejects_multiple_revisions_and_profiles(self) -> None:
        from build_mac_standalone_app import validate_macos_playwright_staging

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write_macos_chromium_marker(root, revision="1217")
            self._write_macos_chromium_marker(root, revision="1234")
            with self.assertRaises(ValueError):
                validate_macos_playwright_staging(root)

            single = root / "single"
            self._write_macos_chromium_marker(single, revision="1217")
            (single / "mcp-chrome-profile" / "Default").mkdir(parents=True, exist_ok=True)
            with self.assertRaises(ValueError):
                validate_macos_playwright_staging(single, expected_revision="1217")

    def test_build_spec_inputs_includes_required_resources_and_workers(self) -> None:
        from build_mac_standalone_app import build_spec_inputs

        with tempfile.TemporaryDirectory() as td:
            source_root = Path(td)
            browsers_dir = source_root / "ms-playwright"
            (source_root / "app" / "webui").mkdir(parents=True, exist_ok=True)
            (source_root / "app" / "assets" / "icon").mkdir(parents=True, exist_ok=True)
            (source_root / "src" / "hdzz").mkdir(parents=True, exist_ok=True)
            (source_root / "ook").mkdir(parents=True, exist_ok=True)
            browsers_dir.mkdir(parents=True, exist_ok=True)
            (source_root / "app" / "assets" / "icon" / "AppIcon.icns").write_text("icon", encoding="utf-8")
            (source_root / "gdcg.jpg").write_text("preview", encoding="utf-8")

            spec_inputs = build_spec_inputs(source_root, browsers_dir, bundle_name="AG Matrix Standalone")

            self.assertIn(str(source_root / "app"), spec_inputs["pathex"])
            self.assertIn(str(source_root / "src"), spec_inputs["pathex"])
            self.assertIn(str(source_root / "ook"), spec_inputs["pathex"])
            self.assertIn((str(source_root / "app" / "webui"), "app/webui"), spec_inputs["datas"])
            self.assertIn((str(browsers_dir), "ms-playwright"), spec_inputs["datas"])
            self.assertIn((str(source_root / "gdcg.jpg"), "."), spec_inputs["datas"])
            self.assertIn("ag_wmc_watch", spec_inputs["hiddenimports"])
            self.assertIn("telegram_signal_watch", spec_inputs["hiddenimports"])
            self.assertIn("signal_forwarder", spec_inputs["hiddenimports"])
            self.assertIn("playwright", spec_inputs["collect_submodules"])
            self.assertIn("PySide6.QtWebEngineWidgets", spec_inputs["hiddenimports"])
            self.assertIn("PySide6.QtWebEngineCore", spec_inputs["hiddenimports"])
            self.assertIn("PySide6", spec_inputs["build_requirements"])
            self.assertEqual(
                spec_inputs["icon_path"],
                str((source_root / "app" / "assets" / "icon" / "AppIcon.icns").resolve()),
            )

    def test_build_spec_inputs_can_package_playwright_browser_archive(self) -> None:
        from build_mac_standalone_app import build_spec_inputs

        with tempfile.TemporaryDirectory() as td:
            source_root = Path(td)
            browsers_dir = source_root / "ms-playwright"
            archive_path = source_root / "bundle-assets" / "ms-playwright.tar.gz"
            (source_root / "app" / "webui").mkdir(parents=True, exist_ok=True)
            (source_root / "app" / "assets" / "icon").mkdir(parents=True, exist_ok=True)
            (source_root / "src" / "hdzz").mkdir(parents=True, exist_ok=True)
            (source_root / "ook").mkdir(parents=True, exist_ok=True)
            browsers_dir.mkdir(parents=True, exist_ok=True)
            archive_path.parent.mkdir(parents=True, exist_ok=True)
            archive_path.write_text("archive", encoding="utf-8")
            (source_root / "app" / "assets" / "icon" / "AppIcon.icns").write_text("icon", encoding="utf-8")

            spec_inputs = build_spec_inputs(
                source_root,
                browsers_dir,
                bundle_name="AG Matrix Standalone",
                browsers_archive_path=archive_path,
            )

            self.assertIn((str(archive_path.resolve()), "."), spec_inputs["datas"])
            self.assertNotIn((str(browsers_dir), "ms-playwright"), spec_inputs["datas"])

    def test_build_spec_inputs_can_override_icon_path(self) -> None:
        from build_mac_standalone_app import build_spec_inputs

        with tempfile.TemporaryDirectory() as td:
            source_root = Path(td)
            browsers_dir = source_root / "ms-playwright"
            icon_path = source_root / "custom" / "HD-Crown.icns"
            (source_root / "app" / "webui").mkdir(parents=True, exist_ok=True)
            (source_root / "app" / "assets" / "icon").mkdir(parents=True, exist_ok=True)
            (source_root / "src" / "hdzz").mkdir(parents=True, exist_ok=True)
            (source_root / "ook").mkdir(parents=True, exist_ok=True)
            browsers_dir.mkdir(parents=True, exist_ok=True)
            icon_path.parent.mkdir(parents=True, exist_ok=True)
            icon_path.write_text("icon", encoding="utf-8")

            spec_inputs = build_spec_inputs(
                source_root,
                browsers_dir,
                bundle_name="HD Crown Sports系统",
                icon_path=icon_path,
            )

            self.assertEqual(spec_inputs["icon_path"], str(icon_path.resolve()))

    def test_build_spec_inputs_can_include_custom_splash_asset(self) -> None:
        from build_mac_standalone_app import build_spec_inputs

        with tempfile.TemporaryDirectory() as td:
            source_root = Path(td)
            browsers_dir = source_root / "ms-playwright"
            splash_path = source_root / "custom" / "snake.jpg"
            (source_root / "app" / "webui").mkdir(parents=True, exist_ok=True)
            (source_root / "app" / "assets" / "icon").mkdir(parents=True, exist_ok=True)
            (source_root / "src" / "hdzz").mkdir(parents=True, exist_ok=True)
            (source_root / "ook").mkdir(parents=True, exist_ok=True)
            browsers_dir.mkdir(parents=True, exist_ok=True)
            (source_root / "app" / "assets" / "icon" / "AppIcon.icns").write_text("icon", encoding="utf-8")
            splash_path.parent.mkdir(parents=True, exist_ok=True)
            splash_path.write_text("splash", encoding="utf-8")

            spec_inputs = build_spec_inputs(
                source_root,
                browsers_dir,
                bundle_name="HD Sports后台系统",
                splash_asset_path=splash_path,
            )

            self.assertIn((str(splash_path.resolve()), "bundle_assets"), spec_inputs["datas"])

    def test_build_spec_inputs_includes_tg_auth_seed_when_present(self) -> None:
        from build_mac_standalone_app import build_spec_inputs

        with tempfile.TemporaryDirectory() as td:
            source_root = Path(td)
            browsers_dir = source_root / "ms-playwright"
            tg_auth = source_root / "app" / "data" / "tg_auth.json"
            (source_root / "app" / "webui").mkdir(parents=True, exist_ok=True)
            (source_root / "app" / "assets" / "icon").mkdir(parents=True, exist_ok=True)
            (source_root / "src" / "hdzz").mkdir(parents=True, exist_ok=True)
            (source_root / "ook").mkdir(parents=True, exist_ok=True)
            browsers_dir.mkdir(parents=True, exist_ok=True)
            tg_auth.parent.mkdir(parents=True, exist_ok=True)
            (source_root / "app" / "assets" / "icon" / "AppIcon.icns").write_text("icon", encoding="utf-8")
            tg_auth.write_text('{"admins":{"ids":["7585028453"]}}', encoding="utf-8")

            spec_inputs = build_spec_inputs(source_root, browsers_dir, bundle_name="HD Sports后台系统")

            normalized = {(str(Path(src).resolve()), dst) for src, dst in spec_inputs["datas"]}
            self.assertIn((str(tg_auth.resolve()), "app/data"), normalized)

    def test_build_spec_inputs_includes_signal_puller_state_seed_when_present(self) -> None:
        from build_mac_standalone_app import build_spec_inputs

        with tempfile.TemporaryDirectory() as td:
            source_root = Path(td)
            browsers_dir = source_root / "ms-playwright"
            state_seed = source_root / "app" / "runtime_seeds" / "ook_signal_puller_state.json"
            (source_root / "app" / "webui").mkdir(parents=True, exist_ok=True)
            (source_root / "app" / "assets" / "icon").mkdir(parents=True, exist_ok=True)
            (source_root / "src" / "hdzz").mkdir(parents=True, exist_ok=True)
            (source_root / "ook").mkdir(parents=True, exist_ok=True)
            browsers_dir.mkdir(parents=True, exist_ok=True)
            state_seed.parent.mkdir(parents=True, exist_ok=True)
            (source_root / "app" / "assets" / "icon" / "AppIcon.icns").write_text("icon", encoding="utf-8")
            state_seed.write_text('{"version":1,"last_signal_id":0,"seen_keys":[]}', encoding="utf-8")

            spec_inputs = build_spec_inputs(source_root, browsers_dir, bundle_name="AG Matrix Standalone")

            normalized = {(str(Path(src).resolve()), dst) for src, dst in spec_inputs["datas"]}
            self.assertIn((str(state_seed.resolve()), "app/runtime_seeds"), normalized)


if __name__ == "__main__":
    unittest.main()
