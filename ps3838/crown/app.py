import json
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from project_config import load_config

from crown_runtime import (
    close as close_crown,
    account_action,
    add_account,
    initialize as initialize_crown,
    lock as crown_runtime_lock,
    public_accounts,
    extra_markets_enabled,
    refresh_event_more,
    refresh_once,
    state as crown_state,
)

HOST = "127.0.0.1"
PORT = 9223
# The upstream list call usually completes in about one second. A four-second
# cadence keeps the feed materially fresher while leaving headroom for Crown's
# rate limits, connection jitter, and slower responses.
CROWN_REFRESH_SECONDS = 4
CROWN_FAILURE_BACKOFF_SECONDS = (4, 8, 16, 30)


def _crown_retry_delay(result):
    if result.get("ok"):
        return CROWN_REFRESH_SECONDS
    reason = str(result.get("reason") or "")
    if reason == "refresh_in_progress":
        return 2
    if reason == "no_session":
        return 5
    if reason == "session_conflict":
        return 5
    if reason == "runtime_unavailable":
        return 30
    with crown_runtime_lock:
        failures = int((crown_state.get("diagnostics") or {}).get("consecutive_failures") or 1)
    index = min(max(failures, 1) - 1, len(CROWN_FAILURE_BACKOFF_SECONDS) - 1)
    return CROWN_FAILURE_BACKOFF_SECONDS[index]


def crown_refresh_loop(stop_event=None):
    stop_event = stop_event or threading.Event()
    while not stop_event.is_set():
        refresh_started = time.monotonic()
        try:
            result = refresh_once()
        except Exception:
            result = {"ok": False, "reason": "crown_unavailable"}
        delay = _crown_retry_delay(result)
        with crown_runtime_lock:
            diagnostics = dict(crown_state.get("diagnostics") or {})
            diagnostics["retry_in_ms"] = int(round(delay * 1000))
            crown_state["diagnostics"] = diagnostics
        remaining = delay - (time.monotonic() - refresh_started)
        if remaining > 0:
            stop_event.wait(remaining)


def _compact_market_items(items, *, include_extra_fields=False):
    keys = ["line", "a", "b", "gid", "gidm", "hgid", "market_id", "master", "period"]
    if include_extra_fields:
        keys.extend((
            "raw_line", "market_type", "market_key", "home", "draw", "away",
            "over", "under",
        ))
    compact = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        compact.append({
            key: item.get(key)
            for key in keys
            if item.get(key) not in (None, "")
        })
    return compact


def _compact_extra_markets(event):
    extra = event.get("extra_markets") if isinstance(event, dict) else None
    if not isinstance(extra, dict):
        return {"status": "not_requested", "handicap": [], "total": [], "half_handicap": [], "half_total": [], "moneyline": []}
    result = {"status": str(extra.get("status") or "not_requested")}
    for market_type in ("handicap", "total", "half_handicap", "half_total", "moneyline"):
        result[market_type] = _compact_market_items(
            extra.get(market_type),
            include_extra_fields=True,
        )
    return result


def _compact_crown_event(event):
    if not isinstance(event, dict):
        return None
    markets = event.get("markets") if isinstance(event.get("markets"), dict) else {}
    result = {
        key: event.get(key)
        for key in (
            "id", "league_id", "league", "is_live", "is_finished", "home",
            "away", "start_label", "score", "minute", "period",
            "odds_format_label",
        )
    }
    result["markets"] = {
        "moneyline": list(markets.get("moneyline") or []),
        "handicap": _compact_market_items(markets.get("handicap")),
        "total": _compact_market_items(markets.get("total")),
        "half_handicap": _compact_market_items(markets.get("half_handicap")),
        "half_total": _compact_market_items(markets.get("half_total")),
    }
    result["extra_markets"] = _compact_extra_markets(event)
    return result


def _snapshot_crown(compact=False):
    details_enabled = extra_markets_enabled()
    with crown_runtime_lock:
        snapshot = {
            "events": list(crown_state.get("events") or []),
            "leagues": list(crown_state.get("leagues") or []),
            "updated_at": crown_state.get("updated_at"),
            "status": crown_state.get("status"),
            "error": crown_state.get("error"),
            "cached": bool(crown_state.get("cached")),
            "source": crown_state.get("source"),
            "extra_markets_enabled": details_enabled,
            "diagnostics": dict(crown_state.get("diagnostics") or {}),
        }
    if compact:
        snapshot["events"] = [
            item for item in (
                _compact_crown_event(event) for event in snapshot["events"]
            ) if item is not None
        ]
    return snapshot


STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_ASSETS = {
    "app.css": "text/css; charset=utf-8",
    "app.js": "text/javascript; charset=utf-8",
}


def render_crown_html(*, embedded=False, fixtures=False):
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    classes = []
    if embedded:
        classes.append("crown-embedded")
    if fixtures:
        classes.append("crown-fixtures")
    if classes:
        return html.replace("<body>", f'<body class="{" ".join(classes)}">', 1)
    return html


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError("请求格式不正确") from None
        if length < 0 or length > 128 * 1024:
            raise ValueError("请求体过大")
        raw = self.rfile.read(length)
        if not raw:
            return {}
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("请求格式不正确") from None
        if not isinstance(payload, dict):
            raise ValueError("请求必须是 JSON 对象")
        return payload

    def _crown_state(self, compact=False):
        return _snapshot_crown(compact=compact)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        if path == "/api/crown/events":
            compact = str((query.get("compact") or ["0"])[0]).lower() in {"1", "true", "yes"}
            self._send_json(self._crown_state(compact=compact))
            return
        if path == "/api/crown/accounts":
            try:
                self._send_json({"accounts": public_accounts()})
            except Exception:
                self._send_json({"error": "账号存储暂不可用"}, status=500)
            return
        if path == "/api/crown/session":
            payload = self._crown_state()
            payload.pop("events", None)
            payload.pop("leagues", None)
            self._send_json(payload)
            return
        if path.startswith("/crown/static/"):
            relative = unquote(path[len("/crown/static/"):])
            parts = relative.split("/")
            module = relative.startswith("modules/") and relative.endswith(".js")
            content_type = STATIC_ASSETS.get(relative)
            if module:
                content_type = "text/javascript; charset=utf-8"
            if content_type is None or any(part in {"", ".", ".."} or "\\" in part for part in parts):
                self.send_error(404)
                return
            candidate = (STATIC_DIR / relative).resolve()
            try:
                candidate.relative_to(STATIC_DIR.resolve())
                body = candidate.read_bytes()
            except (OSError, ValueError):
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/crown":
            embedded = str((query.get("embed") or ["0"])[0]).lower() in {"1", "true", "yes"}
            fixtures = str((query.get("fixtures") or ["0"])[0]).lower() in {"1", "true", "yes"}
            body = render_crown_html(embedded=embedded, fixtures=fixtures).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/":
            self.send_response(302)
            self.send_header("Location", "/crown")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_error(404)

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/crown/session/check":
            refresh_once()
            payload = self._crown_state()
            payload.pop("events", None)
            payload.pop("leagues", None)
            self._send_json(payload)
            return
        parts = [part for part in path.split("/") if part]
        query = parse_qs(urlparse(self.path).query)
        force = str((query.get("force") or ["0"])[0]).lower() in {"1", "true", "yes"}
        if len(parts) == 5 and parts[:3] == ["api", "crown", "events"] and parts[4] == "details":
            try:
                result = refresh_event_more(parts[3], force=True) if force else refresh_event_more(parts[3])
            except KeyError as exc:
                self._send_json({"error": str(exc)}, status=404)
                return
            except Exception:
                self._send_json({"error": "附加盘加载失败"}, status=500)
                return
            status = 200 if result.get("status") in {"loaded", "disabled"} else 202 if result.get("status") == "loading" else 502
            self._send_json(result, status=status)
            return
        if path == "/api/crown/accounts":
            try:
                payload = self._read_json()
                required = ("login_url", "username")
                if any(not isinstance(payload.get(key), str) or not payload.get(key, "").strip() for key in required):
                    raise ValueError("登录地址和账号不能为空")
                account = add_account(payload["login_url"], payload["username"])
                account = account_action(account["id"], "login") or account
                self._send_json({"account": account}, status=201)
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=400)
            except Exception:
                self._send_json({"error": "账号保存失败"}, status=500)
            return
        parts = [part for part in path.split("/") if part]
        if len(parts) == 5 and parts[:3] == ["api", "crown", "accounts"]:
            account_id, action = parts[3], parts[4]
            if action not in {"login", "check", "relogin", "cancel", "clear", "delete"}:
                self._send_json({"error": "不支持的账号操作"}, status=404)
                return
            try:
                account = account_action(account_id, action)
                self._send_json({"account": account, "accounts": public_accounts()})
            except (KeyError, ValueError) as exc:
                self._send_json({"error": str(exc)}, status=400)
            except Exception:
                self._send_json({"error": "账号操作失败"}, status=500)
            return
        self.send_error(404)

    def do_DELETE(self):
        path = self.path.split("?", 1)[0]
        parts = [part for part in path.split("/") if part]
        if len(parts) == 4 and parts[:3] == ["api", "crown", "accounts"]:
            try:
                account = account_action(parts[3], "delete")
                self._send_json({"account": account, "accounts": public_accounts()})
            except (KeyError, ValueError) as exc:
                self._send_json({"error": str(exc)}, status=400)
            except Exception:
                self._send_json({"error": "删除账号失败"}, status=500)
            return
        self.send_error(404)

    def log_message(self, fmt, *args):
        print("[%s] %s" % (self.log_date_time_string(), fmt % args))

def main():
    config = load_config()
    host = str(config.get("crown_host") or HOST)
    port = int(config.get("crown_port") or PORT)
    stop_event = threading.Event()
    worker = None
    server = ThreadingHTTPServer((host, port), Handler)

    def handle_sigterm(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, handle_sigterm)
    try:
        initialize_crown()
        worker = threading.Thread(
            target=crown_refresh_loop, args=(stop_event,),
            name="crown-refresh", daemon=True,
        )
        worker.start()
        print("皇冠赛事网页已启动")
        print(f"本机访问：http://{host}:{port}")
        print("停止服务：按 Control + C")
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        stop_event.set()
        if worker is not None and worker.ident is not None:
            worker.join()
        close_crown()


if __name__ == "__main__":
    main()
