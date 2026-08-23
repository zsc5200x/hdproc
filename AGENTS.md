# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## 项目概述

全栈自动化应用，用于体育赛事注单监听和自动跟单交易。Python 后端 + Vanilla JS 前端 + Node.js 抓包工具。

## 常用命令

### 启动 / 停止服务（Python 多进程管理器）

```bash
cd app
python app.py start    # 启动所有组件
python app.py status   # 查看运行状态
python app.py stop     # 停止所有服务
```

### 单独启动各组件

```bash
python -u ag_wmc_watch.py --config config.json --interval 3 --only-orders
python webui_server.py --host 127.0.0.1 --port 9999 --orders-dir orders
python -u member_arsenal_watch.py --data-dir data --interval 20
python -u follow_engine.py --orders-dir orders --data-dir data
python -u telegram_bot.py --config config.json
```

### 测试

```bash
pytest tests/                        # 运行全部测试
pytest tests/test_follow_engine.py   # 运行单个测试文件
pytest tests/ -k "test_name"         # 运行匹配名称的测试
```

### Node.js 抓包工具（cc/ 目录）

```bash
cd cc
npm install
npm run install:browsers   # 安装 Chromium
npm run wui                # 启动 WUI 服务（端口 3000）
npm run capture:login      # 抓取登录网络请求
npm run capture:odds       # 抓取盘口/水位接口
npm run verify:parsers     # 验证解析器
```

### macOS 打包

```bash
./Build_Mac_App.command                # 构建 .app（依赖外部浏览器）
./Build_Mac_Standalone_App.command     # 构建独立 .app（内置运行时）
./Build_Mac_Portable_ZIP.command       # 构建可移植 ZIP
```

## 代码架构

### 整体数据流

```
config.json
    ↓
app.py（多进程管理器）
    ├─ ag_wmc_watch → src/hdzz/core/watcher/ag_watcher.py
    │    轮询后台接口 → 写 orders/<target>.jsonl → Telegram 推送
    ├─ webui_server → src/hdzz/api/webui/server.py
    │    读 orders/ 和 data/ → HTTP REST + WebSocket 实时推送
    ├─ member_arsenal_watch → Playwright 自动登录
    │    刷新 Cookie/uid → 存入 data/arsenal.json
    ├─ follow_engine → src/hdzz/core/follower/engine.py
    │    读 orders/ + follow_settings.json → 调 FT_bet 接口 → follow_results.jsonl
    └─ telegram_bot → src/hdzz/api/telegram/bot.py
```

### 目录职责

| 目录 | 职责 |
|------|------|
| `app/` | 应用入口、配置、WebUI 静态资源、遗留实现 |
| `src/hdzz/` | 全部核心业务逻辑（Python 包） |
| `src/hdzz/api/webui/` | Flask/aiohttp WebUI 服务端（`server.py` 5000+ 行） |
| `src/hdzz/api/telegram/` | Telegram 机器人 + 付费权限 + 支付 |
| `src/hdzz/core/watcher/` | AG 后台注单监听 |
| `src/hdzz/core/follower/` | 跟单引擎（策略、执行器、盘口解析、重试调度） |
| `src/hdzz/core/member/` | 会员续期（Playwright 自动登录） |
| `src/hdzz/core/settlement/` | 结算数据导入 |
| `src/hdzz/common/` | 配置解析、HTTP、日志、数据解析等通用工具 |
| `cc/` | Node.js 网络抓包工具（HGA 平台接口逆向） |
| `ook/` | 信号中继模块（转发器 + 拉取器） |
| `tests/` | pytest 测试套件（47 个文件） |
| `data/` | 运行时数据（arsenal.json、follow_settings.json、结算等） |
| `app/orders/` | 订单存储（JSONL 格式，按账号分文件） |
| `python/` | 嵌入式 Python 3.11 运行时（用于单文件分发） |

### 关键文件

- `app/app.py` — 统一进程管理入口，负责启停所有子进程
- `src/hdzz/core/follower/engine.py` — 跟单核心逻辑，含盘口策略、重试、中断处理
- `src/hdzz/core/watcher/ag_watcher.py` — AG 注单轮询监听
- `src/hdzz/api/webui/server.py` — WebUI REST API + WebSocket
- `app/webui/index.html` + `app/webui/app.js` — 前端仪表板
- `app/config.json` — 运行时配置（目标账号、代理等）
- `app/data/follow_settings.json` — 跟单策略配置

### 跟单引擎模块关系（`src/hdzz/core/follower/`）

```
engine.py        ← 主循环，协调以下模块
  ├─ strategy.py         让球/大小/独赢等盘口策略
  ├─ executor.py         实际下注 HTTP 调用
  ├─ quote_manager.py    盘口报价缓存与匹配
  ├─ game_ctx_cache.py   比赛上下文缓存
  ├─ game_more_resolver.py  复合盘口（更多玩法）解析
  ├─ retry_scheduler.py  失败重试调度
  └─ models.py           数据模型（Order、BetResult 等）
```

## 运行时数据说明

- `app/data/arsenal.json` — 会员账号池（含登录状态、Cookie）
- `app/data/follow_results.jsonl` — 跟单结果历史记录
- `app/data/follow_trace.jsonl` — 跟单执行轨迹（调试用）
- `app/orders/<账号>.jsonl` — 各账号原始注单流水
- `app/.run/logs/` — 各子进程 stdout/stderr 日志

## 依赖

- Python: `requests[socks]>=2.31.0`、`urllib3<2`、`playwright>=1.49.0`
- Node.js: `express`、`playwright`
- 测试: `pytest`
