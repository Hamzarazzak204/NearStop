"""
TEST MODE ONLY: pretends to be the machines (MA Conveyor, MB Hoist) and Sam's vest (W1).

Sends the same UDP JSON the real boards send:
  * each machine, 5 times a second (HC-SR04 ultrasonic report):
      {"type":"machine","id":"MA","state":"slowed","dist_cm":87,"zone":"warning",
       "sensor":"ok","warning_cm":150,"danger_cm":50}
    dist_cm = -1 when nobody is in the sensor's cone
  * the vest, once a second: {"type":"heartbeat","id":"W1",...,"activity","heat_c","fall"}
  * a check-in answer ({"type":"checkin_ack"}) when Sam presses OK

and behaves like the machine firmware should: it applies set_zones commands
(and echoes the active thresholds) and obeys stop / hold / lock / resume.

Scenarios:
  loop  Sam walks up to each machine in turn (into its warning zone) and away
        again; answers any check-in after ~3 s.
  demo  Scripted safety story (~55 s), then Sam stays away from the machines:
          0 s  on site, not near any machine
          8 s  walks up to the Conveyor (it slows; zones grow as Sam heats up)
         16 s  works next to the Conveyor
         19 s  falls -> check-in countdown starts, no answer
        ~29 s  EMERGENCY, Conveyor stopped
         42 s  Sam presses OK (emergency cleared) and walks away
        ~48 s  zone clear -> "Approve restart" becomes possible

Packets are tagged "sim": true so the dashboard shows a SIMULATED badge.

Usage (backend must already be running):
    python backend/simulator.py                    # loop scenario
    python backend/simulator.py --scenario demo
    python backend/simulator.py --flaky MB         # MB's sensor goes silent 10 s of every 30 s
    python backend/simulator.py --noise 5          # distance jitter (cm)
"""

import argparse
import json
import math
import random
import socket
import time
import urllib.request

import config

WORKER = "W1"
MACHINE_IDS = [d for d, role in config.EXPECTED_DEVICES.items() if role == "machine"]
OVERRIDE_STATE = {"stop": "stopped", "hold": "holding", "lock": "locked"}


def fetch_state(host: str) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://{host}:{config.HTTP_PORT}/api/state", timeout=1) as resp:
            return json.load(resp)
    except Exception:
        return None


def lerp(a, b, f):
    f = min(max(f, 0.0), 1.0)
    return a + (b - a) * f


class SimMachine:
    """Behaves like the machine firmware: own thresholds, local slow/stop,
    obeys commands from NearStop."""

    def __init__(self, mid: str) -> None:
        self.id = mid
        self.warning_cm, self.danger_cm = 150, 50
        self.override = None          # "stopped" / "holding" / "locked" after a command

    def handle(self, cmd: dict) -> None:
        if cmd.get("type") == "set_zones":
            self.warning_cm = int(cmd.get("warning_cm", self.warning_cm))
            self.danger_cm = int(cmd.get("danger_cm", self.danger_cm))
            print(f"[sim] {self.id}: set_zones warning {self.warning_cm} cm, danger {self.danger_cm} cm")
        elif cmd.get("type") == "command":
            c = cmd.get("cmd")
            if c in OVERRIDE_STATE:
                self.override = OVERRIDE_STATE[c]
            elif c == "resume":
                self.override = None
            if c != "slow":
                print(f"[sim] {self.id}: command {c}")

    def report(self, dist_cm: int) -> dict:
        if dist_cm < 0:
            zone = "clear"
        elif dist_cm <= self.danger_cm:
            zone = "danger"
        elif dist_cm <= self.warning_cm:
            zone = "warning"
        else:
            zone = "clear"
        local = {"danger": "stopped", "warning": "slowed", "clear": "running"}[zone]
        return {"type": "machine", "id": self.id, "state": self.override or local, "dist_cm": dist_cm,
                "zone": zone, "sensor": "ok", "warning_cm": self.warning_cm, "danger_cm": self.danger_cm}


def demo_at(t: float) -> dict:
    """Where Sam is (machine + distance, or nowhere near) and what the vest reports."""
    conv = "MA"
    if t < 8:
        return dict(phase="away", at=None, activity="Walking", heat=36.9)
    if t < 16:
        f = (t - 8) / 8
        return dict(phase="approach", at=(conv, lerp(320, 110, f)), activity="Walking", heat=lerp(36.9, 37.6, f))
    if t < 19:
        return dict(phase="working", at=(conv, 110), activity="Working", heat=lerp(37.6, 37.8, (t - 16) / 3))
    if t < 21:
        return dict(phase="fall", at=(conv, 95), activity="Fall detected", heat=37.8, fall=True)
    if t < 42:
        return dict(phase="down", at=(conv, 95), activity="Lying still", heat=37.8)
    if t < 43:
        return dict(phase="ack", at=(conv, 95), activity="Getting up", heat=37.7)
    if t < 50:
        f = (t - 43) / 7
        d = lerp(95, 340, f)
        return dict(phase="leave", at=(conv, d) if d < 330 else None, activity="Walking", heat=lerp(37.7, 37.2, f))
    return dict(phase="away", at=None, activity="Walking", heat=37.0)


def loop_at(t: float, machines: list[str]) -> dict:
    """Visit each machine in turn: away, approach into the warning zone, leave."""
    cycle = 16.0
    m = machines[int(t // cycle) % len(machines)]
    u = t % cycle
    if u < 3:
        at = None
    elif u < 9:
        at = (m, lerp(320, 100, (u - 3) / 6))
    elif u < 11:
        at = (m, 100)
    else:
        d = lerp(100, 340, (u - 11) / 5)
        at = (m, d) if d < 330 else None
    return dict(phase=f"loop-{m}", at=at, activity="Walking", heat=36.8)


def env_at(t: float, scenario: str) -> dict:
    """Strap environment sensors (BMP180 + MQ-2). Normal air, except in the "gas"
    scenario: every 90 s a gas leak builds up and fades, and the air heats up."""
    temp, pres, gas = 23.5 + 0.4 * math.sin(t / 40), 1012.0 + 0.3 * math.sin(t / 55), 420.0
    if scenario == "gas":
        u = t % 90
        if 15 <= u < 60:                         # leak: 420 -> ~2900 mV and back
            gas = 420 + 2500 * math.sin(math.pi * (u - 15) / 45)
        temp = 26 + 10 * max(0.0, math.sin(math.pi * u / 90))   # 26 -> 36 C
    return {"temp_c": round(temp + random.gauss(0, 0.1), 1), "pressure_hpa": round(pres + random.gauss(0, 0.03), 2),
            "gas_mv": int(gas + random.gauss(0, 15)), "gas_warm": t >= 8}


PHASE_TEXT = {
    "away": "Sam is on site, not near any machine",
    "approach": "Sam walks up to the Conveyor",
    "working": "Sam works next to the Conveyor",
    "fall": "Sam falls next to the Conveyor (vest reports a possible fall)",
    "down": "Sam is not responding (check-in will time out)",
    "ack": "Sam presses OK on the vest",
    "leave": "Sam walks away from the Conveyor - approve the restart once clear",
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Simulate NearStop machines and a vest")
    parser.add_argument("--host", default="127.0.0.1", help="backend IP (default: this computer)")
    parser.add_argument("--port", type=int, default=config.UDP_PORT)
    parser.add_argument("--noise", type=float, default=2.0, help="distance jitter, cm (1 sigma)")
    parser.add_argument("--scenario", choices=["loop", "demo", "gas"], default="loop")
    parser.add_argument("--flaky", nargs="*", default=[], metavar="ID",
                        help="device IDs that go silent for 10 s every 30 s")
    parser.add_argument("--junk", action="store_true",
                        help="also send a malformed packet every 5 s (tests error handling)")
    args = parser.parse_args()

    target = (args.host, args.port)
    socks = {}
    for dev in MACHINE_IDS + [WORKER]:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind(("0.0.0.0", 0))          # fixed port per device: commands come back here
        s.setblocking(False)
        socks[dev] = s
    machines = {m: SimMachine(m) for m in MACHINE_IDS}
    start = time.time()
    next_report = next_heartbeat = next_state = next_junk = start
    seq = 0
    phase = None
    ack_at = None
    demo_ack_sent = False
    range_cm = 300
    order = list(MACHINE_IDS)

    print(f"[sim] sending to {target[0]}:{target[1]}, scenario {args.scenario}  (Ctrl+C to stop)")

    def send(dev: str, msg: dict) -> None:
        msg["sim"] = True
        try:
            socks[dev].sendto(json.dumps(msg, separators=(",", ":")).encode(), target)
        except OSError as exc:
            print(f"[sim] send failed: {exc}")

    def silent(dev: str, now: float) -> bool:
        return dev in args.flaky and (now - start) % 30 >= 20

    try:
        while True:
            now = time.time()
            t = now - start
            w = demo_at(t) if args.scenario == "demo" else loop_at(t, order)
            if w["phase"] != phase:
                phase = w["phase"]
                text = PHASE_TEXT.get(phase) or f"Sam walks up to {phase.split('-')[-1]} and away again"
                print(f"[sim] {t:4.0f} s: {text}")

            # Commands from NearStop (set_zones, stop/resume...) arrive on the machine sockets
            for mid, m in machines.items():
                while True:
                    try:
                        data, _ = socks[mid].recvfrom(2048)
                    except OSError:          # nothing waiting (or Windows ICMP reset)
                        break
                    try:
                        m.handle(json.loads(data))
                    except ValueError:
                        pass

            # Every second: site layout + whether a check-in is waiting
            if now >= next_state:
                state = fetch_state(args.host)
                if state:
                    sc = state.get("site_config", {})
                    range_cm = sc.get("sensor_range_cm", range_cm)
                    order = [m for m in sc.get("machines", {}) if m in machines] or order
                    pending = any(x["id"] == WORKER and x["checkin"] for x in state["site"]["workers"])
                    can_answer = w["phase"] not in ("fall", "down")
                    if pending and can_answer and ack_at is None:
                        ack_at = now + random.uniform(2.5, 4.0)
                    if not pending:
                        ack_at = None
                next_state = now + 1

            if ack_at is not None and now >= ack_at:
                send(WORKER, {"type": "checkin_ack", "id": WORKER})
                print("[sim] Sam answered the check-in")
                ack_at = None
            if w["phase"] == "ack" and not demo_ack_sent:
                send(WORKER, {"type": "checkin_ack", "id": WORKER})
                demo_ack_sent = True

            # Machine reports, 5 per second
            if now >= next_report:
                for mid, m in machines.items():
                    if silent(mid, now):
                        continue
                    dist = -1
                    if w["at"] and w["at"][0] == mid:
                        d = w["at"][1] + random.gauss(0, args.noise)
                        dist = int(round(d)) if 2 <= d <= max(range_cm, 400) else -1
                    send(mid, m.report(dist))
                next_report += 0.2

            # Vest heartbeat, 1 per second
            if now >= next_heartbeat:
                seq += 1
                if not silent(WORKER, now):
                    send(WORKER, {"type": "heartbeat", "id": WORKER, "role": "worker", "seq": seq,
                                  "uptime_ms": int(t * 1000), "wifi_rssi": random.randint(-65, -45),
                                  "activity": w["activity"], "heat_c": round(w["heat"], 1),
                                  "fall": bool(w.get("fall")), **env_at(t, args.scenario)})
                next_heartbeat += 1.0

            if args.junk and now >= next_junk:
                socks[MACHINE_IDS[0]].sendto(b"this is not json", target)
                next_junk = now + 5

            time.sleep(0.02)
    except KeyboardInterrupt:
        print("\n[sim] stopped")


if __name__ == "__main__":
    main()
