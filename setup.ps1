# =====================================================================
#  NearStop one-time setup (Windows PowerShell 5.1+)
#    powershell -ExecutionPolicy Bypass -File setup.ps1
#  Installs everything needed to run NearStop, its tests and to build/flash
#  the ESP32 firmware. Safe to run again: it skips what is already there.
# =====================================================================
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
function Step($t) { Write-Host "`n=== $t" -ForegroundColor Cyan }

Step "Python environment (.venv)"
$py = $null
foreach ($c in @("py -3", "python")) {
  try { $v = & cmd /c "$c --version" 2>$null; if ($LASTEXITCODE -eq 0 -and $v -match "Python 3\.(1[1-9]|[2-9]\d)") { $py = $c; break } } catch {}
}
if (-not $py) { throw "Python 3.11+ not found. Install it from https://www.python.org/downloads/ and run this again." }
if (-not (Test-Path ".venv\Scripts\python.exe")) { & cmd /c "$py -m venv .venv" }
& .venv\Scripts\python.exe -m pip install -q --disable-pip-version-check -r backend\requirements.txt -r requirements-desktop.txt
& .venv\Scripts\python.exe -c "import fastapi, uvicorn, numpy, serial; print('python packages OK')"

Step "Arduino CLI + ESP32 core + NeoPixel library"
$env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')
if (-not (Get-Command arduino-cli -ErrorAction SilentlyContinue)) {
  Write-Host "Installing arduino-cli with winget..."
  winget install --id ArduinoSA.CLI -e --accept-package-agreements --accept-source-agreements
  $env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')
}
# (run through cmd: PowerShell 5.1 treats harmless messages on stderr, like
#  "config file already exists", as fatal errors)
cmd /c "arduino-cli config init >nul 2>nul"
cmd /c "arduino-cli config add board_manager.additional_urls https://espressif.github.io/arduino-esp32/package_esp32_index.json >nul 2>nul"
cmd /c "arduino-cli core update-index >nul 2>nul"
if (-not (cmd /c "arduino-cli core list 2>nul" | Select-String "esp32:esp32")) { cmd /c "arduino-cli core install esp32:esp32@3.3.12" }
if (-not (cmd /c "arduino-cli lib list 2>nul" | Select-String "Adafruit NeoPixel")) { cmd /c "arduino-cli lib install ""Adafruit NeoPixel""" }
arduino-cli core list | Select-String "esp32"

Step "Firmware settings (config.h from config.example.h)"
foreach ($sketch in "motor_node", "machine_node_usb", "hoist_node", "worker_strap") {
  $dir = "firmware\$sketch"
  if (-not (Test-Path "$dir\config.h")) {
    Copy-Item "$dir\config.example.h" "$dir\config.h"
    Write-Host "created $dir\config.h  <- put your 2.4 GHz Wi-Fi name/password in it" -ForegroundColor Yellow
  } else { Write-Host "$dir\config.h already exists (kept)" }
}

Step "Done"
Write-Host "Next:  powershell -ExecutionPolicy Bypass -File run_tests.ps1   (no hardware needed)"
Write-Host "Then:  double-click 'NAT Basestation.bat', then 'NearStop USB Bridge.bat'"
