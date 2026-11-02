# HDPROC

体育赛事注单监听与自动跟单系统，包含 Python 后端、Vanilla JS WebUI、Telegram 集成和 Node.js 抓包工具。

## 本地启动

```bash
cd app
cp config.example.json config.json
python app.py start
python app.py status
```

WebUI 默认地址：<http://127.0.0.1:9999/>

PS3838 数据终端已合并进本仓库（`ps3838/`），入口在 HD 顶部导航 `PS38赛事`，同源地址 <http://127.0.0.1:9999/ps3838/>。`app.py start/stop/restart` 会同时管理 PS3838 后台订单轮询和看板；也可用 HD 的「平博 / PS3838」节点会话直接同步代理后台来单。皇冠数据服务代码已从仓库移除；需要皇冠联动时，须自行运行兼容的外部服务并配置其地址。

停止服务：

```bash
cd app
python app.py stop
```

## 测试

```bash
python -m pytest -q tests
```

## 配置与数据

真实配置、账号、Cookie、Token、注单、运行状态、浏览器环境和打包产物均由 `.gitignore` 排除，不会提交到 GitHub。

- 主配置模板：`app/config.example.json`
- 主配置文件：`app/config.json`（仅保存在本机）
- 付费机器人环境变量模板：`app/.env.example`
- 运行数据：`app/data/`、`app/orders/`、`app/state/`
- PS3838 配置：`ps3838/config.json`（仅本机）
- PS3838 运行数据：`ps3838/var/`（数据库、采集、日志、PID，已忽略）

PS3838 主进程使用 HD 的 Python 3.9 环境。

新电脑克隆仓库后，需要重新创建本机配置和安装依赖，不能从 Git 仓库恢复真实账号及运行数据。

## 日常版本更新

修改前同步：

```bash
git pull --rebase
```

修改完成后保存并上传：

```bash
git status
git add -A
git commit -m "fix: 说明本次修改"
git push
```

查看历史：

```bash
git log --oneline --decorate -20
```
