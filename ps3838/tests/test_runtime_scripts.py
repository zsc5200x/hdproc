"""Launcher regression tests use temporary projects and mocked processes only."""

import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class RuntimeScriptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / "scripts").mkdir()
        for name in ("project_config.py", "start.sh", "stop.sh", "dashboard.sh", "crown.sh", "scripts/runtime.sh"):
            shutil.copy2(ROOT / name, self.root / name)
        self.env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(self.root),
                    "PYTHON_BIN": sys.executable, "CROWN_PYTHON_BIN": sys.executable}

    def run_helper(self, body, *, locale=None):
        source = "\n".join([
            "set -Eeuo pipefail",
            "PROJECT_DIR=" + shlex.quote(str(self.root)),
            'RUN_DIR="$PROJECT_DIR/.run"',
            'LOG_DIR="$PROJECT_DIR/logs"',
            'mkdir -p "$RUN_DIR" "$LOG_DIR"',
            'source "$PROJECT_DIR/scripts/runtime.sh"',
            body,
        ])
        env = dict(self.env)
        if locale is not None:
            env["LC_ALL"] = locale
        return subprocess.run(["/bin/bash", "-c", source], cwd=self.root, env=env,
                              text=True, capture_output=True, timeout=10)

    def test_invalid_config_stops_all_launchers_before_side_effects(self):
        (self.root / "config.json").write_text("{malformed", encoding="utf-8")
        fakebin = self.root / "bin"
        fakebin.mkdir()
        marker = self.root / "unexpected-action"
        for tool in ("curl", "nohup", "lsof", "kill"):
            stub = fakebin / tool
            stub.write_text("#!/bin/sh\ntouch " + shlex.quote(str(marker)) + "\nexit 0\n", encoding="utf-8")
            stub.chmod(0o755)
        env = dict(self.env, PATH=str(fakebin) + ":" + self.env["PATH"])
        for script in ("start.sh", "stop.sh", "dashboard.sh", "crown.sh"):
            with self.subTest(script=script):
                result = subprocess.run(["/bin/bash", str(self.root / script)], cwd=self.root, env=env,
                                        text=True, capture_output=True, timeout=10)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("配置加载失败", result.stderr)
                self.assertFalse(marker.exists())
                self.assertFalse((self.root / ".run").exists())
                self.assertFalse((self.root / "logs").exists())

    def test_missing_selected_config_stops_launcher(self):
        env = dict(self.env, PS3838_CONFIG="missing.json")
        result = subprocess.run(["/bin/bash", str(self.root / "start.sh")], env=env,
                                text=True, capture_output=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("无法读取配置文件", result.stderr)
        self.assertFalse((self.root / ".run").exists())

    def test_ownership_checks_python_command_and_working_directory(self):
        result = self.run_helper(r'''
PID_COMMAND="python3 $PROJECT_DIR/capture.py"
PID_EXECUTABLE="python3"
PID_CWD="$PROJECT_DIR"
runtime_pid_alive() { return 0; }
runtime_process_cwd() { printf '%s\n' "$PID_CWD"; }
ps() {
  if [[ "$*" == *"comm="* ]]; then printf '%s\n' "$PID_EXECUTABLE";
  else printf '%s\n' "$PID_COMMAND"; fi
}
runtime_owned_pid 123 "$PROJECT_DIR/capture.py"
PID_CWD="/different-project"
if runtime_owned_pid 123 "$PROJECT_DIR/capture.py"; then exit 91; fi
PID_CWD="$PROJECT_DIR"
PID_COMMAND="python3 $PROJECT_DIR/other.py"
if runtime_owned_pid 123 "$PROJECT_DIR/capture.py"; then exit 92; fi
PID_COMMAND="vim $PROJECT_DIR/capture.py"
PID_EXECUTABLE="vim"
if runtime_owned_pid 123 "$PROJECT_DIR/capture.py"; then exit 93; fi
PID_COMMAND="python3 -u app.py"
PID_EXECUTABLE="/usr/bin/python3"
PID_CWD="$PROJECT_DIR/crown"
runtime_owned_pid 123 "$PROJECT_DIR/crown/app.py"
''')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_stop_refuses_unowned_process(self):
        result = self.run_helper(r'''
runtime_pid_alive() { return 0; }
runtime_owned_pid() { return 1; }
kill() { touch "$PROJECT_DIR/killed"; }
if runtime_stop_pid 123 "$PROJECT_DIR/capture.py" capture; then exit 91; fi
[[ ! -e "$PROJECT_DIR/killed" ]]
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("不属于本项目", result.stderr)

    def test_http_readiness_bypasses_proxy(self):
        result = self.run_helper(r'''
curl() { printf '%s\n' "$@" >"$PROJECT_DIR/curl-args"; }
runtime_http_ready "http://127.0.0.1:9876/api/health"
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        args = (self.root / "curl-args").read_text().splitlines()
        self.assertEqual(args[:2], ["--noproxy", "*"])
        self.assertIn("http://127.0.0.1:9876/api/health", args)

    def test_existing_server_requires_health_check(self):
        result = self.run_helper(r'''
echo 123 >"$RUN_DIR/dashboard.pid"
runtime_owned_pid() { return 0; }
runtime_port_pid() { echo 123; }
runtime_script_pids() { echo 123; }
runtime_wait_ready() { printf '%s\n' "$3" >"$PROJECT_DIR/health-checked"; return 1; }
nohup() { touch "$PROJECT_DIR/started"; }
if runtime_start dashboard "$PROJECT_DIR/dashboard_server.py" /bin/sh http://127.0.0.1:9876/api/health 9876; then exit 91; fi
[[ -f "$PROJECT_DIR/health-checked" && ! -f "$PROJECT_DIR/started" ]]
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("进程存在但接口未就绪", result.stderr)

    def test_existing_healthy_server_is_reused(self):
        result = self.run_helper(r'''
echo 123 >"$RUN_DIR/dashboard.pid"
runtime_owned_pid() { return 0; }
runtime_port_pid() { echo 123; }
runtime_script_pids() { echo 123; }
runtime_wait_ready() { touch "$PROJECT_DIR/health-checked"; return 0; }
nohup() { touch "$PROJECT_DIR/started"; }
runtime_start dashboard "$PROJECT_DIR/dashboard_server.py" /bin/sh http://127.0.0.1:9876/api/health 9876
[[ -f "$PROJECT_DIR/health-checked" && ! -f "$PROJECT_DIR/started" ]]
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("已在运行且接口已就绪", result.stdout)

    def test_missing_pid_file_recovers_running_worker(self):
        result = self.run_helper(r'''
runtime_script_pids() { echo 123; }
nohup() { touch "$PROJECT_DIR/started"; }
runtime_start capture "$PROJECT_DIR/capture.py" /bin/sh "" ""
[[ "$(cat "$RUN_DIR/capture.pid")" == 123 && ! -f "$PROJECT_DIR/started" ]]
''')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_multiple_workers_are_reported_without_starting_another(self):
        result = self.run_helper(r'''
runtime_script_pids() { printf '123\n456\n'; }
nohup() { touch "$PROJECT_DIR/started"; }
if runtime_start capture "$PROJECT_DIR/capture.py" /bin/sh "" ""; then exit 91; fi
[[ ! -f "$PROJECT_DIR/started" ]]
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("发现多个", result.stderr)

    def test_start_lock_prevents_concurrent_launch(self):
        result = self.run_helper(r'''
mkdir "$RUN_DIR/capture.start.lock"
echo 123 >"$RUN_DIR/capture.start.lock/pid"
runtime_pid_alive() { return 0; }
runtime_start_unlocked() { touch "$PROJECT_DIR/started"; }
if runtime_start capture "$PROJECT_DIR/capture.py" /bin/sh "" ""; then exit 91; fi
[[ ! -f "$PROJECT_DIR/started" ]]
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("未重复启动", result.stderr)

    def test_stale_start_lock_is_recovered_and_cleaned(self):
        result = self.run_helper(r'''
mkdir "$RUN_DIR/capture.start.lock"
echo 123 >"$RUN_DIR/capture.start.lock/pid"
runtime_pid_alive() { return 1; }
runtime_start_unlocked() { touch "$PROJECT_DIR/started"; }
runtime_start capture "$PROJECT_DIR/capture.py" /bin/sh "" ""
[[ -f "$PROJECT_DIR/started" && ! -d "$RUN_DIR/capture.start.lock" ]]
''')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_occupied_port_is_not_adopted_or_stopped(self):
        result = self.run_helper(r'''
runtime_port_pid() { echo 456; }
runtime_owned_pid() { return 1; }
nohup() { touch "$PROJECT_DIR/started"; }
kill() { touch "$PROJECT_DIR/killed"; }
if runtime_start dashboard "$PROJECT_DIR/dashboard_server.py" /bin/sh http://127.0.0.1:9876/api/health 9876; then exit 91; fi
[[ ! -f "$PROJECT_DIR/started" && ! -f "$PROJECT_DIR/killed" ]]
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("被其他项目", result.stderr)

    def test_startup_grace_waits_for_nohup_to_exec(self):
        result = self.run_helper(r'''
ATTEMPT=0
runtime_pid_alive() { return 0; }
runtime_owned_pid() { ATTEMPT=$((ATTEMPT + 1)); [[ "$ATTEMPT" -gt 1 ]]; }
runtime_http_ready() { return 0; }
sleep() { :; }
runtime_wait_ready 123 "$PROJECT_DIR/dashboard_server.py" http://127.0.0.1:9876/api/health
[[ "$ATTEMPT" -eq 2 ]]
''')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_stop_recovers_worker_with_missing_pid_file(self):
        result = self.run_helper(r'''
runtime_script_pids() { echo 123; }
runtime_stop_pid() { printf '%s\n' "$1" >"$PROJECT_DIR/stopped-pid"; }
runtime_stop capture "$PROJECT_DIR/capture.py"
[[ "$(cat "$PROJECT_DIR/stopped-pid")" == 123 ]]
''')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_real_temporary_python_process_is_owned(self):
        script = self.root / "test_owned_worker.py"
        script.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
        child = subprocess.Popen([sys.executable, str(script)], cwd=self.root,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            result = self.run_helper("runtime_owned_pid " + str(child.pid) + " " + shlex.quote(str(script)))
            self.assertEqual(result.returncode, 0, result.stderr)
        finally:
            child.terminate()
            child.wait(timeout=5)

    def test_system_bash_can_start_and_stop_temporary_worker_in_both_locales(self):
        script = self.root / "test_locale_worker.py"
        script.write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
        available = subprocess.run(["locale", "-a"], text=True, capture_output=True, check=True).stdout.splitlines()
        utf8_locale = next((value for value in available if value.lower() == "en_us.utf-8"),
                           next((value for value in available if "utf" in value.lower()), None))
        locales = ["C"] + ([utf8_locale] if utf8_locale else [])
        for locale in locales:
            with self.subTest(locale=locale):
                result = self.run_helper(r'''
trap 'if [[ -s "$RUN_DIR/locale_worker.pid" ]]; then runtime_stop locale_worker "$PROJECT_DIR/test_locale_worker.py"; fi' EXIT
runtime_start locale_worker "$PROJECT_DIR/test_locale_worker.py" "$PYTHON_BIN" "" ""
worker_pid="$(cat "$RUN_DIR/locale_worker.pid")"
runtime_stop locale_worker "$PROJECT_DIR/test_locale_worker.py"
[[ ! -f "$RUN_DIR/locale_worker.pid" ]]
if runtime_pid_alive "$worker_pid"; then exit 91; fi
''', locale=locale)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("进程已启动", result.stdout)
                self.assertIn("正在停止 locale_worker（PID=", result.stdout)

    def test_shell_variables_have_explicit_boundaries_before_non_ascii_text(self):
        import re
        for filename in ("start.sh", "stop.sh", "dashboard.sh", "crown.sh", "scripts/runtime.sh"):
            with self.subTest(filename=filename):
                self.assertIsNone(re.search(r"\$[A-Za-z_][A-Za-z0-9_]*(?=[^\x00-\x7f])",
                                            (ROOT / filename).read_text()))


if __name__ == "__main__":
    unittest.main()
