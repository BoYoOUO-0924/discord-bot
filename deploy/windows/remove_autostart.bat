@echo off
title Gawa Bot - Remove auto start
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
if exist "%STARTUP%\GawaBot.bat" del "%STARTUP%\GawaBot.bat"
echo Auto start is OFF. The bot will no longer start when you sign in.
pause
