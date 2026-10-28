@echo off
title Gawa Bot - Install
cd /d "%~dp0..\.."

echo [1/3] Checking Python...
set "PY="
py -3 --version >nul 2>nul && set "PY=py -3"
if not defined PY python --version >nul 2>nul && set "PY=python"
if not defined PY (
    echo [ERROR] Python is not installed.
    echo Install Python 3.12 from https://www.python.org/downloads/
    echo and tick "Add python.exe to PATH" during setup. Or run this command:
    echo     winget install Python.Python.3.12
    pause
    exit /b 1
)
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 (
    echo [ERROR] Python 3.10 or newer is required. Your version:
    %PY% --version
    pause
    exit /b 1
)

git --version >nul 2>nul
if errorlevel 1 (
    echo [WARNING] Git is not installed, so auto update will not work.
    echo Install it with:  winget install Git.Git
    echo.
) else if not exist ".git" (
    echo [WARNING] This folder was not downloaded with "git clone", so auto update will not work.
    echo.
)

echo [2/3] Creating virtual environment .venv ...
if not exist ".venv\Scripts\python.exe" %PY% -m venv .venv
if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Failed to create the virtual environment.
    pause
    exit /b 1
)

echo [3/3] Installing packages...
".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Package installation failed. Check the internet connection and try again.
    pause
    exit /b 1
)

if not exist "config.py" (
    copy /y config_example.py config.py >nul
    echo.
    echo config.py has been created. Fill in DISCORD_TOKEN and GEMINI_API_KEY, then save it.
    start "" notepad config.py
)

echo Creating desktop shortcut "Gawa Bot"...
powershell -NoProfile -Command "$s = (New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop') + '\Gawa Bot.lnk'); $s.TargetPath = '%~dp02_start.bat'; $s.WorkingDirectory = '%~dp0'; $s.Save()"

echo.
echo Install finished. Next step: double-click "Gawa Bot" on the desktop (or 2_start.bat)
pause
