# Legacy: RSSI positioning (not used)

NearStop originally estimated the worker's 2D position from ESP-NOW signal
strength (RSSI) measured by three anchors (trilateration). It has been replaced
by an HC-SR04 ultrasonic distance sensor on each machine.

The code is kept here in case it is added back:

| File | What it contained |
|---|---|
| `rssi_positioning.py` | RSSI → distance model, median filter, least-squares trilateration, EMA smoothing, uncertainty radius, `PositioningSettings` (anchors, calibration, settings.json) |
| `rssi_registry.py` | The device-registry methods and config constants that stored RSSI samples (`record_rssi`, `fresh_rssi`, `latest_rssi`) |

The node firmware that sends the RSSI measurements (`firmware/node/`) is unchanged.

To bring it back you would need to: import these again in `main.py`, call
`PositioningEngine.compute()` in `build_state()`, re-add the `rssi_measurement`
handler in `udp_receiver.py`, and re-add the calibration/anchor endpoints and UI.
Look at git history before commit "Ultrasonic sensing" for the exact wiring.
