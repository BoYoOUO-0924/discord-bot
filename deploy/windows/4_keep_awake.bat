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
rem Ask Windows directly whether this laptop uses Modern Standby (AoAc in SYSTEM_POWER_CAPABILITIES)
powershell -NoProfile -Command "$p = Add-Type -MemberDefinition '[DllImport(\"powrprof.dll\")] public static extern bool GetPwrCapabilities(byte[] c);' -Name Pwr -Namespace GawaBot -PassThru; $c = New-Object byte[] 128; [void]$p::GetPwrCapabilities($c); exit $c[20]"
if not "%errorlevel%"=="1" (
    echo.
    echo This laptop does not use Modern Standby, so nothing else needs to change.
    echo Closing the lid while plugged in will no longer put it to sleep.
    goto done
)

echo.
echo This laptop uses "Modern Standby" (S0 Low Power Idle).
echo Windows then goes into standby whenever the screen turns off,
echo for example when the lid is closed, even if the lid is set to "Do nothing".
echo The bot then goes offline.
echo.
echo Turning Modern Standby off makes the laptop use normal sleep instead,
echo so "Do nothing" really does nothing. You can undo this later with:
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
