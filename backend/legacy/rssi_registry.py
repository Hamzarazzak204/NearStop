"""
LEGACY (not imported anywhere): RSSI parts removed from the live backend.

These were part of device_registry.DeviceRegistry and config.py while NearStop
used RSSI trilateration. Kept for reference; see README.md in this folder.
"""

import time
from collections import deque

# ---- former config.py constants -------------------------------------------
RSSI_MAX_AGE_S = 2.0
DEFAULT_ROOM = {"width": 10.0, "height": 8.0}
DEFAULT_ANCHORS = {"GW": {"x": 1.0, "y": 1.0}, "MA": {"x": 9.0, "y": 1.0}, "MB": {"x": 5.0, "y": 7.0}}
DEFAULT_REFERENCE_RSSI = -50.0
DEFAULT_PATH_LOSS_EXPONENT = 2.5
RSSI_MEDIAN_WINDOW = 5
POSITION_EMA_ALPHA = 0.3
EMA_RESET_AFTER_S = 3.0
ASSUMED_RSSI_NOISE_DB = 4.0
MIN_UNCERTAINTY_M = 0.5
MIN_ANCHOR_TRIANGLE_AREA_M2 = 0.5
ANCHOR_IDS = ["GW", "MA", "MB"]
WORKER_IDS = ["W1"]


class RssiRegistryMixin:
    """Methods that lived on DeviceRegistry. Each Device had
    `rssi_samples: dict[str, deque]` (worker id -> deque of (time, rssi))."""

    def record_rssi(self, anchor_id, worker_id, rssi, addr, msg):
        anchor = self.record_packet(anchor_id, addr, msg)
        samples = anchor.rssi_samples.setdefault(worker_id, deque(maxlen=RSSI_MEDIAN_WINDOW * 4))
        samples.append((time.time(), float(rssi)))

    def fresh_rssi(self, anchor_id, worker_id, now):
        dev = self.devices.get(anchor_id)
        if dev is None or worker_id not in dev.rssi_samples:
            return []
        fresh = [r for (t, r) in dev.rssi_samples[worker_id] if now - t <= RSSI_MAX_AGE_S]
        return fresh[-RSSI_MEDIAN_WINDOW:]

    def latest_rssi(self, anchor_id, worker_id):
        dev = self.devices.get(anchor_id)
        if dev is None or not dev.rssi_samples.get(worker_id):
            return None
        return dev.rssi_samples[worker_id][-1]


# udp_receiver.py handled this message:
#   {"type": "rssi_measurement", "anchor_id": "MA", "worker_id": "W1", "rssi": -61, "timestamp": 123456}
#   -> registry.record_rssi(anchor_id, worker_id, rssi, addr, msg) after checking -127 <= rssi <= 0
