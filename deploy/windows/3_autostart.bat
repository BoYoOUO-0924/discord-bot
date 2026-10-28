@echo off
title Gawa Bot - Auto start
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "TARGET=%~dp02_start.bat"

> "%STARTUP%\GawaBot.bat" (
    echo @echo off
    echo start "Gawa Discord Bot" /min "%TARGET%"
)

if exist "%STARTUP%\GawaBot.bat" (
    echo Auto start is ON. The bot will start minimized every time you sign in to Windows.
    echo To turn it off, run remove_autostart.bat
) else (
    echo [ERROR] Could not write to the Startup folder:
    echo     %STARTUP%
)
pause
