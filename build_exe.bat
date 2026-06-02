@echo off
cd /d "%~dp0"

echo ================================
echo   AI TODO Assistant - Build
echo ================================
echo.

echo [1/2] Installing PyInstaller...
pip install pyinstaller >nul 2>&1

echo [2/2] Building exe...
pyinstaller --onefile --console --name "AI-TODO-Assistant" --hidden-import=win32clipboard --hidden-import=PIL --hidden-import=openai --hidden-import=keyboard --hidden-import=dotenv --hidden-import=win10toast hotkey_screenshot.py

if exist "dist\AI-TODO-Assistant.exe" (
    echo.
    echo ================================
    echo   Build success!
    echo   exe: dist\AI-TODO-Assistant.exe
    echo ================================
    echo.

    if not exist "release" mkdir "release"
    copy /y "dist\AI-TODO-Assistant.exe" "release\" >nul
    copy /y "user_config.json" "release\" >nul
    copy /y "env_template" "release\.env" >nul

    echo Package ready in release\ :
    dir /b "release\"
    echo.
    echo Zip the release folder and share it!
) else (
    echo Build failed, check errors above.
)

pause
