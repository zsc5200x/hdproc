from __future__ import annotations

from typing import Any, Dict


SIGNAL_PULL_CONFIG_DEFAULTS: Dict[str, Any] = {
    "signal_pull_enabled": False,
    "signal_pull_url": "",
    "signal_pull_token": "",
    "signal_pull_timeout_seconds": 8,
    "signal_pull_interval_seconds": 1.0,
    "signal_pull_limit": 200,
    "signal_pull_push_history_on_start": False,
    "signal_pull_live_only": True,
    "signal_pull_debug": False,
}


def ensure_signal_pull_config_defaults(doc: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(doc, dict):
        out: Dict[str, Any] = dict(SIGNAL_PULL_CONFIG_DEFAULTS)
    else:
        out = dict(doc)
        for key, value in SIGNAL_PULL_CONFIG_DEFAULTS.items():
            out.setdefault(key, value)

    out["signal_pull_enabled"] = bool(out.get("signal_pull_enabled"))
    out["signal_pull_url"] = str(out.get("signal_pull_url") or "").strip()
    out["signal_pull_token"] = str(out.get("signal_pull_token") or "").strip()
    out["signal_pull_push_history_on_start"] = bool(out.get("signal_pull_push_history_on_start"))
    out["signal_pull_live_only"] = bool(out.get("signal_pull_live_only") if "signal_pull_live_only" in out else True)
    out["signal_pull_debug"] = bool(out.get("signal_pull_debug"))

    try:
        out["signal_pull_timeout_seconds"] = max(1, min(120, int(out.get("signal_pull_timeout_seconds") or 8)))
    except Exception:
        out["signal_pull_timeout_seconds"] = int(SIGNAL_PULL_CONFIG_DEFAULTS["signal_pull_timeout_seconds"])

    try:
        out["signal_pull_interval_seconds"] = max(0.2, min(3600.0, float(out.get("signal_pull_interval_seconds") or 1.0)))
    except Exception:
        out["signal_pull_interval_seconds"] = float(SIGNAL_PULL_CONFIG_DEFAULTS["signal_pull_interval_seconds"])

    try:
        out["signal_pull_limit"] = max(1, min(500, int(out.get("signal_pull_limit") or 200)))
    except Exception:
        out["signal_pull_limit"] = int(SIGNAL_PULL_CONFIG_DEFAULTS["signal_pull_limit"])

    return out


def merge_signal_pull_config(existing: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(existing, dict):
        out: Dict[str, Any] = dict(SIGNAL_PULL_CONFIG_DEFAULTS)
    else:
        out = dict(existing)

    if not isinstance(incoming, dict):
        return ensure_signal_pull_config_defaults(out)

    for key in SIGNAL_PULL_CONFIG_DEFAULTS.keys():
        if key in incoming:
            out[key] = incoming.get(key)

    return ensure_signal_pull_config_defaults(out)


def signal_pull_config_payload(doc: Dict[str, Any]) -> Dict[str, Any]:
    cfg = ensure_signal_pull_config_defaults(doc)
    token = cfg["signal_pull_token"]
    token_mask = (token[:4] + "…" + token[-4:]) if len(token) > 8 else ("已设置" if token else "")
    return {
        "ok": True,
        "config": {
            "signal_pull_enabled": cfg["signal_pull_enabled"],
            "signal_pull_url": cfg["signal_pull_url"],
            "signal_pull_token": "",
            "signal_pull_token_set": bool(token),
            "signal_pull_token_mask": token_mask,
            "signal_pull_live_only": cfg["signal_pull_live_only"],
            "signal_pull_limit": cfg["signal_pull_limit"],
            "signal_pull_interval_seconds": cfg["signal_pull_interval_seconds"],
        },
        "configured": bool(cfg["signal_pull_url"]),
    }


def reset_signal_pull_config(existing: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(existing, dict):
        out: Dict[str, Any] = {}
    else:
        out = dict(existing)

    for key, value in SIGNAL_PULL_CONFIG_DEFAULTS.items():
        out[key] = value

    return ensure_signal_pull_config_defaults(out)
