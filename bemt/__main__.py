"""Entry point: start the local server and open the browser at it."""

from __future__ import annotations

import logging
import socket
import threading
import time
import webbrowser

import uvicorn

from .config import PORT


def _open_browser_when_ready(port: int, timeout: float = 25.0) -> None:
    """Open the browser only once the server is actually accepting connections.

    A blind timer leaves the user staring at a dead tab whenever the port is
    already in use; this polls, and opens nothing if the server never comes up.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                webbrowser.open(f"http://localhost:{port}/")
                return
        except OSError:
            time.sleep(0.25)
    logging.getLogger(__name__).warning(
        "server did not start on port %s within %.0fs; not opening a browser",
        port, timeout)


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    threading.Thread(target=_open_browser_when_ready, args=(PORT,),
                     daemon=True).start()
    try:
        uvicorn.run("bemt.app:app", host="127.0.0.1", port=PORT,
                    log_level="info")
    except OSError as e:
        print(f"\n  Could not start on port {PORT}: {e}")
        print("  BEMT may already be running - check your other windows,")
        print(f"  or open http://localhost:{PORT}/ directly.\n")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
