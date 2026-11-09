"""
Central configuration for the NearStop backend.

Site layout (machine positions, sensor facing angles, warning/danger
distances, names) lives in backend/site_config.json and can be edited from
the dashboard (Setup -> Machines & site).
"""

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------
UDP_HOST = "0.0.0.0"      # listen on every network interface
# Ports can be overridden with environment variables (used for testing a
# second copy next to the real one); normally you never need to.
UDP_PORT = int(os.environ.get("NAT_UDP_PORT", 5005))   # boards send to this port
HTTP_HOST = "0.0.0.0"     # dashboard reachable from other devices on the LAN
HTTP_PORT = int(os.environ.get("NAT_HTTP_PORT", 8000))

# ---------------------------------------------------------------------------
# Timing
# ---------------------------------------------------------------------------
HEARTBEAT_TIMEOUT_S = 3.0   # device offline after this long without any message
RADIO_FALL_MEMORY_S = 1.5   # radio machines: a fall locks them only if they heard Sam (strongly) this recently.
                            # The strap keeps beaconing after a fall, so no long memory is needed.
RADIO_RSSI_AVG_S = 2.0      # strap signal strength is averaged over this long (one lucky packet doesn't count)
RADIO_NEAR_CM = 30          # a machine that HEARS Sam's strap treats him as this close (no distance by
                            # radio; hearing it at all means nearby). Its own ultrasonic reading is used
                            # if that is closer.
WORKER_TIMEOUT_S = 8.0      # worker straps: longer, their Wi-Fi (no antenna, battery) loses packets.
                            # Falls don't depend on this: the strap resends them until ACKed.

# Worker strap (firmware/worker_strap): its numbers -> NearStop IDs
STRAP_WORKERS = {1: "W1"}                          # "strap": 1 -> worker W1
STRAP_MACHINE_IDS = {1: "GW", 2: "MA", 3: "MB"}    # "machine": 1/2/3 in strap messages
IMPACT_HOLD_S = 3.0         # impact_hold machines resume if no fall is confirmed this long after a hit
FALL_NEAR_MEMORY_S = 4.0    # a fall locks out machines that saw the worker within their
                            # warning distance in the last 4 s (same as the strap's red memory)
SENSOR_TIMEOUT_S = 2.0      # machine sensor = "Error" after this long without a machine message
BROADCAST_HZ = 5.0          # dashboard updates per second over the WebSocket

# ---------------------------------------------------------------------------
# Devices we expect to see. They are listed on the dashboard even before
# they have sent anything, so a missing board is obvious.
# (Names and machine settings are in site_config.json.)
# ---------------------------------------------------------------------------
EXPECTED_DEVICES = {
    "MA": "machine",
    "MB": "machine",
    "W1": "worker",
}

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BACKEND_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BACKEND_DIR.parent / "frontend"
SITE_CONFIG_FILE = Path(os.environ.get("NAT_SITE_CONFIG", BACKEND_DIR / "site_config.json"))
