# NearStop

**Machines that know you're there. A strap that feels you fall.**

natHacks 2026 · Construction & Manufacturing

NearStop is a worker-safety system for construction and manufacturing sites. Each worker wears a strap on the upper arm, and each machine has a small sensor node. When a worker falls near a running machine, NearStop stops that machine, alerts the supervisor within seconds, and keeps the machine locked until a person approves the restart.

---

## The problem

In 2017, nearly **49,000 Canadian workers** were hurt badly enough by falls to miss work, and 63 died, making falls the third most common lost-time injury in the country (Association of Workers' Compensation Boards of Canada). The longer a worker waits for help after a fall, the worse an injury can get.

Machines on a site can't tell when a worker near them is in trouble. A worker who falls beside a running machine can be hurt again by it, and on a busy site a fall can go unnoticed for minutes. Proximity systems and man-down alarms both exist, but they work separately, and neither tells the dangerous machine to stop.

## What NearStop does

- **Being near a machine is normal work.** Presence alone never stops a machine, so workers have no reason to switch the system off.
- **A fall near a machine stops it.** The Hoist holds the moment the strap feels a hard hit nearby. If the worker then lies still for about a second, the fall is confirmed and the hold becomes a **lockout**.
- **The supervisor knows right away.** Every fall, near a machine or not, raises an alarm on the dashboard and starts a 10-second check-in. No answer means **SOS**.
- **Only a person brings a machine back.** The worker's "I'm OK" answers the check-in but never unlocks a machine, because someone who just fell can still be hurt. A supervisor approves the restart, and only once nobody is left in the zone.
- **Worker conditions.** The strap also reads air temperature, air pressure and combustible gas, shown live on the dashboard's Monitoring tab.

NearStop is an extra layer, **not a replacement** for guards, emergency stops or lockout/tagout.

## How it works

```
 Worker strap ──ESP-NOW beacon (10×/s)──►  Machine nodes   (tells each machine "the worker is near")
 Worker strap ──fall / impact / status / "I'm OK"──►  NearStop hub
 Machine nodes ──distance, zone, state (~5×/s)──►     NearStop hub
 NearStop hub ──stop / hold / lock / resume──►        Machine nodes
 NearStop hub ──live dashboard (FastAPI + WebSocket)──►  Supervisor, http://<laptop-ip>:8000
```

| Part | Hardware | Firmware / code |
|---|---|---|
| **Worker strap** (Sam, `W1`) | Adafruit Feather ESP32 V2, MPU-6050 motion sensor, BMP180 temperature + pressure, MQ-2 gas sensor, "I'm OK" switch, buzzer, speaker, RGB LED | `firmware/worker_strap` |
| **Conveyor** (`MA`) | Feather ESP32 V2, HC-SR04 ultrasonic sensor, DC motor through a logic-level MOSFET | `firmware/motor_node` |
| **Hoist** (`MB`) | Feather ESP32 V2, HC-SR04 ultrasonic sensor, motor (DC motor or SG90 servo) | `firmware/hoist_node` |
| **NearStop hub** | Any laptop (Windows, macOS, Linux) or a Raspberry Pi 5 | `backend/` (Python, FastAPI), `frontend/` (dashboard) |

The ultrasonic sensor only measures distance, so on its own it reacts to boxes and walls as much as people. The strap's radio beacon tells each machine that what's in front of it is a person.

## Demo walkthrough

1. Sam, wearing the strap, walks up to the Conveyor and then the Hoist. Both keep running.
2. Sam falls near the Hoist. The strap feels the hit and NearStop **holds the Hoist** immediately.
3. About a second later the strap confirms the fall, and the hold becomes a **lockout**. The dashboard shows a red fall banner and starts the 10-second check-in.
4. A supervisor answers the check-in (or Sam presses "I'm OK"). The Hoist **stays locked**.
5. Sam gets up and leaves the zone. The supervisor presses **Approve restart**, and the Hoist goes back to work.

## How to run it

### Try it without any hardware (simulator)

Needs Python 3.11 or newer.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS / Linux: source .venv/bin/activate
pip install -r backend/requirements.txt
python backend/main.py
```

Open **http://localhost:8000**, then go to **Setup (gear icon) → Tools & connection → Start simulator** and pick the **demo** scenario. You can also run `python backend/simulator.py --scenario demo` in a second terminal. Turn the simulator off before connecting real boards.

On Windows you can instead double-click **`NAT Basestation.bat`**. The first run creates the virtual environment and installs everything.

### Run the tests

```powershell
powershell -ExecutionPolicy Bypass -File setup.ps1        # Python packages, arduino-cli, ESP32 core, libraries
powershell -ExecutionPolicy Bypass -File run_tests.ps1    # backend safety tests + every sketch compiles
```

### Run it with the real hardware

1. Put every board and the laptop on the same **2.4 GHz** network (a phone hotspot works; campus Wi-Fi like eduroam doesn't).
2. Copy each sketch's `config.example.h` to `config.h` and fill in the Wi-Fi name and password. `config.h` is git-ignored, so passwords never get committed.
3. Flash the boards with Arduino IDE or `arduino-cli`, board **Adafruit Feather ESP32 V2**. The commands and which sketch goes on which board are in **[START_HERE.md](START_HERE.md)**.
4. Start **`NAT Basestation.bat`** (or `python backend/main.py`) and open the dashboard. If Wi-Fi is unreliable, run **`NearStop USB Bridge.bat`** to carry boards over their USB cables instead.

Full wiring, message formats, safety rules and the API are in **[TECHNICAL.md](TECHNICAL.md)**.

## Repository layout

```
backend/        NearStop hub: safety rules (safety.py), UDP receiver, API, simulator
frontend/       Supervisor dashboard (site view, equipment and worker cards, Monitoring tab)
firmware/       ESP32 sketches: worker_strap, motor_node (Conveyor), hoist_node (Hoist),
                machine_node_usb, plus diagnostics/ test sketches
tests/          Backend safety and protocol tests
tools/          USB bridge, voice-clip generator
slides/         Presentation (open NearStop_Walkthrough.html in a browser)
docs/           Lessons learned, design reference, the team's original briefs (history/)
```

## Supporting materials

- **Slides:** [`slides/NearStop_Walkthrough.html`](slides/NearStop_Walkthrough.html). Open it in a browser and use **Next / Back** or the arrow keys. **Auto-play** runs the whole walkthrough.
- **Technical documentation:** [TECHNICAL.md](TECHNICAL.md)
- **Quick start for the hardware:** [START_HERE.md](START_HERE.md), [README_HOW_TO_USE.md](README_HOW_TO_USE.md)
- **Connecting a new machine:** [CONNECT_YOUR_MACHINE.md](CONNECT_YOUR_MACHINE.md)
- **Problems we hit and how we fixed them:** [docs/LESSONS_LEARNED.md](docs/LESSONS_LEARNED.md)

## Current limitations

- Location comes from the strap's radio signal and the machines' ultrasonic sensors, so NearStop knows when a worker is **near a machine**, not their exact position on site. A fall away from every machine is still detected and alerted, just without a machine location.
- The gas sensor compares readings with clean air. It is not a certified gas detector, and gas or heat never stops a machine.
- This is a hackathon prototype, not a certified safety system.

## Team

- Ashik Hredoy
- Rafat Islam
- Sepehr Ghafouri
- Zeeshan Haque
- Hamza Razzak

## Credits

- Built at **natHacks 2026** (Network for Applied Technology, University of Alberta), November 6–9, 2026. All code was written during the 64-hour hackathon.
- Fall statistic: Association of Workers' Compensation Boards of Canada (AWCBC), 2017 national work injury data.
- Open-source libraries: FastAPI, Uvicorn, NumPy, pyserial, pywebview, Arduino-ESP32 core, Adafruit NeoPixel.
- AI assistance: Claude (Anthropic) was used as a coding and writing assistant.
