@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [setup] Creating virtual environment...
    python -m venv .venv || goto :fail
    ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :fail
)

echo [bot] Starting. Press Ctrl+C to stop.
".venv\Scripts\python.exe" -m bot %*
set EXITCODE=%ERRORLEVEL%
echo.
echo [bot] Stopped with code %EXITCODE%.
pause
exit /b %EXITCODE%

:fail
echo [setup] Failed. Make sure Python 3.11+ is installed and available as "python".
pause
exit /b 1
