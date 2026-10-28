@echo off
title Gawa Bot - Keep awake
echo This changes power settings for when the laptop is PLUGGED IN:
echo   - never sleep or hibernate
echo   - closing the lid does nothing (screen can still turn off)
echo Battery settings are not changed.
echo.
choice /m "Apply these settings"
if errorlevel 2 exit /b 0

powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0
powercfg /setactive SCHEME_CURRENT

echo.
echo Done. Keep the laptop plugged in so the bot stays online.
pause
