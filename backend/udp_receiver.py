"""
UDP receiver: listens on 0.0.0.0:5005 for JSON packets from the ESP32s.

Accepted messages (one JSON object per UDP packet):

  {"type": "machine", "id": "MA", "state": "slowed", "dist_cm": 87,
   "zone": "warning", "sensor": "ok"}            (HC-SR04 machine sensor)

  {"type": "heartbeat", "id": "W1", "role": "worker", ...optional vest telemetry}
  {"type": "checkin_ack", "id": "W1"}             (worker answered a check-in)
  {"type": "rssi_measurement", ...}               (old firmware: counted, ignored)

  Worker strap (firmware/worker_strap):
  {"type": "status", "worker": "W1", "strap": 1, "zone": "red", "g": 1.01, ...}   (1/s)
  {"type": "fall_alert", "worker": "W1", "strap": 1, "boot": 123, "id": 4,
   "peak_g": 4.7, "machine": 2, "distance_cm": 40, ...}   (resent until we reply "ACK 4")
  {"type": "worker_ok", "worker": "W1", "strap": 1, "boot": 123, "id": 2,
   "for_alert": 4, "secs_after_alert": 12}     ("I'm OK" button, resent until "OKACK 2")

Any other "type" that carries an "id" is accepted and counted as activity.
Malformed packets are counted and logged (rate-limited) but never crash
the receiver.
"""

import asyncio
import json
import time

import config


def _valid_id(value) -> bool:
    return isinstance(value, str) and 0 < len(value) <= 16


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


class UDPStats:
    def __init__(self) -> None:
        self.listening = False
        self.bind_error: str | None = None
        self.started_at: float | None = None
        self.packets_total = 0
        self.packets_valid = 0
        self.packets_malformed = 0
        self.by_type: dict[str, int] = {}
        self.last_packet_at: float | None = None
        self.last_packet_from: str | None = None
        self.last_error: str | None = None
        self.last_error_at: float | None = None

    def to_dict(self, now: float) -> dict:
        return {
            "host": config.UDP_HOST,
            "port": config.UDP_PORT,
            "listening": self.listening,
            "bind_error": self.bind_error,
            "uptime_s": None if self.started_at is None else round(now - self.started_at),
            "packets_total": self.packets_total,
            "packets_valid": self.packets_valid,
            "packets_malformed": self.packets_malformed,
            "by_type": self.by_type,
            "last_packet_ago": None if self.last_packet_at is None
                else round(now - self.last_packet_at, 1),
            "last_packet_from": self.last_packet_from,
            "last_error": self.last_error,
            "last_error_ago": None if self.last_error_at is None
                else round(now - self.last_error_at, 1),
        }


class UDPReceiver(asyncio.DatagramProtocol):
    def __init__(self, registry, stats: UDPStats) -> None:
        self.registry = registry
        self.stats = stats
        self._last_error_log = 0.0
        self.transport = None

    def connection_made(self, transport) -> None:
        self.transport = transport      # used to answer the strap's fall alerts ("ACK <id>")

    # Called by asyncio for every UDP packet
    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self.stats.packets_total += 1
        self.stats.last_packet_at = time.time()
        self.stats.last_packet_from = f"{addr[0]}:{addr[1]}"
        try:
            self._handle(data, addr)
            self.stats.packets_valid += 1
        except ValueError as exc:
            self._malformed(str(exc), data, addr)
        except Exception as exc:  # never let one bad packet stop the receiver
            self._malformed(f"unexpected error: {exc!r}", data, addr)

    def error_received(self, exc: Exception) -> None:
        print(f"[udp] socket error: {exc}")

    def _handle(self, data: bytes, addr: tuple[str, int]) -> None:
        try:
            msg = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"not valid JSON ({exc.__class__.__name__})")
        if not isinstance(msg, dict):
            raise ValueError("JSON is not an object")

        msg_type = msg.get("type")
        if not isinstance(msg_type, str):
            raise ValueError("missing 'type'")

        if msg_type == "heartbeat":
            # Accept "id" (positioning firmware) or "device_id" (connection-test firmware)
            device_id = msg.get("id", msg.get("device_id"))
            if not _valid_id(device_id):
                raise ValueError("heartbeat without valid 'id' or 'device_id'")
            self.registry.record_heartbeat(device_id, addr, msg)

        elif msg_type == "machine":
            # {"type":"machine","id":"MA","state":"slowed","dist_cm":87,"zone":"warning","sensor":"ok"}
            # dist_cm is -1 when nothing is in range
            if not _valid_id(msg.get("id")):
                raise ValueError("machine message without valid 'id'")
            dist = msg.get("dist_cm")
            if not _is_number(dist) or not (dist == -1 or 0 <= dist <= 2000):
                raise ValueError(f"machine message with invalid dist_cm {dist!r}")
            self.registry.record_machine(msg["id"], addr, msg)

        elif msg_type == "rssi_measurement":
            # Old RSSI-positioning firmware (see backend/legacy). Not used any more:
            # counted as activity from that board, otherwise ignored.
            if not _valid_id(msg.get("anchor_id")):
                raise ValueError("rssi_measurement without valid anchor_id")
            self.registry.record_packet(msg["anchor_id"], addr, msg)

        elif msg_type == "checkin_ack":
            # {"type": "checkin_ack", "id": "W1"} - the worker answered a check-in
            device_id = msg.get("id", msg.get("device_id"))
            if not _valid_id(device_id):
                raise ValueError("checkin_ack without valid 'id'")
            self.registry.record_ack(device_id, addr, msg)

        elif msg_type in ("status", "fall_alert", "impact", "worker_ok") and ("worker" in msg or "strap" in msg):
            # Worker strap. "id" in a fall_alert is the alert number, so the worker is
            # named by "worker" (or looked up from the strap number).
            worker_id = msg.get("worker")
            if not _valid_id(worker_id):
                worker_id = config.STRAP_WORKERS.get(msg.get("strap"))
            if not _valid_id(worker_id):
                raise ValueError(f"strap {msg_type} without a known worker/strap")
            if msg_type == "status":
                self.registry.record_strap_status(worker_id, addr, msg)
            elif msg_type == "impact":
                self.registry.record_strap_impact(worker_id, addr, msg)
            elif msg_type == "worker_ok":
                ok_id = msg.get("id")
                if not _is_number(ok_id):
                    raise ValueError("worker_ok without numeric 'id'")
                self.registry.record_worker_ok(worker_id, addr, msg)
                if self.transport is not None:     # confirm every copy, so the strap stops resending
                    self.transport.sendto(f"OKACK {int(ok_id)}".encode(), addr)
            else:
                alert_id = msg.get("id")
                if not _is_number(alert_id):
                    raise ValueError("fall_alert without numeric 'id'")
                self.registry.record_strap_fall(worker_id, addr, msg)
                if self.transport is not None:     # confirm, so the strap stops resending
                    self.transport.sendto(f"ACK {int(alert_id)}".encode(), addr)

        else:
            # Unknown but well-formed message: count it as device activity.
            if not _valid_id(msg.get("id")):
                raise ValueError(f"'{msg_type}' message without valid 'id'")
            self.registry.record_packet(msg["id"], addr, msg)

        self.stats.by_type[msg_type] = self.stats.by_type.get(msg_type, 0) + 1

    def _malformed(self, reason: str, data: bytes, addr: tuple[str, int]) -> None:
        self.stats.packets_malformed += 1
        self.stats.last_error = f"{addr[0]}: {reason}"
        self.stats.last_error_at = time.time()
        # Rate-limit console output so a flood of junk cannot spam the log
        if time.time() - self._last_error_log > 2.0:
            self._last_error_log = time.time()
            preview = data[:80].decode("utf-8", errors="replace")
            print(f"[udp] malformed packet from {addr[0]}:{addr[1]}: {reason} | {preview!r}")


async def start_udp_receiver(registry, stats: UDPStats):
    """Bind the UDP socket. Returns the transport, or None if binding failed."""
    loop = asyncio.get_running_loop()
    try:
        transport, _ = await loop.create_datagram_endpoint(
            lambda: UDPReceiver(registry, stats),
            local_addr=(config.UDP_HOST, config.UDP_PORT),
        )
    except OSError as exc:
        stats.listening = False
        stats.bind_error = str(exc)
        print(f"[udp] FAILED to bind {config.UDP_HOST}:{config.UDP_PORT}: {exc}")
        print("[udp] Is another copy of the backend already running?")
        return None
    stats.listening = True
    stats.bind_error = None
    stats.started_at = time.time()
    print(f"[udp] listening on {config.UDP_HOST}:{config.UDP_PORT}")
    return transport
