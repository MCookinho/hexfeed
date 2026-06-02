@echo off
REM hexfeed Windows bootstrap installer
REM Works even without Python installed.
REM Double-click or run: install.bat
setlocal

set "HEXFEED_URL=https://github.com/MCookinho/hexfeed"
set "TEMP_DIR=%TEMP%\hexfeed-install"

echo.
echo   ⬡ hexfeed -- Windows bootstrap installer
echo.

REM Check Python
where python 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo   Python not found. Downloading Python 3.12...
    curl -fsSL -o "%TEMP%\python-installer.exe" https://www.python.org/ftp/python/3.12.9/python-3.12.9-amd64.exe
    if %ERRORLEVEL% NEQ 0 (
        echo   ✗ Failed to download Python. Download manually from https://python.org
        pause
        exit /b 1
    )
    echo   Installing Python (silent)...
    start /wait "" "%TEMP%\python-installer.exe" /quiet InstallAllUsers=0 PrependPath=1
    echo   ✓ Python installed. Restart your terminal and re-run this script.
    pause
    exit /b 0
)

echo   ✓ Python found

REM Clone / download hexfeed
echo   Downloading hexfeed...
if exist "%TEMP_DIR%" rmdir /s /q "%TEMP_DIR%"
mkdir "%TEMP_DIR%"

curl -fsSL -o "%TEMP_DIR%\hexfeed.zip" "%HEXFEED_URL%/archive/refs/heads/main.zip"
if %ERRORLEVEL% NEQ 0 (
    echo   ✗ Failed to download hexfeed.
    pause
    exit /b 1
)

REM Extract zip
powershell -Command "Expand-Archive -Path '%TEMP_DIR%\hexfeed.zip' -DestinationPath '%TEMP_DIR%' -Force"

REM Find the extracted folder
for /d %%i in ("%TEMP_DIR%\hexfeed-*") do set "EXTRACT_DIR=%%i"

REM Run the Python installer
echo   Running hexfeed installer...
python "%EXTRACT_DIR%\scripts\install.py" %*

echo.
echo   ✓ Done!
pause
