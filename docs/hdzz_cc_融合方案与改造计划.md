# hdzz 跟单引擎 × cc 实时水位脚本：融合方案与改造计划

> 日期：2026-02-07  
> 目标：把 `cc/` 中“实时盘口/水位（含附加盘口）抓取 + `FT_order_view` 校验”的关键能力，以**最小风险**方式沉入 `hdzz` 跟单引擎（`src/hdzz/core/follower/engine.py`），减少因“水位/盘口参数不一致”导致的 `1X015/1X016/betError` 重试与队列卡顿。

---

## 历史实现（已停用）：独立「查水」页面（只读盘口/水位）

> 更新：2026-02-08

- 旧独立页面入口已移除；相关后端接口暂时保留供内部链路和诊断使用
- 数据来源：复用弹药库 `app/data/arsenal.json` 的会话字段（`uid/ver/cookie/proxy/base_url`），不包含下注（不会调用 `p=FT_bet`）
- 后端接口（由 `app/webui_server.py` 提供）：
  - `/api/chashui/status`：列出弹药库会员并回显会话信息（uid 脱敏）
  - `/api/chashui/games?member_id=...&mode=live|today|early`：拉取比赛列表（`get_game_list`）
  - `/api/chashui/odds?member_id=...&gid=...&confirm=1`：拉取盘口/水位（`get_game_more`），可选用 `FT_order_view` 校验
  - `/api/chashui/stream?...`：SSE 实时轮询推送（页面“开始监控”）

## 已落地（WebUI）：查水页升级为「跟单队列」视图（卡片高亮目标盘口）

> 更新：2026-02-08

- 主 WebUI 顶部导航：原「查水」已替换为「跟单队列」视图（仍可新窗口打开）
- 页面：`/chashui/queue.html`
  - 数据：读取 `app/data/follow_state.json` 的 `pending_orders/jobs`
  - 盘口：正式跟单统一使用后台 Snapshot，不再由队列页直连 `/api/chashui/odds`
  - 展示：队列页展示原单与后台执行结果，不再渲染旧实时查水盘口
- 原「单 gid 查水」页已移除，不再作为正式入口
- 新增 API：`/api/follow-queue`（WebUI 队列页轮询用）

## 已落地（WebUI）：策略页新增「查水融合 / 提速」开关

> 更新：2026-02-08

在主 WebUI「策略配置」页新增了与本次融合相关的开关（保存后立即写入 `app/data/follow_settings.json`，下一单生效）：

- `game_more_scan_all_gids`：get_game_more 跨同场所有 gid 扫描并命中目标盘口线（主盘↔附加盘切换时更稳）
- `order_view_confirm_before_bet`：下注前再调用一次 `FT_order_view` 轻量确认最新水位（失败/超时不阻断下注）
- `order_view_confirm_timeout_s`：上述确认请求的超时（建议 1~2 秒）
- `allow_quote_members`：允许 `role=quote` 的会员参与真实下注（谨慎：若该账号同时被查水页面轮询，可能触发 doubleLogin/掉线）

这块的定位是：**把 cc 的“拿最新水位 + 校验思路”以可视化方式接进来，便于你排查跟单慢/失败是否来自水位漂移与盘口映射问题**；真正的“下注前 confirm”融合仍建议按本文后续的方案 A/B 分阶段落地。

## 已落地（引擎）：附加盘找回提速（第二/第三笔分单更快）

> 更新：2026-02-08

针对你反馈的“第二单小4掉到附加盘能找到但偏慢”，在 `src/hdzz/core/follower/engine.py` 做了不改变下注口径的提速改动：

- `lid/ecid` 缓存增加 `showtype` 提示（hint），分单换会员/重试时减少无效的 showtype 扫描
- `get_game_more` 在 `showtype!=live` 时优先尝试 `isRB=N`（更贴近热门/今日），并去重避免 `is_live_order=False` 的重复请求
- 大小盘“严格找回原盘口线”链路按盘口线形态动态排序：
  - 非分盘线（0.5 的整数倍，例如 4/4.5/5）：优先 `get_game_more`（更常命中 A-F 附加盘），失败再回退 `p3type`
  - 分盘线（非 0.5 整数倍，例如 5/5.5→5.25）：仍保持 `p3type` 优先（避免 `get_game_more` 简化盘口线导致漏线）
- `p3type` 扫描遇到 `CheckEMNU/VariableStandard/code_type is null` 这类明显阻断时提前中止，尽快进入 `get_game_more` 兜底

## 已落地（稳定性）：SSLEOF / 非 XML 短串自愈（查水页 + 跟单共用）

> 更新：2026-02-08

针对你反馈的“查水页刷新不出来盘口/水位、跟单偶发拿不到上下文导致 LINE_NOT_FOUND”，落地了两类兜底：

- 会员端请求增加轻量重试：`src/hdzz/core/follower/executor.py` 的 `member_post_session()` 对 `SSLEOF/连接中断/ChunkedEncodingError` 做 3 次短重试，并在重试时强制 `Connection: close` + 关闭连接池，减少复用坏连接导致的连锁失败。
- 避免“错误串缓存”：`src/hdzz/core/follower/engine.py` 的短 TTL XML 缓存只缓存 `<serverresponse>` 形态的 XML，避免把 `code_type is null/loading High/...` 这类短串缓存住造成同一 attempt 内重复失败。

**注意**：上述改动需要重启 `follow_engine/webui_server` 才会生效（建议用 `Stop_AG_Matrix.command` 后再 `Start_AG_Matrix_ALL.command`）。

## 已落地（引擎）：提速（减少重复查水 / 重复刷新）

> 更新：2026-02-08

当 `follow_settings.json` 中 `order_view_confirm_before_bet=true`（默认开启）时：

- 引擎在下注前会做一次 `FT_order_view` 作为 **confirm**（你要的“下注前拿最新水位”）
- 因为 confirm 已经拿到最新 `ioratio` 且会做“严格跟线一致性校验”，因此引擎会**跳过**一次额外的
  `get_game_list/get_game_more` 刷水位（避免“重复查水”把单次 attempt 拉长）

这通常能把成功单的 `attempt_wall_s` 压下来（尤其是 split 后第二/三笔）。如果你想回到“下注前仍强制 get_game_list 刷水位”的旧行为，可以临时把 `order_view_confirm_before_bet` 关掉（但会更慢）。

## 0. 背景与结论（先给你可操作结论）

你描述的现象（“跟单慢、拿不到最新水位去提交、导致失败并拖慢队伍”）在本仓库里对应两类问题：

1) **参数/水位不一致触发快速失败 → 进入重试/找盘链路 → 单次 attempt 耗时上升**  
   - 典型错误码：`1X015` / `1X016` / `betError...`  
   - 根因常见是：下注使用“后台单历史水位(odds)”或用错了“别的盘口线的水位”。

2) **盘口线漂移（主盘挪到附加盘/更多盘口）时，找回原盘口线的扫描链路会变长**  
   - 引擎需要在 `get_game_list` / `get_game_more`、不同 `showtype/p3type`、以及主盘/附加盘 `wtype(A-F)` 之间做映射与刷新。

> 重要发现：当前 `src/hdzz/core/follower/engine.py` **已经具备**（且比 legacy 更完整的）`order_view(FT_order_view) + game_list/game_more(含附加盘)` 刷新能力，并且在下注前还有“提交前刷新到目标盘口线/最新水位”的逻辑；`cc/` 则更像是**诊断/UI/抓包工具**，并额外提供了“用 `FT_order_view` 并发校验主盘水位”的实践。
>
> 因此本次融合的重点不是“从 0 到 1”，而是：**把 cc 里最有效的校验思路（refresh 后再用 order_view 轻量确认）与解析规则沉到 Python 引擎**，并**收敛重复实现**（`fetch_odds.py`、`mos_api.py`、`cc/`、`engine.py` 的重叠部分）。

---

## 1. 现状盘点（基于代码的事实）

### 1.1 跟单引擎（你口中的 hdcc / 本仓库实际为 hdzz）

- 实际跟单入口：`app/follow_engine.py` → `src/hdzz/core/follower/engine.py`
- 后台订单 → 下注参数推导：
  - `infer_bet_params_from_backend_order()` 从后台单推导 `BetParams`（含 `wtype/rtype/chose_team/con/ratio/ioratio`）
  - **注意**：初始 `ioratio` 默认来自 `order.odds`（后台历史水位），后续会被刷新链路覆盖
- 预校验（“order_view”）：
  - `member_order_view()` 调 `ft_order_view()`，本质是 `p=FT_order_view`
  - `extract_order_view_overrides()` 取回 `ioratio/con/ratio`（站点预校验口径）
- 盘口线不一致时的“找盘/刷新”：
  - 大小盘：优先 `get_game_list`（支持 `5/5.5` 分盘线），不行再 `get_game_more`
  - 让球盘：优先 `get_game_more`（含 A-F 附加盘 `wtype`），必要时切换 `showtype/filter` 兜底
  - “更多盘口板块”：通过 `get_page_count` → `get_game_list(p3type=...)` 做映射
- 提交下注前再次刷新（关键逻辑已存在）：
  - 在真正 `FT_bet` 前，按“目标盘口线/当前准备提交盘口线”再执行一次刷新（OU 走 `game_list`，让球走 `game_more`）
- 错误处理：
  - `1X015/1X016`：同一次 place_bet 内允许快速刷新后立刻重试（`fast_retry_1X015_max_attempts`）
  - `betError...`：短延迟快速追水重试（`fast_retry_betError_*`）

**配置现状（`app/data/follow_settings.json`）**

- `split_count = 3`
- `split_delay_seconds = 60.0`（仍会明显拉长“同一订单分单跟全”的总时间窗口）
- `game_more_quick_mode = true`
- `allow_line_change = false` / `allow_total_line_change = false`
- `fast_retry_1X015_max_attempts = 12` / `fast_retry_betError_max_attempts = 8`

### 1.2 cc（实时盘口/水位 UI + 抓包/解析）

cc 目录是一个 Node/Playwright 常驻会话 + Express 服务，核心价值有三块：

1) **常驻浏览器会话**：更贴近网页行为，降低 `CheckEMNU/VariableStandard` 这类“上下文不足”响应
2) **解析策略清晰**：
   - `get_game_list`：直接解析 XML 的 `<original>{JSON}</original>`（最省事且最全）
   - `get_game_more`：按 `<game>...</game>` 拆分 boards，并从 `sw_XXX/ratio_xxx/ior_XXX*` 还原盘口
3) **可选的 `FT_order_view` 水位校验**（你做 cc 的关键点）：
   - 对主盘简化市场（让球 RE、大小 ROU）的每条 line，用 `FT_order_view(gid,wtype,chose_team)` 并发拉一遍最新 `ioratio/spread/strong`
   - 用短 TTL cache（约 1.8s）降低重复请求

> cc 的 “confirmOdds” 思路对 hdzz 引擎的启发：  
> **当我们通过 game_list/game_more 找到目标盘口线后，最终用于提交的 ioratio 应尽量以 order_view（或与下注口径一致的来源）再确认一次**，以减少“game_more 上看到的水位 ≠ 预校验/下注口径水位”导致的 betError/1X015。

---

## 2. 融合要解决的“核心矛盾”

站点下注成功依赖三件事同时一致：

- 盘口线（`con/ratio` 能解码出的 line 值）
- 玩法与盘口编码（`wtype/rtype/chose_team`，包含主盘与 A-F 附加盘）
- 水位（`ioratio`）

跟单慢通常不是“单次请求慢”，而是：

1) **参数不一致 → 被拒单**（`1X015/1X016/betError`）  
2) **被拒单后触发“找盘 + 刷水位”链路**（多次 `get_game_list/get_game_more/order_view`）  
3) **分单 + 重试** 把“盘口变化窗口”拉长，后续更容易继续不一致 → 形成雪球效应

---

## 3. 融合方案选型（给你 3 个可落地路径）

### 方案 A（推荐，KISS / 最小改动）：引擎内加入“refresh 后 order_view confirm”

**核心想法**：  
当引擎已经通过 `get_game_list/get_game_more` 把 `params` 刷到“目标盘口线/正确 wtype”，在真正 `FT_bet` 前，再做一次**轻量** `FT_order_view`，只要返回 OK 且不冲突，就用它的 `ioratio`（必要时也可用它的 `con/ratio` 做归一化）。

**收益**
- 直接复用 cc 的“confirmOdds”实践，但只对“本次要下的这一条”做确认（不会引入大规模并发）
- 降低 `game_more ior` 与下注口径不一致的概率
- 对“附加盘口 wtype（ARE/AROU/...）”尤其有效：game_more 负责定位盘口线，order_view 负责给出此刻可下注的水位

**成本/风险**
- 增加一次请求；需加硬超时与可开关配置，避免反过来拖慢
- order_view 在少数站点/运动可能返回 `CheckEMNU`，需容错（失败就放过，不阻断下注）

**建议新增配置（默认关闭，逐步灰度）**
- `order_view_confirm_before_bet`: true/false
- `order_view_confirm_timeout_s`: 1~2（one-shot 模式也受外层 hard timeout 约束）
- `order_view_confirm_cache_ttl_s`: 0.3~0.8（只缓存“极短窗口内重复调用”）

### 方案 B（中等改造，SRP）：抽出 OddsProvider / LineMapper 模块（Python 单栈统一）

把这些逻辑从 `engine.py` 抽出去：
- `FT_order_view`（预校验/确认水位）
- `get_game_list`（主盘 + 分盘线）
- `get_game_more`（附加盘 A-F）
- “目标盘口线定位 + 刷新 params” 的策略

形成 `src/hdzz/core/odds/`（或 `src/hdzz/core/follower/odds_provider.py`）：
- 引擎只管调度与风控（队列/分单/重试/超时）
- OddsProvider 只管“在限定时间内把 params 刷到可下注状态”

**收益**
- 彻底收敛重复实现：`fetch_odds.py` / `mos_api.py` / `engine.py` / `cc` 的解析规则统一口径
- 可测试性显著提升（可以用固定 XML/JSON fixtures 做单测）

**成本/风险**
- 文件改动面更大（属于“批量改造”），建议你确认后再做

### 方案 C（服务化）：复用 cc server 作为“行情服务”（Python 通过 HTTP 调）

让 `cc/src/server.js` 常驻在同机/局域网，Python 引擎遇到某些失败路径时调用：
- `/api/odds?gid=...&confirm=true` 获取“经过 order_view 校验过的主盘快照”

**收益**
- 最大化利用 Playwright 常驻会话（更像浏览器）
- 能快速给 WebUI/排查提供可视化行情

**成本/风险**
- 生产依赖 Node/Playwright（部署复杂、资源占用高、稳定性要单独保障）
- 跨进程调用与故障切换需要额外设计

---

## 4. 推荐落地路径（按优先级分阶段）

### P0（当天可做）：先把“慢”的真实来源量化出来

**目标**：不要凭感觉判断“拿不到水位”，用现有日志字段把耗时拆出来。

执行项：
- 用 `follow_results.jsonl` 统计每单：
  - `1X015/1X016/betError` 的出现次数
  - `timings.order_view_s / timings.odds_refresh_gl_s / timings.odds_refresh_gm_s` 的分布
- 结合 `split_delay_seconds/split_count` 看“同一订单跟全”的总耗时窗口

产出：
- 一张表：Top 慢单（按 attempt 总耗时/重试次数）
- 一张表：Top 失败原因（按订单终态）

### P1（最小改动，上线风险最低）：实现方案 A 的“confirm”

设计要点：
- 只在**即将提交** `FT_bet` 前做一次确认（并且必须有硬超时）
- 失败就跳过（不阻断下注，不引入新的等待）
- 只在以下条件触发（避免无意义请求）：
  - 本次 attempt 刚做过 `game_list/game_more` 刷新，或
  - 上一次下注返回 `1X015/1X016/betError`（快速重试窗口内）

预期指标：
- `1X015/1X016/betError` 每单次数下降
- 同等失败率下，单次 attempt 的总耗时下降（少走深度找盘）

### P2（结构优化，可选）：做方案 B 的模块化抽取 + 去重

优先抽取顺序（最不影响行为 → 最影响行为）：
1) 抽出 `ft_order_view` + order_view 的缓存/确认策略
2) 抽出 `get_game_list/get_game_more` 请求与短 TTL cache
3) 抽出 `try_refresh_*`（让球/大小）策略为纯函数 + fixtures 单测
4) `fetch_odds.py` / `mos_api.py` 改为复用新模块（避免漂移）

---

## 5. 风险点与回滚策略

- 风险：新增 confirm 请求导致“平均耗时上升”  
  - 对策：严格硬超时 + 默认关闭 + 分阶段开启（先只对重试单开启）
- 风险：order_view 在特定上下文返回 `CheckEMNU`  
  - 对策：confirm 失败不阻断下注；并把失败原因记录到 result（便于复盘）
- 风险：严格跟原线场景被意外改线  
  - 对策：保持现有的“提交前二次校验”护栏（已存在），并把 confirm 仅用于 `ioratio`（除非显式允许变盘）

---

## 6. 需要你确认的 1 个关键业务选择（否则无法定最终策略）

当“原盘口线消失/主盘变线”时，你希望：

- A) **严格跟原线**：找不到就等待/停单（降低打错盘风险，但更容易“没跟全”）
- B) **允许变盘换线**：原线消失就跟最新盘口线（提高“跟上/跟全”概率，但可能与信号盘口线不完全一致）

目前配置里 `allow_line_change=false` 且 `allow_total_line_change=false`，但引擎对大小盘在某些场景已内置“找不到就跟最新”的回退逻辑；要不要把让球盘也纳入类似策略，需要你明确选 A 还是 B。
