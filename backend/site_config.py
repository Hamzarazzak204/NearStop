"""
Site configuration: names, machine positions, sensor facing angles and
warning/danger distances. Stored in backend/site_config.json and editable
from the dashboard (Setup -> Machines & site).

Map coordinates are metres with (0, 0) at the bottom-left of the site.
facing_deg is the direction the machine's ultrasonic sensor points:
0 = east (right on the map), 90 = north (up), 180 = west, 270 = south.
"""

import copy
import json

import config

DEFAULT_WARNING_CM = 150
DEFAULT_DANGER_CM = 50

ENV_DEFAULTS = {"heat_warn_c": 30.0, "heat_danger_c": 35.0, "gas_warn_mv": 1500, "gas_danger_mv": 2500,
                "stale_s": 10,
                # The strap grades gas itself (resistance vs clean air); these are only
                # drawn on the dashboard chart and must match the strap firmware.
                "gas_yellow_ratio": 0.60, "gas_red_ratio": 0.35}

DEFAULTS = {
    "site_name": "NearStop",
    "site": {"width_m": 10.0, "height_m": 8.0},
    "machines": {
        # All machines: presence never slows or stops them (motor keeps its duty cycle);
        # only a fall near them (lockout), an emergency or an evacuation does.
        "MA": {"name": "Conveyor", "safe_action": "stop", "x": 9.0, "y": 1.0, "facing_deg": 135,
               "warning_cm": DEFAULT_WARNING_CM, "danger_cm": DEFAULT_DANGER_CM, "presence_stop": False},
        # presence_stop: False = someone in front does NOT slow/stop this machine; only a
        # fall near it (lockout), an emergency or an evacuation does. The Hoist works this way.
        "MB": {"name": "Hoist", "safe_action": "hold", "x": 5.0, "y": 7.0, "facing_deg": 270,
               "warning_cm": DEFAULT_WARNING_CM, "danger_cm": DEFAULT_DANGER_CM, "presence_stop": False,
               # fall_stop_zone "danger": a fall only stops it if the worker was CLOSE (within
               # danger_cm), not merely nearby. impact_hold: hold it the instant the worker's
               # strap feels a hard hit while close, before the fall is confirmed.
               "fall_stop_zone": "danger", "impact_hold": True},
    },
    "workers": {"W1": {"name": "Sam", "tag": "Vest Tag"}},
    # How far the HC-SR04 is trusted / how long the cone is drawn
    "sensor_range_cm": 300,
    # Thresholds grow with worker strain: scale = 1 + strain_growth * strain (capped)
    "adaptive_zones": {"strain_growth": 0.5, "max_scale": 1.5, "min_change_cm": 5},
    "checkin_timeout_s": 10,
    # Worker environment sensors on the strap (Monitoring tab). Heat levels use the
    # BMP180's air temperature (no humidity sensor, so no "feels like"). Pressure is shown only.
    # MQ-2 gas is its output voltage in mV: clean air is usually a few hundred mV
    # after warm-up; tune these with your own sensor (e.g. unlit lighter gas).
    "environment": dict(ENV_DEFAULTS),
    # null = send commands back to the IP:port the machine's messages come from
    "machine_command_port": None,
}

COMMENTS = {
    "_comment": "NearStop site settings. Editable in Setup > Machines & site (restart not needed). "
                "Device IDs must match the firmware IDs.",
    "_coords": "x/y in metres, (0,0) = bottom-left. facing_deg: 0 = east/right, 90 = north/up. "
               "warning_cm / danger_cm: ultrasonic distance thresholds (danger < warning).",
}


def _num(value, default):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else default


def normalise(data: dict) -> dict:
    """Merge a loaded/edited config with the defaults and fix obvious problems.
    Also upgrades the older format ("devices" with "kind")."""
    cfg = copy.deepcopy(DEFAULTS)
    if not isinstance(data, dict):
        return cfg
    cfg["site_name"] = str(data.get("site_name", cfg["site_name"]))[:40]

    site = data.get("site", {})
    if isinstance(site, dict):
        cfg["site"]["width_m"] = max(1.0, _num(site.get("width_m"), cfg["site"]["width_m"]))
        cfg["site"]["height_m"] = max(1.0, _num(site.get("height_m"), cfg["site"]["height_m"]))

    machines_in, workers_in = data.get("machines"), data.get("workers")
    if isinstance(data.get("devices"), dict):       # old format
        machines_in = {k: v for k, v in data["devices"].items() if isinstance(v, dict) and v.get("kind") == "machine"}
        workers_in = {k: v for k, v in data["devices"].items() if isinstance(v, dict) and v.get("kind") == "worker"}

    if isinstance(machines_in, dict) and machines_in:
        machines = {}
        for mid, m in machines_in.items():
            if not isinstance(m, dict):
                continue
            base = copy.deepcopy(DEFAULTS["machines"].get(mid, {
                "name": mid, "safe_action": "stop", "x": cfg["site"]["width_m"] / 2,
                "y": cfg["site"]["height_m"] / 2, "facing_deg": 0,
                "warning_cm": DEFAULT_WARNING_CM, "danger_cm": DEFAULT_DANGER_CM}))
            base["name"] = str(m.get("name", base["name"]))[:30]
            base["safe_action"] = m.get("safe_action") if m.get("safe_action") in ("stop", "hold") else base["safe_action"]
            base["presence_stop"] = m["presence_stop"] if isinstance(m.get("presence_stop"), bool) \
                else base.get("presence_stop", True)
            base["fall_stop_zone"] = m["fall_stop_zone"] if m.get("fall_stop_zone") in ("warning", "danger") \
                else base.get("fall_stop_zone", "warning")
            base["impact_hold"] = m["impact_hold"] if isinstance(m.get("impact_hold"), bool) \
                else base.get("impact_hold", False)
            # Radio presence (anchors): Sam counts as near this machine only if its strap
            # beacon is heard at least this strong (dBm). -127 = any beacon heard.
            base["near_rssi_dbm"] = int(min(max(_num(m.get("near_rssi_dbm"), base.get("near_rssi_dbm", -127)), -127), 0))
            for key in ("x", "y", "facing_deg", "warning_cm", "danger_cm"):
                base[key] = _num(m.get(key), base[key])
            base["facing_deg"] = base["facing_deg"] % 360
            base["warning_cm"] = int(min(max(base["warning_cm"], 20), 400))
            base["danger_cm"] = int(min(max(base["danger_cm"], 10), base["warning_cm"] - 5))
            base["x"] = min(max(base["x"], 0.0), cfg["site"]["width_m"])
            base["y"] = min(max(base["y"], 0.0), cfg["site"]["height_m"])
            machines[mid] = base
        cfg["machines"] = machines

    if isinstance(workers_in, dict) and workers_in:
        cfg["workers"] = {wid: {"name": str(w.get("name", wid))[:30], "tag": str(w.get("tag", ""))[:30]}
                          for wid, w in workers_in.items() if isinstance(w, dict)}

    cfg["sensor_range_cm"] = int(min(max(_num(data.get("sensor_range_cm"), cfg["sensor_range_cm"]), 50), 500))
    az = data.get("adaptive_zones", {})
    if isinstance(az, dict):
        for key in ("strain_growth", "max_scale", "min_change_cm"):
            cfg["adaptive_zones"][key] = _num(az.get(key), cfg["adaptive_zones"][key])
    cfg["checkin_timeout_s"] = min(max(_num(data.get("checkin_timeout_s"), cfg["checkin_timeout_s"]), 3), 120)
    env = data.get("environment", {})
    if isinstance(env, dict):
        for key in ENV_DEFAULTS:
            cfg["environment"][key] = _num(env.get(key), cfg["environment"][key])
    e = cfg["environment"]
    e["heat_danger_c"] = max(e["heat_danger_c"], e["heat_warn_c"] + 1)
    e["gas_warn_mv"] = int(min(max(e["gas_warn_mv"], 50), 5900))
    e["gas_danger_mv"] = int(max(e["gas_danger_mv"], e["gas_warn_mv"] + 50))
    for m in cfg["machines"].values():       # defaults too: any heard beacon counts as near
        m.setdefault("near_rssi_dbm", -127)
    port = data.get("machine_command_port")
    cfg["machine_command_port"] = int(port) if isinstance(port, int) and 1 <= port <= 65535 else None
    return cfg


def load() -> dict:
    path = config.SITE_CONFIG_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        cfg = normalise(data)
        upgraded = "devices" in data
        print(f"[site] loaded {path.name}: "
              + ", ".join(f"{k}={v['name']}" for k, v in {**cfg['machines'], **cfg['workers']}.items()))
        if upgraded:
            print("[site] upgraded old site_config format (machines now have position, facing and distances)")
            save(cfg)
        return cfg
    except FileNotFoundError:
        print(f"[site] {path.name} not found, using defaults")
    except (ValueError, OSError) as exc:
        print(f"[site] could not read {path.name} ({exc}); using defaults")
    return copy.deepcopy(DEFAULTS)


def save(cfg: dict) -> None:
    path = config.SITE_CONFIG_FILE
    try:
        path.write_text(json.dumps({**COMMENTS, **cfg}, indent=2) + "\n", encoding="utf-8")
        print(f"[site] saved {path.name}")
    except OSError as exc:
        print(f"[site] could not save {path.name}: {exc}")
