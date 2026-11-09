# NearStop: start here

Worker-safety system: ultrasonic machine nodes (Robot Arm, Conveyor, Hoist with a motor) + a worker strap with fall detection + a laptop hub with a live dashboard.

## Humans
1. Unzip anywhere, open PowerShell in this folder.
2. `powershell -ExecutionPolicy Bypass -File setup.ps1` (installs Python packages, arduino-cli, ESP32 core, NeoPixel; creates `firmware/*/config.h`).
3. Put your 2.4 GHz Wi-Fi name/password into the three `firmware/*/config.h` files.
4. `powershell -ExecutionPolicy Bypass -File run_tests.ps1` → "All checks passed" (no hardware needed).
5. Flash the boards (see `.claude/skills/nearstop-flash-board/SKILL.md`), then double-click **NAT Basestation.bat** and **NearStop USB Bridge.bat**.

### Which firmware goes on which ESP32 (Adafruit Feather ESP32 V2)

| Board | Sketch folder | Upload command (PowerShell, in this folder; replace COM5) |
|---|---|---|
| Sam's strap (W1) | `firmware\worker_strap` | `arduino-cli compile --fqbn esp32:esp32:adafruit_feather_esp32_v2 -u -p COM5 firmware\worker_strap` |
| Conveyor (MA) | `firmware\motor_node` | `arduino-cli compile --fqbn esp32:esp32:adafruit_feather_esp32_v2 -u -p COM5 --build-property 'build.extra_flags=-DDEVICE_ID=\"MA\"' firmware\motor_node` |
| Robot Arm (GW) | `firmware\motor_node` | same, with `\"GW\"` |
| Hoist (MB) | `firmware\hoist_node` | `arduino-cli compile --fqbn esp32:esp32:adafruit_feather_esp32_v2 -u -p COM5 firmware\hoist_node` |

Before uploading: put your Wi-Fi name/password in that sketch's `config.h` (setup.ps1 creates it), close the USB bridge window, and **back up a board you didn't flash yourself**: `.venv\Scripts\python.exe -m pip install esptool` then `.venv\Scripts\python.exe -m esptool --port COM5 read-flash 0x0 0x150000 backup.bin`.

How it behaves now: machines react **only to Sam's strap** (radio beacon, no antennas needed for "nearby"), keep running while he is near, and **stop only when he falls near them**. All boards and the laptop on the same 2.4 GHz hotspot (channel 6), or plugged in with the USB bridge running; **only one NearStop per network**.

Overview: `README.md`. Full documentation: `TECHNICAL.md`. Problems already solved: `docs/LESSONS_LEARNED.md`.

## Claude (or another AI assistant)
Open this folder in Claude Code: `CLAUDE.md` loads automatically and the skills in `.claude/skills/` (setup-and-run, flash-board, test, hardware-debug, safety-rules) are available. In a chat without file access, paste `CLAUDE.md` first, then the files you're asked about.
