# hdzz × cc 融合实施任务清单（可执行版）

> 日期：2026-02-07  
> 适用范围：在不引入生产 Node/Playwright 依赖的前提下，把 cc 的“`FT_order_view` 校验最新水位”能力融入 Python 跟单引擎，优先降低 `1X015/1X016/betError` 重试带来的整体变慢。  
> 重要约束：本清单**不包含** `git commit/push/reset` 等操作（按你的要求），仅列出代码改动与验证步骤。

---

## 阶段 0：基线确认（不改代码）

### Task 0.1：确认当前实际运行的是新引擎

**检查点**
- 跟单进程入口应为：`app/follow_engine.py`（其内部 re-export 到 `src/hdzz/core/follower/engine.py`）

**验证**
- 运行中日志（或进程命令行）应包含 `follow_engine.py`，且你能在日志里看到类似 `timings.order_view_s`、`odds_refresh_gl_s/gm_s` 的字段。

### Task 0.2：用现有工具量化“慢点”和“打错盘风险”

**工具**
- `app/tools/analyze_follow_results.py`（仓库已内置）

**运行**
- `python -u "app/tools/analyze_follow_results.py" --file "app/data/follow_results.jsonl" --limit 5000`

**你要拿到的基线**
- `attempt_wall_s/menu_warmup_s/order_view_s/odds_refresh_gl_s/odds_refresh_gm_s/bet_request_s` 的 p50/p90/p99/max
- “后台原线 != 实际提交盘口线”的条数（特别关注 `strict_line=true` 的情况）

### Task 0.3：确认当前配置是否本身制造“慢”

**文件**
- `app/data/follow_settings.json`

**检查点**
- `split_count`、`split_delay_seconds`、`queue_max_per_tick`
- `fast_retry_1X015_max_attempts`、`fast_retry_betError_*`

**判定建议（经验）**
- `split_delay_seconds >= 30` 会显著拉长“同一订单跟全”的窗口（并提高变盘概率）；如果你追求“跟全优先”，建议后续作为独立优化项处理。

---

## 阶段 1：最小融合（方案 A：refresh 后 order_view confirm）

> 目标：在不推翻现有 `game_list/game_more` 盘口线定位策略的前提下，只补齐“最终用于提交的 ioratio 更贴近预校验/下注口径”。

### Task 1.1：新增配置开关（默认关闭）

**文件**
- 修改：`src/hdzz/core/follower/engine.py`
- 修改：`app/data/follow_settings.json`（先不改默认值，等灰度时再开）

**建议配置项**
- `order_view_confirm_before_bet`: `false`
- `order_view_confirm_timeout_s`: `2`（或 `1`）
- `order_view_confirm_cache_ttl_s`: `0.5`

**验证**
- 引擎启动后能读取到这些配置（日志/结果里可记录回显）

### Task 1.2：为 `FT_order_view` 增加“极短 TTL cache”（只为去重，不为复用历史）

**文件**
- 修改：`src/hdzz/core/follower/engine.py`

**实现要点**
- key 建议包含：`base_url|uid|ver|gid|gtype|wtype|chose_team`
- TTL 建议 0.3~0.8 秒；只解决“同一次 attempt 内重复调用”的去重问题
- cache 命中返回时，仍要把它当作“可能已过期”的数据：只用于 confirm 失败时的兜底，不用于强制覆盖逻辑

**验证**
- 在 result 里记录 `order_view_confirm_cache_hit=true/false`（便于复盘）

### Task 1.3：在提交 `FT_bet` 前插入 confirm（核心融合点）

**文件**
- 修改：`src/hdzz/core/follower/engine.py`

**触发条件（建议）**
- 仅当 `order_view_confirm_before_bet=true` 且：
  - 本次 attempt 不是 `fast_first_bet`，并且
  - 本次 attempt 发生过刷新（`odds_refresh_*` 任一成功）或刚经历 `1X015/1X016/betError` 快速重试窗口

**动作**
- 用当前 `params`（含可能是附加盘的 `wtype/rtype/chose_team/con/ratio`）调用 `member_order_view`
- 只在“盘口线一致 / 或显式允许变盘”的前提下使用它的 `ioratio`（必要时也可做 con/ratio 归一化，但默认只动 ioratio）

**强约束（避免引入新风险）**
- confirm 必须有硬超时；失败必须“放过”，不能阻断下注
- 严格跟原线（`allow_line_change_effective=false`）时，继续沿用现有的“提交前二次校验”护栏，防止改线打错盘

**可观测性（必须落盘）**
- `timings.order_view_confirm_s`
- `order_view_confirm_msg`（ok / code_not_ok / non_xml / timeout 等）
- `order_view_confirm_head_min`（仅记录 `code/errormsg/msg/ioratio/con/ratio`，不要落 uid/cookie）
- `order_view_confirm_applied=true/false`（以及 applied 的字段列表）

### Task 1.4：灰度策略（避免一次性全量打开）

**建议灰度方式（选 1）**
- 方式 A：只在 `attempt > 1`（发生过失败）时启用 confirm
- 方式 B：只对滚球（live）启用 confirm
- 方式 C：只对让球盘（RE/R/HRE）启用 confirm（你最在意“附加盘口/变盘”）

**验证**
- 对照基线：`1X015/1X016/betError` 的每单次数应下降
- `attempt_wall_s` 的 p90/p99 不应上升（或上升极小）

---

## 阶段 2：收敛重复实现（可选，但建议做）

> 目标：把 cc / mos_api / fetch_odds / watch_handicap_odds 里重复的解析规则，收敛到 Python 单一实现，减少长期漂移风险。

### Task 2.1：抽出“盘口/水位解析工具”到公共模块

**候选重复点（现状确实存在）**
- `try_refresh_total_odds_via_game_more()` 内的 `_pick_total_ior_for_ratio_tag` 与 `app/tools/watch_handicap_odds.py` 的同名逻辑高度相似
- `mos_api.py` / `fetch_odds.py` 与引擎 `ft_order_view` 的能力重叠

**建议落点**
- 新增：`src/hdzz/core/odds/`（或 `src/hdzz/common/odds.py`）

**验证**
- 迁移后 `watch_handicap_odds.py` 仅做展示，不再复制解析逻辑

### Task 2.2：将 `mos_api.py` 复用统一解析（避免两套口径）

**目标**
- `mos_api.py` 的 `parse_game_more_xml` 尽量复用引擎侧“game_more 解析/选 ior”的规则（或最小复用其中关键函数）

**收益**
- 诊断接口/调试脚本看到的“水位/盘口线”与跟单引擎口径一致

---

## 阶段 3：回归验证与上线准备（高风险：真钱下注）

> 这部分属于资金高风险操作，只列验证思路；是否执行由你确认。

### Task 3.1：小额、少会员、少目标灰度

**建议**
- 只开 1-2 个 target
- 只开 1-2 个 member
- stake 降到最小可下注金额附近

**观察项**
- 单场盘口快速变化时，`1X015/1X016/betError` 是否明显减少
- `LINE_NOT_FOUND` 的等待窗口是否合理（不会长时间占队列）

### Task 3.2：回滚开关

**原则**
- 出现异常（耗时上升/误判/误改线）第一时间把 `order_view_confirm_before_bet` 关掉即可回滚行为

