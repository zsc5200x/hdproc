@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d %~dp0..\\app

set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PLAYWRIGHT_BROWSERS_PATH=%~dp0..\\ms-playwright"
set "PYTHONPATH=%~dp0..\\src;%CD%"
set "NO_PROXY=127.0.0.1,localhost"
set "no_proxy=127.0.0.1,localhost"

set "HOST=127.0.0.1"
set "PORT=9999"

rem 安全模式：不强制 taskkill 占用端口的进程（避免误杀客户电脑上的其它服务）。
for /f "tokens=5" %%a in ('netstat -ano ^| findstr /R /C:":%PORT% .*LISTENING"') do (
  echo [ERROR] 端口 %PORT% 已被占用（PID=%%a）。
  echo 请关闭占用进程，或修改本文件中的 PORT=9999 为其它端口（例如 19999）。
  echo.
  pause
  exit /b 2
)

rem Start the full project stack with bundled Python.
"%~dp0..\\python\\python.exe" app.py start --host %HOST% --port %PORT%
if errorlevel 1 (
  echo.
  echo [ERROR] Failed to start the full project stack.
  echo.
  pause
  exit /b 1
)

start "" "http://%HOST%:%PORT%/"

@echo.
@pause
