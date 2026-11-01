"""真实 Chromium + 本地模拟服务器；不访问平博，不使用任何真实凭据。"""
import json
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ps38.client import Ps3838Client, BET_MONITOR_PATH, KEEPALIVE_PATH
from ps38.storage import OrderStore


calls = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def send(self, content_type, body):
        body = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.send("text/html", "<html><body>Local smoke test</body></html>")

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        calls.append((self.path, json.loads(body) if body else None))
        if self.path == KEEPALIVE_PATH:
            self.send("application/json", '{"alive":true}')
        elif self.path == BET_MONITOR_PATH:
            self.send("application/json", json.dumps({"data": [{"playerWagerId": "local-test", "stake": 150, "currency": "THB"}]}))
        else:
            self.send_error(404)


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        with tempfile.TemporaryDirectory(prefix="ps38-local-smoke-") as tmp:
            cfg = {"name": "local", "base_url": f"http://127.0.0.1:{server.server_port}", "headless": True}
            client = Ps3838Client(cfg, Path(tmp) / "profile")
            try:
                client._ensure_context()
                client.page.goto(cfg["base_url"] + "/partner/")
                client.wait_for_login()
                assert client.session_ready
                assert client.initial_payload["data"][0]["playerWagerId"] == "local-test"
                keepalive, payload = client.poll()
                assert keepalive["alive"]
                store = OrderStore(Path(tmp) / "data", "local")
                try:
                    events, count = store.ingest(payload)
                    assert count == 1 and events[0]["event"] == "snapshot"
                    assert (Path(tmp) / "data/orders.jsonl").exists()
                finally:
                    store.close()
                assert {p for p, _ in calls} == {KEEPALIVE_PATH, BET_MONITOR_PATH}
                assert calls[-1][1]["historyTime"] == 60
                print("PASS：独立浏览器启动 → 本地会话校验 → 保活/订单 fetch → 事务保存/导出 → 关闭浏览器")
            finally:
                client.close()
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


if __name__ == "__main__":
    main()
