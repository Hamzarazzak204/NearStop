# Prompt for Claude: integrate the smart hard hat system

**How to use this package**
- **Claude.ai (browser):** upload `HardHat_Integration.zip`, then paste everything below the line.
- **Claude Code:** unzip it, open Claude Code inside the unzipped `HardHat_Integration` folder, then paste everything below the line.

This package is a snapshot taken 2026-11-07. Whichever copy you edit becomes the team's main copy, so don't keep editing two copies.

---

You're helping a team at **natHacks 2026** (64-hour hackathon, University of Alberta). Submissions are due **Nov 9, 2026, 11:00 AM MDT**, so time is short. Give complete, working files, explain wiring simply, and flag risks before they bite. The theme is Construction & Manufacturing, Standard Division.

If you can't edit files directly (for example in claude.ai), give me **complete files** I can paste in, and say which path each one replaces. Don't give fragments.

## The package (all paths are relative to the `HardHat_Integration` folder)
```
HardHat_Integration/
├── INTEGRATION_PROMPT.md                    (this file)
├── Part1_Machine_Motor_Ultrasonic/          motor + HC-SR04 presence stop (HARDWARE-TESTED)
│   ├── Part1_Machine_Motor_Ultrasonic.ino
│   └── WIRING.md
├── Part2_HardHat_Buzzer/                    hat: fall detection + 7 s buzzer + cancel button
│   ├── Part2_HardHat_Buzzer.ino, config.h
│   └── WIRING.md
└── NAT_Basestation/                         the full system (backend, dashboard, firmware)
    ├── README.md                            read first; section 7 = machine nodes
    ├── backend/      main.py, safety.py, device_registry.py, udp_receiver.py, positioning.py, config.py, simulator.py
    ├── frontend/     index.html, app.js, machines.js, style.css
    ├── firmware/
    │   ├── node/     anchor/worker firmware (GW uses this)
    │   ├── machine/  machine node MA/MB (compiles, NOT yet run on hardware)
    │   ├── hat/      real hat firmware (= Part 2)
    │   └── hat_sim/  sensorless test hat
    ├── NAT Basestation.bat, desktop_app.py  Windows desktop app
    └── requirements-desktop.txt
```

## Read these first, in this order
1. `NAT_Basestation/README.md`
2. `Part1_Machine_Motor_Ultrasonic/WIRING.md` and its `.ino`
3. `Part2_HardHat_Buzzer/WIRING.md` and its `.ino`
4. `NAT_Basestation/firmware/machine/machine.ino`
5. `NAT_Basestation/backend/safety.py`, `main.py`, `device_registry.py`, `udp_receiver.py`, then `NAT_Basestation/frontend/machines.js`

## The project
A smart hard hat plus machine nodes. If a worker falls, **only the machines near that worker stop**; the rest of the site keeps running. Pitch it as an *extra* safety layer on top of certified e-stops and lockout/tagout (LOTO), not a replacement.

- Location means **RSSI zones** ("near Machine A"), not GPS.
- "Oxygen" means **blood SpO2** from a pulse sensor, not oxygen in the air.

## What's tested and what isn't
| Piece | Status |
|---|---|
| Motor PWM + MOSFET + Zener flyback | Hardware-tested |
| HC-SR04 presence stop (Part 1) | Hardware-tested: readings correct, motor stops within 1 m |
| Buzzer circuit (CEM-1203 via MOSFET on GPIO27) | Hardware-tested with a 2048 Hz test tone |
| Hat sketch (Part 2) | Compiles and boots. Fall flow only triggered from Serial. **MPU-6050 not wired yet** |
| `firmware/machine` | Compiles only |
| Backend machine safety + hat alerts + dashboard panel | Tested with fake packets on a laptop, not with real boards |
| GW + W1 positioning over the hotspot | Tested earlier by the team |

## How the system works (don't redesign it)
- All boards are **Adafruit Feather ESP32 V2** running Arduino-ESP32 core **3.3.12**. Use `ledcAttach(pin, freq, bits)` / `ledcWrite(pin, duty)` / `ledcWriteTone`, and the core 3.x ESP-NOW receive callback `(const esp_now_recv_info_t*, const uint8_t*, int)`.
- **Every board joins the same Wi-Fi network**, the iPhone hotspot (SSID and password in each `config.h`). ESP-NOW automatically uses that network's channel. Don't use a fixed channel without joining Wi-Fi: the boards stop hearing each other.
- Boards send **UDP JSON to the backend** at `PI_IP:5005` (`PI_IP` is in each `config.h`). The backend (FastAPI: `python backend/main.py`, or double-click `NAT Basestation.bat` on Windows) serves the dashboard on port 8000.
- Device IDs: `GW`, `MA`, `MB` = anchors (MA/MB are also the machines), `W1` = worker hat.
- **Worker hello (ESP-NOW broadcast, every 200 ms):**
  `{"type":"hello","id":"W1","role":"worker","seq":N,"fall":0,"pend":0,"alert":0}`
  - `pend=1` means a possible fall: the 7 s cancel window is running.
  - `fall=1` means a confirmed fall: it wasn't cancelled.
  - `alert` is reserved for vitals/gas.
- **Hat events (UDP to the backend, sent 3x, dedupe by `eseq`):** `{"type":"hat_event","id":"W1","event":"pending|cancelled|fall|cleared","eseq":N}`. They show as a banner on the dashboard's Live tab.
- **Machines:**
  - They report status inside their UDP heartbeat as a `"machine":{...}` object.
  - The backend sends `{"type":"base_hb"}` every 1 s to each machine's IP on **UDP 5006**. A machine stops if it misses that for 3 s.
  - Commands: `{"type":"cmd","target":"MA","cmd":"approve|stop|lock|unlock","seq":N,"stop_id":S,"worker":"W1"}`. Machines only accept them from `PI_IP`.
- **Every machine stop is latched.** A machine restarts only on an `approve` carrying the current `stop_id`, from the dashboard's Machines panel or `POST /api/machines/{id}/approve`. Machines also boot stopped.
- **The firmware's tiny JSON parser needs compact JSON**: no spaces, i.e. `json.dumps(..., separators=(",",":"))`.

## Your task: integrate into one system
1. **Merge Part 1 into `NAT_Basestation/firmware/machine/machine.ino`.** Add the HC-SR04 presence stop to the machine node:
   - Person within `STOP_CM` (1 m) → `latchStop(R_PRESENCE, ...)`. Add a new `Reason` and add it to `REASON_N`.
   - It must **latch like every other stop**. Remove Part 1's 2 s auto-restart; restart only via approve.
   - Sensor fault (no echo pulse at all) → stop, with its own reason. `tryApprove()` must refuse while a person is in range or the sensor is faulted.
   - Keep Part 1's **interrupt-based** echo timing. Never use blocking `pulseIn()` in the machine loop.
   - Report the distance and sensor state in the heartbeat's `machine` object, and show them on the dashboard card (`NAT_Basestation/frontend/machines.js`).
2. **The fall + near → stop logic already exists** in `firmware/machine/machine.ino` (`handleHello`, `isClose`, `NEAR_MEMORY_MS`). Check it against the hat's real hello format and keep it.
3. **Part 2 = `NAT_Basestation/firmware/hat` is the real W1 firmware**, replacing `firmware/hat_sim` and `firmware/node` on the worker board. Keep `firmware/hat` as the source of truth.
4. **Later, not now unless asked:** MAX30102 HR/SpO2 and MQ-2 gas sensors on the hat setting `alert:1`; vibration motor for the proximity warning; full LOTO layer; signed commands (the team has crypto chips).

## Pin rules (do not break)
| Board | Pin | Use |
|---|---|---|
| Machine | GPIO26 (A0) | motor PWM, 20 kHz, 8-bit |
| Machine | GPIO25 (A1) | HC-SR04 Trig |
| Machine | GPIO34 (A2) | HC-SR04 Echo, through a 10k/20k divider (input-only pin) |
| Machine / hat | GPIO13 | red LED |
| Machine / hat | GPIO0 | NeoPixel |
| Machine / hat | GPIO2 | NeoPixel / STEMMA I2C power |
| Hat | GPIO27 | buzzer, through a MOSFET |
| Hat | GPIO14 | cancel button to GND |
| Hat | GPIO38 | on-board SW38 button |
| Hat | SDA/SCL | MPU-6050, `Wire.begin()` with no args |

- Analog reads only on ADC1 (GPIO32–39). ADC2 doesn't work while Wi-Fi is on.
- Keep outputs off boot-strapping pins 0, 2, 5, 12, 15 (except the NeoPixel, which is fine).
- MQ-2: analog out through a 10k/20k divider. Never connect its DO pin.
- Every MOSFET gate gets 220 Ω in series and a 10 kΩ to GND.

## Build / upload / test (Windows laptop with Arduino IDE 2.3.10)
- Compile and upload with the IDE's bundled CLI:
  `"%LOCALAPPDATA%\Programs\Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe" compile --fqbn esp32:esp32:adafruit_feather_esp32_v2 -u -p COM6 <sketch folder>`
  Use port COM6 (it was COM5 once). In the IDE: board "Adafruit Feather ESP32 V2". Each `.ino` must sit in a folder with the same name.
- Read serial from PowerShell without resetting the board: `System.IO.Ports.SerialPort` at 115200 with DTR/RTS false.
- Backend setup: Python 3.11+. Create a venv with `python -m venv .venv`, then `.venv\Scripts\python.exe -m pip install -r backend\requirements.txt -r requirements-desktop.txt`, then run `backend\main.py`. Restart the backend after editing backend files. (`.venv` isn't in the package; it only works on the PC that built it.)
- Compile every sketch you touch. Test backend changes by feeding fake JSON to `UDPReceiver.datagram_received` in-process, or with a fake machine on UDP 5006.
- Say clearly what was hardware-tested and what only compiled.

## Gotchas already hit (don't repeat them)
- **Arduino auto-prototypes:** a function that takes a struct defined later fails to compile. Add explicit prototypes after the struct.
- **Name clash:** `PENDING` collides with an ESP32 core enum. Prefix your enum values (`HAT_PENDING`).
- **Short circuits:** a brownout reset loop (`E BOD: Brownout detector was triggered`) means a coil circuit is shorting 3V. It happened with the buzzer's diode band on the wrong side.
- **Floating MOSFET gate (no 10 kΩ):** the buzzer hums and the motor runs by itself.
- **Magnetic buzzer:** a steady HIGH only clicks. It needs a tone (2048 Hz).
- **The laptop must be on the hotspot**, not campus Wi-Fi (eduroam), and its IP must equal `PI_IP` in every `config.h` (check the dashboard's Setup tab). Allow Python through Windows Firewall on Private + Public.
- **Only one backend at a time.** Boards send to a single IP.
- **The HC-SR04 sees walls.** Long ranges, like 5 m, keep the motor stopped forever. Keep it about 1 m and aim it at open space.
- **Never use blocking calls in the machine loop:** no `readStringUntil`, `pulseIn` or long `delay()`. A watchdog reboots the board if the loop stalls for 3 s.

## Risks to flag in the pitch / demo
- The hotspot is part of the safety chain. If it drops, every machine stops within about 3 s. That's the fail-safe, but at a demo it halts everything. Keep the phone awake and plugged in.
- RSSI near/far thresholds (−55/−65 dBm) are guesses. Calibrate on site with the hat walked toward each machine.
- Commands aren't signed yet. Anything sending from `PI_IP` is trusted.
- The ultrasonic beam is narrow (about 15°), and soft clothing can absorb echoes.
