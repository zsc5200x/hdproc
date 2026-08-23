#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
OUT_DIR="${ROOT}/_dist"
PYTHON_BIN="${PYTHON_BIN:-python3}"

if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  echo "[ERROR] 未找到 ${PYTHON_BIN}。请先安装 Python 3。"
  if [[ -t 0 ]]; then read -r -p "按回车关闭..." _; fi
  exit 2
fi

mkdir -p "${OUT_DIR}"

"${PYTHON_BIN}" "${ROOT}/app/tools/build_mac_app_bundle.py" \
  --source-root "${ROOT}" \
  --output-dir "${OUT_DIR}" \
  --bundle-name "AG Matrix"

echo "[INFO] 构建完成：${OUT_DIR}/AG Matrix.app"
echo "[INFO] 你可以把它拖到 Applications，然后像普通应用一样双击打开。"

if [[ -t 0 ]]; then read -r -p "按回车关闭..." _; fi
