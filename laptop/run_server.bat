@echo off
setlocal EnableExtensions EnableDelayedExpansion
title LanLink laptop server
cd /d "%~dp0"

echo Starting LanLink from %CD%
echo Keep this window open while the phone is connected.
echo.

python --version >nul 2>&1
if not errorlevel 1 (
    python server.py %*
    set "EXIT_CODE=!ERRORLEVEL!"
) else (
    py -3 --version >nul 2>&1
    if not errorlevel 1 (
        py -3 server.py %*
        set "EXIT_CODE=!ERRORLEVEL!"
    ) else (
        echo Python was not found. Install Python 3.10+ and try again.
        set "EXIT_CODE=1"
    )
)

if not "%EXIT_CODE%"=="0" (
    echo.
    echo LanLink stopped with code %EXIT_CODE%.
    echo If the port is busy, close the other LanLink window or run:
    echo     python server.py --port 8766
)
echo.
pause
exit /b %EXIT_CODE%
