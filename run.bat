@echo off
cd /d "%~dp0"

echo AI TODO Assistant - Starting...
echo.

echo [1/2] Starting calendar server...
start "AI-Calendar" /min C:\Users\32030\miniconda3\envs\ai\python.exe "%~dp0calendar_server.py"
echo       Calendar will be at http://127.0.0.1:8080
echo       Waiting 3 seconds for server to boot...
timeout /t 3 /nobreak >nul
echo       Opening calendar in browser...
start http://127.0.0.1:8080
echo.

echo [2/2] Starting screenshot watcher...
echo       Win+Shift+S to capture - AI will auto-analyze
echo       Close this window to stop
echo.
C:\Users\32030\miniconda3\envs\ai\python.exe "%~dp0hotkey_screenshot.py"
pause
