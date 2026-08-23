# Mac Desktop WebView Shell Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将现有本地 WebUI 嵌入独立 Mac 软件窗口中，让用户双击后直接看到软件界面，而不是调用系统浏览器。

**Architecture:** 继续复用现有 Python worker 和本地 `webui_server`，但把独立版入口从“启动后 open 浏览器”改为“启动后创建 PySide6/QWebEngineView 窗口并加载 localhost 页面”。PyInstaller 独立包增加 PySide6/QtWebEngine 依赖与 hooks，仍然保留 worker 分发模式。

**Tech Stack:** Python 3, PySide6, QtWebEngine, PyInstaller, localhost WebUI

---

### Task 1: 桌面入口测试与纯函数 helper

**Files:**
- Modify: `app/mac_standalone_entry.py`
- Test: `tests/test_mac_desktop_entry.py`

**Step 1: Write the failing test**

```python
def test_build_service_url_normalizes_host_and_port():
    assert build_service_url("127.0.0.1", 9999) == "http://127.0.0.1:9999/"
```

**Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests/test_mac_desktop_entry.py`
Expected: FAIL because desktop helpers do not exist.

**Step 3: Write minimal implementation**

- 新增 `build_service_url()` 等纯函数 helper
- 保持 worker 模式与 runtime bootstrap 不变

**Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests/test_mac_desktop_entry.py`
Expected: PASS

### Task 2: 独立版构建输入切换到桌面壳

**Files:**
- Modify: `app/tools/build_mac_standalone_app.py`
- Modify: `app/tools/hdzz_mac_standalone.spec`
- Test: `tests/test_mac_standalone_bundle_builder.py`

**Step 1: Write the failing test**

```python
def test_build_spec_inputs_include_pyside6_webengine_for_desktop_shell():
    ...
```

**Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests/test_mac_standalone_bundle_builder.py`
Expected: FAIL because desktop shell dependencies are not collected yet.

**Step 3: Write minimal implementation**

- 构建 venv 安装 `PySide6`
- spec file 增加桌面入口需要的 hidden imports / collected modules

**Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests/test_mac_standalone_bundle_builder.py`
Expected: PASS

### Task 3: 软件窗口实现

**Files:**
- Modify: `app/mac_standalone_entry.py`

**Step 1: Write the failing behavior-oriented test**

- 用纯函数/参数测试保证默认 host/port 与关闭逻辑可控

**Step 2: Implement desktop window**

- 懒加载 `PySide6`
- 启动 workers 后轮询服务健康状态
- 用 `QMainWindow + QWebEngineView` 加载 localhost URL
- 关闭窗口时调用 `stop_all()`
- 失败时退回清晰错误提示，而不是静默退出

**Step 3: Verify**

Run: `python3 -m unittest tests/test_mac_desktop_entry.py tests/test_mac_standalone_bundle_builder.py`
Expected: PASS

### Task 4: 真实打包与运行验证

**Files:**
- Modify: `README_PORTABLE.txt`

**Step 1: Build**

Run: `python3 app/tools/build_mac_standalone_app.py --source-root . --output-dir _dist --bundle-name "AG Matrix Standalone"`

**Step 2: Verify artifact**

Expected:
- `.app` 成功生成
- bundle 含 Qt WebEngine / AppIcon / ms-playwright
- 启动后不再调用系统浏览器

**Step 3: Document**

- 在 `README_PORTABLE.txt` 增加“桌面窗口版”的说明

