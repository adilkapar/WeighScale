#!/usr/bin/env python3
"""
scale_service.py — Weighbridge Serial Reader + REST API
========================================================
Reads weight data continuously from a serial (COM) port connected to an
industrial weighbridge, keeps the latest reading in a thread-safe store,
and serves it over HTTP as JSON via Flask + Waitress.

Endpoints
---------
GET /weight  → latest weight reading (JSON)
GET /health  → service heartbeat

Architecture
------------
  ┌─────────────┐  RS-232/USB   ┌──────────────────┐  HTTP/JSON  ┌──────┐
  │ Weighbridge │──────────────▶│ This service      │◀───────────│ SAP  │
  └─────────────┘   COM port    │ (pyserial + Flask)│            └──────┘
                                └──────────────────┘

Author : WeighScale Integration Team
License: MIT
"""

# ──────────────────────────────────────────────────────────────────────
#  CONFIGURATION — change these for your environment
# ──────────────────────────────────────────────────────────────────────

# Serial port settings (RS-232 / USB-serial)
SERIAL_PORT  = "COM3"        # Windows COM port the scale is connected to
BAUD_RATE    = 9600          # Most industrial scales default to 9600
DATA_BITS    = 8             # 8 data bits (standard)
PARITY       = "N"           # N=None, E=Even, O=Odd
STOP_BITS    = 1             # 1 stop bit (standard)
SERIAL_TIMEOUT = 2           # Seconds to wait for a line before retrying

# HTTP / REST settings
HTTP_HOST = "0.0.0.0"       # Bind address (0.0.0.0 = all interfaces)
HTTP_PORT = 8080             # Port the REST API listens on

# API-key security (optional — set to None or "" to disable)
# When set, every request must include header  X-API-Key: <this value>
API_KEY = None               # e.g. "my-secret-key-change-me"

# Logging
LOG_FILE  = "scale_service.log"   # Path to the log file
LOG_LEVEL = "INFO"                # DEBUG, INFO, WARNING, ERROR

# Serial reconnect behaviour
RECONNECT_BASE_DELAY = 2    # Seconds before first reconnect attempt
RECONNECT_MAX_DELAY  = 60   # Maximum backoff delay in seconds

# ──────────────────────────────────────────────────────────────────────
#  IMPORTS
# ──────────────────────────────────────────────────────────────────────

import re
import time
import logging
import threading
from datetime import datetime, timezone

import serial                       # pyserial
from flask import Flask, jsonify, request

# ──────────────────────────────────────────────────────────────────────
#  LOGGING SETUP
# ──────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),          # also print to console
    ],
)
logger = logging.getLogger("scale_service")

# ──────────────────────────────────────────────────────────────────────
#  SCALE PROTOCOL PARSER
# ──────────────────────────────────────────────────────────────────────
#
#  The function below parses a single ASCII line produced by the scale.
#  The *default* format handled is the common Toledo / generic pattern:
#
#      ST,GS,+000012345.67,kg\r\n
#
#  Tokens:
#    ST | US   → stable / unstable
#    GS | NT   → gross / net weight (informational; we just read the number)
#    +/-NNN.NN → signed decimal weight value
#    kg | lb   → unit
#
#  ──── Adapting to other vendors ──────────────────────────────────────
#  • Mettler Toledo SICS: line looks like "S S     12.345 g"
#      → change the regex to r"(S|D)\s+(S|D)\s+([\d.]+)\s+(\w+)"
#        and map the first token: "S" = stable, "D" = dynamic.
#  • Avery Weigh-Tronix ZM / CSW: uses a fixed-width format like
#      "   12345 kg ST" — adapt the regex accordingly.
#  • Sartorius SBI / Combics: similar to Toledo; the stability flag is
#      at a fixed column position.
#
#  In every case, the function must return a dict with keys:
#      weight (float), unit (str), stable (bool), raw (str)
#  or None if the line is unparseable.
# ──────────────────────────────────────────────────────────────────────

# Regex for the default "ST,GS,+000012345.67,kg" format.
# Groups: 1=stability token  2=signed decimal  3=unit
_SCALE_LINE_RE = re.compile(
    r"(ST|US)"            # stability: ST = stable, US = unstable
    r","                  # separator
    r"[A-Z]{2}"          # weight type token (GS/NT) — captured but unused
    r","                  # separator
    r"([+-]?\d+\.?\d*)"  # signed decimal weight value
    r","                  # separator
    r"(\w+)"             # unit string (kg, lb, t …)
)


def parse_scale_line(raw_line: str) -> dict | None:
    """Parse one ASCII line from the weighbridge.

    Returns
    -------
    dict  with keys weight, unit, stable, raw   — on success
    None                                         — if the line cannot be parsed
    """
    line = raw_line.strip()
    if not line:
        return None

    m = _SCALE_LINE_RE.search(line)
    if not m:
        logger.debug("Unparseable line: %r", line)
        return None

    stability_token = m.group(1)
    weight_str      = m.group(2)
    unit            = m.group(3)

    try:
        weight = float(weight_str)
    except ValueError:
        logger.warning("Could not convert weight value %r to float", weight_str)
        return None

    return {
        "weight": weight,
        "unit":   unit,
        "stable": stability_token == "ST",
        "raw":    line,
    }

# ──────────────────────────────────────────────────────────────────────
#  THREAD-SAFE WEIGHT STORE
# ──────────────────────────────────────────────────────────────────────

class WeightStore:
    """Holds the latest parsed weight reading, protected by a lock."""

    def __init__(self):
        self._lock = threading.Lock()
        self._reading: dict | None = None
        self._timestamp: str | None = None

    def update(self, reading: dict) -> None:
        with self._lock:
            self._reading = reading
            self._timestamp = datetime.now(timezone.utc).isoformat()

    def get(self) -> dict:
        """Return the latest reading as a JSON-ready dict."""
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
#  SERIAL READER THREAD (with auto-reconnect & backoff)
# ──────────────────────────────────────────────────────────────────────

def _serial_reader_loop(stop_event: threading.Event) -> None:
    """Continuously read lines from the serial port and update the store.

    If the port errors out or disconnects, the loop backs off and retries.
    """
    delay = RECONNECT_BASE_DELAY

    parity_map = {"N": serial.PARITY_NONE, "E": serial.PARITY_EVEN, "O": serial.PARITY_ODD}

    while not stop_event.is_set():
        port = None
        try:
            logger.info("Opening serial port %s at %d baud …", SERIAL_PORT, BAUD_RATE)
            port = serial.Serial(
                port=SERIAL_PORT,
                baudrate=BAUD_RATE,
                bytesize=DATA_BITS,
                parity=parity_map.get(PARITY, serial.PARITY_NONE),
                stopbits=STOP_BITS,
                timeout=SERIAL_TIMEOUT,
            )
            logger.info("Serial port %s opened successfully.", SERIAL_PORT)
            delay = RECONNECT_BASE_DELAY          # reset backoff on success

            while not stop_event.is_set():
                raw_bytes = port.readline()
                if not raw_bytes:
                    continue                       # timeout — no data, try again

                raw_line = raw_bytes.decode("ascii", errors="replace")
                logger.debug("RX: %r", raw_line.strip())

                reading = parse_scale_line(raw_line)
                if reading:
                    weight_store.update(reading)

        except serial.SerialException as exc:
            logger.error("Serial error on %s: %s — retrying in %ds", SERIAL_PORT, exc, delay)
        except Exception as exc:
            logger.exception("Unexpected error in serial reader: %s", exc)
        finally:
            if port and port.is_open:
                port.close()
                logger.info("Serial port %s closed.", SERIAL_PORT)

        # Back off before reconnecting (capped exponential)
        stop_event.wait(delay)
        delay = min(delay * 2, RECONNECT_MAX_DELAY)

# ──────────────────────────────────────────────────────────────────────
#  FLASK APPLICATION
# ──────────────────────────────────────────────────────────────────────

app = Flask(__name__)


def _check_api_key():
    """If an API key is configured, reject requests that don't supply it."""
    if API_KEY:
        provided = request.headers.get("X-API-Key", "")
        if provided != API_KEY:
            return jsonify({"error": "Unauthorized — invalid or missing X-API-Key header"}), 401
    return None   # all good


@app.before_request
def before_request():
    auth_result = _check_api_key()
    if auth_result is not None:
        return auth_result


@app.route("/weight", methods=["GET"])
def get_weight():
    """Return the latest weight reading as JSON.

    Response example:
        {
          "weight": 12345.67,
          "unit":   "kg",
          "stable": true,
          "raw":    "ST,GS,+000012345.67,kg"
        }
    """
    return jsonify(weight_store.get())


@app.route("/health", methods=["GET"])
def health():
    """Simple health-check endpoint."""
    return jsonify({"status": "ok"})

# ──────────────────────────────────────────────────────────────────────
#  MAIN — start serial thread + Waitress production server
# ──────────────────────────────────────────────────────────────────────

_stop_event = threading.Event()


def start_service():
    """Start the serial reader thread and the HTTP server.

    Called directly when running standalone, or from the Windows service.
    """
    logger.info("Starting scale service on %s:%d …", HTTP_HOST, HTTP_PORT)

    # Launch the serial reader in a daemon thread
    reader_thread = threading.Thread(
        target=_serial_reader_loop,
        args=(_stop_event,),
        daemon=True,
        name="serial-reader",
    )
    reader_thread.start()

    # Serve HTTP with the production-grade Waitress WSGI server
    from waitress import serve
    serve(app, host=HTTP_HOST, port=HTTP_PORT)


def stop_service():
    """Signal the serial reader to stop."""
    logger.info("Stopping scale service …")
    _stop_event.set()


if __name__ == "__main__":
    try:
        start_service()
    except KeyboardInterrupt:
        stop_service()
        logger.info("Scale service shut down.")
