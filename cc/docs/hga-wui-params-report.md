# HGA WUI 参数来源说明（人话版）

> 目标：告诉你**页面上看到的每个“参数/盘口/水位”**，分别是从哪个接口查到的、字段叫什么、应该怎么理解。  
> 说明：HGA 的很多功能都走同一个入口 `transform.php`，靠表单参数 `p=xxx` 区分不同接口。

---

## 0) 总入口：`transform.php`

- **请求方式**：`POST https://hga027.com/transform.php?ver=...`
- **鉴权关键参数**（登录后才有）：
  - `uid`：会话 ID（登录成功后接口返回）
  - `ver`：版本号（页面带着走）
  - `langx`：语言（例：`zh-cn`）
  - `ltype`：展示/盘口类型（例：`4`）
  - `odd_f_type`：赔率格式（例：`H`）

你在 WUI 里看到的内容，主要来自下面 3 个 `p=...` 接口：

1) `p=get_game_list`：比赛列表（带主盘的一部分盘口/水位）
2) `p=get_game_more`：更多盘（拿到同场比赛的多个 gid 与更多盘口/水位）
3) `p=FT_order_view`：下注前“校验水位”（更接近原站最终显示，因此更准但更慢）

---

## 0.1) 三种模式：滚球 / 今日 / 早盘（字段为什么不一样）

你现在的 WUI 支持 3 种列表模式（页面按钮可切换）：

- **滚球**：进行中的比赛
- **今日**：今天开赛的比赛（未开始/已开赛都可能出现）
- **早盘**：未来比赛（一般是“更早的赛程/盘”）

它们的核心差异不是“能不能查水位”，而是：
> **同一个玩法，在不同模式下字段名不同。**

| 玩法 | 滚球（live）字段 | 今日/早盘（today/early）字段 |
|---|---|---|
| 让球 | `sw_RE` / `ratio_re` / `ior_REH` / `ior_REC` | `sw_R` / `ratio` / `ior_RH` / `ior_RC` |
| 大小 | `sw_ROU` / `ratio_rouo` / `ior_ROUH` / `ior_ROUC` | `sw_OU` / `ratio_o` / `ior_OUH` / `ior_OUC` |
| 独赢 | `sw_RM` / `ior_RMH` / `ior_RMN` / `ior_RMC` | `sw_M` / `ior_MH` / `ior_MN` / `ior_MC` |

> “水位大/小”映射规律保持一致：**大 = `…C`，小 = `…H`**（滚球是 `ROUC/ROUH`，今日/早盘是 `OUC/OUH`）。

---

## 1) “当前快照”这些参数从哪里来？

WUI 页面上的“当前快照”主要是 **把 `get_game_list` 和 `get_game_more` 的信息拼在一起**，优先用 `get_game_more`（因为它是当前监控比赛的实时大 XML）。

| WUI 显示（中文） | 主要来源接口 | 字段位置（响应里长什么样） | 人话解释 |
|---|---|---|---|
| 你输入的GID | 你手动输入 | URL 参数 `gid=` | 你要监控的比赛 ID |
| 主盘GID | `get_game_more` | `<game master="Y" ...><gid>...</gid>` | 同一场比赛里“主盘”的那个 gid |
| 联赛 | `get_game_more` | `<league>西班牙甲组联赛</league>` | 联赛名称 |
| 对阵 | `get_game_more` | `<team_h>主队</team_h>` / `<team_c>客队</team_c>` | 主队 vs 客队 |
| 比分 | `get_game_more` | `<score_h>1</score_h>` / `<score_c>0</score_c>` | 当前比分 |
| 滚球时间 | `get_game_more` | `<re_time>1H^29:21</re_time>` | 进行到上半场/下半场多少分钟 |
| 服务器时间 | `get_game_more` | `<systime>2026-02-07 16:32:09</systime>` | HGA 返回的时间戳 |
| 联赛ID(LID) | `get_game_list` / `get_game_more` | `get_game_list` 的 JSON 里是 `LID`；`get_game_more` 里是 `<lid>` | 联赛编号（`get_game_more` 请求必带） |
| 事件ID(ECID) | `get_game_list` / `get_game_more` | `get_game_list` 的 JSON 里是 `ECID`；`get_game_more` 里结尾有 `<ecid>` | 比赛事件编号（`get_game_more` 请求必带） |
| 数据ID(MT_ID) | `get_game_list` | `get_game_list` JSON 里 `MT_ID` | 主要给比分/统计那套接口用（不是盘口核心） |
| 同场比赛GID列表 | `get_game_more` | XML 结尾 `<FT_gid><gid id="..." master="Y" />...</FT_gid>` | 一场比赛可能对应多个 gid（主盘 + 多个子盘） |

---

## 2) 盘口/水位：三大玩法分别从哪里来？

你要求只看 3 个玩法：

1) **让球（RE）**
2) **大小（ROU）**
3) **独赢（RM）**

这三块的“盘口 + 水位”**基础数据来源**都是：`p=get_game_more` 的 XML 里每个 `<game>` 节点的字段。

### 2.1 让球（RE）

**在 `get_game_more` 里对应字段：**
- 是否开盘：`<sw_RE>Y</sw_RE>`（Y 才代表有）
- 盘口：`<ratio_re>0 / 0.5</ratio_re>`（例：平/半）
- 水位（两边）：
  - `<ior_REH>...</ior_REH>`：主队这一边水位
  - `<ior_REC>...</ior_REC>`：客队这一边水位
- 强弱方（决定正负号怎么显示）：`<strong>H</strong>` 或 `<strong>C</strong>`

**今日/早盘同玩法字段名（等价关系）：**
- 是否开盘：`<sw_R>Y</sw_R>`
- 盘口：`<ratio>0 / 0.5</ratio>`
- 水位：`<ior_RH>`（主队） / `<ior_RC>`（客队）
- 强弱方：同样看 `<strong>H/C</strong>`

**WUI 怎么把它显示成你看到的“±盘口”：**
- 如果 `<strong>H</strong>`：表示主队让球（主队盘口显示负号），客队受让（显示正号）
- 如果 `<strong>C</strong>`：表示客队让球（客队盘口显示负号），主队受让（显示正号）

> 例：`ratio_re = 0/0.5` 且 `strong=C`  
> - 主队会显示 `+0/0.5`（受让）  
> - 客队会显示 `-0/0.5`（让球）

---

### 2.2 大小（ROU）

**在 `get_game_more` 里对应字段：**
- 是否开盘：`<sw_ROU>Y</sw_ROU>`
- 盘口（总进球线）：`<ratio_rouo>4.5 / 5</ratio_rouo>`（有时 `ratio_rouu` 也会一起出现，通常是同一个值）
- 水位（两边）：
  - `<ior_ROUC>...</ior_ROUC>`：**大**（Over）
  - `<ior_ROUH>...</ior_ROUH>`：**小**（Under）

**今日/早盘同玩法字段名（等价关系）：**
- 是否开盘：`<sw_OU>Y</sw_OU>`
- 盘口：`<ratio_o>2.5</ratio_o>` / `<ratio_u>2.5</ratio_u>`（通常同一个值）
- 水位：`<ior_OUC>`=大，`<ior_OUH>`=小

> 这个“大/小”对应关系，是我对照你原站截图实测确认的：  
> **大 = `IOR_ROUC`，小 = `IOR_ROUH`**（之前写反过一次，所以你当时会看到对不上）

---

### 2.3 独赢（RM）

**在 `get_game_more` 里对应字段：**
- 是否开盘：`<sw_RM>Y</sw_RM>`
- 三项水位：
  - `<ior_RMH>...</ior_RMH>`：主胜
  - `<ior_RMN>...</ior_RMN>`：和局
  - `<ior_RMC>...</ior_RMC>`：客胜

**今日/早盘同玩法字段名（等价关系）：**
- 是否开盘：`<sw_M>Y</sw_M>`
- 三项水位：`<ior_MH>`（主胜） / `<ior_MN>`（和局） / `<ior_MC>`（客胜）

---

## 3) 为什么“校验水位(更准更慢)”会更准？

### 3.1 它用的是哪个接口？

当你勾选 WUI 的“校验水位(更准更慢)”时，WUI 会额外调用：

- `p=FT_order_view`

这是原站在你**准备下注/点选某个盘口**时会调用的接口，用来确认：
> “此刻最新的盘口与水位是多少？还能不能下？限额是多少？”

补充一个非常关键的点（也是“今日/早盘一开始查不到”的根源）：

- **滚球**校验用的 `wtype` 一般是：`RE`（让球）、`ROU`（大小）
- **今日/早盘**校验用的 `wtype` 一般是：`R`（让球）、`OU`（大小）（独赢是 `M`）

### 3.2 `FT_order_view` 返回了什么？

你会在响应 XML 里看到类似字段：
- `<spread>0 / 0.5</spread>`：盘口（让球/大小的线）
- `<ioratio>0.98</ioratio>`：这一边的“最新水位”
- `<strong>H</strong>` / `<strong>C</strong>`：强弱方
- `<fast_check>REH</fast_check>`：代表当前校验的是哪个选项

### 3.3 为什么会更慢？

因为 `FT_order_view` **一次只能校验一个选项的一边**，而一场比赛会有多条盘口：
- 让球每条盘至少要校验 `H` 和 `C` 两边
- 大小每条盘也要校验 “大/小” 两边

所以每次刷新就会多出一批请求，准确性更高，但耗时自然更大。

> WUI 已做了提速优化：并发 + 1.8s 短缓存（同一盘口短时间不重复校验）。

---

## 4) 你截图里那种“盘口拿不全”一般是什么原因？

最常见原因是：**同一条 `ratio_re`（比如 0/0.5）会同时出现两种“正负方向”**（取决于 `<strong>`），例如：
- `阿鲁卡 -0/0.5`  vs `甘马雷斯 +0/0.5`
- `阿鲁卡 +0/0.5`  vs `甘马雷斯 -0/0.5`

如果程序只按 `ratio_re` 去重，会把其中一条干掉，就会表现成“原站有 4 行，我这里只有 3 行”。

WUI 现在已经改成按“最终显示出来的正负盘口”去重，尽量把两条都保留下来。

---

## 5) 你要自己去“查接口”的最短路径（按操作顺序）

1) 先登录拿 `uid`：`p=chk_login`
2) 列比赛（拿 `gid/ecid/lid/mt_id`）：`p=get_game_list`
3) 监控某场（拿更多盘 + FT_gid）：`p=get_game_more`（要带 `lid/ecid`）
4) 需要“最准水位”时，再用：`p=FT_order_view`（对每条盘逐个校验）
