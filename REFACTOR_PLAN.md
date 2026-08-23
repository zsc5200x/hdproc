# 项目重构优化建议书

> 基于代码库深度分析，版本日期：2026-04-01

---

## 一、项目当前情况概览

### 整体结构

项目是一套**体育赛事注单监听与自动跟单执行系统**，由以下几个独立子系统组成：

- **Python 核心应用**（`app/` + `src/hdzz/`）：多进程管理器统一调度多个后台工作子进程
- **Node.js 抓包服务**（`cc/`）：独立 Express 服务，负责与 HGA 平台的网络会话
- **信号中继模块**（`ook/`）：信号转发器与拉取器，与外部系统对接
- **Web 仪表板**（`app/webui/`）：纯 HTML/JS 单文件前端

### 主要模块

| 模块 | 路径 | 核心职责 |
|------|------|---------|
| 进程管理器 | `app/app.py` | 启停和监控所有子进程 |
| 注单监听 | `src/hdzz/core/watcher/` | 轮询 AG 后台接口，写入 JSONL 订单流水 |
| 跟单引擎 | `src/hdzz/core/follower/` | 读取订单、匹配盘口、调用接口下注 |
| 会员续期 | `src/hdzz/core/member/` | Playwright 自动登录，刷新 Cookie |
| WebUI 服务 | `src/hdzz/api/webui/` | REST API + WebSocket 推送 |
| Telegram 机器人 | `src/hdzz/api/telegram/` | 订单通知、付费权限、跟单操作 |
| 结算导入 | `src/hdzz/core/settlement/` | 外部结算数据导入 |

### 当前架构明显特点

1. **经历过一次不完整的代码迁移**：业务代码从 `app/` 迁移到 `src/hdzz/`，但在 `app/` 下保留了大量转发入口和 `_legacy.py` 遗留文件，形成了"两层皮"架构
2. **以文件为单位积累，未做模块划分**：核心文件动辄数千行，未随业务增长做切割
3. **打包与开发混在一起**：构建脚本、发布产物、运行时数据与源码散落在同一目录树中

---

## 二、当前存在的主要问题

### 2.1 目录结构混乱

**问题一：两个并行的 data 目录，职责模糊**

```
/data/               ← 顶层，被 .gitignore 忽略，实际用于什么不清晰
/app/data/           ← 应用层，混放配置文件、运行状态、JSONL 日志
/app/orders/         ← 订单流水，独立于 /app/data/，位置反直觉
/app/.run/           ← 进程 PID 和运行时状态，隐藏目录
```

数据类内容分散在四个地方，任何需要清理数据或排查问题的人，都要先摸清这四个目录的关系，认知负担极高。

**问题二：构建脚本和启动脚本散落在根目录**

根目录有 8 个 `.command`/`.bat` 脚本，与源代码混在一起。这是"桌面开发"的残留习惯，在多人维护或 CI/CD 场景下会造成混乱。

**问题三：`_dist/` 打包产物未加入 `.gitignore`**

`_dist/` 目录当前约 3.1GB，包含打包好的 macOS `.app` 和 Windows 便携版，但未被忽略，存在被意外提交到 Git 仓库的风险。

---

### 2.2 模块职责混乱

**问题一：`app/` 目录职责不清**

`app/` 目录下目前混放了三类完全不同性质的文件：
- **纯转发入口**（如 `webui_server.py`，28 行，只有一个 import 和一次函数调用）
- **真实业务代码**（如 `member_client.py`、`playwright_proxy.py`）
- **遗留代码**（6 个 `_legacy.py` 文件）

进入 `app/` 目录看到 50+ 个 `.py` 文件，无法快速判断哪些是入口、哪些是真正的业务。

**问题二：`ook/` 模块孤悬在外**

信号中继模块 `ook/` 有独立的业务逻辑（`signal_forwarder.py` 428 行、`signal_puller.py` 469 行），但它既不在 `src/hdzz/` 下，也不在 `app/` 下，是一个孤立存在的目录。它与主系统的关系、是否被 `app.py` 管理、是否需要独立部署——这些对新人来说完全不透明。

---

### 2.3 存在大量重复代码和遗留代码

**遗留文件共 13,284 行，全部在 `app/` 下：**

| 文件 | 行数 |
|------|------|
| `follow_engine_legacy.py` | 5,934 |
| `telegram_bot_legacy.py` | 2,414 |
| `webui_server_legacy.py` | 1,852 |
| `ag_wmc_watch_legacy.py` | 1,717 |
| `member_arsenal_watch_legacy.py` | 979 |
| `settlement_importer_legacy.py` | 388 |
| **合计** | **13,284** |

这批代码已被 `src/hdzz/` 取代，但仍保留在仓库中占位，导致：任何全局搜索、grep、AI 辅助分析都会受到干扰，维护者也难以分清当前哪个版本是"真正活着的"。

**转发层重复模式（7 个文件，每个大同小异）：**

```python
# app/webui_server.py（28行，全部都是这种结构）
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "src"))
from hdzz.api.webui.server import main
if __name__ == "__main__":
    main()
```

7 个文件都是同一个 bootstrap + import + main() 模式，完全可以用一个通用入口替代。

---

### 2.4 命名不统一

- 目录名混用风格：`watcher`/`follower`/`member`（英文功能名）vs `ook`（无意义缩写）vs `cc`（无意义缩写）
- 文件名混用风格：`ag_wmc_watch.py`（带平台前缀）vs `webui_server.py`（功能名）
- 测试文件 44 个平铺在 `tests/` 根目录，无子目录分组，目录本身无法传达测试的层次结构

---

### 2.5 配置管理不规范

当前配置分散在四处，职责界定模糊：

| 位置 | 存放内容 | 问题 |
|------|---------|------|
| `app/config.json` | 账号凭据、Telegram token、代理 | 含大量敏感信息，缺乏字段注释 |
| `app/data/*.json` | 跟单策略、盘口缓存、会话状态 | 配置文件与运行时产物混在一起 |
| `app/.run/*.json` | 进程 PID、Bot 状态 | 隐藏目录，和日志混在一起 |
| 环境变量 | `HDZZ_APP_DIR`、`HDZZ_LOG_DIR` | 有但未文档化 |

最大的问题是：**配置（需要人工编辑的）** 和 **状态（运行时产生的）** 没有清晰区分，导致误删或误提交的风险。

---

### 2.6 存在严重的"上帝文件"

| 文件 | 行数 | 职责混乱表现 |
|------|------|-------------|
| `src/hdzz/core/follower/engine.py` | **7,663 行** | 同时包含：主循环、盘口解析、消息模板、Telegram 格式化、缓存逻辑、重试调度、结算处理 |
| `src/hdzz/api/webui/server.py` | **5,007 行** | 同时包含：路由定义、业务逻辑、数据聚合、WebSocket 管理、认证处理 |
| `src/hdzz/api/telegram/bot.py` | **3,473 行** | 同时包含：命令处理、消息模板、状态管理、权限控制 |
| `app/webui/index.html` | **~4,149 行** | 内嵌 1000+ 行 CSS + 完整 DOM + 内联 JS，单文件即整个前端 |

`engine.py` 的 7,663 行是这个项目最大的维护炸弹。任何人要修改跟单逻辑，都必须在这一个文件中导航数千行代码。

---

### 2.7 前后端耦合过重，前端架构落后

- **前端无构建工具**：通过 CDN 引入 Tailwind CSS、Vue 3、Font Awesome，意味着离线环境不可用，且版本不受控
- **Vue 使用方式不规范**：`index.html` 内用 `Vue.createApp()` 全局挂载，与 DOM 操作混用，不是 SFC 组件化方式
- **CSS 内联在 HTML 中**：约 1000 行 CSS 直接写在 `<style>` 标签里，与业务 HTML 耦合
- **备份文件混入源码**：`webui/` 目录下有多个 `.backup`、`.bak` 文件，应由 Git 管理版本，不应手动备份

---

### 2.8 日志未做管理，存在超大日志文件

```
webui_server.err.log     429 MB   ← 严重，出现大量重复错误未被修复
follow_engine.out.log    578 MB   ← 信息量过大，缺乏日志分级
settlement_importer.err  206 MB   ← 高错误率信号
```

没有日志轮换策略（`RotatingFileHandler` 或 `logrotate`），日志会无限增长直到磁盘耗尽。

---

## 三、重构优化建议

### 3.1 应优先解决的问题

1. **清理 13,284 行遗留代码**：`_legacy.py` 系列在确认业务正常运行后直接删除，这是零风险的减熵操作
2. **修复导致 429MB 错误日志的根本原因**：`webui_server.err.log` 增长速度意味着某个错误在高频静默重复，这是潜在的运行时 bug，应立即排查
3. **统一配置层**：把"需要人工编辑的配置"和"运行时自动产生的状态文件"分开存放
4. **`.gitignore` 补全 `_dist/`**：防止 3GB+ 打包产物意外入库

### 3.2 建议拆分的模块

**`src/hdzz/core/follower/engine.py`（7,663 行）**——最迫切的拆分对象，建议按职责切割为：

```
follower/
├── engine.py            ← 只保留主循环和协调逻辑（目标 < 500 行）
├── strategy.py          ← 已有，盘口策略计算
├── executor.py          ← 已有，下注 HTTP 调用
├── models.py            ← 已有，数据模型
├── message.py           ← 新拆出，Telegram 消息模板和格式化
├── cache.py             ← 新拆出，游戏上下文缓存（合并 game_ctx_cache）
├── parser.py            ← 新拆出，盘口解析逻辑（合并 game_more_resolver）
├── retry.py             ← 已有，重试调度（retry_scheduler）
└── quote.py             ← 已有，报价管理（quote_manager）
```

**`src/hdzz/api/webui/server.py`（5,007 行）**：

```
api/webui/
├── server.py            ← 只保留服务启动和路由注册（< 200 行）
├── routes/
│   ├── orders.py        ← 订单相关路由
│   ├── follow.py        ← 跟单相关路由
│   ├── member.py        ← 会员相关路由
│   └── system.py        ← 系统/诊断路由
└── ws.py                ← WebSocket 管理
```

### 3.3 建议重新整理的目录

**将 `app/` 下的业务代码迁移完成，消除混乱层**：

当前 `app/` 下的真实业务文件（`member_client.py`、`playwright_proxy.py`、`tg_status_log.py` 等）应归入 `src/hdzz/` 的对应模块，而不是散落在 `app/`。`app/` 应只保留：
- `app.py`（进程管理器）
- `config.json`（主配置）
- `webui/`（前端静态资源）
- `portable_env.py`（打包环境路径）

**将 `ook/` 纳入统一管理**：

`ook/` 的信号转发/拉取逻辑迁移到 `src/hdzz/core/signal/`，消除孤悬目录。

### 3.4 配置统一管理建议

建立明确的三层配置分离原则：

```
config/（或保留 app/ 下）
├── config.json           ← 用户配置（账号、token、代理）—— 人工编辑
├── config.example.json   ← 配置模板 —— 提交到 Git

runtime/（对应现在的 app/.run/）
├── pids.json             ← 进程状态 —— 运行时产生，不提交
└── logs/                 ← 日志 —— 运行时产生，不提交

data/（合并现在的 app/data/ 和根 data/）
├── follow_settings.json  ← 跟单策略配置
├── arsenal.json          ← 会员账号池
├── orders/               ← 订单 JSONL（按账号）
├── results/              ← 跟单结果 JSONL
└── settlements/          ← 结算数据
```

### 3.5 公共层/工具层抽取建议

以下逻辑目前散落在多处，适合抽取为公共工具：

- **HTTP 客户端封装**：`common/http.py` 已有基础，但各模块仍有重复的重试/超时逻辑
- **日志初始化**：每个模块都有独立的 logger 初始化，可统一为 `common/logger.py` 中的一个工厂函数
- **进程 Bootstrap 逻辑**：7 个转发入口的 `sys.path.insert` + `main()` 模式可统一为一个 `common/entrypoint.py`

### 3.6 客户发布版/打包版准备

当前打包体系已有基础（PyInstaller `.spec` 文件、`Build_*.command` 脚本），但需要规范化：

- 建立 `build/` 目录统一存放所有构建脚本和 spec 文件，根目录不应有 `.command`/`.bat` 文件
- `_dist/` 加入 `.gitignore`，改由 CI 或手动脚本生成并单独分发
- 为嵌入式 Python 运行时（`python/`）和 Playwright 浏览器（`ms-playwright/`）建立独立的下载/安装脚本，不直接入库

---

## 四、推荐的目标结构

```
hdproc/
│
├── src/hdzz/                        # 全部核心业务逻辑（Python 包）
│   ├── api/
│   │   ├── webui/
│   │   │   ├── server.py            # 启动 + 路由注册（< 200 行）
│   │   │   ├── routes/              # 按功能拆分的路由模块
│   │   │   └── ws.py                # WebSocket 管理
│   │   └── telegram/
│   │       ├── bot.py
│   │       ├── premium_bot.py
│   │       └── templates.py         # 消息模板（从 bot.py 拆出）
│   ├── core/
│   │   ├── watcher/                 # 注单监听
│   │   ├── follower/                # 跟单引擎（细化拆分，见 3.2）
│   │   ├── member/                  # 会员续期
│   │   ├── signal/                  # 信号转发/拉取（从 ook/ 迁入）
│   │   └── settlement/              # 结算导入
│   └── common/                      # 通用工具（config、http、logger、parse）
│
├── app/                             # 应用层（薄层）
│   ├── app.py                       # 多进程管理器
│   ├── portable_env.py              # 打包环境路径（仅打包场景用）
│   ├── config.json                  # 用户主配置（不提交）
│   ├── config.example.json          # 配置模板（提交到 Git）
│   └── webui/                       # 前端静态资源
│       ├── index.html
│       ├── app.js
│       └── chashui/
│
├── data/                            # 运行时数据（统一，不提交）
│   ├── arsenal.json                 # 会员账号池
│   ├── follow_settings.json         # 跟单策略
│   ├── orders/                      # 订单 JSONL
│   ├── results/                     # 跟单结果 JSONL
│   └── settlements/                 # 结算数据
│
├── runtime/                         # 进程运行时（不提交）
│   ├── pids.json
│   └── logs/
│
├── tests/                           # 测试（按模块分组）
│   ├── conftest.py
│   ├── core/
│   │   ├── test_follower_*.py
│   │   ├── test_watcher_*.py
│   │   └── test_member_*.py
│   ├── api/
│   │   ├── test_webui_*.py
│   │   └── test_telegram_*.py
│   └── build/
│       └── test_mac_bundle_*.py
│
├── build/                           # 构建脚本和打包配置（提交到 Git）
│   ├── mac_app.command
│   ├── mac_standalone.command
│   ├── windows_portable.ps1
│   ├── hdzz_mac_standalone.spec
│   └── tools/                       # 打包辅助工具（从 app/tools/ 迁入）
│
├── cc/                              # Node.js 抓包工具（独立子系统，保持现状）
│   ├── src/
│   ├── scripts/
│   └── package.json
│
├── docs/                            # 文档
│
├── scripts/                         # 运维启动脚本（面向最终用户）
│   ├── start.command
│   └── stop.command
│
└── .gitignore                       # 需补充 _dist/、data/、runtime/
```

**各目录性质说明：**

| 目录 | 性质 | 是否提交 Git |
|------|------|-------------|
| `src/hdzz/` | 源代码（核心业务） | ✅ 是 |
| `app/` | 源代码（应用入口 + 前端） | ✅ 是（除 config.json） |
| `data/` | 运行时数据 | ❌ 否 |
| `runtime/` | 进程状态 + 日志 | ❌ 否 |
| `tests/` | 测试代码 | ✅ 是 |
| `build/` | 构建脚本 | ✅ 是 |
| `cc/` | Node.js 子系统 | ✅ 是（除 node_modules） |
| `python/` | 嵌入式运行时 | ❌ 否（独立分发） |
| `ms-playwright/` | Playwright 浏览器 | ❌ 否（独立下载） |
| `_dist/` | 打包产物 | ❌ 否 |

---

## 五、分阶段重构计划

### Phase 1：低风险高收益优化

> 目标：减熵、修复明显问题，零业务改动，可在几天内完成

1. **删除 6 个 `_legacy.py` 文件**（确认业务正常后直接删，无依赖）
2. **补全 `.gitignore`**：添加 `_dist/`、`data/`、`runtime/`、`python/`、`ms-playwright/`
3. **清理 `webui/` 目录下的 `.backup` / `.bak` 文件**（由 Git 管理版本，不需要手动备份）
4. **排查 `webui_server.err.log` 的 429MB 错误来源**，修复高频静默错误
5. **添加日志轮换**：在 `app/app.py` 进程启动时为所有子进程日志加上 `RotatingFileHandler`（10MB/3个备份）
6. **统一 data 目录**：将根目录的 `/data/` 和 `/app/data/` 合并为一个 `data/`，更新 `.gitignore` 和相关路径常量

---

### Phase 2：结构整理与模块拆分

> 目标：消除"两层皮"、拆解上帝文件、规范测试，约 2-4 周

1. **消除转发层**：
   - 把 `app/` 下的真实业务文件（`member_client.py` 等）迁移进 `src/hdzz/`
   - 7 个转发入口统一改为通过 `app.py` 直接传参调用，不再需要独立的 `app/webui_server.py` 等

2. **拆解 `engine.py`**（最重要）：
   - 按第 3.2 节建议，将 `message.py`、`cache.py`、`parser.py` 从 7,663 行文件中切割出来
   - 每次切割后运行全量 `pytest`，确保测试通过

3. **拆解 `server.py`**：
   - 新建 `api/webui/routes/` 目录，按功能切割路由

4. **将 `ook/` 迁移到 `src/hdzz/core/signal/`**：
   - 消除孤悬目录，统一纳入包管理

5. **规范测试结构**：
   - 添加 `tests/conftest.py`，提取公共 fixtures
   - 创建 `tests/core/`、`tests/api/`、`tests/build/` 子目录，将现有测试分组归入

6. **统一配置目录**：
   - 按第 3.4 节建议，明确区分用户配置（`config.json`）和运行时状态（`data/`、`runtime/`）

---

### Phase 3：发布、打包、维护性完善

> 目标：为客户版本和持续交付做好准备，约 1-2 周

1. **建立 `build/` 目录**：
   - 将根目录的 `.command`/`.bat` 脚本和 `app/tools/` 下的打包工具统一迁入
   - 根目录只保留 `README.md`、`CLAUDE.md`、`.gitignore` 等项目文件

2. **前端现代化（可选，较大改动）**：
   - 引入 Vite 构建工具，将 `index.html` 拆分为 Vue 3 SFC 组件
   - 将 CDN 依赖改为本地 npm 包，确保离线可用
   - 此项改动较大，建议作为独立子项目进行

3. **嵌入式运行时管理**：
   - 为 `python/` 和 `ms-playwright/` 编写独立的安装脚本（`build/install_runtime.sh`）
   - 发布时通过脚本下载而非打包入仓库

4. **添加 `pytest.ini` 或 `pyproject.toml`**：
   - 配置 `testpaths`、`python_files`、`python_classes`
   - 加入 `pytest-cov`，建立覆盖率基线

---

## 六、风险与注意事项

### 绝对不能乱动的地方

| 区域 | 原因 |
|------|------|
| `app/app.py` 的进程管理逻辑 | 核心调度，任何改动都可能导致子进程无法正常启停 |
| `app/config.json` 的字段结构 | 现有运行实例依赖此结构，加字段可以，改/删字段需小心 |
| `app/data/follow_settings.json` 的格式 | 跟单引擎读取此配置，格式变化直接影响下注逻辑 |
| `app/orders/` 的 JSONL 格式 | 历史数据存储格式，改变会导致历史订单无法读取 |
| `src/hdzz/core/follower/engine.py` 的盘口匹配逻辑 | 任何细节改动都可能导致跟单金额或方向错误，属于高风险业务逻辑 |
| PyInstaller `.spec` 文件 | 打包配置，改错会导致客户版本无法启动 |

### 容易影响业务的改动

- **路径常量变更**：`orders/`、`data/`、`.run/` 的路径如果调整，必须同步更新所有引用，包括 `app.py`、各个 worker 的命令行参数、WebUI 的文件读取路径
- **进程名称变更**：`pids.json` 中的 key（如 `"follow_engine"`）如果重命名，会导致旧的进程状态无法识别，需要同步更新检测逻辑
- **API 路由变更**：前端 `app.js` 和 `queue.js` 中硬编码了 `/api/orders`、`/api/follow` 等路径，后端路由重构需同步更新前端

### 重构时避免改坏的原则

1. **每次只做一件事**：文件移动、逻辑拆分、接口调整不要同时进行，每一步完成后先运行测试
2. **先写测试再重构**：对于即将拆分的大文件（尤其是 `engine.py`），先补充集成测试覆盖主要路径，再开始切割
3. **保持接口不变**：拆分内部实现时，保持对外的函数签名和返回值不变，再逐步迭代
4. **遗留代码确认无用后再删**：`_legacy.py` 系列，建议先 grep 确认没有任何地方 import 它们，再统一删除
5. **运行时数据迁移要有过渡期**：如果要合并 `data/` 目录路径，应先让代码同时支持新旧路径，再切换，避免运行中的实例因找不到路径而崩溃

---

> 以上为完整重构优化建议。请确认方向和优先级后，再开始具体执行。
