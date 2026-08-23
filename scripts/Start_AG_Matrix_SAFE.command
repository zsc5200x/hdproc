#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_DIR="${ROOT}/app"
BOOTSTRAP_SH="${ROOT}/scripts/_mac_bootstrap.sh"

if [[ ! -f "${APP_DIR}/webui_server.py" ]]; then
  echo "[ERROR] 未找到 ${APP_DIR}/webui_server.py，请确认当前目录是否为项目根目录：${ROOT}"
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
    echo "  PORT=19999 \"${ROOT}/Start_AG_Matrix_SAFE.command\""
    if [[ -t 0 ]]; then read -r -p "按回车退出..." _; fi
    exit 2
  fi
else
  echo "[WARN] 未找到 lsof，跳过端口占用检查。"
fi

echo "[INFO] 打开浏览器：http://${HOST}:${PORT}/"
open "http://${HOST}:${PORT}/" >/dev/null 2>&1 || true

echo "[INFO] 启动 WebUI 服务（关闭本窗口即停止 WebUI）..."
"${PYTHON_CMD}" "webui_server.py" \
  --host "${HOST}" \
  --port "${PORT}" \
  --orders-dir "orders" \
  --webui-dir "webui" \
  --state-dir "state" \
  --config "config.json" \
  --data-dir "data"

if [[ -t 0 ]]; then read -r -p "WebUI 已退出。按回车关闭..." _; fi
