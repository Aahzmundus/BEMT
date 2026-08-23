@echo off
title BEMT - Benji Eve Market Tool
cd /d %~dp0

if not exist .venv (
    echo.
    echo   First run - setting up. This takes a minute, only happens once.
    echo.
    py -3.12 -m venv .venv 2>nul || py -3 -m venv .venv 2>nul || python -m venv .venv
    if not exist .venv\Scripts\python.exe (
        echo.
        echo   ERROR: Python was not found.
        echo   Install Python 3.11 or newer from https://www.python.org/downloads/
        echo   and tick "Add python.exe to PATH" during the install, then run this again.
        echo.
        pause
        exit /b 1
    )
    call .venv\Scripts\python -m pip install --upgrade pip
    call .venv\Scripts\pip install -e .
)

echo.
echo   Starting BEMT... your browser will open at http://localhost:8425
echo   Leave this window open while you use it. Close it to stop BEMT.
echo.
.venv\Scripts\python -m bemt
pause
