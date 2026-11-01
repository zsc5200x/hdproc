# HD PS38

从原项目提取的 **PS3838 / 平博独立 Python 项目**，只做浏览器登录、等待人工完成 2FA、读取订单和保存本地数据。没有 WebUI、皇冠模块、跟单引擎或下注入口；此精简版不发送 Telegram 消息。

## 开始使用

要求 Python 3.9 或更高版本，推荐 3.11/3.12。在 macOS 双击 `Setup.command` 安装一次依赖，然后双击 `Start.command`。

命令行安装和启动：

```bash
cd "$HOME/Desktop/HD PS38"
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m playwright install chromium
python run.py init
python run.py watch
```

程序会打开独立 Chromium 窗口。默认不保存账号密码，请在窗口里输入账号、密码及 2FA。验证期间没有自动关窗超时；受保护订单接口确认会话有效后，开始约每 15 秒一次的扫描。不要关闭这个窗口；`Ctrl+C` 可以停止程序并关闭它，Cookie 和订单数据会留在独立的 `data/` 目录。

如果需要自动填账号密码，可编辑本地 `config.json` 的 `username`、`password`，也可通过环境变量 `PS38_USERNAME`、`PS38_PASSWORD` 提供。程序只在你启动登录流程时尝试提交一次；验证码始终手动输入。`config.json` 和 `data/` 已加入 Git 忽略规则。此副本没有携带旧项目的真实账号、Cookie、Telegram 密钥或历史订单。

## 常用命令

以下命令都在项目目录、虚拟环境中执行：

```bash
python run.py doctor          # 检查依赖和浏览器文件；不会连接网站
python run.py login           # 登录/2FA，通过后保存一次读取结果并退出
python run.py watch           # 登录后持续读取
python run.py watch --once    # 完成一次有效读取后退出
python run.py watch --relogin # 清除此副本的 Cookie，重新登录
python run.py status          # 查看采集进程与最近状态
python -m unittest discover -s tests -v  # 离线测试，不访问平博
python tests/smoke_browser.py           # 真实浏览器与本地模拟服务器测试
```

关闭登录窗口会停止采集，请重新运行 `watch`。运行中会话失效时，程序保留窗口等待人工登录，不反复填写密码。如果窗口还在但已无法恢复，可以先 `Ctrl+C`，再运行 `watch --relogin`。

## 配置

`config.json` 由 `init` 从 `config.example.json` 生成，不会覆盖已有配置。

| 字段 | 用途 |
| --- | --- |
| `name` | 本地账号标识，决定 `data/<name>/`；不等于网站用户名 |
| `username`、`password` | 可留空，在浏览器里手动填写 |
| `base_url` | 当前只支持已使用的 `https://www.ps38ag.com` |
| `interval_seconds` | 默认 15，最低 5；请求耗时会使实际间隔稍长 |
| `headless` | 默认 false；首次登录和需要 2FA 时保持 false |
| `minimize_after_login` | 默认 false；true 时验证成功后只最小化窗口 |
| `browser_channel` | 默认空值使用 Playwright Chromium；也可填 chrome 使用本机 Chrome |
| `data_dir` | 默认 data；相对路径相对于配置文件所在目录 |

不同配置可用 `--config /path/to/config.json` 运行；不同账号应使用不同 `name`。同一数据目录有进程锁，重复启动会被拒绝。

## 数据和事件

默认写入 `data/ps3838/`：

| 文件 | 内容 |
| --- | --- |
| `orders.sqlite3` | 事务数据库，保存曾采集的订单与事件，是权威数据源 |
| `orders.jsonl` | 曾采集订单的最新版本，每行一笔，保留原始 `raw` |
| `events.jsonl` | 事件流水：`snapshot` / `new` / `update`，含稳定的 `event_id` |
| `snapshot.json` | 最近一次成功读取的完整响应和解析结果，可能为空 |
| `status.json` | 最近进程状态；用 `status` 命令检查进程是否仍在运行 |
| `profile/` | 此副本自己的浏览器配置和 Cookie |

首次成功读取的每笔订单标记为 `snapshot` 并保存；后续未见过的单号标记为 `new`。同一单号的状态、金额、盘口、赔率等字段变化时保存 `update` 和 `changes`。订单暂时消失再出现，不会重复标记新单；重启后去重信息仍在。接口缺少某字段时按空值保留，不编造状态或结算结果。

SQLite 同时提交订单与事件，JSONL 在提交后以原子替换导出。如果导出中断，下次启动会从数据库重建。后续接 Telegram 等消费者时按 `event_id` 保存消费进度，并默认跳过 `snapshot`。

## 接口和限制

登录沿用原项目的 `/partner/login` 流程，扫描时只允许：

- `/partner-account/keepAlive`
- `/partner-reporting/ddd/total-bets/bet-monitor`

两个调用都沿用原实现的 POST 查询，通过同源浏览器 `fetch` 携带 Cookie 和捕获的 `x-intellifend-secret`。仅检测 Cookie 或跳转 URL 不能代表完成 2FA；需订单接口成功返回有效 JSON 列表。

查询参数保持原实现，包括 `groupType=OWN_DOWNLINE`、`isInPlay=-1`、`productCodes=["SB"]`、`minRisk=0`、`historyTime=60`。这里没有新猜测接口，`historyTime` 的精确服务端单位/上限没有额外验证。

**这不是历史订单补采器。** 停机期间发生、重启后已经不在监控接口返回范围内的订单无法自动补回。订单从列表消失也不能视作结算。以后开发历史补采应先用 Reqable 验证真实历史接口。

## 代码入口

- `ps38/client.py`：持久浏览器、登录、2FA 等待、会话校验、查询接口。
- `ps38/orders.py`：请求参数、订单解析、唯一 ID 和字段变更比较。
- `ps38/storage.py`：事务存储、JSONL 导出和单实例锁。
- `ps38/cli.py`：配置、进程生命周期、持续轮询、状态输出。
- `run.py`：命令行入口。

依赖仅为 Playwright 和 Python 标准库；不引用原项目路径。原项目文件及运行状态保持独立。修改开发时可从这份目录直接开始。
