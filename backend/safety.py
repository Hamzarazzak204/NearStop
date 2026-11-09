"""
NearStop safety layer (ultrasonic version).

Every machine has an HC-SR04 ultrasonic sensor pointing in a known direction
and reports, several times a second:
    {"type":"machine","id":"MA","state":"slowed","dist_cm":87,"zone":"warning","sensor":"ok"}
(dist_cm = -1 when nothing is in range).

About 5 times a second this module decides:
  * each machine's sensor health (OK / No reading / Error = no message for 2 s);
  * which zone the nearest detected person is in (danger / warning / clear),
    using thresholds that grow with the worker's strain (adaptive zones; the
    new thresholds are sent to the machine with a set_zones command);
  * the machine state NearStop wants: Running, Slowed, Stopped, Holding, Locked.
    Stopped / Holding / Locked are LATCHED: a person must press
    "Approve restart", which is refused while anyone is inside the zone.
    State changes are sent to the machine as commands.
  * check-ins (possible fall from the vest, or "Check on") with a countdown
    that escalates to an EMERGENCY when nobody answers;
  * the overall site status and a timeline of events (with distances).

Limits worth knowing:
  * An ultrasonic sensor cannot tell WHO is in front of it. With one vest on
    site, the closest detection is assumed to be that worker.
  * A sensor only sees a narrow cone (~20 degrees) in front of the machine.
"""

import time
from collections import deque

import config
import site_config

LATCHED = ("Stopped", "Holding", "Locked")
STATE_LEVEL = {
    "Running": "ok", "Slowed": "warn", "Holding": "warn",
    "Stopped": "danger", "Locked": "danger", "Offline": "offline",
}
SAFE_ACTION_LABELS = {"stop": "Slow, then stop", "hold": "Slow, then hold load"}
STATE_COMMAND = {"Stopped": "stop", "Holding": "hold", "Locked": "lock", "Running": "resume", "Slowed": "slow"}

DANGER_TICKS = 2           # consecutive ~0.2 s ticks inside the danger zone before latching
EXIT_TICKS = 2             # consecutive ticks outside a zone before it counts as left
HYSTERESIS_CM = 10         # must be this far past a zone edge to leave it
ZONE_LOG_STEP_PCT = 10     # log adaptive-zone changes in steps of this many percent
ZONE_RESEND_S = 10.0       # re-send set_zones this often if the machine hasn't confirmed them
RESUME_GRACE_S = 5.0       # after sending "resume", give the board this long before re-adopting its lock
LATCH_RESEND_S = 1.0       # re-send stop/hold/lock this often until the board confirms it
HEAT_HYST_C = 1.0          # environment levels: must drop this far below a threshold to relax
GAS_HYST_MV = 100


def _graded(value, warn, danger, hyst, prev):
    """ok / warn / danger, with hysteresis so a reading on a threshold doesn't flicker."""
    if value >= danger or (prev == "danger" and value >= danger - hyst):
        return "danger"
    if value >= warn or (prev in ("warn", "danger") and value >= warn - hyst):
        return "warn"
    return "ok"


def _m(cm):
    return f"{cm / 100:.2f} m"


class SafetyEngine:
    def __init__(self, registry, send_command=None) -> None:
        self.registry = registry
        self.send_command = send_command or (lambda machine_id, payload: False)
        self.events: deque = deque(maxlen=300)
        self._event_id = 0
        now = time.time()
        self.started_at = now
        self.last_emergency_at: float | None = None
        self.checkins: dict[str, dict] = {}
        self.evacuation: dict | None = None
        self.last_command: dict[str, dict] = {}
        self._sent_zones: dict[str, dict] = {}
        self._zone: dict[str, str | None] = {}
        self._zone_ticks: dict[str, tuple] = {}
        self._health: dict[str, str] = {}
        self._was_online: dict[str, bool] = {}
        self._fall_seen: dict[str, int] = {}
        # Fall lockout: when each machine last saw someone within its warning
        # distance (time, cm), and the last lockout per worker.
        self._near_at: dict[str, tuple[float, int]] = {}
        self._danger_at: dict[str, tuple[float, int]] = {}   # last time someone was CLOSE (danger zone)
        self._impact_holds: dict[str, float] = {}             # machine -> time of an impact hold
        self.fall_lockouts: dict[str, dict] = {}
        self._env_level: dict[str, dict] = {}                 # worker -> {"heat": level, "gas": level}
        self._env_view: dict[str, dict | None] = {}           # worker -> environment snapshot
        self._acks_seen = registry.ack_count   # answers already there before we started don't count
        self._last_strain = 0.0
        self.apply_config(site_config.load(), announce=False)
        self.event("info", f"{self.cfg['site_name']} monitoring started", "Monitoring started")

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------
    def apply_config(self, cfg: dict, announce: bool = True) -> None:
        self.cfg = cfg
        self.machine_ids = list(cfg["machines"])
        self.worker_ids = list(cfg["workers"])
        now = time.time()
        old = getattr(self, "machines", {})
        self.machines = {m: old.get(m, {"state": "Running", "reason": "", "since": now}) for m in self.machine_ids}
        for w in self.worker_ids:
            self._fall_seen.setdefault(w, 0)
        if announce:
            self.event("info", "Site layout updated: " + ", ".join(
                f"{c['name']} warning {_m(c['warning_cm'])} / danger {_m(c['danger_cm'])}"
                for c in cfg["machines"].values()), "Site layout updated")
            self._sent_zones.clear()          # push the new thresholds to the machines

    # ------------------------------------------------------------------
    # Names, events, commands
    # ------------------------------------------------------------------
    def name(self, device_id: str) -> str:
        c = self.cfg["machines"].get(device_id) or self.cfg["workers"].get(device_id) or {}
        return c.get("name", device_id)

    def _person(self) -> str:
        """Ultrasonic sensors can't identify people: with one vest on site,
        a detection is assumed to be that worker."""
        return self.name(self.worker_ids[0]) if len(self.worker_ids) == 1 else "Worker"

    def display_name(self, device_id: str) -> str:
        w = self.cfg["workers"].get(device_id)
        if w and w.get("tag"):
            return f"{w['name']} ({w['tag']})"
        return self.name(device_id)

    def event(self, level: str, text: str, short: str | None = None) -> None:
        """text: detailed (Setup log, may include distances).
        short: plain one-line sentence for the Site view (no numbers)."""
        self._event_id += 1
        self.events.append({"id": self._event_id, "t": time.time(), "level": level,
                            "text": text, "short": short or text})
        if level == "danger":
            self.last_emergency_at = time.time()
        print(f"[event] {level.upper():6} {text}")

    def events_since(self, last_id: int) -> list[dict]:
        return [e for e in self.events if e["id"] > last_id]

    def _command(self, m: str, payload: dict) -> None:
        payload = {"id": m, **payload}
        sent = bool(self.send_command(m, payload))
        self.last_command[m] = {"t": time.time(), "payload": payload, "sent": sent}

    # ------------------------------------------------------------------
    # Readings
    # ------------------------------------------------------------------
    def _device(self, device_id):
        return self.registry.devices.get(device_id)

    def _online(self, device_id, now) -> bool:
        dev = self._device(device_id)
        return bool(dev and dev.is_online(now))

    def _strain(self, worker_id) -> float:
        dev = self._device(worker_id)
        if not dev:
            return 0.0
        if "strain" in dev.telemetry:
            return dev.telemetry["strain"]
        if "heat_c" in dev.telemetry:          # 37 C -> 0, 39 C -> 1
            return min(max((dev.telemetry["heat_c"] - 37.0) / 2.0, 0.0), 1.0)
        return 0.0

    def thresholds(self, m: str, strain: float) -> dict:
        c = self.cfg["machines"][m]
        az = self.cfg["adaptive_zones"]
        scale = min(1 + az["strain_growth"] * strain, az["max_scale"])
        return {
            "base_warning_cm": c["warning_cm"], "base_danger_cm": c["danger_cm"],
            "warning_cm": round(c["warning_cm"] * scale), "danger_cm": round(c["danger_cm"] * scale),
            "scale": round(scale, 2),
        }

    def health(self, m: str, now: float) -> tuple[str, str]:
        """("ok" | "no_reading" | "error", human text)"""
        dev = self._device(m)
        r = dev.machine if dev else None
        if not r:
            return "error", "never reported"
        age = now - r["t"]
        if age > config.SENSOR_TIMEOUT_S:
            return "error", f"no data for {age:.0f} s"
        sensor = r.get("sensor", "ok")
        if sensor in ("ok", "good"):
            return "ok", "OK"
        if sensor in ("no_reading", "noreading", "timeout", "no_echo", "none"):
            return "no_reading", "no echo from sensor"
        return "error", f"sensor reports '{sensor}'"

    def radio_rssi(self, m: str, now: float) -> float:
        """Sam's strap signal strength at machine m, averaged over the last RADIO_RSSI_AVG_S
        (-127 = not heard). Averaging stops one strong packet from counting as 'near'."""
        dev = self._device(m)
        if not dev:
            return -127
        recent = [v for t, v in dev.rssi_hist if now - t <= config.RADIO_RSSI_AVG_S]
        if recent:
            return sum(recent) / len(recent)
        return (dev.machine or {}).get("worker_rssi", -127)

    def detection_cm(self, m: str, now: float) -> int | None:
        """Distance to the nearest object in the sensor cone, or None."""
        if self.health(m, now)[0] != "ok":
            return None
        r = self._device(m).machine
        d = r["dist_cm"]
        if "worker_near" in r:
            return self._radio_detection(m, now, d)[0]
        return d if d >= 0 else None

    def _radio_detection(self, m: str, now: float, d) -> tuple:
        """Radio machines (anchors): is Sam near machine m, and how do we know?

        Only Sam's strap counts, so the strap must be heard. Then Sam is near if EITHER
          - "radio": its averaged signal is at least near_rssi_dbm (-94 dBm on the demo site), or
          - "ultrasonic": the ultrasonic sensor sees someone within the warning distance
            while the strap is heard (weaker antennas-free signal, but he's in front of it).
        Returns (distance_cm or None, "radio" | "ultrasonic" | None)."""
        r = self._device(m).machine
        if not r.get("worker_near"):
            return None, None                      # strap not heard: clear, whatever the sensor sees
        if self.radio_rssi(m, now) >= self.cfg["machines"][m].get("near_rssi_dbm", -127):
            return (d if isinstance(d, (int, float)) and 0 <= d <= config.RADIO_NEAR_CM
                    else config.RADIO_NEAR_CM), "radio"
        if isinstance(d, (int, float)) and 0 <= d <= self.thresholds(m, self._last_strain)["warning_cm"]:
            return d, "ultrasonic"
        return None, None

    def sam_near(self, m: str, now: float) -> tuple:
        """(near?, "radio" | "ultrasonic" | None) for a radio machine; (None, None) otherwise."""
        dev = self._device(m)
        r = (dev.machine or {}) if dev else {}
        if "worker_near" not in r or not self._online(m, now) or self.health(m, now)[0] != "ok":
            return None, None
        how = self._radio_detection(m, now, r.get("dist_cm"))[1]
        return how is not None, how

    @staticmethod
    def _raw_zone(d, th):
        if d is None:
            return None
        if d <= th["danger_cm"]:
            return "danger"
        if d <= th["warning_cm"]:
            return "warning"
        return "clear"

    # ------------------------------------------------------------------
    # Main update (~5 times a second)
    # ------------------------------------------------------------------
    def update(self) -> dict:
        now = time.time()
        self._online_events(now)
        self._process_falls_and_acks()
        strain = max([self._strain(w) for w in self.worker_ids if self._online(w, now)] or [0.0])
        self._last_strain = strain
        th = {m: self.thresholds(m, strain) for m in self.machine_ids}
        self._adaptive_zones(now, th, strain)
        det = {m: self.detection_cm(m, now) for m in self.machine_ids}
        for m in self.machine_ids:
            self._update_zone(m, det[m], th[m])
            if det[m] is not None and self._zone.get(m) in ("warning", "danger"):
                self._near_at[m] = (now, det[m])
            if det[m] is not None and det[m] <= th[m]["danger_cm"]:
                self._danger_at[m] = (now, det[m])
        self._release_impact_holds(now)
        self._update_environment(now)
        self._process_checkin_timeouts(now, det, th)
        for m in self.machine_ids:
            self._update_machine(m, now, det[m], th[m])
        self._enforce_latched(now)
        return self._snapshot(now, det, th, strain)

    def _enforce_latched(self, now) -> None:
        """A stop/hold/lock is sent once when the state changes. If the board missed
        it (packet lost, board rebooted or reflashed) it would run on, so while a
        machine is latched, re-send the command every LATCH_RESEND_S until the
        board reports that state."""
        for m in self.machine_ids:
            state = self.machines[m]["state"]
            if not self._online(m, now):
                continue
            dev = self._device(m)
            reported = (dev.machine or {}).get("state") if dev else None
            last = self.last_command.get(m)
            just_resumed = bool(last and last["payload"].get("cmd") == "resume" and now - last["t"] < RESUME_GRACE_S)
            if reported == "locked" and state not in LATCHED and not just_resumed:
                # Only NearStop ever locks a machine, so the board kept a lockout from
                # before NearStop (re)started: show it, and require Approve restart.
                self._set_machine(m, "Locked", "was still locked on the machine (NearStop restarted) – "
                                  "approve restart when it is safe", "danger")
                continue
            if state not in LATCHED:
                continue
            if reported == state.lower():
                continue
            last = self.last_command.get(m)
            if last and now - last["t"] < LATCH_RESEND_S:
                continue
            self._command(m, {"type": "command", "cmd": STATE_COMMAND[state],
                              "reason": f"re-sent: board reports '{reported}' but NearStop has it {state}"})


    def _online_events(self, now) -> None:
        for dev_id in list(self.cfg["machines"]) + list(self.cfg["workers"]):
            online = self._online(dev_id, now)
            was = self._was_online.get(dev_id)
            if was is not None and online != was:
                self.event("ok" if online else "warn",
                           f"{self.display_name(dev_id)} {'back online' if online else 'went offline'}",
                           f"{self.name(dev_id)} {'online' if online else 'offline'}")
            elif was is None and online:
                self.event("ok", f"{self.display_name(dev_id)} connected", f"{self.name(dev_id)} online")
            self._was_online[dev_id] = online
        for m in self.machine_ids:
            h, text = self.health(m, now)
            prev = self._health.get(m)
            seen = bool(self._device(m) and self._device(m).machine)
            if prev is not None and h != prev and seen:
                if h == "ok":
                    self.event("ok", f"{self.name(m)} sensor OK", f"{self.name(m)} sensor OK")
                elif h == "no_reading":
                    self.event("warn", f"{self.name(m)} sensor: no reading ({text})", f"{self.name(m)} sensor fault")
                else:
                    self.event("warn", f"{self.name(m)} sensor ERROR – {text}", f"{self.name(m)} sensor fault")
            self._health[m] = h

    def _env_event(self, kind, level, prev, w, text_value):
        """Log a change of a worker's gas/heat level (only changes into or out of warn/danger)."""
        if level == prev or not (level in ("warn", "danger") or prev in ("warn", "danger")):
            return
        name = self.name(w)
        if kind == "gas":
            words = {"danger": ("High combustible gas", "High gas"), "warn": ("Combustible gas detected", "Gas detected"),
                     "ok": ("Gas back to normal", "Gas normal")}
        else:
            words = {"danger": ("Dangerous heat", "Dangerous heat"), "warn": ("High heat", "High heat"),
                     "ok": ("Heat back to normal", "Heat normal")}
        if level not in words:          # sensor went quiet / stale while high
            self.event("warn", f"{name}'s {kind} sensor stopped reporting", f"{name}'s {kind} sensor offline")
            return
        detail, short = words[level]
        self.event(level, f"{detail} at {name}'s strap ({text_value})", f"{short} near {name}")

    def _update_environment(self, now) -> None:
        """Grade each worker's strap environment readings and log level changes."""
        cfg = self.cfg["environment"]
        rank = {"danger": 3, "warn": 2, "ok": 1}
        for w in self.worker_ids:
            dev = self._device(w)
            env = dev.telemetry.get("env") if dev else None
            if not env:
                self._env_view[w] = None
                continue
            prev = self._env_level.get(w, {})
            age = now - env["t"]
            stale = age > cfg["stale_s"]
            t, pr, g = env["temp_c"], env["pressure_hpa"], env["gas_mv"]
            if stale or t is None:
                heat = "offline"
            else:
                heat = _graded(t, cfg["heat_warn_c"], cfg["heat_danger_c"], HEAT_HYST_C, prev.get("heat"))
            strap_level = env.get("gas_strap_level")
            if stale or (g is None and strap_level is None):
                gas = "offline"
            elif strap_level:                  # the strap's own MQ-2 level (vs clean air)
                gas = {"warming": "warming", "green": "ok", "yellow": "warn", "red": "danger",
                       "fault": "fault"}[strap_level]      # fault = wiring/power problem, never "clean air"
            elif not env["gas_warm"]:
                gas = "warming"
            else:
                gas = _graded(g, cfg["gas_warn_mv"], cfg["gas_danger_mv"], GAS_HYST_MV, prev.get("gas"))
            ratio = env.get("gas_ratio")
            if ratio is not None:
                gas_text = f"sensor at {ratio * 100:.0f}% of clean air"
            else:
                gas_text = f"{g:.0f} mV" if g is not None else "no reading"
            self._env_event("gas", gas, prev.get("gas", "offline"), w, gas_text)
            self._env_event("heat", heat, prev.get("heat", "offline"), w,
                            f"{t:.0f} °C" if t is not None else "no reading")
            self._env_level[w] = {"heat": heat, "gas": gas}
            overall = max((heat, gas), key=lambda x: rank.get(x, 0))
            self._env_view[w] = {
                "temp_c": t, "pressure_hpa": pr, "gas_mv": g, "gas_warm": env["gas_warm"],
                "gas_ratio": ratio, "gas_source": "strap" if strap_level else "mv",
                "age_s": round(age, 1), "stale": stale,
                "heat_level": heat, "gas_level": gas,
                "level": overall if overall in rank else "offline",
                "thresholds": dict(cfg),
            }

    def _process_falls_and_acks(self) -> None:
        for w in self.worker_ids:
            dev = self._device(w)
            if dev and dev.fall_count > self._fall_seen.get(w, 0):
                self._fall_seen[w] = dev.fall_count
                where = self._fall_lockout(w)
                # Name the machine the worker fell at (not just the closest detection);
                # a fall away from every machine names none.
                self.start_checkin(w, "fall", near_override=where, away=where is None)
        oks = {(w, t): (fa, secs) for w, t, fa, secs in self.registry.worker_oks}
        # New answers = the ones added since we last looked (counted, not timed: two
        # answers can share a clock tick on Windows).
        new = min(self.registry.ack_count - self._acks_seen, len(self.registry.acks))
        self._acks_seen = self.registry.ack_count
        for dev_id, t in (list(self.registry.acks)[-new:] if new > 0 else []):
            if (dev_id, t) in oks:          # strap "I'm OK" button
                fa, secs = oks[(dev_id, t)]
                name = self.name(dev_id)
                locked = [self.name(m) for m in self.machine_ids
                          if self.machines[m]["state"] == "Locked" and "fell" in self.machines[m]["reason"]]
                self.event("ok", f"{name} pressed I'M OK on the strap"
                           + (f", {secs} s after fall alert #{fa}" if fa else " (check-in)")
                           + (f". {', '.join(locked)} stay locked until a supervisor approves restart" if locked else ""),
                           f"{name} pressed I'm OK")
            if dev_id in self.checkins:
                status = self.checkins.pop(dev_id)["status"]
                self.event("ok", f"{self.name(dev_id)} responded: OK"
                           + (" (emergency cleared)" if status == "emergency" else ""),
                           f"{self.name(dev_id)} responded OK")

    def _adaptive_zones(self, now, th, strain) -> None:
        """Send set_zones whenever a machine's effective thresholds change (or
        haven't been confirmed), and log one summary line per noticeable step."""
        min_change = self.cfg["adaptive_zones"]["min_change_cm"]
        for m in self.machine_ids:
            t = th[m]
            sent = self._sent_zones.get(m)
            changed = (sent is None or abs(sent["warning_cm"] - t["warning_cm"]) >= min_change
                       or abs(sent["danger_cm"] - t["danger_cm"]) >= min_change)
            dev = self._device(m)
            echo = dev.machine if dev and dev.machine else {}
            confirmed = echo.get("warning_cm") == (sent or {}).get("warning_cm") and \
                echo.get("danger_cm") == (sent or {}).get("danger_cm")
            resend = (sent is not None and not confirmed and "warning_cm" in echo
                      and now - sent["t"] > ZONE_RESEND_S)
            if (changed or resend) and self._online(m, now):
                self._command(m, {"type": "set_zones", "warning_cm": t["warning_cm"], "danger_cm": t["danger_cm"]})
                self._sent_zones[m] = {"warning_cm": t["warning_cm"], "danger_cm": t["danger_cm"], "t": now}

        # One timeline line for the whole site, only on steps of >= ZONE_LOG_STEP_PCT
        scale = max((th[m]["scale"] for m in self.machine_ids), default=1.0)
        logged = getattr(self, "_logged_scale", 1.0)
        if abs(scale - logged) * 100 >= ZONE_LOG_STEP_PCT or (scale == 1.0 and logged != 1.0):
            who = max(self.worker_ids, key=self._strain) if self.worker_ids else None
            detail = ", ".join(f"{self.name(m)} {_m(th[m]['warning_cm'])}/{_m(th[m]['danger_cm'])}"
                               for m in self.machine_ids)
            if scale == 1.0:
                self.event("info", f"Zones back to normal (warning/danger): {detail}", "Extra caution off")
            else:
                verb = "raised" if scale > logged else "lowered"
                self.event("warn" if scale > logged else "info",
                           f"Zones {verb} +{round((scale - 1) * 100)}% for {self.name(who)}'s strain "
                           f"({round(strain * 100)}%): {detail}",
                           f"Extra caution: {self.name(who)} under strain" if scale > logged else "Extra caution reduced")
            self._logged_scale = scale

    def _update_zone(self, m, d, t) -> None:
        cur = self._zone.get(m)
        raw = self._raw_zone(d, t)
        # Hysteresis: the sensor jitters a few cm, so only leave a zone once
        # clearly outside it (10 cm past the edge).
        if d is not None:
            if cur == "danger" and d <= t["danger_cm"] + HYSTERESIS_CM:
                raw = "danger"
            elif cur in ("danger", "warning") and raw == "clear" and d <= t["warning_cm"] + HYSTERESIS_CM:
                raw = "warning"
        prev_raw, count = self._zone_ticks.get(m, (None, 0))
        count = count + 1 if raw == prev_raw else 1
        self._zone_ticks[m] = (raw, count)
        rank = {None: 0, "clear": 0, "warning": 1, "danger": 2}
        new = cur
        if rank[raw] > rank[cur]:
            # entering: warning immediately, danger after DANGER_TICKS readings
            if raw == "warning" or count >= DANGER_TICKS:
                new = raw
        elif rank[raw] < rank[cur]:
            if count >= EXIT_TICKS:      # leaving needs ~0.4 s (a single dropout doesn't count)
                new = raw
        else:
            new = raw
        if new != cur:
            name = self.name(m)
            if new == "danger":
                self.event("danger", f"Person {_m(d)} from {name} – inside DANGER zone (≤ {_m(t['danger_cm'])})",
                           f"{self._person()} too close to {name}")
            elif new == "warning" and rank[cur] < 1:
                self.event("warn", f"Person {_m(d)} from {name} – warning zone (≤ {_m(t['warning_cm'])})",
                           f"{self._person()} near {name}")
            elif rank[new] == 0 and rank[cur] > 0:
                self.event("ok", f"{name} area clear" + (f" (nearest object {_m(d)})" if d is not None
                                                         else " (nothing in range)"), f"{name} clear")
        self._zone[m] = new

    def _set_machine(self, m, state, reason, level=None) -> None:
        mach = self.machines[m]
        if mach["state"] == state:
            mach["reason"] = reason
            return
        mach.update(state=state, reason=reason, since=time.time())
        short = (f"Restart approved: {self.name(m)}" if "restart approved" in reason
                 else f"{self.name(m)} {'running' if state == 'Running' else state.lower()}")
        self.event(level or STATE_LEVEL.get(state, "info"), f"{self.name(m)}: {state.upper()} – {reason}", short)
        self._command(m, {"type": "command", "cmd": STATE_COMMAND[state], "reason": reason})

    def _update_machine(self, m, now, d, t) -> None:
        mach = self.machines[m]
        if self.evacuation:
            if mach["state"] != "Locked":
                self._set_machine(m, "Locked", "evacuation", "danger")
            return
        if mach["state"] in LATCHED or not self._online(m, now):
            return
        if not self.cfg["machines"][m].get("presence_stop", True):
            # Presence alone doesn't slow/stop this machine (only a fall lockout,
            # an emergency or an evacuation does). Zones are still tracked and shown.
            if mach["state"] != "Running":
                self._set_machine(m, "Running", "presence doesn't stop this machine – only a fall near it", "ok")
            return
        stop_state = "Holding" if self.cfg["machines"][m].get("safe_action") == "hold" else "Stopped"
        zone = self._zone.get(m)
        if zone == "danger":
            self._set_machine(m, stop_state, f"person {_m(d)} away (danger ≤ {_m(t['danger_cm'])})", "danger")
        elif zone == "warning":
            self._set_machine(m, "Slowed", f"person {_m(d)} away (warning ≤ {_m(t['warning_cm'])})", "warn")
        elif mach["state"] == "Slowed":
            self._set_machine(m, "Running", "area clear", "ok")

    # ------------------------------------------------------------------
    # Check-ins, emergencies, evacuation, restart (also called by the API)
    # ------------------------------------------------------------------
    def _closest(self, det=None):
        """(machine_id, distance_cm) of the closest detection, or None."""
        if det is None:
            now = time.time()
            det = {m: self.detection_cm(m, now) for m in self.machine_ids}
        hits = [(m, d) for m, d in det.items() if d is not None]
        return min(hits, key=lambda x: x[1]) if hits else None

    def start_checkin(self, worker_id, reason, near_override=None, away=False) -> tuple[bool, str]:
        if worker_id not in self.worker_ids:
            return False, "unknown worker"
        if worker_id in self.checkins:
            if near_override and self.checkins[worker_id]["status"] == "pending":
                # A new fall during a running check-in: point it at the new machine
                self.checkins[worker_id].update(machine=near_override[0], distance_cm=near_override[1], pinned=True)
            return False, "a check-in is already running"
        near = near_override or (None if away else self._closest())
        timeout = self.cfg["checkin_timeout_s"]
        self.checkins[worker_id] = {
            "status": "pending", "reason": reason, "started": time.time(),
            "deadline": time.time() + timeout,
            "machine": near[0] if near else None, "distance_cm": near[1] if near else None,
            "pinned": bool(near_override),     # keep naming the machine the worker fell at
        }
        where = f" near {self.name(near[0])} ({_m(near[1])})" if near else ""
        if reason == "fall":
            self.event("warn", f"Possible fall: {self.name(worker_id)}{where} – waiting for response",
                       f"Possible fall: {self.name(worker_id)}")
        else:
            self.event("info", f"Check-in sent to {self.name(worker_id)}{where} – waiting for response",
                       f"Checking on {self.name(worker_id)}")
        return True, "ok"

    def _fall_lockout(self, worker_id) -> None:
        """A worker fell: LOCK OUT every machine they were near.

        "Near" = the machine saw someone within its warning distance in the last
        FALL_NEAR_MEMORY_S seconds (the person may drop out of the narrow sensor
        cone when they hit the floor), plus the machine the strap itself reports.
        Locked machines stay locked until a supervisor approves the restart, which
        is refused while the fall check-in is open or the zone is occupied."""
        now = time.time()
        dev = self._device(worker_id)
        reported = (dev.telemetry.get("last_fall") or {}) if dev else {}
        mem = config.FALL_NEAR_MEMORY_S
        near = {}
        for m in self.machines:
            # Machines with fall_stop_zone "danger" (the Hoist) only count if the worker
            # was CLOSE (danger zone); the others if within their warning distance.
            src = self._danger_at if self.cfg["machines"][m].get("fall_stop_zone") == "danger" else self._near_at
            radio = "worker_near" in ((self._device(m).machine or {}) if self._device(m) else {})
            # Radio machines: only if they hear Sam (strongly enough) right at the fall. The
            # ultrasonic-era 4 s memory locked a machine Sam had just walked away from.
            if m in src and now - src[m][0] <= (config.RADIO_FALL_MEMORY_S if radio else mem):
                if not radio or self.detection_cm(m, now) is not None:
                    near[m] = src[m][1]
        # The machine the strap names counts only if the worker was within its lockout
        # distance (the strap sends EVERY fall now, also ones far from any machine).
        m_strap = reported.get("machine")
        if m_strap in self.machines and m_strap not in near and now - reported.get("t", 0) < 10:
            d = reported.get("distance_cm")
            th = self.thresholds(m_strap, self._last_strain)
            close_only = self.cfg["machines"][m_strap].get("fall_stop_zone") == "danger"
            limit = th["danger_cm"] if close_only else th["warning_cm"]
            m_radio = "worker_near" in ((self._device(m_strap).machine or {}) if self._device(m_strap) else {})
            # Radio machine: the strap's red-zone memory isn't enough; it must hear Sam now.
            if d is not None and d <= limit and (not m_radio or self.detection_cm(m_strap, now) is not None):
                near[m_strap] = d
        for m in near:
            self._impact_holds.pop(m, None)    # the hold becomes a lockout
        who = self.name(worker_id)
        if not near:
            peak = reported.get("peak_g")
            self.event("danger", f"FALL: {who} fell – not near a machine"
                       + (f" ({peak:.1f} g)" if isinstance(peak, (int, float)) else "")
                       + ". Nothing locked out. Check on them now.", f"{who} fell – check on them")
            return None
        locked = []
        for m, d in sorted(near.items(), key=lambda x: x[1] if x[1] is not None else 9999):
            state = self.machines[m]["state"]
            if state == "Locked":
                continue
            was = "running" if state in ("Running", "Slowed") else state.lower()
            where = f"{_m(d)} away" if d is not None else "nearby"
            self._set_machine(m, "Locked", f"LOCKED OUT – {who} fell {where} (was {was})", "danger")
            locked.append(self.name(m))
        names = ", ".join(locked) or ", ".join(self.name(m) for m in near)
        peak = reported.get("peak_g")
        # Primary machine: the one the strap names, else the nearest one that saw the worker
        primary = m_strap if m_strap in near else min(near, key=lambda m: near[m] if near[m] is not None else 9999)
        self.fall_lockouts[worker_id] = {"t": now, "machines": locked or [self.name(m) for m in near],
                                         "machine_ids": list(near), "primary": self.name(primary),
                                         "peak_g": peak, "severity": reported.get("severity")}
        self.event("danger", f"FALL: {who} fell near {names}"
                   + (f" ({peak:.1f} g)" if isinstance(peak, (int, float)) else "")
                   + " – machine LOCKED OUT until a supervisor approves restart",
                   f"Fall near {names}: locked out")
        return (primary, near[primary])

    def on_impact(self, worker_id, msg) -> None:
        """The strap felt a hard hit while the worker was close to a machine (sent the
        instant it happens, ~1.3 s before a fall can be confirmed). Machines with
        impact_hold (the Hoist) are HELD at once if the worker was close to them.
        A confirmed fall turns the hold into a lockout; otherwise it is released
        after IMPACT_HOLD_S."""
        now = time.time()
        reported = msg.get("machine_id") if isinstance(msg.get("machine_id"), str) else \
            config.STRAP_MACHINE_IDS.get(msg.get("machine"))
        rep_d = msg.get("distance_cm") if isinstance(msg.get("distance_cm"), (int, float)) else None
        who = self.name(worker_id)
        for m in self.machine_ids:
            c = self.cfg["machines"][m]
            if not c.get("impact_hold") or self.machines[m]["state"] not in ("Running", "Slowed"):
                continue
            danger = self.thresholds(m, self._last_strain)["danger_cm"]
            close = None
            if m in self._danger_at and now - self._danger_at[m][0] <= config.FALL_NEAR_MEMORY_S:
                close = self._danger_at[m][1]
            elif m == reported and rep_d is not None and rep_d <= danger:
                close = rep_d
            if close is None:
                continue
            self._impact_holds[m] = now
            self._set_machine(m, "Holding", f"possible fall: {who} hit the ground {_m(close)} away – "
                              "holding until confirmed", "danger")

    def _release_impact_holds(self, now) -> None:
        for m, t in list(self._impact_holds.items()):
            if now - t < config.IMPACT_HOLD_S:
                continue
            self._impact_holds.pop(m)
            mach = self.machines[m]
            if mach["state"] == "Holding" and mach["reason"].startswith("possible fall"):
                self._set_machine(m, "Running", "no fall confirmed – resumed", "ok")

    def _process_checkin_timeouts(self, now, det, th) -> None:
        for w, c in list(self.checkins.items()):
            if c["status"] == "pending":
                near = self._closest(det)
                if near and not c.get("pinned"):
                    c["machine"], c["distance_cm"] = near
                if now >= c["deadline"]:
                    self._escalate(w, det, th, "No response")

    def resolve_checkin(self, worker_id, outcome) -> tuple[bool, str]:
        c = self.checkins.get(worker_id)
        if not c:
            return False, "no check-in running"
        if outcome == "ok":
            self.checkins.pop(worker_id)
            self.event("ok", f"{self.name(worker_id)} confirmed OK by supervisor", f"{self.name(worker_id)} confirmed OK")
        elif outcome == "escalate":
            now = time.time()
            det = {m: self.detection_cm(m, now) for m in self.machine_ids}
            th = {m: self.thresholds(m, self._last_strain) for m in self.machine_ids}
            self._escalate(worker_id, det, th, "Escalated by supervisor")
        else:
            return False, "outcome must be 'ok' or 'escalate'"
        return True, "ok"

    def _escalate(self, worker_id, det, th, why) -> None:
        c = self.checkins.setdefault(worker_id, {"reason": "manual", "started": time.time(),
                                                  "machine": None, "distance_cm": None})
        c.update(status="emergency", since=time.time(), deadline=None)
        near = (c["machine"], c.get("distance_cm")) if c.get("pinned") and c.get("machine") else self._closest(det)
        where = f" near {self.name(near[0])} ({_m(near[1])})" if near else ""
        self.event("danger", f"{why} from {self.name(worker_id)}{where} – EMERGENCY",
                   f"SOS: {self.name(worker_id)} not responding")
        # Stop every machine that sees someone within its warning distance;
        # otherwise the machine with the closest detection.
        targets = [m for m in self.machine_ids if det.get(m) is not None and det[m] <= th[m]["warning_cm"]]
        if not targets and near:
            targets = [near[0]]
        if near and not c.get("pinned"):
            c["machine"], c["distance_cm"] = near
        for m in targets:
            stop_state = "Holding" if self.cfg["machines"][m].get("safe_action") == "hold" else "Stopped"
            if self.machines[m]["state"] not in LATCHED:
                self._set_machine(m, stop_state,
                                  f"emergency: {self.name(worker_id)} not responding at {_m(det[m])}", "danger")
        if not targets:
            self.event("warn", f"No machine detects {self.name(worker_id)} – nothing to stop",
                       f"No machine near {self.name(worker_id)}")

    def resolve_emergency(self, worker_id=None) -> tuple[bool, str]:
        targets = [w for w, c in self.checkins.items() if c["status"] == "emergency"
                   and (worker_id is None or w == worker_id)]
        if not targets:
            return False, "no active emergency"
        for w in targets:
            self.checkins.pop(w)
            self.event("ok", f"Emergency for {self.name(w)} resolved by supervisor", "Emergency resolved")
        return True, "ok"

    def set_evacuation(self, active: bool) -> tuple[bool, str]:
        if active and not self.evacuation:
            self.evacuation = {"since": time.time()}
            self.event("danger", "EVACUATION started – all equipment locked", "Evacuation started")
        elif not active and self.evacuation:
            self.evacuation = None
            self.event("ok", "Evacuation ended – equipment stays locked until restart is approved", "Evacuation ended")
        return True, "ok"

    def restart_blockers(self, m, now=None) -> str | None:
        now = now or time.time()
        mach = self.machines[m]
        if mach["state"] not in LATCHED:
            return "not stopped"
        if self.evacuation:
            return "evacuation in progress"
        if not self._online(m, now):
            return f"{self.name(m)} is offline"
        h, text = self.health(m, now)
        if h != "ok":
            return f"sensor not reading ({text}) – can't confirm the zone is clear"
        for w, c in self.checkins.items():
            if c["status"] == "emergency" and c.get("machine") == m:
                return f"emergency for {self.name(w)} still active"
            if c.get("reason") == "fall" and m in [mm for mm in self.machine_ids
                                                   if self.name(mm) in self.fall_lockouts.get(w, {}).get("machines", [])]:
                return f"{self.name(w)} fell here – waiting for the check-in to be answered"
        d = self.detection_cm(m, now)
        t = self.thresholds(m, self._last_strain)
        # Same zone state the machine logic uses (includes the exit hysteresis),
        # so a restart is never approved while the zone still counts as occupied.
        if (d is not None and d <= t["warning_cm"]) or self._zone.get(m) in ("warning", "danger"):
            where = f"person {_m(d)} in front" if d is not None else "zone still occupied"
            return f"{where} – zone not clear (needs > {_m(t['warning_cm'] + HYSTERESIS_CM)})"
        return None

    def approve_restart(self, m) -> tuple[bool, str]:
        if m not in self.machines:
            return False, "unknown machine"
        blocker = self.restart_blockers(m)
        if blocker:
            return False, blocker
        d = self.detection_cm(m, time.time())
        clear = f"nearest object {_m(d)}" if d is not None else "nothing in range"
        self._set_machine(m, "Running", f"restart approved by supervisor ({clear})", "ok")
        return True, "ok"

    # ------------------------------------------------------------------
    # Snapshot for the dashboard
    # ------------------------------------------------------------------
    def _snapshot(self, now, det, th, strain) -> dict:
        closest = self._closest(det)
        workers = [self._worker_snapshot(w, now, closest) for w in self.worker_ids]
        machines = [self._machine_snapshot(m, now, det[m], th[m]) for m in self.machine_ids]

        reasons, level = [], "clear"
        if self.evacuation:
            level = "emergency"; reasons.append("Evacuation in progress")
        for w in workers:
            if w["checkin"] and w["checkin"]["status"] == "emergency":
                level = "emergency"; reasons.append(f"{w['name']} not responding")
        if level != "emergency":
            for w in workers:
                if w["checkin"]:
                    reasons.append(f"Checking on {w['name']}")
                elif w["status"]["level"] in ("warn", "danger"):
                    reasons.append(f"{w['name']}: {w['status']['text']}")
            for m in machines:
                if m["display_state"] != "Running":
                    reasons.append(f"{m['name']} {m['display_state'].lower()}")
                elif m["sensor"]["health"] != "ok":
                    reasons.append(f"{m['name']} sensor {m['sensor']['label'].lower()}")
            for w in workers:
                env = w.get("env")
                if env and env["gas_level"] in ("warn", "danger"):
                    reasons.append(f"{'High gas' if env['gas_level'] == 'danger' else 'Gas detected'} near {w['name']}")
                if env and env["heat_level"] in ("warn", "danger"):
                    reasons.append(f"{'Dangerous' if env['heat_level'] == 'danger' else 'High'} heat near {w['name']}")
            for w in workers:
                if w["online"] and (w.get("strap") or {}).get("imu") is False:
                    reasons.append(f"{w['name']}'s fall detection OFF (motion sensor)")
            for w in workers:
                if not w["online"]:
                    reasons.append(f"{w['name']}'s vest offline")
            if reasons:
                level = "warning"
        text = {"clear": "All clear", "warning": "Warning", "emergency": "Emergency"}[level]
        return {
            "name": self.cfg["site_name"],
            "room": {"width": self.cfg["site"]["width_m"], "height": self.cfg["site"]["height_m"]},
            "status": {"level": level, "text": text, "reasons": reasons[:4]},
            "hub": {"id": None, "name": None, "online": False, "seen": False},
            "evacuation": {"active": bool(self.evacuation),
                           "since": self.evacuation["since"] if self.evacuation else None},
            "workers": workers,
            "machines": machines,
            "names": {d: self.display_name(d) for d in list(self.cfg["machines"]) + list(self.cfg["workers"])},
            "sensor_range_cm": self.cfg["sensor_range_cm"],
            "strain": round(strain, 2),
            "checkin_timeout_s": self.cfg["checkin_timeout_s"],
            "started_at": self.started_at,
            "last_emergency_at": self.last_emergency_at,
            "events": list(self.events)[-60:],
        }

    def _worker_snapshot(self, w, now, closest) -> dict:
        dev = self._device(w)
        online = self._online(w, now)
        c = self.checkins.get(w)
        checkin = None
        if c:
            checkin = {
                "status": c["status"], "reason": c["reason"],
                "machine_name": self.name(c["machine"]) if c.get("machine") else None,
                "distance_m": round(c["distance_cm"] / 100, 2) if c.get("distance_cm") is not None else None,
                "seconds_left": max(0, round(c["deadline"] - now, 1)) if c.get("deadline") else None,
                "started": c["started"],
            }
        # With one vest on site, the closest ultrasonic detection is assumed to be this worker.
        near = None
        if closest and len(self.worker_ids) == 1:
            m, d = closest
            mdev = self._device(m)
            radio = bool(mdev and (mdev.machine or {}).get("worker_near"))
            near = {"machine_id": m, "name": self.name(m), "distance_m": round(d / 100, 2),
                    "zone": self._zone.get(m), "radio": radio}
        if not online:
            status = ("offline", "Vest offline" if dev and dev.last_seen else "Vest not connected")
        elif c and c["status"] == "emergency":
            status = ("danger", "Not responding – EMERGENCY")
        elif c:
            status = ("warn", "Possible fall – checking in" if c["reason"] == "fall" else "Check-in pending")
        elif near and near.get("radio"):
            status = ("warn", f"Near {near['name']} (strap heard by radio)")
        elif near and near["zone"] == "danger":
            status = ("danger", f"In {near['name']} danger zone ({near['distance_m']:.2f} m)")
        elif near and near["zone"] == "warning":
            status = ("warn", f"Near {near['name']} ({near['distance_m']:.2f} m)")
        elif near:
            status = ("ok", f"Near {near['name']} ({near['distance_m']:.2f} m)")
        else:
            status = ("ok", "On site – not near any machine")
        tel = dev.telemetry if dev else {}
        return {
            "id": w, "name": self.name(w), "tag": self.cfg["workers"][w].get("tag"),
            "display_name": self.display_name(w),
            "online": online,
            "simulated": bool(dev and dev.simulated),
            "last_seen_ago": None if not dev or dev.last_seen is None else round(now - dev.last_seen, 1),
            "status": {"level": status[0], "text": status[1]},
            "near": near if online or near else None,
            "activity": tel.get("activity"),
            "heat_c": tel.get("heat_c"),
            "strain": round(self._strain(w), 2),
            "fall_active": bool(dev and dev.fall_active),
            "has_sensors": bool(tel),
            "strap": tel.get("strap"),          # worker strap: zone colour, near-LED, g
            "last_fall": None if w not in self.fall_lockouts else {
                **self.fall_lockouts[w], "ago_s": round(now - self.fall_lockouts[w]["t"], 1)},
            "checkin": checkin,
            # EVERY fall the strap reported (near a machine or not), for the dashboard's Fall banner
            "recent_fall": None if not (dev and dev.telemetry.get("last_fall")) else {
                "t": dev.telemetry["last_fall"]["t"],
                "ago_s": round(now - dev.telemetry["last_fall"]["t"], 1),
                "peak_g": dev.telemetry["last_fall"].get("peak_g"),
                "machine_name": self.name(dev.telemetry["last_fall"]["machine"])
                if dev.telemetry["last_fall"].get("machine") in self.machines else None,
                "zone": dev.telemetry["last_fall"].get("zone"),
            },
            "env": self._env_view.get(w),       # strap BMP180 + MQ-2 (Monitoring tab), None = no sensors
        }

    def _machine_snapshot(self, m, now, d, t) -> dict:
        cfg = self.cfg["machines"][m]
        mach = self.machines[m]
        dev = self._device(m)
        online = self._online(m, now)
        h, htext = self.health(m, now)
        reading = dev.machine if dev and dev.machine else {}
        latched = mach["state"] in LATCHED
        blocker = self.restart_blockers(m, now) if latched else None
        display_state = mach["state"] if online or latched else "Offline"
        sent = self._sent_zones.get(m)
        synced = None
        if sent and "warning_cm" in reading:
            synced = reading.get("warning_cm") == sent["warning_cm"] and reading.get("danger_cm") == sent["danger_cm"]
        lc = self.last_command.get(m)
        return {
            "id": m, "name": cfg["name"],
            "x": cfg["x"], "y": cfg["y"], "facing_deg": cfg["facing_deg"],
            "online": online,
            "seen": bool(dev and dev.last_seen),
            "state": mach["state"],
            "display_state": display_state,
            "level": STATE_LEVEL["Offline"] if display_state == "Offline" else STATE_LEVEL[mach["state"]],
            "reason": mach["reason"] if online or latched else
                      ("never connected" if not (dev and dev.last_seen) else "offline – not monitored"),
            "since": mach["since"],
            "machine_reports": reading.get("state"),
            "safe_action": cfg.get("safe_action", "stop"),
            "safe_action_label": SAFE_ACTION_LABELS.get(cfg.get("safe_action", "stop"), cfg.get("safe_action")),
            "sensor": {"health": h, "label": {"ok": "OK", "no_reading": "No reading", "error": "Error"}[h],
                       "text": htext, "age_s": round(now - reading["t"], 1) if reading else None},
            "dist_cm": d,
            "raw_dist_cm": reading.get("dist_cm"),
            "zone": self._zone.get(m),
            # Radio presence: True/False when the machine listens for Sam's strap, None otherwise
            "worker_near": reading.get("worker_near") if online else None,
            "worker_rssi": reading.get("worker_rssi"),
            "near_rssi_dbm": self.cfg["machines"][m].get("near_rssi_dbm", -127),
            # The backend's own verdict (same rule as lockouts): Sam near this machine, and how
            "sam_near": self.sam_near(m, now)[0],
            "sam_near_by": self.sam_near(m, now)[1],
            "worker_rssi_avg": (round(self.radio_rssi(m, now)) if reading.get("worker_near") else None),
            "thresholds": {**t, "raised": t["scale"] > 1.0},
            "range_cm": self.cfg["sensor_range_cm"],
            "zones_synced": synced,
            "last_command": {"payload": lc["payload"], "age_s": round(now - lc["t"], 1), "sent": lc["sent"]} if lc else None,
            "latched": latched,
            "can_restart": latched and blocker is None,
            "restart_blocked_reason": blocker,
        }
