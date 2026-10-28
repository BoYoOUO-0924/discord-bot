@echo off
title Gawa Bot - Keep awake

rem Changing Modern Standby needs administrator rights: ask for them first
net session >nul 2>&1
if errorlevel 1 (
    echo Asking for administrator rights...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b 0
)

echo This changes power settings for when the laptop is PLUGGED IN:
echo   - never sleep or hibernate
echo   - closing the lid does nothing
echo Battery settings are not changed.
echo.
choice /m "Apply these settings"
if errorlevel 2 exit /b 0

powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0
powercfg /setactive SCHEME_CURRENT

echo.
echo ------------------------------------------------------------------
echo Sleep states on this laptop:
powercfg /a
echo ------------------------------------------------------------------
echo.
echo Many newer laptops use "Modern Standby" (S0 Low Power Idle).
echo On those laptops Windows goes into standby whenever the screen turns off,
echo for example when the lid is closed, even if the lid is set to "Do nothing".
echo The bot then goes offline.
echo.
echo If the list above shows "S0 Low Power Idle" as AVAILABLE, turn Modern Standby off.
echo The laptop will use normal sleep instead, so "Do nothing" really does nothing.
echo You can undo this later with:
echo     reg delete HKLM\SYSTEM\CurrentControlSet\Control\Power /v PlatformAoAcOverride /f
echo.
choice /m "Turn off Modern Standby"
if errorlevel 2 goto done

reg add HKLM\SYSTEM\CurrentControlSet\Control\Power /v PlatformAoAcOverride /t REG_DWORD /d 0 /f
if errorlevel 1 (
    echo [ERROR] Could not change the setting.
) else (
    echo.
    echo Modern Standby is off. RESTART the laptop for this to take effect.
)

:done
echo.
echo Done. Keep the laptop plugged in so the bot stays online.
pause
