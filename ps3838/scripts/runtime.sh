#!/usr/bin/env bash
# Shared launcher helpers. Load configuration before creating runtime files.

runtime_load_config() {
  local python_bin="$1" config_assignments
  if [[ ! -x "$python_bin" ]]; then
    echo "找不到 Python 虚拟环境：$python_bin" >&2
    return 1
  fi
  if ! config_assignments="$("$python_bin" "$PROJECT_DIR/project_config.py" shell)"; then
    echo "配置加载失败，未启动或停止任何服务。" >&2
    return 1
  fi
  # A fixed allowlist is shell-escaped by Python shlex.quote.
  eval "$config_assignments"
}

runtime_pid_alive() {
  local pid="$1" state
  [[ "$pid" =~ ^[1-9][0-9]*$ ]] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  state="$(ps -p "$pid" -o stat= 2>/dev/null || true)"
  # Some sandboxed / restricted shells cannot execute ps.  kill -0 already
  # proved the PID is alive, so only reject an explicit zombie state.
  [[ -z "$state" || "$state" != *Z* ]]
}

runtime_process_cwd() {
  local pid="$1"
  if [[ -e "/proc/$pid/cwd" ]]; then
    readlink "/proc/$pid/cwd" 2>/dev/null || true
  else
    lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -n 1 || true
  fi
}

runtime_owned_pid() {
  local pid="$1" script="$2" command executable process_cwd relative basename
  runtime_pid_alive "$pid" || return 1
  executable="$(ps -p "$pid" -o comm= 2>/dev/null || true)"
  executable="${executable##*/}"
  command="$(ps -p "$pid" -o command= 2>/dev/null || true)"
  process_cwd="$(runtime_process_cwd "$pid")"
  [[ "$process_cwd" == "$PROJECT_DIR" || "$process_cwd" == "$(dirname "$script")" ]] || return 1
  # Restricted shells may block ps.  A PID recorded in this project's own
  # pid file and a matching cwd is enough provenance to reuse it.
  if [[ -z "$executable" && -z "$command" ]]; then return 0; fi
  case "$executable" in python*|Python*|pypy*) ;; *) return 1 ;; esac
  relative="${script#"$PROJECT_DIR/"}"
  basename="${script##*/}"
  if [[ " $command " == *" $script "* ]]; then return 0; fi
  if [[ "$process_cwd" == "$PROJECT_DIR" && " $command " == *" $relative "* ]]; then return 0; fi
  [[ "$process_cwd" == "$(dirname "$script")" && " $command " == *" $basename "* ]]
}

runtime_http_ready() {
  if [[ -n "${RUNTIME_HEALTH_SOCKET:-}" ]]; then
    curl --noproxy '*' --unix-socket "$RUNTIME_HEALTH_SOCKET" -fsS --max-time 2 "$1" >/dev/null 2>&1
    return
  fi
  curl --noproxy '*' -fsS --max-time 2 "$1" >/dev/null 2>&1
}

runtime_wait_ready() {
  local pid="$1" script="$2" health_url="$3" attempt attempts
  # 看板首次启动要载入 CSV/SQLite 历史，30 秒可能不够；允许按服务覆盖等待时长。
  attempts="${RUNTIME_READY_ATTEMPTS:-30}"
  [[ "$attempts" =~ ^[1-9][0-9]*$ ]] || attempts=30
  for ((attempt = 1; attempt <= attempts; attempt++)); do
    runtime_pid_alive "$pid" || return 1
    if runtime_owned_pid "$pid" "$script" && runtime_http_ready "$health_url"; then return 0; fi
    sleep 1
  done
  return 1
}

runtime_port_pid() {
  lsof -nP -tiTCP:"$1" -sTCP:LISTEN 2>/dev/null | head -n 1 || true
}

runtime_script_pids() {
  local script="$1" candidate
  while IFS= read -r candidate; do
    if runtime_owned_pid "$candidate" "$script"; then printf '%s\n' "$candidate"; fi
  done < <(pgrep -f "${script##*/}" 2>/dev/null || true)
}

runtime_start() (
  # A per-service lock closes the race between simultaneous launcher invocations.
  local name="$1" lock_dir="$RUN_DIR/$1.start.lock" lock_owner=""
  if ! mkdir "$lock_dir" 2>/dev/null; then
    if [[ -s "$lock_dir/pid" ]]; then lock_owner="$(cat "$lock_dir/pid")"; fi
    if [[ "$lock_owner" =~ ^[1-9][0-9]*$ ]] && ! runtime_pid_alive "$lock_owner"; then
      rm -f "$lock_dir/pid"
      rmdir "$lock_dir" 2>/dev/null || true
    fi
    if ! mkdir "$lock_dir" 2>/dev/null; then
      echo "$name 的启动检查正在进行或锁状态无法确认，未重复启动。" >&2
      return 1
    fi
  fi
  printf '%s\n' "$$" >"$lock_dir/pid"
  trap 'rm -f "$lock_dir/pid"; rmdir "$lock_dir" 2>/dev/null || true' EXIT
  trap 'exit 1' HUP INT TERM
  runtime_start_unlocked "$@"
)

runtime_start_unlocked() {
  local name="$1" script="$2" python_bin="$3" health_url="$4" port="$5"
  shift 5
  local pid_file="$RUN_DIR/$name.pid" log_file="$LOG_DIR/$name.log" pid="" port_pid="" candidate
  [[ -x "$python_bin" ]] || { echo "找不到 Python：$python_bin" >&2; return 1; }
  if [[ -s "$pid_file" ]]; then
    pid="$(cat "$pid_file")"
    if ! runtime_owned_pid "$pid" "$script"; then
      if runtime_pid_alive "$pid"; then echo "$pid_file 指向其他进程，未对其操作。" >&2; fi
      rm -f "$pid_file"
      pid=""
    fi
  fi
  if [[ -n "$port" ]]; then
    port_pid="$(runtime_port_pid "$port")"
    if [[ -n "$port_pid" ]]; then
      if ! runtime_owned_pid "$port_pid" "$script"; then
        echo "端口 $port 被其他项目或无法确认归属的进程占用（PID=${port_pid}），未启动 ${name}。" >&2
        return 1
      fi
      if [[ -n "$pid" && "$pid" != "$port_pid" ]]; then
        echo "${name} 的 PID 记录与端口进程不一致，未启动重复服务。" >&2
        return 1
      fi
      pid="$port_pid"
      echo "$pid" >"$pid_file"
    fi
  fi
  # Recover an owned process whose PID file was removed instead of duplicating it.
  while IFS= read -r candidate; do
    [[ -n "$candidate" ]] || continue
    if [[ -n "$pid" && "$pid" != "$candidate" ]]; then
      echo "发现多个 ${name} 进程，未启动重复服务；请先检查现有进程。" >&2
      return 1
    fi
    pid="$candidate"
  done < <(runtime_script_pids "$script")
  if [[ -n "$pid" ]]; then echo "$pid" >"$pid_file"; fi
  if [[ -n "$pid" ]]; then
    if [[ -n "$health_url" ]] && ! runtime_wait_ready "$pid" "$script" "$health_url"; then
      echo "$name 进程存在但接口未就绪，请查看 ${log_file}。" >&2
      return 1
    fi
    echo "${name} 已在运行${health_url:+且接口已就绪}，PID=${pid}"
    return 0
  fi
  nohup "$python_bin" -u "$script" "$@" >>"$log_file" 2>&1 &
  pid=$!
  echo "$pid" >"$pid_file"
  if [[ -n "$health_url" ]]; then
    if ! runtime_wait_ready "$pid" "$script" "$health_url"; then
      echo "${name} 未能就绪，请查看 ${log_file}。" >&2
      runtime_stop_pid "$pid" "$script" "$name" || true
      rm -f "$pid_file"
      return 1
    fi
    echo "${name} 接口已就绪，PID=${pid}，日志=${log_file}"
  else
    sleep 1
    if ! runtime_owned_pid "$pid" "$script"; then
      echo "${name} 启动失败，请查看 ${log_file}。" >&2
      rm -f "$pid_file"
      return 1
    fi
    echo "${name} 进程已启动，PID=${pid}（数据是否更新请查看看板），日志=${log_file}"
  fi
}

runtime_stop_pid() {
  local pid="$1" script="$2" name="$3" attempt
  runtime_pid_alive "$pid" || return 0
  if ! runtime_owned_pid "$pid" "$script"; then
    echo "${name} 的 PID=${pid} 不属于本项目，已跳过。" >&2
    return 1
  fi
  echo "正在停止 ${name}（PID=${pid}）..."
  kill -TERM "$pid" 2>/dev/null || return 0
  for attempt in {1..40}; do
    runtime_pid_alive "$pid" || return 0
    sleep 0.25
  done
  if runtime_owned_pid "$pid" "$script"; then
    echo "${name} 未按时退出，发送强制停止信号。" >&2
    kill -KILL "$pid" 2>/dev/null || true
  fi
}

runtime_stop() {
  local name="$1" script="$2" port="${3:-}" pid_file="$RUN_DIR/$1.pid" pid="" port_pid="" candidate
  if [[ -s "$pid_file" ]]; then
    pid="$(cat "$pid_file")"
    runtime_stop_pid "$pid" "$script" "$name" || true
    rm -f "$pid_file"
  fi
  if [[ -n "$port" ]]; then
    port_pid="$(runtime_port_pid "$port")"
    if [[ -n "$port_pid" && "$port_pid" != "$pid" ]]; then
      runtime_stop_pid "$port_pid" "$script" "$name" || true
    fi
  fi
  # Capture/snapshot workers have no listening port; recover missing PID files.
  while IFS= read -r candidate; do
    [[ -n "$candidate" && "$candidate" != "$pid" && "$candidate" != "$port_pid" ]] || continue
    runtime_stop_pid "$candidate" "$script" "$name" || true
  done < <(runtime_script_pids "$script")
}
