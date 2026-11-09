# NearStop: how to use it

NearStop keeps machines running while people work next to them, and stops **the one machine next to a worker who has fallen**. A worker wears the **NearStop strap**. Each machine has a small **machine anchor** (ESP32 + ultrasonic sensor). A laptop runs the **NearStop dashboard**.

This guide is for anyone running the demo or the dashboard. **Start with section 3.** For the technical details see `README.md`, `HANDOFF.md` and `docs/LESSONS_LEARNED.md`.

---

## 1. What's in the kit

| Device | ID | What it does |
|---|---|---|
| **Sam's strap** (worn on the upper arm) | W1 | Detects falls (motion sensor), asks **"Are you okay?"** through its speaker, blinks its **RGB light red** after a fall, has an **"I'm OK" switch**, measures **temperature + air pressure** (BMP180) and **combustible gas** (MQ-2), and sends a radio beacon ("Sam is here") 10 times a second. |
| **Conveyor** | MA | DC motor + ultrasonic sensor. Listens for Sam's beacon. |
| **Hoist** | MB | Servo motor + ultrasonic sensor. Listens for Sam's beacon. Pauses **instantly** on a hard hit within 50 cm. |
| **Laptop** | – | Runs NearStop: the dashboard, the safety rules and the alarms. |

---

## 2. How it behaves (the rules in plain words)

1. **Being near a machine never stops it.** Sam can work right next to the Conveyor or the Hoist and they keep running.
2. **"Sam nearby"** shows on a machine's card when that machine hears Sam's strap:
   - signal **-94 dBm or stronger**, or
   - a weaker signal **and** the ultrasonic sensor sees someone within **1.5 m** ("seen by sensor").
   - A box or a person **without** the strap is ignored: the card stays **Clear**.
3. **Sam falls next to a machine** → that machine is **LOCKED OUT** (stops) and the dashboard alarms. Only the machine he fell next to stops; the others keep running. The Hoist only locks if he fell **within 50 cm** of it.
4. **Sam falls anywhere else** → no machine stops, but the dashboard still shows the **red Fall banner** and starts a **check-in**.
5. **Check-in:** 10 seconds for Sam (or a supervisor) to answer. **No answer = EMERGENCY (SOS).**
6. **"I'm OK"** answers the check-in. It **never** restarts a machine: someone who fell can still be hurt.
7. **Only a supervisor brings a locked machine back**, with **Approve restart**. It's refused while the check-in is still open or while Sam is still next to the machine.

---

## 3. Starting the system

### Every time

1. **Power the boards.**
   - Conveyor and Hoist: plug them into the laptop by **USB** (use a USB hub if you run out of ports).
   - Sam's strap: a **power bank** (it reports over Wi-Fi), or the laptop's USB.
2. **Connect the laptop** to the phone hotspot **"Rafat's iPhone"** (2.4 GHz). Keep the Personal Hotspot screen open with **Maximise Compatibility** on.
3. Double-click **`NAT Basestation.bat`**. The NearStop window opens with the dashboard (also at <http://127.0.0.1:8000> in any browser).
4. Double-click **`NearStop USB Bridge.bat`**. It carries the boards that are plugged in by USB. **Leave its window open.** It finds boards on any COM port by itself and picks them up again if you re-plug them.
5. Check the dashboard: **Sam**, **Conveyor** and **Hoist** all show as online, and the top banner says **All clear**.

> **Only one NearStop on the hotspot.** If a teammate's laptop also runs NearStop, the boards can get locked by the wrong one.

### First time on a new laptop

1. Install **Python 3.11+** and the **Arduino IDE** (or arduino-cli).
2. In PowerShell, in this folder: `powershell -ExecutionPolicy Bypass -File setup.ps1`. This installs everything and creates the `firmware/*/config.h` files.
3. Put the hotspot **name and password** into each `firmware/*/config.h` (they're not in the zip on purpose).
4. Find the laptop's address on the hotspot (`ipconfig`, usually `172.20.10.x`). Put it in:
   - `firmware/worker_strap/config.h` → `NEARSTOP_IP`
   - `firmware/motor_node/config.h` and `firmware/hoist_node/config.h` → `SERVER_IP` (only needed if the machines run on Wi-Fi instead of USB)
5. Flash the boards (`START_HERE.md` has the exact commands). Close the USB bridge window first: it holds the COM ports.
6. Optional check, no hardware needed: `powershell -ExecutionPolicy Bypass -File run_tests.ps1`.

---

## 4. The dashboard

### Top bar

| Item | What it does |
|---|---|
| **Site floor** | The main live view (below). |
| **Monitoring** | Sam's **temperature, air pressure and gas** with 10-minute charts. |
| **Hub** dot | Green = NearStop is running and receiving. |
| **Bell** | Alarm sound on/off. |
| **Gear (Setup)** | Raw sensor data, machine and site settings, the test simulator and connection tools. |
| **Evacuate** (red) | Stops **every** machine at once. Press again (**End evacuation**) when it's safe. |

Under the top bar, a **status banner** sums up the site: **All clear** (green), **Caution** (amber) or a red alarm.

### Worker card (Sam)

- **Status:** OK · Near Conveyor/Hoist · Possible fall – checking in · **Not responding – EMERGENCY** · Offline.
- **Temperature and pressure** from the strap, plus the **gas** level: *warming up*, green / yellow / red, or **Sensor fault** (wiring).
- **"Fell … ago"** after a recent fall.
- **Check on Sam:** starts a check-in by hand.

### Machine cards (Conveyor, Hoist)

- **Proximity:** **Clear** · **Sam nearby · -91 dBm** (signal strength) · **seen by sensor** (the ultrasonic sensor confirmed him) · No signal · Offline.
- **State:** **Running** · **Holding** (Hoist paused after a hard hit, waiting to see if it's a real fall) · **Locked** (after a fall) · Stopped · Offline.
- **Approve restart:** appears when a machine is locked. It's greyed out, with the reason, while it isn't safe yet.

### Activity

A running log of everything that happened: falls, check-ins, lockouts, restarts, sensor problems.

### When Sam falls

1. The red **Fall** banner appears (8 seconds) and the alarm sounds.
2. A **"Waiting for Sam…"** pop-up counts down 10 seconds:
   - **Sam is OK:** the supervisor confirms he's fine.
   - **SOS now:** raise the emergency straight away.
   - **Hide:** close the pop-up; the countdown keeps running.
3. On the strap, the speaker repeats **"Are you okay?"** and the **RGB light blinks red**, until Sam flips the **"I'm OK" switch**.
4. If he fell next to a machine, its card shows **Locked**. When Sam has moved away and the check-in is answered, press **Approve restart**.

---

## 5. Using the strap

- **Wear it** on the upper arm.
- **The small on-board light** changes colour with distance to a machine (green → amber → red), and the red LED on pin 13 lights near a machine.
- **After a fall:** voice + blinking red light. **Flip the "I'm OK" switch** to answer (every flip counts as one "I'm OK").
- **Gas sensor:** learns clean air **8 seconds** after power-on. Switch it on away from gas, solvents or alcohol. "Sensor fault" = no voltage reaching the sensor pin: check its wiring.
- **Radio range is short** (the boards have no antennas fitted): for "Sam nearby" and the lockout, the strap must be **within a few tens of centimetres** of a machine's board. Fitting 2.4 GHz u.FL antennas fixes this.

---

## 6. Demo script (2 minutes)

1. Everything **Running**, banner **All clear**.
2. Hold the strap next to the **Conveyor** → card says **Sam nearby**, the Conveyor **keeps running**.
3. Move to the **Hoist** → **Sam nearby**, it keeps running.
4. **Drop the strap hard** next to the Hoist and leave it still for a second → Hoist **Holding** then **Locked**, red Fall banner, check-in pop-up, voice + red blinking light.
5. Let the 10 s run out → **EMERGENCY**. Then flip **"I'm OK"** → emergency cleared, Hoist **still Locked**.
6. Move the strap away → press **Approve restart** → Hoist **Running**.
7. **Drop the strap away from the machines** → Fall banner + check-in, **nothing stops**.

---

## 7. Troubleshooting

| Problem | Fix |
|---|---|
| Conveyor / Hoist **Offline** though plugged in | The **USB bridge** isn't running: double-click `NearStop USB Bridge.bat`. |
| Sam **Offline** on a power bank | The laptop's hotspot address changed. Run `ipconfig`, put the new address in `NEARSTOP_IP` (`firmware/worker_strap/config.h`) and reflash the strap, or plug the strap in by USB. |
| Machine never shows **Sam nearby** | Hold the strap closer (no antennas = tiny range). Check the strap is on and online. |
| Machine **Locked** and won't restart | Answer the check-in first, move Sam away, then **Approve restart**. |
| Boards locked although nobody fell | Another NearStop is running on the hotspot. Close it. |
| Gas shows **Sensor fault** | The MQ-2's signal isn't reaching pin A2: check VCC → USB (5 V), GND, AO → 10 kΩ → A2, A2 → 20 kΩ → GND. |
| Uploading firmware fails ("port busy") | Close the USB bridge window and the Arduino Serial Monitor first. |
| Dashboard looks old after an update | Hard refresh (Ctrl+F5). |

**NearStop adds to emergency stops and lockout/tagout. It never replaces them.**
