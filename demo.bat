@echo off
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src;%PYTHONPATH%"
if not exist ".venv\Scripts\python.exe" (
    echo Project virtual environment not found. Complete the README installation first.
    pause
    exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" -m safeslicesql.desktop
