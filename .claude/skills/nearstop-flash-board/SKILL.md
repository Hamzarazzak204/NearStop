---
name: nearstop-flash-board
description: Identify which ESP32 is on which COM port and upload the right NearStop firmware (Robot Arm GW / Conveyor MA / Hoist MB / worker strap W1), then verify it reports. Use for "flash", "upload", "make this board the hoist", or a board showing offline after changes.
---

# Flash a NearStop board

## 1. Free the ports
The USB bridge and any Serial Monitor hold the COM ports. Stop the bridge (processes with `usb_bridge.py` in the command line, and the `cmd.exe` running `NearStop USB Bridge.bat`). Close the Arduino IDE's Serial Monitor.

## 2. Identify boards (never guess; ports renumber on replug)
```powershell
foreach ($p in [System.IO.Ports.SerialPort]::GetPortNames()) {
  $o = New-Object System.IO.Ports.SerialPort $p,115200; $o.ReadTimeout=200
  try { $o.Open(); $o.DtrEnable=$false; $o.RtsEnable=$false   # does NOT reset the board
    $end=(Get-Date).AddSeconds(3); $ls=@(); while((Get-Date) -lt $end){ try { $ls += $o.ReadLine().Trim() } catch {} }
    $o.Close(); "== $p"; $ls | Where-Object { $_ } | Select-Object -Last 2 } catch { "== $p busy" }
}
```
Machine lines show `"id":"GW"`/`"MB"`; the strap prints `g 1.05 | zone ...`. Garbage → reset it (RTS pulse) and read boot lines. Unknown or a teammate's board → back it up first: `.venv\Scripts\python.exe -m esptool --port COMx read-flash 0x0 0x150000 backup.bin` (pip install esptool if needed).

## 3. Upload
| Board | Sketch | ID |
|---|---|---|
| Robot Arm (with 5 V motor on A1, or none) | `firmware\motor_node` | `--build-property "build.extra_flags=-DDEVICE_ID=\"GW\""` (radio presence; add `-DPRESENCE_STOP=1` only for the old slow/stop-on-presence rules) |
| Conveyor (with 5 V motor on A1) | `firmware\motor_node` | `...-DDEVICE_ID=\"MA\""` (defaults: MA, PRESENCE_STOP 0 = ignores presence) |
| Robot Arm / Conveyor, sensor only | `firmware\machine_node_usb` | same `DEVICE_ID` flag |
| Hoist (motor) | `firmware\hoist_node` | MB (config.h) |
| Worker strap | `firmware\worker_strap` | W1 (config.h); needs Adafruit NeoPixel |
```powershell
arduino-cli compile --fqbn esp32:esp32:adafruit_feather_esp32_v2 -u -p COM4 firmware\hoist_node
```
"Verification failed after fast reflash… Reflashing the whole image" is harmless. In PowerShell the `-DDEVICE_ID` quoting is `'build.extra_flags=-DDEVICE_ID=\"MA\"'` in single quotes.

## 4. Verify
1. Read serial (step 2 snippet): machines print JSON every 200 ms (`#W {...}` = sent over Wi-Fi), the strap a status line every second.
2. Restart the bridge; `GET /api/state` → the device `online=true`, `ip` 127.0.0.1 (USB) or 172.20.x.x (Wi-Fi).
3. Machines start Running only when their area is clear; a latched state needs Approve restart.

## Don'ts
- Don't flash GW/MA firmware onto the Hoist: its pins differ (motor on A0, TRIG on A1).
- Don't leave diagnostics firmware (A0 PWM test, sniffer) on a board: reflash the real sketch and say so.
