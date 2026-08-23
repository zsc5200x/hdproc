# Standalone 付费信号机器人（隐藏推送 + 付费解锁） Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 实现一个独立 Telegram 机器人：后台来单先推“隐藏版预告”，用户点“购买/查看订单”，只有已付费用户可查看完整订单内容。

**Architecture:** 新增独立进程 `telegram_premium_bot`，直接读取 `orders/*.jsonl` 监听新订单并推送模板消息；不依赖 WUI 配置。权限采用本地 JSON 账本（用户到期时间），管理员通过机器人命令开通/续期/撤销。查看完整订单时优先私发给点击者（避免群内泄露），未开通则弹窗拦截。

**Tech Stack:** Python 3、requests、现有 long polling 逻辑、`orders/*.jsonl` 增量读取、JSON 持久化、pytest。

---

> 约束说明：按仓库规则，本计划不包含 git commit 步骤。

### Task 1: 创建独立机器人骨架（与现有 bot 解耦）

**Files:**
- Create: `src/hdzz/api/telegram/premium_bot.py`
- Create: `app/telegram_premium_bot.py`
- Test: `tests/test_telegram_premium_bootstrap.py`

**Step 1: Write the failing test**

```python
# tests/test_telegram_premium_bootstrap.py

def test_premium_bot_module_has_main():
    from hdzz.api.telegram import premium_bot
    assert callable(getattr(premium_bot, "main", None))
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_telegram_premium_bootstrap.py::test_premium_bot_module_has_main -v`
Expected: FAIL（模块不存在）

**Step 3: Write minimal implementation**

```python
# src/hdzz/api/telegram/premium_bot.py
def main() -> int:
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
```

```python
# app/telegram_premium_bot.py
# 与 app/telegram_bot.py 同样的 bootstrap 方式
from hdzz.api.telegram.premium_bot import *
if __name__ == "__main__":
    raise SystemExit(main())
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_telegram_premium_bootstrap.py::test_premium_bot_module_has_main -v`
Expected: PASS

**Step 5: Add startup smoke test**

Run: `python app/telegram_premium_bot.py --help`
Expected: 正常输出参数说明。

---

### Task 2: 硬编码配置区（不走 WUI）+ 安全校验

**Files:**
- Modify: `src/hdzz/api/telegram/premium_bot.py`
- Test: `tests/test_telegram_premium_settings.py`

**Step 1: Write the failing test**

```python
# tests/test_telegram_premium_settings.py

def test_settings_have_required_fields():
    from hdzz.api.telegram.premium_bot import PREMIUM_SETTINGS
    assert PREMIUM_SETTINGS["bot_token"]
    assert PREMIUM_SETTINGS["push_chat_id"]
    assert isinstance(PREMIUM_SETTINGS["admin_ids"], list)
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_telegram_premium_settings.py::test_settings_have_required_fields -v`
Expected: FAIL

**Step 3: Write minimal implementation**

```python
PREMIUM_SETTINGS = {
    "bot_token": "<在代码里填写>",
    "push_chat_id": "-100xxxxxxxxxx",
    "admin_ids": [123456789],
    "buy_contact": "@okpay",
    "business_contact": "管理员用户名",
    "poll_timeout": 20,
}
```

并加启动校验：缺字段时打印错误并退出。

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_telegram_premium_settings.py::test_settings_have_required_fields -v`
Expected: PASS

**Step 5: Add validation test for empty token**

Run: `pytest tests/test_telegram_premium_settings.py -v`
Expected: PASS（覆盖“空 token 拒绝启动”）

---

### Task 3: 后台来单监听 + 隐藏版模板推送（含两个按钮）

**Files:**
- Modify: `src/hdzz/api/telegram/premium_bot.py`
- Test: `tests/test_telegram_premium_templates.py`

**Step 1: Write the failing test**

```python
# tests/test_telegram_premium_templates.py

def test_teaser_template_masks_league_and_teams():
    from hdzz.api.telegram.premium_bot import build_teaser_message
    order = {
        "live": True,
        "league": "西班牙杯",
        "sides": "马竞 v 巴萨",
    }
    txt = build_teaser_message(order)
    assert "⚡ 出货提醒｜滚球" in txt
    assert "联赛" not in txt or "***" in txt
    assert "仅限已开通权限账户查看完整内容" in txt
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_telegram_premium_templates.py::test_teaser_template_masks_league_and_teams -v`
Expected: FAIL

**Step 3: Write minimal implementation**

模板固定为：

```text
⚡ 出货提醒｜{滚球/早盘}

🏆 ***
⚽ *** vs ***

🔒 信号已生成
仅限已开通权限账户查看完整内容

👇 点击下方按钮验证权限。
```

按钮固定：
- `💳 点击购买`（callback: `ps|buy|{order_id}`）
- `🔓 查看订单`（callback: `ps|view|{order_id}`）

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_telegram_premium_templates.py::test_teaser_template_masks_league_and_teams -v`
Expected: PASS

**Step 5: Add feed de-dup test**

Run: `pytest tests/test_telegram_premium_templates.py -v`
Expected: PASS（同一 orderId 不重复推送）

---

### Task 4: 付费权限模型（按到期时间，不做次数扣减）

**Files:**
- Create: `src/hdzz/api/telegram/premium_access.py`
- Test: `tests/test_telegram_premium_access.py`

**Step 1: Write the failing test**

```python
# tests/test_telegram_premium_access.py
from pathlib import Path

def test_user_active_when_not_expired(tmp_path: Path):
    from hdzz.api.telegram.premium_access import ensure_access_doc, upsert_access, is_active
    p = tmp_path / "premium_access.json"
    doc = ensure_access_doc(p)
    doc = upsert_access(doc, user_key="uid:1001", days=7)
    ok, info = is_active(doc, "uid:1001")
    assert ok is True
    assert info["expired"] is False
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_telegram_premium_access.py::test_user_active_when_not_expired -v`
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# src/hdzz/api/telegram/premium_access.py
# 存储文件: data/premium_access.json
# 结构:
# {
#   "users": {"uid:1001": {"expire_at": 1777777777, "note": "manual"}},
#   "updated_at": "..."
# }

def ensure_access_doc(path): ...
def upsert_access(doc, user_key, days): ...
def revoke_access(doc, user_key): ...
def is_active(doc, user_key): ...
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_telegram_premium_access.py::test_user_active_when_not_expired -v`
Expected: PASS

**Step 5: Add expired/revoke tests**

Run: `pytest tests/test_telegram_premium_access.py -v`
Expected: PASS

---

### Task 5: 回调逻辑（购买/查看订单）与“仅付费可见”落地

**Files:**
- Modify: `src/hdzz/api/telegram/premium_bot.py`
- Modify: `src/hdzz/api/telegram/premium_access.py`
- Test: `tests/test_telegram_premium_callbacks.py`

**Step 1: Write the failing test**

```python
# tests/test_telegram_premium_callbacks.py

def test_view_order_denied_for_non_paid():
    from hdzz.api.telegram.premium_bot import check_view_permission
    ok, reason = check_view_permission(is_admin=False, paid_active=False)
    assert ok is False
    assert reason == "paid_required"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_telegram_premium_callbacks.py::test_view_order_denied_for_non_paid -v`
Expected: FAIL

**Step 3: Write minimal implementation**

行为要求：
1. 点击 `💳 点击购买`
   - 弹窗展示：充值联系 `@okpay`、商务联系管理员。
2. 点击 `🔓 查看订单`
   - 非付费：`answerCallbackQuery(show_alert=True)` 拒绝。
   - 付费：发送完整模板（尽量私发给点击者 `chat_id=user_id`）。
   - 私发失败：提示“请先私聊机器人发送 /start 后再查看”。

完整模板：

```text
📣 新注单提醒｜{滚球/早盘}

🏆 联赛：{联赛}
⚽ 对阵：{主队} vs {客队}
⏱ 时间：{时间}

🎯 推荐玩法：{玩法}
💰 金额：{金额}

━━━━━━━━━━━━━━
📌 说明
充值请联系 @okpay
官方合作请联系管理员

订单来源于多渠道数据整合，
胜率长期稳定，历史 ROI 长期保持 15%+
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_telegram_premium_callbacks.py::test_view_order_denied_for_non_paid -v`
Expected: PASS

**Step 5: Add paid-success path test**

Run: `pytest tests/test_telegram_premium_callbacks.py -v`
Expected: PASS（覆盖“拒绝 + 放行”双路径）

---

### Task 6: 管理员命令（开通/续期/取消/查询）

**Files:**
- Modify: `src/hdzz/api/telegram/premium_bot.py`
- Modify: `src/hdzz/api/telegram/premium_access.py`
- Test: `tests/test_telegram_premium_admin.py`

**Step 1: Write the failing test**

```python
# tests/test_telegram_premium_admin.py

def test_admin_command_parse_grant():
    from hdzz.api.telegram.premium_bot import parse_admin_grant
    uid, days = parse_admin_grant("/vip_add 1001 7")
    assert uid == "1001"
    assert days == 7
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_telegram_premium_admin.py::test_admin_command_parse_grant -v`
Expected: FAIL

**Step 3: Write minimal implementation**

新增命令：
- `/vip_add <user_id> <days>` 开通
- `/vip_extend <user_id> <days>` 续期
- `/vip_del <user_id>` 取消
- `/vip_info <user_id>` 查询
- `/vip_list` 最近 30 个有效用户

仅 `PREMIUM_SETTINGS["admin_ids"]` 内用户可执行。

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_telegram_premium_admin.py::test_admin_command_parse_grant -v`
Expected: PASS

**Step 5: Run admin suite**

Run: `pytest tests/test_telegram_premium_admin.py -v`
Expected: PASS

---

### Task 7: 进程接入（可由 app.py 一键启动）+ 运维文档

**Files:**
- Modify: `app/app.py`
- Create: `docs/telegram-premium-bot-ops.md`
- Test: `tests/test_telegram_premium_bootstrap.py`

**Step 1: Write the failing test**

```python
# tests/test_telegram_premium_bootstrap.py

def test_app_workers_include_premium_bot():
    from app.app import workers
    ws = workers("127.0.0.1", 8080, 10, 5)
    names = [w.name for w in ws]
    assert "telegram_premium_bot" in names
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_telegram_premium_bootstrap.py::test_app_workers_include_premium_bot -v`
Expected: FAIL

**Step 3: Write minimal implementation**

- 在 `workers()` / `core_workers()` 增加 `telegram_premium_bot.py`。
- 在 `_WORKER_MARKERS` 增加 `telegram_premium_bot.py`，避免 stop/restart 漏管。

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_telegram_premium_bootstrap.py::test_app_workers_include_premium_bot -v`
Expected: PASS

**Step 5: Add ops doc**

文档包含：
- 如何填写 `PREMIUM_SETTINGS`。
- 如何启动/停止机器人。
- 如何给用户开通权限。
- 常见故障（私发失败、按钮无响应、重复推送）排查。

---

## 验收标准（Definition of Done）

1. 后台来新订单时，机器人自动推送“隐藏版预告”模板。  
2. 预告消息包含两个按钮：`💳 点击购买` / `🔓 查看订单`。  
3. 非付费用户点击“查看订单”被拦截，且不会看到完整联赛/球队/玩法。  
4. 付费用户点击后可收到完整订单模板（优先私发，避免群内泄露）。  
5. 整个功能不依赖 WUI 配置（token/chat/admin 等来自代码常量区）。  
6. 管理员可通过命令开通、续期、撤销和查询用户权限。  
7. 重启后权限数据与订单偏移状态可恢复（不重复刷历史单）。

## 风险与回滚

- 风险1：私发失败导致用户“点了没反应”。  
  - 处理：统一弹窗提示“先私聊机器人 /start”。
- 风险2：与现有机器人重复推送。  
  - 处理：独立 token；或关闭旧机器人订单推送 token。
- 风险3：硬编码配置泄露风险。  
  - 处理：单独常量区 + 后续可迁移到私有环境变量。

