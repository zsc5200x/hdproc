"""Local transport for the PS3838 dashboard.

The PS3838 worker no longer binds a TCP port.  It listens on a Unix domain
socket under ``ps3838/var/run`` and the HD WebUI (9999) is the only HTTP
entry point.  This module implements the small HTTP client needed to talk to
that socket from both the WebUI and the launcher.
"""

from __future__ import annotations

import http.client
import json
import socket
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


DEFAULT_SOCKET_NAME = "dashboard.sock"


class UnixHTTPConnection(http.client.HTTPConnection):
    """``http.client`` connection over an ``AF_UNIX`` socket."""

    def __init__(self, socket_path: Path, *, host: str = "localhost", timeout: float = 10.0) -> None:
        super().__init__(host, timeout=max(0.2, float(timeout)))
        self.socket_path = str(socket_path)

    def connect(self) -> None:  # noqa: D401 - http.client hook
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(self.socket_path)
        except BaseException:
            try:
                sock.close()
            finally:
                raise
        self.sock = sock


def request(
    socket_path: Path,
    method: str,
    path: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    body: Optional[bytes] = None,
    timeout: float = 10.0,
) -> Tuple[int, List[Tuple[str, str]], bytes]:
    """Send one HTTP request over the Unix socket and return the raw reply."""
    connection = UnixHTTPConnection(socket_path, timeout=timeout)
    try:
        connection.request(str(method).upper(), path, body=body, headers=headers or {})
        response = connection.getresponse()
        payload = response.read()
        return int(response.status), list(response.getheaders()), payload
    finally:
        connection.close()


def health(socket_path: Path, *, timeout: float = 2.0) -> Dict[str, Any]:
    """Read ``/api/health`` from the dashboard socket."""
    try:
        status, _headers, payload = request(
            socket_path,
            "GET",
            "/api/health",
            headers={"Accept": "application/json"},
            timeout=timeout,
        )
    except FileNotFoundError:
        return {"ok": False, "error": f"socket 不存在: {socket_path}"}
    except (OSError, socket.timeout) as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:  # pragma: no cover - defensive
        return {"ok": False, "error": str(exc)}
    if status != 200:
        return {"ok": False, "error": f"HTTP {status}"}
    try:
        doc = json.loads(payload.decode("utf-8", errors="replace")) if payload else {}
    except (UnicodeError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": f"响应不是 JSON: {exc}"}
    if isinstance(doc, dict):
        doc.setdefault("ok", True)
        return doc
    return {"ok": True, "raw": payload[:500].decode("utf-8", errors="replace")}


def socket_ready(socket_path: Path) -> bool:
    """Return True when the socket file exists and accepts a connection."""
    path = Path(socket_path)
    if not path.exists():
        return False
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        probe.settimeout(0.5)
        probe.connect(str(path))
        return True
    except OSError:
        return False
    finally:
        probe.close()
