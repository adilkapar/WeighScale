#!/usr/bin/env python3
"""
scale_win_service.py — Windows Service wrapper for the Scale REST API
=====================================================================
Wraps scale_service.py as an NT service so that it starts automatically
with Windows, can be managed from services.msc, and survives logoffs.

Usage (run from an elevated / Administrator command prompt)
-----------------------------------------------------------
  python scale_win_service.py install     Install the service
  python scale_win_service.py start       Start the service
  python scale_win_service.py stop        Stop the service
  python scale_win_service.py remove      Uninstall the service
  python scale_win_service.py restart     Stop + Start
  python scale_win_service.py debug       Run interactively (for debugging)

Prerequisites
-------------
  pip install pywin32
  After installing pywin32, run:
      python Scripts/pywin32_postinstall.py -install
  (from your Python environment) to register the service helper DLLs.

Notes
-----
  • The service runs under the LOCAL SYSTEM account by default.
    If your COM port requires a specific user, change the Log On account
    in services.msc after installing.
  • Log output goes to scale_service.log (see scale_service.py config).
  • The HTTP server (Waitress) runs inside a child thread so that the
    service control manager can stop it cleanly.

Author : WeighScale Integration Team
License: MIT
"""

# ──────────────────────────────────────────────────────────────────────
#  CONFIGURATION — must match scale_service.py or be imported from it
# ──────────────────────────────────────────────────────────────────────

SERVICE_NAME         = "WeighScaleService"
SERVICE_DISPLAY_NAME = "Weigh Scale REST Service"
SERVICE_DESCRIPTION  = (
    "Reads weight data from a serial weighbridge and exposes it as a "
    "REST API for SAP integration."
)

# ──────────────────────────────────────────────────────────────────────
#  IMPORTS
# ──────────────────────────────────────────────────────────────────────

import sys
import os
import threading
import logging

import win32serviceutil
import win32service
import win32event
import servicemanager

# Ensure the directory containing this script is on sys.path so that
# scale_service can be imported regardless of the service working dir.
_script_dir = os.path.dirname(os.path.abspath(__file__))
if _script_dir not in sys.path:
    sys.path.insert(0, _script_dir)

import scale_service  # noqa: E402 — our companion module

logger = logging.getLogger("scale_win_service")

# ──────────────────────────────────────────────────────────────────────
#  WINDOWS SERVICE CLASS
# ──────────────────────────────────────────────────────────────────────

class WeighScaleWinService(win32serviceutil.ServiceFramework):
    """NT service that wraps scale_service.start_service / stop_service."""

    _svc_name_         = SERVICE_NAME
    _svc_display_name_ = SERVICE_DISPLAY_NAME
    _svc_description_  = SERVICE_DESCRIPTION

    def __init__(self, args):
        super().__init__(args)
        # Create a Windows event so the SCM can tell us to stop.
        self.hWaitStop = win32event.CreateEvent(None, 0, 0, None)
        self._server_thread = None

    # ---- called by the SCM when the service should start ----
    def SvcDoRun(self):
        servicemanager.LogMsg(
            servicemanager.EVENTLOG_INFORMATION_TYPE,
            servicemanager.PYS_SERVICE_STARTED,
            (self._svc_name_, ""),
        )
        logger.info("Windows service SvcDoRun invoked.")
        self.main()

    # ---- called by the SCM when the service should stop -----
    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        logger.info("Windows service SvcStop invoked.")
        scale_service.stop_service()
        win32event.SetEvent(self.hWaitStop)

    # ---- service main logic ----------------------------------
    def main(self):
        """Run the scale service in a background thread and wait for stop."""
        self._server_thread = threading.Thread(
            target=self._run_server, daemon=True, name="scale-server"
        )
        self._server_thread.start()

        # Block here until the SCM sets the stop event.
        win32event.WaitForSingleObject(self.hWaitStop, win32event.INFINITE)
        logger.info("Stop event received — exiting main loop.")

    @staticmethod
    def _run_server():
        try:
            scale_service.start_service()
        except Exception:
            logger.exception("scale_service.start_service() raised an exception.")

# ──────────────────────────────────────────────────────────────────────
#  ENTRY POINT
# ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) == 1:
        # Launched by the SCM without arguments → run the service.
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(WeighScaleWinService)
        servicemanager.StartServiceCtrlDispatcher()
    else:
        # Command-line management: install / start / stop / remove / debug
        win32serviceutil.HandleCommandLine(WeighScaleWinService)
