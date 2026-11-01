# 本次交付验证

日期：2026-10-17（Asia/Shanghai）。

- Python 3.9 与独立 Python 3.11 环境验证。
- 18 项离线测试：订单解析、首次快照、新单、字段更新、空窗口后重现去重、重启去重、写盘中断后恢复、单实例锁、配置校验、空凭据不提交、登录提交一次、2FA 等待 300 秒、403 后原窗口等待、取消、窗口关闭和只读接口约束。
- 真实 Chromium 本地模拟接口验证：启动独立浏览器配置、会话验证、POST 保活/订单查询、SQLite/JSONL 存储、关闭浏览器。
- 命令行帮助、初始化、依赖诊断、状态查询和脚本语法检查。
- 源码不导入 hdzz、皇冠、WebUI 或跟单模块。

本次没有使用真实账号重新登录平博，也没有新请求真实订单或发送 Telegram。真实站点的登录/2FA 和接口兼容性仍须由首次运行确认；已提取原项目使用的登录和查询实现。

复验：

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python tests/smoke_browser.py
.venv/bin/python run.py doctor
```
