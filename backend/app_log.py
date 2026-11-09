"""
Keeps the most recent log lines in memory so the dashboard can show them
(Tools tab -> Log), while still printing them to the terminal.

install() replaces sys.stdout / sys.stderr with a "tee" that writes to the
original stream AND to the in-memory buffer. All existing print() calls and
uvicorn's own log messages therefore end up in the dashboard automatically.
"""

import sys
import threading
import time
from collections import deque

MAX_LINES = 500

_lines: deque = deque(maxlen=MAX_LINES)   # (id, time, text)
_lock = threading.Lock()
_next_id = 1


def add(text: str) -> None:
    """Store one log line (also used for the simulator's output)."""
    global _next_id
    text = text.rstrip()
    if not text:
        return
    with _lock:
        _lines.append((_next_id, time.time(), text))
        _next_id += 1


def since(last_id: int) -> list[dict]:
    """Lines with an id greater than last_id."""
    with _lock:
        return [{"id": i, "time": t, "text": s} for (i, t, s) in _lines if i > last_id]


class _Tee:
    def __init__(self, original) -> None:
        self.original = original   # may be None when run with pythonw (no console)
        self._partial = ""

    def write(self, data: str) -> int:
        if self.original is not None:
            try:
                self.original.write(data)
            except Exception:
                pass
        self._partial += data
        while "\n" in self._partial:
            line, self._partial = self._partial.split("\n", 1)
            add(line)
        return len(data)

    def flush(self) -> None:
        if self.original is not None:
            try:
                self.original.flush()
            except Exception:
                pass

    def isatty(self) -> bool:
        return False

    @property
    def encoding(self) -> str:
        return getattr(self.original, "encoding", None) or "utf-8"


_installed = False


def install() -> None:
    global _installed
    if _installed:
        return
    _installed = True
    sys.stdout = _Tee(sys.stdout)
    sys.stderr = _Tee(sys.stderr)
