# Part 1: Machine motor + HC-SR04 ultrasonic presence stop

Sketch: `Part1_Machine_Motor_Ultrasonic.ino` (standalone, no Wi-Fi). Board: Adafruit Feather ESP32 V2.

## Status
- **Tested on hardware** (2026-11-07): distances read correctly (36–57 cm, up to ~4.7 m), and the motor stops when someone is within 1 m.
- Stop range: `STOP_CM = 100`, clear at `CLEAR_CM = 120` after 2 s clear, then the motor ramps back up (auto-restart).

## Motor (low-side MOSFET switch)
| From | To |
|---|---|
| GPIO26 (A0) | 220 Ω → MOSFET gate (BSO110N03MS, SO-8: pins 1–3 S, pin 4 G, pins 5–8 D) |
| MOSFET gate | 10 kΩ → GND (keeps motor OFF during boot/reset/flash) |
| MOSFET source | GND |
| MOSFET drain | motor − |
| motor + | +6.6 V (two 3.3 V packs in series) |
| 10 V Zener (DO-41) | across the motor, **band toward motor +**. REQUIRED, or the 20 kHz PWM won't spin the motor |
| battery − | ESP32 GND (shared ground) |
| 10 µF+ cap | across the motor supply (recommended) |

## HC-SR04
| HC-SR04 | Feather |
|---|---|
| VCC | USB pin (5 V, only while USB is plugged in) |
| GND | GND |
| Trig | A1 (GPIO25), direct |
| Echo | 10 kΩ → A2 (GPIO34), and A2 → 20 kΩ → GND. **Echo is 5 V: never connect it direct** |

## Behaviour
- Person closer than 1 m → `stopNow()` instantly (no ramp). NeoPixel red.
- No echo pulse at all (sensor unplugged/dead) → treated as a fault → motor stays stopped. NeoPixel yellow.
- Clear → green, motor ramps to `runPct` (60 %, type 0–100 in Serial Monitor).
- Echo timing is interrupt-based, so the sensor never blocks the loop.
