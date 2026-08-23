# Windows Portable Full Startup Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 让 `hdproc` 复制整个文件夹到 Windows 后，双击根目录启动脚本即可使用内置 Python 自动拉起完整项目链路，而不是只启动 WebUI。

**Architecture:** 保留当前“根目录 `.bat` 作为客户入口”的结构，但把实际启动目标从 `webui_server.py` 切换为 `app.py start`。同时修正便携打包脚本输出的 launcher，保证生成物和仓库内脚本行为一致。必要时让 PowerShell 入口优先使用内置 `..\python\python.exe`，避免依赖系统 Python。

**Tech Stack:** Windows `.bat`、PowerShell `.ps1`、Python `unittest` 文本检查。

---

### Task 1: 写失败测试锁定 Windows 启动行为

**Files:**
- Create: `tests/test_windows_portable_startup.py`

**Step 1: 写失败测试**

- 断言 `scripts/Start_AG_Matrix.bat` 与 `scripts/Start_AG_Matrix_SAFE.bat` 使用内置 `python\\python.exe`
- 断言它们调用 `app.py start` 而不是 `webui_server.py`
- 断言 `app/tools/package_portable.ps1` 生成同样的 launcher 行为

**Step 2: 运行测试确认失败**

Run:

```bash
python3 -m unittest tests/test_windows_portable_startup.py
```

Expected: FAIL，因为当前脚本仍是 WebUI only。

### Task 2: 修复仓库内 Windows 根启动脚本

**Files:**
- Modify: `scripts/Start_AG_Matrix.bat`
- Modify: `scripts/Start_AG_Matrix_SAFE.bat`

**Step 1: 改为全量启动**

- 保留端口处理逻辑
- 使用内置 `..\python\python.exe`
- 调用 `app.py start --host ... --port ...`

**Step 2: 运行测试转绿**

Run:

```bash
python3 -m unittest tests/test_windows_portable_startup.py
```

### Task 3: 修复便携打包输出的 launcher

**Files:**
- Modify: `app/tools/package_portable.ps1`
- Modify: `README_PORTABLE.txt`

**Step 1: 让生成的 `Start_AG_Matrix.bat` 与仓库脚本一致**

- 改为全量启动
- 文案更新为“自动拉起完整项目链路”

**Step 2: 可选补 PowerShell 入口**

**Files:**
- Modify: `app/app.ps1`
- Modify: `app/run_all.ps1`
- Modify: `app/stop_all.ps1`

- 优先查找 `..\python\python.exe`
- 无内置 Python 时再回退 PATH

### Task 4: 验证与使用结论

**Step 1: 运行测试**

```bash
python3 -m unittest tests/test_windows_portable_startup.py tests/test_port_defaults.py
```

**Step 2: 文本核对**

- `README_PORTABLE.txt`
- `scripts/Start_AG_Matrix.bat`
- `scripts/Start_AG_Matrix_SAFE.bat`
- `app/tools/package_portable.ps1`

**Step 3: 输出用户指南**

- 当前仓库整目录复制到 Windows 的运行方式
- 需要双击哪个文件
- 是否依赖系统 Python
