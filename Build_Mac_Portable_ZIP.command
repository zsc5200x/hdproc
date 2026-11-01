#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
OUT_DIR="${ROOT}/_dist"
TS="$(date +"%Y%m%d_%H%M%S")"
ZIP_PATH="${OUT_DIR}/hdpro_mac_portable_${TS}.zip"

mkdir -p "${OUT_DIR}"
cd "${ROOT}"

if ! command -v zip >/dev/null 2>&1; then
  echo "[ERROR] 当前系统未安装 zip 命令。"
  if [[ -t 0 ]]; then read -r -p "按回车退出..." _; fi
  exit 2
fi

echo "[INFO] 正在打包（自动排除本机运行时缓存/日志/锁文件）..."
zip -rq "${ZIP_PATH}" . \
  -x "*/.DS_Store" \
     ".git/*" \
     ".pytest_cache/*" \
     "_dist/*" \
     "_work/*" \
     "python/*" \
     "ms-playwright/*" \
     "app/.venv/*" \
     "app/.run/*" \
     "app/orders/*" \
     "app/state/*" \
     "app/config.json" \
     "app/data/*" \
     "app/data/*.lock" \
     "app/data/*.tmp.*" \
     "ps3838/config.json" \
     "ps3838/var/*" \
     "ps3838/crown/data/*" \
     "ps3838/crown/.run/*" \
     "data/logs/*" \
     "__pycache__/*" \
     "*.pyc"

echo "[INFO] 打包完成：${ZIP_PATH}"
echo "[INFO] 客户机解压后，双击：Start_AG_Matrix_ALL.command"

if [[ -t 0 ]]; then read -r -p "按回车关闭..." _; fi
