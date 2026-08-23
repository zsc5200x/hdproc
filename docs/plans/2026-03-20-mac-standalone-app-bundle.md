# Mac Standalone App Bundle Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 生成一个适用于 Apple Silicon Mac 的独立 `AG Matrix.app`，让朋友机器无需额外安装 Python 也能双击启动。

**Architecture:** 使用 PyInstaller `onedir` 构建真正的 macOS `.app`。主可执行文件负责两类模式：正常启动 WebUI 栈，以及隐藏的 worker 派发模式；现有 `app.py` 改为在冻结环境中通过同一个可执行文件拉起各个 worker。Playwright 浏览器与 WebUI/static 资源通过 spec file 一并打入 bundle，并在运行时通过环境变量与资源定位函数解析路径。

**Tech Stack:** Python 3, PyInstaller, Playwright, argparse, macOS `.app` bundle

---

### Task 1: 冻结环境下的 worker 派发

**Files:**
- Modify: `app/app.py`
- Test: `tests/test_mac_standalone_worker_launch.py`

**Step 1: Write the failing test**

```python
def test_build_worker_command_uses_same_executable_when_frozen():
    ...
```

**Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests/test_mac_standalone_worker_launch.py`
Expected: FAIL because helper/dispatch logic does not exist yet.

**Step 3: Write minimal implementation**

- 在 `app/app.py` 中新增 worker 规格到执行命令的转换 helper
- 冻结环境下改为 `[sys.executable, "--hdzz-worker", worker_name, ...]`
- 普通源码环境保持现在的 `sys.executable + script.py` 逻辑
- 增加隐藏参数派发，把 worker 名映射到对应 `main(argv)` 入口

**Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests/test_mac_standalone_worker_launch.py`
Expected: PASS

### Task 2: 独立包运行时资源定位

**Files:**
- Modify: `app/portable_env.py`
- Modify: `app/app.py`
- Test: `tests/test_mac_standalone_runtime_paths.py`

**Step 1: Write the failing test**

```python
def test_detect_playwright_browsers_path_prefers_bundle_resources_when_frozen():
    ...
```

**Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests/test_mac_standalone_runtime_paths.py`
Expected: FAIL because bundle资源路径尚未识别。

**Step 3: Write minimal implementation**

- 为冻结环境增加 bundle root / resources root 解析
- 让 Playwright 浏览器目录、日志目录、app 目录可从 bundle 内定位
- 保持现有源码运行路径不变

**Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests/test_mac_standalone_runtime_paths.py`
Expected: PASS

### Task 3: PyInstaller 构建脚本与 spec file

**Files:**
- Create: `app/tools/build_mac_standalone_app.py`
- Create: `app/tools/hdzz_mac_standalone.spec`
- Create: `Build_Mac_Standalone_App.command`
- Test: `tests/test_mac_standalone_bundle_builder.py`

**Step 1: Write the failing test**

```python
def test_spec_includes_required_datas_and_hiddenimports():
    ...
```

**Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests/test_mac_standalone_bundle_builder.py`
Expected: FAIL because standalone builder/spec file do not exist yet.

**Step 3: Write minimal implementation**

- 构建脚本负责准备输出目录、生成/校验 Playwright 浏览器目录、调用 PyInstaller
- spec file 打入 `app/webui`、`src/`、`ook/`、配置模板、Playwright 浏览器目录
- 根目录 `.command` 作为双击构建入口

**Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests/test_mac_standalone_bundle_builder.py`
Expected: PASS

### Task 4: 真实构建验证

**Files:**
- Modify: `README_PORTABLE.txt`

**Step 1: Build the standalone app**

Run: `python3 app/tools/build_mac_standalone_app.py --source-root . --output-dir _dist`

**Step 2: Inspect output**

Expected:
- `_dist/AG Matrix Standalone.app` exists
- bundle contains launcher executable and Playwright/browser resources

**Step 3: Document usage**

- 在 `README_PORTABLE.txt` 说明独立包的生成方式和朋友机器的使用方式

