@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Complete the README installation first.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m safeslicesql.cli demo
if errorlevel 1 pause
