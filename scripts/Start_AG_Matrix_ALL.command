#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_DIR="${ROOT}/app"
BOOTSTRAP_SH="${ROOT}/scripts/_mac_bootstrap.sh"

if [[ ! -f "${APP_DIR}/app.py" ]]; then
  echo "[ERROR] 未找到 ${APP_DIR}/app.py，请确认当前目录是否为项目根目录：${ROOT}"
  if [[ -t 0 ]]; then read -r -p "按回车退出..." _; fi
  exit 1
fi

if [[ ! -f "${BOOTSTRAP_SH}" ]]; then
  echo "[ERROR] 未找到 ${BOOTSTRAP_SH}"
  if [[ -t 0 ]]; then read -r -p "按回车退出..." _; fi
  exit 1
fi

# shellcheck disable=SC1091
source "${BOOTSTRAP_SH}"
hdzz_prepare_mac_runtime "${ROOT}" "${APP_DIR}"

cd "${APP_DIR}"

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-9999}"

if command -v lsof >/dev/null 2>&1; then
  if lsof -nP -iTCP:"${PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "[ERROR] 端口 ${PORT} 已被占用："
    lsof -nP -iTCP:"${PORT}" -sTCP:LISTEN || true
    echo ""
    echo "解决方案：关闭占用端口的程序，或临时换端口启动："
    echo "  PORT=19999 \"${ROOT}/Start_AG_Matrix_ALL.command\""
    if [[ -t 0 ]]; then read -r -p "按回车退出..." _; fi
    exit 2
  fi
else
  echo "[WARN] 未找到 lsof，跳过端口占用检查。"
fi

echo "[INFO] 启动全部进程（监听/跟单/WebUI/保活等）..."
"${PYTHON_CMD}" "app.py" start --host "${HOST}" --port "${PORT}"

echo "[INFO] 打开浏览器：http://${HOST}:${PORT}/"
open "http://${HOST}:${PORT}/" >/dev/null 2>&1 || true

echo ""
echo "[INFO] 已启动。日志目录：${HDZZ_LOG_DIR}"
echo "[INFO] 停止：在 ${APP_DIR} 目录执行：${PYTHON_CMD} app.py stop"

if [[ -t 0 ]]; then read -r -p "按回车关闭此窗口..." _; fi
