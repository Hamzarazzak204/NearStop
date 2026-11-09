---
name: nearstop-setup-and-run
description: Set up NearStop on a Windows PC and run it (backend + dashboard + USB bridge). Use when starting fresh on a new machine, when the dashboard shows boards "never seen"/offline, or when asked to start/restart NearStop.
---

# Set up and run NearStop

## First time on a PC
1. `powershell -ExecutionPolicy Bypass -File setup.ps1` (Python venv + packages, arduino-cli, ESP32 core 3.3.12, Adafruit NeoPixel, creates `firmware/*/config.h` from the examples).
2. Put the **2.4 GHz** Wi-Fi name/password in `firmware/machine_node_usb/config.h`, `firmware/hoist_node/config.h`, `firmware/worker_strap/config.h` (iPhone names use `\xE2\x80\x99` for ’). Never commit them.
3. `powershell -ExecutionPolicy Bypass -File run_tests.ps1` must say "All checks passed".
4. If `.venv` came from another PC (copied folder), delete it and rerun setup.ps1. Never run `python -m venv` over an existing venv.

## Run
1. Double-click **`NAT Basestation.bat`** (backend + dashboard window; also on http://localhost:8000). Only one backend at a time (ports 8000/5005).
2. Double-click **`NearStop USB Bridge.bat`** for boards plugged into this PC. Boards on other power use Wi-Fi.
3. Check: `Invoke-RestMethod http://127.0.0.1:8000/api/state` → `devices[].online`, `site.machines[].display_state`.

## Restart (after editing backend files)
```powershell
$ns = (Get-Location).Path
Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'pythonw.exe' -and $_.ExecutablePath -like "$ns\.venv\*" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Start-Process -FilePath "$ns\NAT Basestation.bat" -WorkingDirectory $ns -WindowStyle Hidden
```
Stop the bridge the same way (processes whose CommandLine contains `usb_bridge.py`, and the `cmd.exe` running `NearStop USB Bridge.bat`). Hard-refresh the dashboard afterwards (cached JS).

## Wi-Fi path checklist
- Laptop on the same 2.4 GHz network as the boards (phone hotspot; **not eduroam**): `netsh wlan show interfaces`.
- Windows Firewall allows NearStop's Python (Public networks). Boards broadcast reports, so the laptop IP may change freely.
- Boards without antennas must be close to the phone.

## Clearing a test / incident (only when the user asks)
- Answer check-in: `POST /api/checkin/resolve {"worker_id":"W1","outcome":"ok"}`; SOS: `POST /api/emergency/resolve {"worker_id":"W1"}`.
- Restart a machine: `POST /api/machines/MB/restart` (refused with a reason if the zone isn't clear, sensor not reading, or a fall check-in is open; relay the reason to the user).
