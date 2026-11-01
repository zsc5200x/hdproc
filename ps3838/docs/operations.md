# 运行、迁移与维护

所有命令均在项目根目录运行。以下涉及启动、停止、迁移、回填或安装定时任务的命令会改变本机状态；`./check.sh` 使用隔离测试目录，`migrate_runtime.py` 不带参数只预览迁移计划。

## 启动与健康检查

```bash
./start.sh
./stop.sh
./dashboard.sh
./crown.sh
```

`start.sh` 启动完整采集链路；`dashboard.sh` 启动主看板和皇冠后端；`crown.sh` 仅启动皇冠后端。使用自定义配置时，为本次终端会话设置同一个文件，保证启动、停止、回填与维护读取一致：

```bash
export PS3838_CONFIG=config.local.json
./start.sh
```

相对配置路径以项目根目录为基准。环境变量覆盖 JSON，JSON 覆盖内置默认值。配置损坏或显式指定文件不存在时，启停脚本会直接报错，不继续操作进程。

运行脚本会确认 PID 的 Python 可执行文件、项目工作目录和目标脚本；PID 文件丢失时会查找本项目已运行的脚本。端口被其他进程占用时直接报错，不会接管或停止该进程。每个服务有启动互斥锁，避免并行启动重复进程；保留的锁只有在其记录的进程已经退出时才自动回收。

按配置地址检查本地接口：

```bash
CONFIG_ASSIGNMENTS="$(.venv/bin/python project_config.py shell)" || exit 1
eval "$CONFIG_ASSIGNMENTS"
curl --noproxy '*' --fail --silent --show-error "$CDP_URL/json/version"
curl --noproxy '*' --fail --silent --show-error "$DASHBOARD_URL/api/health"
curl --noproxy '*' --fail --silent --show-error "$CROWN_URL/api/crown/session"
```

上面适合交互诊断；启动脚本自身会先检查配置子进程的退出码，再读取变量。HTTP 200 只能说明服务接口可访问。数据新鲜度还需检查页面的来源状态、最后成功时间、缓存提示和采集日志。

## 手动登录与会话同步

1. 使用 `./start.sh` 启动共享 Chrome；在平博标签中手动登录。
2. 皇冠已有账号时会按配置补开普通标签页；没有账号时在皇冠设置保存登录地址和账号、打开登录页。
3. 在皇冠站点页面自己输入密码和验证信息。项目不填写表单、不自动点击登录、不保存密码或 TOTP。
4. 保持两个站点标签打开；系统复用当前浏览器会话读取赛事与赔率。
5. 若显示 `session_conflict`，先观察当前标签会话同步，不要仅凭此错误判断其他设备重复登录。

默认皇冠继承平博 `cdp_url`。只有明确需要另一浏览器实例时才设置 `crown_cdp_url` 或 `CROWN_CDP_URL`。`stop.sh` 停止项目 Python 进程但不关闭 Chrome。

## 迁移运行数据

旧布局的默认值继续有效。迁移工具把已知的项目运行数据移入 `var/`，更新实际使用旧路径的配置项，并为常用旧路径保留兼容软链接；不会遍历或搬动任意外部配置路径。

| 内容 | 新布局 |
| --- | --- |
| 当前与轮转采集文件 | `var/capture/` |
| 元数据、实时与导出 CSV | `var/exports/` |
| 历史库与赛事映射库 | `var/db/` |
| 皇冠账号、加密会话和缓存 | `var/crown/` |
| PID、运行状态与日志 | `var/run/`、`var/logs/` |
| 数据库备份 | `var/backups/` |
| 迁移清单 | `var/migrations/时间戳/manifest.json` |

先预览，再停止写入进程并执行迁移：

```bash
.venv/bin/python scripts/migrate_runtime.py
./stop.sh
.venv/bin/python scripts/migrate_runtime.py --apply
./check.sh
./start.sh
```

工具拒绝仍被已知进程或打开文件占用的数据，检查 SQLite 完整性，拒绝覆盖已有迁移目标，并在每步移动前记录清单。发生普通执行异常时会尝试按清单回退；强制终止后仍可用清单恢复。启动后核对首页、两家数据页、联赛/赛事绑定数量和最后更新时间。

迁移属于同一项目目录内的移动，并不替代备份。大文件不会额外复制一份；旧软链接只是兼容入口。`manifest.json` 含迁移前的本机配置，按私有运行数据保管，不提交 Git。不要对 `var/` 或兼容软链接批量删除。

## 回退运行数据

迁移使用项目根的固定文件锁与维护任务互斥，同时检查旧维护锁；维护锁自身读取配置 `run_dir`。请勿在迁移期间启动采集、历史回填或其他写入任务。迁移后旧回填断点仍含原文件绝对路径，下一次显式回填可能重新读取旧文件，数据库唯一约束保持去重。当前历史发现沿用 `capture-*` 命名，建议保留活动文件名 `capture.jsonl`。

使用本次迁移输出的 `manifest.json` 最精确。下面命令自动选取时间最近且状态为 `completed` 或 `running` 的迁移清单，并输出选择结果；如连续做过多次迁移，应确认所选路径对应要回退的批次。

```bash
./stop.sh
MIGRATION_MANIFEST="$(.venv/bin/python - <<'PY'
import json
from pathlib import Path
paths = sorted(Path('var/migrations').glob('*/manifest.json'), reverse=True)
for path in paths:
    if json.loads(path.read_text())['status'] in {'completed', 'running'}:
        print(path)
        break
else:
    raise SystemExit('没有可回退的迁移清单')
PY
)" || exit 1
printf '%s\n' "$MIGRATION_MANIFEST"
.venv/bin/python scripts/migrate_runtime.py --rollback "$MIGRATION_MANIFEST"
./check.sh
./start.sh
```

回退移动当前数据回原位置，并恢复迁移前配置文本；它不会把数据库内容恢复到历史时间点。若迁移后修改了本机配置，先另存配置再回退。旧路径被占用、软链接目标被改变时会中止，避免覆盖新文件。

## 代码与依赖检查

```bash
./check.sh
./check.sh --static-only
```

`check.sh` 执行 Python、前端 JS、Shell 和差异格式检查，并分别调用平博与皇冠虚拟环境运行测试。Python 测试使用临时配置和数据目录，不读取真实账号库。`--static-only` 跳过 Python 单元测试，仍执行前端测试与静态检查。

两套完整版本快照位于 `requirements/pinnacle-macos-py39.lock` 与 `requirements/crown-macos-py314.lock`。不要将两个环境的依赖混装，或为目录整理顺手升级包。升级后先通过统一检查，再更新对应锁文件；锁文件不保证其他操作系统或 Python 主次版本兼容。

## 历史回填与元数据补齐

```bash
.venv/bin/python -u backfill_history.py --max-lines 5000
.venv/bin/python -u backfill_history.py --batch-size 1000
.venv/bin/python metadata_sync.py --limit 5
.venv/bin/python metadata_sync.py --limit 5 --apply
```

回填工具从配置的采集路径发现当前和轮转 JSONL，支持 `.gz` 与 `.zst`，按断点继续并由数据库唯一约束去重。不要同时运行多个回填任务。断点位于配置 `run_dir` 下的 `backfill_history.state.json`。`metadata_sync.py` 不带 `--apply` 时只检查；页面“检查缺失 / 一键补全”使用同一补齐链路。

已有一次性导出入口继续保留：

```bash
.venv/bin/python export_latest.py
.venv/bin/python normalize_latest.py
.venv/bin/python live_snapshot.py --once
```

这些是离线导出功能；主页盘口仍只按香港盘显示。

## 数据库备份与采集压缩

```bash
./maintenance.sh --dry-run
./maintenance.sh --db-only
./maintenance.sh --capture-only
```

维护通过 SQLite Backup API 生成一致性历史库备份，不直接复制正在写入的 SQLite 主文件；备份目录读取 `backup_dir`，保留数读取 `backup_keep`。当前维护脚本备份的是历史赔率库，人工映射库和皇冠会话库不在该每日备份范围内，完整恢复需另行保留这些数据及会话密钥。

采集压缩仅处理符合命名规则的旧轮转文件，跳过当前采集文件；满足年龄和大小阈值后使用 `zstd` 压缩，校验压缩格式、解压 SHA-256 与行数一致后才删除原文件。`--dry-run` 不备份/压缩数据，但会检查所需工具并使用运行锁。

## 安装 macOS 每日维护

先预览实际生成的 launchd 配置；预览不会写入 LaunchAgents、创建日志目录或调用 `launchctl`：

```bash
./install_maintenance.sh --dry-run
```

需要启用每日维护时才执行：

```bash
./install_maintenance.sh
launchctl print "gui/$(id -u)/com.ps3838-wss.maintenance"
```

安装脚本按当前项目目录、选定 Python、`PS3838_CONFIG` 与配置的 `log_dir` 生成 `~/Library/LaunchAgents/com.ps3838-wss.maintenance.plist`。每天按本机时区 04:15 执行；安装不会立即触发维护。仓库内同名 plist 是模板，不要直接复制到 LaunchAgents。

显式维护环境覆盖仅保留 `DASHBOARD_DB_PATH`、`BACKUP_DIR`、`CAPTURE_OUTPUT`、`CAPTURE_PATH`、`RUN_DIR`、`LOG_DIR`，不复制任意 shell 环境或账号秘密。移动项目、更换 Python、选择另一配置文件或更改日志路径后重新安装。

停止自动维护并删除本机任务配置：

```bash
launchctl bootout "gui/$(id -u)/com.ps3838-wss.maintenance"
rm "$HOME/Library/LaunchAgents/com.ps3838-wss.maintenance.plist"
```

## 常用只读接口

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/health` | 主看板健康与状态 |
| GET | `/api/history?event_id=100&hours=24&limit=5000` | 指定比赛历史赔率 |
| GET | `/api/metadata/status` | 元数据补齐状态 |
| GET | `/api/crown/session` | 皇冠会话运行诊断 |
| GET | `/api/crown/events?compact=1` | 皇冠轻量赛事缓存 |

历史查询可附加 `stage`、`market`、`option`、`line` 精确选择盘口；`hours` 最大 8760，`limit` 最大 10000。浏览器访问皇冠 API 时使用主看板同源代理即可，无需另开 9223；需要直接调试皇冠服务时可访问 `http://127.0.0.1:9223`。

## 排障顺序

1. **页面打不开**：检查配置、监听端口和 `dashboard.log` / `crown.log`；不要直接删除 PID 后盲目重启。
2. **页面能打开但数据不新鲜**：检查最后成功时间、缓存标记、CDP 和当前登录标签，再看 `capture.log` / `crown.log`。
3. **皇冠旧会话被拒绝**：保留当前已登录标签，等待会话同步；持续失败再检查会话或手动登录。不能仅凭 `doubleLogin` 判定另一设备登录。
4. **附加盘与主盘不同步**：附加盘独立限流、缓存和后台加载，查看该赛事详情更新时间及错误状态。
5. **赛事名字缺失**：检查元数据筛选，使用小范围 `metadata_sync.py` 检查；不要用占位名称覆盖完整元数据。
6. **迁移失败**：先看具体错误和生成清单；确认服务已停、目标未被占用，再决定继续或回退，不手动删除数据库。
