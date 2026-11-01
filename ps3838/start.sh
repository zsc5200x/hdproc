#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"
source "$PROJECT_DIR/scripts/runtime.sh"
PYTHON_BIN="${PYTHON_BIN:-$PROJECT_DIR/.venv/bin/python}"
if [[ ! -x "$PYTHON_BIN" && -x "$PROJECT_DIR/../app/.venv/bin/python" ]]; then
  # Merged layout: the main runtime lives in hdproc/app/.venv.
  PYTHON_BIN="$PROJECT_DIR/../app/.venv/bin/python"
fi
CROWN_PYTHON_BIN="${CROWN_PYTHON_BIN:-$PROJECT_DIR/crown/.venv/bin/python}"
runtime_load_config "$PYTHON_BIN"

if [[ ! -x "$CROWN_PYTHON_BIN" ]]; then
  echo "找不到皇冠 Python 虚拟环境：$CROWN_PYTHON_BIN" >&2
  exit 1
fi
if ! "$PYTHON_BIN" -c 'import playwright' >/dev/null 2>&1; then
  echo "当前 Python 环境缺少 playwright：$PYTHON_BIN -m pip install -r requirements.txt" >&2
  exit 1
fi
mkdir -p "$RUN_DIR" "$LOG_DIR"
CHROME_PID_FILE="$RUN_DIR/chrome.pid"
CHROME_STARTED=0

cdp_is_ready() {
  curl --noproxy '*' -fsS --max-time 3 "$CDP_URL/json/version" >/dev/null 2>&1
}

if ! cdp_is_ready; then
  if [[ ! -x "$CHROME_BINARY" ]]; then
    echo "无法启动 Chrome：找不到可执行文件 $CHROME_BINARY" >&2
    echo "请修改 config.json 中的 chrome_binary。" >&2
    exit 1
  fi

  CDP_PORT="$($PYTHON_BIN - "$CDP_URL" <<'PY'
from urllib.parse import urlsplit
import sys

parsed = urlsplit(sys.argv[1])
print(parsed.port or 9222)
PY
  )"

  mkdir -p "$CHROME_PROFILE_DIR"
  echo "Chrome CDP 未启动，正在启动项目专用 Chrome。"
  echo "Chrome 配置目录：$CHROME_PROFILE_DIR"
  echo "自动打开：$START_URL"
  nohup "$CHROME_BINARY" \
    --remote-debugging-port="$CDP_PORT" \
    --user-data-dir="$CHROME_PROFILE_DIR" \
    --no-first-run \
    "$START_URL" >>"$LOG_DIR/chrome.log" 2>&1 &
  chrome_pid=$!
  CHROME_STARTED=1
  echo "$chrome_pid" >"$CHROME_PID_FILE"

  ready=0
  for _ in {1..20}; do
    if cdp_is_ready; then
      ready=1
      break
    fi
    sleep 0.5
  done
  if [[ "$ready" != 1 ]]; then
    echo "Chrome 已启动但 CDP 仍不可用：$CDP_URL" >&2
    echo "请查看日志：$LOG_DIR/chrome.log" >&2
    exit 1
  fi
else
  echo "Chrome CDP 已可用：$CDP_URL"
fi

# CDP 已存在时，优先通过 DevTools 的 /json/new 打开目标赛事页面，
# 这样不会因为重复执行 start.sh 而创建多个相同标签页。
if [[ "$CHROME_STARTED" != 1 && -n "$START_URL" && -x "$CHROME_BINARY" ]]; then
  START_HOST="$($PYTHON_BIN - "$START_URL" <<'PY'
from urllib.parse import urlsplit
import sys

print(urlsplit(sys.argv[1]).hostname or "")
PY
  )"
  if ! curl --noproxy '*' -fsS --max-time 3 "$CDP_URL/json/list" \
    | grep -F '"url"' \
    | grep -F -- "$START_HOST" >/dev/null 2>&1; then
    ENCODED_START_URL="$($PYTHON_BIN - "$START_URL" <<'PY'
from urllib.parse import quote
import sys

print(quote(sys.argv[1], safe=""))
PY
    )"
    if ! curl --noproxy '*' -fsS --max-time 3 -X PUT \
      "$CDP_URL/json/new?$ENCODED_START_URL" >/dev/null 2>&1; then
      # 旧版 Chrome 可能不支持 /json/new 的 PUT 方式，使用 Chrome
      # 命令行作为兼容回退；Chrome 会把 URL 转交给已有实例。
      nohup "$CHROME_BINARY" \
        --user-data-dir="$CHROME_PROFILE_DIR" \
        "$START_URL" >>"$LOG_DIR/chrome.log" 2>&1 &
    fi
  fi
fi

if [[ ! -f "$EVENTS_FILE" ]]; then
  echo "缺少赛事映射文件：$EVENTS_FILE" >&2
  exit 1
fi

mkdir -p "$(dirname "$CAPTURE_PATH")"
touch "$CAPTURE_PATH"

# 看板优先：PS38 页面只依赖 8787 看板；皇冠起不来时看板仍应可用，
# 否则整个标签页只会显示“连接失败”，还得手工再点一次启动。
DASHBOARD_OK=1
if ! runtime_start dashboard "$PROJECT_DIR/dashboard_server.py" "$PYTHON_BIN" \
  "$DASHBOARD_URL/api/health" "$DASHBOARD_PORT" \
  --host "$DASHBOARD_HOST" --port "$DASHBOARD_PORT"; then
  DASHBOARD_OK=0
  echo "dashboard 未能就绪，请查看 $LOG_DIR/dashboard.log。" >&2
fi

# 平博赔率采集已停用：后台来单 + 皇冠快照即可生成跟单信号。
CROWN_OK=0
if CROWN_PYTHON_BIN="$CROWN_PYTHON_BIN" "$PROJECT_DIR/crown.sh"; then
  CROWN_OK=1
  if ! "$CROWN_PYTHON_BIN" "$PROJECT_DIR/crown/ensure_login_tab.py" \
    --cdp-url "$CROWN_CDP_URL" \
    --service-url "$CROWN_URL"; then
    echo "皇冠页面标签页未能打开；请查看皇冠服务状态后重试。" >&2
  fi
else
  echo "皇冠服务未能就绪（不影响 PS38 看板），请查看 $LOG_DIR/crown.log。" >&2
fi
# 平博赔率快照已停用：不再维护 latest_odds_live.csv。
if [[ -f "$PROJECT_DIR/var/run/hd_partner_sync.enabled" ]]; then
  echo "外部 partner_orders 轮询已由 HD 平博节点接管，跳过。"
else
  runtime_start partner_orders "$PROJECT_DIR/partner_orders_poll.py" "$PYTHON_BIN" "" "" \
    || echo "partner_orders 未能启动；请确认第三个标签页已登录代理后台。" >&2
fi

if [[ "$DASHBOARD_OK" != 1 ]]; then
  echo "看板未就绪，启动失败；请查看 $LOG_DIR/dashboard.log 后重试。" >&2
  exit 1
fi

echo
echo "项目进程已启动，看板已就绪。数据是否更新请查看看板状态。"
echo "统一看板：$DASHBOARD_URL"
if [[ "$CROWN_OK" != 1 ]]; then
  echo "皇冠服务未就绪：请查看 $LOG_DIR/crown.log（看板不受影响）" >&2
fi
echo "停止命令：$PROJECT_DIR/stop.sh"
