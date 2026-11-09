# NearStop: handoff (as of 2026-11-09, morning of the natHacks deadline)

You are taking over **NearStop** from Zeeshan's session. Read this file, then `CLAUDE.md`, `TECHNICAL.md`, `docs/LESSONS_LEARNED.md`. This file is the **current truth**; where older notes disagree, this wins.

> **Secrets in this folder (do not publish):** `firmware/*/config.h` contain the real hotspot password (git-ignored), and `firmware/backups/*.bin` is a flash dump that also contains it (git-ignored). Never commit or post them.

## 1. What the demo is now

| ID | Device | Firmware | Status |
|---|---|---|---|
| **W1** | **Sam's worker strap** (Feather ESP32 V2) | `firmware/worker_strap` | Working on Wi-Fi; sends to the laptop at `172.20.10.2` |
| **MA** | **Conveyor** (DC motor + HC-SR04) | `firmware/motor_node` (`DEVICE_ID "MA"`) | Working on Wi-Fi (usually `172.20.10.6`) |
| **MB** | **Hoist** (**SG90 servo** + HC-SR04, Sepehr's board) | `firmware/hoist_node` with `SERVO_MOTOR 1` | On Wi-Fi (usually `172.20.10.3`); servo motion not yet confirmed by eye |
| ~~GW~~ | Robot Arm | removed from the live site | still in the tests' own config |

- **Hub:** the laptop runs NearStop (FastAPI backend + dashboard). Open it with `desktop_app.py` or `NAT Basestation.bat` **in this folder only**. The old `Downloads\anchorv1\NearStop` copy is superseded and doesn't run on this laptop.
- **Network:** iPhone hotspot "Rafat's iPhone", 2.4 GHz, channel 6. The laptop is `172.20.10.2`. **Run only ONE NearStop on the hotspot.**
- **USB fallback:** `NearStop USB Bridge.bat` carries boards over USB cables if Wi-Fi fails. Close it before uploading firmware (it holds the COM ports).

## 2. How it behaves (the rules)

- **Only Sam counts (radio presence / "anchors").**
  - The strap broadcasts an ESP-NOW beacon `NSB:W1` 10 times a second on channel 6.
  - Each machine reports `worker_near` plus `worker_rssi` (signal strength).
  - Objects in front of the ultrasonic are ignored.
  - NearStop counts Sam as near a machine (dashboard "Sam nearby", fall lockout) when the machine hears his strap AND either:
    - its **2-second average RSSI** is at least that machine's `near_rssi_dbm` (in `backend/site_config.json`; **-94** for MA and MB since 2026-11-09, default -127 = any beacon), or
    - the signal is weaker, but the **ultrasonic sensor sees someone within the warning distance** (150 cm) at the same time (the card then says "seen by sensor").
  - An object in front of the sensor while the strap is NOT heard is still "Clear".
- **Presence never stops a machine** (`presence_stop: false` for all).
- **Every fall raises an alert**, wherever Sam is:
  - red **FALL** event
  - red **Fall banner** with alarm for **8 s** (`NS_FALL_BANNER_S` in `frontend/site.js`)
  - 10 s **check-in**: no answer means **SOS**
  - Sam's card shows "Fell … ago"
- **Lockout:** a fall locks a machine only if that machine **hears Sam strongly at the moment of the fall** (within 1.5 s, `RADIO_FALL_MEMORY_S`).
  - The Hoist also **holds instantly** on a hard hit within 50 cm (`impact_hold`). The hold turns into a lockout if the fall is confirmed, or the Hoist resumes after 3 s.
  - **Approve restart** on the dashboard clears a lockout. It's refused while the check-in is open or Sam is still near.
- **"I'm OK" switch** (latching SPDT on the strap): **every flip** sends `worker_ok`. NearStop replies `OKACK`. It answers the check-in, and **never** unlocks a machine.
- **Reliability:** NearStop **re-sends stop/hold/lock every 1 s** until the board confirms, and adopts a `locked` a board reports after a NearStop restart.

## 3. Sam's strap: hardware and pins (Feather ESP32 V2)

| Part | Wiring |
|---|---|
| MPU-6050 (motion) | 3V, GND, SDA 22, SCL 20 (0x68) |
| BMP180 (temperature + air pressure) | VIN 3V, GND, SDA, SCL (0x77). **Replaced the DHT11** (that DHT11 was dead) |
| MQ-2 gas | VCC → USB 5 V, GND, AO → 10k → GPIO 34, GPIO 34 → 20k → GND |
| "I'm OK" **latching** switch | C (common) → GPIO 14, NO → GND (`BUTTON_LATCHING 1`) |
| Speaker (was buzzer) | GPIO 27 → 220 Ω → MOSFET gate, 10k gate → GND, source → GND, drain → speaker −, speaker + → 5 V, Schottky across the speaker (stripe to +) |
| **4-pin RGB LED** (enclosure light) | R → 220 Ω → GPIO 33, G → 100 Ω → GPIO 32, B → 100 Ω → GPIO 15, long leg → GND |
| On-board | NeoPixel (zone colour), red LED 13 (near a machine) |

**What the strap does:**
- **Falls:** detection runs on the MPU-6050. Every confirmed fall is sent. Thresholds were **made more sensitive again later on 2026-11-09**: free fall below 0.90 g for 15 ms → impact ≥ 1.8 g within 1.5 s (≥ 2.5 g without free fall) → still for 0.8 s (spread ≤ 0.30 g).
- **Bad sensor data is ignored:** readings from a dead or restarting MPU never trigger falls. If the MPU is missing, the strap reports `imu:false` and the dashboard shows "Fall detection OFF".
- **After a fall:**
  - the speaker repeats **"Are you okay?"** (`voice_are_you_ok.h`, made by `tools/make_voice.py`)
  - the **RGB LED blinks red** (150 ms on / 150 ms off)
  - both continue until the "I'm OK" switch is flipped
- **Sensor readings:** BMP180 temperature + pressure and MQ-2 gas (% of clean air; learned **8 s after power-on** (5 s warm-up + 3 s), and the clean-air value follows the heater for the first 3 minutes; or **"Sensor fault"** if it reads ~0 V) go to the dashboard's **Monitoring** tab.
- **Wi-Fi:** sends **directly to `NEARSTOP_IP "172.20.10.5"`** (the laptop's hotspot address since 2026-11-09; the machines' `SERVER_IP` is still .2, they run on USB), ignores other hubs, and sends status twice a second.
- **Beacon start:** the beacon starts **after** Wi-Fi joins (starting it earlier stalls the Wi-Fi join).
- **Serial test commands** (115200 baud):
  - `f` fake fall
  - `ok` fake "I'm OK"
  - `say` play the voice
  - `light` RGB red for 3 s
  - `sweep` / `hz N` speaker test tones
  - `b` / `s` re-learn clean air / skip the gas warm-up
  - `2 40` fake machine distance

## 4. Machines

- **Conveyor MA:** teammate's `motor_node`.
  - Motor on A1/GPIO 25 via MOSFET; HC-SR04 Trig A0/GPIO 26, Echo A2/GPIO 34.
  - `RUN_PCT 70`; listens for the beacon.
  - `config.h` has the hotspot settings and `SERVER_IP "172.20.10.2"`.
- **Hoist MB:** `hoist_node` with a **servo option** I added, ported from `Downloads\hoist-handoff-prompt.md`.
  - Its `config.h`: `MOTOR_PIN 25` (servo signal), `TRIG_PIN 26`, `SERVO_MOTOR 1`, `SERVO_360 0`, `SERVO_SPEED 12.0f`, `FAULTS_TO_ERROR 10`, plus the hotspot settings and `SERVER_IP`.
  - The servo sweeps 0→180° and snaps back while running, and **gets no pulses** on hold/stop/lock, so it freezes.
  - If an upload drops off USB, unplug the servo's red wire.

## 5. Dashboard (`frontend/`)

- **Site floor:** Conveyor and Hoist side by side.
  - Machine cards show "Sam nearby · -90 dBm".
  - Fall banner lasts 8 s.
  - Sam's card shows status, temperature/pressure, "Fall detection OFF" if the MPU is missing, and recent falls.
- **Monitoring tab:** temperature, air pressure, gas (% of clean air), with 10-minute charts.

## 6. Tested vs not

- **Tested on hardware:**
  - Strap falls reach NearStop in under 0.1 s.
  - A real 4.8 g fall near the Conveyor (heard by radio) locked it; Approve restart restored it.
  - Fall away from machines: alert and check-in, nothing locked.
  - "I'm OK" works end to end; the switch was confirmed working by the user.
  - Voice and RGB red are driven from GPIO 27 / 33-32-15. The **RGB flash was confirmed by the user**; the voice was triggered but its loudness wasn't judged.
  - BMP180 reads about 924–928 hPa and 23–25 °C.
  - The Conveyor heard the beacon at -89 to -94 dBm when the strap was right next to it.
  - All three boards worked on Wi-Fi at once.
- **Automated:** **106 backend tests pass** (`tests/test_nearstop.py`), and all 9 firmware sketches compile.
- **Not working or not confirmed:**
  1. **Radio range is tiny (most important).** No antennas are fitted (PICO-MINI-**02U**), so the beacon carries only a few tens of cm. In the last demo try, neither machine heard Sam "close by" (`heard=False`). **Fix: fit 2.4 GHz u.FL antennas** on the strap and both machines, then tune `near_rssi_dbm`. Without antennas, set `near_rssi_dbm` back to -127 and keep the strap within ~20 cm of the machine's board.
  2. **iPhone hotspot drops or refuses the boards** sometimes. Keep the phone close, keep the Personal Hotspot screen open with Maximise Compatibility on, limit connected devices (~5 max), and re-plug the board.
  3. **MQ-2 reads ~0 V** (power/wiring), so it shows Sensor fault.
  4. The **Hoist servo** motion after the latest upload hasn't been confirmed by eye.
  5. The **speaker is quiet**. It's a hardware limit: a non-logic-level MOSFET or the 3V supply. Fix: speaker + to 5 V, a logic-level MOSFET, or a PAM8403 amplifier.
  6. The KY-034 7-colour LED module tried earlier was dead (never lit, even on 5 V). Replaced by the RGB LED.

## 7. How to run, test, flash (Windows)

- Run:
  - `NAT Basestation.bat` (creates `.venv` the first time) or `desktop_app.py` in this folder
  - then, only if using USB, `NearStop USB Bridge.bat`
- Tests: `.venv\Scripts\python.exe tests\test_nearstop.py`, or `powershell -ExecutionPolicy Bypass -File run_tests.ps1` (also compiles every sketch).
- arduino-cli on this PC lives inside the Arduino IDE: `%LOCALAPPDATA%\Programs\Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe`. Board `esp32:esp32:adafruit_feather_esp32_v2`, core 3.3.12.
- Flash, e.g.: `arduino-cli compile --fqbn esp32:esp32:adafruit_feather_esp32_v2 -u -p COM8 firmware\hoist_node`
  - Identify a board's COM port by reading its serial output (`"id":"MA"` / `"MB"`, or the strap's `zone` lines).
  - COM numbers change.
- **If the laptop's hotspot IP changes** from 172.20.10.2: update `NEARSTOP_IP` in `firmware/worker_strap/config.h` and `SERVER_IP` in `motor_node` / `hoist_node` `config.h`, then re-flash all three.
- On a new PC, delete `.venv` and let `NAT Basestation.bat` / `setup.ps1` rebuild it. **Don't copy `.venv` between PCs.**

## 8. Main files changed in this session

- **`firmware/worker_strap/worker_strap.ino`:**
  - every fall sent
  - "I'm OK" latching switch
  - voice / speaker
  - RGB LED
  - BMP180 + MQ-2 (gas fault)
  - IMU guard + `imu` health
  - direct `NEARSTOP_IP` and hub lock-on
  - beacon after Wi-Fi
  - sensitivity
- **`firmware/hoist_node/`:** servo option, `FAULTS_TO_ERROR` override.
- **`firmware/motor_node/`** (from the teammate), **`firmware/diagnostics/`:** `strap_sensor_check`, `bmp180_test`.
- **`backend/`:**
  - radio presence + RSSI average/threshold + fall-time check (`safety.py`)
  - `worker_ok` / `OKACK` (`udp_receiver.py`, `device_registry.py`)
  - environment (BMP180/MQ-2)
  - 8 s worker timeout
  - `recent_fall`
  - re-send of latched commands
  - Robot Arm removed from `site_config.json`
- **`frontend/`:** Monitoring tab (`monitor.js`), fall banner + cards (`site.js`), "Site floor | Monitoring" nav.
- **`tools/`:** `usb_bridge.py` (OKACK, restart-proof, unicode-safe), `make_voice.py`.
- **`tests/`:** `test_nearstop.py` (106 tests), `site_config_test.json` (3-machine test config).
- **`desktop_app.py`:** relaunches itself with `.venv`, so double-clicking it works.
- **Docs:** `README.md` (overview), `TECHNICAL.md`, `CLAUDE.md`, `docs/LESSONS_LEARNED.md`, `PRESENTATION_PROMPT.md` (5-speaker pitch brief).
