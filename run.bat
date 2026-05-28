@echo off
REM AI TODO Clipboard Watcher — manual start for testing
REM Screenshot (Win+Shift+S) → auto-detect → AI → calendar

cd /d "%~dp0"
C:\Users\32030\miniconda3\envs\ai\python.exe hotkey_screenshot.py
pause
