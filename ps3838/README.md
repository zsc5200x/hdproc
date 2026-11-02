# 平博与皇冠赛事看板

项目通过 Chrome DevTools Protocol（CDP）复用用户已登录的普通 Chrome 标签页，采集平博 WSS 赔率与皇冠足球赛事，提供实时看板、历史查询及人工赛事绑定。

账号密码、安全密码和 TOTP 由用户在站点页面手动输入。项目不自动填写或提交登录表单，不提供下注提交逻辑。“我的赛事”展示平博代理订单：历史注单从 `partner_history_from`（默认 2026-07-01）回填，常驻服务每 1 秒轮询未结算订单，新单到达时在看板提示并刷新列表。


> **已并入 HD 主项目**：源码位于 `hdproc/ps3838/`。正式运行时由 `hdproc/app/app.py start|stop|restart` 统一管理，HD WebUI 通过同源 `/ps3838/` 内嵌看板。此目录下的 `start.sh`/`stop.sh` 仍可用于单独调试。
>
> 运行数据已迁移到 `hdproc/ps3838/var/`，旧目录 `~/ps3838-wss` 仅保留作回滚备份。

已有数据的项目请先阅读 [运行数据迁移与回退](docs/operations.md#迁移运行数据)，不要用示例配置覆盖现有 `config.json`。

## 日常使用

以下命令均在项目根目录运行：

```bash
./start.sh
./stop.sh
```

默认入口是 HD WebUI 的 `http://127.0.0.1:9999/ps3838/`：看板监听 `var/run/dashboard.sock`（Unix domain socket，不再占用 8787 端口），由 9999 同源转发。使用 `./dashboard.sh` 可单独启动看板；皇冠联动需要自行运行外部兼容服务并在配置中指定地址。停止脚本不会关闭 Chrome。开发时仍可用 `python dashboard_server.py --host 127.0.0.1 --port 8787` 以独立 TCP 模式运行看板。

完整启动顺序为：检查配置与 Python 环境 → 确认/启动 Chrome CDP → 启动平博采集 → 启动主看板 → 启动实时快照。皇冠联动仅在 `crown_enabled=true` 时通过 `crown_host/crown_port` 连接外部兼容服务；已有进程会核对项目归属。HTTP 服务只有通过接口健康检查才会报告就绪，进程就绪不等于数据新鲜。

平博通过 CDP 采集；皇冠页面和 API 由外部兼容服务提供，本仓库不再启动或管理本地皇冠进程。

“我的赛事”的订单采集复用同一个 Chrome 里已登录的 `ps38ag.com` 代理后台标签页，不需要复制 Cookie。首次使用先手工回填历史注单（会按配置的 `partner_history_from` 到当天逐月读取）：

```bash
.venv/bin/python backfill_partner_orders.py
```

之后 `./start.sh` 会同时拉起常驻轮询 `partner_orders_poll.py`，它每 1 秒读取已配置代理的未结算订单，每 300 秒回查近 7 天已结算状态，新单事件写入 `var/db/partner_orders.sqlite3`。代理后台会话失效时轮询器不会退出，会每 15 秒重试，重新登录后自动恢复。

合并后的正式模式不再启动 `capture.py` 和 `live_snapshot.py`：跟单信号直接来自代理后台订单 + 皇冠快照，不需要持续采集平博赔率。

### 用 HD 平博节点直接同步代理后台

HD 的「接入节点 → 平博 / PS3838」节点也可以直接承担代理后台来单：

1. 节点用代理后台父账号登录一次，会话保存在 `app/data/ps3838/profiles/<节点名>/`。
2. 勾选「用本节点会话直接同步代理后台来单」，代理代码可留空自动发现。
3. 节点会调用后台 `profile`、`win-loss-detail`、`bet-list/v2`，写入 `ps3838/var/db/partner_orders.sqlite3`。
4. 启用后会在 `var/run/hd_partner_sync.enabled` 留标记，`start.sh` 下次启动会跳过外部 `partner_orders_poll.py`；当前已运行的外部轮询仍需手动停一次。

这条链路仍然只读后台订单，不会下注或修改订单；下注由 HD 跟单引擎的会员会话执行。

### 新单 TG 推送

在「系统设置 → 新单 TG 推送」中填写机器人 Token、群 Chat ID，打开推送开关后保存，再点「发送测试消息」确认能收到。

- **机器人 Token**：从 Telegram 的 @BotFather 创建机器人后获得，格式为 `123456789:AA...`；保存后接口只返回“已配置”，不会回显原始 Token。
- **群 Chat ID**：群组 ID 以 `-100` 开头；公开群也可以填 `@群用户名`。机器人必须先被拉进群并允许发言。
- **代理地址（可选）**：本机直连 `api.telegram.org` 不通时填写，例如 `http://127.0.0.1:7890`；不填则走系统环境变量里的代理。
- 新单按「同比赛 + 同盘口 + 同比分、20 秒内」合并成一条推送，版式为：新订单提醒 → 赛事信息（平博联赛 · 皇冠联赛）→ 比分 → 盘口对照（平博水位区间 / 皇冠对手水位）→ 合水 → 合并总金额 → 订单汇总（单数/会员数）→ 合并时间（开始/结束/耗时）；皇冠没匹配到或没挂同线时会注明。等满 20 秒后发送，失败按整组重试并记录在设置页。
- 只有开启推送后新到达的订单会推送，不会把库里的历史订单一次性刷进群；推送失败会自动重试，结果记录在设置页。
- Token 保存在本机 `config.json`，文件权限会收紧为仅本人可读；建议不要把它复制到共享位置。

## 第一次准备

仓库只保留 PS3838 主进程使用的 Python 环境；皇冠联动由外部兼容服务负责：

| 环境 | 当前基线 | 完整依赖快照 |
| --- | --- | --- |
| PS3838、合并与主看板 | macOS / Python 3.9 | `requirements/pinnacle-macos-py39.lock` |

已有 `.venv` 时无需重新创建。新环境在安装 Python 3.9 后运行：

```bash
python3.9 -m venv .venv
.venv/bin/python -m pip install -r requirements/pinnacle-macos-py39.lock
```

锁文件记录当前 macOS 环境的完整版本，不是带下载哈希的跨平台锁；切换 Python 或系统时先在独立环境验证。程序通过 CDP 连接已安装的 Chrome，正常共享浏览器流程无需执行 `playwright install`。前端检查需要 Node.js；旧采集文件压缩需要 `zstd`。

只有全新安装、没有本机配置和数据时，才复制示例配置：

```bash
test -f config.json || cp config.example.json config.json
mkdir -p var/capture var/exports var/db var/crown var/run var/logs var/backups
```

示例将运行数据统一放在 `var/`。首次启动前应将已有的赛事元数据 CSV 放到配置的 `events_file` 路径；`start.sh` 会创建采集文件，但不会编造赛事元数据。如果已有根目录数据，使用迁移工具处理，不要手工复制正在使用的数据库。

## 统一配置

配置入口为 `project_config.py`，优先级：**非空环境变量 > `PS3838_CONFIG` 选定 JSON > 内置默认值**。未指定时读取项目根目录 `config.json`；该默认文件可以不存在。显式指定的文件不存在、JSON 损坏或端口非法时直接中止，不会悄悄启动默认环境。

```bash
PS3838_CONFIG=config.local.json ./start.sh
DASHBOARD_PORT=8788 CROWN_PORT=8888 ./start.sh
.venv/bin/python project_config.py shell
```

相对配置路径和相对数据路径均以项目根目录为基准，不随调用命令的当前目录变化。`shell` 子命令仅输出固定白名单、经过 shell 引号转义的启动变量，不导出任意配置字段。

| 配置项 | 环境变量 | 默认或行为 |
| --- | --- | --- |
| `cdp_url` | `CAPTURE_CDP_URL`，其次 `CDP_URL` | `http://127.0.0.1:9222` |
| `crown_cdp_url` | `CROWN_CDP_URL` | 留空则继承平博 CDP |
| `dashboard_host / dashboard_port` | `DASHBOARD_HOST / DASHBOARD_PORT` | 仅独立 TCP 模式使用（默认 `127.0.0.1:8787`）；合并模式走 `var/run/dashboard.sock` |
| `dashboard_url` | — | 合并模式下设为 `http://127.0.0.1:9999/ps3838`，供采集进程上报 presence |
| `crown_enabled` | — | 是否启用外部皇冠兼容服务，默认 `false` |
| `crown_host / crown_port` | `CROWN_HOST / CROWN_PORT` | 外部兼容服务地址，默认 `127.0.0.1:9223` |
| `capture_path` | `CAPTURE_OUTPUT`，其次 `CAPTURE_PATH` | 原始采集路径 |
| `events_file / live_snapshot_file` | `EVENTS_FILE / LIVE_SNAPSHOT_FILE` | 赛事元数据与实时快照 |
| `dashboard_db_path / merge_db_path` | `DASHBOARD_DB_PATH / MERGE_DB_PATH` | 历史库与人工映射库 |
| `partner_orders_db_path` | `PARTNER_ORDERS_DB_PATH` | 平博代理订单库 |
| `partner_backend_url` | `PARTNER_BACKEND_URL` | 代理后台第三个标签页地址 |
| `partner_agents` | `PARTNER_AGENTS` | 轮询的代理代码，默认 `TLH0R02/03/04/28` |
| `partner_history_from` | `PARTNER_HISTORY_FROM` | 历史回填起点，默认 `2026-07-01` |
| `partner_open_interval_seconds` | `PARTNER_OPEN_INTERVAL_SECONDS` | OPEN 轮询目标间隔，默认 `1` |
| `partner_settled_interval_seconds` | `PARTNER_SETTLED_INTERVAL_SECONDS` | SETTLED 回查间隔，默认 `300` |
| `local_signal_push_enabled` | `LOCAL_SIGNAL_PUSH_ENABLED` | 是否把新单批量推送到 HD 本地交易节点，默认关闭 |
| `local_signal_push_url` | `LOCAL_SIGNAL_PUSH_URL` | HD 节点接收地址，默认 `http://127.0.0.1:9999/api/local-signal/push` |
| `local_signal_push_token` | `LOCAL_SIGNAL_PUSH_TOKEN` | HD 节点 Token，可留空 |
| `local_signal_push_target` | `LOCAL_SIGNAL_PUSH_TARGET` | HD 侧订单目标名，默认 `ps3838` |
| `local_signal_push_require_gid` | — | 默认开启；没有 HGA/HD `gid` 时拒绝发送 |
| `local_signal_push_interval_seconds` | `LOCAL_SIGNAL_PUSH_INTERVAL_SECONDS` | 桥接轮询间隔，默认 `3` 秒 |
| `local_signal_push_gid_map_file` | `LOCAL_SIGNAL_PUSH_GID_MAP_FILE` | 人工赛事映射 JSON，默认 `var/db/local_signal_gid_map.json` |
| `local_signal_push_state_db` | `LOCAL_SIGNAL_PUSH_STATE_DB` | 桥接游标和投递状态库 |
| `run_dir / log_dir / backup_dir` | `RUN_DIR / LOG_DIR / BACKUP_DIR` | PID、日志与备份目录 |
| `crown_data_dir` | `CROWN_DATA_DIR` | 皇冠账号、加密会话与缓存目录 |

旧配置 `fixture_merge_db_path`、`events_path`、`output`、`latest_output`、`normalized_output` 仍可读取；新配置使用规范字段。内置默认值继续兼容原根目录数据、`.run/`、`logs/` 和旧运行数据目录，复制新示例不会自动迁移旧数据。

## 页面和数据边界

合并进 HD 后，内嵌终端只保留以下两个页面：

- **我的赛事**：展示平博代理订单，默认未结算优先；顶部汇总投注总量、已结算输赢、未结算/已结算单数，并按代理列出各自的投注量与输赢。明细含联赛、主队 vs 客队、盘口、水位、金额、输赢、订单号、代理、会员、结果与下单时间；已结算订单显示胜/负/赢半/输半/平/退还及对应输赢金额。金额只显示数值、不带币种后缀；盘口按中文习惯显示：大小球写作“大3/3.5”“小3.5”，让球写作“法马利康+0.5”“杜塞尔多夫-0.5/1”，四分之一盘展开为相邻半球（3.25→3/3.5、-0.25→-0/0.5）。支持状态切换、搜索、代理筛选、日期范围（今天/近7天/近30天/自定义），以及下单/结算口径、比赛/推送视角与 GMT+8/GMT-4 时区切换（默认 GMT-4，结算口径 + GMT-4 可与代理后台盈亏报表对齐；推送视角展示最近 200 条 Telegram 推送及其订单明细）；新单由常驻轮询写入本地库并提示。“统计口径”旁的“比赛视角”（默认视图）按比赛（队名 + 开赛时间）聚合订单，列出范围内全部比赛并**按最近下单时间**排列：哪场来了新单就自动置顶，15 秒内队名旁标“新”；有新单时列表约 3 秒自动刷新，且保持已展开的比赛不收起。列表含该场不同盘口数（方向或线位不同各算 1 个）与金额；点开一场先按盘口汇总（盘口 / 数量 / 水位 / 皇冠对手 / 合水 / 比分 / 金额 / 会员 / 时间），再展开“逐单明细”：同盘口、同比分且 20 秒内的订单合并成一条（显示首单时间、总金额、单数、会员），并同样带上皇冠同线对手盘水位与合水（合水 = 平博水位 + 皇冠对手水位；平博小2.5 → 皇冠大2.5，取皇冠实时挂盘，皇冠没挂同一条线时显示“—”；未开启皇冠附加盘时，打开一场或推送新单会针对该场单独点播一次皇冠附加盘（只查这一场，不打开全局采集），仍没有这条线才显示“—”），点该条展开每单的时间、水位、金额、会员、结果与订单号。比赛视角保留平博原名，括号内实时匹配皇冠名称：联赛按联赛绑定翻译，主客队用皇冠当前赛事列表按开赛时间（±20 分钟）、联赛、队名匹配，匹配不到显示“（未匹配）”。每场比赛**第一张单到达时**（约 3 秒内）就会匹配皇冠并把皇冠联赛、主客队和当时的盘口/水位写入 `crown_fixture_snapshots` 快照；之后即使皇冠把这场下架，页面仍用快照显示皇冠名称和对手水位，并在展开标题标注“皇冠快照”时间，从未匹配到过快照的历史比赛才显示未匹配。点“下单时间/结算时间”可切回逐单列表。
- **系统设置**：运行概况与数据维护、界面偏好、皇冠附加盘开关、采集/存储参数、新单 TG 推送和连接信息。

> 主页、平博数据、皇冠数据、赛事合并四个页面已删除。平博采集和外部皇冠兼容服务只用于“我的赛事”的订单/对手水位和本地桥接，不再提供独立展示页。

### 推送到 HD 本地交易节点

项目根目录的 `local_signal_push.py` 会复用“我的赛事”新单游标，把“同比赛 + 同盘口 + 同比分、20 秒内”的订单**合并成一单**再推送：金额为合计、`orderId` 为随机 8 位数字（本地 `signal_ids` 表去重，重试时保持不变），并带上皇冠同线对手盘字段（`crownLeague`、`crownHome`、`crownAway`、`crownOpponent`、`crownOdds`、`combinedWater`、`crownSnapshotAt`），另有 `orderCount`、`oddsRange` 和原始单号列表。

桥接默认关闭。打开前必须先在 `hdproc` 的「接入节点 → 本地桥接」中确认接收地址和 Token。PS3838 与 HGA/HD 使用不同的赛事 ID 空间，不能把 `eventDate`、订单号或 PS3838 `event_id` 当作 HD `gid`；默认 `local_signal_push_require_gid=true`，没有人工映射就只记录拒绝原因，不会发送。映射文件可用订单号、`代理:会员:订单号` 或 `队名|event_ms` 作为键，值为 HGA/HD 的赛事 ID，例如：

```json
{
  "TLH0R03:TLH0R03MH3:3831870304": "HGA_EVENT_ID"
}
```

确认映射和小额策略后，再将 `local_signal_push_enabled` 设为 `true` 并重启看板。桥接状态可查看 `GET /api/local-signal-push/status`；真实跟单由 HD 项目的 `follow_engine` 执行，发送有效 GID 会产生不可逆下注行为。

平台 ID 属于各自编号空间，不能直接相等匹配。人工联赛和赛事映射保存在 `merge_db_path` 指定的 SQLite 中，不改写两家原始赛事。匹配逻辑默认将平博 `Asia/Shanghai` 与皇冠 `America/New_York` 转换到具体时刻比较，再以北京时间显示。站点时区不同可设置 `PS3838_EVENT_TIMEZONE`、`CROWN_EVENT_TIMEZONE` 和 `MERGE_DISPLAY_TIMEZONE`。

皇冠列表与附加盘有不同刷新节奏；前端读到缓存时会显示最后成功时间。数据时间不变化时应检查来源状态与日志，不能把“服务已启动”视为采集成功。

## 项目结构

```text
capture.py / live_snapshot.py  平博赔率采集与快照（旧页面功能，合并模式默认不启动）
dashboard_server.py            主看板 HTTP 入口
fixture_merge.py               旧导入路径兼容入口
project_config.py             配置、路径与服务地址
src/core/                     常量、时间等公共基础
src/providers/pinnacle/        平博数据解析
src/services/                 赔率状态、可见性、来源新鲜度与赛事匹配
src/storage/                  历史 SQLite 与联赛/赛事映射存储
crown_proxy.py                外部皇冠兼容服务的同源代理
dashboard/static/modules/     页面路由、平博、合并、设置等前端模块
scripts/runtime.sh            启停、健康检查、进程归属与启动互斥
scripts/check.py               统一静态检查与隔离测试
scripts/migrate_runtime.py     可回退的本机运行数据迁移
requirements/                 两套现有环境的完整依赖版本快照
tests/                        主模块、配置、启动与前端回归测试
docs/operations.md            运维、迁移、回退和排障
var/                          新布局运行数据，不提交 Git
```

本次按职责拆分现有代码，保留原入口和兼容导入。后续业务代码放在对应 provider/service/storage 或前端页面模块，不继续堆入 HTTP 入口。皇冠实现由外部兼容服务提供，仓库仅保留固定路径代理。

## 修改后检查

```bash
./check.sh
./check.sh --static-only
```

完整检查执行 Python 静态解析、JavaScript 语法与前端测试、Shell 语法、Git 差异格式和单元测试。测试配置指向临时目录，不使用真实账号库、历史库或采集文件，不会启动/停止在线服务。

如 Python 路径不同：

```bash
PYTHON_BIN=/usr/local/bin/python3.9 ./check.sh
```

## 常见问题

**皇冠提示不可用或重复登录**：先确认 `crown_enabled=true`、`crown_host/crown_port` 指向可用的外部兼容服务，再按该服务的日志和会话说明处理。

**已启动但赔率不更新**：先核对 CDP、外部皇冠服务连通性和来源最后成功时间，再查看配置 `log_dir` 中的 `capture.log`、`dashboard.log` 日志。看板保留旧缓存是为了可见性，不代表旧缓存仍是当前报价。

**显式配置报错**：修复 `PS3838_CONFIG` 路径或 JSON 后再启动。启动和停止脚本都会在配置错误时中止，避免操作错误环境。

更多命令见 [运维指南](docs/operations.md)。皇冠接口由外部兼容服务提供。
