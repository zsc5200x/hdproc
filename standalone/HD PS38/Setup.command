#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ ! -x .venv/bin/python ]]; then
  runtime="${PS38_PYTHON:-python3}"
  "$runtime" -m venv .venv
fi
.venv/bin/python -m pip install -r requirements.txt
PLAYWRIGHT_SKIP_BROWSER_GC=1 .venv/bin/python -m playwright install chromium
.venv/bin/python run.py init
.venv/bin/python run.py doctor
echo "安装完成。双击 Start.command 开始登录和抓单。"
