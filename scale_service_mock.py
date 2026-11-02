#!/usr/bin/env python3
"""
scale_service_mock.py — Pure-HTTP Mock (no serial port / driver needed)
=======================================================================
Runs the same REST API as scale_service.py but generates simulated
weighbridge readings internally.  Perfect for:

  • Testing the SAP ABAP client without any hardware or COM drivers
  • Integration testing on a developer laptop or CI server
  • Demos and training sessions

Endpoints
---------
  GET /weight → simulated weight reading (same JSON contract)
  GET /health → { "status": "ok", "mode": "mock" }

The simulator runs a background thread that cycles through a realistic
vehicle lifecycle (tare → ramp-up → settle → stable → drive-off → pause)
exactly like mock_weighbridge.py, but entirely in memory.

Usage
-----
  python scale_service_mock.py              # default port 8080
  python scale_service_mock.py --port 9090  # custom port

Author : WeighScale Integration Team
License: MIT
"""

# ──────────────────────────────────────────────────────────────────────
#  CONFIGURATION
# ──────────────────────────────────────────────────────────────────────

HTTP_HOST      = "0.0.0.0"
HTTP_PORT      = 8080
API_KEY        = None          # Set to a string to enable X-API-Key auth
LOG_FILE       = "scale_service_mock.log"
LOG_LEVEL      = "INFO"

MIN_GROSS_KG   = 5_000
MAX_GROSS_KG   = 42_000
TARE_WEIGHT    = 50.0
LINE_INTERVAL  = 0.5          # Simulated time between readings

# ──────────────────────────────────────────────────────────────────────
#  IMPORTS
# ──────────────────────────────────────────────────────────────────────

import argparse
import random
import time
import threading
import logging
from datetime import datetime, timezone

from flask import Flask, jsonify, request

# ──────────────────────────────────────────────────────────────────────
#  LOGGING
# ──────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("scale_service_mock")

# ──────────────────────────────────────────────────────────────────────
#  THREAD-SAFE WEIGHT STORE (identical to scale_service.py)
# ──────────────────────────────────────────────────────────────────────

class WeightStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._reading: dict | None = None
        self._timestamp: str | None = None

    def update(self, reading: dict) -> None:
        with self._lock:
            self._reading = reading
            self._timestamp = datetime.now(timezone.utc).isoformat()

    def get(self) -> dict:
        with self._lock:
            if self._reading is None:
                return {
                    "weight": 0.0,
                    "unit":   "kg",
                    "stable": False,
                    "raw":    "",
                    "timestamp": None,
                    "error":  "No reading available yet",
                }
            return {**self._reading, "timestamp": self._timestamp}


weight_store = WeightStore()

# ──────────────────────────────────────────────────────────────────────
#  SIMULATED WEIGHBRIDGE LIFECYCLE (background thread)
# ──────────────────────────────────────────────────────────────────────

def _make_raw_line(weight: float, stable: bool, unit: str = "kg") -> str:
    """Build the raw ASCII line as the real scale would emit it."""
    flag = "ST" if stable else "US"
    sign = "+" if weight >= 0 else "-"
    return f"{flag},GS,{sign}{abs(weight):013.2f},{unit}"


def _post(weight: float, stable: bool):
    """Update the store with a simulated reading."""
    raw = _make_raw_line(weight, stable)
    weight_store.update({
        "weight": round(weight, 2),
        "unit":   "kg",
        "stable": stable,
        "raw":    raw,
    })
    logger.debug("SIM: %s", raw)


def _simulate_vehicle():
    """Run one full vehicle drive-on → stable → drive-off cycle."""
    target = random.uniform(MIN_GROSS_KG, MAX_GROSS_KG)
    logger.info("=== Mock vehicle — target %.2f kg ===", target)

    # Phase 1: tare / empty
    for _ in range(random.randint(3, 5)):
        _post(TARE_WEIGHT + random.gauss(0, 2), stable=True)
        time.sleep(LINE_INTERVAL)

    # Phase 2: ramp-up (unstable)
    current = TARE_WEIGHT
    steps = random.randint(8, 15)
    inc = (target - current) / steps
    for _ in range(steps):
        current += inc + random.gauss(0, inc * 0.3)
        current = max(current, 0)
        _post(current, stable=False)
        time.sleep(LINE_INTERVAL)

    # Phase 3: settling (unstable)
    for _ in range(random.randint(5, 10)):
        _post(target + random.gauss(0, target * 0.002), stable=False)
        time.sleep(LINE_INTERVAL)

    # Phase 4: stable reading
    stable_w = round(target, 2)
    for _ in range(random.randint(5, 8)):
        _post(stable_w, stable=True)
        time.sleep(LINE_INTERVAL)

    # Phase 5: drive-off (unstable)
    current = stable_w
    steps = random.randint(6, 12)
    dec = current / steps
    for _ in range(steps):
        current -= dec + random.gauss(0, dec * 0.3)
        current = max(current, 0)
        _post(current, stable=False)
        time.sleep(LINE_INTERVAL)

    # Phase 6: pause
    pause = random.uniform(3, 8)
    logger.info("Pause %.1f s …", pause)
    time.sleep(pause)


def _simulator_loop(stop_event: threading.Event):
    """Endless loop cycling through vehicles."""
    while not stop_event.is_set():
        _simulate_vehicle()

# ──────────────────────────────────────────────────────────────────────
#  FLASK APP
# ──────────────────────────────────────────────────────────────────────

app = Flask(__name__)


def _check_api_key():
    if API_KEY:
        if request.headers.get("X-API-Key", "") != API_KEY:
            return jsonify({"error": "Unauthorized"}), 401
    return None


@app.before_request
def before_request():
    auth = _check_api_key()
    if auth is not None:
        return auth


@app.route("/weight", methods=["GET"])
def get_weight():
    """Return the latest simulated weight reading."""
    return jsonify(weight_store.get())


@app.route("/health", methods=["GET"])
def health():
    """Health check — includes mode indicator so callers know this is a mock."""
    return jsonify({"status": "ok", "mode": "mock"})

# ──────────────────────────────────────────────────────────────────────
#  MAIN
# ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Scale service MOCK (no serial port)")
    parser.add_argument("--port", type=int, default=HTTP_PORT, help="HTTP port (default: %(default)s)")
    parser.add_argument("--host", default=HTTP_HOST, help="Bind address (default: %(default)s)")
    args = parser.parse_args()

    stop_event = threading.Event()

    sim_thread = threading.Thread(
        target=_simulator_loop, args=(stop_event,), daemon=True, name="mock-simulator"
    )
    sim_thread.start()
    logger.info("Mock simulator thread started.")

    logger.info("Starting mock scale service on %s:%d …", args.host, args.port)

    from waitress import serve
    try:
        serve(app, host=args.host, port=args.port)
    except KeyboardInterrupt:
        stop_event.set()
        logger.info("Mock scale service shut down.")


if __name__ == "__main__":
    main()
