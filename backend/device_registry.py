"""
Device registry: remembers every ESP32 we have heard from.

For each device it tracks role, last-seen time, heartbeat time, source IP,
packet counters, the latest machine sensor reading (machines) and vest
telemetry (workers).

Everything here runs inside the single asyncio event loop (the UDP receiver
and the web server share it), so no locks are needed.
"""

import time
from collections import deque
from dataclasses import dataclass, field

import config

ENV_HISTORY_STEP_S = 2.0      # one chart point every 2 s ...
ENV_HISTORY_POINTS = 300      # ... for the last 10 minutes


@dataclass
class Device:
    device_id: str
    role: str = "unknown"
    ip: str | None = None
    port: int | None = None
    first_seen: float | None = None     # time.time() of first packet
    last_seen: float | None = None      # any packet
    last_heartbeat: float | None = None
    packet_count: int = 0
    heartbeat_count: int = 0
    last_message_type: str | None = None
    simulated: bool = False             # True if packets come from simulator.py
    # Optional extra info the firmware puts in its heartbeat
    wifi_rssi: int | None = None        # RSSI of the ESP32's link to the router
    uptime_ms: int | None = None
    seq: int | None = None
    channel: int | None = None
    mac: str | None = None
    # Optional worker-tag telemetry (activity, heat_c, strain). The current
    # firmware has no such sensors, so for real tags this stays empty; the
    # simulator fills it in for the demo.
    telemetry: dict = field(default_factory=dict)
    fall_count: int = 0                 # rising edges of the "fall" flag
    fall_active: bool = False
    # Latest machine message: {"t", "dist_cm", "zone", "state", "sensor",
    # "warning_cm", "danger_cm"} (machines only)
    machine: dict = field(default_factory=dict)
    machine_count: int = 0
    # Recent (time, rssi) of Sam's strap beacon as heard by this machine (radio presence)
    rssi_hist: deque = field(default_factory=lambda: deque(maxlen=40))
    # Recent packet arrival times, used for a packets/second figure
    recent_packets: deque = field(default_factory=lambda: deque(maxlen=50))
    # Worker environment sensors (strap BMP180 + MQ-2): (t, temp_c, pressure_hpa, gas_mv, gas_ratio),
    # one point every ENV_HISTORY_STEP_S, for the dashboard's Monitoring charts
    env_history: deque = field(default_factory=lambda: deque(maxlen=ENV_HISTORY_POINTS))

    def timeout_s(self) -> float:
        return config.WORKER_TIMEOUT_S if self.role == "worker" else config.HEARTBEAT_TIMEOUT_S

    def is_online(self, now: float) -> bool:
        return (
            self.last_heartbeat is not None
            and now - self.last_heartbeat <= self.timeout_s()
        )

    def packets_per_second(self, now: float, window: float = 5.0) -> float:
        n = sum(1 for t in self.recent_packets if now - t <= window)
        return round(n / window, 1)


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


class DeviceRegistry:
    def __init__(self) -> None:
        self.devices: dict[str, Device] = {}
        self.acks: deque = deque(maxlen=20)   # (device_id, time) check-in answers
        self.ack_count = 0                    # answers ever received (the safety engine counts new ones)
        self._strap_alerts_seen: deque = deque(maxlen=50)   # (strap, boot, id) of strap fall alerts
        self._strap_oks_seen: deque = deque(maxlen=50)      # (strap, boot, id) of "I'm OK" presses
        self.worker_oks: deque = deque(maxlen=20)           # (worker, time, for_alert, secs_after_alert)
        # Set by main.py so falls / impacts are handled the instant they arrive
        # (not on the next ~0.2 s update tick).
        self.on_fall = None
        self.on_impact = None
        # Pre-register the expected boards so the dashboard lists them
        # (as "never seen") before they connect.
        for device_id, role in config.EXPECTED_DEVICES.items():
            self.devices[device_id] = Device(device_id=device_id, role=role)

    # ------------------------------------------------------------------
    # Updates (called by the UDP receiver)
    # ------------------------------------------------------------------
    def _get_or_create(self, device_id: str) -> Device:
        if device_id not in self.devices:
            print(f"[registry] new device seen: {device_id}")
            self.devices[device_id] = Device(device_id=device_id)
        return self.devices[device_id]

    def record_packet(self, device_id: str, addr: tuple[str, int], msg: dict) -> Device:
        """Update bookkeeping common to every packet type."""
        now = time.time()
        dev = self._get_or_create(device_id)
        if dev.first_seen is None:
            dev.first_seen = now
        dev.last_seen = now
        dev.ip, dev.port = addr[0], addr[1]
        dev.packet_count += 1
        dev.last_message_type = msg.get("type")
        dev.simulated = bool(msg.get("sim", False))
        dev.recent_packets.append(now)
        role = msg.get("role")
        if isinstance(role, str) and role in ("machine", "anchor", "worker", "gateway"):
            dev.role = role
        return dev

    def record_heartbeat(self, device_id: str, addr: tuple[str, int], msg: dict) -> None:
        dev = self.record_packet(device_id, addr, msg)
        was_online = dev.is_online(time.time())
        dev.last_heartbeat = time.time()
        dev.heartbeat_count += 1
        # Optional diagnostic fields; ignore them if missing or the wrong type
        # The connection-test firmware uses different names: "uptime" (seconds)
        # and "rssi" (Wi-Fi signal). Translate them to the names used here.
        fields = dict(msg)
        if "uptime_ms" not in fields and _is_number(fields.get("uptime")):
            fields["uptime_ms"] = fields["uptime"] * 1000
        if "wifi_rssi" not in fields and _is_number(fields.get("rssi")):
            fields["wifi_rssi"] = fields["rssi"]
        for key in ("wifi_rssi", "uptime_ms", "seq", "channel"):
            value = fields.get(key)
            if _is_number(value):
                setattr(dev, key, int(value))
        if isinstance(msg.get("mac"), str) and len(msg["mac"]) <= 32:
            dev.mac = msg["mac"]
        self._record_telemetry(dev, msg)
        self._record_env(dev, msg)
        if not was_online:
            print(f"[registry] {device_id} is ONLINE ({addr[0]})")

    def _record_telemetry(self, dev: Device, msg: dict) -> None:
        if isinstance(msg.get("activity"), str):
            dev.telemetry["activity"] = msg["activity"][:24]
        if _is_number(msg.get("heat_c")):
            dev.telemetry["heat_c"] = round(float(msg["heat_c"]), 1)
        if _is_number(msg.get("strain")):
            dev.telemetry["strain"] = min(max(float(msg["strain"]), 0.0), 1.0)
        fall = msg.get("fall") is True
        if fall and not dev.fall_active:
            dev.fall_count += 1          # the safety engine reacts to this
        dev.fall_active = fall

    def _record_env(self, dev: Device, msg: dict) -> None:
        """Environment readings a worker's strap/vest may include:
        "temp_c" (air, BMP180), "pressure_hpa" (air pressure, BMP180), "gas_mv" (MQ-2 output), "gas_warm".
        null / missing = no reading from that sensor."""
        if not any(k in msg for k in ("temp_c", "pressure_hpa", "gas_mv")):
            return
        now = time.time()

        def num(key, lo, hi):
            v = msg.get(key)
            return round(float(v), 2 if key == "pressure_hpa" else 1) if _is_number(v) and lo <= v <= hi else None

        env = {"t": now, "temp_c": num("temp_c", -40, 85), "pressure_hpa": num("pressure_hpa", 300, 1100),
               "gas_mv": num("gas_mv", 0, 6000), "gas_warm": msg.get("gas_warm") is not False}
        # Strap MQ-2 module (team's gas_test logic): its own level from the sensor
        # resistance vs clean air. Used instead of NearStop's mV thresholds when present.
        ratio = msg.get("gas_ratio")
        env["gas_ratio"] = round(float(ratio), 3) if _is_number(ratio) and 0 <= ratio <= 20 else None
        level = msg.get("gas_level")
        env["gas_strap_level"] = level if level in ("warming", "green", "yellow", "red", "fault") else None
        dev.telemetry["env"] = env
        last = dev.env_history[-1][0] if dev.env_history else 0
        if now - last >= ENV_HISTORY_STEP_S - 0.05:
            dev.env_history.append((now, env["temp_c"], env["pressure_hpa"], env["gas_mv"], env["gas_ratio"]))

    def env_history(self, device_id: str) -> list[dict]:
        dev = self.devices.get(device_id)
        if not dev:
            return []
        return [{"t": round(t, 1), "temp_c": tc, "pressure_hpa": pr, "gas_mv": g, "gas_ratio": r}
                for t, tc, pr, g, r in dev.env_history]

    def record_strap_status(self, worker_id: str, addr: tuple[str, int], msg: dict) -> None:
        """Worker strap status (1/s). Counts as the worker's heartbeat."""
        self.record_heartbeat(worker_id, addr, {"type": "status", "role": "worker"})
        dev = self.devices[worker_id]
        dev.last_message_type = "status"
        strap = {"zone": msg.get("zone") if isinstance(msg.get("zone"), str) else None,
                 "near_led": msg.get("near_led") is True,
                 # strap's motion sensor working? (False = fall detection OFF; missing = old firmware)
                 "imu": msg.get("imu") if isinstance(msg.get("imu"), bool) else None,
                 "falls": int(msg["falls"]) if _is_number(msg.get("falls")) else None}
        if _is_number(msg.get("g")):
            strap["g"] = round(float(msg["g"]), 2)
        dev.telemetry["strap"] = strap
        self._record_env(dev, msg)
        # A fall alert waiting for our ACK shows as an active fall.
        dev.fall_active = msg.get("alert_pending") is True or (
            "last_fall" in dev.telemetry and time.time() - dev.telemetry["last_fall"]["t"] < 5)

    def record_strap_fall(self, worker_id: str, addr: tuple[str, int], msg: dict) -> bool:
        """Strap fall alert (only sent for falls in the red zone). The strap resends it
        until it gets our ACK, so each (strap, boot, id) is counted once."""
        dev = self.record_packet(worker_id, addr, msg)
        key = (msg.get("strap"), msg.get("boot"), msg.get("id"))
        if key in self._strap_alerts_seen:
            return False
        self._strap_alerts_seen.append(key)
        machine = msg.get("machine_id") if isinstance(msg.get("machine_id"), str) else \
            config.STRAP_MACHINE_IDS.get(msg.get("machine"))
        dev.telemetry["last_fall"] = {
            "t": time.time(),
            "peak_g": float(msg["peak_g"]) if _is_number(msg.get("peak_g")) else None,
            "severity": msg.get("severity") if isinstance(msg.get("severity"), str) else "fall",
            "machine": machine,
            "distance_cm": msg.get("distance_cm") if _is_number(msg.get("distance_cm")) and msg["distance_cm"] >= 0 else None,
            # where the strap says the worker was: red (< 50 cm) / yellow / green / none (no machine)
            "zone": msg.get("zone") if msg.get("zone") in ("red", "yellow", "green", "none") else None,
        }
        dev.fall_count += 1          # the safety engine reacts to this (lockout + check-in)
        dev.fall_active = True
        print(f"[registry] FALL ALERT from {worker_id}: peak {msg.get('peak_g')} g near {machine}")
        if self.on_fall:
            self.on_fall()
        return True

    def record_strap_impact(self, worker_id: str, addr: tuple[str, int], msg: dict) -> None:
        """Strap felt a hard hit while close to a machine (possible fall, not confirmed)."""
        self.record_packet(worker_id, addr, msg)
        if self.on_impact:
            self.on_impact(worker_id, msg)

    def record_worker_ok(self, worker_id: str, addr: tuple[str, int], msg: dict) -> bool:
        """Worker pressed the strap's "I'm OK" button. Resent until we reply OKACK,
        so each (strap, boot, id) counts once. Answers an open check-in (like
        checkin_ack); it does not cancel a fall lockout."""
        self.record_packet(worker_id, addr, msg)
        key = (msg.get("strap"), msg.get("boot"), msg.get("id"))
        if key in self._strap_oks_seen:
            return False
        self._strap_oks_seen.append(key)
        now = time.time()
        for_alert = int(msg["for_alert"]) if _is_number(msg.get("for_alert")) else 0
        secs = int(msg["secs_after_alert"]) if _is_number(msg.get("secs_after_alert")) else 0
        self.worker_oks.append((worker_id, now, for_alert, secs))
        self.acks.append((worker_id, now))
        self.ack_count += 1
        print(f"[registry] {worker_id} pressed I'M OK" + (f" ({secs} s after fall alert #{for_alert})" if for_alert else ""))
        return True

    def record_ack(self, device_id: str, addr: tuple[str, int], msg: dict) -> None:
        """Worker answered a check-in (e.g. pressed a button on the tag)."""
        self.record_packet(device_id, addr, msg)
        self.acks.append((device_id, time.time()))
        self.ack_count += 1

    def record_machine(self, device_id: str, addr: tuple[str, int], msg: dict) -> None:
        """Ultrasonic machine report, e.g.
        {"type":"machine","id":"MA","state":"slowed","dist_cm":87,"zone":"warning","sensor":"ok"}
        Machine reports are sent continuously, so they also count as a heartbeat."""
        dev = self.record_packet(device_id, addr, msg)
        now = time.time()
        was_online = dev.is_online(now)
        dev.last_heartbeat = now
        dev.role = "machine"
        dev.machine_count += 1
        reading = {"t": now, "dist_cm": int(msg["dist_cm"])}
        for key in ("zone", "state", "sensor"):
            if isinstance(msg.get(key), str):
                reading[key] = msg[key].lower()[:16]
        for key in ("warning_cm", "danger_cm"):      # optional echo of the active thresholds
            if _is_number(msg.get(key)):
                reading[key] = int(msg[key])
        # Radio presence (motor_node / hoist_node anchors): did this machine hear Sam's strap beacon?
        if isinstance(msg.get("worker_near"), bool):
            reading["worker_near"] = msg["worker_near"]
            if _is_number(msg.get("worker_rssi")):
                reading["worker_rssi"] = int(msg["worker_rssi"])
                if msg["worker_near"]:
                    dev.rssi_hist.append((now, int(msg["worker_rssi"])))
        dev.machine = reading
        if not was_online:
            print(f"[registry] {device_id} is ONLINE ({addr[0]}) - machine sensor reporting")

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------
    def snapshot(self, now: float) -> list[dict]:
        """JSON-friendly list of all devices for the API / dashboard."""
        out = []
        for dev in self.devices.values():
            online = dev.is_online(now)
            if dev.last_seen is None:
                wifi_status = "never seen"
            elif online:
                wifi_status = "connected"
            elif now - dev.last_seen <= dev.timeout_s():
                wifi_status = "packets but no heartbeat"
            else:
                wifi_status = "lost"
            out.append({
                "id": dev.device_id,
                "role": dev.role,
                "online": online,
                "wifi_status": wifi_status,
                "ip": dev.ip,
                "port": dev.port,
                "mac": dev.mac,
                "last_seen": dev.last_seen,
                "last_seen_ago": None if dev.last_seen is None else round(now - dev.last_seen, 1),
                "last_heartbeat_ago": None if dev.last_heartbeat is None
                    else round(now - dev.last_heartbeat, 1),
                "last_message_type": dev.last_message_type,
                "packet_count": dev.packet_count,
                "heartbeat_count": dev.heartbeat_count,
                "packets_per_second": dev.packets_per_second(now),
                "wifi_rssi": dev.wifi_rssi,
                "uptime_ms": dev.uptime_ms,
                "channel": dev.channel,
                "simulated": dev.simulated,
                "telemetry": dict(dev.telemetry),
                "machine": dict(dev.machine, age_s=round(now - dev.machine["t"], 1)) if dev.machine else None,
            })
        return out
