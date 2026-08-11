"""One-click desktop launcher.

Runs the FastAPI server in a background thread and opens it in a native
app window (pywebview) instead of a browser tab. Launched via
pythonw.exe (the windowless Python interpreter) rather than python.exe,
so double-clicking a shortcut to this file gives a console-free,
browser-free "real app" experience - see README.md's "Desktop app"
section for the one-time setup and shortcut target.

Not used by the normal `uvicorn app.main:app --reload` development
workflow - that still runs the server directly, with live reload, in a
terminal.
"""
import socket
import threading
import time

import uvicorn
import webview

from app.main import app

HOST = "127.0.0.1"
PORT = 8000


def _run_server():
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


def _wait_until_listening():
    for _ in range(100):
        try:
            with socket.create_connection((HOST, PORT), timeout=0.2):
                return
        except OSError:
            time.sleep(0.1)


if __name__ == "__main__":
    threading.Thread(target=_run_server, daemon=True).start()
    _wait_until_listening()
    webview.create_window(
        "HealthCross - Underwriting Intelligence",
        f"http://{HOST}:{PORT}",
        width=1440,
        height=920,
    )
    webview.start()
