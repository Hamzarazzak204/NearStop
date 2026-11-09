"""
Start/stop the test simulator (simulator.py) from the dashboard.

The simulator runs as a separate process, exactly as if you had started it
in a terminal, so it still sends real UDP packets to the receiver. Its
output is copied into the dashboard log.
"""

import subprocess
import sys
import threading
import time

import app_log
import config

SIMULATOR = config.BACKEND_DIR / "simulator.py"


class SimulatorControl:
    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self.options: dict = {}
        self.started_at: float | None = None

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def status(self) -> dict:
        return {
            "running": self.running(),
            "options": self.options if self.running() else {},
            "uptime_s": round(time.time() - self.started_at) if self.running() else None,
        }

    def start(self, noise: float = 3.0, flaky: list[str] | None = None, junk: bool = False,
              scenario: str = "loop") -> dict:
        self.stop()
        flaky = [d for d in (flaky or []) if d in config.EXPECTED_DEVICES]
        cmd = [sys.executable, "-u", str(SIMULATOR), "--noise", str(noise), "--scenario", scenario]
        if flaky:
            cmd += ["--flaky", *flaky]
        if junk:
            cmd.append("--junk")
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        self.proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, creationflags=flags,
        )
        self.options = {"noise": noise, "flaky": flaky, "junk": junk, "scenario": scenario}
        self.started_at = time.time()
        threading.Thread(target=self._pump_output, args=(self.proc,), daemon=True).start()
        print(f"[main] simulator started ({scenario}, noise {noise} dB, flaky {flaky or 'none'}, junk {junk})")
        return self.status()

    def stop(self) -> dict:
        if self.running():
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            print("[main] simulator stopped")
        self.proc = None
        return self.status()

    @staticmethod
    def _pump_output(proc: subprocess.Popen) -> None:
        # Runs in a thread: copy the simulator's output into the log buffer.
        for line in proc.stdout:
            app_log.add(line)
