@echo off
rem ------------------------------------------------------------------
rem  NAT Basestation - double-click to start the desktop app.
rem  First run: creates the Python environment and installs packages.
rem ------------------------------------------------------------------
cd /d "%~dp0"

if exist ".venv\Scripts\pythonw.exe" goto run

echo First-time setup: creating Python environment...
rem Note: "python" on PATH may be the Microsoft Store placeholder, so it is tried last.
set "PY="
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY="%LOCALAPPDATA%\Programs\Python\Python312\python.exe""
if not defined PY where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (
    echo Python 3.11+ was not found. Install it from https://www.python.org/downloads/
    pause
    exit /b 1
)
%PY% -m venv .venv || (echo Could not create .venv & pause & exit /b 1)
".venv\Scripts\python.exe" -m pip install -r backend\requirements.txt -r requirements-desktop.txt || (echo Package install failed & pause & exit /b 1)

:run
start "" ".venv\Scripts\pythonw.exe" desktop_app.py
