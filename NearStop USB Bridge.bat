@echo off
rem ------------------------------------------------------------------
rem  NearStop USB Bridge - connects machine boards plugged in by USB
rem  (firmware/machine_node_usb) to NearStop. Start "NAT Basestation.bat"
rem  first. Close this window before uploading new firmware to a board.
rem ------------------------------------------------------------------
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Run "NAT Basestation.bat" once first so the Python environment is set up.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -c "import serial" 2>nul || ".venv\Scripts\python.exe" -m pip install pyserial
".venv\Scripts\python.exe" tools\usb_bridge.py %*
pause
