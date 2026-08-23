# Remote Signal Node Port Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将 `hdpro` 现有“远端交易节点跟单”能力按最小完整链路移植到 `hdproc`，让 `hdproc` 支持接入远端信号源并把信号写入本地 `orders/*.jsonl` 参与跟单。

**Architecture:** 复用 `hdpro` 已验证过的配置模型、`ook/signal_puller.py` 进程编排、WebUI 配置接口与前端 helper 文件。保留 `hdproc` 当前页面结构，只做增量嵌入，不覆盖整页。`signal_share` 对外分享页面不纳入本次移植范围。

**Tech Stack:** Python `unittest`、Node `assert` 纯函数测试、现有 `SimpleHTTPRequestHandler` WebUI、Vue CDN 单页。

---

### Task 1: 补齐失败测试

**Files:**
- Create: `tests/test_signal_pull_config_merge.py`
- Create: `tests/test_signal_puller_live_only.py`
- Create: `tests/test_add_node_form.js`
- Create: `tests/test_sidebar_signal_list.js`
- Modify: `tests/test_port_defaults.py`

**Step 1: 写 Python 失败测试**

- 校验 `signal_pull_*` 默认键存在、合并逻辑正确
- 校验 `signal_puller` 默认只拉滚球，允许显式关闭

**Step 2: 写 Node 失败测试**

- 校验 `add_node_form.js` 能生成 signal 模式请求
- 校验 `sidebar_items.js` 能生成远端节点卡片

**Step 3: 运行失败测试**

Run:

```bash
python3 -m unittest tests/test_signal_pull_config_merge.py tests/test_signal_puller_live_only.py
node tests/test_add_node_form.js
node tests/test_sidebar_signal_list.js
```

Expected: 因为 `hdproc` 还没有这些模块或行为而失败。

### Task 2: 移植后端配置与进程

**Files:**
- Create: `src/hdzz/api/webui/signal_pull_config.py`
- Copy/verify: `ook/signal_puller.py`
- Modify: `src/hdzz/api/webui/server.py`
- Modify: `app/app.py`
- Modify: `app/config.example.json`

**Step 1: 写最小实现**

- 引入 `ensure_signal_pull_config_defaults` / `merge_signal_pull_config`
- 在 `server.py` 中增加 `GET/POST /api/signal-pull-config`
- 在 `app.py workers()` 中拉起 `ook_signal_puller`
- 在 `config.example.json` 中补齐 `signal_pull_*` 默认项

**Step 2: 运行测试转绿**

Run:

```bash
python3 -m unittest tests/test_signal_pull_config_merge.py tests/test_signal_puller_live_only.py
```

Expected: PASS

### Task 3: 移植前端 helper 与 UI 接线

**Files:**
- Create: `app/webui/add_node_form.js`
- Create: `app/webui/sidebar_items.js`
- Modify: `app/webui/index.html`

**Step 1: 写最小实现**

- 接入 helper 脚本
- 扩展 `showAddModal` 弹窗为 `account/signal` 双模式
- 增加 `signalPullConfig`、`addNodeForm`、`sidebarItems` 相关状态/计算属性/方法
- 将提交动作改为 `submitAddNode`

**Step 2: 运行前端纯函数测试**

Run:

```bash
node tests/test_add_node_form.js
node tests/test_sidebar_signal_list.js
```

Expected: PASS

### Task 4: 补齐 `app.py` 进程编排回归

**Files:**
- Modify: `tests/test_port_defaults.py`
- Or create: `tests/test_remote_signal_worker_bootstrap.py`

**Step 1: 添加失败测试**

- 验证 `workers()` 包含 `ook_signal_puller`

**Step 2: 运行确认失败**

**Step 3: 最小修正并转绿**

Run:

```bash
python3 -m unittest tests/test_remote_signal_worker_bootstrap.py
```

### Task 5: 端到端验证

**Files:**
- No code

**Step 1: 运行整组回归**

Run:

```bash
python3 -m unittest \
  tests/test_signal_pull_config_merge.py \
  tests/test_signal_puller_live_only.py \
  tests/test_remote_signal_worker_bootstrap.py \
  tests/test_port_defaults.py
node tests/test_add_node_form.js
node tests/test_sidebar_signal_list.js
```

**Step 2: 静态核对关键文件**

- `app/webui/index.html`
- `src/hdzz/api/webui/server.py`
- `app/app.py`
- `app/config.example.json`

**Step 3: 总结残留风险**

- 说明未移植 `signal_share.html`
- 说明远端节点目前仍为全局单实例配置
