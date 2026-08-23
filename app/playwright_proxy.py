import socket
import struct
import threading
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse


def _read_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("socket_closed")
        buf += chunk
    return buf


def _parse_socks5_upstream(proxy: str) -> Optional[Tuple[str, int, str, str]]:
    """
    返回 (host, port, username, password)。
    仅在 socks5 且包含鉴权信息时返回；否则返回 None。
    支持：
      - ip|port|user|pass|...（忽略后面的到期日/备注）
      - socks5://user:pass@ip:port
    """
    proxy = str(proxy or "").strip()
    if not proxy:
        return None

    if "|" in proxy:
        parts = [p.strip() for p in proxy.split("|")]
        while parts and not parts[-1]:
            parts.pop()
        if len(parts) >= 4 and parts[0] and parts[1]:
            host = parts[0]
            try:
                port = int(parts[1])
            except Exception:
                return None
            user = parts[2] if len(parts) >= 3 else ""
            pwd = parts[3] if len(parts) >= 4 else ""
            if user and pwd:
                return host, port, user, pwd
        return None

    u = urlparse(proxy)
    scheme = (u.scheme or "").lower()
    if scheme not in ("socks5", "socks5h"):
        return None
    if not (u.hostname and u.port and u.username and u.password):
        return None
    return str(u.hostname), int(u.port), str(u.username), str(u.password)


def _build_socks5_noauth_server(proxy: str) -> str:
    """
    将各种输入归一化为 socks5://host:port（无鉴权，仅用于 Playwright server 字段）。
    """
    proxy = str(proxy or "").strip()
    if not proxy:
        return ""
    if "|" in proxy:
        parts = [p.strip() for p in proxy.split("|")]
        while parts and not parts[-1]:
            parts.pop()
        if len(parts) >= 2 and parts[0] and parts[1]:
            return f"socks5://{parts[0]}:{parts[1]}"
        return proxy
    u = urlparse(proxy)
    if u.scheme and u.hostname:
        scheme = u.scheme.lower()
        if scheme == "socks5h":
            scheme = "socks5"
        if u.port:
            return f"{scheme}://{u.hostname}:{u.port}"
        return f"{scheme}://{u.hostname}"
    return proxy


def _relay(a: socket.socket, b: socket.socket) -> None:
    try:
        while True:
            data = a.recv(65536)
            if not data:
                break
            b.sendall(data)
    except Exception:
        pass
    finally:
        try:
            b.shutdown(socket.SHUT_WR)
        except Exception:
            pass


def _handle_one_client(client: socket.socket, upstream: Tuple[str, int, str, str]) -> None:
    up_host, up_port, up_user, up_pwd = upstream
    upstream_sock: Optional[socket.socket] = None
    try:
        # 1) client greeting
        ver, nmethods = _read_exact(client, 2)
        if ver != 5:
            return
        methods = _read_exact(client, nmethods)
        if b"\x00" not in methods:
            client.sendall(b"\x05\xff")
            return
        client.sendall(b"\x05\x00")

        # 2) client request
        ver, cmd, rsv, atyp = _read_exact(client, 4)
        if ver != 5 or cmd != 1:
            # command not supported
            client.sendall(b"\x05\x07\x00\x01\x00\x00\x00\x00\x00\x00")
            return

        addr_bytes = b""
        if atyp == 1:  # ipv4
            addr_bytes = _read_exact(client, 4)
        elif atyp == 3:  # domain
            ln = _read_exact(client, 1)[0]
            addr_bytes = bytes([ln]) + _read_exact(client, ln)
        elif atyp == 4:  # ipv6
            addr_bytes = _read_exact(client, 16)
        else:
            client.sendall(b"\x05\x08\x00\x01\x00\x00\x00\x00\x00\x00")
            return
        port_bytes = _read_exact(client, 2)

        # 3) connect to upstream socks5 (username/password)
        upstream_sock = socket.create_connection((up_host, up_port), timeout=10)
        upstream_sock.settimeout(20)
        upstream_sock.sendall(b"\x05\x01\x02")
        _ver, method = _read_exact(upstream_sock, 2)
        if _ver != 5 or method != 2:
            raise RuntimeError("upstream_method_not_username_password")
        u = up_user.encode("utf-8", "ignore")[:255]
        p = up_pwd.encode("utf-8", "ignore")[:255]
        upstream_sock.sendall(b"\x01" + bytes([len(u)]) + u + bytes([len(p)]) + p)
        _aver, status = _read_exact(upstream_sock, 2)
        if _aver != 1 or status != 0:
            raise RuntimeError("upstream_auth_failed")

        # 4) upstream connect request
        upstream_sock.sendall(b"\x05\x01\x00" + bytes([atyp]) + addr_bytes + port_bytes)
        rep = _read_exact(upstream_sock, 4)
        rver, rcode, rrsv, ratyp = rep[0], rep[1], rep[2], rep[3]
        if rver != 5 or rcode != 0:
            # general failure to client
            client.sendall(b"\x05\x01\x00\x01\x00\x00\x00\x00\x00\x00")
            return
        # drain bind addr/port in upstream reply
        if ratyp == 1:
            _read_exact(upstream_sock, 4)
        elif ratyp == 3:
            ln2 = _read_exact(upstream_sock, 1)[0]
            _read_exact(upstream_sock, ln2)
        elif ratyp == 4:
            _read_exact(upstream_sock, 16)
        _read_exact(upstream_sock, 2)

        # 5) success reply to client (bind=0.0.0.0:0)
        client.sendall(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")

        # 6) relay
        upstream_sock.settimeout(None)
        client.settimeout(None)
        t1 = threading.Thread(target=_relay, args=(client, upstream_sock), daemon=True)
        t2 = threading.Thread(target=_relay, args=(upstream_sock, client), daemon=True)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
    except Exception:
        try:
            client.sendall(b"\x05\x01\x00\x01\x00\x00\x00\x00\x00\x00")
        except Exception:
            pass
    finally:
        try:
            client.close()
        except Exception:
            pass
        try:
            if upstream_sock:
                upstream_sock.close()
        except Exception:
            pass


@dataclass(frozen=True)
class _UpstreamKey:
    host: str
    port: int
    username: str
    password: str


class Socks5AuthBridgeManager:
    """
    将“上游带鉴权的 socks5”桥接为“本地无鉴权 socks5”，供 Playwright 使用。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._servers: Dict[_UpstreamKey, Tuple[str, socket.socket]] = {}

    def get_local_server(self, upstream: Tuple[str, int, str, str]) -> str:
        key = _UpstreamKey(upstream[0], upstream[1], upstream[2], upstream[3])
        with self._lock:
            existing = self._servers.get(key)
            if existing:
                return existing[0]

            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind(("127.0.0.1", 0))
            srv.listen(128)
            host, port = srv.getsockname()
            local = f"socks5://{host}:{port}"

            def accept_loop() -> None:
                while True:
                    try:
                        c, _ = srv.accept()
                    except Exception:
                        break
                    t = threading.Thread(target=_handle_one_client, args=(c, upstream), daemon=True)
                    t.start()

            threading.Thread(target=accept_loop, daemon=True).start()
            self._servers[key] = (local, srv)
            return local


_BRIDGE = Socks5AuthBridgeManager()


def build_playwright_proxy(proxy: str) -> Optional[Dict[str, Any]]:
    """
    返回可直接传给 Playwright 的 proxy dict。

    关键：Chromium/Playwright 不支持 socks5 代理鉴权。
    因此如果检测到 socks5 + username/password，会自动走本地桥接。
    """
    proxy = str(proxy or "").strip()
    if not proxy:
        return None

    upstream = _parse_socks5_upstream(proxy)
    if upstream:
        local = _BRIDGE.get_local_server(upstream)
        return {"server": local}

    # 无鉴权 socks5：直接用
    if str(proxy).strip().lower().startswith(("socks5://", "socks5h://")) or "|" in proxy:
        return {"server": _build_socks5_noauth_server(proxy)}

    # http/https：允许 username/password@，但 Playwright 的推荐写法是分字段
    u = urlparse(proxy)
    if u.scheme and u.hostname:
        server = f"{u.scheme}://{u.hostname}:{u.port}" if u.port else f"{u.scheme}://{u.hostname}"
        out: Dict[str, Any] = {"server": server}
        if u.username:
            out["username"] = u.username
        if u.password:
            out["password"] = u.password
        return out

    return {"server": proxy}
