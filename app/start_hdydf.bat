@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
title AG Matrix (HDYDF)

echo ==========================================
echo       Starting HDYDF Control Center...
echo ==========================================
echo.

set "HOST=127.0.0.1"
set "PORT=9999"

rem Control Center only: WebUI stays up, Start/Stop in UI controls core workers.

rem Make logs readable in UTF-8
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

echo Working Dir: "%CD%"
echo Script Dir : "%~dp0"
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python not found in PATH.
  echo Please install Python 3.11+ and ensure "python" is available.
  echo.
  pause
  exit /b 2
)

rem Stop any legacy full-suite instance (app.py start might have launched WebUI/core workers before)
python "%~dp0app.py" stop --force >nul 2>nul

rem Ensure old WebUI instance is not occupying the port (best-effort)
for /f "tokens=5" %%a in ('netstat -ano ^| findstr /R /C:":%PORT% .*LISTENING"') do (
  echo [INFO] Stopping existing listener on port %PORT% (PID %%a)...
  taskkill /PID %%a /F >nul 2>nul
)

rem Start WebUI only (core workers are started/stopped from the WebUI button)
echo WebUI Script: "%~dp0webui_server.py"
echo.
python "%~dp0webui_server.py" --host "%HOST%" --port %PORT% --orders-dir "orders" --webui-dir "%~dp0webui" --state-dir "%~dp0state" --config "%~dp0config.json" --data-dir "%~dp0data"
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
  echo.
  echo [ERROR] Failed to start WebUI server.
  echo.
  pause
  exit /b 1
)

:OPEN_UI
echo.
echo Started. WebUI: http://%HOST%:%PORT%/
echo Logs: "%~dp0.run\logs"
echo Core Stop: python "app.py" stop-core --force
echo Core Status: python "app.py" status-core
echo.

start "" "http://%HOST%:%PORT%/"
echo (This window can be closed. Services keep running in background.)
echo.
pause
