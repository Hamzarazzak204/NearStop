"""
NAT Basestation desktop app.

Double-click "NAT Basestation.bat" (or the desktop shortcut) to run this.
It starts the backend (UDP receiver + web server) inside this process and
shows the dashboard in its own window. Closing the window stops everything.

The Raspberry Pi does not need this file: there you run backend/main.py and
open the dashboard in a browser. Both use exactly the same backend code.
"""

import os
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"


def _popup(title: str, text: str) -> None:
    """A plain Windows message box (works without any installed packages)."""
    try:
        import tkinter
        from tkinter import messagebox
        root = tkinter.Tk()
        root.withdraw()
        messagebox.showerror(title, text)
        root.destroy()
    except Exception:
        print(f"{title}: {text}")


# Double-clicking desktop_app.py runs it with the computer's main Python, which
# doesn't have the backend's packages (FastAPI, uvicorn, pywebview). Restart it
# with this project's .venv instead, the same way "NAT Basestation.bat" does.
if Path(sys.prefix).resolve() != VENV.resolve() and not os.environ.get("NEARSTOP_RELAUNCHED"):
    venv_python = VENV / "Scripts" / "pythonw.exe"
    if venv_python.exists():
        subprocess.Popen([str(venv_python), str(Path(__file__).resolve())], cwd=str(ROOT),
                         env={**os.environ, "NEARSTOP_RELAUNCHED": "1"},
                         creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
        sys.exit(0)
    _popup("NearStop could not start",
           "The Python environment (.venv) isn't set up in this folder yet.\n\n"
           "Double-click \"NAT Basestation.bat\" once: it installs everything and starts NearStop.")
    sys.exit(1)

sys.path.insert(0, str(ROOT / "backend"))

# With pythonw.exe there is no console, so sys.stdout/stderr are None.
# Send output to logs/desktop.log instead so errors are never lost.
if sys.stdout is None or sys.stderr is None:
    (ROOT / "logs").mkdir(exist_ok=True)
    _logfile = open(ROOT / "logs" / "desktop.log", "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stdout or _logfile
    sys.stderr = sys.stderr or _logfile

import config  # noqa: E402

URL = f"http://127.0.0.1:{config.HTTP_PORT}"
WINDOW_TITLE = "NearStop"


def server_already_running() -> bool:
    try:
        with urllib.request.urlopen(URL + "/api/network", timeout=1):
            return True
    except Exception:
        return False


def show_error(message: str) -> None:
    """Show an error in a window if possible, otherwise just log it."""
    print(f"[desktop] ERROR: {message}")
    try:
        import webview
        html = (f"<body style='font-family:system-ui;padding:24px'><h2>NAT Basestation could not start</h2>"
                f"<pre style='white-space:pre-wrap'>{message}</pre>"
                f"<p>Details are in logs/desktop.log</p></body>")
        webview.create_window(WINDOW_TITLE, html=html, width=700, height=400)
        webview.start()
    except Exception:
        _popup("NAT Basestation could not start", message[-1500:] + "\n\nDetails are in logs/desktop.log")


def open_window() -> None:
    """Open the dashboard in a native window; fall back to the web browser."""
    try:
        import webview
    except ImportError:
        import webbrowser
        print("[desktop] pywebview not installed - opening the web browser instead")
        webbrowser.open(URL)
        # Keep the server alive until this process is closed.
        while True:
            time.sleep(1)
    webview.create_window(WINDOW_TITLE, URL, width=1400, height=900, min_size=(800, 600))
    webview.start()   # blocks until the window is closed


def main() -> None:
    if server_already_running():
        # Another copy (or `python backend/main.py`) is already running:
        # just open another window onto it.
        print("[desktop] backend already running - opening a window onto it")
        open_window()
        return

    try:
        import uvicorn
        import main as backend
    except Exception:
        show_error("Could not load the backend:\n\n" + traceback.format_exc())
        return

    server = uvicorn.Server(uvicorn.Config(
        backend.app, host=config.HTTP_HOST, port=config.HTTP_PORT,
        log_level="info", access_log=False,
    ))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Wait (max 10 s) for the web server to come up.
    deadline = time.time() + 10
    while not server.started and thread.is_alive() and time.time() < deadline:
        time.sleep(0.1)
    if not server.started:
        show_error(f"The web server did not start on port {config.HTTP_PORT}.\n"
                   "Is another program using that port?")
        server.should_exit = True
        return

    try:
        open_window()
    finally:
        print("[desktop] window closed - shutting down")
        server.should_exit = True
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
