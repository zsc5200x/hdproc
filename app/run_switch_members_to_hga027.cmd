@echo off
setlocal
cd /d "%~dp0"

REM Use PowerShell to avoid CMD encoding issues.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_switch_members_to_hga027.ps1"

echo.
pause

