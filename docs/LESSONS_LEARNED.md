# Lessons learned (2026-11-07/08 build session)

Every problem below was hit for real on this hardware. Check here before debugging.

## Hardware
| Symptom | Cause | Fix / check |
|---|---|---|
| Wi-Fi connects slowly or not at all; ESP-NOW RSSI -85…-97 dBm even 1 m apart; packets drop for seconds | Boards are **ESP32-PICO-MINI-02U** (u.FL **external-antenna** version) with **no antenna** fitted. esptool reports "ESP32-PICO-V3-02" for both versions, so software can't tell. | Read the module's label (…**02U**). Fit 2.4 GHz u.FL/IPEX antennas. Meanwhile use USB + `tools/usb_bridge.py`, keep boards near the phone hotspot. |
| A machine stays Stopped/Holding; restart refused "person 0.13 m in front" | The HC-SR04 sees a nearby object (table edge, wires, motor, wall) | Aim the sensor at open space; nothing within ~1.6 m. The sensor can't tell a person from an object. |
| Distance jumps 5 cm ↔ 390 cm | Something flickering at the edge of the beam, or a loose ECHO wire / divider joint | Clear the area, reseat wires and the divider resistors. |
| Motor doesn't spin although NearStop says Running | Fault after the Feather: A0 was proven switching (20 kHz, 54 % duty) with `firmware/diagnostics/a0_pwm_test`. Drain had no voltage → motor supply not reaching drain. | Battery pack voltage, **battery − to Feather GND**, battery + to motor +, MOSFET orientation (SO-8 pin 1 dot), Zener band to motor +. |
| Board printed `rst:0x1 (POWERON_RESET)` while the motor ran at 100 % | Board lost power (motor current dragging the supply, or a replug) | Separate motor battery, shared ground only, 10 µF+ across motor supply. Don't run above `MAX_PCT 90` for long. |
| Board prints garbage at 115200 | Another sketch / reboot loop | Reset with RTS and read the boot lines; a teammate's buzzer test was on that board. |
| `E BOD: Brownout detector was triggered` loop (teammate) | Buzzer/motor diode reversed = short | Diode band toward + supply. |
| HC-SR04 ECHO is 5 V | ESP32 pins are 3.3 V | Divider: 1 kΩ + 2 kΩ (2.2 kΩ ok), or 10k + 20k. Top resistor ECHO→pin, bottom pin→GND. |
| Board has no LED on GPIO 2 | Feather V2 red LED is **GPIO 13**; NeoPixel GPIO 0 with power GPIO 2; button GPIO 38 | — |

## Firmware
| Symptom | Cause | Fix |
|---|---|---|
| Board associates with the hotspot but never gets an IP (core 3.x) | `esp_now_init()` called right after `WiFi.begin()` | Start ESP-NOW **after** `WL_CONNECTED` (or after a 15 s fallback). Proven with a side-by-side test. |
| `"sensor":"no_reading"` while distances are valid | `millis()` captured before the reading, compared with a timestamp set during it → unsigned underflow | Compare with a fresh `millis()`. |
| Strap fall alert never confirmed over USB | Bridge only forwarded JSON; NearStop's reply is plain `ACK <id>` | Bridge passes `ACK…` lines through. |
| Strap fall_alert rejected/ignored | Its `id` is the alert NUMBER, not the device | Identify the strap by `worker` (or `strap` number). |
| Machine stopped by noise | Blocking `pulseIn` + motor | Hoist uses interrupt-timed echo (teammate's Part 1). |
| Two programs fight over a COM port | Only one program can open a port | Stop the bridge / close the Serial Monitor before uploading. |
| COM port number changes (COM3→4→5→6) | Windows renumbers on replug | Pick the current port each time; identify boards by reading their serial output (`"id":"MB"`). |
| Pasted code fails with `attribute`, `uint8_tdata` errors | Chat/markdown ate `__` and `*` | Share `.ino` files, not pasted text. |
| Strap pressed "I'm OK" again every second over USB | NearStop's reply `OKACK 1` came back as a serial line, and the strap's `ok` test command matched its first two letters | Route `OKACK` lines to the NearStop handler first; the `ok` command must be exactly `ok` |
| Strap `T/H --`, DHT11 "never answered" | First: no DHT11 on GPIO 33. Then: a pull-up is on GPIO 33 but the sensor never pulls the line low after the start pulse (0 edges; Adafruit's library fails too) = not powered, or + and data swapped (many modules are `+ / OUT / -` with data in the MIDDLE) | `firmware/diagnostics/strap_sensor_check` prints the pull-up test, a raw edge count and the library read |
| DHT11 dead with power + ground confirmed (2026-11-08, strap W1) | `dht_pin_finder`: GPIO 33 is the only pin with a pull-up (data wire in the right place, not shorted to 3V), but no pin gets any answer to the start pulse: the DHT11 element itself is faulty | Swap the DHT11. Checked with `firmware/diagnostics/dht_pin_finder` (no library needed) |
| Strap on Wi-Fi: almost none of its broadcast status reached NearStop while a ping to it worked | Wi-Fi broadcast is sent once, at the lowest rate, with no retries; with no antenna most were lost. A second NearStop on the hotspot (a teammate's laptop) also made the strap flip between hubs | Set `NEARSTOP_IP` in the strap's `config.h` to the laptop's hotspot IP: direct packets are retried by Wi-Fi and other hubs are ignored. Run only ONE NearStop per hotspot |
| Button, DHT11 AND MQ-2 all dead at once (button never pulls GPIO 14 low, DHT11 never answers, MQ-2 flat at the ADC floor 0.14 V on the pin = 0.21 V shown) | One shared fault: the breadboard GND rail not connected to the Feather GND (or a split rail) | Check GND first: Feather GND -> rail, and every part's GND on that same rail. `strap_sensor_check` shows all three |
| MQ-2 pin reads a flat 3.15 V (4.72 V at the sensor), no noise | The pin is at or above the ESP32's maximum: AO not through the 10k/20k divider, or touching 3V/5V | Check the divider before powering the MQ-2 again: 5 V on GPIO 34 can damage it |
| Arduino `'GasLevel' was not declared` | Arduino adds function prototypes above the enum | Take an `int` parameter instead of the enum type |
| MAC prints 00:00:00:00:00:00 | `WiFi.macAddress()` before the stack is up | `esp_read_mac(mac, ESP_MAC_WIFI_STA)`. |

## Backend / dashboard
| Symptom | Cause | Fix |
|---|---|---|
| Dashboard console full of `reference_rssi` errors | Browser cached the old NAT dashboard's app.js (same address) | Hard refresh / `fetch(url,{cache:'reload'})`. |
| Fall pop-up said "near Robot Arm" for a fall at the Hoist | Check-in used the closest detection; Robot Arm's sensor saw a nearby object | Fall check-ins are **pinned** to the machine the worker fell at. |
| Delay between fall and motor stop | Strap confirms a fall only after ~1.3 s of stillness (rejects hammering/jumps) | `impact` message → Hoist holds instantly; falls/impacts handled on packet arrival, not the 5 Hz tick. |
| Old `.venv` from another PC | Copied project; venv paths/versions don't match | Delete `.venv` and run `setup.ps1`. Never `python -m venv` over an existing venv. |
| Boards can't reach the laptop | Laptop on **eduroam** (can't join, client isolation) | Laptop and boards on the same **2.4 GHz** hotspot; allow Python through Windows Firewall (Public). |

## Process
- Confirm hardware facts before designing (module label, wiring photo, what sketch is running).
- Each change → `run_tests.ps1` → flash → read serial + `/api/state` → say exactly what was proven.
- Back up a teammate's board before overwriting it: `python -m esptool --port COMx read-flash 0x0 0x150000 backup.bin`.

## From the radio-presence / motor_node work (anchorv1, merged 2026-11-08)
| Symptom | Cause | Fix / check |
|---|---|---|
| `'esp_now_recv_info_t' does not name a type` | Arduino auto-generates prototypes near the top of the .ino, before an `#include` placed mid-file | Put `#include <esp_now.h>` / `<esp_wifi.h>` with the other includes at the top |
| "Motor stops as soon as the sensor firmware runs" (Conveyor) | Firmware was right: the pin self-check (`# [PWM] motor pin GPIO25: high 45%`) showed PWM, then the sensor saw an object ~14 cm away (50/50 readings with the motor OFF, so not motor noise) and the Conveyor stopped by design | Aim the sensor at open space; check the `# [PWM]` and `dist_cm` lines before suspecting the motor stage |
| Board went **Locked** although this laptop's NearStop logged nothing | Laptop was on eduroam; the board (bridge stopped) broadcast over the hotspot and **another NearStop on the hotspot** (a teammate's laptop) answered and sent `lock` | Run **one NearStop per network**. Firmware now ignores Wi-Fi commands while the USB bridge is connected. Reflash or Approve restart clears a foreign lock |
| Motor stage dead although A1/A0 switches | **IRF630N / plain 10N06 are not logic-level** (need ~10 V on the gate); drain stayed at the supply voltage | Use a logic-level MOSFET (IRLZ44N, IRLB8721, IRL540N, BSO110N03MS) or a driver stage |
| NearStop says Stopped/Locked but the board keeps running | (1) The stop is sent once, on the state change; a board that missed it (reflashed, rebooted) never knew. (2) **Windows UDP quirk**: after NearStop restarts, the bridge's next `recvfrom` raised `ConnectionResetError` and the NearStop→board thread died silently (reports still went up, commands never came down) | NearStop now **re-sends stop/hold/lock every 1 s until the board reports that state**, and **adopts a "locked" a board reports** after a restart. The bridge ignores `ConnectionResetError` (and disables `SIO_UDP_CONNRESET`). Proven: commands still delivered after two NearStop restarts |
| Board says `running` but `motor_pct` 0 and the pin reads 0 % | A brief sensor glitch called `stopMotorNow()` (target 0); with presence ignored the state never changed, so nothing set the speed again | `driveMotor()` re-applies RUN/SLOW target every loop while Running/Slowed; sensor glitches only stop presence-stop machines |
| Approve restart flips straight back to Locked | Board still reported `locked` for a moment after "resume", and NearStop re-adopted the lock | No re-adopting for `RESUME_GRACE_S` (5 s) after a resume (test added) |
| Conveyor keeps getting locked whenever it's briefly on Wi-Fi (start-up, bridge restart) | A second NearStop (teammate's laptop) on the hotspot answers its broadcasts | Close every other NearStop on the hotspot. Restart the bridge quickly (the board waits 3 s before falling back to Wi-Fi) |
