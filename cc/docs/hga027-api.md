# HGA027 接口说明（抓包版｜小白能看懂）

> 仅用于你自己账号的授权测试，请遵守目标站点条款。  
> **抓包文件里包含敏感信息**（`uid/cookie/账号/会话`，甚至会出现“密码安全字串”），不要外传，不要发到群里。

---

## 0) 你这次抓到了什么（结论）

你现在最有用的一份抓包在：

- `captures/20260208-030747/`（✅带“响应内容”，可以直接分析盘口/水位字段）

里面最关键的文件是：

- `captures/20260208-030747/network-summary.json`：请求摘要（表单参数已脱敏 `password/blackbox`）
- `captures/20260208-030747/responses-index.json`：告诉你“每个响应保存在哪个文件”
- `captures/20260208-030747/responses/`：真正的响应内容（XML/JSON）

你要的“比赛盘口 + 水位实时刷新”，核心就是这 3 类接口：

1) 登录：`p=chk_login`（拿到 `uid` 会话）
2) 比赛列表（带主盘）：`p=get_game_list`
3) 更多盘/附加盘：`p=get_game_more`

可选（更像下注前确认，但能拿“此刻最新水位”）：

- `p=FT_order_view`

---

## 1) 最重要的规律：很多接口都走同一个入口

你抓到的绝大多数“业务接口”都是：

- **同一个入口**：`POST https://hga027.com/transform.php?ver=...`
- **靠表单参数 `p=xxx` 区分功能**

你可以把它理解成：
> “同一个电话（transform.php），按不同分机号（p=xxx）办不同业务。”

---

## 2) 必懂 ID：`gid / ecid / lid / MT_ID`

后面做脚本时，最容易搞混的就是各种 ID。你这次抓包里至少有 4 个：

- `gid`：**下注系统的比赛 ID**（你想在 UI 里输入的就是它）
- `ecid`：**事件/比赛分组 ID**（`get_game_more` 需要它）
- `lid`：**联赛 ID**（`get_game_more` 需要它）
- `MT_ID`：**比赛数据/比分统计的 ID**（给 `gismo/...` 那套接口用）

你可以记一句话：
> **显示盘口/水位：看 `gid/ecid/lid`；显示比分/时间：看 `MT_ID`。**

---

## 3) 登录：`p=chk_login`

### 3.1 请求长什么样

- URL：`POST https://hga027.com/transform.php?ver=<版本号>`
- 表单参数里：`p=chk_login`
- 你这次抓到过的字段（不一定齐全）：
  - `username`：账号
  - `password`：密码（抓包摘要里会被我脚本脱敏成 `***`）
  - `blackbox`：风控指纹（页面生成的，不能自己乱写）
  - `userAgent`：浏览器 UA
  - `langx`：语言（例：`zh-cn`）
  - `ver`：版本号

### 3.2 响应里最重要的是什么

抓包证据文件：`captures/20260208-030747/responses/transform--p-chk_login--r32.txt`

响应（XML）里你最该关注：

- `<status>`：`200` 表示登录成功
- `<uid>`：**会话 ID（后面所有接口都要带）**
- `<mid>`：会员 ID
- `<odd_f>` / `<ltype>`：赔率格式/展示模式

> 重要：这个响应里会出现非常敏感字段（例如 `uid`、`passwd_safe`），你只在自己电脑上用，千万别外发。

---

## 4) 比赛列表（带主盘）：`p=get_game_list`

### 4.1 用途（人话）

拿到“滚球足球”的比赛列表，并且**每场比赛就自带一份主盘盘口/水位**。

### 4.2 请求参数（你这次抓到过的）

抓包证据：`captures/20260208-030747/network-summary.json` 里 `p=get_game_list`

- `uid`：登录后拿到的会话 ID
- `gtype=ft`：足球
- `showtype=live`：滚球
- `rtype=rb`：滚球类型
- `ltype=4`：显示模式
- `sorttype=L`：排序
- `ts/chgSortTS`：时间戳（页面刷新会变）

### 4.3 响应怎么读（重点）

抓包证据文件：`captures/20260208-030747/responses/transform--p-get_game_list--r122.txt`

这个响应是 XML，但它里面有一个对我们超级友好的东西：

- 在 XML 结尾有：`<original>{...JSON...}</original>`
- 这个 JSON 直接就是“比赛列表结构化数据”，**比解析 XML 简单很多**

### 4.4 你要的“主盘盘口/水位”字段（最常用）

下面这些字段在你抓包里都有真实值（我用其中一场举例说明）：

- 基本信息
  - `GID`：比赛 `gid`
  - `ECID`：比赛 `ecid`（后面给 `get_game_more` 用）
  - `LID`：联赛 `lid`
  - `LEAGUE`：联赛名称
  - `TEAM_H / TEAM_C`：主队 / 客队
  - `SCORE_H / SCORE_C`：比分
  - `RETIMESET`：滚球时间（例：`2H^79:40`）
  - `MT_ID`：比赛数据 ID（给 `gismo/...` 用）

- 盘口/水位（主盘）
  - **让球（RE）**
    - `RATIO_RE`：盘口（例：`0`）
    - `IOR_REH`：主队水位
    - `IOR_REC`：客队水位
  - **大小（ROU）**
    - `RATIO_ROUO / RATIO_ROUU`：大小盘盘口（例：`2.5`）
    - `IOR_ROUH / IOR_ROUC`：两边水位（✅实测：**大=ROUC，小=ROUH**）
  - **独赢（RM，三项）**
    - `IOR_RMH`：主胜水位
    - `IOR_RMN`：和局水位
    - `IOR_RMC`：客胜水位

- “是否还有更多盘”
  - `MORE`：>0 表示还有很多附加盘（建议继续调 `get_game_more`）

### 4.5 一个“人话版”示例（从抓包 JSON 里摘出来的）

（示例来自你抓包里某场比赛的 `<original>` JSON，我只保留常用字段）

```json
{
  "GID": "8513893",
  "ECID": "10466285",
  "LID": "100028",
  "LEAGUE": "德国甲组联赛",
  "TEAM_H": "门兴格拉德巴赫",
  "TEAM_C": "勒沃库森",
  "RETIMESET": "2H^79:40",
  "SCORE_H": "1",
  "SCORE_C": "1",
  "RATIO_RE": "0",
  "IOR_REH": "1.200",
  "IOR_REC": "0.710",
  "RATIO_ROUO": "2.5",
  "IOR_ROUH": "0.710",
  "IOR_ROUC": "1.190",
  "IOR_RMH": "5.70",
  "IOR_RMN": "1.54",
  "IOR_RMC": "4.25",
  "MORE": 31,
  "MT_ID": "61513974"
}
```

---

## 5) 更多盘/附加盘：`p=get_game_more`

### 5.1 用途（人话）

当你点进某场比赛“更多盘/附加盘”页面时，站点会调用它来拿更全的盘口/水位。

### 5.2 请求参数（你这次抓到过的）

抓包证据：`captures/20260208-030747/network-summary.json` 里 `p=get_game_more`

- `uid`：会话 ID
- `gtype=ft`、`showtype=live`、`isRB=Y`：滚球足球
- `lid`：联赛 ID（例：`100028`）
- `ecid`：事件 ID（例：`10466285`）
- `filter=Main`：你这次抓到的是 Main（如果你想抓“更多更多玩法”，后续可能要在页面里切到别的筛选触发不同值）
- `ts`：时间戳（刷新会变）

### 5.3 响应怎么读（重点）

抓包证据文件：`captures/20260208-030747/responses/transform--p-get_game_more--r184.txt`

这是一个超级长的 XML。你不用被吓到，你只要记两个规律：

1) `sw_XXX`：表示这个玩法/盘口**开没开**（`Y`=有，`N`=没有）
2) `ratio_xxx` + `ior_XXX*`：就是**盘口 + 水位**

举例（从你抓包里提取的关键字段）：

```json
{
  "gid": "8513893",
  "league": "德国甲组联赛",
  "team_h": "门兴格拉德巴赫",
  "team_c": "勒沃库森",
  "sw_RE": "Y",
  "ratio_re": "0",
  "ior_REH": "1.23",
  "ior_REC": "0.68",
  "sw_ROU": "Y",
  "ratio_rouo": "2.5",
  "ratio_rouu": "2.5",
  "ior_ROUH": "0.74",
  "ior_ROUC": "1.16",
  "sw_RM": "Y",
  "ior_RMH": "6.100",
  "ior_RMN": "1.500",
  "ior_RMC": "4.500"
}
```

### 5.4 一个很关键但容易忽略的东西：`FT_gid`

在 `get_game_more` 的响应结尾，你抓到了这一段：

```xml
<FT_gid>
  <gid id="8513893" master="Y" />
  <gid id="8513895" master="N" />
  <gid id="8513897" master="N" />
  <gid id="8513899" master="N" />
</FT_gid>
```

这说明：

- **同一场比赛可能会对应多个 `gid`**（1 个 master + 多个子 gid）
- 页面在你点不同盘口/附加盘的时候，可能会切换用不同的 `gid`

> 后续我们要做“输入 gid → 拉全盘口”时，会优先用 `gid` 在 `get_game_list` 里反查到 `ecid/lid`，再用 `get_game_more` 的 `FT_gid` 把同场比赛的 gid 都收集齐，这样才能尽量拿全。

---

## 6) 单个选项“最新水位确认”：`p=FT_order_view`（可选）

### 6.1 用途（人话）

你在页面点某个盘口准备下注时，它会先调这个接口确认“此刻最新水位/盘口 + 限额”。

**你如果只是展示全部盘口**，优先用 `get_game_list/get_game_more` 就够了；  
**你如果想做“点某个盘口 → 立即显示最新水位”**，再用它。

### 6.2 请求参数（你抓到过的）

- `gid`：比赛 gid（注意：你这次抓包里就出现了同一场的多个 gid）
- `wtype`：玩法（你抓到过：`RE`=让球、`ROU`=大小）
- `chose_team`：选择哪边（你抓到过：`H/C`）
- `odd_f_type`：赔率格式（例：`H`）
- 以及：`uid/ver/langx`

### 6.3 响应里你最关心的

抓包证据：`captures/20260208-030747/responses/transform--p-FT_order_view--gid-8513895--r220.txt`

- `<spread>`：盘口（例：`0 / 0.5`）
- `<ioratio>`：水位（例：`0.41`）
- `<gold_gmin>/<gold_gmax>`：最小/最大金额（只在你做下注时才重要）
- `<fast_check>`：这次选中的项（例：`REH`、`ROUC`）

> 你抓包里这个接口的 `<code>` 恒为 `501`，看起来是正常现象，不要当成报错。

### 6.4 ⚠️ 下注提交：`p=FT_bet`（严禁调用）

你之前的老抓包里出现过 `p=FT_bet`（证据：`captures/20260208-024715/network-summary.json`）。  
这个接口是**真正提交下注**用的：

- 调用它可能会发生真实下注/扣款
- 我们做“展示盘口/水位”的功能**完全不需要它**

后续我写脚本/服务时，会明确把它列入“黑名单”，避免误触发。

---

## 7) 比分/时间/统计（可选）：`gismo/...`

你抓到了这类接口（示例用的是 `MT_ID=61513974`）：

- `GET .../gismo/match_info/<MT_ID>`
- `GET .../gismo/match_details/<MT_ID>`
- `GET .../gismo/match_detailsextended/<MT_ID>`
- `GET .../gismo/match_timeline/<MT_ID>`
- `GET .../gismo/match_timelinedelta/<MT_ID>`（页面刷新最频繁）

注意：抓包里 URL 上带了一个很长的 `T=...` 参数（像 CDN token）。  
后续做脚本时要先验证：**不带 `T` 能不能请求成功**。如果不行，就只能继续通过浏览器方式拿 token。

---

## 8) 你要的“输入 gid → 实时刷新盘口/水位”最小闭环（最新版）

你真正要做的逻辑（用人话说）是：

1) 登录（`chk_login`）拿到 `uid` + cookie
2) 拉比赛列表（`get_game_list`）找到你输入的 `gid`，并拿到它的 `ecid/lid/MT_ID`
3) 拉更多盘（`get_game_more`）拿到附加盘的盘口/水位（并顺手收集 `FT_gid`，避免漏盘）
4) 前端每隔几秒刷新一次（重复第 2/3 步，或者只重复第 3 步）

下一步我们要做的就是：

- 写一个“解析器”：把 `get_game_list/get_game_more` 的 XML（或 `<original>` JSON）变成**你自己 UI 能直接展示的 JSON**  
- 然后再做一个最小后台：`/login`、`/odds?gid=xxx`、`/stream?gid=xxx`（SSE/WebSocket 推送实时刷新）
