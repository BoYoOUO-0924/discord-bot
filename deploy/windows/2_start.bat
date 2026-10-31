@echo off
title Gawa Discord Bot
cd /d "%~dp0..\.."

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv not found. Run 1_install.bat first.
    pause
    exit /b 1
)
if not exist ".env" if not exist "config.py" (
    echo [ERROR] .env not found. Run 1_install.bat first.
    pause
    exit /b 1
)

rem Turn on auto update from GitHub (only versions that passed the GitHub tests are installed)
set "AUTO_UPDATE=1"

echo Gawa Bot is running. It restarts automatically if it stops,
echo and updates itself when a new version is pushed to GitHub.
echo Close this window to stop the bot. Log file: logs\bot.log
echo.

:loop
rem Install any new packages that a new version may need (fast when nothing changed)
".venv\Scripts\python.exe" -m pip install -r requirements.txt --quiet --disable-pip-version-check
".venv\Scripts\python.exe" bot.py
if "%errorlevel%"=="3" (
    echo [%date% %time%] Updated to a new version. Restarting now...
    goto loop
)
echo.
echo [%date% %time%] Bot stopped with exit code %errorlevel%. Restarting in 10 seconds...
echo Close this window now if you want to stop it.
ping -n 11 127.0.0.1 >nul
goto loop
