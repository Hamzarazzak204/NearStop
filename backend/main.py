"""
NearStop backend - FastAPI app.

Runs in ONE process:
  * UDP receiver on 0.0.0.0:5005 (machine sensor reports + vest heartbeats)
  * commands back to the machines over UDP (set_zones, stop / resume ...)
  * HTTP API + dashboard on 0.0.0.0:8000
  * WebSocket /ws pushing live state ~5 times per second

Start it from the project folder with:
    python backend/main.py
"""

import asyncio
import json
import socket
import time
from contextlib import asynccontextmanager

import app_log
app_log.install()   # copy all console output into the dashboard's Log panel

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import config
import site_config
from device_registry import DeviceRegistry
from safety import SafetyEngine
from serial_monitor import SerialMonitor
from sim_control import SimulatorControl
from udp_receiver import UDPStats, start_udp_receiver

# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------
registry = DeviceRegistry()
udp_stats = UDPStats()
simulator = SimulatorControl()
serial_mon = SerialMonitor()
ws_clients: set[WebSocket] = set()
latest_state: dict = {}
udp_transport = None          # set at start-up; also used to send commands


def send_machine_command(machine_id: str, payload: dict) -> bool:
    """Send a JSON command to a machine over UDP.

    It goes back to the IP:port the machine's reports come from (so the
    machine firmware must send from a fixed port with udp.begin(port) and
    listen on it), or to machine_command_port if set in site_config.json.
    """
    dev = registry.devices.get(machine_id)
    if udp_transport is None or dev is None or not dev.ip:
        return False
    port = safety.cfg.get("machine_command_port") or dev.port
    try:
        udp_transport.sendto(json.dumps(payload, separators=(",", ":")).encode(), (dev.ip, port))
        return True
    except OSError as exc:
        print(f"[cmd] could not send to {machine_id} ({dev.ip}:{port}): {exc}")
        return False


safety = SafetyEngine(registry, send_machine_command)
# React to strap falls / impacts the instant the packet arrives (same event loop, so safe)
registry.on_fall = safety._process_falls_and_acks
registry.on_impact = safety.on_impact


def local_ip_addresses() -> list[str]:
    """Best-effort list of this machine's LAN IPv4 addresses (for diagnostics)."""
    ips = set()
    try:
        # Connecting a UDP socket sends nothing; it just picks the outgoing interface.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            ips.add(s.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    return sorted(ip for ip in ips if not ip.startswith("127."))


def network_status(now: float) -> dict:
    return {
        "udp": udp_stats.to_dict(now),
        "http_port": config.HTTP_PORT,
        "server_ips": local_ip_addresses(),
        "websocket_clients": len(ws_clients),
    }


def send_worker_proximity(now: float) -> None:
    """Tell each online worker strap what every machine's ultrasonic sensor sees,
    so the strap can colour its light, turn on its "close to a machine" LED, and
    apply its red-zone fall rule. -1 = nothing in range / no reading."""
    machines = {}
    for m in safety.machine_ids:
        d = safety.detection_cm(m, now)
        machines[m] = -1 if d is None else int(d)
    for w in safety.worker_ids:
        dev = registry.devices.get(w)
        # Also while it looks offline (heard in the last 2 min): the strap learns our address
        # from these replies and switches from lossy broadcast to direct packets.
        recent = dev and dev.last_seen is not None and now - dev.last_seen < 120
        if recent and dev.ip and dev.last_message_type in ("status", "fall_alert", "impact", "worker_ok"):
            send_machine_command(w, {"type": "proximity", "worker": w, "machines": machines})


def build_state() -> dict:
    now = time.time()
    site = safety.update()   # NearStop: sensors, zones, machine states, alerts
    send_worker_proximity(now)
    return {
        "server_time": now,
        "room": site["room"],
        "devices": registry.snapshot(now),
        "site": site,
        "site_config": safety.cfg,
        "network": network_status(now),
        "simulator": simulator.status(),
    }


async def broadcast_loop() -> None:
    """Recompute state and push it to every connected dashboard."""
    global latest_state
    interval = 1.0 / config.BROADCAST_HZ
    while True:
        try:
            latest_state = build_state()
            dead = []
            for ws in list(ws_clients):
                try:
                    await asyncio.wait_for(ws.send_json(latest_state), timeout=1.0)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                ws_clients.discard(ws)
        except Exception as exc:  # keep the loop alive no matter what
            print(f"[broadcast] error: {exc!r}")
        await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global udp_transport
    udp_transport = await start_udp_receiver(registry, udp_stats)
    task = asyncio.create_task(broadcast_loop())
    ips = ", ".join(local_ip_addresses()) or "unknown"
    print(f"[main] dashboard: http://<this-machine-ip>:{config.HTTP_PORT}  (this machine: {ips})")
    yield
    task.cancel()
    simulator.stop()
    serial_mon.close()
    if udp_transport is not None:
        udp_transport.close()


app = FastAPI(title="NearStop", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------
class SimulatorRequest(BaseModel):
    running: bool
    scenario: str = "loop"          # "loop" (visit each machine) or "demo" (scripted safety demo)
    noise: float = Field(default=2.0, ge=0, le=30)   # distance jitter, cm
    flaky: list[str] = []
    junk: bool = False


class WorkerRequest(BaseModel):
    worker_id: str


class CheckinResolve(BaseModel):
    worker_id: str
    outcome: str            # "ok" or "escalate"


class EmergencyResolve(BaseModel):
    worker_id: str | None = None   # None = resolve all emergencies


class SerialOpen(BaseModel):
    port: str
    baud: int = Field(default=115200, ge=300, le=2000000)


class EvacuateRequest(BaseModel):
    active: bool


class MachineLayout(BaseModel):
    x: float
    y: float
    facing_deg: float = Field(ge=-360, le=720)
    warning_cm: int = Field(ge=20, le=400)
    danger_cm: int = Field(ge=10, le=395)


class SiteLayout(BaseModel):
    width_m: float = Field(gt=0.5, le=500)
    height_m: float = Field(gt=0.5, le=500)
    machines: dict[str, MachineLayout]


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
@app.get("/api/state")
async def get_state():
    return latest_state or build_state()


@app.get("/api/devices")
def get_devices():
    return registry.snapshot(time.time())


@app.get("/api/site-config")
async def get_site_config():
    return safety.cfg


@app.post("/api/site-config")
async def set_site_config(layout: SiteLayout):
    """Machine positions, facing angles and warning/danger distances (Setup page)."""
    cfg = json.loads(json.dumps(safety.cfg))
    unknown = set(layout.machines) - set(cfg["machines"])
    if unknown:
        raise HTTPException(400, f"Unknown machine id(s): {sorted(unknown)}")
    for mid, m in layout.machines.items():
        name = cfg["machines"][mid]["name"]
        if not (0 <= m.x <= layout.width_m and 0 <= m.y <= layout.height_m):
            raise HTTPException(400, f"{name} at ({m.x}, {m.y}) is outside the site")
        if m.danger_cm >= m.warning_cm:
            raise HTTPException(400, f"{name}: danger distance must be smaller than warning distance")
        cfg["machines"][mid].update(x=round(m.x, 2), y=round(m.y, 2), facing_deg=round(m.facing_deg % 360, 1),
                                    warning_cm=m.warning_cm, danger_cm=m.danger_cm)
    for mid, m in cfg["machines"].items():      # machines not in the request must still fit
        if not (0 <= m["x"] <= layout.width_m and 0 <= m["y"] <= layout.height_m):
            raise HTTPException(400, f"Site too small: {m['name']} would be outside it")
    cfg["site"] = {"width_m": round(layout.width_m, 2), "height_m": round(layout.height_m, 2)}
    cfg = site_config.normalise(cfg)
    site_config.save(cfg)
    safety.apply_config(cfg)
    return cfg


@app.get("/api/network")
def get_network():
    return network_status(time.time())


@app.get("/api/logs")
def get_logs(since: int = 0):
    """Log lines newer than the given id (the dashboard polls this)."""
    return app_log.since(since)


@app.get("/api/simulator")
def get_simulator():
    return simulator.status()


@app.post("/api/simulator")
def set_simulator(req: SimulatorRequest):
    """Start/stop the TEST simulator. Real boards never need this."""
    if req.running:
        if req.scenario not in ("loop", "demo", "gas"):
            raise HTTPException(400, "scenario must be 'loop', 'demo' or 'gas'")
        return simulator.start(noise=req.noise, flaky=req.flaky, junk=req.junk, scenario=req.scenario)
    return simulator.stop()


# ---------------------------------------------------------------------------
# NearStop safety actions
# ---------------------------------------------------------------------------
def _result(ok_msg: tuple[bool, str]):
    ok, msg = ok_msg
    if not ok:
        raise HTTPException(409, msg)
    return {"ok": True}


@app.get("/api/site")
async def get_site():
    return (latest_state or build_state())["site"]


@app.get("/api/workers/{worker_id}/environment")
async def worker_environment(worker_id: str):
    """Last 10 minutes of a worker's strap temperature / air pressure / gas readings."""
    if worker_id not in safety.worker_ids:
        raise HTTPException(404, f"Unknown worker {worker_id}")
    return {"worker_id": worker_id, "history": registry.env_history(worker_id),
            "thresholds": safety.cfg["environment"]}


@app.get("/api/events")
async def get_events(since: int = 0):
    return safety.events_since(since)


@app.post("/api/checkin")
async def checkin(req: WorkerRequest):
    """Supervisor pressed "Check on": ask the worker to respond."""
    return _result(safety.start_checkin(req.worker_id, "manual"))


@app.post("/api/checkin/resolve")
async def checkin_resolve(req: CheckinResolve):
    return _result(safety.resolve_checkin(req.worker_id, req.outcome))


@app.post("/api/emergency/resolve")
async def emergency_resolve(req: EmergencyResolve):
    return _result(safety.resolve_emergency(req.worker_id))


@app.post("/api/evacuate")
async def evacuate(req: EvacuateRequest):
    return _result(safety.set_evacuation(req.active))


@app.post("/api/machines/{machine_id}/restart")
async def machine_restart(machine_id: str):
    return _result(safety.approve_restart(machine_id))


# ---------------------------------------------------------------------------
# USB serial monitor (diagnostics only - boards send data over Wi-Fi)
# ---------------------------------------------------------------------------
@app.get("/api/serial")
def serial_status():
    return serial_mon.status()


@app.post("/api/serial/open")
def serial_open(req: SerialOpen):
    ok, msg = serial_mon.open(req.port, req.baud)
    if not ok:
        raise HTTPException(409, msg)
    return serial_mon.status()


@app.post("/api/serial/close")
def serial_close():
    serial_mon.close()
    return serial_mon.status()


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    ws_clients.add(ws)
    try:
        await ws.send_json(latest_state or build_state())
        while True:
            await ws.receive_text()   # we don't expect messages; this detects disconnects
    except WebSocketDisconnect:
        pass
    finally:
        ws_clients.discard(ws)


# Dashboard files. Mounted last so the /api and /ws routes take priority.
app.mount("/", StaticFiles(directory=config.FRONTEND_DIR, html=True), name="frontend")


if __name__ == "__main__":
    uvicorn.run(app, host=config.HTTP_HOST, port=config.HTTP_PORT, log_level="info",
                access_log=False)   # don't log every HTTP request
