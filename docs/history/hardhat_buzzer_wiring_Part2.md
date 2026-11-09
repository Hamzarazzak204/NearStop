# Part 2: Hard hat buzzer + fall detection + cancel button

Sketch: `Part2_HardHat_Buzzer.ino` + `config.h` (copy of `NAT_Basestation/firmware/hat`). Board: Adafruit Feather ESP32 V2.

## Status
- **Buzzer circuit tested on hardware** (2026-11-07) with a 2048 Hz test tone on GPIO27: beeps.
- Full sketch: compiles and boots. The fall flow was triggered from Serial (`f` → PENDING) but **not yet completed on hardware**, because the buzzer short (now fixed) kept resetting the board.
- MPU-6050 **not wired yet** (sketch prints "IMU NOT FOUND", retries every 2 s, reports `imu_ok:false` to the hub).

## Behaviour
1. IMU detects a fall (free fall < 0.5 g for 80 ms, then impact > 2.5 g within 1 s, OR a single impact > 4 g).
2. Buzzer beeps for 7 s (faster in the last 2 s). Hello carries `pend:1`.
3. Button pressed within 7 s → false alarm: short chirp, `hat_event "cancelled"`, nothing else.
4. Not pressed → `hat_event "fall"` (UDP, 3 copies) + `fall:1` in every ESP-NOW hello and heartbeat. Buzzer gives a "find me" beep every 2 s. Red banner on the dashboard.
5. Hold the button 3 s → clears the alert (`hat_event "cleared"`).
Serial: `f` = fake fall, `b` = button, `g` = print live g-force for tuning.

## Buzzer CEM-1203(42): magnetic, 42 Ω, ~80 mA, needs a tone (2048 Hz = loudest)
Too much current for a GPIO pin, so it goes through a MOSFET:

```
3V ──────────┬──────── buzzer leg B
             │
       10 V Zener   ← BAND ON THE 3V SIDE (backwards = dead short -> brownout reset loop)
             │
             ├──────── buzzer leg A
             │
        MOSFET drain (pins 5-8)
        MOSFET source (pins 1-3) ── GND
GPIO27 ──[220 Ω]── gate (pin 4) ──[10 kΩ]── GND
```

## Other parts
| Part | Wiring |
|---|---|
| MPU-6050 (GY-521) | VCC → 3V, GND → GND, SDA → SDA, SCL → SCL (address 0x68). `Wire.begin()` with no arguments |
| Cancel button | GPIO14 ↔ GND (internal pull-up). On-board SW38 (GPIO38) also works |

## Lessons from debugging
- Floating gate (no 10 kΩ) → buzzer hums quietly by itself.
- Diode in series instead of across the buzzer → no sound. Diode backwards → brownout loop (`E BOD: Brownout detector was triggered`).
- A steady HIGH on a magnetic buzzer only clicks. It needs a tone to buzz.
