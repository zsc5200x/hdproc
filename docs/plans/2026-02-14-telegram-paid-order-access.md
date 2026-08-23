# Telegram 付费查阅订单机器人 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 给现有 Telegram 机器人增加“付费查阅订单/报表”能力：普通用户需购买权限后才能查询，管理员可授权与管理。

**Architecture:** 采用“机器人内权限闸门 + 本地账本落盘”方案。查询类命令统一走 `paid_access` 校验与扣次；管理员命令负责开通/续期/撤销。首版不接第三方支付网关，采用人工收款后管理员在机器人内开通，先保证稳定可控，再预留支付接口。

**Tech Stack:** Python 3、requests、现有 Telegram Long Poll 机器人、JSON 本地持久化（`data/*.json`）、pytest。

---

> 约束说明：按仓库规则，本计划不包含 git commit 步骤。

### Task 1: 新增付费权限账本模块（数据模型 + 读写 + 核心计算）

**Files:**
- Create: `src/hdzz/api/telegram/paid_access.py`
- Test: `tests/test_telegram_paid_access.py`

**Step 1: Write the failing test**

```python
# tests/test_telegram_paid_access.py
from pathlib import Path
from hdzz.api.telegram.paid_access import ensure_paid_store, check_access

def test_default_store_and_expired_access(tmp_path: Path):
    path = tmp_path / "tg_paid_access.json"
    store = ensure_paid_store(path)
    assert "plans" in store and "users" in store
    ok, info = check_access(store, subject_key="chat:1001")
    assert ok is False
    assert info["reason"] in {"not_found", "expired", "quota_empty"}
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_telegram_paid_access.py::test_default_store_and_expired_access -v`
Expected: FAIL（模块或函数不存在）

**Step 3: Write minimal implementation**

```python
# src/hdzz/api/telegram/paid_access.py
DEFAULT_PLANS = {
    "trial": {"name": "试用", "days": 1, "quota": 20, "price": 0.0, "enabled": True},
    "day": {"name": "日卡", "days": 1, "quota": 200, "price": 28.0, "enabled": True},
    "week": {"name": "周卡", "days": 7, "quota": 2000, "price": 128.0, "enabled": True},
}

def ensure_paid_store(path): ...
def check_access(store, subject_key): ...
def consume_quota(store, subject_key, feature): ...
def grant_access(store, subject_key, plan_id, now_ts=None): ...
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_telegram_paid_access.py::test_default_store_and_expired_access -v`
Expected: PASS

**Step 5: Add second core test for quota consumption**

Run: `pytest tests/test_telegram_paid_access.py -v`
Expected: PASS（至少覆盖“过期、剩余次数、扣次”三条路径）

---

### Task 2: 机器人菜单与用户自助入口（购买说明 / 我的权限）

**Files:**
- Modify: `src/hdzz/api/telegram/bot.py`
- Test: `tests/test_telegram_order_buttons.py`

**Step 1: Write the failing test**

```python
# tests/test_telegram_order_buttons.py
from hdzz.api.telegram.bot import main_keyboard

def test_main_keyboard_contains_paid_buttons():
    kb = main_keyboard()
    flat = [btn.get("text") for row in kb.get("keyboard", []) for btn in row]
    assert "💳 购买查阅" in flat
    assert "🪪 我的权限" in flat
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_telegram_order_buttons.py::test_main_keyboard_contains_paid_buttons -v`
Expected: FAIL（按钮文案不存在）

**Step 3: Write minimal implementation**

```python
# src/hdzz/api/telegram/bot.py
BTN_BUY_ACCESS = "💳 购买查阅"
BTN_MY_ACCESS = "🪪 我的权限"

def main_keyboard():
    return {
        "keyboard": [
            [{"text": BTN_QUERY_BALANCE}, {"text": BTN_TODAY_REPORT}],
            [{"text": BTN_PNL_REPORT}, {"text": BTN_WINRATE}],
            [{"text": BTN_BUY_ACCESS}, {"text": BTN_MY_ACCESS}],
            [{"text": BTN_ADMIN_AUTH}],
        ],
        ...
    }
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_telegram_order_buttons.py::test_main_keyboard_contains_paid_buttons -v`
Expected: PASS

**Step 5: Add command parsing tests**

Run: `pytest tests/test_telegram_order_buttons.py -k "paid_buttons or roi_button" -v`
Expected: PASS（确保旧按钮兼容不回归）

---

### Task 3: 接入“付费闸门”到文本查询命令

**Files:**
- Modify: `src/hdzz/api/telegram/bot.py`
- Test: `tests/test_telegram_paid_access.py`

**Step 1: Write the failing test**

```python
def test_query_requires_paid_access_when_enabled(tmp_path):
    from hdzz.api.telegram.bot import should_allow_query

    allowed, reason = should_allow_query(
        paid_enabled=True,
        is_admin=False,
        in_free_grants=False,
        paid_ok=False,
    )
    assert allowed is False
    assert reason == "paid_required"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_telegram_paid_access.py::test_query_requires_paid_access_when_enabled -v`
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# src/hdzz/api/telegram/bot.py
# 查询类命令：余额/今日跟单/输赢报表/投资回报率/oid
# 放行规则：管理员 or 免费授权(grants) or 付费有效

def should_allow_query(*, paid_enabled, is_admin, in_free_grants, paid_ok):
    if is_admin or in_free_grants:
        return True, "ok"
    if not paid_enabled:
        return True, "paid_disabled"
    return (True, "ok") if paid_ok else (False, "paid_required")
```

并在主循环 `needs_auth` 判定后增加：
- 购买按钮：返回套餐与付款说明
- 我的权限：返回到期时间、剩余次数
- 被拦截：提示“未开通付费查阅，发送 💳 购买查阅”

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_telegram_paid_access.py::test_query_requires_paid_access_when_enabled -v`
Expected: PASS

**Step 5: Run focused regression for existing auth flow**

Run: `pytest tests/test_telegram_order_buttons.py -k "roi_button or parse_order_callback_data" -v`
Expected: PASS（原授权逻辑未破坏）

---

### Task 4: 管理员付费管理命令（开通 / 续期 / 撤销 / 查询）

**Files:**
- Modify: `src/hdzz/api/telegram/bot.py`
- Modify: `src/hdzz/api/telegram/paid_access.py`
- Test: `tests/test_telegram_paid_access.py`

**Step 1: Write the failing test**

```python
def test_admin_can_grant_and_revoke_paid_access(tmp_path):
    from hdzz.api.telegram.paid_access import ensure_paid_store, grant_access, revoke_access, check_access

    p = tmp_path / "tg_paid_access.json"
    store = ensure_paid_store(p)
    store, changed, _ = grant_access(store, "chat:1001", "week")
    assert changed
    ok, _ = check_access(store, "chat:1001")
    assert ok is True

    store, changed2, _ = revoke_access(store, "chat:1001")
    assert changed2
    ok2, _ = check_access(store, "chat:1001")
    assert ok2 is False
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_telegram_paid_access.py::test_admin_can_grant_and_revoke_paid_access -v`
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# bot.py 新增管理员命令
# /paid_grant <chat_id|user_id|@username> <plan_id>
# /paid_extend <chat_id|user_id|@username> <days>
# /paid_revoke <chat_id|user_id|@username>
# /paid_info <chat_id|user_id|@username>
```

实现要点：
- 仅管理员可执行。
- 使用与授权系统一致的目标解析（chat_id/user_id/@username）。
- 每次修改都落盘到 `data/tg_paid_access.json`。

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_telegram_paid_access.py::test_admin_can_grant_and_revoke_paid_access -v`
Expected: PASS

**Step 5: Add parser tests for new admin commands**

Run: `pytest tests/test_telegram_paid_access.py -k "admin and paid" -v`
Expected: PASS

---

### Task 5: Callback 按钮查询也接入付费闸门 + 扣次

**Files:**
- Modify: `src/hdzz/api/telegram/bot.py`
- Modify: `src/hdzz/api/telegram/paid_access.py`
- Test: `tests/test_telegram_paid_access.py`

**Step 1: Write the failing test**

```python
def test_paid_quota_consumed_by_callback_queries(tmp_path):
    from hdzz.api.telegram.paid_access import ensure_paid_store, grant_access, consume_quota, check_access

    p = tmp_path / "tg_paid_access.json"
    store = ensure_paid_store(p)
    store, _, _ = grant_access(store, "chat:1001", "trial")

    ok1, _ = consume_quota(store, "chat:1001", feature="callback:res")
    assert ok1
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_telegram_paid_access.py::test_paid_quota_consumed_by_callback_queries -v`
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# callback_query 分支（res/log/pnl/amt/yday/today）
# 在执行 build_xxx 报表前先 check_access + consume_quota
# 拦截时 answerCallbackQuery(show_alert=True, text="未开通或次数不足")
```

实现要点：
- 管理员/免费授权用户不扣次。
- 普通付费用户每次查询扣 1 次。
- 扣次失败时不进入查询逻辑。

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_telegram_paid_access.py::test_paid_quota_consumed_by_callback_queries -v`
Expected: PASS

**Step 5: Run callback 相关回归测试**

Run: `pytest tests/test_telegram_order_buttons.py::TestTelegramOrderButtons::test_parse_order_callback_data -v`
Expected: PASS

---

### Task 6: 运营文案、配置开关、上线检查清单

**Files:**
- Modify: `src/hdzz/api/telegram/bot.py`
- Modify: `src/hdzz/api/webui/server.py`
- Modify: `app/webui/index.html`
- Create: `docs/telegram-paid-access-ops.md`
- Test: `tests/test_telegram_paid_access.py`

**Step 1: Write the failing test (config defaults)**

```python
def test_paid_feature_config_defaults():
    from hdzz.api.telegram.bot import load_paid_feature_flags
    flags = load_paid_feature_flags({})
    assert flags["enabled"] is False
    assert "buy_tip" in flags
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_telegram_paid_access.py::test_paid_feature_config_defaults -v`
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# config.json 新增
# telegram_paid_enabled: bool
# telegram_paid_buy_tip: str
# telegram_paid_currency: str
# telegram_paid_contact: str
```

并在 WebUI 机器人配置页增加输入项：
- 开启付费查阅
- 套餐说明/付款说明
- 联系方式

**Step 4: Run tests + smoke checks**

Run: `pytest tests/test_telegram_paid_access.py tests/test_telegram_order_buttons.py -v`
Expected: PASS

Run: `python app/telegram_bot.py --config config.json --data-dir data --orders-dir orders --state .run/tg_bot_state.json`
Expected: 机器人正常启动，无语法错误。

**Step 5: 写上线操作文档**

文档必须包含：
- 如何开关功能
- 如何新增套餐
- 如何给用户开通/续费/封禁
- 如何排查“用户说已付款但查不了”的问题

---

## 验收标准（Definition of Done）

1. 普通未开通用户：点击“今日跟单/输赢报表/投资回报率/订单按钮”会被拦截并收到购买指引。  
2. 管理员与免费授权用户：不受付费限制，原功能可用。  
3. 付费用户：在有效期 + 次数充足时可查询，且每次查询正确扣次。  
4. 到期/次数耗尽：立即生效拦截，文案清晰。  
5. 重启进程后权限不丢失（`data/tg_paid_access.json` 持久化）。  
6. 现有按钮弹窗逻辑、报表逻辑、授权逻辑不回归（已有测试通过）。

## 风险与回滚

- 风险1：权限判断写错导致全员被拦截。  
  - 兜底：`telegram_paid_enabled=false` 一键关闭付费闸门。
- 风险2：扣次时机不对导致“重复扣费”。  
  - 兜底：仅在查询成功执行前扣 1 次；失败不扣次（或可配置）。
- 风险3：账本并发写冲突。  
  - 兜底：复用 `atomic_write_json`，并在单进程机器人内串行写。

