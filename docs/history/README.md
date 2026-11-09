# Where NearStop came from

NearStop merges four pieces built by different team members on 2026-11-07/08. These are their original briefs, kept for reference. **The current behaviour is described in the main TECHNICAL.md and CLAUDE.md; where these files disagree, those win.**

| File | Origin | What was taken from it |
|---|---|---|
| `strap_brief_Rafat.md` | Worker strap (natHacks demo, Rafat) | Fall detector, IMU auto-recovery, zone colours, "fall only counts in the red zone" rule → `firmware/worker_strap` (now fed by NearStop instead of ESP-NOW; adds an instant `impact` message) |
| `hardhat_integration_prompt.md` | HardHat package | Context only; its RSSI positioning backend was replaced by NearStop. The hard hat (buzzer + cancel button) is not integrated yet: it would make a good "I'm OK" answer (`checkin_ack`) |
| `hoist_motor_wiring_Part1.md` | HardHat Part 1 (hardware-tested) | Motor PWM + MOSFET + interrupt-timed HC-SR04 → `firmware/hoist_node` |
| `hardhat_buzzer_wiring_Part2.md` | HardHat Part 2 | Not integrated (see above) |

The NearStop backend/dashboard itself came from the team's `NearStop_Share` package (see `CONNECT_YOUR_MACHINE.md` for its original Wi-Fi-only machine protocol, which is unchanged). The USB path, strap integration, Hoist rules and tests were added afterwards.
