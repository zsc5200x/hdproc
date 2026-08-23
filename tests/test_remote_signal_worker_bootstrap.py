import importlib.util
import os
from pathlib import Path
import sys
import unittest
from unittest import mock


class TestRemoteSignalWorkerBootstrap(unittest.TestCase):
    def test_workers_include_signal_puller(self) -> None:
        root = Path(__file__).resolve().parents[1]
        app_dir = root / "app"
        app_path = app_dir / "app.py"
        if str(app_dir) not in sys.path:
            sys.path.insert(0, str(app_dir))
        spec = importlib.util.spec_from_file_location("hdzz_app_main_remote_signal", app_path)
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(mod)

        workers = mod.workers("127.0.0.1", 9999, 10, 5)
        names = [w.name for w in workers]

        self.assertIn("ook_signal_puller", names)

    def test_workers_use_source_ook_paths_even_with_runtime_root_override(self) -> None:
        root = Path(__file__).resolve().parents[1]
        app_dir = root / "app"
        app_path = app_dir / "app.py"
        if str(app_dir) not in sys.path:
            sys.path.insert(0, str(app_dir))
        spec = importlib.util.spec_from_file_location("hdzz_app_main_remote_signal_runtime", app_path)
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        with mock.patch.dict(os.environ, {"HDZZ_APP_DIR": "/tmp/runtime-app"}, clear=False):
            spec.loader.exec_module(mod)

        workers = mod.workers("127.0.0.1", 9999, 10, 5)
        puller = next(w for w in workers if w.name == "ook_signal_puller")
        self.assertEqual(
            puller.args[1],
            str((root / "ook" / "signal_puller.py").resolve()),
        )

    def test_core_workers_use_source_ook_paths_even_with_runtime_root_override(self) -> None:
        root = Path(__file__).resolve().parents[1]
        app_dir = root / "app"
        app_path = app_dir / "app.py"
        if str(app_dir) not in sys.path:
            sys.path.insert(0, str(app_dir))
        spec = importlib.util.spec_from_file_location("hdzz_app_main_remote_signal_core_runtime", app_path)
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        with mock.patch.dict(os.environ, {"HDZZ_APP_DIR": "/tmp/runtime-app"}, clear=False):
            spec.loader.exec_module(mod)

        workers = mod.core_workers(10, 5)
        puller = next(w for w in workers if w.name == "ook_signal_puller")
        self.assertEqual(
            puller.args[1],
            str((root / "ook" / "signal_puller.py").resolve()),
        )


if __name__ == "__main__":
    unittest.main()
