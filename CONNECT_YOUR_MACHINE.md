# Connect your machine node to NearStop

This folder contains the whole NearStop system: the backend, the dashboard (the UI) and reference firmware. Your job is to connect **your machine's ESP32 + HC-SR04 ultrasonic sensor** so it shows up live on the dashboard.

| Machine | ID (must match exactly) | Status |
|---|---|---|
| **Robot Arm** | `GW` | ← connect this |
| **Hoist** | `MB` | ← connect this |
| Conveyor | `MA` | not connected yet (shows "Offline") |
| Worker vest (Sam) | `W1` | not connected yet (shows "Vest not connected") |

## For you (the human)

1. **Start the dashboard on your laptop.**
   - **Windows:** double-click **`NAT Basestation.bat`**. The first run installs everything (Python 3.11+ needed). Allow Python through Windows Firewall (tick **Public networks**) when asked.
   - **Mac/Linux:** `python3 -m venv .venv && source .venv/bin/activate && pip install -r backend/requirements.txt && python backend/main.py`, then open `http://localhost:8000`.
2. Put your laptop and your ESP32 on **the same 2.4 GHz Wi-Fi network**: a phone hotspot or your own router. **Not eduroam**, and not the campus guest Wi-Fi: devices can't reach each other there.
3. Open this folder in Claude Code (or your AI assistant), paste the prompt below, and fill in the last line.

---

## Prompt for your Claude

```text
You are helping connect an ESP32 machine node to "NearStop", a construction-site
worker-safety dashboard. This folder is the full project. Read TECHNICAL.md first,
then backend/udp_receiver.py, backend/safety.py (skim) and the reference sketch
firmware/machine_node/machine_node.ino + config.h.

THE SYSTEM
- A laptop runs the NearStop backend (Python/FastAPI) and dashboard on port 8000.
  It listens for UDP JSON on port 5005.
- Each machine has an ESP32 + HC-SR04 ultrasonic sensor pointing at the area in
  front of the machine. The machine node measures the distance to the nearest
  person and reports it to NearStop. NearStop decides the safety state and sends
  commands back.
- Machine IDs (must match backend/site_config.json): GW = Robot Arm, MA = Conveyor,
  MB = Hoist. The worker vest is W1. I am connecting: <ROBOT ARM (GW) / HOIST (MB)>.
- The Conveyor (MA) and the vest (W1) are NOT connected yet. They will show as
  Offline / "Vest not connected", and the site status will say "Caution" because
  of that. That is expected; don't try to fix it.

PROTOCOL (do not change it; the backend depends on it exactly)
1. Machine -> NearStop, one UDP packet every ~200 ms, to <laptop IP>:5005:
     {"type":"machine","id":"GW","state":"running","dist_cm":87,"zone":"warning",
      "sensor":"ok","warning_cm":150,"danger_cm":50}
   - id: "GW" or "MB" exactly.
   - dist_cm: integer cm to the nearest object, or -1 when nothing is in range.
   - zone: "clear" | "warning" | "danger"  (dist <= danger_cm -> danger,
     dist <= warning_cm -> warning, else clear; -1 -> clear)
   - state: "running" | "slowed" | "stopped" | "holding" | "locked"
   - sensor: "ok" | "no_reading" | "error"
   - warning_cm / danger_cm: the thresholds the node is CURRENTLY using (echo).
   If no packet arrives for 2 s the dashboard shows the sensor as Error.
2. NearStop -> machine: sent back to the SAME IP:port the reports come from.
   So the node must call udp.begin(LOCAL_PORT) once (e.g. 5006), send its reports
   with that same WiFiUDP object, and read incoming packets on it:
     {"type":"set_zones","id":"GW","warning_cm":180,"danger_cm":60}
     {"type":"command","id":"GW","cmd":"stop","reason":"..."}
        cmd: stop | hold | lock | resume | slow
3. Node behaviour:
   - set_zones: start using the new thresholds immediately and echo them in the
     next reports (NearStop raises them when the worker is under strain).
   - stop / hold / lock: enter that state and STAY there until "resume".
     Only a supervisor pressing "Approve restart" on the dashboard sends resume.
   - Without an override: danger -> "stopped", warning -> "slowed",
     clear -> "running" (the node can protect the machine on its own).
   - slow: nothing extra needed.

WHAT TO DO
- I already have machine-node firmware. Merge the NearStop protocol into it.
  Keep my existing machine-control code. Use
  firmware/machine_node/machine_node.ino as the reference: it is compile-checked
  for ESP32 Arduino core 3.x (Adafruit Feather ESP32 V2) but NOT yet tested on real
  HC-SR04 hardware. Its applyMachineState(state) function is where the machine's
  relay/motor/light should react. If my firmware is very different, explain the
  minimum changes first, then make them.
- Put Wi-Fi SSID/password, SERVER_IP (the laptop's IP, shown on the dashboard
  under Setup > Tools & connection > "This computer's IP"), ports, DEVICE_ID and
  pins in a config header. Never commit real passwords; use placeholders.
- Hardware: the HC-SR04 runs on 5 V and its ECHO pin outputs 5 V. ESP32 GPIOs are
  3.3 V only, so put a voltage divider on ECHO (e.g. 1 kΩ + 2 kΩ). Check my
  board's pinout before choosing pins.
- Keep loop() non-blocking apart from the short pulseIn (≤ 25 ms timeout); retry
  Wi-Fi every ~10 s if it drops; print useful lines to Serial at 115200.
- If my board cannot send from a fixed port, the alternative is to set
  "machine_command_port" in backend/site_config.json to the port the node
  listens on. Prefer the fixed-port approach.

ON THE DASHBOARD (http://localhost:8000 on the laptop)
- Setup (gear icon) > Machines & site: set my machine's X/Y position on the
  site (metres, (0,0) = bottom-left), the direction the sensor faces
  (0° = right/east, 90° = up/north), and warning/danger distances (default
  150/50 cm). Press Save.
- Do NOT start the simulator (Setup > Tools) while real boards are connected:
  it pretends to be GW and MB too and the data would mix.

DEFINITION OF DONE (test each and tell me the result honestly)
1. Serial Monitor: Wi-Fi CONNECTED with an IP in the same range as the laptop.
2. Setup > Sensors: my machine's row shows Sensor "OK", dist_cm changing when I
   move my hand, "Last message" under 1 s, and "Thresholds on machine" with ✓.
3. Site view: my machine's card is "Clear" with nothing in front.
4. Stand inside the warning distance: the card shows "Worker nearby"; the
   machine reports "slowed".
5. Get closer than the danger distance: the card shows "Too close" and then
   "Stopped" with a lock; Serial shows the stop command arriving.
6. Step away: the machine stays Stopped. Press "Approve restart" on the
   dashboard: Serial shows "resume" and the card returns to "Running".
7. Unplug the ESP32: within ~3 s the dashboard shows the machine "Offline"
   (Setup > Sensors shows the sensor as Error after 2 s).

TROUBLESHOOTING HINTS
- Board says CONNECTED but nothing on the dashboard: wrong SERVER_IP, laptop on
  another network, or Windows Firewall blocking Python (allow it on Public
  networks). Setup > Tools & connection > Discover devices lists every board heard.
- Reports arrive but commands never do: the node isn't sending from the port it
  listens on (missing udp.begin(LOCAL_PORT) or a second WiFiUDP object).
- "network not found (201)": SSID typo, 5 GHz-only network, or an iPhone hotspot
  name with a curly apostrophe (write it as "Name\xE2\x80\x99s iPhone"). On an
  iPhone, turn on Maximize Compatibility and Allow Others to Join.
- Distance stuck at -1: wiring, or the ECHO divider is wrong/missing.

RULES
- Don't change the message formats, the backend logic or the dashboard unless I
  ask. If something in the backend seems wrong, explain it to me first.
- Don't claim something works until it has been tested on the real board; say
  what was and wasn't tested.

MY MACHINE / MY TASK:
<e.g. "I have the Hoist (MB). My firmware is in ../hoist_node/. Board: ESP32 DevKit V1,
HC-SR04 on GPIO 5 (trig) / 18 (echo). Laptop IP 172.20.10.3, hotspot 'Alex's iPhone'.">
```
