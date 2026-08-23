import importlib.util
import json
import sys
from pathlib import Path


def test_premium_bot_module_has_main() -> None:
    from hdzz.api.telegram import premium_bot

    assert callable(getattr(premium_bot, "main", None))


def test_app_workers_exclude_premium_bot() -> None:
    app_dir = Path(__file__).resolve().parents[1] / "app"
    app_path = app_dir / "app.py"
    if str(app_dir) not in sys.path:
        sys.path.insert(0, str(app_dir))
    spec = importlib.util.spec_from_file_location("hdzz_app_main", app_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    ws = mod.workers("127.0.0.1", 8080, 10, 5)
    names = [w.name for w in ws]
    assert "telegram_premium_bot" not in names
    assert "ook_signal_forwarder" in names


def test_config_example_includes_signal_forwarding_settings() -> None:
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
        assert key in cfg
