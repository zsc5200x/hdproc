import argparse
import http.client
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from portable_env import apply_playwright_browsers_path


def resolve_app_root(default_file: Optional[Path] = None) -> Path:
    env_root = (os.environ.get("HDZZ_APP_DIR") or "").strip()
    if env_root:
        return Path(env_root).resolve()
    base = Path(default_file) if default_file is not None else Path(__file__)
    return base.resolve().parent


def is_frozen_runtime() -> bool:
    return bool(getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"))


ROOT = resolve_app_root(Path(__file__))
SOURCE_APP_DIR = Path(__file__).resolve().parent
SOURCE_REPO_ROOT = SOURCE_APP_DIR.parent
RUN_DIR = ROOT / ".run"
LOG_DIR = Path(os.environ.get("HDZZ_LOG_DIR") or (RUN_DIR / "logs"))
PID_FILE = RUN_DIR / "pids.json"
CORE_PID_FILE = RUN_DIR / "pids_core.json"
FOLLOW_ENGINE_PID_FILE = RUN_DIR / "follow_engine.pid.json"
SNAPSHOT_WATCH_PID_FILE = RUN_DIR / "odds_snapshot_watch.pid.json"
TELEGRAM_SIGNAL_PID_FILE = RUN_DIR / "telegram_signal.pid.json"
PS3838_PID_FILE = ROOT / "data" / "ps3838" / "worker.pid"
PS3838_APP_DIR = Path(os.environ.get("HDZZ_PS3838_DIR") or (SOURCE_REPO_ROOT / "ps3838")).expanduser()
PS3838_HEALTH_URL = os.environ.get("HDZZ_PS3838_HEALTH_URL") or ""


def ps3838_health_url() -> str:
    explicit = str(os.environ.get("HDZZ_PS3838_HEALTH_URL") or PS3838_HEALTH_URL).strip()
    if explicit:
        return explicit.rstrip("/")
    host = str(os.environ.get("DASHBOARD_HOST") or "127.0.0.1").strip() or "127.0.0.1"
    port: Any = os.environ.get("DASHBOARD_PORT") or 8787
    config_path = Path(os.environ.get("PS3838_CONFIG") or (PS3838_APP_DIR / "config.json")).expanduser()
    try:
        if config_path.is_file():
            payload = json.loads(config_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                host = str(payload.get("dashboard_host") or host).strip() or host
                port = payload.get("dashboard_port") or port
    except (OSError, TypeError, ValueError):
        pass
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1"
    try:
        port = int(port)
    except (TypeError, ValueError):
        port = 8787
    return f"http://{host}:{port}/api/health"


@dataclass
class WorkerSpec:
    name: str
    args: List[str]


def _normalize_worker_args(args: List[str]) -> List[str]:
    out = list(args)
    while out and out[0] == "-u":
        out = out[1:]
    if out and out[0].endswith(".py"):
        out = out[1:]
    return out


def build_worker_process_command(
    worker: WorkerSpec,
    *,
    executable: Optional[str] = None,
    frozen: Optional[bool] = None,
) -> List[str]:
    exe = str(executable or sys.executable)
    frozen_runtime = is_frozen_runtime() if frozen is None else bool(frozen)
    if frozen_runtime:
        return [exe, "--hdzz-worker", worker.name] + _normalize_worker_args(worker.args)
    args = list(worker.args)
    if args:
        if len(args) >= 2 and args[0] == "-u" and str(args[1]).endswith(".py") and not Path(str(args[1])).is_absolute():
            args[1] = str((SOURCE_APP_DIR / str(args[1])).resolve())
        elif str(args[0]).endswith(".py") and not Path(str(args[0])).is_absolute():
            args[0] = str((SOURCE_APP_DIR / str(args[0])).resolve())
    return [exe] + args


def now_iso() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def ensure_dirs() -> None:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def _write_json_atomic(path: Path, payload: Dict[str, Any]) -> None:
    ensure_dirs()
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)


def read_pid_info() -> Optional[Dict[str, Any]]:
    if not PID_FILE.exists():
        return None
    try:
        return json.loads(PID_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def write_pid_info(payload: Dict[str, Any]) -> None:
    _write_json_atomic(PID_FILE, payload)


def read_core_pid_info() -> Optional[Dict[str, Any]]:
    if not CORE_PID_FILE.exists():
        return None
    try:
        return json.loads(CORE_PID_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def write_core_pid_info(payload: Dict[str, Any]) -> None:
    _write_json_atomic(CORE_PID_FILE, payload)


def read_follow_engine_pid_info() -> Optional[Dict[str, Any]]:
    if not FOLLOW_ENGINE_PID_FILE.exists():
        return None
    try:
        return json.loads(FOLLOW_ENGINE_PID_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def write_follow_engine_pid_info(payload: Dict[str, Any]) -> None:
    _write_json_atomic(FOLLOW_ENGINE_PID_FILE, payload)


def write_snapshot_watch_pid_info(payload: Dict[str, Any]) -> None:
    _write_json_atomic(SNAPSHOT_WATCH_PID_FILE, payload)


def read_telegram_signal_pid_info() -> Optional[Dict[str, Any]]:
    if not TELEGRAM_SIGNAL_PID_FILE.exists():
        return None
    try:
        return json.loads(TELEGRAM_SIGNAL_PID_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def write_telegram_signal_pid_info(payload: Dict[str, Any]) -> None:
    _write_json_atomic(TELEGRAM_SIGNAL_PID_FILE, payload)


def is_port_bindable(host: str, port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((str(host), int(port)))
        return True
    except OSError:
        return False


def pick_bindable_port(host: str, preferred_port: int) -> int:
    preferred = int(preferred_port)
    if preferred > 0 and is_port_bindable(host, preferred):
        return preferred

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((str(host), 0))
        return int(sock.getsockname()[1])


def is_pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            try:
                stat = subprocess.check_output(["ps", "-p", str(pid), "-o", "stat="], stderr=subprocess.DEVNULL, timeout=2)
                state = str(stat.decode("utf-8", "ignore") or "").strip().upper()
                if state.startswith("Z"):
                    return False
            except Exception:
                pass
            return True
        except Exception:
            return False
    # Windows: use OpenProcess/GetExitCodeProcess via ctypes to avoid external deps.
    try:
        import ctypes  # type: ignore

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32  # type: ignore
        h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, int(pid))
        if not h:
            return False
        code = ctypes.c_ulong(0)
        ok = kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        kernel32.CloseHandle(h)
        if not ok:
            return False
        return int(code.value) == STILL_ACTIVE
    except Exception:
        return False


def _windows_get_process_info(pid: int) -> Dict[str, str]:
    """
    Best-effort process info via PowerShell CIM.
    Returns dict keys: name, cmdline, exe.
    """
    try:
        cmd = (
            f"$p=Get-CimInstance Win32_Process -Filter \"ProcessId={int(pid)}\";"
            "if(-not $p){exit 2};"
            "$o=[pscustomobject]@{name=$p.Name;cmd=$p.CommandLine;exe=$p.ExecutablePath};"
            "$o|ConvertTo-Json -Compress"
        )
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", cmd],
            stderr=subprocess.DEVNULL,
            timeout=3,
        )
        obj = json.loads(out.decode("utf-8", "ignore") or "{}")
        if isinstance(obj, dict):
            return {
                "name": str(obj.get("name") or ""),
                "cmd": str(obj.get("cmd") or ""),
                "exe": str(obj.get("exe") or ""),
            }
    except Exception:
        pass
    return {}


_WORKER_MARKERS = [
    "ag_wmc_watch.py",
    "signal_forwarder.py",
    "signal_puller.py",
    "webui_server.py",
    "member_arsenal_watch.py",
    "odds_snapshot_watch.py",
    "settlement_importer.py",
    "hd_network_telemetry_sampler.py",
    "follow_engine.py",
    "telegram_bot.py",
    "telegram_signal_watch.py",
    "ps3838_watch.py",
]


def _is_probably_hdydf_worker_pid(pid: int) -> bool:
    """
    Avoid killing unrelated processes when PID was reused (very common on Windows).
    We only treat a PID as "ours" if its command line contains one of our worker scripts.
    """
    if pid <= 0:
        return False
    if os.name != "nt":
        return True
    info = _windows_get_process_info(pid)
    cmdline = (info.get("cmd") or "").lower()
    if not cmdline:
        return False
    return any(m.lower() in cmdline for m in _WORKER_MARKERS)


def _is_runtime_playwright_browser_command(command: str, root: Path = ROOT) -> bool:
    cmd = str(command or "")
    if not cmd:
        return False
    root_path = str(Path(root).resolve())
    own_markers = [
        str((Path(root_path) / "state" / "_pw").resolve()),
        str((Path(root_path) / "data" / "_pw_member").resolve()),
        str((Path(root_path) / ".run" / "ms-playwright").resolve()),
    ]
    return any(marker in cmd for marker in own_markers)


def _runtime_playwright_browser_pids(root: Path = ROOT) -> List[int]:
    if os.name == "nt":
        return []
    try:
        out = subprocess.check_output(
            ["ps", "ax", "-o", "pid=,command="],
            stderr=subprocess.DEVNULL,
            timeout=3,
        )
    except Exception:
        return []
    pids: List[int] = []
    for raw_line in out.decode("utf-8", "ignore").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        try:
            pid = int(parts[0])
        except Exception:
            continue
        if pid == os.getpid():
            continue
        if _is_runtime_playwright_browser_command(parts[1], root):
            pids.append(pid)
    return pids


def cleanup_runtime_playwright_browsers(force: bool) -> None:
    for pid in _runtime_playwright_browser_pids(ROOT):
        if not is_pid_running(pid):
            continue
        print(f"Stop runtime browser PID={pid}")
        stop_pid(pid, force=bool(force))


def stop_ps3838_worker(force: bool) -> bool:
    """停止 WebUI 独立启动的平博只读 worker，并清理其 PID 文件。"""
    try:
        pid = int(PS3838_PID_FILE.read_text(encoding="utf-8").strip()) if PS3838_PID_FILE.exists() else 0
    except Exception:
        pid = 0
    if not pid:
        return False
    had_process = is_pid_running(pid)
    if had_process and _is_probably_hdydf_worker_pid(pid):
        print(f"Stop PS3838 watcher PID={pid}")
        stop_pid(pid, force=bool(force))
        deadline = time.time() + 12.0
        while time.time() < deadline and is_pid_running(pid):
            time.sleep(0.2)
        if is_pid_running(pid) and not force:
            print(f"Force stop PS3838 watcher PID={pid}")
            stop_pid(pid, force=True)
    try:
        PS3838_PID_FILE.unlink(missing_ok=True)
    except Exception:
        pass
    return had_process


def stop_pid(pid: int, force: bool) -> None:
    if not is_pid_running(pid):
        return

    if os.name == "nt":
        cmd = ["taskkill", "/PID", str(pid), "/T"]
        if force:
            cmd.append("/F")
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        return

    try:
        os.kill(pid, signal.SIGTERM)
    except Exception:
        return
    deadline = time.time() + 8.0
    while time.time() < deadline:
        if not is_pid_running(pid):
            return
        time.sleep(0.2)
    if force:
        try:
            os.kill(pid, signal.SIGKILL)
        except Exception:
            pass


def workers(host: str, port: int, backend_interval: int, member_interval: int) -> List[WorkerSpec]:
    out: List[WorkerSpec] = []
    out.append(
        WorkerSpec(
            name="ag_wmc_watch",
            args=[
                "-u",
                "ag_wmc_watch.py",
                "--config",
                "config.json",
                "--timeout",
                "30",
                "--interval",
                str(int(backend_interval)),
                "--only-orders",
            ],
        )
    )
    out.append(
        WorkerSpec(
            name="ook_signal_forwarder",
            args=[
                "-u",
                str((SOURCE_REPO_ROOT / "ook" / "signal_forwarder.py").resolve()),
                "--orders-dir",
                "orders",
                "--config",
                "config.json",
                "--state",
                ".run/ook_signal_forwarder_state.json",
            ],
        )
    )
    out.append(
        WorkerSpec(
            name="ook_signal_puller",
            args=[
                "-u",
                str((SOURCE_REPO_ROOT / "ook" / "signal_puller.py").resolve()),
                "--orders-dir",
                "orders",
                "--config",
                "config.json",
                "--state",
                ".run/ook_signal_puller_state.json",
            ],
        )
    )
    out.append(
        WorkerSpec(
            name="webui_server",
            args=[
                "-u",
                "webui_server.py",
                "--host",
                host,
                "--port",
                str(int(port)),
                "--orders-dir",
                "orders",
                "--webui-dir",
                "webui",
                "--state-dir",
                "state",
                "--config",
                "config.json",
                "--data-dir",
                "data",
            ],
        )
    )
    out.append(
        WorkerSpec(
            name="member_arsenal_watch",
            args=[
                "-u",
                "member_arsenal_watch.py",
                "--data-dir",
                "data",
                "--interval",
                str(int(member_interval)),
            ],
        )
    )
    out.append(_snapshot_watch_worker_spec())
    out.append(
        WorkerSpec(
            name="settlement_importer",
            args=[
                "-u",
                "settlement_importer.py",
                "--data-dir",
                "data",
                "--interval",
                "60",
            ],
        )
    )
    out.append(
        WorkerSpec(
            name="network_telemetry_sampler",
            args=[
                "-u",
                "tools/hd_network_telemetry_sampler.py",
                "--data-dir",
                "data",
                "--interval",
                os.environ.get("HDZZ_NETWORK_SAMPLER_INTERVAL", "0.5").strip() or "0.5",
            ],
        )
    )
    # 跟单引擎：为了降低“分单/重试排队导致延迟”，默认更高频轮询、更短网络超时（可用环境变量覆盖）
    follow_timeout = os.environ.get("FOLLOW_TIMEOUT", "12").strip() or "12"
    follow_interval = os.environ.get("FOLLOW_INTERVAL", "0.2").strip() or "0.2"
    follow_args = [
        "-u",
        "follow_engine.py",
        "--orders-dir",
        "orders",
        "--data-dir",
        "data",
        "--timeout",
        str(int(float(follow_timeout))),
        "--interval",
        str(float(follow_interval)),
    ]
    out.append(WorkerSpec(name="follow_engine", args=follow_args))
    out.append(
        WorkerSpec(
            name="telegram_bot",
            args=[
                "-u",
                "telegram_bot.py",
                "--config",
                "config.json",
                "--data-dir",
                "data",
                "--orders-dir",
                "orders",
                "--state",
                ".run/tg_bot_state.json",
            ],
        )
    )
    return out


def _follow_engine_worker_spec() -> WorkerSpec:
    follow_timeout = os.environ.get("FOLLOW_TIMEOUT", "12").strip() or "12"
    follow_interval = os.environ.get("FOLLOW_INTERVAL", "0.2").strip() or "0.2"
    return WorkerSpec(
        name="follow_engine",
        args=[
            "-u",
            "follow_engine.py",
            "--orders-dir",
            "orders",
            "--data-dir",
            "data",
            "--timeout",
            str(int(float(follow_timeout))),
            "--interval",
            str(float(follow_interval)),
        ],
    )


def _snapshot_watch_worker_spec() -> WorkerSpec:
    """Build the one public-market worker required by the follow engine."""
    return WorkerSpec(
        name="odds_snapshot_watch",
        args=[
            "-u",
            "odds_snapshot_watch.py",
            "--data-dir",
            "data",
            "--orders-dir",
            "orders",
            "--interval",
            os.environ.get("ODDS_SNAPSHOT_INTERVAL", "1.0").strip() or "1.0",
            "--active-seconds",
            os.environ.get("ODDS_SNAPSHOT_ACTIVE_SECONDS", "120").strip() or "120",
            "--max-workers",
            os.environ.get("ODDS_SNAPSHOT_MAX_WORKERS", "4").strip() or "4",
            "--target-interval-ms",
            os.environ.get("ODDS_SNAPSHOT_TARGET_INTERVAL_MS", "400").strip() or "400",
            "--market-deadline-ms",
            os.environ.get("ODDS_SNAPSHOT_MARKET_DEADLINE_MS", DEFAULT_SNAPSHOT_MARKET_DEADLINE_MS).strip()
            or DEFAULT_SNAPSHOT_MARKET_DEADLINE_MS,
            "--public-timeout-s",
            os.environ.get("ODDS_SNAPSHOT_PUBLIC_TIMEOUT_S", "2").strip() or "2",
            "--hedge-delay-ms",
            os.environ.get("ODDS_SNAPSHOT_HEDGE_DELAY_MS", "350").strip() or "350",
            "--scheduler-tick-ms",
            os.environ.get("ODDS_SNAPSHOT_SCHEDULER_TICK_MS", "50").strip() or "50",
        ],
    )


def _telegram_signal_worker_spec() -> WorkerSpec:
    return WorkerSpec(
        name="telegram_signal_watch",
        args=[
            "-u",
            "telegram_signal_watch.py",
            "--settings",
            "data/telegram_signal.json",
            "--secrets",
            "data/telegram_signal_secrets.json",
            "--state",
            ".run/telegram_signal_state.json",
            "--cursor-state",
            ".run/telegram_signal_cursor.json",
            "--health",
            ".run/telegram_signal_health.json",
            "--events",
            "data/telegram_signal_events.jsonl",
            "--orders-dir",
            "orders",
        ],
    )


def core_workers(backend_interval: int, member_interval: int) -> List[WorkerSpec]:
    out: List[WorkerSpec] = []
    out.append(
        WorkerSpec(
            name="ag_wmc_watch",
            args=[
                "-u",
                "ag_wmc_watch.py",
                "--config",
                "config.json",
                "--timeout",
                "30",
                "--interval",
                str(int(backend_interval)),
                "--only-orders",
            ],
        )
    )
    out.append(
        WorkerSpec(
            name="ook_signal_forwarder",
            args=[
                "-u",
                str((SOURCE_REPO_ROOT / "ook" / "signal_forwarder.py").resolve()),
                "--orders-dir",
                "orders",
                "--config",
                "config.json",
                "--state",
                ".run/ook_signal_forwarder_state.json",
            ],
        )
    )
    out.append(
        WorkerSpec(
            name="ook_signal_puller",
            args=[
                "-u",
                str((SOURCE_REPO_ROOT / "ook" / "signal_puller.py").resolve()),
                "--orders-dir",
                "orders",
                "--config",
                "config.json",
                "--state",
                ".run/ook_signal_puller_state.json",
            ],
        )
    )
    out.append(
        WorkerSpec(
            name="member_arsenal_watch",
            args=[
                "-u",
                "member_arsenal_watch.py",
                "--data-dir",
                "data",
                "--interval",
                str(int(member_interval)),
            ],
        )
    )
    out.append(_snapshot_watch_worker_spec())
    out.append(
        WorkerSpec(
            name="settlement_importer",
            args=[
                "-u",
                "settlement_importer.py",
                "--data-dir",
                "data",
                "--interval",
                "60",
            ],
        )
    )
    out.append(
        WorkerSpec(
            name="network_telemetry_sampler",
            args=[
                "-u",
                "tools/hd_network_telemetry_sampler.py",
                "--data-dir",
                "data",
                "--interval",
                os.environ.get("HDZZ_NETWORK_SAMPLER_INTERVAL", "0.5").strip() or "0.5",
            ],
        )
    )
    follow_timeout = os.environ.get("FOLLOW_TIMEOUT", "12").strip() or "12"
    follow_interval = os.environ.get("FOLLOW_INTERVAL", "0.2").strip() or "0.2"
    out.append(
        WorkerSpec(
            name="follow_engine",
            args=[
                "-u",
                "follow_engine.py",
                "--orders-dir",
                "orders",
                "--data-dir",
                "data",
                "--timeout",
                str(int(float(follow_timeout))),
                "--interval",
                str(float(follow_interval)),
            ],
        )
    )
    out.append(
        WorkerSpec(
            name="telegram_bot",
            args=[
                "-u",
                "telegram_bot.py",
                "--config",
                "config.json",
                "--data-dir",
                "data",
                "--orders-dir",
                "orders",
                "--state",
                ".run/tg_bot_state.json",
            ],
        )
    )
    return out


def ps3838_enabled() -> bool:
    value = str(os.environ.get("HDZZ_PS3838_ENABLED") or "1").strip().lower()
    return value not in {"0", "false", "no", "off", "disabled"}


def ps3838_dashboard_socket() -> Path:
    """Resolve the dashboard's Unix socket (merged layout, no TCP port)."""
    config_path = Path(os.environ.get("PS3838_CONFIG") or (PS3838_APP_DIR / "config.json")).expanduser()
    run_dir = "var/run"
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and payload.get("run_dir"):
            run_dir = str(payload["run_dir"])
    except (OSError, ValueError):
        pass
    base = Path(run_dir).expanduser()
    if not base.is_absolute():
        base = PS3838_APP_DIR / base
    return base / "dashboard.sock"


class _UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: Path, timeout: float) -> None:
        super().__init__("localhost", timeout=max(0.2, float(timeout)))
        self._socket_path = str(socket_path)

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(self._socket_path)
        except BaseException:
            sock.close()
            raise
        self.sock = sock


def _ps3838_socket_health(socket_path: Path, timeout: float) -> Dict[str, Any]:
    connection = _UnixHTTPConnection(socket_path, timeout)
    try:
        connection.request("GET", "/api/health", headers={"Accept": "application/json"})
        response = connection.getresponse()
        raw = response.read().decode("utf-8", errors="replace")
        if response.status != 200:
            return {"ok": False, "error": f"HTTP {response.status}"}
        payload = json.loads(raw) if raw else {}
        return payload if isinstance(payload, dict) else {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        connection.close()


def ps3838_health(timeout: float = 2.5) -> Dict[str, Any]:
    socket_path = ps3838_dashboard_socket()
    if socket_path.exists():
        return _ps3838_socket_health(socket_path, timeout)
    request = urllib.request.Request(ps3838_health_url(), headers={"Accept": "application/json"}, method="GET")
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=max(0.5, float(timeout))) as response:
            raw = response.read().decode("utf-8", errors="replace")
            payload = json.loads(raw) if raw else {}
            return payload if isinstance(payload, dict) else {"ok": True}
    except urllib.error.URLError as exc:
        return {"ok": False, "error": str(getattr(exc, "reason", exc))}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def start_ps3838_stack(wait_seconds: float = 30.0) -> int:
    if not ps3838_enabled():
        print(f"[{now_iso()}] PS3838 已禁用（HDZZ_PS3838_ENABLED=0）")
        return 0
    script = PS3838_APP_DIR / "start.sh"
    if not script.is_file():
        print(f"[{now_iso()}] 跳过 PS3838：未找到 {script}")
        return 0
    if os.name == "nt":
        print(f"[{now_iso()}] 跳过 PS3838：当前系统不支持 .sh 启动脚本")
        return 0
    if ps3838_health().get("ok"):
        print(f"[{now_iso()}] PS3838 已在运行: {ps3838_health_url()}")
        return 0
    log_path = LOG_DIR / "ps3838_stack.out.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_handle = log_path.open("ab")
    try:
        process = subprocess.Popen(
            ["/bin/bash", str(script)],
            cwd=str(PS3838_APP_DIR),
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    finally:
        log_handle.close()
    print(f"[{now_iso()}] Start: ps3838_stack PID={process.pid} dir={PS3838_APP_DIR} log={log_path}")
    deadline = time.time() + max(1.0, float(wait_seconds))
    while time.time() < deadline:
        health = ps3838_health(timeout=1.5)
        if health.get("ok"):
            print(f"[{now_iso()}] PS3838 已就绪: {ps3838_health_url()}")
            return 0
        time.sleep(0.5)
    print(f"[{now_iso()}] PS3838 启动后未在 {wait_seconds:.0f}s 内就绪: {ps3838_health_url()}")
    return 1


def stop_ps3838_stack() -> int:
    if not ps3838_enabled():
        return 0
    script = PS3838_APP_DIR / "stop.sh"
    if not script.is_file():
        return 0
    if os.name == "nt":
        return 0
    print(f"[{now_iso()}] Stop PS3838 stack: {script}")
    try:
        result = subprocess.run(
            ["/bin/bash", str(script)],
            cwd=str(PS3838_APP_DIR),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=90,
        )
        tail = (result.stdout or "").strip().splitlines()[-8:]
        for line in tail:
            print(f"  {line}")
        return int(result.returncode)
    except subprocess.TimeoutExpired:
        print(f"[{now_iso()}] PS3838 stop.sh 超时")
        return 2
    except Exception as exc:
        print(f"[{now_iso()}] PS3838 stop.sh 失败: {exc}")
        return 1


def status_ps3838() -> int:
    health = ps3838_health()
    state = "RUNNING" if health.get("ok") else "STOPPED"
    socket_path = ps3838_dashboard_socket()
    endpoint = f"unix:{socket_path}" if socket_path.exists() else ps3838_health_url()
    print(f"{state:8} endpoint={endpoint} dir={PS3838_APP_DIR}")
    if health:
        print(json.dumps(health, ensure_ascii=False))
    return 0 if health.get("ok") else 1


def start_all(host: str, port: int, backend_interval: int, member_interval: int) -> int:
    ensure_dirs()
    env = dict(os.environ)
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    apply_playwright_browsers_path(env, ROOT)
    requested_port = int(port)
    selected_port = pick_bindable_port(host, requested_port)
    if selected_port != requested_port:
        print(f"[{now_iso()}] 端口 {requested_port} 已被占用，自动切换到空闲端口 {selected_port}")
    port = selected_port

    existing = read_pid_info()
    if existing:
        alive_all = [int(p) for p in (existing.get("pids") or []) if is_pid_running(int(p))]
        alive_ours = [p for p in alive_all if _is_probably_hdydf_worker_pid(int(p))]
        if alive_ours:
            print(f"[{now_iso()}] 检测到已有进程在运行（{len(alive_ours)}个），请先执行：python app.py stop 或 python app.py restart")
            return 2
        # stale pid file (PIDs exited or reused by other apps): auto-heal
        try:
            write_pid_info(
                {
                    "stale_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "host": existing.get("host") or "",
                    "port": int(existing.get("port") or 0),
                    "live": True,
                    "pids": [],
                }
            )
        except Exception:
            pass

    snapshot_pids = _alive_snapshot_watch_pids()
    global_snapshot_pids = _alive_global_worker_pids("odds_snapshot_watch.py")
    if global_snapshot_pids and not set(global_snapshot_pids).intersection(snapshot_pids):
        print(f"[{now_iso()}] 其它运行目录已有 odds_snapshot_watch（PID={global_snapshot_pids[0]}），拒绝启动混合运行时")
        return 2
    follow_pids = _alive_follow_engine_pids()[:1]
    global_follow_pids = _alive_global_worker_pids("follow_engine.py")
    if global_follow_pids and not set(global_follow_pids).intersection(follow_pids):
        print(f"[{now_iso()}] 其它运行目录已有 follow_engine（PID={global_follow_pids[0]}），拒绝启动混合运行时")
        return 2
    launched: List[tuple[WorkerSpec, subprocess.Popen]] = []
    worker_specs = workers(host, port, backend_interval, member_interval)
    for w in worker_specs:
        if w.name == "odds_snapshot_watch" and snapshot_pids:
            print(f"[{now_iso()}] Reuse: odds_snapshot_watch PID={snapshot_pids[0]}")
            continue
        if w.name == "follow_engine" and follow_pids:
            print(f"[{now_iso()}] Reuse: follow_engine PID={follow_pids[0]}")
            continue
        out_path = LOG_DIR / f"{w.name}.out.log"
        err_path = LOG_DIR / f"{w.name}.err.log"
        cmd = build_worker_process_command(w)
        print(f"[{now_iso()}] Start: {w.name} {' '.join(cmd)}")
        print(f"  logs: {out_path} | {err_path}")
        out_f = open(out_path, "ab")
        err_f = open(err_path, "ab")
        creationflags = 0
        if os.name == "nt":
            try:
                creationflags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
            except Exception:
                creationflags = 0
        popen_kwargs = {
            "cwd": str(ROOT),
            "stdout": out_f,
            "stderr": err_f,
            "env": env,
            "creationflags": creationflags,
        }
        # 让 worker 从启动命令的进程组中“脱离”，避免外层启动脚本/终端退出时把 worker 一并杀掉。
        if os.name != "nt":
            popen_kwargs["start_new_session"] = True
        p = subprocess.Popen(cmd, **popen_kwargs)
        launched.append((w, p))

    payload = {
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "host": host,
        "port": int(port),
        "live": True,
        "pids": [proc.pid for _spec, proc in launched] + snapshot_pids[:1] + follow_pids[:1],
    }
    write_pid_info(payload)
    try:
        started_at = time.strftime("%Y-%m-%dT%H:%M:%S")
        if snapshot_pids:
            write_snapshot_watch_pid_info({"recovered_at": started_at, "live": True, "pids": snapshot_pids[:1]})
        if follow_pids:
            write_follow_engine_pid_info({"recovered_at": started_at, "live": True, "pids": follow_pids[:1]})
        for spec, proc in launched:
            if spec.name == "odds_snapshot_watch":
                write_snapshot_watch_pid_info({"started_at": started_at, "live": True, "pids": [proc.pid]})
            if spec.name == "follow_engine":
                write_follow_engine_pid_info({"started_at": started_at, "live": True, "pids": [proc.pid]})
    except Exception:
        pass
    if start_ps3838_stack() != 0:
        print(f"[{now_iso()}] 警告：PS3838 未就绪，主项目仍已启动；请查看 .run/logs/ps3838_stack.out.log")
    print("")
    print(f"WebUI: http://{host}:{port}/")
    print(f"PID file: {PID_FILE}")
    return 0


def start_core(backend_interval: int, member_interval: int) -> int:
    ensure_dirs()
    env = dict(os.environ)
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    apply_playwright_browsers_path(env, ROOT)

    existing = read_core_pid_info()
    if existing:
        alive_all = [int(p) for p in (existing.get("pids") or []) if is_pid_running(int(p))]
        alive_ours = [p for p in alive_all if _is_probably_hdydf_worker_pid(int(p))]
        if alive_ours:
            print(f"[{now_iso()}] 检测到核心进程已在运行（{len(alive_ours)}个），请先执行：python app.py stop-core 或 python app.py restart-core")
            return 2
        try:
            write_core_pid_info({"stale_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "live": True, "pids": []})
        except Exception:
            pass

    snapshot_pids = _alive_snapshot_watch_pids()
    global_snapshot_pids = _alive_global_worker_pids("odds_snapshot_watch.py")
    if global_snapshot_pids and not set(global_snapshot_pids).intersection(snapshot_pids):
        print(f"[{now_iso()}] 其它运行目录已有 odds_snapshot_watch（PID={global_snapshot_pids[0]}），拒绝启动混合运行时")
        return 2
    follow_pids = _alive_follow_engine_pids()[:1]
    global_follow_pids = _alive_global_worker_pids("follow_engine.py")
    if global_follow_pids and not set(global_follow_pids).intersection(follow_pids):
        print(f"[{now_iso()}] 其它运行目录已有 follow_engine（PID={global_follow_pids[0]}），拒绝启动混合运行时")
        return 2
    launched: List[tuple[WorkerSpec, subprocess.Popen]] = []
    worker_specs = core_workers(backend_interval, member_interval)
    for w in worker_specs:
        if w.name == "odds_snapshot_watch" and snapshot_pids:
            print(f"[{now_iso()}] Reuse(core): odds_snapshot_watch PID={snapshot_pids[0]}")
            continue
        if w.name == "follow_engine" and follow_pids:
            print(f"[{now_iso()}] Reuse(core): follow_engine PID={follow_pids[0]}")
            continue
        out_path = LOG_DIR / f"{w.name}.out.log"
        err_path = LOG_DIR / f"{w.name}.err.log"
        cmd = build_worker_process_command(w)
        print(f"[{now_iso()}] Start(core): {w.name} {' '.join(cmd)}")
        print(f"  logs: {out_path} | {err_path}")
        out_f = open(out_path, "ab")
        err_f = open(err_path, "ab")
        creationflags = 0
        if os.name == "nt":
            try:
                creationflags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
            except Exception:
                creationflags = 0
        popen_kwargs = {
            "cwd": str(ROOT),
            "stdout": out_f,
            "stderr": err_f,
            "env": env,
            "creationflags": creationflags,
        }
        if os.name != "nt":
            popen_kwargs["start_new_session"] = True
        p = subprocess.Popen(cmd, **popen_kwargs)
        launched.append((w, p))

    payload = {
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "live": True,
        "pids": [proc.pid for _spec, proc in launched] + snapshot_pids[:1] + follow_pids[:1],
    }
    write_core_pid_info(payload)
    try:
        started_at = time.strftime("%Y-%m-%dT%H:%M:%S")
        if snapshot_pids:
            write_snapshot_watch_pid_info({"recovered_at": started_at, "live": True, "pids": snapshot_pids[:1]})
        if follow_pids:
            write_follow_engine_pid_info({"recovered_at": started_at, "live": True, "pids": follow_pids[:1]})
        for spec, proc in launched:
            if spec.name == "odds_snapshot_watch":
                write_snapshot_watch_pid_info({"started_at": started_at, "live": True, "pids": [proc.pid]})
            if spec.name == "follow_engine":
                write_follow_engine_pid_info({"started_at": started_at, "live": True, "pids": [proc.pid]})
    except Exception:
        pass
    print(f"CORE PID file: {CORE_PID_FILE}")
    return 0


def _alive_worker_pids(info: Optional[Dict[str, Any]]) -> List[int]:
    if not isinstance(info, dict):
        return []
    pids = [int(x) for x in (info.get("pids") or [])]
    return [pid for pid in pids if is_pid_running(pid) and _is_probably_hdydf_worker_pid(int(pid))]


def _alive_global_worker_pids(marker: str) -> List[int]:
    """Find a worker regardless of cwd/data-dir.

    Tauri uses the per-user runtime directory while a legacy source launcher
    may use ``app/data``. Per-directory PID files cannot detect that duplicate;
    this discovery complements the worker-level per-user advisory lock.
    """
    if os.name == "nt":
        return []
    try:
        output = subprocess.check_output(
            ["ps", "-axo", "pid=,command="],
            stderr=subprocess.DEVNULL,
            timeout=3,
        ).decode("utf-8", "ignore")
    except Exception:
        return []
    marker = str(marker or "").strip().lower()
    out: List[int] = []
    for line in output.splitlines():
        text = line.strip()
        if not text or marker not in text.lower():
            continue
        match = re.match(r"^(\d+)\s+", text)
        if not match:
            continue
        pid = int(match.group(1))
        if pid == os.getpid() or not is_pid_running(pid):
            continue
        if pid not in out:
            out.append(pid)
    return out


def _pid_command_line(pid: int) -> str:
    if os.name == "nt":
        return str(_windows_get_process_info(pid).get("cmd") or "")
    try:
        out = subprocess.check_output(
            ["ps", "-p", str(int(pid)), "-o", "command="],
            stderr=subprocess.DEVNULL,
            timeout=2,
        )
        return out.decode("utf-8", "ignore").strip()
    except Exception:
        return ""


def _alive_snapshot_watch_pids() -> List[int]:
    seen = set()
    out: List[int] = []
    for path in (SNAPSHOT_WATCH_PID_FILE, CORE_PID_FILE, PID_FILE):
        try:
            obj = json.loads(path.read_text(encoding="utf-8-sig") or "{}")
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        for raw_pid in obj.get("pids") or []:
            try:
                pid = int(raw_pid)
            except (TypeError, ValueError):
                continue
            if pid in seen or not is_pid_running(pid):
                continue
            seen.add(pid)
            command = _pid_command_line(pid).lower()
            if "odds_snapshot_watch.py" in command or "--hdzz-worker odds_snapshot_watch" in command:
                out.append(pid)
    try:
        output = subprocess.check_output(
            ["ps", "-axo", "pid=,command="],
            stderr=subprocess.DEVNULL,
            timeout=3,
        ).decode("utf-8", "ignore")
    except Exception:
        output = ""
    current_dir = ROOT.resolve()
    for line in output.splitlines():
        text = line.strip()
        if not text or "odds_snapshot_watch.py" not in text:
            continue
        match = re.match(r"^(\d+)\s+", text)
        if not match:
            continue
        try:
            pid = int(match.group(1))
        except (TypeError, ValueError):
            continue
        if pid in seen or not is_pid_running(pid):
            continue
        command = _pid_command_line(pid).lower()
        if "odds_snapshot_watch.py" not in command and "--hdzz-worker odds_snapshot_watch" not in command:
            continue
        try:
            cwd_output = subprocess.check_output(
                ["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
                stderr=subprocess.DEVNULL,
                timeout=2,
            ).decode("utf-8", "ignore")
            process_cwd = next(
                (item[1:].strip() for item in cwd_output.splitlines() if item.startswith("n")),
                "",
            )
            if process_cwd and Path(process_cwd).resolve() != current_dir:
                continue
        except Exception:
            pass
        seen.add(pid)
        out.append(pid)
    return out


def _alive_follow_engine_pids() -> List[int]:
    """Discover a live follow engine when its dedicated PID file is stale."""
    out: List[int] = []
    seen = set()
    try:
        output = subprocess.check_output(
            ["ps", "-axo", "pid=,command="],
            stderr=subprocess.DEVNULL,
            timeout=3,
        ).decode("utf-8", "ignore")
    except Exception:
        output = ""
    current_dir = ROOT.resolve()
    for line in output.splitlines():
        text = line.strip()
        if not text or "follow_engine.py" not in text:
            continue
        match = re.match(r"^(\d+)\s+", text)
        if not match:
            continue
        pid = int(match.group(1))
        if pid in seen or not is_pid_running(pid):
            continue
        try:
            cwd_output = subprocess.check_output(
                ["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
                stderr=subprocess.DEVNULL,
                timeout=2,
            ).decode("utf-8", "ignore")
            process_cwd = next(
                (item[1:].strip() for item in cwd_output.splitlines() if item.startswith("n")),
                "",
            )
            if process_cwd and Path(process_cwd).resolve() != current_dir:
                continue
        except Exception:
            pass
        seen.add(pid)
        out.append(pid)
    return out


def _merge_dedicated_worker_pids(pids: List[int]) -> List[int]:
    merged: List[int] = []
    seen = set()
    for pid in list(pids):
        pid_i = int(pid)
        if pid_i not in seen:
            seen.add(pid_i)
            merged.append(pid_i)

    follow_info = read_follow_engine_pid_info()
    if isinstance(follow_info, dict):
        for raw_pid in (follow_info.get("pids") or []):
            pid_i = int(raw_pid)
            if pid_i not in seen:
                seen.add(pid_i)
                merged.append(pid_i)
    try:
        snapshot_info = json.loads(SNAPSHOT_WATCH_PID_FILE.read_text(encoding="utf-8-sig") or "{}")
    except Exception:
        snapshot_info = {}
    if isinstance(snapshot_info, dict):
        for raw_pid in snapshot_info.get("pids") or []:
            pid_i = int(raw_pid)
            if pid_i not in seen:
                seen.add(pid_i)
                merged.append(pid_i)
    return merged


def start_snapshot_watch() -> int:
    """Ensure the public盘口 worker exists before the follow engine starts."""
    ensure_dirs()
    env = dict(os.environ)
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    apply_playwright_browsers_path(env, ROOT)

    alive = _alive_snapshot_watch_pids()
    global_alive = [pid for pid in _alive_global_worker_pids("odds_snapshot_watch.py") if pid not in alive]
    if global_alive:
        print(
            f"[{now_iso()}] 检测到其它运行目录已有盘口快照进程（PID={global_alive[0]}），拒绝启动混合运行时",
            file=sys.stderr,
        )
        # Do not let the follow engine attach to another installation's
        # snapshot stream.  ``2`` is reserved for reusing this runtime's
        # already-running worker.
        return 1
    if alive:
        write_snapshot_watch_pid_info(
            {"recovered_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "live": True, "pids": alive[:1]}
        )
        return 2

    worker = _snapshot_watch_worker_spec()
    cmd = build_worker_process_command(worker)
    out_path = LOG_DIR / f"{worker.name}.out.log"
    err_path = LOG_DIR / f"{worker.name}.err.log"
    print(f"[{now_iso()}] Start(snapshot): {worker.name} {' '.join(cmd)}")
    print(f"  logs: {out_path} | {err_path}")
    out_f = open(out_path, "ab")
    err_f = open(err_path, "ab")
    kwargs: Dict[str, Any] = {
        "cwd": str(ROOT),
        "stdout": out_f,
        "stderr": err_f,
        "env": env,
        "creationflags": 0,
    }
    if os.name == "nt":
        try:
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        except Exception:
            pass
    else:
        kwargs["start_new_session"] = True
    process = subprocess.Popen(cmd, **kwargs)
    write_snapshot_watch_pid_info(
        {"started_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "live": True, "pids": [process.pid]}
    )

    deadline = time.time() + 1.0
    while time.time() < deadline:
        if not is_pid_running(int(process.pid)):
            break
        time.sleep(0.1)
    if is_pid_running(int(process.pid)):
        return 0

    # A concurrent launcher may have won the single-instance lock. Reuse it
    # instead of reporting a false failure.
    recovered = _alive_snapshot_watch_pids()
    if recovered:
        write_snapshot_watch_pid_info(
            {"recovered_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "live": True, "pids": recovered[:1]}
        )
        return 2
    write_snapshot_watch_pid_info(
        {
            "failed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "live": True,
            "pids": [],
            "last_exit_code": process.poll(),
        }
    )
    print(
        f"odds_snapshot_watch exited during startup (pid={process.pid}, code={process.poll()})",
        file=sys.stderr,
    )
    return 1


def start_follow_engine() -> int:
    ensure_dirs()
    env = dict(os.environ)
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    apply_playwright_browsers_path(env, ROOT)

    existing = read_follow_engine_pid_info()
    alive = _alive_worker_pids(existing)
    if not alive:
        alive = _alive_follow_engine_pids()
    global_alive = [pid for pid in _alive_global_worker_pids("follow_engine.py") if pid not in alive]
    if global_alive:
        print(f"[{now_iso()}] 检测到其它运行目录已有跟单引擎（PID={global_alive[0]}），拒绝启动重复实例")
        return 2
    if alive:
        print(f"[{now_iso()}] 检测到跟单引擎已在运行（{len(alive)}个），请先执行：python app.py stop-follow-engine 或 python app.py restart-follow-engine")
        return 2

    # Snapshot Direct cannot progress without the public盘口 worker. Starting
    # only follow_engine used to leave every order waiting on SNAPSHOT_MISSING
    # until its 160-second market window expired.
    snapshot_rc = start_snapshot_watch()
    if snapshot_rc not in (0, 2):
        print(f"[{now_iso()}] 盘口快照进程启动失败，拒绝启动跟单引擎", file=sys.stderr)
        return 1
    if existing:
        try:
            write_follow_engine_pid_info({"stale_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "live": True, "pids": []})
        except Exception:
            pass

    w = _follow_engine_worker_spec()
    out_path = LOG_DIR / f"{w.name}.out.log"
    err_path = LOG_DIR / f"{w.name}.err.log"
    cmd = build_worker_process_command(w)
    print(f"[{now_iso()}] Start(follow): {w.name} {' '.join(cmd)}")
    print(f"  logs: {out_path} | {err_path}")
    out_f = open(out_path, "ab")
    err_f = open(err_path, "ab")
    creationflags = 0
    if os.name == "nt":
        try:
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        except Exception:
            creationflags = 0
    popen_kwargs = {
        "cwd": str(ROOT),
        "stdout": out_f,
        "stderr": err_f,
        "env": env,
        "creationflags": creationflags,
    }
    if os.name != "nt":
        popen_kwargs["start_new_session"] = True
    p = subprocess.Popen(cmd, **popen_kwargs)
    payload = {"started_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "live": True, "pids": [p.pid]}
    write_follow_engine_pid_info(payload)
    print(f"FOLLOW PID file: {FOLLOW_ENGINE_PID_FILE}")
    # Popen succeeding only proves that Python was launched.  A worker that
    # immediately exits (for example because of an import/runtime exception)
    # used to be reported as started, leaving the WebUI control out of sync.
    # Give the child a short grace window and fail the start command when it
    # has already exited.  Network work inside the engine is asynchronous from
    # this launcher's perspective, so this does not wait for a market request.
    deadline = time.time() + 1.0
    while time.time() < deadline:
        if is_pid_running(int(p.pid)):
            return 0
        time.sleep(0.1)
    if not is_pid_running(int(p.pid)):
        write_follow_engine_pid_info({
            "failed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "live": True,
            "pids": [],
            "last_exit_code": p.poll(),
        })
        print(f"follow_engine exited during startup (pid={p.pid}, code={p.poll()})", file=sys.stderr)
        return 1
    return 0


def stop_follow_engine(force: bool) -> int:
    info = read_follow_engine_pid_info()
    if not info:
        print(".run/follow_engine.pid.json not found.")
        return 1
    pids_all = [int(x) for x in (info.get("pids") or [])]
    pids = [pid for pid in pids_all if is_pid_running(pid) and _is_probably_hdydf_worker_pid(pid)]
    skipped = [pid for pid in pids_all if pid not in pids]
    if skipped:
        print(f"Skip non-worker/stale follow PIDs: {skipped}")
    if not pids:
        write_follow_engine_pid_info({"stopped_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "live": True, "pids": []})
        print("No running follow_engine found. Cleared follow PID file.")
        return 0

    for pid in pids:
        if is_pid_running(pid):
            print(f"Stop(follow) PID={pid}")
            stop_pid(pid, force=bool(force))

    deadline = time.time() + 12.0
    while time.time() < deadline:
        alive = [pid for pid in pids if is_pid_running(pid)]
        if not alive:
            break
        time.sleep(0.2)

    alive = [pid for pid in pids if is_pid_running(pid)]
    if alive and not force:
        for pid in alive:
            print(f"Force stop(follow) PID={pid}")
            stop_pid(pid, force=True)
        deadline = time.time() + 10.0
        while time.time() < deadline:
            alive = [pid for pid in pids if is_pid_running(pid)]
            if not alive:
                break
            time.sleep(0.2)

    alive = [pid for pid in pids if is_pid_running(pid)]
    if alive:
        print(f"Warning: still running follow PIDs: {alive}")
        return 2

    write_follow_engine_pid_info({"stopped_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "live": True, "pids": []})
    print("Done(follow).")
    return 0


def status_follow_engine() -> int:
    info = read_follow_engine_pid_info()
    if not info:
        print("Follow engine not running (no .run/follow_engine.pid.json).")
        return 0
    print(f"follow_started_at={info.get('started_at')} live={info.get('live')}")
    for pid in [int(x) for x in (info.get("pids") or [])]:
        if not is_pid_running(pid):
            print("EXITED  ".ljust(8) + f" PID={pid}")
        else:
            print("RUNNING ".ljust(8) + f" PID={pid}")
    return 0


def start_telegram_signal() -> int:
    ensure_dirs()
    existing = read_telegram_signal_pid_info()
    alive = _alive_worker_pids(existing)
    if alive:
        print(f"[{now_iso()}] TG 信号监听已在运行（{len(alive)}个）")
        return 2

    env = dict(os.environ)
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    worker = _telegram_signal_worker_spec()
    cmd = build_worker_process_command(worker)
    out_f = open(LOG_DIR / "telegram_signal_watch.out.log", "ab")
    err_f = open(LOG_DIR / "telegram_signal_watch.err.log", "ab")
    kwargs = {
        "cwd": str(ROOT),
        "stdout": out_f,
        "stderr": err_f,
        "env": env,
        "creationflags": 0,
    }
    if os.name != "nt":
        kwargs["start_new_session"] = True
    process = subprocess.Popen(cmd, **kwargs)
    write_telegram_signal_pid_info(
        {"started_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "live": True, "pids": [process.pid]}
    )
    print(f"TG SIGNAL PID file: {TELEGRAM_SIGNAL_PID_FILE}")
    return 0


def stop_telegram_signal(force: bool) -> int:
    info = read_telegram_signal_pid_info()
    if not info:
        return 0
    pids = _alive_worker_pids(info)
    for pid in pids:
        stop_pid(pid, force=bool(force))
    deadline = time.time() + 8.0
    while time.time() < deadline and any(is_pid_running(pid) for pid in pids):
        time.sleep(0.2)
    alive = [pid for pid in pids if is_pid_running(pid)]
    if alive:
        for pid in alive:
            stop_pid(pid, force=True)
    write_telegram_signal_pid_info(
        {"stopped_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "live": True, "pids": []}
    )
    return 0


def status_telegram_signal() -> int:
    info = read_telegram_signal_pid_info() or {}
    pids = [int(value) for value in (info.get("pids") or [])]
    for pid in pids:
        state = "RUNNING" if is_pid_running(pid) else "EXITED"
        print(state.ljust(8) + f" PID={pid}")
    if not pids:
        print("TG signal listener not running.")
    return 0


def stop_all(force: bool) -> int:
    ps3838_was_running = stop_ps3838_worker(force=bool(force))
    try:
        stop_ps3838_stack()
    except Exception as exc:
        print(f"[{now_iso()}] PS3838 停止失败: {exc}")
    info = read_pid_info()
    if not info:
        print(".run/pids.json not found (maybe not started by app.py/app.ps1/run_all.ps1).")
        return 0 if ps3838_was_running else 1
    pids = _merge_dedicated_worker_pids([int(x) for x in (info.get("pids") or [])])
    if not pids:
        print("PID list is empty.")
        return 0

    # Safety: only stop processes that still look like our workers (avoid PID reuse killing other apps).
    pids_all = list(pids)
    pids = [pid for pid in pids if is_pid_running(pid) and _is_probably_hdydf_worker_pid(pid)]
    skipped = [pid for pid in pids_all if pid not in pids]
    if skipped:
        print(f"Skip non-worker/stale PIDs: {skipped}")
    if not pids:
        # Nothing to stop, but clear PID file to unblock future starts.
        try:
            write_pid_info(
                {
                    "stopped_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "host": info.get("host") or "",
                    "port": int(info.get("port") or 0),
                    "live": True,
                    "pids": [],
                }
            )
        except Exception:
            pass
        print("No running worker processes found. Cleared PID file.")
        return 0

    for pid in pids:
        if is_pid_running(pid):
            print(f"Stop PID={pid}")
            stop_pid(pid, force=bool(force))
        else:
            print(f"PID={pid} not found/already exited")

    # 等待进程真正退出（Windows 上 taskkill 返回后可能仍有短暂存活）
    deadline = time.time() + 12.0
    while time.time() < deadline:
        alive = [pid for pid in pids if is_pid_running(pid)]
        if not alive:
            break
        time.sleep(0.2)

    # 若仍有残留，且未使用 --force，则再强杀一次并等待
    alive = [pid for pid in pids if is_pid_running(pid)]
    if alive and not force:
        for pid in alive:
            print(f"Force stop PID={pid}")
            stop_pid(pid, force=True)
        deadline = time.time() + 10.0
        while time.time() < deadline:
            alive = [pid for pid in pids if is_pid_running(pid)]
            if not alive:
                break
            time.sleep(0.2)

    alive = [pid for pid in pids if is_pid_running(pid)]
    if alive:
        print(f"Warning: still running PIDs: {alive}")
        return 2

    cleanup_runtime_playwright_browsers(force=bool(force))

    # 清空 PID 文件，避免 restart 时 start_all 误判“仍在运行”
    try:
        write_pid_info(
            {
                "stopped_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "host": info.get("host") or "",
                "port": int(info.get("port") or 0),
                "live": True,
                "pids": [],
            }
        )
    except Exception:
        pass

    print("Done.")
    try:
        write_follow_engine_pid_info({"stopped_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "live": True, "pids": []})
    except Exception:
        pass
    try:
        write_snapshot_watch_pid_info({"stopped_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "live": True, "pids": []})
    except Exception:
        pass
    return 0


def stop_core(force: bool) -> int:
    info = read_core_pid_info()
    if not info:
        print(".run/pids_core.json not found (core not started).")
        return 1
    pids = _merge_dedicated_worker_pids([int(x) for x in (info.get("pids") or [])])
    if not pids:
        print("Core PID list is empty.")
        return 0

    pids_all = list(pids)
    pids = [pid for pid in pids if is_pid_running(pid) and _is_probably_hdydf_worker_pid(pid)]
    skipped = [pid for pid in pids_all if pid not in pids]
    if skipped:
        print(f"Skip non-worker/stale core PIDs: {skipped}")
    if not pids:
        try:
            write_core_pid_info({"stopped_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "live": True, "pids": []})
        except Exception:
            pass
        print("No running core worker processes found. Cleared core PID file.")
        return 0

    for pid in pids:
        if is_pid_running(pid):
            print(f"Stop(core) PID={pid}")
            stop_pid(pid, force=bool(force))

    deadline = time.time() + 12.0
    while time.time() < deadline:
        alive = [pid for pid in pids if is_pid_running(pid)]
        if not alive:
            break
        time.sleep(0.2)

    alive = [pid for pid in pids if is_pid_running(pid)]
    if alive and not force:
        for pid in alive:
            print(f"Force stop(core) PID={pid}")
            stop_pid(pid, force=True)
        deadline = time.time() + 10.0
        while time.time() < deadline:
            alive = [pid for pid in pids if is_pid_running(pid)]
            if not alive:
                break
            time.sleep(0.2)

    alive = [pid for pid in pids if is_pid_running(pid)]
    if alive:
        print(f"Warning: still running core PIDs: {alive}")
        return 2

    cleanup_runtime_playwright_browsers(force=bool(force))

    try:
        write_core_pid_info({"stopped_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "live": True, "pids": []})
    except Exception:
        pass
    try:
        write_follow_engine_pid_info({"stopped_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "live": True, "pids": []})
    except Exception:
        pass
    try:
        write_snapshot_watch_pid_info({"stopped_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "live": True, "pids": []})
    except Exception:
        pass
    print("Done(core).")
    return 0


def status() -> int:
    info = read_pid_info()
    if not info:
        print("Not running (no .run/pids.json).")
        return 0
    print(f"started_at={info.get('started_at')} host={info.get('host')} port={info.get('port')} live={info.get('live')}")
    for pid in [int(x) for x in (info.get("pids") or [])]:
        if not is_pid_running(pid):
            print("EXITED  ".ljust(8) + f" PID={pid}")
            continue
        if _is_probably_hdydf_worker_pid(pid):
            print("RUNNING ".ljust(8) + f" PID={pid}")
        else:
            info2 = _windows_get_process_info(pid) if os.name == "nt" else {}
            pname = str(info2.get("name") or "")
            print("FOREIGN ".ljust(8) + f" PID={pid}" + (f" ({pname})" if pname else ""))
    return 0


def status_core() -> int:
    info = read_core_pid_info()
    if not info:
        print("Core not running (no .run/pids_core.json).")
        return 0
    print(f"core_started_at={info.get('started_at')} live={info.get('live')}")
    for pid in [int(x) for x in (info.get("pids") or [])]:
        if not is_pid_running(pid):
            print("EXITED  ".ljust(8) + f" PID={pid}")
        else:
            print("RUNNING ".ljust(8) + f" PID={pid}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Single-file launcher for AG watcher/WebUI/member keepalive/follow engine")
    parser.add_argument(
        "action",
        choices=[
            "start",
            "stop",
            "restart",
            "status",
            "start-core",
            "stop-core",
            "restart-core",
            "status-core",
            "start-follow-engine",
            "stop-follow-engine",
            "restart-follow-engine",
            "status-follow-engine",
            "start-telegram-signal",
            "stop-telegram-signal",
            "restart-telegram-signal",
            "status-telegram-signal",
            "start-ps3838",
            "stop-ps3838",
            "restart-ps3838",
            "status-ps3838",
        ],
        nargs="?",
        default="start",
    )
    parser.add_argument("--force", action="store_true", help="force stop (taskkill /F on Windows)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9999)
    parser.add_argument("--backend-interval", type=int, default=3)
    parser.add_argument("--member-interval", type=int, default=3)
    args = parser.parse_args(argv)

    if args.action == "status":
        return status()
    if args.action == "status-core":
        return status_core()
    if args.action == "status-follow-engine":
        return status_follow_engine()
    if args.action == "status-telegram-signal":
        return status_telegram_signal()
    if args.action == "status-ps3838":
        return status_ps3838()
    if args.action == "start-ps3838":
        return start_ps3838_stack()
    if args.action == "stop-ps3838":
        return stop_ps3838_stack()
    if args.action == "restart-ps3838":
        stop_ps3838_stack()
        time.sleep(0.5)
        return start_ps3838_stack()
    if args.action == "stop":
        return stop_all(force=bool(args.force))
    if args.action == "stop-core":
        return stop_core(force=bool(args.force))
    if args.action == "stop-follow-engine":
        return stop_follow_engine(force=bool(args.force))
    if args.action == "stop-telegram-signal":
        return stop_telegram_signal(force=bool(args.force))
    if args.action == "restart":
        stop_all(force=bool(args.force))
        time.sleep(0.3)
        return start_all(args.host, args.port, args.backend_interval, args.member_interval)
    if args.action == "restart-core":
        stop_core(force=bool(args.force))
        time.sleep(0.3)
        return start_core(args.backend_interval, args.member_interval)
    if args.action == "restart-follow-engine":
        stop_follow_engine(force=bool(args.force))
        time.sleep(0.3)
        return start_follow_engine()
    if args.action == "restart-telegram-signal":
        stop_telegram_signal(force=bool(args.force))
        time.sleep(0.3)
        return start_telegram_signal()
    if args.action == "start-core":
        return start_core(args.backend_interval, args.member_interval)
    if args.action == "start-follow-engine":
        return start_follow_engine()
    if args.action == "start-telegram-signal":
        return start_telegram_signal()
    return start_all(args.host, args.port, args.backend_interval, args.member_interval)


if __name__ == "__main__":
    raise SystemExit(main())
