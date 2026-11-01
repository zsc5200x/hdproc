# 会员 Session / 登录状态有效性专项（只读证据报告）

分析仅读取历史 `follow_results`、`follow_trace`、`arsenal`、member watcher 日志、member ioratio/snapshot 指标和 browser profile；没有重新登录、没有发送请求、没有修改运行时数据。

## 统计范围

|类别|数量|JSONL 行号/范围|进入 FT_bet|session 状态|
|---|---:|---|---|---|
|FT_order_view 明确 `doubleLogin`|12|2116-2123、4930、5121、5124、5132|否|有失效迹象|
|`betError000` / XML 555|51|2728-2762、2808、2827、2863、2879、2884、2888、2890、2909、2910、2921、2944、2945、2947、2949、2951、2954|是（早期记录为 `是*`）|6 条有效迹象充分，其余证据不足|
|HTTP 502|8|2763、2764、2926、2955、5670、5684、5693、5694|是（早期记录为 `是*`）|有完整 telemetry 的 5 条有效迹象充分，其余证据不足|
|`UNKNOWN_RESULT` / ReadTimeout|3|2935、5661、5782|是|有效迹象充分|

`是*` 表示只有 `ft_bet_started_at`/结果记录证明已经进入执行阶段，旧记录没有完整的 `session.post` 元数据。2763、2764 的 HTTP 字段为空，但错误消息明确是 `502 Server Error`，因此仍计入 502；不能把结构化字段缺失当成“没有发请求”。

说明：`follow_results` 中 8 条 HTTP 502 的 `result_status` 也属于 `UNKNOWN_RESULT`；统计表按“502 传输结果”单列，故专项问题 3 的 UNKNOWN_RESULT 总量是 11 条（8 条 502 + 3 条 ReadTimeout）。

按用户要求的逐 attempt 完整表见 [member_session_attempts_detailed_2026-08-25.csv](<../docs/member_session_attempts_detailed_2026-08-25.csv>)；每行带有原始 `follow_results.jsonl` 行号，可直接回到历史记录复核。表中除订单结果字段外，还展开了 `execution_member`、member_id hash、UID hash、Cookie hash/count/names、ver hash、base URL、profile/arsenal 时间、keeper/member watcher 最后在线确认、member-data 成功、Snapshot/ioratio、FT_order_view、FT_bet 时间及间隔。历史记录没有持久化的字段统一写为“未记录”，不会用当前状态反推历史。

`member_session_attempts_2026-08-25.csv` 保留为原始精简版；详细表的 Cookie hash 是 Cookie 值 hash 集合的 SHA-256 前 16 位，Cookie 原值和 UID 原值均未写入报告。`arsenal_last_update` 标注“事后当前文件”时，只表示当前 `arsenal.json` 的文件 mtime，不能证明该字段在对应 attempt 当时的值。

详细表的时间口径：watcher 的 `online` 记录对应 keeper 的 `get_member_data` 登录态检查成功；`重新登录成功` 记录表示登录成功且代码路径随后尝试 `get_member_data`（该次响应没有单独落盘）。`snapshot_ioratio_last_success` 优先使用 `member_ioratio_fetched_at`，没有该字段时才使用同一 attempt 的 `FT_order_view` HTTP 200、code=501 完成时间；`last_valid_session_to_FT_bet_interval_s` 是该 watcher 在线确认到 `ft_bet_started_at` 的秒差。

## Session 指纹

Cookie 指纹是各 Cookie 值 hash 按名称排序后再次 SHA-256 的前 16 位；Cookie 原值从未写入报告。历史 `ft_bet_request` 的 UID 已脱敏，因而逐 attempt 的 UID hash 写为“未记录”；`ver_hash` 只有 502 错误 URL 暴露 ver 的记录能够计算，不能用当前 `arsenal.json` 补写。

|指纹|hash|Cookie count|Cookie names|
|---|---|---:|---|
|C0|未记录|未记录|未记录|
|C1|116fc517701d9cab|13|CookieChk、box4pwd_notshow_45143287、cu、cuipv6、fp_token_7c6a6574-f011-4c9a-abdd-9894a102ccef、io_token_7c6a6574-f011-4c9a-abdd-9894a102ccef、iorChgSw、ipv6、loadBB、login_45143287、myGameVer_45143287、protocolstr、test|
|C2|278f1ae6dc9d9ef6|13|C1 同名集合，另一组值 hash|
|C3|95c2f374b556bfa6|10|CookieChk、cu、cuipv6、iorChgSw、ipv6、loadBB、login_45143304、myGameVer_45143304、protocolstr、test|
|C4|973d3681bd44a1c9|11|C3 加 box4pwd_notshow_45143304|
|C5|231c57d1ea8bd0a3|10|CookieChk、cu、cuipv6、iorChgSw、ipv6、loadBB、login_45143312、myGameVer_45143312、protocolstr、test|
|C6|a58115b1d4dea9d8|13|CookieChk、box4pwd_notshow_45143319、cu、cuipv6、fp_token_7c6a6574-f011-4c9a-abdd-9894a102ccef、io_token_7c6a6574-f011-4c9a-abdd-9894a102ccef、iorChgSw、ipv6、loadBB、login_45143319、myGameVer_45143319、protocolstr、test|

有 telemetry 的记录中，Cookie 名称里的 `login_45143287/304/312/319` 与会员账号后缀一致，`execution_member` 与持久化 `member_id` 一致；进入执行器的记录说明提交前代码已拿到 `base_url/ver/uid/cookie`，但 UID/ver 的具体 hash 没有落盘。历史记录只有提交时单点 Cookie 快照，没有提交前后的双快照；因此只能说“未发现”提交瞬间切换证据，不能排除未留痕的更早变化。

逐 attempt 对照 watcher 日志：62 条有 `ft_bet_started_at` 的 attempt，其提交前最后一条 keeper/member watcher 事件全部是 `online`；12 条明确 `doubleLogin` 的 attempt 都没有 `ft_bet_started_at`。这支持“已被 watcher 明确标记失效的会员没有穿透到 FT_bet”，但不等于旧记录拥有完整 Cookie/UID/ver 快照。

重点检查结论：

- Cookie：18 条有提交时 Cookie 快照且 count/names/hash 可核对，56 条历史行未记录，不能把缺失留痕当成缺失 Cookie。
- UID/ver：进入执行器的 62 条说明四项输入通过了代码非空门槛；UID 原值已脱敏，ver 只有 502 错误 URL 的 8 条可算 hash，无法从历史文件证明旧值覆盖或逐值同 member 绑定。
- member 绑定：有快照的 `login_<账号后缀>` 与账号标签一致，`execution_member` 与 `member_id` 一致；未发现错配，但没有双快照不能证明提交前没有更早切换。
- profile/keeper/watcher：profile 目录均在对应 attempt 前已存在；进入 FT_bet 的 62 条最近 watcher 事件不是 logout、unauthorized、login expired、cookie invalid、session invalid、uid missing 或 member-data failed，明确 `doubleLogin` 的 12 条均在 FT_order_view 阶段被拦截。
- 读取与下注权限：6 条完整 betError000 记录出现近期 FT_order_view/ioratio 成功后再收到 HTTP 200/XML 555，属于“读取接口可用但业务层拒单”的证据，不足以判定 Session 失效。

## 逐类 attempt 证据

|行号|member / execution_member|提交前证据|HTTP/XML|结论|
|---|---|---|---|---|
|2116-2123、4930、5121、5124、5132|summer 账号；见各行 member_id|FT_order_view 返回 `error/doubleLogin`|HTTP 200 / error|明确登录态异常，未进入 FT_bet；程序在只读读取阶段拦截。|
|2728-2762|aabbcc775533、aabbcc995511、aabbcc885522；execution_member 字段未留存，使用 member_id 作为关联键|旧版本没有 Cookie、UID、ver、session 对象或 member-data 成功时间；watcher 只留下此前登录确认和网络型重登失败|结构化字段缺失；结果为 betError000|证据不足。不能把“没有记录”解释成 Cookie 缺失，也不能证明会员已失效。|
|2763、2764|7735…、b9a…|同上，且提交前 session telemetry 缺失|错误消息为 HTTP 502|请求/网络失败；不能用 502 证明 Session 失效。|
|2808、2827、2863、2879、2884、2888、2890、2909、2910、2921|7e16…、b9a…|`member_ioratio_fetched_at` 在 FT_bet 前约 0.07-0.41 秒；Cookie 快照未保存|HTTP 200 / XML 555|业务层拒单，但由于 Cookie/UID 快照缺失，整体标为证据不足。|
|2935、5661、5782|7e16…、821e…|Cookie 快照存在；request 已构造出被脱敏的 UID/ver 字段；近期 ioratio 或 FT_order_view 读取成功|ReadTimeout，无 XML|有效迹象充分；请求已经发出，结果未知，不是 Session 失效结论。|
|2944、2945、2947、2949、2951、2954|7e16…、b9a…|Cookie 10/13 个；request 已构造出被脱敏的 UID/ver 字段；member ioratio 在提交前 43-379ms 更新；无 logout/unauthorized|HTTP 200 / XML 555|会话表面有效，但平台业务层拒单，无法证明 Session 失效。|
|2926、2955、5670、5684、5693、5694|7e16…、821e…、0cae…|其中 5670/5684/5693/5694 有 Cookie + FT_order_view 200/code=501 + watcher/ioratio 成功；2926 telemetry 缺失，2955 缺 FT_order_view 字段|HTTP 502|请求已发出；502 只能说明上游/网络失败。|

## 时间线与 profile

- 7e16…：watcher 在 2026-08-22 12:10:27、2026-08-23 12:02:08 记录“重新登录成功”；2935/2944 等 attempt 的 Cookie 指纹稳定，没有提交瞬间切换证据。
- b9a…：watcher 在 2026-08-22 13:20:32 记录“重新登录成功”；2945/2947/2949/2951/2954 使用同一 Cookie 指纹。
- 821e…：profile 在 2026-08-24 07:04:58 创建，07:05:45 watcher 记录“重新登录成功”；5684 在 07:28:16、5782 在 11:32:57 发生，提交前分别有 FT_order_view 501 成功。
- 0cae…：profile 在 2026-08-24 07:07:31 创建，07:08:16 watcher 记录“重新登录成功”；5694 在 08:46:19 发生，Cookie C6 的 `login_45143319` 与账号后缀一致。
- 相关 profile 目录均在失败前已存在；没有证据表明 FT_bet 开始时切换到了另一个 profile。详细 CSV 的 profile birth/mtime 是当前文件系统可见时间，不能当作历史 attempt 的精确登录时间；当前 `arsenal.json` mtime 同样是事后状态，不能回溯每条历史 attempt 当时的 arsenal 字段。

## 代码侧拦截边界

- [engine.py:4678](/Volumes/zsc5200xx/hdproc/src/hdzz/core/follower/engine.py:4678) 的 `_member_eligible_for_stake` 要求会员 `status=online`、无网络冷却，并且 `base_url/ver/uid/cookie` 四项非空；[engine.py:4826](/Volumes/zsc5200xx/hdproc/src/hdzz/core/follower/engine.py:4826) 的 `ensure_member_session` 在缺少 `ver/uid/cookie` 时把会员标成 offline，交给 watcher 重登。
- [snapshot_direct.py:1087](/Volumes/zsc5200xx/hdproc/src/hdzz/core/follower/snapshot_direct.py:1087) 的 `MemberExecutor.submit` 在调用 FT_bet 的 `session.post()` 前再次检查四项字段；[snapshot_direct.py:944](/Volumes/zsc5200xx/hdproc/src/hdzz/core/follower/snapshot_direct.py:944) 将 `FT_order_view` 非 501（包括 `error/doubleLogin`）标成 context error，[engine.py:5923](/Volumes/zsc5200xx/hdproc/src/hdzz/core/follower/engine.py:5923) 随即关闭 session 并返回，不进入 FT_bet。[snapshot_direct.py:1215](/Volumes/zsc5200xx/hdproc/src/hdzz/core/follower/snapshot_direct.py:1215) 是 FT_bet 响应本身返回 doubleLogin 时的解析分支。
- [engine.py:6124](/Volumes/zsc5200xx/hdproc/src/hdzz/core/follower/engine.py:6124) 负责把执行阶段 session telemetry 落入结果记录。本批早期 JSONL 行没有这些字段，属于历史留痕缺口，不应解释成当时字段为空。

## 对专项问题的明确回答

1. **betError000 是否拿着“已失效会员”提交？** 不能整体这样说。6 条带完整 session telemetry 的 betError000 有 Cookie 快照、`FT_order_view`/ioratio 近期成功，并且已进入执行器（代码在进入执行器前要求 `base_url/ver/uid/cookie` 非空）；UID/ver 的具体 hash 因脱敏未落盘，不能声称已逐值核对。正确结论是“会话表面有效，但平台业务层拒单，无法证明 Session 失效”。早期 35 条只有结果没有提交前 session 快照，结论是证据不足。
2. **502 attempt 提交前是否已失效？** 没有证据证明。8 条均有 FT_bet 执行阶段证据；其中 5 条还有 Cookie、FT_order_view 200/501 和近期 watcher/ioratio 成功，另外 3 条只有 `ft_bet_started_at`/错误消息。502 不能证明 Session 失效。
3. **UNKNOWN_RESULT 是会话失效还是请求发出后结果未知？** 是请求已发出后的结果未知：记录含 `ft_bet_started_at`、`real_submission=snapshot_direct`，并出现 502 或 ReadTimeout。不能把 UNKNOWN_RESULT 归因于 Session 失效。
4. **是否存在会员已失效但程序仍用于 FT_bet 的 bug？** 当前没有证据证明。明确 doubleLogin 的 attempt 在 FT_order_view 就被拦截，没有进入 FT_bet；有完整 telemetry 的 FT_bet attempt 又显示表面 session 有效。
5. **如果存在，哪个状态字段/函数没有拦住？** 本批没有可证实的穿透，因此没有可归责的字段或函数。现有边界是 betError000 的业务拒单不能被错误升级为 Session 失效。
6. **最终结论：当前没有证据证明会员失效后仍被用于 FT_bet。**
