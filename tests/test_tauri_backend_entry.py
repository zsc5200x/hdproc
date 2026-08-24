from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "app"
TOOLS_DIR = APP_DIR / "tools"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import tauri_backend_entry as backend  # noqa: E402

import build_tauri_backend_poc as builder  # noqa: E402


class TauriBackendEntryTest(unittest.TestCase):
    def test_runtime_root_uses_product_application_support_directory(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("HDZZ_RUNTIME_ROOT", None)
            self.assertEqual(
                backend.resolve_runtime_root(Path("/tmp/home")),
                Path("/tmp/home/Library/Application Support/HD跟单系统").resolve(),
            )

    def test_seed_creates_safe_runtime_without_copying_production_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "runtime"
            bundle = Path(temp) / "bundle"
            (bundle / "app" / "webui").mkdir(parents=True)
            (bundle / "app" / "webui" / "index.html").write_text("ok", encoding="utf-8")
            (bundle / "app" / "tauri_config.example.json").write_text('{"targets": []}', encoding="utf-8")
            backend.seed_runtime(root, bundle)
            self.assertTrue((root / "webui" / "index.html").is_file())
            self.assertEqual(json.loads((root / "config.json").read_text()), {"targets": []})
            self.assertTrue((root / "data").is_dir())
            self.assertTrue((root / "orders").is_dir())
            self.assertTrue((root / "state").is_dir())
            self.assertTrue((root / ".run").is_dir())

    def test_existing_config_is_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "runtime"
            bundle = Path(temp) / "bundle"
            (bundle / "app").mkdir(parents=True)
            (bundle / "app" / "tauri_config.example.json").write_text('{"targets": []}', encoding="utf-8")
            root.mkdir(parents=True)
            (root / "config.json").write_text('{"targets": [{"name": "existing"}]}', encoding="utf-8")
            backend.seed_runtime(root, bundle)
            self.assertEqual(json.loads((root / "config.json").read_text())["targets"][0]["name"], "existing")

    def test_config_status_marks_empty_targets_as_unconfigured(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "runtime"
            root.mkdir(parents=True)
            (root / "config.json").write_text('{"targets": []}', encoding="utf-8")
            status = backend.configuration_summary(root)
            self.assertEqual(status["valid"], True)
            self.assertEqual(status["configured"], False)
            self.assertEqual(status["target_count"], 0)
            self.assertIn("尚未配置", status["message"])

    def test_import_config_validates_backups_and_preserves_existing_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "runtime"
            source = Path(temp) / "import.json"
            root.mkdir(parents=True)
            (root / "config.json").write_text('{"targets": [{"name": "old"}]}', encoding="utf-8")
            source.write_text('{"targets": [{"name": "new"}]}', encoding="utf-8")
            target = backend.import_config(source, root)
            self.assertEqual(target, (root / "config.json").resolve())
            self.assertEqual(json.loads(target.read_text())["targets"][0]["name"], "new")
            backups = list((root / ".run" / "config-backups").glob("config-*.json"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(json.loads(backups[0].read_text())["targets"][0]["name"], "old")

    def test_import_config_rejects_invalid_json_without_replacing_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "runtime"
            source = Path(temp) / "import.json"
            root.mkdir(parents=True)
            target = root / "config.json"
            target.write_text('{"targets": [{"name": "old"}]}', encoding="utf-8")
            source.write_text("not-json", encoding="utf-8")
            with self.assertRaises(ValueError):
                backend.import_config(source, root)
            self.assertIn("old", target.read_text())

    def test_backend_ownership_is_single_instance_and_recoverable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "runtime"
            root.mkdir(parents=True)
            self.assertTrue(backend.acquire_ownership(root))
            try:
                owner = backend.read_ownership(root)
                self.assertEqual(owner["pid"], os.getpid())
                self.assertEqual(owner["backend_path"], str(Path(sys.executable).resolve()))
                self.assertFalse(backend.acquire_ownership(root))
            finally:
                backend.release_ownership(root)
            self.assertFalse((root / ".run" / "backend-ownership.json").exists())

    def test_headless_entry_has_no_gui_dependency(self) -> None:
        text = (APP_DIR / "tauri_backend_entry.py").read_text(encoding="utf-8")
        self.assertNotIn("PySide6", text)
        self.assertNotIn("QApplication", text)

    def test_sidecar_spec_excludes_sensitive_runtime_config(self) -> None:
        text = (APP_DIR / "tools" / "hdzz_tauri_backend.spec").read_text(encoding="utf-8")
        self.assertIn("tauri_config.example.json", text)
        self.assertNotIn("APP_DIR / \"config.json\"", text)
        self.assertNotIn("tg_auth.json", text)
        self.assertNotIn("telegram_signal_credentials", text)

    def test_tauri_release_layer_uses_single_instance_and_product_identity(self) -> None:
        config = json.loads(
            (ROOT / "experiments/hd-push-tauri-preview/src-tauri/tauri.conf.json").read_text(encoding="utf-8")
        )
        self.assertEqual(config["productName"], "HD跟单系统")
        self.assertEqual(config["identifier"], "com.hdsports.hdfollow")
        self.assertEqual(config["app"]["windows"][0]["title"], "HD跟单系统")
        rust = (ROOT / "experiments/hd-push-tauri-preview/src-tauri/src/lib.rs").read_text(encoding="utf-8")
        self.assertIn("tauri_plugin_single_instance::init", rust)

    def test_tauri_resources_keep_pyinstaller_onedir_layout(self) -> None:
        config = json.loads(
            (ROOT / "experiments/hd-push-tauri-preview/src-tauri/tauri.conf.json").read_text(encoding="utf-8")
        )
        resources = config["bundle"]["resources"]
        self.assertEqual(resources["resources/backend/hdzz-backend"], "backend/hdzz-backend")
        self.assertNotIn("resources/backend/**/*", resources)

    def test_launcher_has_reuse_and_rotating_diagnostic_contract(self) -> None:
        backend_rs = (ROOT / "experiments/hd-push-tauri-preview/src-tauri/src/backend.rs").read_text(encoding="utf-8")
        self.assertIn("health::webui_ready()", backend_rs)
        self.assertIn("DIAGNOSTIC_MAX_BYTES", backend_rs)
        self.assertIn("config-backups", backend_rs)

    def test_builder_extracts_chromium_revision_from_path(self) -> None:
        path = Path("/tmp/ms-playwright/chromium-1208/chrome-mac-arm64/Google Chrome for Testing.app")
        self.assertEqual(builder._chromium_revision_from_path(path), "1208")


if __name__ == "__main__":
    unittest.main()
