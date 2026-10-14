#!/usr/bin/env python3
"""
mock_weighbridge.py — Virtual Weighbridge Simulator (COM port writer)
=====================================================================
Simulates a physical weighbridge by writing realistic ASCII weight data
to a virtual serial port.  Designed to be used with the **com0com**
null-modem emulator which creates paired virtual COM ports (e.g.
COM3 ↔ COM4).  This script writes to one end (COM4); scale_service.py
reads from the other end (COM3).

Lifecycle of a simulated vehicle
--------------------------------
  1. Tare / empty phase      – a few readings near 0 kg  (stable)
  2. Vehicle drives on       – weight ramps up with noise (unstable)
  3. Settling phase           – weight oscillates around target (unstable)
  4. Stable reading           – several identical readings  (stable)
  5. Vehicle drives off       – weight drops back toward 0  (unstable)
  6. Pause                    – random 3–8 s gap before next vehicle

The target gross weight for each vehicle is randomised between
5 000 and 42 000 kg (configurable).

Serial line format (matches scale_service.py parser):
    ST,GS,+000012345.67,kg\\r\\n

Usage
-----
  python mock_weighbridge.py                # defaults: COM4, 9600 baud
  python mock_weighbridge.py --port COM6    # use a different port

Prerequisites
-------------
  pip install pyserial
  com0com driver installed (https://sourceforge.net/projects/com0com/)
  A virtual pair like COM3 ↔ COM4 created via com0com's Setup utility.

Author : WeighScale Integration Team
License: MIT
"""

# ──────────────────────────────────────────────────────────────────────
#  CONFIGURATION
# ──────────────────────────────────────────────────────────────────────

WRITE_PORT     = "COM4"      # The port this simulator WRITES to
BAUD_RATE      = 9600
LINE_INTERVAL  = 0.5         # Seconds between transmitted lines
MIN_GROSS_KG   = 5_000       # Minimum random gross weight
MAX_GROSS_KG   = 42_000      # Maximum random gross weight
TARE_WEIGHT    = 50.0        # Simulated empty-scale noise centre

# ──────────────────────────────────────────────────────────────────────
#  IMPORTS
# ──────────────────────────────────────────────────────────────────────

import argparse
import random
import time
import sys
import logging

import serial

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("mock_weighbridge")

# ──────────────────────────────────────────────────────────────────────
#  HELPER — build a scale ASCII line
# ──────────────────────────────────────────────────────────────────────

def _make_line(weight: float, stable: bool, unit: str = "kg") -> bytes:
    """Build a scale-protocol line and return it as bytes.

    Format: "ST,GS,+000012345.67,kg\\r\\n"
    """
    flag = "ST" if stable else "US"
    sign = "+" if weight >= 0 else "-"
    line = f"{flag},GS,{sign}{abs(weight):013.2f},{unit}\r\n"
    return line.encode("ascii")

# ──────────────────────────────────────────────────────────────────────
#  SIMULATE ONE VEHICLE CYCLE
# ──────────────────────────────────────────────────────────────────────

def _simulate_vehicle(port: serial.Serial) -> None:
    """Write a full drive-on → settle → stable → drive-off cycle."""

    target = random.uniform(MIN_GROSS_KG, MAX_GROSS_KG)
    logger.info("=== New vehicle — target gross weight: %.2f kg ===", target)

    # Phase 1: Tare / empty  (3–5 stable readings near zero)
    for _ in range(random.randint(3, 5)):
        w = TARE_WEIGHT + random.gauss(0, 2)
        line = _make_line(w, stable=True)
        port.write(line)
        logger.info("TX (tare):     %s", line.decode().strip())
        time.sleep(LINE_INTERVAL)

    # Phase 2: Vehicle driving on  (weight ramps up, unstable)
    current = TARE_WEIGHT
    steps = random.randint(8, 15)
    increment = (target - current) / steps
    for i in range(steps):
        current += increment + random.gauss(0, increment * 0.3)
        current = max(current, 0)
        line = _make_line(current, stable=False)
        port.write(line)
        logger.info("TX (ramp-up):  %s", line.decode().strip())
        time.sleep(LINE_INTERVAL)

    # Phase 3: Settling — oscillate around target (unstable)
    for _ in range(random.randint(5, 10)):
        w = target + random.gauss(0, target * 0.002)
        line = _make_line(w, stable=False)
        port.write(line)
        logger.info("TX (settling): %s", line.decode().strip())
        time.sleep(LINE_INTERVAL)

    # Phase 4: Stable — several identical readings (stable)
    stable_weight = round(target, 2)
    stable_count = random.randint(5, 8)
    for _ in range(stable_count):
        line = _make_line(stable_weight, stable=True)
        port.write(line)
        logger.info("TX (STABLE):   %s", line.decode().strip())
        time.sleep(LINE_INTERVAL)

    # Phase 5: Vehicle driving off — weight drops (unstable)
    current = stable_weight
    steps = random.randint(6, 12)
    decrement = current / steps
    for i in range(steps):
        current -= decrement + random.gauss(0, decrement * 0.3)
        current = max(current, 0)
        line = _make_line(current, stable=False)
        port.write(line)
        logger.info("TX (drive-off): %s", line.decode().strip())
        time.sleep(LINE_INTERVAL)

    # Phase 6: Pause between vehicles
    pause = random.uniform(3, 8)
    logger.info("Pause %.1f s before next vehicle …", pause)
    time.sleep(pause)

# ──────────────────────────────────────────────────────────────────────
#  MAIN
# ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Mock weighbridge simulator (COM writer)")
    parser.add_argument("--port", default=WRITE_PORT, help="COM port to write to (default: %(default)s)")
    parser.add_argument("--baud", type=int, default=BAUD_RATE, help="Baud rate (default: %(default)s)")
    args = parser.parse_args()

    logger.info("Opening %s at %d baud for writing …", args.port, args.baud)
    try:
        port = serial.Serial(port=args.port, baudrate=args.baud, timeout=1)
    except serial.SerialException as exc:
        logger.error("Cannot open %s: %s", args.port, exc)
        logger.error("Make sure com0com is installed and the virtual pair exists.")
        sys.exit(1)

    logger.info("Mock weighbridge running.  Press Ctrl+C to stop.")
    try:
        while True:
            _simulate_vehicle(port)
    except KeyboardInterrupt:
        logger.info("Shutting down mock weighbridge.")
    finally:
        port.close()


if __name__ == "__main__":
    main()
