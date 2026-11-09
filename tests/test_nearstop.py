"""
NearStop backend tests - no hardware needed.

Feeds real JSON packets (exactly what the boards / USB bridge send) into the real
UDP receiver, device registry and safety engine, and checks the results.

Run from the project folder:
    .venv\\Scripts\\python.exe tests\\test_nearstop.py
Exit code 0 = all passed.
"""

import io
import json
import sys
import time
import contextlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
import os                                        # noqa: E402
# The tests use their own site config (with the Robot Arm GW) so every rule is covered,
# whatever machines the live backend/site_config.json has.
os.environ["NAT_SITE_CONFIG"] = str(ROOT / "tests" / "site_config_test.json")
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "tools"))

import config                                    # noqa: E402
from device_registry import DeviceRegistry      # noqa: E402
from safety import SafetyEngine                  # noqa: E402
from udp_receiver import UDPReceiver, UDPStats   # noqa: E402

PASSES, FAILS = 0, 0


def check(cond, msg):
    global PASSES, FAILS
    if cond:
        PASSES += 1
        print(f"  PASS  {msg}")
    else:
        FAILS += 1
        print(f"  FAIL  {msg}")


class FakeTransport:
    def __init__(self):
        self.sent = []

    def sendto(self, data, addr):
        self.sent.append((data.decode(), addr))


class Site:
    """One NearStop backend in a box: registry + safety + receiver, commands recorded."""

    def __init__(self, presence_stop=None):
        with contextlib.redirect_stdout(io.StringIO()):
            self.reg = DeviceRegistry()
            self.commands = []
            self.safety = SafetyEngine(self.reg, self._send)
        for m, on in (presence_stop or {}).items():     # test the generic slow/stop rules
            self.safety.cfg["machines"][m]["presence_stop"] = on
        self.reg.on_fall = self._quiet(self.safety._process_falls_and_acks)
        self.reg.on_impact = self._quiet(self.safety.on_impact)
        self.rx = UDPReceiver(self.reg, UDPStats())
        self.transport = FakeTransport()
        self.rx.connection_made(self.transport)

    @staticmethod
    def _quiet(fn):
        def wrapper(*a, **k):
            with contextlib.redirect_stdout(io.StringIO()):
                return fn(*a, **k)
        return wrapper

    def _send(self, machine_id, payload):
        self.commands.append((machine_id, payload.get("cmd") or payload.get("type")))
        return True

    def packet(self, msg, addr=("127.0.0.1", 50000)):
        with contextlib.redirect_stdout(io.StringIO()):
            self.rx.datagram_received(json.dumps(msg).encode(), addr)

    def machine(self, mid, dist_cm, sensor="ok", near=None):
        msg = {"type": "machine", "id": mid, "state": "running", "dist_cm": dist_cm, "zone": "clear",
               "sensor": sensor, "warning_cm": 150, "danger_cm": 50}
        if near is not None:                       # radio-presence firmware
            msg.update(worker_near=near, worker_rssi=-90 if near else -127)
        self.packet(msg, ("127.0.0.1", 51000 + ord(mid[1])))

    def tick_radio(self, readings, n=6):
        """readings {id: (dist_cm, heard_strap)} from radio-presence machines."""
        for _ in range(n):
            for mid, (cm, near) in readings.items():
                self.machine(mid, cm, near=near)
            self.strap_status()
            with contextlib.redirect_stdout(io.StringIO()):
                self.safety.update()
            time.sleep(0.005)

    def strap_status(self, zone="green"):
        self.packet({"type": "status", "worker": "W1", "strap": 1, "zone": zone, "g": 1.0,
                     "near_led": zone in ("red", "yellow"), "alert_pending": False, "falls": 0},
                    ("127.0.0.1", 52000))

    def tick(self, readings, n=6):
        """Send machine readings {id: cm} + strap status, then run the 5 Hz update n times."""
        for _ in range(n):
            for mid, cm in readings.items():
                self.machine(mid, cm)
            self.strap_status()
            with contextlib.redirect_stdout(io.StringIO()):
                self.safety.update()
            time.sleep(0.005)

    def state(self, m):
        return self.safety.machines[m]["state"]

    def force_running(self, *ms):
        with contextlib.redirect_stdout(io.StringIO()):
            for m in ms:
                if self.state(m) != "Running":
                    self.safety._set_machine(m, "Running", "test start", "ok")
        self.commands.clear()

    def impact(self, machine_id, cm):
        self.packet({"type": "impact", "worker": "W1", "strap": 1, "g": 5.0,
                     "machine_id": machine_id, "distance_cm": cm}, ("127.0.0.1", 52000))

    def fall(self, machine_id, cm, alert_id=1, boot=7):
        self.packet({"type": "fall_alert", "worker": "W1", "strap": 1, "boot": boot, "id": alert_id,
                     "peak_g": 5.0, "severity": "fall", "zone": "red", "machine_id": machine_id,
                     "distance_cm": cm}, ("127.0.0.1", 52000))


def section(title):
    print(f"\n{title}")


def main():
    cfg = Site().safety.cfg["machines"]

    section("Site configuration")
    check(cfg["MB"]["name"] == "Hoist", "MB is the Hoist")
    check(cfg["MB"]["presence_stop"] is False, "Hoist: presence alone does not stop it")
    check(cfg["MB"]["fall_stop_zone"] == "danger", "Hoist: only a fall CLOSE to it (danger zone) stops it")
    check(cfg["MB"]["impact_hold"] is True, "Hoist: holds on impact, before the fall is confirmed")
    check(cfg["MA"]["name"] == "Conveyor" and cfg["MA"]["presence_stop"] is False,
          "Conveyor: presence never slows or stops it")
    check(all(cfg[m]["presence_stop"] is False for m in ("GW", "MA", "MB")),
          "all machines keep running when Sam is nearby (presence_stop false)")
    check(cfg["GW"]["fall_stop_zone"] == "warning", "Robot Arm: fall lockout within its warning distance")

    section("Generic presence rules (a machine with presence_stop on)")
    s = Site(presence_stop={"GW": True})
    s.tick({"GW": -1, "MB": -1})
    check(s.reg.devices["GW"].is_online(time.time()), "machine reports make the machine online")
    s.tick({"GW": 30, "MB": 30})
    check(s.state("GW") == "Stopped", "Robot Arm: person at 30 cm -> Stopped")
    check(("GW", "stop") in s.commands, "Robot Arm: 'stop' command sent to the board")
    check(s.state("MB") == "Running", "Hoist: person at 30 cm -> keeps Running (presence_stop false)")
    check(not any(m == "MB" and c in ("stop", "hold", "slow") for m, c in s.commands),
          "Hoist: no stop/hold/slow command for presence")
    # The board missed the stop (e.g. it was reflashed): NearStop must re-send it
    s.commands.clear()
    for _ in range(3):
        s.machine("GW", 30)               # board keeps reporting state "running"
        with contextlib.redirect_stdout(io.StringIO()):
            s.safety.last_command["GW"]["t"] -= 2      # pretend 2 s passed since the last send
            s.safety.update()
    check(s.commands.count(("GW", "stop")) >= 2,
          "board reports 'running' while NearStop has it Stopped -> 'stop' re-sent until confirmed")
    s.commands.clear()
    s.packet({"type": "machine", "id": "GW", "state": "stopped", "dist_cm": 30, "zone": "danger",
              "sensor": "ok", "warning_cm": 150, "danger_cm": 50}, ("127.0.0.1", 51000 + ord("W")))
    with contextlib.redirect_stdout(io.StringIO()):
        s.safety.last_command["GW"]["t"] -= 2
        s.safety.update()
    check(("GW", "stop") not in s.commands, "...and no more re-sends once the board confirms 'stopped'")
    s3 = Site()                            # a fresh NearStop; the board is still locked from before
    for _ in range(2):
        s3.packet({"type": "machine", "id": "MA", "state": "locked", "dist_cm": -1, "zone": "clear",
                   "sensor": "ok", "warning_cm": 150, "danger_cm": 50}, ("127.0.0.1", 51065))
        with contextlib.redirect_stdout(io.StringIO()):
            s3.safety.update()
    check(s3.state("MA") == "Locked", "NearStop restarted while a machine was locked: it shows Locked (needs Approve restart)")
    with contextlib.redirect_stdout(io.StringIO()):
        ok, _ = s3.safety.approve_restart("MA")
    for _ in range(2):                     # board still reports "locked" until it processes the resume
        s3.packet({"type": "machine", "id": "MA", "state": "locked", "dist_cm": -1, "zone": "clear",
                   "sensor": "ok", "warning_cm": 150, "danger_cm": 50}, ("127.0.0.1", 51065))
        with contextlib.redirect_stdout(io.StringIO()):
            s3.safety.update()
    check(ok and s3.state("MA") == "Running", "...Approve restart sticks while the board catches up (no re-lock loop)")
    s4 = Site()
    s4.tick({"MA": 30})
    check(s4.state("MA") == "Running", "Conveyor: person/object at 30 cm -> keeps Running")
    s4.tick({"MA": 100})
    check(s4.state("MA") == "Running", "Conveyor: person at 100 cm -> keeps Running (not slowed)")
    check(not any(m == "MA" and c in ("stop", "hold", "slow", "lock") for m, c in s4.commands),
          "Conveyor: no slow/stop command for presence (duty cycle unchanged)")
    s2 = Site(presence_stop={"GW": True})
    s2.tick({"GW": 100})
    check(s2.state("GW") == "Slowed", "Robot Arm: person at 100 cm -> Slowed")

    section("Worker strap messages")
    s = Site()
    s.tick({"GW": -1, "MB": -1}, n=2)
    check(s.reg.devices["W1"].is_online(time.time()), "strap 'status' makes Sam (W1) online")
    check(s.reg.devices["W1"].telemetry.get("strap", {}).get("zone") == "green", "strap zone is recorded")
    s.fall("GW", 20, alert_id=5)
    check(("ACK 5", ("127.0.0.1", 52000)) in s.transport.sent, "fall_alert is confirmed with 'ACK 5' to the sender")
    falls_before = s.reg.devices["W1"].fall_count
    s.fall("GW", 20, alert_id=5)                    # the strap resends until it gets the ACK
    check(s.reg.devices["W1"].fall_count == falls_before, "a resent fall_alert (same boot+id) counts once")
    check(s.transport.sent.count(("ACK 5", ("127.0.0.1", 52000))) == 2, "...but every copy is ACKed")

    section("Fall lockout rules")

    def scenario(readings, strap_machine, strap_cm, impact=True, fall=True):
        s = Site()
        s.tick(readings)
        s.force_running("GW", "MB")
        if impact:
            s.impact(strap_machine, strap_cm)
        after_impact = s.state("MB")
        if fall:
            s.fall(strap_machine, strap_cm)
        return s, after_impact

    s, hit = scenario({"GW": -1, "MB": 30}, "MB", 30)
    check(hit == "Holding", "Sam CLOSE to Hoist (30 cm) hits the ground -> Hoist HOLDS at once")
    check(s.state("MB") == "Locked", "...fall confirmed -> Hoist LOCKED OUT")
    check(s.commands[:2] == [("MB", "hold"), ("MB", "lock")], "...commands: hold, then lock")
    check(s.state("GW") == "Running", "...Robot Arm (nobody near it) keeps running")
    w = s.safety._worker_snapshot("W1", time.time(), s.safety._closest())
    check(w["last_fall"] and w["last_fall"]["primary"] == "Hoist", "dashboard: 'fell near Hoist'")
    check(w["checkin"] and w["checkin"]["machine_name"] == "Hoist", "check-in pop-up names the Hoist")

    s, hit = scenario({"GW": 20, "MB": 100}, "GW", 20)
    check(hit == "Running" and s.state("MB") == "Running",
          "Sam only NEAR Hoist (100 cm), close to Robot Arm: Hoist keeps running")
    check(s.state("GW") == "Locked", "...Robot Arm is locked out (unchanged rule)")

    s, _ = scenario({"GW": -1, "MB": 100}, "MB", 100)
    check(s.state("MB") == "Running", "Sam near Hoist (100 cm) only, falls: Hoist keeps running")

    s, _ = scenario({"GW": 120, "MB": -1}, "GW", 120)
    check(s.state("GW") == "Locked", "Sam within Robot Arm's WARNING distance (120 cm) falls: Robot Arm locked")

    s, hit = scenario({"GW": -1, "MB": 30}, "MB", 30, fall=False)
    check(hit == "Holding", "hard hit next to Hoist, no fall: Hoist holds")
    with contextlib.redirect_stdout(io.StringIO()):
        for m in list(s.safety._impact_holds):
            s.safety._impact_holds[m] -= config.IMPACT_HOLD_S + 0.1     # pretend 3 s passed
        s.safety.update()
    check(s.state("MB") == "Running", f"...no fall confirmed within {config.IMPACT_HOLD_S:.0f} s: Hoist resumes")
    check(("MB", "resume") in s.commands, "...'resume' command sent")

    # Conveyor (MA): a fall close to it stops (locks out) the Conveyor, nothing else
    s = Site()
    s.tick({"MA": 40, "MB": -1, "GW": -1})
    s.force_running("MA", "MB", "GW")
    s.fall("MA", 40)
    check(s.state("MA") == "Locked", "Sam falls 40 cm from the Conveyor: Conveyor LOCKED OUT (motor stops)")
    check(("MA", "lock") in s.commands, "...'lock' command sent to the Conveyor")
    check(s.state("MB") == "Running" and s.state("GW") == "Running", "...Hoist and Robot Arm keep running")
    w = s.safety._worker_snapshot("W1", time.time(), s.safety._closest())
    check(w["last_fall"]["primary"] == "Conveyor" and w["checkin"]["machine_name"] == "Conveyor",
          "...dashboard banner and pop-up name the Conveyor")
    s = Site()
    s.tick({"MA": 300, "MB": -1, "GW": -1})
    s.force_running("MA")
    s.fall("MA", 300)
    check(s.state("MA") == "Running", "Sam falls 3 m from the Conveyor: Conveyor keeps running")

    s, hit = scenario({"GW": -1, "MB": 30}, "MB", 30, fall=False)
    check(hit == "Holding", "hard hit next to Hoist, no fall: Hoist holds")
    with contextlib.redirect_stdout(io.StringIO()):
        for m in list(s.safety._impact_holds):
            s.safety._impact_holds[m] -= config.IMPACT_HOLD_S + 0.1     # pretend 3 s passed
        s.safety.update()
    check(s.state("MB") == "Running", f"...no fall confirmed within {config.IMPACT_HOLD_S:.0f} s: Hoist resumes")
    check(("MB", "resume") in s.commands, "...'resume' command sent")

    section("Radio presence (machines hear Sam's strap)")
    s = Site()
    s.tick_radio({"MA": (20, False), "GW": (-1, False), "MB": (-1, False)})
    check(s.safety.detection_cm("MA", time.time()) is None,
          "object 20 cm in front but strap NOT heard -> machine reports clear")
    snap = s.safety._machine_snapshot("MA", time.time(), None, s.safety.thresholds("MA", 0))
    check(snap["worker_near"] is False, "dashboard: Conveyor card says clear (worker_near false)")
    s.tick_radio({"MA": (-1, True), "GW": (-1, False), "MB": (-1, False)})
    check(s.safety.detection_cm("MA", time.time()) == config.RADIO_NEAR_CM,
          "strap heard (no ultrasonic reading) -> Sam counted as near the Conveyor")
    check(all(s.state(m) == "Running" for m in ("GW", "MA", "MB")), "Sam nearby: every machine keeps running")
    w = s.safety._worker_snapshot("W1", time.time(), s.safety._closest())
    check(w["near"] and w["near"]["name"] == "Conveyor" and w["near"]["radio"],
          "dashboard: Sam is near the Conveyor (heard by radio)")
    s.commands.clear()
    s.fall("MA", config.RADIO_NEAR_CM)
    check(s.state("MA") == "Locked", "Sam falls while the Conveyor hears him -> Conveyor LOCKED (motor stops)")
    check(s.state("GW") == "Running" and s.state("MB") == "Running", "...the machines that don't hear him keep running")
    s = Site()
    s.tick_radio({"MA": (20, False), "GW": (15, False), "MB": (-1, True)})
    s.force_running("MA", "GW", "MB")
    s.impact("MB", config.RADIO_NEAR_CM)
    s.fall("MB", config.RADIO_NEAR_CM)
    check(s.state("MB") == "Locked", "Sam falls by the Hoist (heard by radio) -> Hoist LOCKED")
    check(s.state("MA") == "Running" and s.state("GW") == "Running",
          "...Conveyor and Robot Arm (objects in front, strap NOT heard) keep running")

    section("Radio presence: RSSI threshold per machine")
    s = Site()
    s.safety.cfg["machines"]["MA"]["near_rssi_dbm"] = -80
    s.tick_radio({"MA": (-1, True), "GW": (-1, False), "MB": (-1, False)})      # heard at -90 dBm
    check(s.safety.detection_cm("MA", time.time()) is None,
          "strap heard at -90 dBm but Conveyor needs -80 dBm -> not counted as near")
    s.safety.cfg["machines"]["MA"]["near_rssi_dbm"] = -95
    s.tick_radio({"MA": (-1, True), "GW": (-1, False), "MB": (-1, False)})
    check(s.safety.detection_cm("MA", time.time()) == config.RADIO_NEAR_CM,
          "threshold -95 dBm: heard at -90 dBm -> Sam near the Conveyor")
    import site_config
    check(site_config.normalise({"machines": {"MA": {"near_rssi_dbm": -70}}})["machines"]["MA"]["near_rssi_dbm"] == -70,
          "near_rssi_dbm is read from site_config.json")
    check(site_config.normalise({})["machines"]["MA"]["near_rssi_dbm"] == -127, "default: any beacon heard counts")

    section("Radio presence: -94 dBm, or a weaker strap + the ultrasonic sensor seeing him")
    def tick_rssi(s, readings, n=6):
        """readings {id: (dist_cm, rssi or None = strap not heard)}."""
        for _ in range(n):
            for mid, (cm, rssi) in readings.items():
                s.packet({"type": "machine", "id": mid, "state": "running", "dist_cm": cm, "zone": "clear",
                          "sensor": "ok", "warning_cm": 150, "danger_cm": 50, "worker_near": rssi is not None,
                          "worker_rssi": rssi if rssi is not None else -127}, ("127.0.0.1", 51000 + ord(mid[1])))
            s.strap_status()
            with contextlib.redirect_stdout(io.StringIO()):
                s.safety.update()
            time.sleep(0.005)
    def site94():
        s = Site()
        for m in ("MA", "GW", "MB"):
            s.safety.cfg["machines"][m]["near_rssi_dbm"] = -94
        return s
    s = site94()
    tick_rssi(s, {"MA": (-1, -93), "GW": (-1, None), "MB": (-1, None)})
    now = time.time()
    check(s.safety.sam_near("MA", now) == (True, "radio"),
          "strap heard at -93 dBm, nothing in front of the sensor -> Sam near the Conveyor (radio)")
    snap = s.safety._machine_snapshot("MA", time.time(), None, s.safety.thresholds("MA", 0))
    check(snap["sam_near"] is True and snap["sam_near_by"] == "radio" and snap["worker_rssi_avg"] == -93,
          "dashboard gets sam_near=True, 'radio', average -93 dBm")
    s = site94()
    tick_rssi(s, {"MA": (80, -98), "GW": (-1, None), "MB": (-1, None)})
    now = time.time()
    check(s.safety.sam_near("MA", now) == (True, "ultrasonic") and s.safety.detection_cm("MA", now) == 80,
          "strap heard weakly (-98 dBm) and the sensor sees someone at 80 cm -> Sam near (ultrasonic, 80 cm)")
    s = site94()
    tick_rssi(s, {"MA": (-1, -98), "GW": (-1, None), "MB": (-1, None)})
    check(s.safety.sam_near("MA", time.time()) == (False, None),
          "strap heard weakly (-98 dBm), sensor sees nothing -> clear")
    s = site94()
    tick_rssi(s, {"MA": (200, -98), "GW": (-1, None), "MB": (-1, None)})
    check(s.safety.sam_near("MA", time.time()) == (False, None),
          "weak strap + object beyond the warning distance (200 cm) -> clear")
    s = site94()
    tick_rssi(s, {"MA": (40, None), "GW": (-1, None), "MB": (-1, None)})
    check(s.safety.sam_near("MA", time.time()) == (False, None),
          "object at 40 cm but Sam's strap not heard (a box) -> clear")
    snap = s.safety._machine_snapshot("MA", time.time(), None, s.safety.thresholds("MA", 0))
    check(snap["sam_near"] is False and s.state("MA") == "Running", "...dashboard says clear, Conveyor keeps running")
    s = site94()
    tick_rssi(s, {"MA": (80, -98), "GW": (-1, None), "MB": (-1, None)})
    s.force_running("MA", "GW", "MB")
    tick_rssi(s, {"MA": (80, -98), "GW": (-1, None), "MB": (-1, None)}, n=2)
    check(s.state("MA") == "Running", "Sam near the Conveyor by ultrasonic: it keeps running (presence never stops)")
    s.fall("MA", 80)
    check(s.state("MA") == "Locked", "Sam falls there (weak strap + sensor sees him at 80 cm) -> Conveyor LOCKED")
    s = site94()
    tick_rssi(s, {"MA": (-1, None), "GW": (-1, None), "MB": (80, -98)})
    s.force_running("MA", "GW", "MB")
    s.fall("MB", 80)
    check(s.state("MB") == "Running", "Hoist: weak strap + sensor at 80 cm (outside its 50 cm zone) -> fall doesn't lock it")
    s = site94()
    tick_rssi(s, {"MA": (-1, None), "GW": (-1, None), "MB": (40, -98)})
    s.force_running("MA", "GW", "MB")
    s.impact("MB", 40)
    s.fall("MB", 40)
    check(s.state("MB") == "Locked", "Hoist: weak strap + sensor at 40 cm -> fall LOCKS it")

    section("Radio: a fall right after walking away does not lock the machine")
    s = Site()
    s.tick_radio({"MA": (-1, True), "GW": (-1, False), "MB": (-1, False)})        # next to the Conveyor
    s.force_running("MA")
    s.tick_radio({"MA": (-1, False), "GW": (-1, False), "MB": (-1, False)}, n=2)  # walked away: not heard
    for m in ("MA",):
        s.safety._near_at[m] = (time.time() - 2.5, config.RADIO_NEAR_CM)      # heard 2.5 s ago
    s.fall("MA", config.RADIO_NEAR_CM)                                          # strap still remembers 'red'
    check(s.state("MA") == "Running",
          "Sam falls 2.5 s after the Conveyor stopped hearing him -> Conveyor keeps running")
    check(any("not near a machine" in e["text"] for e in s.safety.events), "...the fall is still a red FALL alert")
    s = Site()
    s.safety.cfg["machines"]["MA"]["near_rssi_dbm"] = -91
    for rssi in (-88, -97, -97, -97):                                               # one strong packet, then weak
        s.packet({"type": "machine", "id": "MA", "state": "running", "dist_cm": -1, "zone": "clear",
                  "sensor": "ok", "warning_cm": 150, "danger_cm": 50, "worker_near": True,
                  "worker_rssi": rssi}, ("127.0.0.1", 51065))
    check(s.safety.detection_cm("MA", time.time()) is None,
          "RSSI averaged over 2 s: one strong packet (-88) among weak ones (-97) is not 'near' at -91")

    section("Falls away from machines (strap sends every fall)")
    s = Site()
    s.tick({"GW": -1, "MB": -1})
    s.force_running("GW", "MB")
    s.packet({"type": "fall_alert", "worker": "W1", "strap": 1, "boot": 7, "id": 3, "peak_g": 4.5,
              "severity": "fall", "zone": "none", "machine": 0, "machine_id": "", "distance_cm": -1},
             ("127.0.0.1", 52000))
    check(("ACK 3", ("127.0.0.1", 52000)) in s.transport.sent, "fall away from machines is ACKed")
    check(s.state("GW") == "Running" and s.state("MB") == "Running", "...nothing is locked out")
    ci = s.safety.checkins.get("W1")
    check(ci and ci["reason"] == "fall" and ci["machine"] is None, "...a fall check-in starts, naming no machine")
    check(any(e["level"] == "danger" and "not near a machine" in e["text"] for e in s.safety.events),
          "...logged as a red FALL alert")
    with contextlib.redirect_stdout(io.StringIO()):
        s.safety.resolve_checkin("W1", "ok")
    w = s.safety._worker_snapshot("W1", time.time(), None)
    check(w["recent_fall"] and w["recent_fall"]["peak_g"] == 4.5 and w["recent_fall"]["ago_s"] < 5,
          "...and stays on the dashboard (recent_fall) even after the check-in is answered")
    s = Site()
    s.tick({"GW": -1, "MB": -1})
    s.force_running("GW")
    s.fall("GW", 300)                       # strap: green zone, Robot Arm 3 m away
    check(s.state("GW") == "Running", "fall 3 m from the Robot Arm (outside its warning distance): not locked out")
    s = Site()
    s.tick({"GW": -1, "MB": -1})
    s.force_running("GW")
    s.fall("GW", 100)                       # strap: yellow zone, within warning (150 cm)
    check(s.state("GW") == "Locked", "fall 1 m from the Robot Arm (inside its warning distance): locked out")

    section("Restart after a fall")
    s, _ = scenario({"GW": -1, "MB": 30}, "MB", 30)
    ok, why = s.safety.approve_restart("MB")
    check(not ok and "fell here" in why, "Approve restart refused while the fall check-in is open")
    with contextlib.redirect_stdout(io.StringIO()):
        s.safety.resolve_checkin("W1", "ok")
    s.tick({"GW": -1, "MB": 30}, n=3)
    ok, why = s.safety.approve_restart("MB")
    check(not ok, f"...still refused while someone is in the zone ({why})")
    s.tick({"GW": -1, "MB": -1}, n=4)
    ok, _ = s.safety.approve_restart("MB")
    check(ok and s.state("MB") == "Running", "...allowed once answered and clear -> Running")

    section("Worker environment sensors (strap BMP180 + MQ-2)")

    def env_site(**env):
        s = Site()
        s.packet({"type": "status", "worker": "W1", "strap": 1, "zone": "green", "g": 1.0, "near_led": False,
                  "alert_pending": False, "falls": 0, **env}, ("127.0.0.1", 52000))
        with contextlib.redirect_stdout(io.StringIO()):
            s.safety.update()
        return s, s.safety._worker_snapshot("W1", time.time(), None)["env"]

    s, e = env_site(temp_c=23.4, pressure_hpa=1013.25, gas_mv=612, gas_warm=True)
    check(e and e["temp_c"] == 23.4 and e["pressure_hpa"] == 1013.25 and e["gas_mv"] == 612,
          "strap status with temp_c/pressure_hpa/gas_mv is recorded (pressure to 0.01 hPa)")
    check(s.reg.env_history("W1")[-1]["pressure_hpa"] == 1013.25, "pressure is kept in the chart history")
    check(e["heat_level"] == "ok" and e["gas_level"] == "ok", "normal air -> heat and gas OK")
    check(len(s.reg.env_history("W1")) == 1, "reading is kept in the 10-minute history")
    check(s.safety.machines["GW"]["state"] == "Running", "environment readings never change machine states")
    _, e = env_site(temp_c=23.0, pressure_hpa=1012.0, gas_mv=1700, gas_warm=True)
    check(e["gas_level"] == "warn", "gas 1700 mV -> 'Gas detected' (warn)")
    s, e = env_site(temp_c=23.0, pressure_hpa=1012.0, gas_mv=2600, gas_warm=True)
    check(e["gas_level"] == "danger", "gas 2600 mV -> 'High gas' (danger)")
    check(any(ev["short"] == "High gas near Sam" for ev in s.safety.events), "...logged as an event")
    _, e = env_site(temp_c=23.0, pressure_hpa=1012.0, gas_mv=2600, gas_warm=False)
    check(e["gas_level"] == "warming", "MQ-2 still warming up -> no gas alarm yet")
    _, e = env_site(temp_c=None, pressure_hpa=None, gas_mv=500, gas_warm=True)
    check(e["heat_level"] == "offline" and e["gas_level"] == "ok", "BMP180 not answering (null) -> heat 'no reading'")
    _, e = env_site(temp_c=31.0, pressure_hpa=1010.0, gas_mv=400, gas_warm=True)
    check(e["heat_level"] == "warn", "air 31 Â°C -> high heat (warn at 30 Â°C)")
    _, e = env_site(temp_c=36.0, pressure_hpa=1010.0, gas_mv=400, gas_warm=True)
    check(e["heat_level"] == "danger", "air 36 Â°C -> dangerous heat (danger at 35 Â°C)")
    _, e = env_site(temp_c=22.0, pressure_hpa=5000, gas_mv=400, gas_warm=True)
    check(e["pressure_hpa"] is None, "impossible pressure (5000 hPa) is ignored")
    s, _ = env_site(gas_mv=2600, gas_warm=True)
    s.packet({"type": "status", "worker": "W1", "strap": 1, "zone": "green", "g": 1.0, "falls": 0,
              "gas_mv": 1950, "gas_warm": True}, ("127.0.0.1", 52000))      # 2500 - 100 mV hysteresis
    with contextlib.redirect_stdout(io.StringIO()):
        s.safety.update()
    check(s.safety._env_view["W1"]["gas_level"] == "warn", "gas falling below 2400 mV relaxes to warn (hysteresis)")
    s = Site()
    s.strap_status()
    with contextlib.redirect_stdout(io.StringIO()):
        s.safety.update()
    check(s.safety._env_view.get("W1") is None, "strap without environment sensors -> no env data (old firmware OK)")

    s, e = env_site(temp_c=22.0, pressure_hpa=1012.0, gas_mv=1100, gas_ratio=0.5, gas_level="yellow", gas_warm=True)
    check(e["gas_level"] == "warn" and e["gas_source"] == "strap",
          "strap's own MQ-2 level (yellow, vs clean air) is used instead of mV thresholds")
    check(any("50% of clean air" in ev["text"] for ev in s.safety.events), "...event shows % of clean air")
    _, e = env_site(temp_c=22.0, pressure_hpa=1012.0, gas_mv=3000, gas_ratio=None, gas_level="warming", gas_warm=False)
    check(e["gas_level"] == "warming", "strap MQ-2 still warming up / learning clean air -> no alarm")
    _, e = env_site(temp_c=22.0, pressure_hpa=1012.0, gas_mv=300, gas_ratio=0.3, gas_level="red", gas_warm=True)
    check(e["gas_level"] == "danger", "strap MQ-2 red -> 'High gas'")
    _, e = env_site(temp_c=None, pressure_hpa=None, gas_mv=213, gas_ratio=None, gas_level="fault", gas_warm=True)
    check(e["gas_level"] == "fault" and e["level"] != "ok",
          "strap reports MQ-2 'fault' (reads ~0 V) -> 'Sensor fault', never 'clean air'")

    section("Worker strap on lossy Wi-Fi")
    s = Site()
    s.strap_status()
    dev = s.reg.devices["W1"]
    dev.last_heartbeat -= 5          # 5 s with no packet (lost on Wi-Fi)
    check(dev.is_online(time.time()), "strap silent for 5 s: still online (no flapping on lost packets)")
    dev.last_heartbeat -= 4          # 9 s
    check(not dev.is_online(time.time()), f"strap silent for 9 s: offline (worker timeout {config.WORKER_TIMEOUT_S:.0f} s)")
    s.machine("GW", 100)
    s.reg.devices["GW"].last_heartbeat -= 4
    check(not s.reg.devices["GW"].is_online(time.time()), "machines keep the short 3 s timeout")

    section("'I'm OK' button on the strap")
    s, _ = scenario({"GW": -1, "MB": 30}, "MB", 30)
    check(s.safety.checkins.get("W1"), "fall near Hoist opens a check-in")
    ok = {"type": "worker_ok", "worker": "W1", "strap": 1, "boot": 7, "id": 1, "for_alert": 1, "secs_after_alert": 4}
    s.packet(ok, ("127.0.0.1", 52000))
    check(("OKACK 1", ("127.0.0.1", 52000)) in s.transport.sent, "worker_ok is confirmed with 'OKACK 1' to the sender")
    s.packet(ok, ("127.0.0.1", 52000))                     # resent until OKACK
    check(len(s.reg.worker_oks) == 1, "a resent worker_ok (same boot+id) counts once")
    check(s.transport.sent.count(("OKACK 1", ("127.0.0.1", 52000))) == 2, "...but every copy is OKACKed")
    with contextlib.redirect_stdout(io.StringIO()):
        s.safety.update()
    check("W1" not in s.safety.checkins, "I'm OK answers the open check-in (no SOS)")
    check(s.state("MB") == "Locked", "...the Hoist stays LOCKED: only a supervisor's Approve restart clears it")
    check(any(ev["short"] == "Sam pressed I'm OK" for ev in s.safety.events), "...logged as 'Sam pressed I'm OK'")
    s = Site()
    s.packet({**ok, "id": 9, "for_alert": 0, "secs_after_alert": 0}, ("127.0.0.1", 52000))
    with contextlib.redirect_stdout(io.StringIO()):
        s.safety.update()
    check(any("(check-in)" in ev["text"] for ev in s.safety.events), "a press with no fall alert is logged as a check-in")

    section("USB bridge")
    import usb_bridge
    check(usb_bridge.board_id({"type": "fall_alert", "worker": "W1", "id": 3}) == "W1",
          "strap messages are identified by 'worker' (their 'id' is the alert number)")
    check(usb_bridge.board_id({"type": "machine", "id": "MB"}) == "MB", "machine messages by 'id'")
    check({"machine", "status", "fall_alert", "impact", "worker_ok"} <= usb_bridge.FORWARD_TYPES,
          "bridge forwards machine, status, fall_alert, impact and worker_ok")

    print(f"\n{PASSES} passed, {FAILS} failed")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
