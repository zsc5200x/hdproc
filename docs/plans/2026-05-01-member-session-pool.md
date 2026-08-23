# Member Session Pool Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 为 `follow_engine` 引入“会员级短生命周期 Session 池”，复用同会员对同一站点的 HTTP 会话与连接池，同时在 `1X014`、`doubleLogin`、严重代理异常、会员登录态变更时及时丢弃并重建 Session。

**Architecture:** 新增一个仅存在于 `follow_engine` 进程内的 `session_pool` 模块，作为 `requests.Session` 的运行时缓存层。`arsenal.json` 继续作为会员登录态的持久化事实来源，保存 `cookie / uid / ver / status / last_error / proxy / base_url`；Session 池只缓存不可持久化的内存对象和少量指纹元数据，不写入磁盘，不跨进程共享。

**Tech Stack:** Python 3.9、`requests.Session`、现有 `unittest`、`JsonFileCache`、`follow_engine` / `member keeper` 现有状态模型。

---

> 说明：
> - 按当前仓库约束，本计划不包含 `git commit` / 分支操作。
> - 第一版严格控制范围：不做并发化，不改 WebUI，不把 Session 对象落盘，不让 `keeper.py` 直接管理 Session 池。

### Task 1: 锁定 Session 池行为测试

**Files:**
- Create: `tests/test_follow_session_pool.py`
- Create: `tests/test_follow_engine_session_pool.py`

**Step 1: 写 `session_pool` 纯单元失败测试**

- 校验同一 `member_id + cookie/proxy/base_url` 指纹命中缓存时返回同一个 Session 实例。
- 校验 `cookie`、`proxy`、`base_url` 任一变化时，会丢弃旧 Session 并创建新 Session。
- 校验 `invalidate(member_id)` 会关闭并删除缓存对象。
- 校验 `prune_idle(max_idle_s)` 会清理长期未使用的 Session。

**Step 2: 写 `engine` 集成失败测试**

- 校验同一会员连续两次 attempt 会复用池里的 Session，而不是重复调用 `make_member_session()`。
- 校验 `1X014` 返回后会触发 Session 失效。
- 校验 `doubleLogin` 返回后会触发 Session 失效。
- 校验严重代理异常连续命中阈值后会触发 Session 失效。

**Step 3: 运行测试确认失败**

Run:

```bash
python3 -m unittest tests.test_follow_session_pool tests.test_follow_engine_session_pool
```

Expected:
- 因 `session_pool.py` 尚不存在、`engine.py` 尚未接入池逻辑而失败。

**Step 4: 记录失败点**

- 记下缺失模块、缺失接口、当前 `make_member_session()` 调用次数等失败信息，作为后续实现对照。

### Task 2: 新增进程内 Session 池模块

**Files:**
- Create: `src/hdzz/core/follower/session_pool.py`

**Step 1: 定义最小数据结构**

- 定义 `SessionEntry`，至少包含：
  - `session`
  - `member_id`
  - `fingerprint`
  - `created_at`
  - `last_used_at`
  - `invalid_reason`

**Step 2: 实现最小接口**

- `build_member_session_fingerprint(member: dict) -> str`
- `get_or_create(member: dict) -> requests.Session`
- `invalidate(member_id: str, reason: str) -> None`
- `prune_idle(max_idle_s: float) -> int`
- `close_all() -> None`

**Step 3: 明确失效/重建规则**

- 若 `member_id` 不存在缓存：创建。
- 若存在缓存但当前指纹变化：关闭旧 Session，重建。
- 若显式 `invalidate`：关闭并删除。
- 若超过空闲 TTL：关闭并删除。

**Step 4: 正确关闭连接池**

- 关闭 Session 时，调用 `session.close()`，确保 urllib3 连接池被释放。

**Step 5: 运行单元测试转绿**

Run:

```bash
python3 -m unittest tests.test_follow_session_pool
```

Expected:
- `tests.test_follow_session_pool` PASS。

### Task 3: 让 `executor` 成为池友好的低层会话工厂

**Files:**
- Modify: `src/hdzz/core/follower/executor.py`

**Step 1: 保留 `make_member_session()` 作为底层工厂**

- 不让 `executor.py` 持有池。
- 继续负责：
  - 创建 `requests.Session`
  - 填充 headers
  - 注入 proxy
  - 注入 cookie

**Step 2: 增补低层辅助函数**

- 增加 `close_member_session(session)` 或等价 helper，统一关闭逻辑。
- 如有必要，增加注释，明确 `make_member_session()` 的职责是“创建”，不是“缓存/复用”。

**Step 3: 保证异常重试逻辑不被破坏**

- `member_post_session()` 当前已处理轻量网络重试和坏连接清理，保持这层逻辑不变。
- Session 池只控制“是否复用这个 Session”，不改 `member_post_session()` 的返回契约。

**Step 4: 回归 Session 池测试**

Run:

```bash
python3 -m unittest tests.test_follow_session_pool
```

Expected:
- 仍为 PASS。

### Task 4: 在 `follow_engine` 主链路中接入 Session 池

**Files:**
- Modify: `src/hdzz/core/follower/engine.py`

**Step 1: 在引擎启动处创建池实例**

- 在 `main()` 初始化阶段创建 `MemberSessionPool`。
- 生命周期与 `follow_engine` 进程一致。

**Step 2: 替换热路径里的直接建 Session 调用**

优先替换这些位置：
- `place_bet()` 主循环中的 `make_member_session()` 调用
- quote member 查水路径中的 `make_member_session()` 调用
- 封盘/比分探测等仍面向同会员会话的 `make_member_session()` 调用

重点检查当前调用点：
- `src/hdzz/core/follower/engine.py:548`
- `src/hdzz/core/follower/engine.py:4838`
- `src/hdzz/core/follower/engine.py:7662`

**Step 3: 明确池使用策略**

- 同一会员在一次订单执行和后续重试中，优先复用同一 Session。
- 不同会员绝不共享 Session。
- `allow_quote_members` 不改变池规则，只影响“是否允许该会员进入真实下注选择”。

**Step 4: 运行集成测试**

Run:

```bash
python3 -m unittest tests.test_follow_engine_session_pool
```

Expected:
- 连续 attempt 不再重复创建 Session。

### Task 5: 把失效信号和 `arsenal.json` 协作规则接好

**Files:**
- Modify: `src/hdzz/core/follower/engine.py`
- Verify only: `src/hdzz/core/member/keeper.py`

**Step 1: 统一“会话失效”触发点**

在 `engine.py` 里，把以下情况统一接到 `session_pool.invalidate(member_id, reason)`：

- `1X014`
- `doubleLogin`
- 严重代理异常 / 连接异常
- 业务代码主动将会员标记为 `offline`

**Step 2: 保持 `arsenal.json` 的职责单一**

- 继续使用 `patch_member(data_dir / "arsenal.json", ...)` 更新：
  - `status`
  - `last_error`
  - `cookie`
  - `uid`
  - `ver`
- 不新增任何用于持久化 `requests.Session` 的字段。

**Step 3: 用“指纹变化”连接池与持久状态**

- Session 池每次 `get_or_create(member)` 时，根据当前 `member` 计算指纹。
- 指纹建议至少包含：
  - `member_id`
  - `base_url`
  - `proxy`
  - `cookie`
  - `uid`
  - `ver`
- 若 `keeper.py` 已刷新这些字段，池在下次使用时自动识别为“当前登录态变了”，丢弃旧 Session。

**Step 4: 第一版不改 `keeper.py` 逻辑**

- `keeper.py` 继续只做登录恢复和 `arsenal.json` 更新。
- 不让 `keeper.py` 直接触碰池，避免跨进程/跨职责耦合。

**Step 5: 运行回归测试**

Run:

```bash
python3 -m unittest \
  tests.test_follow_session_pool \
  tests.test_follow_engine_session_pool \
  tests.test_follow_engine_place_bet_exceptions
```

Expected:
- Session 复用与失效规则正确；
- 现有 `place_bet` 异常行为不回退。

### Task 6: 补运行时观测与清理策略

**Files:**
- Modify: `src/hdzz/core/follower/session_pool.py`
- Modify: `src/hdzz/core/follower/engine.py`

**Step 1: 增加轻量日志**

- `session_hit`
- `session_miss`
- `session_rebuilt`
- `session_invalidated`
- `session_pruned`

日志内容只带：
- `member_id`
- `reason`
- `age_s`
- `idle_s`

不要打印完整 cookie。

**Step 2: 增加周期性清理**

- 在 `main()` 循环内低频调用 `prune_idle()`。
- 默认只清理长时间未使用的 Session，避免池无限增长。

**Step 3: 关闭时释放全部 Session**

- 在 `follow_engine` 进程退出前调用 `close_all()`。

**Step 4: 运行最终回归**

Run:

```bash
python3 -m unittest \
  tests.test_follow_session_pool \
  tests.test_follow_engine_session_pool \
  tests.test_follow_retry_policy \
  tests.test_follow_engine_member_pick \
  tests.test_follow_market_rules \
  tests.test_follow_queue_helpers
```

Expected:
- Session 池相关测试 PASS；
- 原有跟单策略/队列逻辑测试不回退。

### Task 7: 手工验证与风险检查

**Files:**
- Verify only: `app/data/arsenal.json`
- Verify only: `app/data/follow_results.jsonl`
- Verify only: `app/data/follow_trace.jsonl`

**Step 1: 手工验证复用**

- 让同一会员连续处理两笔订单，确认日志里出现 `session_hit`。

**Step 2: 手工验证失效**

- 人工制造 `1X014` 或 `doubleLogin`，确认：
  - `arsenal.json` 中会员被标记 `offline`
  - 池中的旧 Session 被清理
  - 后续使用时重建

**Step 3: 手工验证 `keeper` 配合**

- 让 `keeper.py` 刷新该会员 `cookie/uid/ver`。
- 下一次 `follow_engine` 使用该会员时，应检测到指纹变化并重建 Session。

**Step 4: 总结残留风险**

- 当前池仅在单个 `follow_engine` 进程内有效，不跨进程共享。
- 当前仍是单线程串行队列，Session 池只优化建连/会话复用，不改变吞吐上限模型。
- 若后续要做并发执行，需重新评估 `hard_timeout` 的主线程/SIGALRM 约束。

## Out of Scope

- 不做 WebUI Session 诊断页面
- 不做多进程共享 Session
- 不做 `requests.Session` 持久化
- 不做 `keeper.py` 主动驱动池同步
- 不做并发化改造
