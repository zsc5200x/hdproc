# HGA 抓包工具（登录接口）

> 仅用于你自己账号的授权测试，请遵守目标站点条款。  
> 抓包文件可能包含敏感信息（如 token / cookie / password），请不要分享给他人。

## ✅ 启动 WUI（实时盘口/水位页面｜方案A）

> 方案A：服务端启动浏览器，你手动登录一次，服务端复用登录会话去拉取盘口/水位。  
> 只做“展示”，不包含下注，也不会调用 `p=FT_bet`。

1) 安装依赖

```bash
cd "/Users/zsc5200x/Desktop/cc"
npm install
```

2) 安装浏览器（只装 Chromium）

```bash
npm run install:browsers
```

3) 启动本地服务

```bash
npm run wui
```

4) 打开页面

- 浏览器打开：`http://localhost:3000`
- 按页面上的“操作步骤（小白版）”点按钮即可

> 可选环境变量：
> - `PORT`：默认 3000
> - `TARGET_URL`：默认 https://hga027.com/
> - `REFRESH_MS`：默认 3000（SSE 刷新间隔）

## 抓“登录”网络请求

```bash
npm run capture:login
```

如果你需要保存 HAR（不推荐，可能包含敏感信息），用：

```bash
SAVE_HAR=1 npm run capture:login
```

脚本会打开一个浏览器窗口：  
1) 你在页面里手动登录  
2) 看到已经登录成功后，回到终端按回车  
3) 脚本会保存抓包结果到 `captures/<时间戳>/`

输出文件：
- `captures/<时间戳>/login.har.zip`：HAR（网络请求归档，仅当 `SAVE_HAR=1`）
- `captures/<时间戳>/network-summary.json`：脱敏后的摘要（不会保存明文密码）
- `captures/<时间戳>/candidates.json`：脚本自动筛出来的“疑似登录接口”列表

接口文档（小白版）：
- `docs/hga027-api.md`

## 抓“盘口/水位”接口（带响应内容）

> 这个脚本会把 `transform.php`（以及部分比赛数据接口）的**响应内容**保存到本地，便于我后续解析出“gid 对应的所有盘口/水位”。  
> 响应文件可能包含敏感信息（cookie/token），请勿外传。

```bash
npm run capture:odds
```

输出目录：
- `captures/<时间戳>/responses-index.json`：每个响应保存到哪个文件
- `captures/<时间戳>/responses/`：响应内容文件（.json 或 .txt）

## 常见问题

### 1) 打开后闪退或提示没浏览器
先执行：`npm run install:browsers`

### 2) candidates 为空
很可能目标站点登录不是 XHR/Fetch（或接口命名不含 login/auth），此时看 `login.har.zip` 全量请求即可。
