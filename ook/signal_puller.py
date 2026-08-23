#!/usr/bin/env python3
"""
HDPRO 远端信号拉取器（/signals -> 本地 orders/*.jsonl）

目标：
- 从远端信号接口拉取信号（默认兼容 /signals、/signals/json）
- 转成本地订单行并追加写入 orders/*.jsonl
- 让 follow_engine / WebUI / Telegram 复用现有链路
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib import error, parse, request


STATE_VERSION = 1
MAX_SEEN_KEYS = 20000


def now_iso() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def log(msg: str) -> None:
    print(f"[{now_iso()}] [ook-pull] {msg}", flush=True)


def parse_float(v: Any, default: float = 0.0) -> float:
    try:
        s = str(v or "").strip().replace(",", "")
        if not s:
            return default
        return float(s)
    except Exception:
        return default


def parse_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _stable_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:20]


def _safe_target_filename(name: str) -> str:
    s = str(name or "").strip() or "remote_signal"
    s = re.sub(r"[^\w.\-]+", "_", s)
    s = s.strip("._") or "remote_signal"
    return s


def _append_jsonl(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def _extract_signal_id(item: Dict[str, Any]) -> int:
    sid = parse_int(item.get("id"), 0)
    if sid > 0:
        return sid
    raw = item.get("raw")
    if isinstance(raw, dict):
        sid = parse_int(raw.get("id"), 0)
        if sid > 0:
            return sid
    return 0


def _build_fetch_url(raw_url: str, limit: int) -> str:
    u = parse.urlparse(str(raw_url or "").strip())
    q = parse.parse_qs(u.query, keep_blank_values=True)
    path = str(u.path or "")
    if path.rstrip("/").endswith("/signals"):
        q["view"] = ["json"]
    q["limit"] = [str(max(1, min(500, int(limit or 200))))]
    new_query = parse.urlencode(q, doseq=True)
    return parse.urlunparse((u.scheme, u.netloc, u.path, u.params, new_query, u.fragment))


def _fetch_json(url: str, *, token: str, timeout_seconds: int) -> Tuple[bool, Any, str]:
    headers = {"Accept": "application/json"}
    t = str(token or "").strip()
    if t:
        headers["X-HDZZ-Signal-Token"] = t
        headers["X-Signal-Token"] = t
    req = request.Request(url=url, headers=headers, method="GET")
    try:
        with request.urlopen(req, timeout=max(3, int(timeout_seconds))) as resp:
            body = (resp.read() or b"").decode("utf-8", errors="ignore")
            if not (200 <= int(resp.status) < 300):
                return False, None, f"http_{resp.status}:{body[:200]}"
            try:
                return True, json.loads(body), ""
            except Exception as e:
                return False, None, f"json_decode_failed:{e}"
    except error.HTTPError as exc:
        try:
            body = (exc.read() or b"").decode("utf-8", errors="ignore")
        except Exception:
            body = ""
        return False, None, f"http_{exc.code}:{body[:200]}"
    except Exception as exc:
        return False, None, str(exc)


def _extract_entries(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, dict):
        if isinstance(data.get("signals"), list):
            return [x for x in data.get("signals") or [] if isinstance(x, dict)]
        if isinstance(data.get("orders"), list):
            # 兼容 /api/public-signals
            return [x for x in data.get("orders") or [] if isinstance(x, dict)]
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    return []


def _recover_rewound_cursor(last_signal_id: int, ids: List[int]) -> Tuple[int, bool]:
    valid_ids = [x for x in ids if int(x or 0) > 0]
    if last_signal_id <= 0 or not valid_ids:
        return int(last_signal_id), False
    remote_max = max(valid_ids)
    if remote_max >= int(last_signal_id):
        return int(last_signal_id), False
    remote_min = min(valid_ids)
    return max(0, remote_min - 1), True


def _contains_live_text(v: Any) -> bool:
    return "滚球" in str(v or "").strip()


def _is_live_market_code(v: Any) -> bool:
    return str(v or "").strip().lower() in {"rb", "live", "running"}


def is_live_signal(item: Dict[str, Any]) -> bool:
    if _contains_live_text(item.get("market")):
        return True
    if _contains_live_text(item.get("bet_type")) or _contains_live_text(item.get("betType")):
        return True

    raw = item.get("raw")
    if isinstance(raw, dict):
        if _contains_live_text(raw.get("market")):
            return True
        if _contains_live_text(raw.get("bet_type")) or _contains_live_text(raw.get("betType")):
            return True
        if _contains_live_text(raw.get("WAGERSTYPE")):
            return True
        if _is_live_market_code(raw.get("MARKET")) or _is_live_market_code(raw.get("market")):
            return True

        source_order = raw.get("source_order")
        if isinstance(source_order, dict):
            if _contains_live_text(source_order.get("betType")) or _contains_live_text(source_order.get("bet_type")):
                return True
            if _contains_live_text(source_order.get("market")):
                return True
            source_raw = source_order.get("raw")
            if isinstance(source_raw, dict):
                if _contains_live_text(source_raw.get("WAGERSTYPE")):
                    return True
                if _is_live_market_code(source_raw.get("MARKET")) or _is_live_market_code(source_raw.get("market")):
                    return True
    return False


def _signal_to_order(item: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], str]:
    raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
    source_order = raw.get("source_order") if isinstance(raw.get("source_order"), dict) else {}

    if source_order:
        out = dict(source_order)
        # 远端来源机器可能已经给订单打上了自己的 Telegram 元数据；
        # 落到当前节点后必须清掉，改由本机 follow_engine 按本机配置重新决定推送群。
        out.pop("_tg_chat_id", None)
        out.pop("_tg_message_id", None)
        target = str(out.get("target") or raw.get("backend_name") or item.get("backend_name") or "remote_signal").strip()
        if not target:
            target = "remote_signal"
        out["target"] = target
        if str(out.get("orderId") or "").strip() == "":
            oid = str(raw.get("order_id") or item.get("order_id") or "").strip()
            if oid:
                out["orderId"] = oid
        if parse_int(out.get("ts"), 0) <= 0:
            out["ts"] = int(time.time())
        out["_skip_signal_forward"] = True
        out["_signal_origin"] = "remote_signal_pull"
        key = f"{target}|{str(out.get('orderId') or '').strip()}"
        if key.endswith("|"):
            key = f"{target}|h:{_stable_hash(json.dumps(out, ensure_ascii=False, sort_keys=True))}"
        return out, key

    league = str(item.get("league") or raw.get("league") or "").strip()
    match = str(item.get("match") or raw.get("match") or "").strip()
    if not (league and match):
        return None, ""

    target = str(item.get("backend_name") or raw.get("backend_name") or "remote_signal").strip() or "remote_signal"
    order_id = str(item.get("order_id") or raw.get("order_id") or f"sig-{_extract_signal_id(item)}").strip()
    line = str(item.get("line") or raw.get("line") or "").strip()
    bet_type = str(item.get("bet_type") or raw.get("bet_type") or "").strip()
    odds_v = item.get("odds") if item.get("odds") not in (None, "") else raw.get("odds")

    out = {
        "ts": int(time.time()),
        "target": target,
        "orderId": order_id,
        "sport": str(item.get("sport") or raw.get("sport") or ""),
        "stake": str(item.get("amount") or raw.get("amount") or "0"),
        "odds": odds_v,
        "league": league,
        "sides": match,
        "betType": bet_type,
        "line": line,
        "date": "",
        "time": str(item.get("created_at_client") or item.get("time") or raw.get("time") or ""),
        "raw": {},
        "_skip_signal_forward": True,
        "_signal_origin": "remote_signal_pull",
    }
    key = f"{target}|{order_id}" if order_id else f"{target}|h:{_stable_hash(json.dumps(out, ensure_ascii=False, sort_keys=True))}"
    return out, key


@dataclass
class RuntimeConfig:
    enabled: bool
    pull_url: str
    pull_token: str
    timeout_seconds: int
    interval_seconds: float
    limit: int
    push_history_on_start: bool
    live_only: bool
    debug: bool


def load_runtime_config(config_path: Path, interval_default: float, debug_default: bool) -> RuntimeConfig:
    cfg = read_json(config_path, {})
    if not isinstance(cfg, dict):
        cfg = {}
    enabled = bool(cfg.get("signal_pull_enabled") or False)
    pull_url = str(cfg.get("signal_pull_url") or "").strip()
    pull_token = str(cfg.get("signal_pull_token") or "").strip()
    timeout_seconds = parse_int(cfg.get("signal_pull_timeout_seconds"), 8)
    interval_seconds = float(cfg.get("signal_pull_interval_seconds") or interval_default)
    limit = parse_int(cfg.get("signal_pull_limit"), 200)
    push_history_on_start = bool(cfg.get("signal_pull_push_history_on_start") or False)
    live_only = bool(cfg.get("signal_pull_live_only") if "signal_pull_live_only" in cfg else True)
    debug = bool(cfg.get("signal_pull_debug") if "signal_pull_debug" in cfg else debug_default)
    return RuntimeConfig(
        enabled=enabled,
        pull_url=pull_url,
        pull_token=pull_token,
        timeout_seconds=max(3, timeout_seconds),
        interval_seconds=max(0.2, interval_seconds),
        limit=max(1, min(500, limit)),
        push_history_on_start=push_history_on_start,
        live_only=live_only,
        debug=debug,
    )


def default_state() -> Dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "last_signal_id": 0,
        "seen_keys": [],
        "updated_at": int(time.time()),
    }


def ensure_state_shape(obj: Any) -> Dict[str, Any]:
    if not isinstance(obj, dict):
        return default_state()
    out = dict(obj)
    out.setdefault("version", STATE_VERSION)
    out.setdefault("last_signal_id", 0)
    out.setdefault("seen_keys", [])
    out.setdefault("updated_at", int(time.time()))
    if not isinstance(out.get("seen_keys"), list):
        out["seen_keys"] = []
    return out


class SignalPuller:
    def __init__(
        self,
        *,
        orders_dir: Path,
        config_path: Path,
        state_path: Path,
        interval_default: float,
        debug_default: bool,
    ) -> None:
        self.orders_dir = orders_dir
        self.config_path = config_path
        self.state_path = state_path
        self.interval_default = interval_default
        self.debug_default = debug_default

        self.state = ensure_state_shape(read_json(self.state_path, default_state()))
        self.last_signal_id = parse_int(self.state.get("last_signal_id"), 0)
        self.seen_keys = set([str(x) for x in (self.state.get("seen_keys") or []) if str(x)])
        self._last_disabled_reason = ""

    def run_forever(self) -> int:
        log(f"启动 orders_dir={self.orders_dir} config={self.config_path} state={self.state_path}")
        while True:
            rc = load_runtime_config(self.config_path, self.interval_default, self.debug_default)
            try:
                self.tick(rc)
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                log(f"tick error: {exc}")
            time.sleep(float(rc.interval_seconds))

    def tick(self, rc: RuntimeConfig) -> None:
        if not rc.enabled:
            self._log_disabled_once("signal_pull_enabled=false")
            return
        if not rc.pull_url:
            self._log_disabled_once("signal_pull_url 未配置")
            return
        self._last_disabled_reason = ""

        fetch_url = _build_fetch_url(rc.pull_url, rc.limit)
        ok, payload, err = _fetch_json(fetch_url, token=rc.pull_token, timeout_seconds=rc.timeout_seconds)
        if not ok:
            log(f"fetch failed url={fetch_url} err={err}")
            return

        entries = _extract_entries(payload)
        if not entries:
            if rc.debug:
                log("no entries")
            return

        # 首次启动且不回补历史：直接把游标推进到当前最大 id，避免一次性灌入大量旧信号。
        ids = [_extract_signal_id(x) for x in entries if _extract_signal_id(x) > 0]
        recovered_cursor, rewound = _recover_rewound_cursor(self.last_signal_id, ids)
        if rewound:
            log(
                "detected remote signal id rewind "
                f"local_last_signal_id={self.last_signal_id} remote_max={max(ids)} reset_to={recovered_cursor}"
            )
            self.last_signal_id = recovered_cursor
        if self.last_signal_id <= 0 and ids and (not rc.push_history_on_start):
            self.last_signal_id = max(ids)
            self._persist_state()
            if rc.debug:
                log(f"skip history on start last_signal_id={self.last_signal_id}")
            return

        wrote = 0
        touched = False
        # /signals/json 默认倒序，按 id 升序回放，保证顺序稳定。
        if ids:
            entries = sorted(entries, key=lambda x: _extract_signal_id(x))
        else:
            entries = list(reversed(entries))

        for item in entries:
            sid = _extract_signal_id(item)
            if sid > 0 and sid <= self.last_signal_id:
                continue

            if rc.live_only and (not is_live_signal(item)):
                if sid > self.last_signal_id:
                    self.last_signal_id = sid
                    touched = True
                if rc.debug:
                    log(f"skip non-live signal_id={sid or '-'}")
                continue

            order, order_key = _signal_to_order(item)
            if not order:
                continue

            if order_key and order_key in self.seen_keys:
                if sid > self.last_signal_id:
                    self.last_signal_id = sid
                    touched = True
                continue

            target = str(order.get("target") or "remote_signal").strip() or "remote_signal"
            out_file = self.orders_dir / f"{_safe_target_filename(target)}.jsonl"
            _append_jsonl(out_file, order)
            wrote += 1

            if sid > self.last_signal_id:
                self.last_signal_id = sid
                touched = True
            if order_key:
                self.seen_keys.add(order_key)
                touched = True

            if rc.debug:
                log(
                    f"append order target={target} order={order.get('orderId') or '-'} "
                    f"signal_id={sid or '-'} file={out_file.name}"
                )

        if wrote > 0:
            log(f"appended {wrote} orders")
        if wrote > 0 or touched:
            self._persist_state()

    def _persist_state(self) -> None:
        keys = list(self.seen_keys)
        if len(keys) > MAX_SEEN_KEYS:
            keys = keys[-MAX_SEEN_KEYS:]
            self.seen_keys = set(keys)
        self.state["last_signal_id"] = int(self.last_signal_id)
        self.state["seen_keys"] = keys
        self.state["updated_at"] = int(time.time())
        write_json(self.state_path, self.state)

    def _log_disabled_once(self, reason: str) -> None:
        if reason == self._last_disabled_reason:
            return
        self._last_disabled_reason = reason
        log(f"等待配置: {reason}")


def parse_args(argv: List[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Pull remote signals and append to local orders/*.jsonl")
    ap.add_argument("--orders-dir", default="orders")
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--state", default=".run/ook_signal_puller_state.json")
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--once", action="store_true")
    return ap.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    p = SignalPuller(
        orders_dir=Path(args.orders_dir),
        config_path=Path(args.config),
        state_path=Path(args.state),
        interval_default=max(0.2, float(args.interval)),
        debug_default=bool(args.debug),
    )
    if args.once:
        rc = load_runtime_config(Path(args.config), max(0.2, float(args.interval)), bool(args.debug))
        p.tick(rc)
        return 0
    return p.run_forever()


if __name__ == "__main__":
    raise SystemExit(main())
