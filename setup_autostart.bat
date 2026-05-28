@echo off
REM 一键开启：开机自动启动剪贴板监听
REM 之后每次截图 (Win+Shift+S) 都会自动分析导入日历

set STARTUP_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
set VBS_FILE=d:\Study\assitant_vision\clipboard_watcher.vbs

echo Creating startup shortcut...
powershell -Command "$ws = New-Object -ComObject WScript.Shell; $sc = $ws.CreateShortcut('%STARTUP_DIR%\AI-TODO-Clipboard-Watcher.lnk'); $sc.TargetPath = '%VBS_FILE%'; $sc.WorkingDirectory = 'd:\Study\assitant_vision'; $sc.Save()"

echo.
echo Done! AI TODO helper will start automatically when you log in.
echo.
echo To test now: run.bat
echo To disable: Press Win+R, type "shell:startup", delete the shortcut.
pause
