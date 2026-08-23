import plistlib
import stat
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_TOOLS = ROOT / "app" / "tools"

if str(APP_TOOLS) not in sys.path:
    sys.path.insert(0, str(APP_TOOLS))


class TestMacAppBundleBuilder(unittest.TestCase):
    def _make_minimal_source_tree(self, root: Path) -> None:
        (root / "app").mkdir(parents=True, exist_ok=True)
        (root / "app" / "assets" / "icon").mkdir(parents=True, exist_ok=True)
        (root / "scripts").mkdir(parents=True, exist_ok=True)
        (root / "src" / "hdzz").mkdir(parents=True, exist_ok=True)
        (root / "ook").mkdir(parents=True, exist_ok=True)

        (root / "app" / "app.py").write_text("print('hello')\n", encoding="utf-8")
        (root / "app" / "requirements.txt").write_text("requests\n", encoding="utf-8")
        (root / "app" / "assets" / "icon" / "AppIcon.icns").write_text("icon", encoding="utf-8")
        (root / "scripts" / "Start_AG_Matrix_ALL.command").write_text(
            "#!/usr/bin/env bash\nexit 0\n", encoding="utf-8"
        )
        (root / "scripts" / "Stop_AG_Matrix.command").write_text(
            "#!/usr/bin/env bash\nexit 0\n", encoding="utf-8"
        )
        (root / "src" / "hdzz" / "__init__.py").write_text("", encoding="utf-8")
        (root / "ook" / "signal_forwarder.py").write_text("print('ok')\n", encoding="utf-8")

    def test_build_mac_app_bundle_creates_expected_structure(self) -> None:
        from build_mac_app_bundle import build_mac_app_bundle

        with tempfile.TemporaryDirectory() as source_td, tempfile.TemporaryDirectory() as out_td:
            source_root = Path(source_td)
            out_root = Path(out_td)
            self._make_minimal_source_tree(source_root)

            bundle_path = build_mac_app_bundle(source_root, out_root, bundle_name="AG Matrix")

            self.assertEqual(bundle_path, (out_root / "AG Matrix.app").resolve())
            self.assertTrue((bundle_path / "Contents" / "Info.plist").is_file())
            self.assertTrue((bundle_path / "Contents" / "MacOS" / "AG Matrix").is_file())
            self.assertTrue((bundle_path / "Contents" / "Resources" / "AppIcon.icns").is_file())
            self.assertTrue(
                (bundle_path / "Contents" / "Resources" / "app_payload" / "app" / "app.py").is_file()
            )
            self.assertTrue(
                (
                    bundle_path
                    / "Contents"
                    / "Resources"
                    / "app_payload"
                    / "scripts"
                    / "Start_AG_Matrix_ALL.command"
                ).is_file()
            )

            info = plistlib.loads((bundle_path / "Contents" / "Info.plist").read_bytes())
            self.assertEqual(info["CFBundlePackageType"], "APPL")
            self.assertEqual(info["CFBundleExecutable"], "AG Matrix")
            self.assertEqual(info["CFBundleName"], "AG Matrix")
            self.assertEqual(info["CFBundleIconFile"], "AppIcon.icns")

            launcher_mode = stat.S_IMODE((bundle_path / "Contents" / "MacOS" / "AG Matrix").stat().st_mode)
            self.assertTrue(bool(launcher_mode & stat.S_IXUSR))

    def test_build_mac_app_bundle_excludes_runtime_artifacts(self) -> None:
        from build_mac_app_bundle import build_mac_app_bundle

        with tempfile.TemporaryDirectory() as source_td, tempfile.TemporaryDirectory() as out_td:
            source_root = Path(source_td)
            out_root = Path(out_td)
            self._make_minimal_source_tree(source_root)

            (source_root / ".git").mkdir()
            (source_root / ".git" / "HEAD").write_text("ref: main\n", encoding="utf-8")
            (source_root / "_dist").mkdir()
            (source_root / "_dist" / "old.zip").write_text("zip", encoding="utf-8")
            (source_root / "cc" / "node_modules").mkdir(parents=True)
            (source_root / "cc" / "node_modules" / "left-pad.js").write_text("x", encoding="utf-8")
            (source_root / "app" / ".venv" / "bin").mkdir(parents=True)
            (source_root / "app" / ".venv" / "bin" / "python").write_text("py", encoding="utf-8")
            (source_root / "app" / ".run" / "logs").mkdir(parents=True)
            (source_root / "app" / ".run" / "logs" / "worker.err.log").write_text("log", encoding="utf-8")

            bundle_path = build_mac_app_bundle(source_root, out_root, bundle_name="AG Matrix")
            payload_root = bundle_path / "Contents" / "Resources" / "app_payload"

            self.assertFalse((payload_root / ".git").exists())
            self.assertFalse((payload_root / "_dist").exists())
            self.assertFalse((payload_root / "cc" / "node_modules").exists())
            self.assertFalse((payload_root / "app" / ".venv").exists())
            self.assertFalse((payload_root / "app" / ".run").exists())

    def test_launcher_uses_embedded_payload_start_script(self) -> None:
        from build_mac_app_bundle import build_mac_app_bundle

        with tempfile.TemporaryDirectory() as source_td, tempfile.TemporaryDirectory() as out_td:
            source_root = Path(source_td)
            out_root = Path(out_td)
            self._make_minimal_source_tree(source_root)

            bundle_path = build_mac_app_bundle(source_root, out_root, bundle_name="AG Matrix")
            launcher = (bundle_path / "Contents" / "MacOS" / "AG Matrix").read_text(encoding="utf-8")

            self.assertIn("Resources/app_payload", launcher)
            self.assertIn("scripts/Start_AG_Matrix_ALL.command", launcher)
            self.assertIn("exec bash", launcher)


if __name__ == "__main__":
    unittest.main()
