"""
USB serial monitor (diagnostics only).

The boards talk to NearStop over Wi-Fi; USB is NOT used for data. This just
lets the Setup page show a plugged-in board's Serial Monitor output (boot
messages, Wi-Fi status, ESP-NOW counters) in the Log panel, like the Arduino
IDE's Serial Monitor. Close the Arduino Serial Monitor first: only one program
can open a port at a time.
"""

import threading
import time

import app_log

try:
    import serial
    from serial.tools import list_ports
    AVAILABLE = True
except ImportError:          # pyserial not installed
    serial = None
    AVAILABLE = False

# USB chips used by ESP32 boards (VID): CH9102/CH340, CP210x, Espressif native, FTDI
ESP32_USB_VIDS = {0x1A86: "WCH (CH9102/CH340)", 0x10C4: "Silicon Labs CP210x", 0x303A: "Espressif", 0x0403: "FTDI"}


class SerialMonitor:
    def __init__(self) -> None:
        self.port = None            # serial.Serial when open
        self.port_name: str | None = None
        self.opened_at: float | None = None
        self.lines = 0
        self.error: str | None = None
        self._stop = threading.Event()

    def list_ports(self) -> list[dict]:
        if not AVAILABLE:
            return []
        out = []
        for p in list_ports.comports():
            chip = ESP32_USB_VIDS.get(p.vid) if p.vid else None
            out.append({"device": p.device, "description": p.description,
                        "likely_esp32": bool(chip), "chip": chip})
        return sorted(out, key=lambda x: (not x["likely_esp32"], x["device"]))

    def status(self) -> dict:
        return {
            "available": AVAILABLE,
            "open": self.port is not None,
            "port": self.port_name,
            "lines": self.lines,
            "seconds_open": None if self.opened_at is None else round(time.time() - self.opened_at),
            "error": self.error,
            "ports": self.list_ports(),
        }

    def open(self, port_name: str, baud: int = 115200) -> tuple[bool, str]:
        if not AVAILABLE:
            return False, "pyserial is not installed (pip install pyserial)"
        self.close()
        try:
            ser = serial.Serial()
            ser.port, ser.baudrate, ser.timeout = port_name, baud, 0.2
            # Keep DTR/RTS low so opening the port does NOT reset the board
            ser.dtr = False
            ser.rts = False
            ser.open()
        except Exception as exc:   # port busy, unplugged, no permission...
            self.error = f"{port_name}: {exc}"
            return False, f"Could not open {port_name} (is the Arduino Serial Monitor open?)"
        self.port, self.port_name, self.opened_at, self.lines, self.error = ser, port_name, time.time(), 0, None
        self._stop.clear()
        threading.Thread(target=self._reader, args=(ser, port_name), daemon=True).start()
        print(f"[serial] opened {port_name} at {baud} baud (output appears in this log)")
        return True, "ok"

    def close(self) -> None:
        if self.port is not None:
            self._stop.set()
            try:
                self.port.close()
            except Exception:
                pass
            print(f"[serial] closed {self.port_name}")
        self.port = None
        self.opened_at = None

    def _reader(self, ser, name: str) -> None:
        buf = b""
        while not self._stop.is_set():
            try:
                buf += ser.read(512)
            except Exception as exc:     # board unplugged
                self.error = f"{name}: {exc}"
                app_log.add(f"[serial {name}] disconnected: {exc}")
                self.port = None
                self.opened_at = None
                return
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                text = line.decode("utf-8", "replace").rstrip()
                if text:
                    self.lines += 1
                    app_log.add(f"[serial {name}] {text}")
