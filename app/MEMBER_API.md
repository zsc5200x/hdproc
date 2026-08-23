# 会员端接口笔记（transform.php）

本文件用于记录你抓到的会员端关键接口，方便后续实现“弹药库（会员账号池）+ 自动跟单”。

> 注意：以下内容包含敏感字段（账号/密码/黑盒 blackbox/cookie）。不要对外泄露。

## 统一入口

- URL：`{base_url}/transform.php?ver={ver}`
- Method：`POST`
- Content-Type：`application/x-www-form-urlencoded`
- 登录态：主要靠 Cookie（例如 `login_<mid>`，以及站点可能还有其他会话 Cookie）

你当前样例：

- `base_url`: `https://205.201.0.120`
- `ver`: `2025-12-11-mtchg_125`

## 1) 登录（真正登录）

### Request（Payload/Form Data）

- `p=chk_login`
- `username` / `password`
- `app`（样例：`N`）
- `auto`（样例：`DAACDH`）
- `blackbox`（很长的一串）
- `userAgent`（样例是 base64 形式的 UA）
- `langx=zh-cn`

### Response（XML）

成功样例字段：

- `<status>200</status>`
- `<mid>39084258</mid>`
- `<uid>oacvdj8m39084258l189860b0</uid>`
- `<username>...</username>`

### Response Headers

会更新/下发 Cookie（样例）

- `Set-Cookie: login_39084258=...`
- `Set-Cookie: test=...`

## 2) 提交下注（FT）

### Request（Payload/Form Data）

- `p=FT_bet`
- `uid=...`（来自登录响应）
- `golds`（下注金额）
- `gid`（赛事 gid）
- `gtype=FT`
- `wtype`（玩法大类，例如 `OU` / `R`）
- `rtype`（细分玩法 + 方向，例如 `OUC/OUH/RH/RC`）
- `chose_team`（方向：`H` 或 `C`）
- `type`（方向：`H` 或 `C`，通常与 chose_team 一致）
- `ioratio`（赔率）
- `con`（内部盘口参数，示例见下）
- `ratio`（内部盘口参数，示例见下）
- `odd_f_type=H`
- `autoOdd=Y`
- `timestamp`（毫秒时间戳）
- `isRB`（是否滚球，样例：`N`）
- `imp`（样例：`N`）
- `ptype`（样例：空）
- `isYesterday`（样例：`N`）
- `f`（样例：`OU` 用 `1R`；`R` 用 `1M`）

### Response（XML）

成功样例字段：

- `<code>560</code>`（成功）
- `<ticket_id>...</ticket_id>`（单号）
- `<nowcredit>...</nowcredit>`（余额）
- `<gid>...</gid>`（赛事 gid）
- `<wtype>...</wtype>` / `<rtype>...</rtype>` / `<type>...</type>`
- `<ioratio>...</ioratio>` / `<gold>...</gold>`
- `<spread>...</spread>`（显示盘口线）

## 3) 映射样例（你已抓到）

### 3.1 大小盘 OU

- “大 2.5”
  - `wtype=OU`
  - `rtype=OUC`
  - `chose_team=C`
  - `con=3`
  - `ratio=100`
  - `f=1R`

- “小 2.5”
  - `wtype=OU`
  - `rtype=OUH`
  - `chose_team=H`
  - `con=-3`
  - `ratio=-100`
  - `f=1R`

- “大 2 / 2.5”（抓包验证）
  - `wtype=OU`
  - `rtype=OUC`
  - `chose_team=C`
  - `con=2`
  - `ratio=-50`
  - `f=1R`

### 3.2 让球 R

- 客队（样例）
  - `wtype=R`
  - `rtype=RC`
  - `chose_team=C`
  - `con=0`
  - `ratio=50`
  - `f=1M`

- 主队（样例）
  - `wtype=R`
  - `rtype=RH`
  - `chose_team=H`
  - `con=0`
  - `ratio=0`
  - `f=1M`

> 说明：`con/ratio` 与盘口线有关，后续会以“从会员端盘口接口获取当前可下单参数”为准，避免猜测导致下单失败。
