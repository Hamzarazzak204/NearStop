# NearStop: technical documentation

> **Taking over the project?** Read `HANDOFF.md` first (current state, wiring, what's tested, known problems).


NearStop watches the area in front of each machine with an **HC-SR04 ultrasonic distance sensor** and slows, stops or locks the machine when a person gets too close. A worker vest reports activity, heat (strain) and falls. A laptop or Raspberry Pi 5 runs the backend and a live dashboard.

| Board | Shows as | Kind |
|---|---|---|
| GW | Robot Arm | machine (ultrasonic sensor) |
| MA | Conveyor | machine (ultrasonic sensor) |
| MB | Hoist | machine (ultrasonic sensor) |
| W1 | Sam (Vest Tag) | worker vest |

```
 machines ──UDP {"type":"machine", dist_cm, zone, state, sensor} ~5/s──►  NearStop :5005
 vest     ──UDP heartbeat (activity, heat_c, fall) 1/s, checkin_ack──►    NearStop :5005
 NearStop ──UDP set_zones / command (stop, hold, lock, resume)──►  each machine
 NearStop ──FastAPI + WebSocket──►  dashboard  http://IP:8000
```

> **Limits.** An ultrasonic sensor only sees a narrow cone (about 20°) in front of the machine, and it **cannot tell who** it sees. With one vest on site, the closest detection is assumed to be that worker; the dashboard says so. Machine states are commands NearStop sends; the machine firmware must obey them.

**Connecting a machine's ESP32? Start with [CONNECT_YOUR_MACHINE.md](CONNECT_YOUR_MACHINE.md):** it has setup steps and a ready-made prompt for your AI assistant. A compile-checked reference sketch is in `firmware/machine_node/`.

RSSI trilateration (the earlier approach) has been removed from the backend and UI. The code is kept in `backend/legacy/` (see its README).

---

> **Site as of 2026-11-08 (evening): Conveyor (MA) + Hoist (MB) + Sam (W1).** The Robot Arm (GW) is no longer used: it is removed from `backend/site_config.json` and the dashboard. The tests keep a 3-machine config (`tests/site_config_test.json`) so the Robot Arm rules stay tested; to bring it back, add its block to `site_config.json` and `"GW": "machine"` to `EXPECTED_DEVICES` in `backend/config.py`.

## 1. Run it

**Windows desktop app:** double-click **`NAT Basestation.bat`** (or the desktop shortcut). The first run creates `.venv` and installs the packages (Python 3.11+ needed). Closing the window stops everything. Errors go to `logs/desktop.log`. Allow Python through Windows Firewall (Public networks) when asked.

**Raspberry Pi / Linux / macOS:**

```bash
cd ~/NAT_Basestation
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
python backend/main.py
```

Open `http://IP:8000` from any device on the same network (`hostname -I` on the Pi shows the IP). If the firewall is on: `sudo ufw allow 8000/tcp && sudo ufw allow 5005/udp`. Give the Pi a fixed IP (DHCP reservation in the router), because the boards send to it.

**Network:** all boards and the laptop/Pi must be on the same **2.4 GHz** network that lets devices talk to each other. eduroam and campus guest Wi-Fi don't work. A phone hotspot works: on an iPhone, turn on *Maximize Compatibility* and *Allow Others to Join*, and keep the Personal Hotspot screen open while boards join.

### 1.1 Machine nodes over USB *or* Wi-Fi (`firmware/machine_node_usb`)

This firmware reports to NearStop **whenever the board has power**:

- **Plugged into the laptop with `NearStop USB Bridge.bat` running:** reports go over the USB cable. The bridge pings the board once a second; while the pings arrive, the board uses USB.
- **Any other power** (USB charger, power bank, or a laptop without the bridge): reports go over **Wi-Fi** as UDP. By default they are **broadcast** on the network, so the laptop's IP can change without re-flashing; NearStop replies straight to the board.
- It switches automatically and stays online while switching. The messages are the same as `firmware/machine_node`, so the backend and dashboard need no changes.

Setup:

1. In **`firmware/machine_node_usb/config.h`** set the Wi-Fi name and password (a **2.4 GHz** network such as a phone hotspot; *not* eduroam). Optionally set `SERVER_IP` instead of broadcasting.
2. Upload **`machine_node_usb.ino`**, with `DEVICE_ID` (in `config.h`) set to the machine: `GW`, `MA` or `MB`. Wiring: USB (5 V) → VCC, GND → GND, **A0 / GPIO 26 → TRIG**, **ECHO → 1 kΩ → A2 / GPIO 34**, and 2 kΩ (or 2.2 kΩ) from A2 to GND.
3. The **laptop must be on the same Wi-Fi network** for the Wi-Fi path, and Windows Firewall must allow NearStop's Python (Public networks).
4. Start **`NAT Basestation.bat`**, then (for USB-connected boards) **`NearStop USB Bridge.bat`**. Close the bridge window before uploading firmware (it holds the COM ports).

The HC-SR04 is powered from the **USB** pin, so the board needs a **USB** power source (charger or power bank). On a LiPo battery alone the sensor gets no 5 V.

> **Our boards have no antennas** (module PICO-MINI-02U = external-antenna version), so the Wi-Fi path only works close to the hotspot. Fit a 2.4 GHz u.FL antenna on each board for real use.

Tested 2026-11-07 with a real HC-SR04 as MA (Conveyor), laptop and board on an iPhone hotspot (channel 6): over USB, reports arrived and a person at 0.22 m made NearStop send **stop**, which the board latched. Over Wi-Fi (bridge closed), reports arrived from the board's own IP. Closing / reopening the bridge switched Wi-Fi ↔ USB in about 2.6 s without the machine going offline. Not yet tested: commands over the Wi-Fi path, and running from a power bank away from the laptop.

### 1.2 Worker strap (`firmware/worker_strap`): close-to-machine LED and fall lockout

The worker's strap (Feather ESP32 V2 + MPU-6050 motion sensor) is the team's natHacks strap (`strap/strap_main.ino`) connected to NearStop. Its fall detector, IMU auto-recovery and zone colours are unchanged. **Since 2026-11-08 the strap reports EVERY confirmed fall** (before, only falls in the red zone were sent), with the zone the worker was in (`red` / `yellow` / `green` / `none`).

- **NearStop → strap:** about 5 times a second NearStop sends the strap every machine's ultrasonic reading (`{"type":"proximity","machines":{"GW":40,"MA":-1,"MB":-1}}`). The strap's NeoPixel shows the zone (red < 50 cm, yellow < 150 cm, green, blue = no data), and the **red LED (pin 13) is ON while the worker is close to an anchor/machine** (yellow or red zone). ESP-NOW packets from machines are still accepted too.
- **Strap → NearStop:** a `status` message every second (so Sam shows online, with the strap's zone and LED) and a `fall_alert` for every fall, resent until NearStop replies `ACK <id>`. Over USB when `NearStop USB Bridge.bat` runs, otherwise over Wi-Fi (same switching as the machine nodes).
- **Fall detection thresholds (made slightly more sensitive 2026-11-09):** free fall below **0.85 g** for 25 ms, then an impact of **2.5 g** (or **3.5 g** with no free fall), then mostly still for 1 s (spread ≤ **0.20 g**). Was 0.80 g / 30 ms / 3.0 g / 4.0 g / 0.15 g. A bit more sensitive also means hard knocks are a little more likely to count; the Hoist's instant impact-hold uses the same impact thresholds.
- **Fall away from every machine → alert + check-in.** NearStop logs a red *"FALL: Sam fell – not near a machine … Check on them now"*, shows a red **Fall** banner with the alarm, and starts the 10 s check-in (no answer = SOS). No machine is locked out. The strap's machine only counts if Sam was within its lockout distance (warning distance; danger distance for the Hoist), so a fall 3 m from the Robot Arm locks nothing. Tested on the real strap with the `f` command over USB: alert confirmed in < 0.1 s, check-in started, nothing locked.
- **Fall near a machine → LOCKED OUT.** When a fall alert arrives, NearStop immediately **locks** every machine that saw the worker within its warning distance in the last 4 s (plus the machine the strap names), whether it was running, slowed or stopped. The site log says *"FALL: Sam fell near Robot Arm – machine LOCKED OUT until a supervisor approves restart"*, the machine card shows *"Worker fell here – locked out"*, and the worker card shows *"Fell near Robot Arm – locked out"*. **Approve restart** is refused while the fall check-in is open or anyone is in the zone. The normal fall check-in (10 s, then SOS) also runs; the strap has no "I'm OK" button yet, so a supervisor answers it on the dashboard.

Setup: copy `firmware/worker_strap/config.example.h` to `config.h` (Wi-Fi name/password; `ESPNOW_CHANNEL` = the Wi-Fi channel), install the **Adafruit NeoPixel** library, choose **Adafruit Feather ESP32 V2**, upload. Serial Monitor test commands: `1 30` = pretend the Robot Arm sees the worker at 30 cm, `f` = pretend a fall.

Tested 2026-11-07 on the real strap with the Robot Arm (GW) on Wi-Fi: NearStop's readings reached the strap (GW at 10 cm → RED, LED on); a test fall in the red zone was confirmed by NearStop in under 0.1 s and the Robot Arm went to **Locked** with restart blocked; closing/opening the bridge switched the strap Wi-Fi ↔ USB in ~1 s. Not tested: a real fall onto a mat near a machine, and the team's laptop test suite (`tests/run_tests.sh` needs bash + g++, not on this PC).

#### "I'm OK" button, BMP180, MQ-2 and buzzer on the strap (Monitoring tab)

These come from the team's strap (`nathacks-demo`: `strap_main.ino` and `tools/gas_test`), merged into `firmware/worker_strap`, with the same pins and logic. NearStop's backend replaces the Raspberry Pi's `base_receiver.py`: it answers the strap's `ACK`/`OKACK` and shows the readings on the dashboard.

| Part | Feather ESP32 V2 | Notes |
|---|---|---|
| "I'm OK" switch (latching SPDT) | **C (common) → GPIO 14**, one outer pin (NO) → **GND**, other outer pin unconnected | No resistor (`INPUT_PULLUP`). `BUTTON_LATCHING 1` (default): **every flip** of the switch = one "I'm OK" and stops the voice/buzzer. For a momentary push-button set `BUTTON_LATCHING 0` (one press = one "I'm OK"). |
| BMP180 (temperature + air pressure) | VIN → **3V**, GND → GND, **SDA → SDA (22)**, **SCL → SCL (20)** | I2C address 0x77, shares the two wires with the MPU-6050 (0x68). Face it out, away from the skin and the MQ-2 heater. |
| MQ-2 VCC / GND | **USB** (5 V) / GND | The heater needs 5 V (~150 mA): only on USB / power-bank power, not on a LiPo. |
| MQ-2 AO | **10 kΩ → GPIO 34 (A2)**, and **20 kΩ (two 10k) from GPIO 34 to GND** | AO goes up to 5 V. **Never wire AO straight to the pin.** |

- **"I'm OK" button:** a tap sends `worker_ok`, resent every 0.5 s until NearStop answers `OKACK <id>`. The strap flashes green quickly. NearStop logs *"Sam pressed I'm OK"* and **answers an open check-in** (no SOS). It **never cancels a lockout**: the machine stays Locked until a supervisor presses *Approve restart* (team decision: a worker who fell hard can press a button and still be hurt). A tap within 5 min of a fall alert is linked to it (`for_alert`).
- **Enclosure light: 4-leg RGB LED (common cathode)**, seen through the enclosure. **Off normally; solid red from a confirmed fall until "I'm OK"**, together with the voice alarm. Wiring: red leg → 220 Ω → **GPIO 33**, green → 100 Ω → **GPIO 32**, blue → 100 Ω → **GPIO 15**, long (common) leg → **GND** (`RGB_COMMON_ANODE 1` and long leg → 3V for a common-anode LED). Test: `light` in the Serial Monitor. Tested 2026-11-08 on the strap: flashes. (The KY-034 7-colour module we tried first never lit, even on 5 V: faulty. `FLASH_LED_PIN` still supports one.)
- **Fall buzzer (GPIO 27):** a CEM-1203 magnetic buzzer through a MOSFET (GPIO 27 → 220 Ω → gate, 10 kΩ gate → GND, Zener across the buzzer, band to 3V; wiring in `docs/history/hardhat_buzzer_wiring_Part2.md`). After **every confirmed fall** it beeps (2048 Hz, 0.25 s on/off) **until the worker presses "I'm OK"** (or `ok` in the Serial Monitor), so people nearby can find them. Settings `BUZZER_PIN` (-1 = none) and `BUZZER_HZ` in `config.h`. Tested 2026-11-08 on the strap: `f` → *BUZZER ON*, `ok` → *BUZZER OFF* (sound itself to be confirmed by ear).
- **BMP180 (replaced the DHT11, 2026-11-08):** temperature + air pressure every 2 s (team's `bmp180_test.ino` module, no library). It never waits: each step is one short I2C transfer, so it doesn't disturb fall detection. `null` after 3 failed reads; it looks for the chip again every 5 s. **No humidity** (the BMP180 doesn't measure it). Pressure is monitoring only: ~924 hPa in Edmonton (about 670 m up); lifting the sensor 2 m lowers it by ~0.2 hPa.
- **MQ-2 (team's gas_test logic):** 2 min heater warm-up, then 10 s learning **clean air** (keep gas away). After that, the strap compares the sensor's resistance with clean air: **≤ 60 % = yellow (*Gas detected*)**, **≤ 35 % = red (*High gas*)**, with hysteresis (up after 1 s, down after 3 s). It slowly follows drift while clean. **It is relative to clean air, not ppm or %LEL.** Test with isopropyl alcohol or hand sanitizer on a tissue, never a flame. Serial: `b` = re-learn clean air, `s` = skip the warm-up (testing).
- **Sensor fault:** after warm-up, if the MQ-2 reads ~0 V (not powered, GND or AO not connected) or is pinned at the top (no divider) for 3 s, the strap reports `gas_level: "fault"` and the dashboard shows *Sensor fault – check MQ-2 wiring*, never "clean air". It recovers by itself (warm-up + clean-air learning again) once the reading is back.
- **Status message** adds `"temp_c":22.5,"pressure_hpa":924.25,"gas_mv":1210,"gas_ratio":0.97,"gas_level":"green","gas_warm":true` (`gas_level`: `warming` / `green` / `yellow` / `red`). NearStop uses the strap's gas level. Older straps that only send `gas_mv` are graded with `gas_warn_mv` / `gas_danger_mv` instead.
- **Heat:** the BMP180's air temperature: ≥ 30 °C = *High heat*, ≥ 35 °C = *Dangerous heat* (`heat_warn_c` / `heat_danger_c` in `"environment"`, `backend/site_config.json`; demo values).
- **What the supervisor sees:** the **Monitoring** tab (temperature, air pressure, gas % of clean air, 10-min charts), a dot on the tab button and **Caution** status when gas or heat is high, the reading on Sam's card, and the activity log. **Machines are never stopped by gas or heat**: monitoring only, not a certified gas detector.
- Serial line ends with e.g. `| 23.4C 924.2hPa | gas green 1.21V 97%`, or `| T/P -- | gas warming 0.23V`.
- Settings in `config.h`: `ENV_SENSORS` (0 = no BMP180/MQ-2), `MQ2_PIN`, `MQ2_DIVIDER`, `MQ2_WARMUP_S`, `BUTTON_PIN`. An older `config.h` without them uses the pins above.
- **Wiring check:** `firmware/diagnostics/strap_sensor_check` (no library) lists the I2C chips (MPU-6050 0x68, BMP180 0x77, and tells a BMP280/BME280 apart), what GPIO 34 reads, and the button. `firmware/diagnostics/bmp180_test` shows live BMP180 readings. Re-upload `worker_strap` afterwards.
- **Hub address (`NEARSTOP_IP` in `config.h`):** set it to the laptop's IP on the hotspot (e.g. `"172.20.10.2"`, see `ipconfig`). Wi-Fi broadcast was too lossy for this antenna-less strap (whole minutes lost), and a second NearStop on the same hotspot made it flip between hubs. With the IP set it sends directly (Wi-Fi retries lost packets) and ignores other hubs. Leave `""` to find the hub by broadcast (it then sticks to the first hub that replies). If the laptop's IP changes, update it and re-upload.

**Tested 2026-11-08 on the real strap (USB, bridge running):**
- The "I'm OK" path works end to end with the `ok` serial command: strap → bridge → NearStop logs *"Sam pressed I'm OK"* → `OKACK 1` → strap prints *"NearStop CONFIRMED"*. The physical button wasn't pressed: GPIO 14 reads "released" correctly.
- **BMP180 on the real strap (Wi-Fi, no bridge):** 23.4 °C and 924.2 hPa on the Monitoring tab; Sam stayed online for a full minute with no drop-outs (2 messages/s) once `NEARSTOP_IP` was set.
- **Found on this strap:** the DHT11 never answered (faulty unit), so it was replaced by the BMP180. GPIO 34 read a steady 3.15 V, the top of the ESP32's range. Check that the MQ-2 AO goes through the 10k/20k divider (without it, 5 V reaches a 3.3 V pin), or that it isn't touching 3V.
- Not tested yet: the gas levels with a real gas source (the MQ-2 read ~0 V on this strap).

### 1.2b Only Sam counts: radio presence (current behaviour)

Machines react **only to Sam's strap**, not to walls, tables or other people:

- **The strap** broadcasts a tiny radio beacon (`NSB:W1`, ESP-NOW) 10 times a second.
- **Each machine** (`motor_node`, `hoist_node`) listens. Heard in the last 3 s = **Sam is near this machine** (`"worker_near": true`); otherwise the machine reports **clear** (`dist_cm: -1`), whatever its ultrasonic sensor sees. No distance accuracy is needed: our boards have no antennas, so the radio only reaches a short way, and hearing it at all means nearby.
- **Machines keep running while Sam is nearby** (all three have `presence_stop: false`, firmware `PRESENCE_STOP 0`). **A machine stops only when Sam falls while it hears him**: NearStop locks it out (motor off) and the others keep running. (The Hoist also holds the instant the strap feels a hard hit near it, and resumes after 3 s if no fall follows.)
- NearStop counts a machine that hears Sam as `RADIO_NEAR_CM` (30 cm) away, so the strap's red light/LED and its "falls only count near a machine" rule work unchanged. The dashboard shows **"Sam nearby"** on that machine's card and "Near Conveyor (strap heard by radio)" on Sam's card.
- On USB, boards park Wi-Fi and fix the radio on channel 6 (`ESPNOW_CHANNEL`) so channel scanning can't hide the beacon; on Wi-Fi the hotspot must be on channel 6.

**Range depends on the boards:** without antennas "nearby" is short and varies (roughly arm's length); with 2.4 GHz u.FL antennas it grows to several metres.

**Signal-strength (RSSI) threshold per machine:** each machine reports how strongly it hears the strap (`worker_rssi`, dBm, shown on its dashboard card). Set `"near_rssi_dbm"` for a machine in `backend/site_config.json` to count Sam as near only when the beacon is at least that strong, e.g. `-80` (closer) or `-95` (farther). Default `-127` = any beacon heard; the demo site uses `-94`. **The ultrasonic sensor helps too:** if the strap is heard but weaker than `near_rssi_dbm`, Sam still counts as near when the sensor sees someone within the warning distance (card: "seen by sensor"). Tune it on the day: stand where "near" should start and read the dBm on the card. Tested 2026-11-08: the Conveyor reports clear with objects in front and keeps its motor at 70 %; the strap-to-machine radio link itself still needs a live test with the strap.

### 1.25 Conveyor (MA) and Robot Arm (GW) with motors (`firmware/motor_node`)

Both are running machines too: each board keeps its HC-SR04 exactly as before (TRIG **A0/GPIO26**, ECHO → 1 kΩ → **A2/GPIO34**, 2 kΩ/2.2 kΩ A2 → GND) and adds a **5 V motor** on **A1/GPIO25**: A1 → 220 Ω → logic-level N-MOSFET gate, 10 kΩ gate → GND, source → GND, drain → motor −, motor + → a **separate 5 V supply** (power bank / 4×AA NiMH) whose − is tied to the Feather GND, flyback diode across the motor (band toward +; a ≥ 6.2 V Zener such as the Hoist's 10 V one works), 100 µF across the supply.

Firmware: same tested motor/sensor code as the Hoist, with `MAX_PCT 100`, `RUN_PCT 70`, `SLOW_PCT 45`, and a `# [PWM]` line every 2 s that measures the motor pin. Upload with `-DDEVICE_ID=\"MA\"` (Conveyor) or `-DDEVICE_ID=\"GW\" -DPRESENCE_STOP=1` (Robot Arm).

- **Conveyor (MA): presence never slows or stops it.** Someone or something in front keeps the motor at its 70 % duty cycle (`"presence_stop": false` in site_config.json, `PRESENCE_STOP 0` in firmware); the dashboard still shows the zone. **A fall within its warning distance (1.5 m) locks it out** and stops the motor; an emergency, evacuation or sensor fault stops it too. Tested live 2026-11-08: objects at 6-101 cm, motor steady at 70 %; a fall alert 40 cm from it locked it and the motor pin went to 0 %.
- **Robot Arm (GW): presence doesn't stop it either** (`presence_stop: false`, like every machine in radio mode); a fall within its warning distance → Locked out. To bring back the old rules (warning → Slowed 45 %, danger → Stopped), set `"presence_stop": true` for GW in `site_config.json` and flash with `-DPRESENCE_STOP=1`.
- Motor supply here is 6.6 V for a 5 V motor: keep `RUN_PCT` at about 75 or below.

Tested 2026-11-08 on the real Conveyor board: online over Wi-Fi and USB, motor output 45 % (Slowed) / 47 % (Running ramping) with an object at 1.2-1.6 m, and NearStop latched it Stopped when something came within 0.20 m. The Robot Arm board wasn't connected yet.

### 1.3 The Hoist (MB): motor + ultrasonic (`firmware/hoist_node`)

> **Servo Hoist (this demo's MB):** the board has an **SG90 servo** instead of a DC motor. `hoist_node` supports it behind `SERVO_MOTOR 1` (off by default, so DC-motor hoists are unchanged): 50 Hz pulses, sweeps 0→180° and snaps back while Running (speed follows the motor %), and **no pulses at all** on hold / stop / lockout so it freezes instantly. Pins on that board: servo signal **A1/GPIO 25**, HC-SR04 Trig **A0/GPIO 26**, Echo **A2/GPIO 34** (10k/20k divider); servo red → USB 5 V, brown → GND, ~500 µF across the servo supply. Its `config.h` sets `MOTOR_PIN 25`, `TRIG_PIN 26`, `SERVO_MOTOR 1`, `FAULTS_TO_ERROR 10` (servo current makes the sensor miss pings). Unplug the servo's red wire if an upload drops off USB. Rules are unchanged: being near never stops it, a hard hit within 50 cm holds it, a confirmed fall locks it, Approve restart restarts it.

The Hoist is the machine with a real moving part: the team's **Part 1 motor board** (from `HardHat_Integration`), connected to NearStop as **MB**. It uses the same messages and USB/Wi-Fi switching as the other machine nodes, plus the team's hardware-tested motor and sensor code (interrupt-timed echo, never blocking; instant stop; ramped start).

| NearStop state | Motor |
|---|---|
| Running | `RUN_PCT` 44 % of the 90 % motor maximum = **40 % PWM duty** |
| Slowed (someone within the warning distance) | `SLOW_PCT` 30 % (≈ 27 % duty) |
| Holding / Stopped / **Locked** | **off instantly** |
| Sensor fault (no echo pulse at all) | off; NearStop shows the sensor as Error |

**The Hoist only stops when Sam falls CLOSE to it** (`"fall_stop_zone": "danger"`): within its danger distance (50 cm), not merely nearby (warning zone). If Sam falls while 1 m from the Hoist but next to another machine, only that machine is locked out (the other machines keep the warning-zone rule).

**No waiting for the fall to be confirmed** (`"impact_hold": true`): the strap needs ~1.3 s after hitting the ground to confirm a fall (it checks the worker lies still, which is what rejects hammering and jumps). So the strap also sends an instant `impact` message when it feels a hard hit while Sam is close, and NearStop **holds the Hoist the moment it arrives** (motor off, measured 0.03 s from the hit to the Hoist holding). If the fall is confirmed, the hold becomes a lockout; if Sam keeps moving, the Hoist resumes by itself after 3 s (`IMPACT_HOLD_S`). Hammering hard right next to the Hoist (within 50 cm) can therefore pause it for 3 s. NearStop also handles falls and impacts the instant a packet arrives instead of on its next update.

**Presence alone does not stop the Hoist.** It has `"presence_stop": false` in `backend/site_config.json` and `PRESENCE_STOP 0` in its `config.h`: with Sam standing in front of it the motor keeps running, while NearStop still tracks and shows the zone ("Too close" / "Worker nearby"), lights Sam's strap LED and remembers who was near. **Only a fall near the Hoist** (or an emergency / evacuation) stops it: NearStop locks it out, the motor stops, and the dashboard shows a red **Fall** banner with the alarm ("Sam fell near Hoist. Hoist stopped and locked out."), the check-in pop-up "Fell near Hoist · Hoist stopped", the site log, and the machine and worker cards. The restart is refused while the fall check-in is open or anyone is in the zone. Other machines keep the normal presence stop (`presence_stop` defaults to true).

Wiring (Part 1, keep it): motor **A0 / GPIO 26** → 220 Ω → MOSFET gate (10 kΩ gate → GND, Zener across the motor); HC-SR04 **TRIG → A1 / GPIO 25**, **ECHO → 10 kΩ → A2 / GPIO 34**, A2 → 20 kΩ → GND, VCC → USB pin. Settings: copy `firmware/hoist_node/config.example.h` to `config.h`.

Tested 2026-11-07 on the real hoist board: it came online over Wi-Fi, ran the motor at 60 % with the area clear and slowed with something at 95 cm; a test fall near the hoist (strap test commands `3 5` then `f`) made NearStop send **lock** within 0.2 s and the hoist reported **Locked, motor 0 %**; Approve restart was refused while the check-in was open. The motor itself was not watched turning; the board's reported motor output was.

Not integrated from `HardHat_Integration`: the RSSI positioning backend (NearStop replaced it) and the hard hat (Part 2). The worker device is the strap; the hat's **buzzer + cancel button** would be a good "I'm OK" answer to NearStop's fall check-in (send `{"type":"checkin_ack","id":"W1"}`).

## 2. The dashboard

### Site view (main)
- **Top bar:** site status (All clear / Warning / Emergency) with reasons, live clock, whether board data is arriving, alarm sound toggle, **EVACUATE SITE** (with a confirm dialog), and the gear icon for Setup.
- **KPI strip:** personnel on site, equipment running, **sensors OK**, active alerts, time since the last emergency.
- **Site plan:** each machine with its **red (danger) / amber (warning) / green (sensor range) rings** and a ~20° **sensor cone** in its facing direction. A **person marker** is drawn at the measured distance along the cone. When strain raises the zones, the rings grow smoothly and the original rings stay visible, dashed. Workers no sensor can see appear in the **"On site · not near any machine"** panel.
- **Equipment cards:** state (Running / Slowed / Stopped / Holding / Locked), a **live distance gauge** (0 to sensor range, threshold markers that slide when zones grow, value in metres), **sensor health** (OK / No reading / Error, where Error = no message for 2 s), what the machine itself reports, and **Approve restart** (enabled only when the zone is clear and the sensor is reading).
- **Personnel card:** vest status, activity, heat with strain meter, fall flag, **"Near Conveyor (0.87 m)"** from the closest detection, and **Check on**.
- **Site log:** every event, with distances ("Person 0.94 m from Conveyor – warning zone (≤ 1.80 m)").
- **Monitoring tab** (next to *Site floor*, or `#monitor`): for each worker, live **temperature, air pressure and combustible gas** from the strap, each with its level (Normal / High heat / Gas detected / High gas / Warming up / No reading), 10-minute charts (hover for exact values; gas chart shows its two thresholds) and a table view. Alerts stay visible there.
- **Alerts:** full-width emergency banner with an alarm sound, and a check-in dialog with a countdown. The dialog never focuses its OK button, so a stray Enter can't answer it.

### Setup view (gear icon)
| Tab | What's there |
|---|---|
| **Sensors** | Raw data per machine (`dist_cm`, zone, state, sensor, thresholds the machine confirmed, last message, last command sent), the vest's raw data, and the message formats |
| **Machines & site** | Site size, and per machine: **X, Y, sensor facing angle, warning and danger distances** (defaults 150 / 50 cm), with a live preview. **Save** writes `backend/site_config.json` and sends the new distances to the machines; no restart needed |
| **Tools & connection** | Test simulator, connection info, device discovery, USB serial monitor, checklist, network, log |

## 3. Safety logic (`backend/safety.py`)

- **Zones:** distance ≤ danger → *danger*; ≤ warning → *warning*; otherwise clear. Entering danger needs 2 readings in a row (~0.4 s). Leaving a zone needs 2 readings and 10 cm past the edge, so sensor jitter doesn't flicker.
- **Machine states:** warning → **Slowed** (auto-resumes when clear). Danger → **Stopped** (or **Holding** for a hoist) and **latched**. Evacuation → **Locked**. Latched states need **Approve restart**, which is refused while anyone is in the zone (including the 10 cm margin), while the sensor isn't reading, or while an emergency involving that machine is active.
- **Adaptive zones:** `scale = min(1 + strain_growth × strain, max_scale)` (defaults 0.5 and 1.5). Strain comes from the vest's heat (37 °C → 0, 39 °C → 1). When a machine's thresholds change by ≥ 5 cm, NearStop sends `set_zones`, and re-sends every 10 s until the machine echoes the new values.
- **Check-ins:** a vest fall (or **Check on**) starts a 10 s countdown. A `checkin_ack` from the vest, or a supervisor, clears it. No answer → **Emergency**: banner and alarm, and the machines that see the worker within their warning distance (or the closest detection) are stopped.

- **Worker environment** (strap BMP180 + MQ-2): gas and air temperature are graded ok / warn / danger with hysteresis. Warn/danger raise the site status to **Caution** and are logged. They never change machine states.

All settings are in `backend/site_config.json` (`sensor_range_cm`, `adaptive_zones`, `checkin_timeout_s`, `machine_command_port`, `environment`, per machine `presence_stop` / `near_rssi_dbm`).

- **Commands are re-sent until confirmed:** while a machine is Stopped/Holding/Locked, NearStop re-sends the command every 1 s until the board reports that state, and a board that still reports `locked` after NearStop restarts is shown Locked (Approve restart needed; no re-lock for 5 s after a resume).

## 4. Message formats

**Machine → NearStop** (UDP port 5005, several times a second):

```json
{"type":"machine","id":"MA","state":"slowed","dist_cm":87,"zone":"warning","sensor":"ok"}
```

- `dist_cm` = -1 when nothing is in range.
- `sensor`: `ok`, `no_reading` or `error`.
- Optional: `"warning_cm"` and `"danger_cm"`, the thresholds the machine is currently using. Echoing them lets NearStop confirm that `set_zones` was applied.

**NearStop → machine:** sent back to the IP:port the machine's messages come from. So the firmware must send from a fixed port (`udp.begin(port)`) and read incoming packets on it, or you can set `machine_command_port` in `site_config.json`.

```json
{"type":"set_zones","id":"MA","warning_cm":180,"danger_cm":60}
{"type":"command","id":"MA","cmd":"stop","reason":"..."}      // cmd: stop | hold | lock | resume | slow
```

**Strap environment (in the strap's `status`):** `"temp_c":23.4,"pressure_hpa":924.25,"gas_mv":1210,"gas_ratio":0.97,"gas_level":"green","gas_warm":true`. `null` = that sensor isn't answering. Fields that are missing = no environment sensors (older firmware).

**Strap "I'm OK" button:** `{"type":"worker_ok","worker":"W1","strap":1,"boot":N,"id":N,"for_alert":N,"secs_after_alert":S}`, resent every 0.5 s until NearStop replies plain text `OKACK <id>` (deduped by strap+boot+id). It answers an open check-in and never clears a lockout.

**Vest → NearStop:** `{"type":"heartbeat","id":"W1","role":"worker","activity":"Walking","heat_c":37.4,"fall":false}` once a second, and `{"type":"checkin_ack","id":"W1"}` when the worker answers a check-in. The current vest firmware (`firmware/node/`) sends heartbeats only, with no sensors or button yet.

## 5. Test with the simulator

Setup → **Tools & connection** → Start simulator, or `python backend/simulator.py --scenario demo`. The simulated machines behave like real firmware: they apply `set_zones` (and echo it) and obey stop/resume commands.

- **NearStop demo:** Sam on site, away from machines (0 s) → walks up to the Conveyor, which slows and whose zones grow as Sam heats up (8 s) → falls next to it (19 s) → no check-in reply → **Emergency, Conveyor stopped** (~29 s) → Sam presses OK and walks away (42 s) → zone clear → **Approve restart** (~47 s).
- **Visit each machine:** Sam walks into each machine's warning zone in turn and away again.
- **Gas leak + heat:** like *Visit each machine*, plus every 90 s a gas leak builds up past both gas thresholds and the air heats up (Monitoring tab test). All scenarios send normal temperature/pressure/gas readings. Turn the simulator **off** when real boards are connected: its fake machines would mix with the real ones.
- Options: distance jitter (cm), *go silent* per device (tests sensor Error), malformed packets.

## 6. API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/state` | Everything (devices, site, site_config, network, simulator) |
| GET | `/api/site` | NearStop site state: machines, workers, status, events |
| GET / POST | `/api/site-config` | Site size + per-machine `x`, `y`, `facing_deg`, `warning_cm`, `danger_cm` (saved to `site_config.json`) |
| GET | `/api/events?since=ID` | Event timeline |
| GET | `/api/workers/{id}/environment` | Last 10 minutes of a worker's temperature / air pressure / gas (one point per 2 s) + thresholds |
| POST | `/api/checkin`, `/api/checkin/resolve`, `/api/emergency/resolve`, `/api/evacuate`, `/api/machines/{id}/restart` | Safety actions |
| GET | `/api/devices`, `/api/network`, `/api/logs?since=ID` | Diagnostics |
| GET / POST | `/api/simulator` | `{"running":true,"scenario":"demo"|"loop"|"gas","noise":2,"flaky":["MB"],"junk":false}` |
| GET / POST | `/api/serial`, `/api/serial/open`, `/api/serial/close` | USB serial monitor (diagnostics) |
| WS | `/ws` | `/api/state` pushed ~5 times a second |

## 7. Folder layout

```
NAT_Basestation/
    NAT Basestation.bat, desktop_app.py     Windows desktop app
    backend/
        main.py             FastAPI app, UDP receiver start-up, commands to machines, API
        safety.py           zones, machine states, adaptive zones, check-ins, events
        site_config.py      loads/validates/saves site_config.json
        site_config.json    names, machine positions, facing, warning/danger distances
        udp_receiver.py     receives and validates machine/vest messages
        device_registry.py  devices, online status, latest machine readings, vest telemetry
        simulator.py        TEST ONLY: simulated machines + vest
        sim_control.py, serial_monitor.py, app_log.py, config.py
        legacy/             old RSSI trilateration code (not used)
    frontend/
        index.html, site.js, site.css       Site view (NearStop dashboard)
        app.js, style.css                   connection, routing, Setup view
    firmware/node/          ESP32 sketch for the vest (and the old RSSI anchors)
```
