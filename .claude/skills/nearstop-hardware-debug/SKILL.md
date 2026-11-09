---
name: nearstop-hardware-debug
description: Diagnose NearStop hardware problems - weak radio/no antenna, Wi-Fi not joining, ultrasonic reading nearby objects or jumping, motor not spinning, resets/brownouts, garbled serial. Use when a board misbehaves physically or software looks right but the device doesn't.
---

# Hardware debugging playbook

Always start with `docs/LESSONS_LEARNED.md`. Then narrow down with measurements, not guesses.

## Radio / Wi-Fi
1. Module label: **PICO-MINI-02U** = needs a u.FL antenna. No antenna → expect -85…-97 dBm, drops, slow joins. Use USB + bridge; antennas are the real fix.
2. Laptop network: `netsh wlan show interfaces` must show the 2.4 GHz hotspot, not eduroam.
3. Joins but no IP → ESP-NOW started before Wi-Fi connected (see lessons). Machine/hoist sketches don't use ESP-NOW; the strap starts it after connecting.
4. Which channel are things on? Flash `firmware/diagnostics/espnow_sniffer` (scans ch 1-13, prints ESP-NOW frames + "sniffer is working" for other frames).

## Ultrasonic (HC-SR04)
- Readings stuck < 50 cm or jumping 5 ↔ 390 cm → something in/near the beam (table edge, wires, motor) or a loose ECHO/divider joint. Ask the user to aim at open space and reseat wires; confirm with the board's serial `dist_cm`.
- No echo pulse at all → `sensor: error` (wiring, no 5 V on VCC: the USB pin only has 5 V on USB power).
- Echo must go through the divider (5 V → 3.3 V).

## Motor (Hoist) not spinning
1. Is NearStop letting it run? `/api/state` → MB `Running` and the board's `"motor_pct"` > 0. Holding/Locked = by design (read `reason`).
2. Is A0 switching? Flash `firmware/diagnostics/a0_pwm_test` (`TEST_PCT`, `TEST_SECONDS` at the top). It drives the motor **ignoring the sensor** (warn: hands clear) and prints `high 53.9% … ~20.0 kHz`. Reflash `hoist_node` after.
3. A0 OK but no spin → the power stage. Ask for meter readings against Feather GND: battery pack (~6.6 V), motor + (~6.6 V), drain with motor off (~6.6 V; 0 V = no supply/ground or drain not wired), source pins continuity to GND, gate vs source ~1.7 V average while running. Common causes: battery − not tied to Feather GND, flat/unlinked packs, rotated SO-8 MOSFET, missing/reversed Zener.
4. `POWERON_RESET` / brownout while running → supply sag: separate motor battery, shared ground, bulk capacitor.

## Serial looks wrong
- Garbage at 115200: reset (RTS pulse, DTR low) and read boot lines; it may run a different sketch.
- Open ports with DTR/RTS **false** so reading doesn't reset the board.
- "Access denied" → the bridge or Serial Monitor holds the port.

## Before overwriting someone's board
Back it up with esptool `read-flash 0x0 0x150000 backup.bin` and say where the backup is.
