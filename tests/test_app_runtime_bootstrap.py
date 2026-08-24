import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "app"

if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


class TestAppRuntimeBootstrap(unittest.TestCase):
    def test_bootstrap_adds_src_and_vendored_site_packages(self) -> None:
        from runtime_bootstrap import bootstrap_app_runtime

        with tempfile.TemporaryDirectory() as td:
            repo_root = Path(td)
            app_dir = repo_root / "app"
            src_dir = repo_root / "src"
            venv_site = app_dir / ".venv" / "lib" / "python3.9" / "site-packages"
            portable_site = repo_root / "python" / "Lib" / "site-packages"
            wrapper = app_dir / "webui_server.py"

            src_dir.mkdir(parents=True, exist_ok=True)
            venv_site.mkdir(parents=True, exist_ok=True)
            portable_site.mkdir(parents=True, exist_ok=True)
            wrapper.parent.mkdir(parents=True, exist_ok=True)
            wrapper.write_text("# stub\n", encoding="utf-8")

            original_path = list(sys.path)
            seen_path = []
            seen_app_root = ""
            try:
                with mock.patch.dict(os.environ, {}, clear=False):
                    os.environ.pop("HDZZ_APP_DIR", None)
                    sys.path[:] = [p for p in sys.path if str(repo_root) not in str(p)]
                    app_root = bootstrap_app_runtime(wrapper)
                    seen_path = list(sys.path)
                    seen_app_root = str(os.environ.get("HDZZ_APP_DIR") or "")
            finally:
                sys.path[:] = original_path

            self.assertEqual(app_root, app_dir.resolve())
            self.assertEqual(seen_app_root, str(app_dir.resolve()))
            self.assertIn(str(src_dir.resolve()), seen_path)
            self.assertIn(str(venv_site.resolve()), seen_path)
            self.assertIn(str(portable_site.resolve()), seen_path)

    def test_wrapper_scripts_use_shared_runtime_bootstrap(self) -> None:
        for name in (
            "webui_server.py",
            "member_arsenal_watch.py",
            "follow_engine.py",
            "ag_wmc_watch.py",
            "settlement_importer.py",
            "telegram_bot.py",
            "telegram_signal_watch.py",
        ):
            text = (APP_DIR / name).read_text(encoding="utf-8")
            self.assertIn("from runtime_bootstrap import bootstrap_app_runtime", text, msg=name)
            self.assertIn("bootstrap_app_runtime(Path(__file__))", text, msg=name)


if __name__ == "__main__":
    unittest.main()
