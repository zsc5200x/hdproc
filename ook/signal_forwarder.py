#!/usr/bin/env python3
"""
HDPRO 订单 -> Webhook 信号转发器（最小恢复版）

用途：
- 监听 orders/*.jsonl 的新增订单
- 转换为统一 signal payload
- 推送到配置的 Webhook
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib import error, request


STATE_VERSION = 1
MAX_DELIVERED_KEYS = 20000
RECENT_BOOTSTRAP_BACKFILL_SECONDS = 1800
RECENT_BOOTSTRAP_BACKFILL_MAX_LINES = 200
RECENT_BOOTSTRAP_BACKFILL_MAX_BYTES = 262144


def now_iso() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def log(msg: str) -> None:
    print(f"[{now_iso()}] [ook] {msg}", flush=True)


def parse_float(value: Any) -> float:
    s = str(value or "").strip().replace(",", "")
    if not s:
        return 0.0
    try:
        return float(s)
    except Exception:
        return 0.0


def stable_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def compose_play(order: Dict[str, Any]) -> str:
    line = str(order.get("line") or "").strip()
    odds = str(order.get("odds") or "").strip()
    if line and odds:
        return f"{line} @{odds}"
    if line:
        return line
    if odds:
        return f"@{odds}"
    return ""


def compose_time(order: Dict[str, Any]) -> str:
    date = str(order.get("date") or "").strip()
    t = str(order.get("time") or "").strip()
    if date and t:
        return f"{date} {t}"
    return date or t


def order_key(order: Dict[str, Any], backend_name: str) -> str:
    oid = str(order.get("orderId") or order.get("order_id") or "").strip()
    if oid:
        return f"{backend_name}|{oid}"
    raw = json.dumps(order, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"{backend_name}|h:{stable_hash(raw)}"


def should_skip_forwarding_order(order: Dict[str, Any]) -> bool:
    if not isinstance(order, dict):
        return False
    if bool(order.get("_skip_signal_forward")):
        return True
    return str(order.get("_signal_origin") or "").strip() == "remote_signal_pull"


def to_signal_payload(order: Dict[str, Any], backend_name: str, source_file: str) -> Dict[str, Any]:
    return {
        "backend_name": backend_name,
        "order_id": str(order.get("orderId") or order.get("order_id") or "").strip(),
        "league": str(order.get("league") or ""),
        "match": str(order.get("sides") or order.get("match") or ""),
        "play": compose_play(order),
        "amount": parse_float(order.get("stake") or order.get("amount") or order.get("bet")),
        "time": compose_time(order),
        "sport": str(order.get("sport") or ""),
        "bet_type": str(order.get("betType") or ""),
        "line": str(order.get("line") or ""),
        "odds": str(order.get("odds") or ""),
        "source": "hdpro_orders_jsonl",
        "source_file": source_file,
        "source_ts": int(order.get("ts") or 0),
        "source_order": order,
    }


def post_json(url: str, payload: Dict[str, Any], token: str, timeout_seconds: int) -> Tuple[bool, str]:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-TGGD-Token"] = token
    req = request.Request(url=url, data=data, headers=headers, method="POST")
    try:
        with request.urlopen(req, timeout=max(3, int(timeout_seconds))) as resp:
            body = (resp.read() or b"").decode("utf-8", errors="ignore")
            if 200 <= int(resp.status) < 300:
                return True, body[:200]
            return False, f"http_{resp.status}:{body[:200]}"
    except error.HTTPError as exc:
        try:
            body = (exc.read() or b"").decode("utf-8", errors="ignore")
        except Exception:
            body = ""
        return False, f"http_{exc.code}:{body[:200]}"
    except Exception as exc:
        return False, str(exc)


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


def parse_ts(value: Any) -> int:
    try:
        return int(float(value or 0))
    except Exception:
        return 0


def tail_jsonl_objects(path: Path, *, max_lines: int, max_bytes: int) -> List[Dict[str, Any]]:
    max_lines = max(1, int(max_lines or 1))
    max_bytes = max(1024, int(max_bytes or 1024))
    if not path.exists():
        return []
    try:
        with path.open("rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            start = max(0, size - max_bytes)
            fh.seek(start)
            data = fh.read()
        if start > 0:
            first_newline = data.find(b"\n")
            if first_newline >= 0:
                data = data[first_newline + 1 :]
        out: List[Dict[str, Any]] = []
        for line in data.decode("utf-8", errors="replace").splitlines()[-max_lines:]:
            text = str(line or "").strip()
            if not text:
                continue
            try:
                obj = json.loads(text)
            except Exception:
                continue
            if isinstance(obj, dict):
                out.append(obj)
        return out
    except Exception:
        return []


@dataclass
class RuntimeConfig:
    enabled: bool
    webhook_url: str
    webhook_token: str
    timeout_seconds: int
    retry_max: int
    retry_backoff_seconds: float
    interval_seconds: float
    push_history_on_start: bool
    debug: bool


def load_runtime_config(config_path: Path, interval_default: float, debug_default: bool) -> RuntimeConfig:
    cfg = read_json(config_path, {})
    if not isinstance(cfg, dict):
        cfg = {}
    return RuntimeConfig(
        enabled=bool(cfg.get("signal_webhook_enabled") or False),
        webhook_url=str(cfg.get("signal_webhook_url") or "").strip(),
        webhook_token=str(cfg.get("signal_webhook_token") or "").strip(),
        timeout_seconds=max(3, int(cfg.get("signal_webhook_timeout_seconds") or 8)),
        retry_max=max(1, int(cfg.get("signal_webhook_retry_max") or 3)),
        retry_backoff_seconds=max(0.2, float(cfg.get("signal_webhook_retry_backoff_seconds") or 1.0)),
        interval_seconds=max(0.1, float(cfg.get("signal_webhook_interval_seconds") or interval_default)),
        push_history_on_start=bool(cfg.get("signal_webhook_push_history_on_start") or False),
        debug=bool(cfg.get("signal_webhook_debug") if "signal_webhook_debug" in cfg else debug_default),
    )


def default_state() -> Dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "files": {},
        "delivered_keys": [],
        "updated_at": int(time.time()),
    }


def ensure_state_shape(obj: Any) -> Dict[str, Any]:
    if not isinstance(obj, dict):
        return default_state()
    out = dict(obj)
    out.setdefault("version", STATE_VERSION)
    out.setdefault("files", {})
    out.setdefault("delivered_keys", [])
    out.setdefault("updated_at", int(time.time()))
    if not isinstance(out["files"], dict):
        out["files"] = {}
    if not isinstance(out["delivered_keys"], list):
        out["delivered_keys"] = []
    return out


class Forwarder:
    def __init__(self, orders_dir: Path, config_path: Path, state_path: Path, interval_default: float, debug_default: bool) -> None:
        self.orders_dir = orders_dir
        self.config_path = config_path
        self.state_path = state_path
        self.interval_default = interval_default
        self.debug_default = debug_default
        self.state = ensure_state_shape(read_json(self.state_path, default_state()))
        self.delivered_keys = set(str(x) for x in self.state.get("delivered_keys") or [])
        self._last_disabled_reason = ""

    def _persist_state(self) -> None:
        keys = list(self.delivered_keys)
        if len(keys) > MAX_DELIVERED_KEYS:
            keys = keys[-MAX_DELIVERED_KEYS:]
            self.delivered_keys = set(keys)
        self.state["delivered_keys"] = keys
        self.state["updated_at"] = int(time.time())
        write_json(self.state_path, self.state)

    def _log_disabled_once(self, reason: str) -> None:
        if self._last_disabled_reason == reason:
            return
        self._last_disabled_reason = reason
        log(reason)

    def _send_with_retry(self, rc: RuntimeConfig, payload: Dict[str, Any]) -> Tuple[bool, str]:
        last_err = ""
        for idx in range(max(1, int(rc.retry_max))):
            ok, msg = post_json(rc.webhook_url, payload, rc.webhook_token, rc.timeout_seconds)
            if ok:
                return True, msg
            last_err = msg
            if idx + 1 < int(rc.retry_max):
                time.sleep(float(rc.retry_backoff_seconds))
        return False, last_err

    def _backfill_recent_orders_for_new_file(self, path: Path, rc: RuntimeConfig) -> bool:
        cutoff_ts = int(time.time()) - int(RECENT_BOOTSTRAP_BACKFILL_SECONDS)
        recent_orders = tail_jsonl_objects(
            path,
            max_lines=RECENT_BOOTSTRAP_BACKFILL_MAX_LINES,
            max_bytes=RECENT_BOOTSTRAP_BACKFILL_MAX_BYTES,
        )
        for obj in recent_orders:
            if should_skip_forwarding_order(obj):
                continue
            order_ts = parse_ts(obj.get("ts"))
            if order_ts <= 0 or order_ts < cutoff_ts:
                continue
            backend_name = path.stem
            okey = order_key(obj, backend_name)
            if okey in self.delivered_keys:
                continue
            payload = to_signal_payload(obj, backend_name, path.name)
            ok, msg = self._send_with_retry(rc, payload)
            if not ok:
                log(f"bootstrap push failed backend={backend_name} order={obj.get('orderId') or '-'} err={msg}")
                return False
            if rc.debug:
                log(
                    f"bootstrap push ok backend={backend_name} order={obj.get('orderId') or '-'} "
                    f"amount={payload.get('amount')}"
                )
            self.delivered_keys.add(okey)
        return True

    def _process_file(self, path: Path, rc: RuntimeConfig) -> Tuple[bool, bool]:
        files_state = self.state["files"]
        key = str(path.resolve())
        stat = path.stat()
        meta = files_state.get(key)

        if not isinstance(meta, dict):
            offset = 0 if rc.push_history_on_start else int(stat.st_size)
            if rc.debug:
                log(f"track file={path.name} offset={offset} size={stat.st_size}")
            if not rc.push_history_on_start:
                if not self._backfill_recent_orders_for_new_file(path, rc):
                    return False, True
                stat = path.stat()
                files_state[key] = {"offset": int(stat.st_size), "size": int(stat.st_size)}
                return True, True
            files_state[key] = {"offset": offset, "size": int(stat.st_size)}
            meta = files_state[key]

        try:
            offset = int(meta.get("offset") or 0)
        except Exception:
            offset = 0
        if offset < 0 or offset > stat.st_size:
            offset = 0

        changed = False
        with path.open("rb") as fh:
            fh.seek(offset)
            while True:
                line_start = fh.tell()
                line = fh.readline()
                if not line:
                    break
                offset = fh.tell()
                text = line.decode("utf-8", errors="ignore").strip()
                if not text:
                    continue
                changed = True
                try:
                    obj = json.loads(text)
                except Exception:
                    continue
                if not isinstance(obj, dict):
                    continue
                if should_skip_forwarding_order(obj):
                    continue
                backend_name = path.stem
                okey = order_key(obj, backend_name)
                if okey in self.delivered_keys:
                    continue
                payload = to_signal_payload(obj, backend_name, path.name)
                ok, msg = self._send_with_retry(rc, payload)
                if not ok:
                    log(f"push failed backend={backend_name} order={obj.get('orderId') or '-'} err={msg}")
                    files_state[key] = {"offset": line_start, "size": int(stat.st_size)}
                    return False, True
                if rc.debug:
                    log(f"push ok backend={backend_name} order={obj.get('orderId') or '-'} amount={payload.get('amount')} resp={msg}")
                self.delivered_keys.add(okey)

        files_state[key] = {"offset": offset, "size": int(stat.st_size)}
        return True, changed

    def tick(self, rc: RuntimeConfig) -> None:
        if not rc.enabled:
            self._log_disabled_once("signal_webhook_enabled=false")
            return
        if not rc.webhook_url:
            self._log_disabled_once("signal_webhook_url 未配置")
            return
        self._last_disabled_reason = ""

        self.orders_dir.mkdir(parents=True, exist_ok=True)
        changed = False
        for path in sorted(self.orders_dir.glob("*.jsonl")):
            ok, file_changed = self._process_file(path, rc)
            changed = changed or file_changed
            if not ok:
                break
        if changed:
            self._persist_state()

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
            time.sleep(max(0.1, float(rc.interval_seconds)))


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Forward hdpro orders/*.jsonl to webhook signals")
    ap.add_argument("--orders-dir", default="orders")
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--state", default=".run/ook_signal_forwarder_state.json")
    ap.add_argument("--interval", type=float, default=0.5)
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--once", action="store_true")
    return ap.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    ns = parse_args(argv)
    forwarder = Forwarder(
        orders_dir=Path(ns.orders_dir),
        config_path=Path(ns.config),
        state_path=Path(ns.state),
        interval_default=float(ns.interval),
        debug_default=bool(ns.debug),
    )
    if ns.once:
        rc = load_runtime_config(forwarder.config_path, forwarder.interval_default, forwarder.debug_default)
        forwarder.tick(rc)
        return 0
    return forwarder.run_forever()


if __name__ == "__main__":
    raise SystemExit(main())
