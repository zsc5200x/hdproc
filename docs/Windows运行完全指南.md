# HDZZ 项目 Windows 运行完全指南

> **版本：** Portable 便携版
> **检查日期：** 2026-02-09
> **项目大小：** 3.0 GB

---

## ✅ Windows 兼容性检查结果

### **核心结论：完全支持 Windows，可直接拷贝运行！**

项目已经**完整适配 Windows**，包含：
- ✅ 内置 Python 3.11 运行环境（128 MB）
- ✅ 内置 Playwright 浏览器（Chromium，645 MB）
- ✅ 所有依赖已预装
- ✅ Windows 专用启动脚本（.bat）
- ✅ Windows 进程管理兼容性代码
- ✅ 路径分隔符自动处理（支持反斜杠）

---

## 📦 项目完整性检查

### **已包含的组件：**

```
hdzz/  (3.0 GB)
├── python/                        # ✅ Python 3.11 (128 MB)
│   ├── python.exe
│   ├── python311.dll
│   └── *.pyd (已编译的 Windows 扩展)
│
├── ms-playwright/                 # ✅ Chromium 浏览器 (645 MB)
│   └── chromium-*/
│
├── app/                           # ✅ 主程序
│   ├── webui_server.py           # WebUI 服务器
│   ├── ag_wmc_watch.py           # 后台订单监听
│   ├── follow_engine.py          # 跟单引擎
│   ├── member_arsenal_watch.py   # 会员管理
│   ├── config.json               # 配置文件
│   ├── data/                     # 运行时数据
│   ├── webui/                    # Web 界面
│   └── requirements.txt          # 依赖清单（已预装）
│
├── src/hdzz/                      # ✅ 核心代码模块
│   └── core/follower/engine.py   # 跟单引擎核心（6846 行）
│
├── scripts/                       # ✅ 启动脚本
│   ├── Start_AG_Matrix.bat       # Windows 快速启动
│   ├── Start_AG_Matrix_SAFE.bat  # Windows 安全模式
│   ├── check_config.py           # 配置诊断工具
│   └── analyze_logs.py           # 日志分析工具
│
├── Start_AG_Matrix.bat            # ✅ 根目录快捷启动（Windows）
├── Start_AG_Matrix_SAFE.bat      # ✅ 根目录安全启动（Windows）
└── README_PORTABLE.txt            # 使用说明
```

### **依赖项清单（已预装）：**

```
✅ requests[socks] >= 2.31.0
✅ urllib3 < 2
✅ playwright >= 1.49.0
✅ Python 3.11 标准库
```

---

## 🚀 Windows 运行方法

### **方法 1：双击启动（推荐）**

1. **拷贝整个文件夹到 Windows 电脑**
   - 路径示例：`C:\hdzz\` 或 `D:\工作\hdzz\`
   - ⚠️ 避免中文路径和空格（虽然支持，但可能有兼容性问题）

2. **双击启动文件**
   - **快速模式：** 双击 `Start_AG_Matrix.bat`
   - **安全模式：** 双击 `Start_AG_Matrix_SAFE.bat`（推荐）

3. **自动打开浏览器**
   - 会自动打开：`http://127.0.0.1:8080/`
   - 如果没有自动打开，手动访问此地址

### **方法 2：命令行启动**

```batch
# 1. 打开 CMD 或 PowerShell

# 2. 进入项目目录
cd C:\hdzz\app

# 3. 设置环境变量
set PLAYWRIGHT_BROWSERS_PATH=C:\hdzz\ms-playwright
set PYTHONPATH=C:\hdzz\src;%CD%

# 4. 启动 WebUI
..\python\python.exe webui_server.py --host 127.0.0.1 --port 8080 --orders-dir orders --webui-dir webui --config config.json --data-dir data
```

---

## 🔧 快速启动 vs 安全启动

### **Start_AG_Matrix.bat（快速模式）**

**特点：**
- ✅ 自动杀死占用 8080 端口的进程
- ✅ 快速启动，无需手动检查
- ⚠️ 可能误杀其他程序（如果 8080 被占用）

**适用场景：**
- 开发测试环境
- 你确定 8080 端口没有被其他重要程序占用

### **Start_AG_Matrix_SAFE.bat（安全模式）**⭐ 推荐

**特点：**
- ✅ 检测端口占用但不强制杀死
- ✅ 如果端口被占用，会提示你手动处理
- ✅ 避免误杀其他程序

**适用场景：**
- 生产环境
- 多用户环境
- 不确定 8080 端口是否被占用

---

## 🌐 访问 WebUI

### **启动成功后访问：**

```
http://127.0.0.1:8080/
```

### **WebUI 功能：**

1. **主控制面板**
   - 启动/停止核心服务（抓单、跟单、会员管理）
   - 实时状态监控
   - 配置管理

2. **弹药库（会员管理）**
   - 添加/编辑会员账号
   - 配置查水账号（role=quote）
   - 检测/重登会员

3. **跟单设置**
   - 配置跟单规则（固定金额/比例）
   - 选择信号来源和执行账号
   - 查看跟单结果

4. **查水页面**
   - 单场实时监控：`/chashui/index.html`
   - 跟单队列管理：`/chashui/queue.html`

---

## ⚙️ 首次启动配置

### **步骤 1：配置后台监听账号**

1. 打开 WebUI：`http://127.0.0.1:8080/`
2. 点击"配置"或"设置"
3. 添加后台监听账号：
   - 域名：`https://你的后台域名/`
   - 账号：`你的后台账号`
   - 密码：`你的后台密码`
   - UID：留空（自动获取）
   - Cookie：留空（自动获取）

### **步骤 2：配置会员账号（弹药库）**

1. 打开"弹药库"页面
2. 点击"添加会员"
3. 填写信息：
   ```json
   {
     "label": "查水账号A",           // 友好标签
     "role": "quote",               // quote=查水，bet=下注
     "base_url": "https://hga027.com",
     "username": "test001",
     "password": "密码",
     "security_code": "888888",     // 安全码（可选）
     "login_tab": "3"               // 登入页（默认3）
   }
   ```
4. 至少添加：
   - 1 个 `role=quote` 的查水账号
   - 1-3 个 `role=bet` 的下注账号

### **步骤 3：配置跟单规则**

1. 打开"跟单设置"页面
2. 设置参数（推荐值）：
   ```json
   {
     "enabled": true,
     "mode": "fixed",              // fixed=固定金额，ratio=比例
     "fixed_amount": 50,           // 每单金额
     "split_count": 3,             // 分单数量（推荐2-3）
     "split_delay_seconds": 0.5,   // 分单延迟（推荐0.5-1.0）
     "wait_line_not_found_max_seconds": 45  // 探测窗口（推荐45-60）
   }
   ```

### **步骤 4：启动核心服务**

在 WebUI 主页点击：
- ✅ 启动"后台订单监听"（ag_wmc_watch）
- ✅ 启动"会员自动续期"（member_arsenal_watch）
- ✅ 启动"跟单引擎"（follow_engine）

---

## 📁 重要文件和目录

### **配置文件：**

```
app/
├── config.json                    # 后台账号配置
├── data/
│   ├── arsenal.json               # 会员池（弹药库）
│   ├── follow_settings.json       # 跟单配置
│   └── quote_member.json          # 查水账号（可选）
```

### **数据文件：**

```
app/
├── orders/                        # 订单记录（.jsonl）
├── data/
│   ├── follow_results.jsonl       # 跟单结果日志
│   └── logs/                      # 运行日志（如果 HDZZ_LOG_DIR 未设置）
└── .run/
    └── logs/                      # 默认日志目录
        ├── ag_wmc_watch.err.log   # 后台监听日志
        ├── follow_engine.err.log  # 跟单引擎日志
        └── member_arsenal_watch.err.log  # 会员管理日志
```

---

## 🛡️ Windows 防火墙和安全软件

### **可能遇到的问题：**

1. **Windows Defender 阻止 python.exe**
   - 解决：添加 `hdzz\python\python.exe` 到白名单

2. **防火墙阻止 8080 端口**
   - 解决：允许 python.exe 监听 8080 端口（仅本地）

3. **杀毒软件误报**
   - 解决：添加 `hdzz` 整个文件夹到白名单

### **添加白名单步骤（Windows Defender）：**

```
1. 打开"Windows 安全中心"
2. 点击"病毒和威胁防护"
3. 点击"管理设置"
4. 滚动到"排除项"
5. 点击"添加排除项"
6. 选择"文件夹"
7. 浏览到 C:\hdzz\ 并添加
```

---

## 🔍 常见问题排查

### **问题 1：双击 .bat 文件闪退**

**原因：** Python 路径错误或缺少依赖

**解决：**
1. 右键点击 `.bat` 文件，选择"编辑"
2. 检查第 24 行的 Python 路径是否正确
3. 手动运行：
   ```batch
   cd C:\hdzz\app
   ..\python\python.exe webui_server.py
   ```
4. 查看错误信息

### **问题 2：浏览器无法访问 http://127.0.0.1:8080/**

**原因：** 端口被占用或防火墙阻止

**解决：**
1. 检查端口占用：
   ```batch
   netstat -ano | findstr ":8080"
   ```
2. 如果有其他程序占用，修改端口：
   - 编辑 `scripts\Start_AG_Matrix_SAFE.bat`
   - 修改 `set "PORT=8080"` 为 `set "PORT=18080"`
3. 检查防火墙设置

### **问题 3：启动后没有数据**

**原因：** 配置未完成或服务未启动

**解决：**
1. 访问 WebUI：`http://127.0.0.1:8080/`
2. 检查"配置"页面是否已填写
3. 在主页点击"启动"按钮启动核心服务
4. 查看日志：`app\.run\logs\*.err.log`

### **问题 4：Playwright 浏览器无法启动**

**原因：** `ms-playwright` 路径错误

**解决：**
1. 检查 `ms-playwright` 文件夹是否存在
2. 手动设置环境变量：
   ```batch
   set PLAYWRIGHT_BROWSERS_PATH=C:\hdzz\ms-playwright
   ```
3. 重新启动

### **问题 5：中文乱码**

**原因：** 终端编码问题

**解决：**
- 启动脚本已设置 `chcp 65001`（UTF-8）
- 如果仍有问题，右键 CMD 窗口 → 属性 → 字体 → 选择支持中文的字体

---

## 📊 性能和系统要求

### **最低配置：**

```
- 操作系统：Windows 10/11（64位）
- CPU：双核 2.0 GHz
- 内存：4 GB
- 硬盘：5 GB 可用空间
- 网络：稳定的互联网连接
```

### **推荐配置：**

```
- 操作系统：Windows 10/11（64位）
- CPU：四核 3.0 GHz+
- 内存：8 GB+
- 硬盘：10 GB+ 可用空间（SSD 更佳）
- 网络：100 Mbps+ 带宽
```

### **资源占用：**

```
- 磁盘占用：3.0 GB（安装后）
- 内存占用：
  - WebUI：~150 MB
  - 后台监听：~100 MB
  - 跟单引擎：~200 MB
  - 会员管理：~150 MB/账号
  - 总计：~600 MB - 1 GB（取决于会员数量）
- CPU 占用：<10%（空闲）、20-30%（活跃下单）
```

---

## 🔐 安全建议

### **生产环境部署：**

1. **修改默认端口**
   - 不要使用默认的 8080 端口
   - 改为 18080 或其他非标准端口

2. **限制访问**
   - 只监听 `127.0.0.1`（本地）
   - 不要监听 `0.0.0.0`（对外）

3. **数据备份**
   - 定期备份 `app/data/` 目录
   - 定期备份 `app/orders/` 目录

4. **日志管理**
   - 定期清理旧日志（避免磁盘占满）
   - 保留最近 7-14 天的日志

---

## 📦 拷贝到其他 Windows 电脑

### **完全支持！无需安装！**

**拷贝步骤：**

1. **压缩整个文件夹**
   ```
   右键 hdzz 文件夹 → 发送到 → 压缩(zipped)文件夹
   ```

2. **拷贝到目标电脑**
   - U盘、网盘、局域网共享均可
   - 建议放在 `C:\` 或 `D:\` 根目录

3. **解压并运行**
   ```
   1. 解压 hdzz.zip 到 C:\hdzz\
   2. 双击 C:\hdzz\Start_AG_Matrix_SAFE.bat
   3. 完成！
   ```

**注意事项：**
- ✅ 无需安装 Python（已内置）
- ✅ 无需安装浏览器（已内置）
- ✅ 无需联网下载依赖（已预装）
- ⚠️ 需要配置账号信息（首次运行）
- ⚠️ 需要添加防火墙白名单（如果有安全软件）

---

## 🎯 快速上手流程

**5 分钟快速启动：**

```
1. 拷贝文件夹到 Windows 电脑
   └─ 建议路径：C:\hdzz\

2. 双击启动
   └─ Start_AG_Matrix_SAFE.bat

3. 配置账号
   ├─ 后台账号（监听订单）
   ├─ 查水账号（role=quote）
   └─ 下注账号（role=bet）

4. 启动服务
   ├─ 后台订单监听
   ├─ 会员自动续期
   └─ 跟单引擎

5. 开始跟单
   └─ 实时监控：http://127.0.0.1:8080/
```

---

## 📞 技术支持

**遇到问题？**

1. 查看日志：`app\.run\logs\*.err.log`
2. 运行诊断：`python scripts\check_config.py`
3. 查看文档：`docs\` 目录
4. 联系技术支持

---

## 📝 版本信息

```
项目名称：HDZZ (AG Matrix)
版本类型：Portable 便携版
Python 版本：3.11
Playwright 版本：1.49.0+
浏览器：Chromium (内置)
平台支持：Windows 10/11 (x64)
项目大小：3.0 GB
检查日期：2026-02-09
```

---

**总结：完全兼容 Windows，开箱即用，拷贝即可运行！** ✅
