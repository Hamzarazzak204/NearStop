---
name: nearstop-safety-rules
description: Change or explain how NearStop decides when machines slow, stop, hold or lock (presence zones, fall lockout, impact hold, restart blockers, per-machine settings), or add a new machine. Use for requests like "the hoist should only stop when…", "make the conveyor…", "add a machine".
---

# NearStop safety rules

All rules live in `backend/safety.py`; per-machine settings in `backend/site_config.json` (validated by `site_config.py`, editable in Setup > Machines & site for name/position/facing/distances).

## Per-machine settings
| Key | Default | Meaning |
|---|---|---|
| `warning_cm` / `danger_cm` | 150 / 50 | Zone distances (grow with worker strain; sent to the board with `set_zones`) |
| `safe_action` | `stop` | `hold` = Holding instead of Stopped (Hoist) |
| `presence_stop` | `true` | `false`: a person in front never slows/stops it (Hoist) |
| `fall_stop_zone` | `warning` | Which zone counts as "near" for the fall lockout; `danger` = only when close (Hoist) |
| `impact_hold` | `false` | Hold instantly on the strap's `impact`, release after `IMPACT_HOLD_S` if no fall (Hoist) |

Firmware must agree: the hoist board has `PRESENCE_STOP 0` in `firmware/hoist_node/config.h` (otherwise it would hold itself locally).

## Where things happen in safety.py
- `update()` (5 Hz): zones with hysteresis → `_update_machine` (presence) → `_release_impact_holds` → check-in timeouts.
- `_process_falls_and_acks` → `_fall_lockout` (which machines lock, returns the primary machine) → `start_checkin(..., near_override)` (pins the pop-up to that machine). Called instantly by `registry.on_fall` too.
- `on_impact` (instant, from `registry.on_impact`).
- `restart_blockers` (why Approve restart is refused), `approve_restart`.
- `_worker_snapshot` / `_machine_snapshot` feed the dashboard (`frontend/site.js`: `nsRenderBanner`, `nsMachineBody`, `nsWorkerBody`, `nsTickCountdown`).

## Changing a rule (checklist)
1. Confirm the exact wanted behaviour with the user in one sentence ("close" = danger zone, "nearby" = warning zone).
2. Prefer a per-machine setting over a special case; keep other machines and the strap unchanged unless asked.
3. Update safety.py (+ site_config.py defaults/normalise + site_config.json), and firmware if the board decides locally.
4. Add scenarios to `tests/test_nearstop.py`; run `run_tests.ps1`.
5. Restart the backend; live-test (skill nearstop-test); update TECHNICAL.md (Hoist section 1.3 / strap 1.2).

## Adding a machine
Add it to `site_config.json` (`machines`) and `config.EXPECTED_DEVICES`, flash `machine_node_usb` with its `DEVICE_ID`, and if the strap should know it, extend `MACHINE_NAMES` in `worker_strap.ino` and `STRAP_MACHINE_IDS` in `backend/config.py` (the strap tracks 3 machines).
