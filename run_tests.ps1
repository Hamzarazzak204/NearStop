# =====================================================================
#  NearStop checks that need NO hardware:
#    1. backend safety/protocol tests (tests\test_nearstop.py)
#    2. every firmware sketch compiles for the Adafruit Feather ESP32 V2
#  powershell -ExecutionPolicy Bypass -File run_tests.ps1 [-SkipFirmware]
# =====================================================================
param([switch]$SkipFirmware)
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
$env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')
$failed = 0

Write-Host "`n=== Backend tests" -ForegroundColor Cyan
& .venv\Scripts\python.exe tests\test_nearstop.py
if ($LASTEXITCODE -ne 0) { $failed++ }

if (-not $SkipFirmware) {
  Write-Host "`n=== Firmware compiles (esp32:esp32:adafruit_feather_esp32_v2)" -ForegroundColor Cyan
  $sketches = @("motor_node", "machine_node_usb", "hoist_node", "worker_strap", "machine_node",
                "diagnostics\a0_pwm_test", "diagnostics\espnow_sniffer", "diagnostics\strap_sensor_check",
                "diagnostics\bmp180_test")
  foreach ($s in $sketches) {
    $dir = "firmware\$s"
    if ((Test-Path "$dir\config.example.h") -and -not (Test-Path "$dir\config.h")) { Copy-Item "$dir\config.example.h" "$dir\config.h" }
    $out = arduino-cli compile --fqbn esp32:esp32:adafruit_feather_esp32_v2 $dir 2>&1
    if ($LASTEXITCODE -eq 0) { Write-Host ("  OK    {0,-28} {1}" -f $s, (($out | Select-String "Sketch uses") -replace '\s+of program.*','')) }
    else { $failed++; Write-Host "  FAIL  $s" -ForegroundColor Red; $out | Select-String "error" | Select-Object -First 5 }
  }
}

if ($failed) { Write-Host "`n$failed check(s) FAILED" -ForegroundColor Red; exit 1 }
Write-Host "`nAll checks passed" -ForegroundColor Green
