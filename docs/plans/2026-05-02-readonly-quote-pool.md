# Read-only Quote Pool Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 为 `查水 / 盘口信息 / 比赛事件` 建立独立的只读号池（Quote Pool），默认不再占用跟单会员会话；系统在合格只读会员中随机选一个，并在单次查水/详情会话内短时粘住该会员，减少对真实下注会员的干扰。

**Architecture:** 以现有 `role=quote` 概念为基础，新增一个专门面向 `chashui` 只读链路的成员选择模块。后端 `/api/chashui/status`、`/api/chashui/games`、`/api/chashui/odds`、`/api/chashui/events` 优先使用只读号池成员；前端在一次比赛搜索/详情会话里保存该成员 `member_id`，在该会话生命周期内复用，避免每次刷新随机换号导致数据不一致。只有在只读池为空或显式允许 fallback 时，才考虑回退到下注会员。

**Tech Stack:** Python 3.9、现有 `unittest`、Node `node:test`、现有 `SimpleHTTPRequestHandler` WebUI、`requests.Session`、现有 `quote_manager.py` 和 `arsenal.json`。

---

> 说明：
> - 该计划仅针对“只读链路”改造，不改变真实跟单下注逻辑。
> - 当前工作区不是可用 git 仓库，因此本计划不包含 commit 步骤。
> - 第一版默认 **禁用** `quote -> bet member` 的自动回退，避免“读接口把下注号弄脏”。

### Task 1: 锁定只读号池的预期行为

**Files:**
- Create: `tests/test_chashui_quote_pool.py`
- Create: `tests/test_chashui_status_selection.py`
- Create: `tests/test_match_search_member_sticky.js`

**Step 1: 写 Python 失败测试**

- 覆盖只读号池成员筛选规则：
  - 仅 `role=quote` 且 `online`
  - 必须有 `base_url + uid + cookie + ver`
  - 若显式指定 `member_id`，且该会员仍合格，则优先使用
  - 否则从合格池中随机选择
- 覆盖 `/api/chashui/status`：
  - 有 quote 成员时，不再回落到普通下注会员
  - 无 quote 成员时，默认返回“无可用查水会员”而不是偷用下注会员

**Step 2: 写前端失败测试**

- 校验比赛搜索/比赛详情模型在一次会话内会携带 `memberId`
- 校验相同比赛的盘口/事件请求会复用该 `memberId`
- 校验切换到新会话时才允许重新分配只读会员

**Step 3: 运行测试确认失败**

Run:

```bash
python3 -m unittest tests.test_chashui_quote_pool tests.test_chashui_status_selection
node tests/test_match_search_member_sticky.js
```

Expected:
- 因当前实现仍是“优先固定 quote / 第一个在线会员”，而不是“只读池随机 + 会话粘住”而失败。

### Task 2: 抽出只读号池选择模块

**Files:**
- Create: `src/hdzz/api/webui/chashui_quote_pool.py`

**Step 1: 定义模块职责**

- `is_readonly_quote_candidate(member)`
- `list_readonly_quote_candidates(members)`
- `pick_readonly_quote_member(candidates, preferred_member_id="")`
- `load_readonly_quote_pool(arsenal_path)`
- `select_readonly_quote_member(arsenal_path, preferred_member_id="", allow_fallback_to_bet=False)`

**Step 2: 资格判定规则**

- 成员必须满足：
  - `role == "quote"`
  - `status == "online"`
  - `base_url / uid / ver / cookie` 完整
- 第一版不考虑 `relogin` / `offline` 待恢复成员
- 第一版不默认使用普通下注会员补位

**Step 3: 随机策略**

- 若有 `preferred_member_id` 且仍然合格：直接返回该成员
- 否则：从候选池 `random.choice(...)`
- 不做“第一个 quote”或“第一个 online”的顺序偏置

**Step 4: 运行单元测试转绿**

Run:

```bash
python3 -m unittest tests.test_chashui_quote_pool
```

Expected:
- 只读号池筛选和随机逻辑 PASS。

### Task 3: 接入 `/api/chashui/status`

**Files:**
- Modify: `src/hdzz/api/webui/server.py`

**Step 1: 替换当前固定选择逻辑**

当前逻辑位置：
- `src/hdzz/api/webui/server.py:4170`

替换为：
- 若请求显式传 `member_id`：验证该会员是否仍是合格只读会员
- 否则：调用 `select_readonly_quote_member(...)`
- 若没有可用只读会员：返回更明确的只读池错误信息

**Step 2: 丰富返回信息**

响应里增加：
- `pool_type: "quote_readonly"`
- `sticky: false`
- `fallback_used: false`
- `eligible_count`

**Step 3: 不改变 `member_id` 的显式覆盖行为**

- 调试/定位问题时，用户仍可手动指定 `member_id`
- 但若指定会员不合格，应直接报错，不偷偷换号

**Step 4: 运行测试**

Run:

```bash
python3 -m unittest tests.test_chashui_status_selection
```

Expected:
- `/api/chashui/status` 行为符合“优先只读号池 + 随机选”的规则。

### Task 4: 接入 `games / odds / events / token capture`

**Files:**
- Modify: `src/hdzz/api/webui/server.py`

**Step 1: `games` 与 `odds`**

相关位置：
- `src/hdzz/api/webui/server.py:4219`
- `src/hdzz/api/webui/server.py:4295`

要求：
- 继续按前端传入的 `member_id` 使用对应会话
- 不在这里偷偷换号
- `member_id` 不存在或会话不完整时直接报错

**Step 2: `events`**

相关位置：
- `src/hdzz/api/webui/server.py:4369`

要求：
- 若前端传了 `member_id`：优先使用该只读会员来解析 `gid -> match_id`
- 若事件 token 缺失，需要自动采集：优先仍使用该只读会员
- 如果该只读会员失效，再从只读池重新随机选一个并重试一次

**Step 3: `_chashui_find_capture_member`**

相关位置：
- `src/hdzz/api/webui/server.py:2922`

改成：
- 优先返回 `preferred_member_id`
- 否则从只读号池里随机选
- 第一版不回退到普通下注会员

**Step 4: 错误语义清晰化**

返回信息要区分：
- `missing_readonly_member`
- `readonly_member_not_found`
- `readonly_member_session_incomplete`
- `readonly_pool_empty`

### Task 5: 前端做“随机一次后短时粘住”

**Files:**
- Modify: `app/webui/app.js`
- Verify only: `app/webui/match_search.js`

**Step 1: 改 `ensureMatchSearchMember()`**

当前位置：
- `app/webui/app.js:1181`

改造目标：
- 仍从 `/api/chashui/status` 取 `member`
- 但不要把这个选择当成全局永久值
- 应该把它当成一次查水会话的 sticky member

**Step 2: 会话粘住策略**

建议第一版：
- 搜索比赛时选中一个 `memberId`
- 同一轮搜索结果、同一详情弹窗、同一比赛的盘口/事件刷新都复用它
- 当用户清空搜索、刷新搜索、或显式重置查水会话时，才重新分配

**Step 3: 比赛详情弹窗粘住**

- `盘口信息`
- `比赛事件`
- `投注信息`

其中：
- `盘口信息` / `比赛事件` 继续显式带 `member_id`
- `投注信息` 继续走前端已有数据，不强制引入实时会话依赖

**Step 4: 测试转绿**

Run:

```bash
node tests/test_match_search_member_sticky.js
```

Expected:
- 相同会话内复用同一 `memberId`
- 新会话才会重新分配

### Task 6: 补管理与观测能力

**Files:**
- Modify: `src/hdzz/api/webui/server.py`
- Modify: `app/webui/app.js`
- Optional: `app/webui/index.html`

**Step 1: 状态接口返回只读池信息**

- `eligible_count`
- 当前 sticky `member_id`
- 当前 sticky `member_label`
- 是否来自显式指定
- 是否 fallback（第一版应始终 false）

**Step 2: 前端只做轻量可见性**

- 在 `盘口信息` / `比赛事件` 的详情里显示：
  - `查水会员`
  - `来源：quote_readonly`
- 不要把“随机切换”做成高频 UI 动作，避免扰动理解

**Step 3: 日志**

后端增加轻量日志：
- `chashui_quote_pick`
- `chashui_quote_sticky_reuse`
- `chashui_quote_recover_pick`
- `chashui_quote_pool_empty`

### Task 7: 回归验证

**Files:**
- No code

**Step 1: 运行后端回归**

Run:

```bash
python3 -m unittest \
  tests.test_chashui_quote_pool \
  tests.test_chashui_status_selection \
  tests.test_follow_engine_member_pick \
  tests.test_follow_session_pool \
  tests.test_follow_engine_session_pool
```

**Step 2: 运行前端回归**

Run:

```bash
node tests/test_match_search_member_sticky.js
node tests/test_arsenal_cards.js
node tests/test_pool_dashboard.js
node --check app/webui/app.js
```

**Step 3: 手工验证**

- 保持至少 2 个 `role=quote` 在线
- 打开比赛搜索
- 记录本次会话分配的 `memberId`
- 连续打开同一比赛的 `盘口信息` / `比赛事件`
- 确认请求都使用同一 `member_id`
- 刷新/重开搜索会话后，确认可换到另一个只读会员

## Out of Scope

- 不改真实下注成员池逻辑
- 不做“只读号池也完全无 sticky 的每次随机”
- 不做跨浏览器 tab 的共享 sticky 会话
- 不做 fallback 到下注会员的自动开关 UI
- 不做 quote 号池可视化配置页（第一版可继续用现有 `role=quote` + `quote_member.json`）
