@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    py -3.11 -m venv .venv
    if errorlevel 1 exit /b %errorlevel%
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    if errorlevel 1 exit /b %errorlevel%
    ".venv\Scripts\python.exe" -m pip install -e .
    if errorlevel 1 exit /b %errorlevel%
)
".venv\Scripts\python.exe" -m detect_app
endlocal
