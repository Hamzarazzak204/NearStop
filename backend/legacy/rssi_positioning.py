"""
RSSI-based positioning for the worker tag.

Pipeline for each worker, run ~5 times per second:
  1. For each anchor take the fresh RSSI samples (newer than RSSI_MAX_AGE_S).
  2. Moving median of those samples  -> filtered RSSI.
  3. Path-loss model                  -> estimated distance to the anchor.
  4. Weighted least-squares trilateration with all three anchors -> raw (x, y).
  5. Exponential moving average       -> smoothed (x, y) shown on the map.

RSSI is a noisy proxy for distance (walls, bodies, antenna orientation and
reflections all change it), so the result is a ROUGH estimate. Every
position comes with an uncertainty radius, and no position is produced
unless all three anchors have fresh measurements.
"""

import json
import math
import time

import numpy as np

import config


# ---------------------------------------------------------------------------
# Settings that the dashboard can change (persisted to settings.json)
# ---------------------------------------------------------------------------
class PositioningSettings:
    def __init__(self) -> None:
        self.room = dict(config.DEFAULT_ROOM)
        self.anchors = {k: dict(v) for k, v in config.DEFAULT_ANCHORS.items()}
        self.reference_rssi = config.DEFAULT_REFERENCE_RSSI
        self.path_loss_exponent = config.DEFAULT_PATH_LOSS_EXPONENT
        self.load()

    def to_dict(self) -> dict:
        return {
            "room": self.room,
            "anchors": self.anchors,
            "reference_rssi": self.reference_rssi,
            "path_loss_exponent": self.path_loss_exponent,
        }

    def load(self) -> None:
        path = config.SETTINGS_FILE
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text())
            self.room.update({k: float(v) for k, v in data.get("room", {}).items()
                              if k in ("width", "height")})
            for aid, pos in data.get("anchors", {}).items():
                if aid in self.anchors:
                    self.anchors[aid] = {"x": float(pos["x"]), "y": float(pos["y"])}
            self.reference_rssi = float(data.get("reference_rssi", self.reference_rssi))
            self.path_loss_exponent = float(data.get("path_loss_exponent", self.path_loss_exponent))
            print(f"[settings] loaded {path}")
        except (ValueError, KeyError, TypeError, OSError) as exc:
            print(f"[settings] could not read {path} ({exc}); using defaults")

    def save(self) -> None:
        try:
            config.SETTINGS_FILE.write_text(json.dumps(self.to_dict(), indent=2))
            print(f"[settings] saved {config.SETTINGS_FILE}")
        except OSError as exc:
            print(f"[settings] could not save settings: {exc}")


# ---------------------------------------------------------------------------
# Maths helpers
# ---------------------------------------------------------------------------
def rssi_to_distance(rssi: float, reference_rssi: float, path_loss_exponent: float) -> float:
    """Log-distance path-loss model. Returns metres."""
    return 10 ** ((reference_rssi - rssi) / (10 * path_loss_exponent))


def triangle_area(points: np.ndarray) -> float:
    (x1, y1), (x2, y2), (x3, y3) = points[:3]
    return float(abs((x2 - x1) * (y3 - y1) - (x3 - x1) * (y2 - y1)) / 2)


def trilaterate(anchors: np.ndarray, distances: np.ndarray) -> tuple[np.ndarray, float]:
    """
    Least-squares position from >= 3 anchors and distance estimates.

    anchors:   shape (n, 2) anchor coordinates
    distances: shape (n,)   estimated distances

    Returns (position, rms_residual_m). The residual says how well the
    distance circles agree with each other: large residual = inconsistent RSSI.
    """
    # Step 1: linearised solution as the starting point. Subtracting the
    # first circle equation from the others gives a linear system A p = b.
    x0, y0 = anchors[0]
    d0 = distances[0]
    A = 2 * (anchors[1:] - anchors[0])
    b = (d0 ** 2 - distances[1:] ** 2
         + np.sum(anchors[1:] ** 2, axis=1) - (x0 ** 2 + y0 ** 2))
    try:
        p, *_ = np.linalg.lstsq(A, b, rcond=None)
    except np.linalg.LinAlgError:
        p = anchors.mean(axis=0)

    # Step 2: refine with Gauss-Newton on the real (non-linear) residuals
    #   r_i = |p - a_i| - d_i
    # Far anchors are less trustworthy (RSSI error grows with distance), so
    # each residual is weighted by 1/d_i.
    w = 1.0 / np.maximum(distances, 0.5)
    for _ in range(20):
        diff = p - anchors
        ranges = np.maximum(np.linalg.norm(diff, axis=1), 1e-6)
        r = ranges - distances
        J = diff / ranges[:, None]
        Jw = J * w[:, None]
        rw = r * w
        try:
            step, *_ = np.linalg.lstsq(Jw, -rw, rcond=None)
        except np.linalg.LinAlgError:
            break
        p = p + step
        if np.linalg.norm(step) < 1e-4:
            break

    residuals = np.linalg.norm(p - anchors, axis=1) - distances
    rms = float(np.sqrt(np.mean(residuals ** 2)))
    return p, rms


# ---------------------------------------------------------------------------
# Positioning engine
# ---------------------------------------------------------------------------
class PositioningEngine:
    def __init__(self, registry, settings: PositioningSettings) -> None:
        self.registry = registry
        self.settings = settings
        # Smoothed position per worker: {"x", "y", "t"}
        self._ema: dict[str, dict] = {}

    def anchor_triangle_ok(self) -> bool:
        pts = np.array([[self.settings.anchors[a]["x"], self.settings.anchors[a]["y"]]
                        for a in config.ANCHOR_IDS])
        # bool() because numpy's bool type cannot be converted to JSON
        return bool(triangle_area(pts) >= config.MIN_ANCHOR_TRIANGLE_AREA_M2)

    def compute(self) -> dict:
        """Compute per-anchor measurements and a position for each worker."""
        now = time.time()
        s = self.settings
        results = {}
        for worker_id in config.WORKER_IDS:
            measurements = {}
            usable = []   # (anchor_xy, distance)
            for anchor_id in config.ANCHOR_IDS:
                fresh = self.registry.fresh_rssi(anchor_id, worker_id, now)
                latest = self.registry.latest_rssi(anchor_id, worker_id)
                m = {
                    "rssi_latest": None if latest is None else latest[1],
                    "age_s": None if latest is None else round(now - latest[0], 1),
                    "samples": len(fresh),
                    "fresh": len(fresh) > 0,
                    "rssi_filtered": None,
                    "distance_m": None,
                }
                if fresh:
                    rssi_med = float(np.median(fresh))
                    dist = rssi_to_distance(rssi_med, s.reference_rssi, s.path_loss_exponent)
                    m["rssi_filtered"] = round(rssi_med, 1)
                    m["distance_m"] = round(dist, 2)
                    a = s.anchors[anchor_id]
                    usable.append(((a["x"], a["y"]), dist))
                measurements[anchor_id] = m

            results[worker_id] = {
                "measurements": measurements,
                "position": self._estimate(worker_id, usable, now),
            }
        return results

    def _estimate(self, worker_id: str, usable: list, now: float) -> dict:
        n = len(usable)
        unavailable = {
            "available": False, "x": None, "y": None, "raw_x": None, "raw_y": None,
            "uncertainty_m": None, "residual_m": None,
            "anchors_used": n, "confidence": "none",
        }
        if not self.anchor_triangle_ok():
            return {**unavailable, "reason": "Anchors are (nearly) in a straight line - move one"}
        if n < 3:
            # With only 2 circles there are two possible points; with 1 there
            # are infinitely many. Do not guess.
            return {**unavailable, "reason": f"Need fresh RSSI from 3 anchors, have {n}"}

        anchors = np.array([u[0] for u in usable], dtype=float)
        distances = np.array([u[1] for u in usable], dtype=float)
        p, rms = trilaterate(anchors, distances)

        # Keep the estimate inside the room (RSSI noise can push it outside).
        w, h = self.settings.room["width"], self.settings.room["height"]
        clamped = bool(not (0 <= p[0] <= w and 0 <= p[1] <= h))
        x = float(min(max(p[0], 0.0), w))
        y = float(min(max(p[1], 0.0), h))

        # Uncertainty: a deliberately simple, rough indicator.
        #   - an RSSI error of +/-sigma dB scales distance by 10^(sigma/(10n)),
        #     so distance error grows with distance;
        #   - plus how badly the three circles disagree (rms residual).
        rel = 10 ** (config.ASSUMED_RSSI_NOISE_DB / (10 * self.settings.path_loss_exponent)) - 1
        dist_sigma = rel * distances
        uncertainty = math.sqrt(rms ** 2 + float(np.mean(dist_sigma ** 2)))
        uncertainty = max(uncertainty, config.MIN_UNCERTAINTY_M)

        confidence = "low" if (clamped or rms > 1.5) else "medium"

        # Exponential moving average for a calmer display.
        prev = self._ema.get(worker_id)
        a = config.POSITION_EMA_ALPHA
        if prev is None or now - prev["t"] > config.EMA_RESET_AFTER_S:
            sx, sy = x, y
        else:
            sx = a * x + (1 - a) * prev["x"]
            sy = a * y + (1 - a) * prev["y"]
        self._ema[worker_id] = {"x": sx, "y": sy, "t": now}

        return {
            "available": True,
            "x": round(sx, 2), "y": round(sy, 2),
            "raw_x": round(x, 2), "raw_y": round(y, 2),
            "uncertainty_m": round(uncertainty, 2),
            "residual_m": round(rms, 2),
            "anchors_used": n,
            "confidence": confidence,
            "clamped": clamped,
            "reason": "Estimated outside the room, clamped to the wall" if clamped else None,
        }
