# Context for Claude: natHacks 2026 worker-safety strap (demo build)

You're helping a member of a student hackathon team. Read all of this before answering. If you're Claude Code, this file loaded automatically because you're in the project folder. If you're in a chat, the user pasted it.

## Who you're helping and how to talk to them

- A team of 5 students at **natHacks 2026** (Edmonton, in person), **Construction & Manufacturing** theme.
- The build window ends **Mon Nov 9, 2026, 11:00 AM MST**, with a live 15-minute pitch and 5-minute Q&A after. Time is short: prefer small, working changes over rewrites.
- Several team members are **beginners**. Explain in plain words, give exact steps (which file, which line, which menu), and give complete files or clearly placed snippets.
- **Hackathon rules that affect your advice:**
  - All code must be written during the event.
  - The repo will be public, so no secrets.
  - Rented hardware must go back unmodified.
- **Don't add features nobody asked for.** In particular: **no gas-sensor (MQ-2) code until the team asks**, and **no pulse/heart-rate sensor** (it was dropped).

## The project in one paragraph

A worker wears a strap on the **upper arm or shoulder**. Three demo "machines" each measure the worker's distance with an ultrasonic sensor and send it to the strap over ESP-NOW. The strap's light shows the zone: **red under 50 cm, yellow 50-150 cm, green beyond 150 cm, blue if no machine has been heard for 1 s**. If the strap detects a **fall AND the worker was in the red zone**, it sends a fall alert over Wi-Fi to the base station (a Raspberry Pi), which confirms it and logs it. Falls outside the red zone aren't sent. That's a deliberate team decision; don't change it unless asked.

**The pitch angle:** proximity systems know how close a worker is, and man-down wearables know a worker fell, but they don't combine the two around a specific machine. Say "we couldn't find a product that does this", never "nobody does this".

## Architecture

```
Machine 1/2/3: ESP32 + HC-SR04 --ESP-NOW ch1, DistancePacket--> STRAP: Feather V2 + MPU-6050
                                                                 | fall AND red (last 4 s)
                                                                 v
                                     Wi-Fi + UDP JSON --> BASE: Raspberry Pi 5, base_receiver.py
                                      <-- "ACK <id>"
```

| File | Device | Owner |
|---|---|---|
| `strap/strap_main/strap_main.ino` | strap: Adafruit ESP32 Feather V2 (ESP32-PICO-V3-02, 8 MB flash + 2 MB PSRAM) | the user + Claude |
| `machine/machine_sender/machine_sender.ino` | each machine ESP32 + HC-SR04 | machine teammates (they may have their own code; copy in the RADIO parts) |
| `base/base_receiver.py` | Raspberry Pi | GUI teammate: their code goes in `on_fall_alert()` and `on_status()` |
| `tools/i2c_doctor`, `tools/mpu6050_test` | strap | diagnostics |
| `tests/run_tests.sh` | laptop | runs the strap sketch with stand-in hardware against the real Pi script |

## Hardware facts (verified on the real boards)

- **Strap board:** Adafruit ESP32 Feather V2.
  - In Arduino IDE choose **"Adafruit Feather ESP32 V2"**. "ESP32 Dev Module" gets the I2C pins wrong and gives "I2C begin Failed".
  - Arduino-ESP32 core **3.3.12** is installed (so ESP-NOW callbacks use `const esp_now_recv_info_t*`).
  - SDA = GPIO 22, SCL = GPIO 20. **GPIO 2 must be HIGH**: it powers the NeoPixel and I2C. The NeoPixel is on `PIN_NEOPIXEL`.
- **IMU: MPU-6050** (GY-521 board, 6-DOF, red power LED is normal) at I2C **0x68**. It is *not* the ICM-20948 that the rules PDF lists.
  - Wired with jumpers to header pins the user soldered.
  - Read through direct register access, no library (clone-safe): wake with 0x6B=0, set +/-16 g with 0x1C=0x18 (2048 counts per g), filter with 0x1A=0x03, accel data starts at 0x3B.
  - **It restarts asleep if a wire wiggles, and then repeats its last reading.** The sketch auto-recovers: it checks the sleep bit every 1 s, watches for 0.5 s of identical readings and for failed reads, and then does a full reset + wake. Keep this logic.
  - Measured on the real board: about 1.05 g at rest. A pillow drop dips to about 0.6 g, then spikes to about 3 g.
- **Machines:** ESP32 + HC-SR04 (2-400 cm range, 15 degree beam). Echo outputs 5 V and needs a 1k/2k divider into the ESP32.

## Message formats

**Machine to strap (ESP-NOW, channel 1), 4 bytes, packed:**
```c
typedef struct __attribute__((packed)) {
  uint8_t  magic;        // 0xD1
  uint8_t  machine_id;   // 1, 2, 3
  uint16_t distance_cm;
} DistancePacket;
```
The strap also accepts a plain float, a 4-byte int, a 2-byte int or text ("45", "M2:45") and then assigns machine IDs by sender MAC in the order heard. Anything else is counted as "NOT understood (len X)" on the status line. Machines send 10 times a second. Out of range is sent as 400 cm.

**Strap to base (UDP, Pi port 4210, strap listens on 4211), JSON:**
- Fall alert, resent every 0.5 s until ACK, giving up after 60 s:
  `{"type":"fall_alert","strap":1,"boot":<random per power-up>,"id":N,"peak_g":4.7,"severity":"fall"|"severe","zone":"red","machine":2,"distance_cm":40,"uptime_ms":...}`
- Status, once a second:
  `{"type":"status","strap":1,"zone":"red|yellow|green|none","machine":2,"distance_cm":40,"g":1.01,"alert_pending":false,"falls":0}`
- Pi to strap: the plain text `ACK <id>`. The Pi removes duplicate alerts by `(strap, boot, id)`.

**Network:** the Pi runs its own hotspot, `StrapBase` / `strapbase123`, on **channel 1**, at 10.42.0.1:
`sudo nmcli device wifi hotspot ifname wlan0 ssid StrapBase password strapbase123 band bg channel 1`.
Wi-Fi and ESP-NOW share the strap's single radio, so **every device must be on the same channel**. If the hotspot channel changes, `ESPNOW_CHANNEL` must change on the strap and all the machines.

## Strap logic and the numbers behind it

**Zones:** the closest machine heard in the last 1 s decides. RED < 50 cm, YELLOW < 150 cm, otherwise GREEN, with 10 cm hysteresis.
- These are a **1:6 scale of real haul-truck proximity systems**, where one industry design suggests about a 3-4.5 m danger zone and a 6-9 m warning zone.
- They stay well inside the HC-SR04's range. At 150 cm, its 15 degree beam is about person-width.

**Fall detection** runs at about 100 readings a second on the total acceleration (|a|):
1. Free fall: below **0.8 g** for at least 30 ms.
2. Impact: **3.0 g or more** within 1 s. A hit of **4.0 g or more** counts even with no free fall before it.
3. Settle: ignore 300 ms of bouncing.
4. Still: over 1 s, the spread of the readings must be **0.15 g or less**. That's when the fall is confirmed, about 1.3 s after impact.

Peaks of **6 g or more** are labelled "severe".

**Why these numbers:**
- Trunk-sensor research (Bourke et al. 2007) found the smallest fall impacts were about 3.5 g, while normal activity was 1-2.5 g.
- A wrist method uses a 3 g peak and a free-fall check at 0.8 g.
- The upper arm/shoulder sits between those two, so these are reasonable starting values. **They haven't yet been tuned on real falls.**
- The stillness step is what rejects hammering, sitting down hard and jumping.

**The rule:** send an alert only if a fall is confirmed **and** the worker was in RED within the last **4 s** (`RED_MEMORY_MS`). This allows for the ultrasonic losing a person who is on the floor.

## Testing and status

- `bash tests/run_tests.sh` (needs g++ and python3) passes **21/21**:
  - the message formats
  - the zone colours
  - falls in green or yellow are not sent, a fall in red is sent, and the Pi's ACK is received
  - hammering, a hard sit and a jump don't alert
  - the 4 s red memory
  - IMU power-blip recovery
- The stand-in headers in `tests/mock/` only imitate the ESP32 API. **Real-hardware status:** the MPU-6050 and its auto-recovery work on the board. The full strap + machines + Pi system has **not yet been run end to end on real hardware**.
- After you change `strap_main.ino`, rerun `tests/run_tests.sh`. If you change the behaviour, update the tests too.

## Not done / later

- MQ-2 gas sensor (the team will decide, so don't write it yet)
- "I'm OK" cancel button
- vibration
- machines physically stopping when the worker falls
- tuning thresholds on real mat falls (record the `g` values)
- the GUI on the Pi (teammate)
- the written submission and pitch

## Pitch guardrails (if asked about wording)

- Lead with the worker, not part numbers ("motion sensor", not "MPU-6050").
- Give measured numbers, or say it isn't measured yet. Don't claim the strap "detects every fall".
- Ultrasonic sensors measure the nearest **object**, not specifically the worker, so keep the demo area clear.
- It's an extra safety layer, never a replacement for guards or e-stops.
- No medical claims.
- A judge may ask "what about a fall outside the red zone?". The answer: in this design, only red-zone falls go to the base, to keep alerts meaningful. Reporting other falls at a lower priority is a small change.
