#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ ! -x .venv/bin/python ]]; then
  echo "请先双击 Setup.command 安装运行环境。"
  read -r -p "按回车退出"
  exit 1
fi
.venv/bin/python run.py init
exec .venv/bin/python -u run.py watch "$@"
