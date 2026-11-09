# NearStop: briefing for Claude

> **Start with `HANDOFF.md`** (current state as of 2026-11-09: what works, what doesn't, wiring, next steps). It overrides older notes below.


You're working on **NearStop**, a construction-site worker-safety system built at **natHacks 2026** (Construction & Manufacturing theme). This file loads automatically in Claude Code. Read it fully before changing anything; the skills in `.claude/skills/` cover the common jobs step by step, and `docs/LESSONS_LEARNED.md` lists every hardware/software trap already hit.

> **Site as of 2026-11-08 (evening): Conveyor (MA) + Hoist (MB) + Sam (W1).** The Robot Arm (GW) is no longer used: it is removed from `backend/site_config.json` and the dashboard. The tests keep a 3-machine config (`tests/site_config_test.json`) so the Robot Arm rules stay tested; to bring it back, add its block to `site_config.json` and `"GW": "machine"` to `EXPECTED_DEVICES` in `backend/config.py`.

## Who you're helping
- A student team, several beginners. Explain plainly, give exact steps (file, line, menu), complete files, and say clearly **what was tested on hardware and what only compiled or ran with fake data**.
- Time is short: small working changes over rewrites. Don't add features nobody asked for.
- The repo is public: **never commit real Wi-Fi passwords** (`firmware/*/config.h` is git-ignored; ship `config.example.h`).
- It's an extra safety layer, never a replacement for certified e-stops / lockout-tagout. No medical claims.

## The system in one picture
```
 Machines (ESP32 + HC-SR04)         Worker strap (ESP32 + MPU-6050)
   GW Robot Arm, MA Conveyor,         W1 "Sam": fall detection, zone light,
   MB Hoist (motor)                    close-to-machine LED (pin 13)
        |  {"type":"machine",...} 5/s        |  status 1/s, impact, fall_alert
        v                                    v
   USB cable -> tools/usb_bridge.py  OR  Wi-Fi UDP broadcast  -> NearStop :5005
        ^                                    ^
        |  set_zones / command (hold/stop/lock/resume)   proximity 5/s, "ACK <id>"
   NearStop backend (FastAPI, backend/main.py) -> dashboard http://IP:8000 (frontend/)
```
- Each board reports **over USB when the USB bridge is running** (the bridge pings it every second), **otherwise over Wi-Fi** (UDP broadcast to port 5005; NearStop replies to the sender's IP:port). It switches automatically in ~1-3 s and stays online.
- NearStop is the hub: it decides machine states, sends commands, and tells the strap every machine's distance (the strap no longer depends on ESP-NOW radio).

## Devices
| ID | Name | Firmware | Wiring (Adafruit Feather ESP32 V2) |
|---|---|---|---|
| GW | Robot Arm | `firmware/motor_node` (`-DDEVICE_ID=\"GW\"`) | HC-SR04 TRIG A0/GPIO26, ECHO → 1k → A2/GPIO34, 2k(2.2k) A2→GND, VCC→USB pin; **5 V motor**: A1/GPIO25 → 220Ω → MOSFET gate (10k gate→GND), drain → motor −, motor + → separate 5 V supply (ground shared), flyback diode band to + |
| MA | Conveyor | `firmware/motor_node` (`-DDEVICE_ID=\"MA\"`) | same as GW |
| (GW/MA without motor) | | `firmware/machine_node_usb` | sensor only, same pins |
| MB | Hoist | `firmware/hoist_node` | motor A0/GPIO26 → 220Ω → MOSFET gate (10k gate→GND, Zener across motor, band to motor +); HC-SR04 TRIG A1/GPIO25, ECHO → 10k → A2/GPIO34, 20k A2→GND; **this demo's MB (Sepehr's) uses an SG90 servo**: servo signal A1/GPIO25, Trig A0/GPIO26, Echo A2/GPIO34 via divider, servo 5 V from USB pin + ~500 µF; `config.h`: `MOTOR_PIN 25`, `TRIG_PIN 26`, `SERVO_MOTOR 1`, `FAULTS_TO_ERROR 10` (see hoist-handoff) |
| W1 | Sam (strap) | `firmware/worker_strap` | MPU-6050 on SDA/SCL (0x68), NeoPixel (GPIO0, power GPIO2 HIGH), red LED 13; "I'm OK" button GPIO14→GND; RGB LED R→220Ω→GPIO33, G→100Ω→GPIO32, B→100Ω→GPIO15, common→GND (off normally, solid red after a fall until I'm OK); fall buzzer GPIO27→MOSFET gate (2048 Hz, beeps after a fall until I'm OK); BMP180 on I2C SDA/SCL at 0x77 (VIN 3V; temperature + pressure, replaced the DHT11); MQ-2 AO → 10k → A2/GPIO34, 20k A2→GND (VCC USB 5 V) |

Board in tools: **"Adafruit Feather ESP32 V2"**, FQBN `esp32:esp32:adafruit_feather_esp32_v2`, ESP32 Arduino core **3.3.12**. Our boards are the **PICO-MINI-02U (external-antenna) version with no antennas fitted**: radio is ~40 dB weak, Wi-Fi only works near the phone hotspot, ESP-NOW between boards is unreliable. That's why USB + the bridge exists.

## Safety rules (backend/safety.py, settings in backend/site_config.json)
- **Current mode: radio presence (anchors).** The strap beacons `NSB:W1` over ESP-NOW 10×/s; machines (`motor_node`, `hoist_node`) report `worker_near` (heard in the last 3 s) + `worker_rssi`, and `dist_cm: -1` (clear) when not heard. `safety._radio_detection` (used by `detection_cm`, `sam_near` and the dashboard's `sam_near`) counts Sam as near a hearing machine if the 2 s average `worker_rssi >= near_rssi_dbm` (per machine, -94 on the demo site, default -127 = any; treated as `RADIO_NEAR_CM` 30 cm away), OR if the signal is weaker but the ultrasonic sees someone within `warning_cm` (that distance is used). Objects in front never count while the strap isn't heard. All machines keep running for presence (`presence_stop: false`); a fall while a machine hears Sam locks it. Rules below still apply to firmware without `worker_near`.
- Zones from each machine's ultrasonic distance: **danger ≤ danger_cm (50)**, **warning ≤ warning_cm (150)**.
- Normal machines (`presence_stop: true`): warning → **Slowed**, danger → **Stopped** (latched).
- **Hoist** (`presence_stop: false`): a person in front does NOT slow/stop it (zone still shown, strap LED still lights).
- **Every fall is an alert:** the strap reports every confirmed fall (with `zone` red/yellow/green/none). A fall away from all machines → red FALL event + banner + check-in, nothing locked.
- NearStop **re-sends stop/hold/lock every 1 s until the board reports it**, and adopts a `locked` a board reports after NearStop restarts (`_enforce_latched`, no re-adopt for 5 s after a resume).
- **Fall lockout:** a confirmed strap fall **locks out** machines that saw the worker (or that the strap names, if Sam was within that machine's lockout distance): within warning distance for normal machines, within **danger distance only** for the Hoist (`fall_stop_zone: "danger"`). Lockout = state **Locked**, motor off, needs **Approve restart**, refused while the fall check-in is open or the zone is occupied.
- **Impact hold** (Hoist, `impact_hold: true`): the strap sends `impact` the instant it feels a hard hit while close; NearStop **holds** the Hoist immediately (~0.03 s), turns it into a lockout if the fall is confirmed (~1.3 s later), or resumes it after `IMPACT_HOLD_S` (3 s).
- **Worker environment** (strap BMP180 + MQ-2, Monitoring tab): gas level comes from the strap (team's gas_test logic: 2 min warm-up, learns clean air, resistance ≤ 60 % yellow / ≤ 35 % red; not ppm), fallback mV thresholds for older straps; air temperature ≥ 30 / ≥ 35 °C (`"environment"` in site_config.json). Monitoring only: raises site status to Caution and logs events, **never** changes machine states.
- **"I'm OK" button** (strap GPIO 14): `worker_ok` → NearStop logs it and closes an open check-in (no SOS), replies `OKACK <id>`. It **never** clears a lockout (team decision: the supervisor still approves restart).
- Fall check-in: 10 s countdown on the dashboard, then **SOS** (strap has no "I'm OK" button yet; a supervisor answers on the dashboard).
- Latched states (Stopped, Holding, Locked) only clear with **Approve restart**; evacuation locks everything.

## Message formats (don't change without updating firmware, backend, bridge and tests together)
- Machine → NearStop: `{"type":"machine","id":"MB","state":"running","dist_cm":87,"zone":"warning","sensor":"ok","warning_cm":150,"danger_cm":50}` (`dist_cm -1` = nothing in range; hoist adds `motor_pct`).
- NearStop → machine: `{"type":"set_zones","id":"MB","warning_cm":150,"danger_cm":50}`, `{"type":"command","id":"MB","cmd":"stop|hold|lock|resume|slow"}`.
- Strap → NearStop: `{"type":"status","worker":"W1","strap":1,"zone":"red","g":1.02,"near_led":true,...}`, `{"type":"impact","worker":"W1","machine_id":"MB","distance_cm":30,"g":5.0}`, `{"type":"fall_alert","worker":"W1","strap":1,"boot":N,"id":N,"peak_g":4.7,"machine_id":"MB","distance_cm":30,...}` (resent every 0.5 s until NearStop answers plain text `ACK <id>`; deduped by strap+boot+id). In fall_alert, `id` is the alert number, so the worker is in `worker`. Strap `status` may add `"temp_c":23.4,"pressure_hpa":924.25,"gas_mv":1210,"gas_ratio":0.97,"gas_level":"green|yellow|red|warming","gas_warm":true` (null = sensor not answering). `{"type":"worker_ok","worker":"W1","strap":1,"boot":N,"id":N,"for_alert":N,"secs_after_alert":S}` → NearStop replies `OKACK <id>` (the USB bridge passes `ACK`/`OKACK` lines through).
- NearStop → strap: `{"type":"proximity","worker":"W1","machines":{"GW":40,"MA":-1,"MB":-1}}` ~5/s.
- Bridge → board: `{"type":"bridge"}` every 1 s ("report over USB").

## Commands you'll use (Windows PowerShell)
- Setup once: `powershell -ExecutionPolicy Bypass -File setup.ps1`
- No-hardware checks: `powershell -ExecutionPolicy Bypass -File run_tests.ps1` (102 backend tests + compiles every sketch). **Run after every change.**
- Run: double-click `NAT Basestation.bat` (backend + dashboard window), then `NearStop USB Bridge.bat`.
- Flash: stop the bridge first (it holds the COM ports), then `arduino-cli compile --fqbn esp32:esp32:adafruit_feather_esp32_v2 -u -p COM4 firmware\hoist_node`. Details: skill **nearstop-flash-board**.
- Read a board without resetting it: `System.IO.Ports.SerialPort` at 115200 with `DtrEnable=$false; RtsEnable=$false`.
- API: `GET /api/state`, `/api/site`, `/api/events?since=0`, `POST /api/machines/{id}/restart`, `/api/checkin/resolve {"worker_id":"W1","outcome":"ok"}`, `/api/emergency/resolve`.

## Files
```
backend/   main.py (API, proximity push, instant fall hook), safety.py (all rules), device_registry.py,
           udp_receiver.py (message parsing, ACK), site_config.py/.json (per-machine settings), config.py
frontend/  index.html, site.js (site view: banner, cards, check-in pop-up), monitor.js (Monitoring tab:
           worker temperature/pressure/gas), app.js (routing, setup/tools)
firmware/  motor_node (GW/MA with motor), machine_node_usb (GW/MA sensor only), hoist_node (MB), worker_strap (W1, includes the team's nathacks-demo
           strap button/MQ-2), diagnostics/ (A0 PWM test, ESP-NOW sniffer, strap_sensor_check, bmp180_test),
           machine_node + node (teammates' Wi-Fi-only reference sketches), backups/ (flash dumps, git-ignored)
tools/     usb_bridge.py (USB <-> NearStop)
tests/     test_nearstop.py (no hardware)
docs/      LESSONS_LEARNED.md, history/ (teammates' original briefs)
```

## Working agreements
1. **Test before claiming.** Run `run_tests.ps1`; for hardware, read the board's serial output and NearStop's `/api/state` / `/api/events`. Report what was hardware-tested vs. faked (strap test commands `3 30` + `f`/`i` are fakes).
2. **Keep teammates' behaviour intact** unless asked: the strap's fall detector and light colours (falls: every fall is reported, decided by the user 2026-11-08); the other machines' presence stop.
3. **Safety changes need a test** in `tests/test_nearstop.py` (add a scenario), and a line in TECHNICAL.md.
4. **One NearStop per network.** Boards broadcast on Wi-Fi when the bridge isn't running, and ANY NearStop on the hotspot can answer and command them. Boards ignore Wi-Fi commands while the USB bridge is connected; the strap sends to `NEARSTOP_IP` and ignores other hubs.
5. **Don't fight the hardware in software.** Weak radio, a sensor seeing a wall, or a motor not spinning are hardware problems: diagnose (skill **nearstop-hardware-debug**) and tell the user what to check.
6. **Stop the bridge before flashing; restart it after.** Restart the backend after editing backend files; hard-refresh the dashboard if it shows old behaviour (browsers cache app.js/site.js).
7. **Ask before** anything outward-facing, deleting user files, or entering passwords. The Wi-Fi password lives only in local `config.h`.
