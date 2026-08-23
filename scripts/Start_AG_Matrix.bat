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

rem Best-effort: stop existing listener on the port (avoid multiple old instances)
for /f "tokens=5" %%a in ('netstat -ano ^| findstr /R /C:":%PORT% .*LISTENING"') do (
  taskkill /PID %%a /F >nul 2>nul
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
