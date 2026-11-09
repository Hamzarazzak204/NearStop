"""
USB <-> NearStop bridge, for machine nodes connected by USB cable instead of Wi-Fi
(for example boards without an antenna). Firmware: firmware/machine_node_usb.

For every USB-connected board this script:
  * reads the JSON lines the board prints ({"type":"machine",...}) and sends each
    one to NearStop as a UDP packet (to 127.0.0.1:5005), from a socket that
    belongs to that board only;
  * receives NearStop's replies on that same socket (set_zones, command) and
    writes them to the board as one JSON line.
NearStop replies to the address a machine's reports come from, so the backend,
the message formats and the dashboard work exactly as with Wi-Fi nodes.

Run (start NearStop first):
    .venv\\Scripts\\python.exe tools\\usb_bridge.py              # every USB board
    .venv\\Scripts\\python.exe tools\\usb_bridge.py --port COM5  # one board
Close it before uploading firmware: it holds the COM ports.
"""

import argparse
import json
import socket
import sys
import threading
import time

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    sys.exit("pyserial is missing: run  .venv\\Scripts\\python.exe -m pip install pyserial")

FORWARD_TYPES = {"machine", "heartbeat", "checkin_ack",
                 "status", "fall_alert", "impact", "worker_ok"}   # status ... worker_ok = worker strap


def board_id(msg: dict):
    """Machines/vests use "id"; the worker strap uses "worker" ("id" is its alert number)."""
    if isinstance(msg.get("id"), str):
        return msg["id"]
    return msg.get("worker") if isinstance(msg.get("worker"), str) else None
RESCAN_S = 3.0


class BoardLink:
    """One COM port <-> one UDP socket."""

    def __init__(self, port: str, target: tuple[str, int], stop: threading.Event):
        self.port, self.target, self.stop = port, target, stop
        self.device_id = None
        self.alive = True
        self.sent = 0
        self.ser = None
        self.write_lock = threading.Lock()   # commands and pings share the serial port
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Bind on the interface that reaches NearStop, so its replies come back here.
        self.sock.bind(("127.0.0.1" if target[0] in ("127.0.0.1", "localhost") else "0.0.0.0", 0))
        self.sock.settimeout(0.5)
        # Windows: when NearStop restarts, a send to its closed port makes the NEXT
        # recvfrom raise ConnectionResetError (WSAECONNRESET), which used to end the
        # NearStop -> board thread silently (stop/lock commands never arrived again).
        if hasattr(socket, "SIO_UDP_CONNRESET"):
            try:
                self.sock.ioctl(socket.SIO_UDP_CONNRESET, False)
            except OSError:
                pass

    def start(self) -> None:
        try:
            ser = serial.Serial()
            ser.port, ser.baudrate, ser.timeout = self.port, 115200, 0.5
            ser.dtr = False          # don't reset the board when opening the port
            ser.rts = False
            ser.open()
            self.ser = ser
        except serial.SerialException as exc:
            print(f"[bridge] {self.port}: cannot open ({exc}). Close the Arduino IDE / Serial Monitor.")
            self.alive = False
            self.sock.close()
            return
        print(f"[bridge] {self.port}: connected (UDP port {self.sock.getsockname()[1]})")
        threading.Thread(target=self._board_to_nearstop, daemon=True).start()
        threading.Thread(target=self._nearstop_to_board, daemon=True).start()
        threading.Thread(target=self._announce, daemon=True).start()

    def _write(self, data: bytes) -> bool:
        try:
            with self.write_lock:
                self.ser.write(data)
            return True
        except (serial.SerialException, OSError):
            return False

    def _announce(self) -> None:
        """Tell the board once a second that a bridge is listening, so it reports
        over USB. Without these pings (board on other power) it uses Wi-Fi."""
        while self.alive and not self.stop.is_set():
            if not self._write(b'{"type":"bridge"}\n'):
                break
            self.stop.wait(1.0)

    def _board_to_nearstop(self) -> None:
        try:
            while not self.stop.is_set():
                raw = self.ser.readline()
                if not raw:
                    continue
                text = raw.decode("utf-8", "replace").strip()
                if not text.startswith("{"):
                    # "#W {...}" = a report the board sent over Wi-Fi (not forwarded, not printed)
                    if text and not text.startswith("#W "):
                        print(f"[{self.device_id or self.port}] {text.lstrip('# ')}")
                    continue
                try:
                    msg = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if not (isinstance(msg, dict) and msg.get("type") in FORWARD_TYPES and board_id(msg)):
                    continue
                if self.device_id != board_id(msg):
                    self.device_id = board_id(msg)
                    print(f"[bridge] {self.port}: board is {self.device_id}")
                try:
                    self.sock.sendto(text.encode(), self.target)
                    self.sent += 1
                except ConnectionResetError:
                    pass          # NearStop not running right now; keep going
        except (serial.SerialException, OSError) as exc:
            print(f"[bridge] {self.port}: disconnected ({exc.__class__.__name__})")
        finally:
            self.alive = False
            for closer in (self.ser.close, self.sock.close):
                try:
                    closer()
                except Exception:
                    pass

    def _nearstop_to_board(self) -> None:
        while self.alive and not self.stop.is_set():
            try:
                data, _ = self.sock.recvfrom(2048)
            except socket.timeout:
                continue
            except ConnectionResetError:
                continue          # NearStop restarted (Windows UDP quirk): keep listening
            except OSError:
                if not self.alive:
                    break
                time.sleep(0.2)   # transient socket error: keep the command path alive
                continue
            line = data.decode("utf-8", "replace").strip()
            try:
                out = json.dumps(json.loads(line), separators=(",", ":"))
            except json.JSONDecodeError:
                if not line.startswith(("ACK", "OKACK")):   # strap: fall alerts "ACK <id>", I'm OK "OKACK <id>"
                    continue
                out = line
            if not out.startswith('{"type":"proximity"'):   # 5/s to the strap: too chatty to print
                print(f"[bridge] NearStop -> {self.device_id or self.port}: {out}")
            if not self._write(out.encode() + b"\n"):
                break


def usb_ports() -> list[str]:
    return [p.device for p in list_ports.comports() if p.vid is not None]


def main() -> None:
    ap = argparse.ArgumentParser(description="Bridge USB-connected NearStop machine nodes to the backend")
    ap.add_argument("--port", action="append", help="COM port (repeatable). Default: every USB serial port")
    ap.add_argument("--host", default="127.0.0.1", help="NearStop backend address (default 127.0.0.1)")
    ap.add_argument("--udp-port", type=int, default=5005)
    args = ap.parse_args()
    # Boards print characters the Windows console can't show (e.g. the curly ' in
    # "Rafat's iPhone"): show "?" instead of crashing the reader thread.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    target = (args.host, args.udp_port)
    stop = threading.Event()
    links: dict[str, BoardLink] = {}
    print(f"[bridge] NearStop at UDP {target[0]}:{target[1]}  (Ctrl+C or close this window to stop)")
    warned = False
    try:
        while True:
            ports = args.port or usb_ports()
            for port in ports:
                link = links.get(port)
                if link is None or not link.alive:
                    link = BoardLink(port, target, stop)
                    links[port] = link
                    link.start()
            if not ports and not warned:
                print("[bridge] no USB boards found yet; plug one in")
            warned = not ports
            time.sleep(RESCAN_S)
    except KeyboardInterrupt:
        print("\n[bridge] stopping")
        stop.set()


if __name__ == "__main__":
    main()
