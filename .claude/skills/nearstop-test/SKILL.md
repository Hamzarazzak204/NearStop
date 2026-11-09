---
name: nearstop-test
description: Verify NearStop changes - the no-hardware test suite, firmware compile checks, and live tests with real boards (strap test commands, timing a fall-to-stop). Use after any backend/firmware change, or when asked "does it work / test it".
---

# Test NearStop

## 1. No hardware (always first)
`powershell -ExecutionPolicy Bypass -File run_tests.ps1` → backend tests (`tests/test_nearstop.py`, real receiver + registry + safety fed with real JSON) and a compile of every sketch. Add `-SkipFirmware` for speed while iterating on Python.

**Adding a rule = adding a scenario** in `tests/test_nearstop.py` (`scenario({"GW":20,"MB":100}, "GW", 20)` = readings per machine, then the strap's reported machine/distance for impact + fall_alert). Check machine states and the commands list.

## 2. Live, with boards
- Backend restarted with the change, bridge running, boards online (`/api/state`).
- **Strap test commands** (type on its serial port; stop the bridge first so you can open COM, the strap then uses Wi-Fi): `3 30` = pretend the Hoist sees Sam at 30 cm (machine 1 GW, 2 MA, 3 MB), `i` = fake hard hit (impact), `f` = fake confirmed fall. These are FAKES: say so in your report.
- Timing a stop: open the hoist and strap ports together, timestamp lines with a Stopwatch; hoist prints `# [NEARSTOP via …] command hold/lock` and `# [HOIST] -> locked`, reports `"motor_pct":0`. Measured: impact→hold 0.03 s, fall→lock 0.2 s.
- Events: `GET /api/events?since=0` (the timeline the dashboard shows). Machine reasons: `/api/state` → `site.machines[].reason`, `restart_blocked_reason`.
- Dashboard check: open http://localhost:8000 (browser pane), hard-refresh, read `#ns-machines` / `#ns-workers` text and the console for errors.

## 3. Interpreting surprises
A live test can be spoiled by the real sensors (an object at 6 cm makes NearStop think Sam is close). Read the events for "Person 0.06 m from Hoist" before blaming the logic; reproduce in `tests/test_nearstop.py` with clean readings.

## Report format
What changed → what was run (tests: N passed; compiled sketches; live steps) → what was hardware-proven vs. faked → what's left / what the user must check physically.
