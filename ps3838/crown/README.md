# 皇冠足球赔率终端

皇冠运行时负责会员站足球赛事与盘口的本地只读采集，在主看板的“皇冠数据”页面独立展示。主页才对照已确认绑定的平博盘口；“皇冠数据”页只显示皇冠来源。

## 启动和依赖

推荐从项目根目录使用统一脚本：

```bash
./start.sh
./crown.sh
```

`start.sh` 启动完整项目并在需要时补开普通皇冠标签页；`crown.sh` 仅启动皇冠后端。默认统一页面 `http://127.0.0.1:8787/#crown`，皇冠后端默认 `http://127.0.0.1:9223/crown`。端口和主机来自根目录共享配置。Chrome CDP 仍使用 `http://127.0.0.1:9222`，两者是不同用途的端口。

皇冠现有环境基线为 macOS / Python 3.14，完整依赖版本快照位于 `requirements/crown-macos-py314.lock`。只在新建环境时，从项目根目录运行：

```bash
python3.14 -m venv crown/.venv
crown/.venv/bin/python -m pip install -r requirements/crown-macos-py314.lock
```

共享 CDP 使用已安装的 Chrome，不需要为了这一流程安装 Playwright 自带 Chromium。根 `.venv` 是平博的独立 Python 3.9 环境，不与皇冠混装。

手动运行后端仍读取统一配置：

```bash
crown/.venv/bin/python crown/app.py
```

`PS3838_CONFIG` 选择配置文件，非空环境变量优先于 JSON，JSON 优先于默认值。`crown_cdp_url` 留空时继承平博 `cdp_url`；`CROWN_CDP_URL` 可专用覆盖。直接运行后端也默认复用共享 CDP，不再因省略该环境变量切换到独立浏览器。

## 手动登录

1. 打开“皇冠数据”，进入账号设置或“连接皇冠会话”。
2. 保存皇冠登录地址和账号，打开普通登录标签页。
3. 在皇冠站点页面自行输入账号、密码与验证码/二次验证信息。
4. 保持登录标签打开，等待会话同步及赛事刷新；必要时点击“检查会话”。

项目不自动填写或提交登录表单，不接收或保存密码、安全密码和 TOTP。账号与加密会话用于后续只读查询；保存共享上下文 Cookie 时按皇冠来源过滤，不写入平博 Cookie。

上游 `doubleLogin` 映射为 `session_conflict`，表示旧保存会话被拒绝。它不证明当前浏览器标签未登录，也不证明一定存在另一设备登录。系统会检查当前已登录皇冠标签并同步会话；持续未恢复时再检查会话或在原标签手动登录。

## 刷新和缓存

- 基础赛事列表成功刷新默认以 4 秒为目标周期，耗时较长时实际间隔随请求延长。
- 普通上游错误按 4、8、16、30 秒退避；无会话、会话冲突等状态使用各自重试间隔。
- 页面每 1 秒读取本地轻量快照，不以该频率请求皇冠上游。
- 附加盘单独排队和缓存：滚球缓存目标 10 秒、其他赛事 30 秒，受并发和批量限制，不能保证与列表同一时刻更新。
- 最近成功快照原子写入 `crown_data_dir/events-cache.json`。服务重启或请求失败时仍可显示缓存，并标明最后成功时间。
- 缓存只含规范化赛事与盘口，不含账号密码、Cookie 或会话凭据；账号及加密会话存储另在该目录的数据库中。

接口在线和已登录都不等于盘口数据一定最新或有权限。请结合页面来源状态、详情加载状态、最后更新时间及诊断日志判断。

## 目录与数据

```text
app.py                         HTTP 服务、静态页面和 API 路由
crown_runtime.py               账号运行时、列表轮询、缓存和盘口标准化
crown_local/backend/           会话监听、会员站请求、赔率和存储
static/                        皇冠页面、样式和交互脚本
event_filters.py               皇冠赛事名称过滤
test_*.py                      皇冠服务、会话、缓存和赔率回归测试
```

数据路径由根 `project_config.py` 的 `crown_data_dir` 管理；旧默认 `crown/data/` 继续兼容，新示例与迁移布局使用 `var/crown/`。目录中的 `crown.sqlite3`、会话密钥和缓存都是本机数据，不提交 Git。迁移与回退使用根目录统一工具，参见 [运维指南](../docs/operations.md)。

## API

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/crown` | 皇冠赛事页面 |
| GET | `/api/crown/events` | 完整皇冠赛事快照 |
| GET | `/api/crown/events?compact=1` | 前端轻量赛事快照 |
| GET | `/api/crown/accounts` | 皇冠账号列表 |
| POST | `/api/crown/accounts` | 保存地址和账号、打开普通登录标签页 |
| GET | `/api/crown/session` | 当前会话及运行诊断 |
| POST | `/api/crown/session/check` | 检查当前会话并刷新数据 |
| POST | `/api/crown/events/<GID>/details` | 加载指定赛事附加盘 |
| POST | `/api/crown/accounts/<id>/<action>` | 账号登录、检查、取消、清除等操作 |
| DELETE | `/api/crown/accounts/<id>` | 删除本地账号及其会话 |

浏览器通过主看板同源代理使用上述路径。默认仅监听本机 `127.0.0.1`；更改绑定主机前应确认访问边界，账号与会话接口不适合作为公网服务。

## 验证

在项目根目录运行统一检查：

```bash
./check.sh
```

该入口分别使用两套 Python，并为皇冠测试配置临时数据目录。不要为测试连接真实账号数据库。日志位于统一 `log_dir` 下的 `crown.log`，数据滞留、会话同步和附加盘问题的排查步骤见 [运维指南](../docs/operations.md#排障顺序)。
