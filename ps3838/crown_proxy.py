"""Reverse proxy for the locally managed Crown dashboard.

The Crown service intentionally remains a separate process because it owns
Playwright login/session state and may restart independently from the Pinnacle
collector.  The main dashboard exposes its page and API under the same origin,
so the Crown frontend can keep using relative API URLs and embed cleanly.
"""

from __future__ import annotations

import http.client
import json
from typing import Any
from urllib.parse import unquote, urlsplit

CROWN_HOST = "127.0.0.1"
CROWN_PORT = 9223
CROWN_TIMEOUT_SECONDS = 20
MAX_PROXY_BODY_BYTES = 4 * 1024 * 1024
FORWARD_REQUEST_HEADERS = ("Accept", "Content-Type", "Cookie", "Authorization")
FORWARD_RESPONSE_HEADERS = (
    "Content-Type",
    "Cache-Control",
    "Set-Cookie",
    "Location",
)


def is_crown_path(path: str) -> bool:
    """Return whether *path* belongs to the Crown page or its API."""
    decoded = unquote(path)
    module_path = decoded.startswith("/crown/static/modules/") and decoded.endswith(".js")
    valid_module = module_path and all(
        part not in {"", ".", ".."} and "\\" not in part
        for part in decoded[len("/crown/static/"):].split("/")
    )
    return (
        valid_module or path in {
            "/crown", "/crown/", "/crown/static/app.css", "/crown/static/app.js",
            "/api/crown",
        }
        or path.startswith("/api/crown/")
    )


def _unavailable_page() -> bytes:
    return (
        "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<title>皇冠服务暂不可用</title><style>"
        "body{margin:0;min-height:100vh;display:grid;place-items:center;"
        "background:#080d16;color:#f3f7fb;font:16px -apple-system,BlinkMacSystemFont,"
        "'PingFang SC',sans-serif}main{max-width:520px;padding:32px;text-align:center}"
        "h1{font-size:20px;margin:0 0 10px}p{color:#8290a7;line-height:1.7;margin:0}"
        "code{color:#42e6a4}</style></head><body><main>"
        "<h1>皇冠服务暂不可用</h1>"
        "<p>主看板已正常运行，但没有连接上皇冠进程。请执行 "
        "<code>./start.sh</code> 或 <code>./crown.sh</code>，页面会自动重试。</p>"
        "</main><script>setTimeout(function(){location.reload()},5000)</script>"
        "</body></html>"
    ).encode("utf-8")


def _send_upstream_unavailable(handler: Any) -> None:
    path = urlsplit(handler.path).path
    if path in {"/crown", "/crown/"}:
        body = _unavailable_page()
        content_type = "text/html; charset=utf-8"
    else:
        body = json.dumps(
            {"ok": False, "error": "皇冠服务暂不可用，请执行 ./start.sh"},
            ensure_ascii=False,
        ).encode("utf-8")
        content_type = "application/json; charset=utf-8"
    handler.send_response(503)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def proxy_crown(handler: Any, method: str) -> None:
    """Forward the current request to the configured Crown service target.

    Only fixed Crown page/API paths are accepted.  There is deliberately no
    user-controlled upstream URL, which keeps this from becoming an SSRF path.
    """
    parsed = urlsplit(handler.path)
    if not is_crown_path(parsed.path):
        raise ValueError("unsupported Crown proxy path")

    body = None
    if method in {"POST", "PUT", "PATCH"}:
        try:
            length = int(handler.headers.get("Content-Length", "0"))
        except (TypeError, ValueError):
            handler.send_error(400, "Content-Length 格式不正确")
            return
        if length < 0 or length > MAX_PROXY_BODY_BYTES:
            handler.send_error(413, "请求体过大")
            return
        if length:
            body = handler.rfile.read(length)

    request_headers = {}
    for name in FORWARD_REQUEST_HEADERS:
        value = handler.headers.get(name)
        if value:
            request_headers[name] = value
    request_headers.setdefault("Accept", "*/*")

    target = parsed.path
    if parsed.query:
        target = f"{target}?{parsed.query}"

    connection = None
    try:
        config = getattr(handler, "config", {})
        connection = http.client.HTTPConnection(
            str(config.get("crown_host") or CROWN_HOST),
            int(config.get("crown_port") or CROWN_PORT),
            timeout=CROWN_TIMEOUT_SECONDS,
        )
        connection.request(method, target, body=body, headers=request_headers)
        response = connection.getresponse()
        response_body = response.read()
        response_headers = response.getheaders()
    except (OSError, http.client.HTTPException):
        _send_upstream_unavailable(handler)
        return
    finally:
        if connection is not None:
            connection.close()

    handler.send_response(response.status)
    forwarded = set()
    for name, value in response_headers:
        lowered = name.lower()
        if name in FORWARD_RESPONSE_HEADERS:
            handler.send_header(name, value)
            forwarded.add(lowered)
    if "cache-control" not in forwarded:
        handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(response_body)))
    handler.end_headers()
    handler.wfile.write(response_body)
