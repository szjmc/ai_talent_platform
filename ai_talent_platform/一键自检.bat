@echo off
chcp 65001 >nul 2>&1
title Training Module Selfcheck
set "PY=C:\Users\Windows\anaconda3\envs\py311\python.exe"
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"

if not exist "%PY%" (
    echo.
    echo [ERROR] Python interpreter not found:
    echo     %PY%
    echo Please confirm the conda env "py311" still exists.
    echo.
    pause
    exit /b 1
)

echo.
echo   Training Module Selfcheck
echo   Interpreter : %PY%
echo   Project dir : %~dp0
echo.
echo   Press any key to start, or close this window to cancel.
pause >nul

rem Pass through arguments: double-click = full check,
rem or run this file from cmd with --fast / --verbose
"%PY%" scripts\selfcheck.py %*

echo.
echo   Done. Window will stay open so you can read the result.
pause
