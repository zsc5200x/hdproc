# Standalone 付费信号机器人（第三方钱包收款闭环） Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 做一个独立 Telegram 机器人：后台来单先发“隐藏版预告”，用户点击“购买/查看订单”；购买后跳转第三方钱包支付，支付成功自动开通权限，再可查看完整订单。

**Architecture:** 采用“独立机器人进程 + 支付网关适配层 + 本地权限账本 + 支付回调”四层架构。机器人读取 `orders/*.jsonl` 推送隐藏消息；点击“购买”先创建支付订单，再返回钱包支付 URL 按钮；第三方钱包回调验签成功后自动开通用户权限；点击“查看订单”时按权限放行。

**Tech Stack:** Python 3、requests、Telegram Bot API（sendMessage/callback_query）、JSON 落盘（权限/支付单/推送索引）、pytest。

---

> 约束说明：按仓库规则，本计划不包含 git commit 步骤。

## OkayPay 对接结论（基于你提供的两个文件）

- 商户接口基地址：`https://api.okaypay.me/shop/`
- 主要接口：
  - 创建充值单：`POST /payLink`
  - 查询充值单：`POST /checkDeposit`
  - 商户余额：`POST /balance`
- 创建支付链接核心参数：
  - `id`（商户ID，签名时会带）
  - `unique_id`（我们自己的订单号，必须全局唯一）
  - `name`（展示名称，可选）
  - `amount`（金额）
  - `coin`（`USDT` 或 `TRX`）
  - `return_url`（支付后返回地址，可选）
  - `callback_url`（本订单回调地址，可选，建议必传）
  - `status`（文档提到默认 0，可不传）
- 响应关键字段（payLink）：
  - `data.order_id`（钱包订单号）
  - `data.pay_url`（支付跳转链接，按钮直接用这个）
- 签名算法（按 PHP 示例）：
  1. 参数中加入 `id`
  2. 过滤空值
  3. 按 key 升序排序
  4. 构建 querystring 后 `urldecode`
  5. 追加 `&token=<商户密钥>`
  6. `MD5` 后转大写，得到 `sign`
- 回调验签：同样算法，去掉入参 `sign` 后重算比对。
- 回调状态兼容（文档与示例写法有差异）：
  - 以 `type=deposit` 且 `status=1` 为支付成功主判据
  - 同时兼容 `code=10000` / `status=success` 这类示例写法
  - 数据结构兼容“平铺字段”与“嵌套 data 字段”两种格式
- 请求编码：文档写 JSON，但官方 PHP 例子使用 `application/x-www-form-urlencoded`；首版按 PHP 示例实现，必要时加 JSON 回退。

## 本次业务参数确认（2026-02-15）

- 商户 `id`：`27596`
- 商户 `secret/token`：已提供（实现时直接写在代码常量区，后续建议改为环境变量）
- 支付币种：`USDT`
- 价格策略：每场订单固定 `8 USDT`
- 联系方式：
  - 充值联系：`@sgty888`
  - 商务联系：`@sgty888`
- 推送方式：**不推频道/群**，改为机器人私聊内推送（仅对已与机器人建立会话的用户）
- 回调策略：回调地址暂缺，先用 `checkDeposit` 轮询为主；后续补 `callback_url` 时再打开回调实时开通

### Task 1: 独立机器人基础骨架（不依赖 WUI 配置）

**Files:**
- Create: `src/hdzz/api/telegram/premium_bot.py`
- Create: `app/telegram_premium_bot.py`
- Test: `tests/test_telegram_premium_bootstrap.py`

**Step 1: Write the failing test**

```python
def test_premium_bot_main_exists():
    from hdzz.api.telegram import premium_bot
    assert callable(getattr(premium_bot, "main", None))
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_telegram_premium_bootstrap.py::test_premium_bot_main_exists -v`
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# src/hdzz/api/telegram/premium_bot.py
PREMIUM_SETTINGS = {
    "bot_token": "",
    "push_mode": "private_subscribers",
    "push_chat_id": "",
    "admin_ids": [],
    "admin_bootstrap_from_first_private_start": True,
    "buy_contact": "@sgty888",
    "business_contact": "@sgty888",
    "wallet_app_id": "27596",
    "wallet_secret": "",
    "wallet_coin": "USDT",
    "price_per_order": "8",
    "wallet_callback_url": "",
    "wallet_poll_enabled": True,
}

def main() -> int:
    return 0
```

```python
# app/telegram_premium_bot.py
from hdzz.api.telegram.premium_bot import *
if __name__ == "__main__":
    raise SystemExit(main())
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_telegram_premium_bootstrap.py::test_premium_bot_main_exists -v`
Expected: PASS

**Step 5: Add CLI smoke**

Run: `python app/telegram_premium_bot.py --help`
Expected: 正常输出参数。

---

### Task 2: 隐藏推送模板与按钮（购买 + 查看）

**Files:**
- Modify: `src/hdzz/api/telegram/premium_bot.py`
- Test: `tests/test_telegram_premium_templates.py`

**Step 1: Write the failing test**

```python
def test_teaser_message_is_masked():
    from hdzz.api.telegram.premium_bot import build_teaser_message
    txt = build_teaser_message({"live": True, "league": "西甲", "sides": "A vs B"})
    assert "⚡ 出货提醒｜滚球" in txt
    assert "仅限已开通权限账户查看完整内容" in txt
    assert "🏆 ***" in txt
    assert "⚽ *** vs ***" in txt
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_telegram_premium_templates.py::test_teaser_message_is_masked -v`
Expected: FAIL

**Step 3: Write minimal implementation**

固定隐藏模板：

```text
⚡ 出货提醒｜{滚球/早盘}

🏆 ***
⚽ *** vs ***

🔒 信号已生成
仅限已开通权限账户查看完整内容

👇 点击下方按钮验证权限。
```

按钮：
- `💳 点击购买`（callback: `pm|buy|{order_id}`）
- `🔓 查看订单`（callback: `pm|view|{order_id}`）

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_telegram_premium_templates.py::test_teaser_message_is_masked -v`
Expected: PASS

**Step 5: Add dedupe test**

Run: `pytest tests/test_telegram_premium_templates.py -v`
Expected: PASS（同一订单不重复推送）

---

### Task 3: 付费权限账本（按有效期放行）

**Files:**
- Create: `src/hdzz/api/telegram/premium_access.py`
- Test: `tests/test_telegram_premium_access.py`

**Step 1: Write the failing test**

```python
def test_access_active_and_expired(tmp_path):
    from hdzz.api.telegram.premium_access import ensure_access_doc, grant_days, is_active
    p = tmp_path / "premium_access.json"
    doc = ensure_access_doc(p)
    doc = grant_days(doc, "uid:1001", 7)
    ok, info = is_active(doc, "uid:1001")
    assert ok and not info["expired"]
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_telegram_premium_access.py::test_access_active_and_expired -v`
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# data/premium_access.json
# {"users": {"uid:1001": {"expire_at": 0, "source": "wallet"}}, "updated_at": "..."}

def ensure_access_doc(path): ...
def grant_days(doc, user_key, days, source="wallet"): ...
def revoke_user(doc, user_key): ...
def is_active(doc, user_key): ...
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_telegram_premium_access.py::test_access_active_and_expired -v`
Expected: PASS

**Step 5: Add revoke/expired tests**

Run: `pytest tests/test_telegram_premium_access.py -v`
Expected: PASS

---

### Task 4: 第三方钱包适配层（创建支付单 / 验签 / 查单）

**Files:**
- Create: `src/hdzz/api/telegram/premium_payment.py`
- Test: `tests/test_telegram_premium_payment.py`

**Step 1: Write the failing test**

```python
def test_build_create_order_payload():
    from hdzz.api.telegram.premium_payment import build_create_order_payload
    p = build_create_order_payload(
        app_id="27596",
        amount="100",
        order_no="VIP202602140001",
        callback_url="https://demo.com/callback",
    )
    assert p["order_no"] == "VIP202602140001"
    assert p["amount"] == "100"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_telegram_premium_payment.py::test_build_create_order_payload -v`
Expected: FAIL

**Step 3: Write minimal implementation**

```python
class WalletProvider:
    def create_order(...): ...  # 返回 pay_url / pay_bot_link / pay_no
    def verify_callback(...): ...
    def query_order(...): ...
```

设置常量（写代码里，不走 WUI）：
- `wallet_api_base`
- `wallet_app_id`
- `wallet_secret`
- `wallet_callback_url`
- `wallet_pay_timeout_minutes`

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_telegram_premium_payment.py::test_build_create_order_payload -v`
Expected: PASS

**Step 5: Add signature verify tests**

Run: `pytest tests/test_telegram_premium_payment.py -v`
Expected: PASS

---

### Task 5: “点击购买”跳转钱包（动态支付链接）

**Files:**
- Modify: `src/hdzz/api/telegram/premium_bot.py`
- Create: `data/premium_payments.json`（运行时生成）
- Test: `tests/test_telegram_premium_callbacks.py`

**Step 1: Write the failing test**

```python
def test_buy_click_creates_payment_and_returns_url_button():
    from hdzz.api.telegram.premium_bot import build_buy_reply
    msg, btn = build_buy_reply(order_id="234", user_id="1001")
    assert "请点击下方按钮支付" in msg
    assert btn.get("url")
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_telegram_premium_callbacks.py::test_buy_click_creates_payment_and_returns_url_button -v`
Expected: FAIL

**Step 3: Write minimal implementation**

购买流程：
1. 用户点击 `💳 点击购买`
2. 系统创建本地支付单（`pay_no`、`order_id`、`user_id`、`amount`、`status=pending`）
3. 调第三方 `create_order`
4. 返回按钮：`🧷 去 Okpay 付款`（URL 为第三方返回链接）

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_telegram_premium_callbacks.py::test_buy_click_creates_payment_and_returns_url_button -v`
Expected: PASS

**Step 5: Add idempotent test**

Run: `pytest tests/test_telegram_premium_callbacks.py -k "buy_click" -v`
Expected: PASS（防止重复创建多单）

---

### Task 6: 钱包回调自动开通权限（支付闭环核心）

**Files:**
- Modify: `src/hdzz/api/telegram/premium_bot.py`
- Modify: `src/hdzz/api/telegram/premium_payment.py`
- Modify: `src/hdzz/api/telegram/premium_access.py`
- Test: `tests/test_telegram_premium_callback_server.py`

**Step 1: Write the failing test**

```python
def test_callback_paid_grants_access(tmp_path):
    from hdzz.api.telegram.premium_bot import handle_payment_callback
    payload = {"order_no": "VIP001", "status": "paid", "sign": "ok"}
    ok, msg = handle_payment_callback(payload)
    assert ok is True
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_telegram_premium_callback_server.py::test_callback_paid_grants_access -v`
Expected: FAIL

**Step 3: Write minimal implementation**

回调要求：
- 路径：`/premium/payment/callback`
- 验签通过 + 订单状态 paid 才处理
- 幂等：重复回调不重复开通
- 成功后：
  - 更新 `premium_payments.json`
  - 调 `grant_days()` 开通权限
  - 给用户推送“已到账，权限已开通至 xx-xx xx:xx”

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_telegram_premium_callback_server.py::test_callback_paid_grants_access -v`
Expected: PASS

**Step 5: Add replay/invalid-sign tests**

Run: `pytest tests/test_telegram_premium_callback_server.py -v`
Expected: PASS

---

### Task 7: “查看订单”权限校验 + 完整内容私发

**Files:**
- Modify: `src/hdzz/api/telegram/premium_bot.py`
- Test: `tests/test_telegram_premium_view_access.py`

**Step 1: Write the failing test**

```python
def test_non_paid_user_cannot_view_full_order():
    from hdzz.api.telegram.premium_bot import can_view_order
    ok, reason = can_view_order(is_admin=False, paid_active=False)
    assert ok is False and reason == "paid_required"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_telegram_premium_view_access.py::test_non_paid_user_cannot_view_full_order -v`
Expected: FAIL

**Step 3: Write minimal implementation**

- 非付费：弹窗提示“未开通权限，请先购买”
- 付费：发送完整模板（优先私发用户）
- 私发失败：弹窗提示“请先私聊机器人发送 /start”

完整模板固定为你定义的：

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

Run: `pytest tests/test_telegram_premium_view_access.py::test_non_paid_user_cannot_view_full_order -v`
Expected: PASS

**Step 5: Add paid-pass test**

Run: `pytest tests/test_telegram_premium_view_access.py -v`
Expected: PASS

---

### Task 8: 管理员收款补单命令（避免回调丢失）

**Files:**
- Modify: `src/hdzz/api/telegram/premium_bot.py`
- Modify: `src/hdzz/api/telegram/premium_payment.py`
- Test: `tests/test_telegram_premium_admin.py`

**Step 1: Write the failing test**

```python
def test_admin_can_manual_confirm_payment():
    from hdzz.api.telegram.premium_bot import parse_admin_pay_confirm
    pay_no = parse_admin_pay_confirm("/pay_ok VIP001")
    assert pay_no == "VIP001"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_telegram_premium_admin.py::test_admin_can_manual_confirm_payment -v`
Expected: FAIL

**Step 3: Write minimal implementation**

新增管理员命令：
- `/pay_query <pay_no>`：主动查单
- `/pay_ok <pay_no>`：手动确认到账并开通
- `/pay_fail <pay_no>`：标记失败
- `/vip_info <user_id>`：看用户到期时间

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_telegram_premium_admin.py::test_admin_can_manual_confirm_payment -v`
Expected: PASS

**Step 5: Run admin suite**

Run: `pytest tests/test_telegram_premium_admin.py -v`
Expected: PASS

---

### Task 9: 启动编排 + 上线文档

**Files:**
- Modify: `app/app.py`
- Create: `docs/telegram-premium-wallet-ops.md`
- Test: `tests/test_telegram_premium_bootstrap.py`

**Step 1: Write the failing test**

```python
def test_workers_include_telegram_premium_bot():
    from app.app import workers
    ws = workers("127.0.0.1", 8080, 10, 5)
    assert any(w.name == "telegram_premium_bot" for w in ws)
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_telegram_premium_bootstrap.py::test_workers_include_telegram_premium_bot -v`
Expected: FAIL

**Step 3: Write minimal implementation**

- 在 `workers()` / `core_workers()` 增加进程：`telegram_premium_bot.py`
- 增加 `_WORKER_MARKERS`：`telegram_premium_bot.py`

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_telegram_premium_bootstrap.py::test_workers_include_telegram_premium_bot -v`
Expected: PASS

**Step 5: Add ops document**

文档必须包含：
- 如何填写钱包配置（app_id/secret/callback_url）
- 如何设置第三方后台“回调地址”
- 如何验证回调是否通
- 如何排查“用户付款了但没开通”

---

## 执行前剩余待补参数（最小集）

1. `admin_user_id`（管理员数字 ID；若启用自动引导，可由首次私聊 `/start` 自动写入）  
2. `wallet_callback_url`（可选；当前可先空着，用轮询查单兜底）  
3. 订阅用户策略（默认：谁私聊机器人并发送 `/start`，谁加入推送名单）

## 验收标准（Definition of Done）

1. 新订单推送隐藏模板，不泄露联赛与球队信息。  
2. 点击“购买”能跳转到第三方钱包支付页面/机器人。  
3. 支付成功后自动开通权限（无需人工二次操作）。  
4. 未付费点击“查看订单”被拦截，付费用户可看完整模板。  
5. 回调重复到达不会重复开通（幂等）。  
6. 即使回调失败，也可用 `/pay_query` + `/pay_ok` 补单。  
7. 全流程不依赖 WUI 设置页面。

## 风险与回滚

- 风险1：第三方文档不全导致对接字段错误。  
  - 处理：先做适配层 + 沙箱验证 + 手动补单兜底。
- 风险2：回调无法公网访问。  
  - 处理：先用查单轮询/管理员补单过渡。
- 风险3：支付链接泄露。  
  - 处理：支付单设置短时效，状态变更后立刻失效。
