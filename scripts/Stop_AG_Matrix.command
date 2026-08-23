#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_DIR="${ROOT}/app"

if [[ ! -f "${APP_DIR}/app.py" ]]; then
  echo "[ERROR] 未找到 ${APP_DIR}/app.py，请确认当前目录是否为项目根目录：${ROOT}"
  if [[ -t 0 ]]; then read -r -p "按回车退出..." _; fi
  exit 1
fi

VENV_DIR="${APP_DIR}/.venv"
if [[ ! -d "${VENV_DIR}" ]]; then
  echo "[ERROR] 未找到虚拟环境：${VENV_DIR}"
  echo "请先运行一次启动脚本（例如 Start_AG_Matrix_SAFE.command 或 Start_AG_Matrix_ALL.command）以创建并安装依赖。"
  if [[ -t 0 ]]; then read -r -p "按回车退出..." _; fi
  exit 2
fi

cd "${APP_DIR}"
# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"

PYTHON_CMD="python"
if ! command -v "${PYTHON_CMD}" >/dev/null 2>&1; then
  PYTHON_CMD="python3"
fi
if ! command -v "${PYTHON_CMD}" >/dev/null 2>&1; then
  echo "[ERROR] 虚拟环境内未找到 python/python3 可执行文件，请检查 Python 安装。"
  if [[ -t 0 ]]; then read -r -p "按回车退出..." _; fi
  exit 1
fi

export PYTHONUTF8=1
export PYTHONIOENCODING="utf-8"
export PYTHONPATH="${APP_DIR}"

echo "[INFO] 停止全部进程（监听/跟单/WebUI/保活等）..."
"${PYTHON_CMD}" "app.py" stop

# 兼容：如果用户曾启动过 start-core，避免残留 core 进程导致重复 watcher/混乱
"${PYTHON_CMD}" "app.py" stop-core >/dev/null 2>&1 || true

# 兜底：SAFE 模式是直接运行 webui_server.py（不经过 app.py start），以及历史版本可能未正确写入 PID 列表，
# 会导致 python app.py stop 显示 “PID list is empty” 但端口仍被占用。
echo "[INFO] 进一步清理残留进程（SAFE 模式/历史残留）..."

declare -a PIDS_TO_KILL=()

add_pid() {
  local pid="$1"
  if [[ -z "${pid}" ]]; then
    return 0
  fi
  if ! [[ "${pid}" =~ ^[0-9]+$ ]]; then
    return 0
  fi
  # 不要杀自己
  if [[ "${pid}" == "$$" ]]; then
    return 0
  fi
  PIDS_TO_KILL+=("${pid}")
}

collect_by_pgrep() {
  local pat="$1"
  if command -v pgrep >/dev/null 2>&1; then
    while IFS= read -r pid; do
      add_pid "${pid}"
    done < <(pgrep -f "${pat}" 2>/dev/null || true)
  fi
}

# 常见服务进程（尽量精确匹配脚本名，避免误杀其它 Python）
collect_by_pgrep "webui_server.py"
collect_by_pgrep "follow_engine.py"
collect_by_pgrep "member_arsenal_watch.py"
collect_by_pgrep "ag_wmc_watch.py"
collect_by_pgrep "telegram_bot.py"
collect_by_pgrep "settlement_importer.py"

if [[ "${#PIDS_TO_KILL[@]}" -gt 0 ]]; then
  # 去重
  # macOS 默认 bash(3.2) 不支持 mapfile/readarray，这里用命令替换实现去重。
  PIDS_TO_KILL=($(printf "%s\n" "${PIDS_TO_KILL[@]}" | sort -u))
  echo "[INFO] 发送 TERM：${PIDS_TO_KILL[*]}"
  kill -TERM "${PIDS_TO_KILL[@]}" >/dev/null 2>&1 || true
  sleep 2

  declare -a STILL=()
  for pid in "${PIDS_TO_KILL[@]}"; do
    if kill -0 "${pid}" >/dev/null 2>&1; then
      STILL+=("${pid}")
    fi
  done
  if [[ "${#STILL[@]}" -gt 0 ]]; then
    echo "[WARN] 仍有进程存活，发送 KILL：${STILL[*]}"
    kill -KILL "${STILL[@]}" >/dev/null 2>&1 || true
  fi
else
  echo "[INFO] 未发现残留进程。"
fi

if [[ -t 0 ]]; then read -r -p "已停止。按回车关闭..." _; fi
