from __future__ import annotations

import math
from typing import Any, Dict, List, Optional


MARKET_RULE_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "handicap": {"enabled": True, "ratio_percent": 100.0},
    "total_over": {"enabled": True, "ratio_percent": 100.0},
    "total_under": {"enabled": True, "ratio_percent": 100.0},
    "moneyline": {"enabled": True, "ratio_percent": 100.0},
}

REVERSE_RULE_DEFAULTS: Dict[str, bool] = {
    "all": False,
    "total": False,
    "handicap": False,
}


def _sanitize_follow_odds_limit(v: Any) -> Optional[float]:
    if v in (None, ""):
        return None
    try:
        out = float(v)
    except Exception:
        return None
    if not math.isfinite(out) or out <= 0:
        return None
    return out


def _sanitize_nonnegative_amount(v: Any) -> float:
    try:
        out = float(v or 0)
    except Exception:
        return 0.0
    if not math.isfinite(out) or out < 0:
        return 0.0
    return out


def _sanitize_market_rules(v: Any) -> Dict[str, Dict[str, Any]]:
    raw = v if isinstance(v, dict) else {}
    out: Dict[str, Dict[str, Any]] = {}
    for key, default in MARKET_RULE_DEFAULTS.items():
        item_raw = raw.get(key) if isinstance(raw.get(key), dict) else {}
        enabled = bool(item_raw.get("enabled", default["enabled"]))
        try:
            ratio = float(item_raw.get("ratio_percent", default["ratio_percent"]))
        except Exception:
            ratio = float(default["ratio_percent"])
        ratio = max(0.0, min(500.0, float(ratio)))
        out[key] = {"enabled": enabled, "ratio_percent": ratio}
    return out


def _sanitize_reverse_rules(v: Any) -> Dict[str, bool]:
    raw = v if isinstance(v, dict) else {}
    out: Dict[str, bool] = {}
    for key, default in REVERSE_RULE_DEFAULTS.items():
        out[key] = bool(raw.get(key, default))
    return out


def _sanitize_target_rules(v: Any) -> List[Dict[str, Any]]:
    raw = v if isinstance(v, list) else []
    out: List[Dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        ratio_value: Optional[float] = None
        ratio_raw = item.get("ratio_percent")
        if ratio_raw not in (None, ""):
            try:
                ratio_value = float(ratio_raw)
            except Exception:
                ratio_value = None
            if ratio_value is not None:
                ratio_value = max(0.0, min(500.0, float(ratio_value)))
        out.append(
            {
                "name": name,
                "enabled": bool(item.get("enabled", True)),
                "only_live": bool(item.get("only_live") or False),
                "ratio_percent": ratio_value,
            }
        )
    return out


FOLLOW_SETTINGS_DEFAULTS: Dict[str, Any] = {
    "enabled": False,
    "mode": "fixed",
    "fixed_amount": 50,
    "ratio_percent": 100,
    "max_amount": 0,
    "match_limit_amount": 0,
    "market_limit_amount": 0,
    "follow_min_odds": None,
    "follow_max_odds": None,
    "split_count": 1,
    "split_delay_seconds": 0,
    "targets": [],
    "members": [],
    "updated_at": "",
    "member_cooldown_1X014_seconds": 0,
    # Snapshot Direct 是唯一生产执行路径。
    "execution_mode": "snapshot_direct",
    "snapshot_direct_enabled": True,
    "snapshot_max_age_ms": 500,
    # 默认关闭；仅用于下一笔手工诊断订单的最小上下文 A/B 实验。
    "bet_context_mode": "disabled",
    "unknown_result_reconcile_max_seconds": 120,
    "unknown_result_reconcile_max_attempts": 5,
    "member_error_cooldown_seconds": 30,
    # 会员角色
    "allow_quote_members": False,
    # 盘口策略（让球/大小球分开控制禁跟与比例）
    "market_rules": _sanitize_market_rules({}),
    # 反打策略（先落配置，实际执行逻辑由引擎按需接入）
    "reverse_rules": _sanitize_reverse_rules({}),
}


def ensure_follow_settings_defaults(doc: Dict[str, Any]) -> Dict[str, Any]:
    """
    兼容旧 follow_settings.json：补齐新增字段，保证 WebUI 可显示/可编辑，并避免缺字段导致行为意外变化。
    """
    if not isinstance(doc, dict):
        out = dict(FOLLOW_SETTINGS_DEFAULTS)
    else:
        out = dict(doc)
        for k, v in FOLLOW_SETTINGS_DEFAULTS.items():
            out.setdefault(k, v)

    out["targets"] = _sanitize_target_rules(out.get("targets"))
    if not isinstance(out.get("members"), list):
        out["members"] = []
    out["follow_min_odds"] = _sanitize_follow_odds_limit(out.get("follow_min_odds"))
    out["follow_max_odds"] = _sanitize_follow_odds_limit(out.get("follow_max_odds"))
    out["match_limit_amount"] = _sanitize_nonnegative_amount(out.get("match_limit_amount"))
    out["market_limit_amount"] = _sanitize_nonnegative_amount(out.get("market_limit_amount"))
    out["execution_mode"] = "snapshot_direct"
    out["snapshot_direct_enabled"] = True
    if str(out.get("bet_context_mode") or "disabled").strip().lower() not in {
        "disabled",
        "diagnostic_order_view",
    }:
        out["bet_context_mode"] = "disabled"
    try:
        out["snapshot_max_age_ms"] = max(50, min(10000, int(out.get("snapshot_max_age_ms") or 500)))
    except Exception:
        out["snapshot_max_age_ms"] = 500
    for retired_key in (
        "snapshot_missing_policy",
        "snapshot_only_for_market",
        "snapshot_max_age_seconds",
        "game_more_scan_all_gids",
        "order_view_confirm_before_bet",
        "order_view_confirm_timeout_s",
        "use_odds_cache_for_total_odds",
        "odds_cache_max_age_seconds",
        "use_quote_member_for_total_odds",
        "quote_member_total_odds_timeout_s",
    ):
        out.pop(retired_key, None)
    try:
        out["unknown_result_reconcile_max_seconds"] = max(1, min(3600, int(out.get("unknown_result_reconcile_max_seconds") or 120)))
    except Exception:
        out["unknown_result_reconcile_max_seconds"] = 120
    try:
        out["unknown_result_reconcile_max_attempts"] = max(1, min(100, int(out.get("unknown_result_reconcile_max_attempts") or 5)))
    except Exception:
        out["unknown_result_reconcile_max_attempts"] = 5
    try:
        out["member_error_cooldown_seconds"] = max(0, min(600, int(out.get("member_error_cooldown_seconds") or 30)))
    except Exception:
        out["member_error_cooldown_seconds"] = 30
    out["market_rules"] = _sanitize_market_rules(out.get("market_rules"))
    out["reverse_rules"] = _sanitize_reverse_rules(out.get("reverse_rules"))
    return out


def merge_follow_settings(existing: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
    """
    合并保存策略（白名单字段）：
    - 仅当 incoming 显式携带字段时才更新（避免旧版 UI 缺字段把现有设置覆盖成默认值）
    - 保留 existing 的其它字段（例如高级参数）
    """
    if not isinstance(existing, dict):
        out: Dict[str, Any] = dict(FOLLOW_SETTINGS_DEFAULTS)
    else:
        out = dict(existing)

    if not isinstance(incoming, dict):
        return ensure_follow_settings_defaults(out)

    # 基础
    if "enabled" in incoming:
        out["enabled"] = bool(incoming.get("enabled"))
    if "mode" in incoming:
        out["mode"] = str(incoming.get("mode") or "fixed")
    if "fixed_amount" in incoming:
        try:
            out["fixed_amount"] = float(incoming.get("fixed_amount") or 0)
        except Exception:
            out["fixed_amount"] = 0.0
    if "ratio_percent" in incoming:
        try:
            out["ratio_percent"] = float(incoming.get("ratio_percent") or 0)
        except Exception:
            out["ratio_percent"] = 0.0
    if "max_amount" in incoming:
        try:
            out["max_amount"] = float(incoming.get("max_amount") or 0)
        except Exception:
            out["max_amount"] = 0.0
    if "match_limit_amount" in incoming:
        out["match_limit_amount"] = _sanitize_nonnegative_amount(incoming.get("match_limit_amount"))
    if "market_limit_amount" in incoming:
        out["market_limit_amount"] = _sanitize_nonnegative_amount(incoming.get("market_limit_amount"))
    if "follow_min_odds" in incoming:
        out["follow_min_odds"] = _sanitize_follow_odds_limit(incoming.get("follow_min_odds"))
    if "follow_max_odds" in incoming:
        out["follow_max_odds"] = _sanitize_follow_odds_limit(incoming.get("follow_max_odds"))

    # 分单
    if "split_count" in incoming:
        try:
            out["split_count"] = int(incoming.get("split_count") or 1)
        except Exception:
            out["split_count"] = 1
    if "split_delay_seconds" in incoming:
        try:
            out["split_delay_seconds"] = float(incoming.get("split_delay_seconds") or 0)
        except Exception:
            out["split_delay_seconds"] = 0.0

    # 旧客户端即使提交 Legacy/Shadow 字段，也不能改变唯一执行路径。
    out["execution_mode"] = "snapshot_direct"
    out["snapshot_direct_enabled"] = True
    if "snapshot_max_age_ms" in incoming:
        try:
            out["snapshot_max_age_ms"] = max(50, min(10000, int(incoming.get("snapshot_max_age_ms") or 0)))
        except Exception:
            out["snapshot_max_age_ms"] = int(FOLLOW_SETTINGS_DEFAULTS.get("snapshot_max_age_ms") or 500)
    if "bet_context_mode" in incoming:
        mode = str(incoming.get("bet_context_mode") or "disabled").strip().lower()
        out["bet_context_mode"] = mode if mode in {"disabled", "diagnostic_order_view"} else "disabled"
    if "unknown_result_reconcile_max_seconds" in incoming:
        try:
            out["unknown_result_reconcile_max_seconds"] = max(1, min(3600, int(incoming.get("unknown_result_reconcile_max_seconds") or 0)))
        except Exception:
            out["unknown_result_reconcile_max_seconds"] = 120
    if "unknown_result_reconcile_max_attempts" in incoming:
        try:
            out["unknown_result_reconcile_max_attempts"] = max(1, min(100, int(incoming.get("unknown_result_reconcile_max_attempts") or 0)))
        except Exception:
            out["unknown_result_reconcile_max_attempts"] = 5
    if "member_error_cooldown_seconds" in incoming:
        try:
            out["member_error_cooldown_seconds"] = max(0, min(600, int(incoming.get("member_error_cooldown_seconds") or 0)))
        except Exception:
            out["member_error_cooldown_seconds"] = 30

    # quote 会员
    if "allow_quote_members" in incoming:
        out["allow_quote_members"] = False

    # 硬隔离：即使旧配置里残留 true，也统一压回 false
    out["allow_quote_members"] = False

    # 盘口策略
    if "market_rules" in incoming:
        out["market_rules"] = _sanitize_market_rules(incoming.get("market_rules"))

    # 反打策略
    if "reverse_rules" in incoming:
        out["reverse_rules"] = _sanitize_reverse_rules(incoming.get("reverse_rules"))

    # per-target
    if "targets" in incoming:
        out["targets"] = _sanitize_target_rules(incoming.get("targets"))

    if "members" in incoming:
        members_in = incoming.get("members") or []
        out["members"] = list(members_in) if isinstance(members_in, list) else []

    return ensure_follow_settings_defaults(out)
