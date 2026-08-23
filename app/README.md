# AG 注单监听（Python 纯接口 + WebUI）

此项目通过轮询后台注单监听窗口使用的接口 `transform.php` 获取新注单，并把新订单追加写入 `orders/<target>.jsonl`，再由 WebUI 读取展示。

## 安装

```bash
python -m pip install -r requirements.txt
python -m playwright install chromium
```

## 配置

- 复制 `config.example.json` 为 `config.json`
- 填写/确认：
  - `targets[].uid`：浏览器 Network → `transform.php` → Payload/Form Data 里的 `uid`
  - `targets[].cookie`：Network → Headers → Request Headers 里的 `cookie` 整串
  - `proxy`：如果你需要走本地代理（例如 `http://127.0.0.1:7897`）

## 启动监听脚本（抓单）

```bash
python -u ag_wmc_watch.py --config config.json --timeout 30 --interval 3 --only-orders
```

- `--interval 3`：每 3 秒轮询一次
- `--only-orders`：只输出订单/掉线/错误（不刷“无新注单”）

## 启动 WebUI（暗色仪表盘）

1) 先运行监听脚本（确保持续写入 `orders/`）

2) 再启动 WebUI 服务：

```bash
python webui_server.py --host 127.0.0.1 --port 9999 --orders-dir orders --webui-dir webui --config config.json --data-dir data
```

3) 打开浏览器访问：`http://127.0.0.1:9999/`

### 在 WebUI 里添加账号（推荐）

- 左侧栏点“添加”，填写：域名 / 账号 / 密码（可选：安全码；登入页默认 3）
- 添加后会写入 `config.json`，监听脚本支持热加载配置：无需重启即可新增监控账号
- 若登录过程出现验证码/图形校验：会弹出浏览器窗口，你手动完成一次登录即可（后续复用会话）

## 弹药库 / 跟单设置（WebUI）

- `弹药库`：管理会员端账号（用于后续跟单执行）。数据保存在 `data/arsenal.json`。
- `跟单设置`：保存规则（固定金额/比例/封顶、选择信号来源与执行账号）。数据保存在 `data/follow_settings.json`。
- 说明：只启动 WebUI 不会自动下注；需要额外启动 `follow_engine.py`（或用 `run_all.ps1`）才会执行跟单下注。

## 会员端自动登录（弹药库自动补齐 uid/cookie）

如果你不想手动去抓 `uid/cookie`，可以启动会员端 watcher，它会：
- 用你在 `弹药库` 里填写的 `域名/账号/密码` 自动登录会员端
- 自动写回 `data/arsenal.json`：补齐 `ver/uid/mid/cookie`
- 掉线后自动检测并重新登录（尽量复用会话，减少频繁登录）

启动命令（建议单独开一个 PowerShell 窗口常驻）：

```bash
python -u member_arsenal_watch.py --data-dir data --interval 20
```

## 跟单执行引擎（真实下注）⚠️

本项目的“真实跟单下注”需要你额外启动一个进程：`follow_engine.py`。

重要说明：
- 这会真实调用会员端 `FT_bet` 接口下注，属于不可逆资金操作，请先小额测试。
- 当前版本只支持后台订单的两类玩法：`让球(R)`、`大小(OU)`；其他玩法会跳过并记录失败原因。
- 跟单结果会写入 `data/follow_results.jsonl`，并在 WebUI 的“跟单设置”页展示最近结果。
- 盘口关闭/暂停下注（`err=1X001`）时，默认采用“30秒后重试；若仍失败则60秒后再试，并让下一单先尝试”的策略，避免一直卡在同一单上。

启动命令（建议单独开一个 PowerShell 窗口常驻）：

```bash
python -u follow_engine.py --orders-dir orders --data-dir data
```

## 一键启动（推荐）

如果你不想分别开多个窗口，可以用 PowerShell 一键拉起全部进程（抓单 + WebUI + 会员续期 + 跟单引擎）：

```powershell
# 默认真实下注（Live）
.\run_all.ps1
```

停止全部（可选）：

```powershell
.\stop_all.ps1
```

## 单文件启动（推荐给不想记脚本的人）
如果你只想记一个文件/一个命令，可以用 `app.py`（它会启动：抓单 + WebUI + 会员续期 + 跟单引擎）：

```powershell
# 启动（默认 live）
python app.py start

# 查看状态
python app.py status

# 停止（可加 --force 强制）
python app.py stop
```

日志（排查掉线/重登原因）：
- `.run/logs/ag_wmc_watch.err.log`：后台抓单/掉线/重登日志
- `.run/logs/member_arsenal_watch.err.log`：会员自动登录/掉线重登日志
- `.run/logs/follow_engine.err.log`：跟单下注执行日志

## 掉线自动重登（可选）

当接口返回 `code=4X014` 等“需要重新登录”时，可让脚本自动拉起浏览器重新登录并刷新 Cookie，然后继续轮询。

- 在对应 target 里设置：
  - `poll_mode`: `"playwright"`（用无头浏览器上下文来抓单，避免纯 requests 在某些网络/SSL 环境下不稳定）
  - `force_relogin_on_offline`: `true`（仅当检测到“被踢/掉线需要重新登录”时，缩短重登间隔并立即重登；不会因为网络超时/系统忙碌去重登其它正常账号）
  - `relogin_mode`: `"playwright"`
  - `relogin_on_start`: `true`（启动/新增账号后先登录一次拿 Cookie/uid）
  - `relogin_headless`: `true`（无头浏览器；若首次登录遇到验证码，建议先临时改为 `false` 完成一次登录）
  - `relogin_login_tab`: `3`（如果必须用“登入3”）
  - `username/password/security_code`：本地保存用于自动填充（不要泄露）

## 常见问题

- `Read timed out`：网络/代理不稳定，确认代理运行；或把 `--timeout` 调大（例如 `30`）
- `m_rep 系统忙碌`：服务端繁忙/限流；如你明确要求不冷却，在 target 里设 `cooldown_on_m_rep=false`（可能导致持续 m_rep）
- `code=4X014/login_required`：Cookie 失效/被踢下线；脚本会自动尝试重登（若启用），否则提示你更新 cookie
- `doubleLogin`：会员端认为“账号已在别处登录/登录态不完整”，下注相关接口会拒绝；先关闭该会员账号在浏览器/手机的登录，再等待 `member_arsenal_watch.py` 自动重登刷新 Cookie（必要时重启 `run_all.ps1`）

## 如何在浏览器里找到 uid/cookie

1) 登录后台 → 打开注单监听页：`/app/wmc/index.php`
2) `F12` → `Network` → `Fetch/XHR`
3) 点开 `transform.php` 请求：
   - `Payload` → `Form Data`：复制 `uid`
   - `Headers` → `Request Headers`：复制 `cookie`
