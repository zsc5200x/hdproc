import importlib.util
import json
import sys
import unittest
from pathlib import Path


class TestSignalForwardingRemoved(unittest.TestCase):
    def test_app_workers_include_signal_forwarder(self) -> None:
        app_dir = Path(__file__).resolve().parents[1] / "app"
        app_path = app_dir / "app.py"
        if str(app_dir) not in sys.path:
            sys.path.insert(0, str(app_dir))
        spec = importlib.util.spec_from_file_location("hdzz_app_main_signal_test", app_path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        ws = mod.workers("127.0.0.1", 9999, 10, 5)
        names = [w.name for w in ws]
        self.assertIn("ook_signal_forwarder", names)

    def test_config_example_includes_signal_forwarding_settings(self) -> None:
        cfg_path = Path(__file__).resolve().parents[1] / "app" / "config.example.json"
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        for key in (
            "signal_webhook_enabled",
            "signal_webhook_url",
            "signal_webhook_token",
            "signal_webhook_timeout_seconds",
            "signal_webhook_retry_max",
            "signal_webhook_retry_backoff_seconds",
            "signal_webhook_interval_seconds",
            "signal_webhook_push_history_on_start",
            "signal_webhook_debug",
        ):
            self.assertIn(key, cfg)


if __name__ == "__main__":
    unittest.main()
