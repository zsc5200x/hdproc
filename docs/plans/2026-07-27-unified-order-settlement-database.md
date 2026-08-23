# 订单、跟单结果与结算统一数据库实施计划

## 目标

将 `orders/*.jsonl`、`data/follow_results.jsonl` 和
`data/settlements/*.jsonl` 统一导入 `data/hdzz.db`，在一个 SQLite
数据库内按业务关系保存为多张表：

```text
orders 1 -- N follow_results 1 -- 0..1 settlements
```

旧 JSONL 在兼容观察期继续保留，不删除、不覆盖，作为审计和回滚来源。

## 设计原则

- SQLite 是新的结构化事实源，旧 JSONL 是兼容输出和迁移原始凭据。
- 不建立包含所有字段的宽表，避免订单、跟单和结算生命周期互相污染。
- 每条记录保留 `raw_json`，未知字段不因结构化迁移丢失。
- 历史导入使用来源内容 SHA-256 作为幂等依据，可重复执行。
- 金额以规范化十进制字符串存储，Python 使用 `Decimal` 计算，不使用
  SQLite `REAL` 做财务聚合。
- 开启 `foreign_keys`、WAL 和 `busy_timeout`，所有批量导入使用短事务。

## Schema

### `schema_migrations`

记录结构版本和应用时间，所有升级脚本必须可重复判断当前版本。

### `orders`

主要字段：

- `id INTEGER PRIMARY KEY`
- `target TEXT NOT NULL`
- `order_id TEXT NOT NULL`
- `account`、`sport`、`league`、`bet_type`、`line`、`odds_text`
- `stake_text`、`ts`、`bet_ts`
- `source_hash TEXT NOT NULL`
- `raw_json TEXT NOT NULL`
- `created_at`、`updated_at`

约束和索引：

- `UNIQUE(target, order_id)`
- `UNIQUE(source_hash)`
- 按 `ts`、`bet_ts` 和 `order_id` 建查询索引

同一业务订单出现更新版本时，以较新的 `ts` 更新结构化字段和
`raw_json`；内容完全相同的来源行直接跳过。

### `follow_results`

主要字段：

- `id INTEGER PRIMARY KEY`
- `order_fk INTEGER`，可空外键到 `orders.id`
- `target TEXT NOT NULL`
- `order_id TEXT NOT NULL`
- `member_id`、`member`、`ticket_id`
- `ok`、`skipped`、`skip_reason`、`message`
- `stake_text`、`source_stake_text`、`ts_ms`
- `source_hash TEXT NOT NULL UNIQUE`
- `raw_json TEXT NOT NULL`

说明：一次后台订单可能产生多次尝试或多名会员跟单，因此不能把
`order_id` 设为唯一键。迁移时先按 `(target, order_id)` 连接订单；找不到
订单时允许 `order_fk` 为空，并写入迁移问题报告，避免丢弃孤立结果。

### `settlements`

主要字段：

- `ticket_id TEXT PRIMARY KEY`
- `follow_result_fk INTEGER`，可空外键到 `follow_results.id`
- `member`、`status`、`raw_status`、`source`
- `profit_text`、`gold_text`、`win_gold_text`
- `is_final`、`ts`
- `source_hash TEXT NOT NULL`
- `raw_json TEXT NOT NULL`
- `created_at`、`updated_at`

同一票号的新结算记录通过 UPSERT 更新。沿用当前
`choose_settlement` 的优先级规则，最终状态不得被较旧或 pending 状态覆盖。
找不到跟单结果时允许外键为空，并记录迁移问题。

### `migration_runs` 与 `migration_issues`

记录每次导入的来源文件、文件哈希、读取行数、导入数、更新数、跳过数、
错误数和开始/结束时间。无法解析或无法关联的行写入
`migration_issues`，包括来源文件和行号，但不包含凭据等敏感字段。

## 实施步骤

### 1. 建立失败测试

新增 `tests/test_unified_storage.py`，覆盖：

- 初始化数据库和 Schema 版本
- 订单重复导入与更新
- 一个订单关联多个跟单结果
- 成功和失败跟单结果的幂等导入
- 结算 pending 到 final 的状态升级
- final 不被旧 pending 覆盖
- 孤立跟单和孤立结算不丢失
- 金额精度和 `raw_json` 完整性
- 并发写入的 busy timeout 行为

新增 `tests/test_jsonl_to_sqlite_migration.py`，用临时目录模拟三类历史文件，
验证迁移可重入及逐文件对账。先运行测试确认因功能缺失而失败。

### 2. 实现统一存储层

新增 `src/hdzz/common/storage.py`：

- 连接配置和 Schema 迁移
- `upsert_order`
- `append_follow_result`
- `upsert_settlement`
- WebUI 和 Telegram 所需的批量查询接口
- 事务、重试、完整性检查和在线备份辅助函数

该模块只负责存储，不承载盘口、跟单或结算业务判断。

### 3. 实现历史迁移工具

新增 `app/tools/migrate_jsonl_to_sqlite.py`，支持：

- `--app-dir` 指定运行数据根目录
- `--database` 指定目标数据库
- `--dry-run` 仅解析和对账
- 可重入导入及迁移报告
- 导入后执行 `foreign_key_check` 和 `quick_check`

导入顺序固定为订单、跟单结果、结算，确保可建立尽可能多的关联。

### 4. 切换在线写入

按以下顺序逐个切换并运行相关测试：

1. `src/hdzz/core/watcher/ag_watcher.py` 和 `ook/signal_puller.py`
2. `src/hdzz/core/follower/engine.py`
3. `src/hdzz/core/settlement/importer.py`

兼容期内统一 helper 先提交 SQLite，再写旧 JSONL。SQLite 写失败必须返回
明确错误并记录日志；旧 JSONL 写失败也必须告警，不能静默形成分叉。

### 5. 切换查询端

修改：

- `src/hdzz/api/webui/server.py`
- `src/hdzz/api/telegram/bot.py`

优先查询 SQLite；数据库尚未建立时回退当前 JSONL 逻辑。报表切换前后使用
同一时间范围对比订单数、跟单数、票号数、结算数和盈亏合计。

### 6. 迁移实际运行数据

迁移前：

1. 确认 2299 应用和 worker 已停止。
2. 对整个运行数据目录创建带时间戳的只读备份。
3. 记录所有来源文件的 SHA-256、行数和大小。
4. 先运行 `--dry-run`，所有可解析错误必须解释。

正式迁移后：

1. 核对每类来源行数与导入、更新、跳过、错误数量。
2. 执行 `PRAGMA foreign_key_check`。
3. 执行 `PRAGMA quick_check`；最终发布前执行 `integrity_check`。
4. 对订单、票号、最终结算和盈亏做抽样与总量对账。
5. 保存迁移报告和数据库备份。

### 7. 构建和应用验证

使用仓库的 macOS standalone 构建流程重建 `.app`。启动后验证：

- 新订单同时进入 SQLite 和兼容 JSONL
- 跟单结果可关联订单并保存票号
- 结算更新能关联票号且 final 不回退
- WebUI 历史与结算报表一致
- Telegram 今日报表和结算图片内容一致
- 重启后数据不丢失，多进程并发无锁死

## 回滚方案

- 不删除旧 JSONL，因此可将查询和写入开关恢复到 JSONL。
- 恢复源码备份并重新构建原 `.app`。
- 将运行数据备份恢复到原目录；新 `hdzz.db` 单独保留用于问题分析。
- 回滚前后再次记录文件哈希，避免误覆盖迁移后产生的新业务数据。

## 验收标准

- 全部现有测试和新增测试通过。
- 数据库 `quick_check`、`integrity_check`、`foreign_key_check` 均通过。
- 三类来源数据逐文件对账闭合，所有异常均有迁移问题记录。
- WebUI 和 Telegram 的历史订单数、结算状态及盈亏与迁移前一致。
- 重复执行迁移不会产生重复业务记录。
- 原 JSONL 和迁移前备份均未删除。
