#!/usr/bin/env python3
"""Read-only process/socket/environment sampler for HTTP 502 incidents."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import platform
import re
import shutil
import signal
import socket
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


# A single process-local salt prevents the sampler from writing raw public
# addresses while keeping hashes comparable across its sampling window.
_PEER_SALT = os.urandom(32)
_PEER_SALT_FINGERPRINT = hashlib.sha256(_PEER_SALT).hexdigest()


def _run(cmd: List[str], timeout: float = 1.5) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:
        return ""


def _pid_rows() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    ps = _run(["ps", "-axo", "pid=,comm=,command="])
    markers = (
        "follow_engine",
        "odds_snapshot_watch",
        "member_arsenal_watch",
        "settlement_importer",
        "hd_network_telemetry_sampler.py",
        "MacPacketTunnel",
        "WidgetExtension",
    )
    for line in ps.splitlines():
        match = re.match(r"\s*(\d+)\s+([^\s]+)\s+(.*)$", line)
        if not match:
            continue
        pid, comm, command = int(match.group(1)), match.group(2), match.group(3)
        if not any(marker.lower() in (comm + " " + command).lower() for marker in markers):
            continue
        combined = (comm + " " + command).lower()
        # Match the actual tunnel executable names.  A broad "Shadowrocket"
        # substring would also classify the sampler's own shell command when
        # this function is invoked from a heredoc or test runner.
        if "macpackettunnel" in combined:
            role = "shadowrocket"
        elif "widgetextension" in combined:
            role = "shadowrocket_aux"
        else:
            role = "worker"
        row: Dict[str, Any] = {"pid": pid, "name": comm, "role": role, "command_hash": _hash(command), "thread_count": None, "fd_count": None}
        thread_out = _run(["ps", "-M", "-p", str(pid)], timeout=1.0)
        if thread_out:
            row["thread_count"] = max(0, len(thread_out.splitlines()) - 1)
        if shutil.which("lsof"):
            fd_out = _run(["lsof", "-p", str(pid)], timeout=1.5)
            if fd_out:
                row["fd_count"] = max(0, len(fd_out.splitlines()) - 1)
        out.append(row)
    return out


def _hash(value: Any) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8", "ignore")).hexdigest()


def _peer_hash(value: Any) -> str:
    return hashlib.sha256(_PEER_SALT + str(value or "").encode("utf-8", "ignore")).hexdigest()


def _split_endpoint(value: str) -> tuple[str, Any]:
    """Split lsof's numeric endpoint into host and port without DNS."""

    value = value.strip()
    if value.startswith("[") and "]" in value:
        host, _, port = value[1:].partition("]")
        port = port.lstrip(":")
    else:
        host, sep, port = value.rpartition(":")
        if not sep:
            return value, None
    try:
        return host, int(port) if port.isdigit() else port or None
    except Exception:
        return host, port or None


def _ip_scope(value: str) -> str:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return "unknown"
    if ip.version == 4 and ip in ipaddress.ip_network("198.18.0.0/15"):
        return "fake"
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
        return "private"
    return "public"


def _parse_lsof_connections(pid: int) -> List[Dict[str, Any]]:
    """Return numeric TCP/UDP sockets for one process, never resolving DNS."""

    if not shutil.which("lsof"):
        return []
    text = _run(["lsof", "-nP", "-a", "-p", str(pid), "-i"], timeout=1.5)
    rows: List[Dict[str, Any]] = []
    # NAME is the final lsof column and contains e.g.
    # ``TCP 127.0.0.1:1082->27.44.142.81:22311 (ESTABLISHED)``.
    pattern = re.compile(
        r"\b(?P<proto>TCP|UDP)\s+"
        r"(?P<endpoint>[^\s]+)\s*"
        r"(?:\((?P<state>[^)]+)\))?\s*$",
        re.IGNORECASE,
    )
    for line in text.splitlines()[1:]:
        match = pattern.search(line.strip())
        if not match:
            continue
        endpoint = match.group("endpoint")
        local_raw, arrow, remote_raw = endpoint.partition("->")
        local_host, local_port = _split_endpoint(local_raw)
        remote_host, remote_port = _split_endpoint(remote_raw) if remote_raw else (None, None)
        state = (match.group("state") or "UNKNOWN").upper()
        scope = _ip_scope(remote_host or "") if remote_host else "unknown"
        rows.append(
            {
                "pid": int(pid),
                "protocol": match.group("proto").upper(),
                "local_address": local_host,
                "local_port": local_port,
                "remote_port": remote_port,
                "remote_peer_hash": _peer_hash(remote_host) if remote_host else None,
                "remote_scope": scope,
                "socket_state": state,
            }
        )
    return rows


def _connection_summary(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "connections": rows,
        "connection_count": len(rows),
        "ESTABLISHED": sum(1 for row in rows if row.get("socket_state") == "ESTABLISHED"),
        "SYN_SENT": sum(1 for row in rows if row.get("socket_state") == "SYN_SENT"),
        "target_host_hash": None,
    }


def _socket_states() -> Dict[str, int]:
    states = {"ESTABLISHED": 0, "TIME_WAIT": 0, "CLOSE_WAIT": 0, "SYN_SENT": 0, "LISTEN": 0}
    if shutil.which("netstat"):
        text = _run(["netstat", "-an"], timeout=2.0)
        for line in text.splitlines():
            upper = line.upper()
            for state in states:
                if re.search(r"\b" + re.escape(state) + r"\b", upper):
                    states[state] += 1
                    break
    return states


def _environment(processes: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    route = _run(["route", "-n", "get", "default"], timeout=1.0) if shutil.which("route") else ""
    resolver = _run(["scutil", "--dns"], timeout=1.5) if shutil.which("scutil") else ""
    proxy = _run(["scutil", "--proxy"], timeout=1.5) if shutil.which("scutil") else ""
    utun = _run(["ifconfig"], timeout=1.0) if shutil.which("ifconfig") else ""
    return {
        "default_route_hash": _hash(route[-4000:]) if route else None,
        "default_route_tail": route[-1000:] if route else None,
        "resolver_hash": _hash(resolver[-4000:]) if resolver else None,
        "system_proxy_hash": _hash(proxy[-2000:]) if proxy else None,
        "utun_present": bool(re.search(r"^utun\d+:", utun, re.MULTILINE)),
        "shadowrocket_pids": [row["pid"] for row in (processes if processes is not None else _pid_rows()) if row.get("role") == "shadowrocket"],
    }


def sample_once() -> Dict[str, Any]:
    load = None
    try:
        load = list(os.getloadavg())
    except Exception:
        pass
    timestamp = time.time()
    processes = _pid_rows()
    shadowrocket_rows: List[Dict[str, Any]] = []
    app_fake_rows: List[Dict[str, Any]] = []
    shadowrocket_pids = [row["pid"] for row in processes if row.get("role") == "shadowrocket"]
    worker_pids = [row["pid"] for row in processes if row.get("role") == "worker"]
    for pid in shadowrocket_pids:
        for connection in _parse_lsof_connections(pid):
            # Loopback proxy listeners and private tunnel peers are retained
            # with scope metadata, while public addresses remain hashed.
            connection["timestamp"] = timestamp
            shadowrocket_rows.append(connection)
    for pid in worker_pids:
        for connection in _parse_lsof_connections(pid):
            remote_scope = connection.get("remote_scope")
            if remote_scope == "fake" and connection.get("remote_port") == 443:
                connection["timestamp"] = timestamp
                app_fake_rows.append(connection)
    outbound = [row for row in shadowrocket_rows if row.get("remote_scope") == "public"]
    return {
        "timestamp": timestamp,
        "monotonic_ns": time.monotonic_ns(),
        "pid": os.getpid(),
        "platform": platform.platform(),
        "load_average": load,
        "processes": processes,
        "socket_states": _socket_states(),
        "environment": _environment(processes),
        "peer_hash_salt_fingerprint": _PEER_SALT_FINGERPRINT,
        "shadowrocket_socket_connections": shadowrocket_rows,
        "shadowrocket_outbound": {**_connection_summary(outbound), "pid": shadowrocket_pids[0] if len(shadowrocket_pids) == 1 else None},
        "app_fake_ip_connections": app_fake_rows,
    }


def rotate(path: Path, max_bytes: int) -> None:
    try:
        if path.exists() and path.stat().st_size >= max_bytes:
            backup = path.with_suffix(path.suffix + ".1")
            try:
                backup.unlink()
            except FileNotFoundError:
                pass
            path.replace(backup)
    except Exception:
        pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default=str(Path.cwd() / "data"))
    parser.add_argument("--interval", type=float, default=0.5)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--max-bytes", type=int, default=5 * 1024 * 1024)
    args = parser.parse_args()
    path = Path(args.data_dir).expanduser().resolve() / "network_sampler.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            rotate(path, max(1024, int(args.max_bytes)))
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(sample_once(), ensure_ascii=False, separators=(",", ":")) + "\n")
        except Exception:
            pass
        if args.once:
            return 0
        time.sleep(max(0.25, float(args.interval)))


if __name__ == "__main__":
    raise SystemExit(main())
