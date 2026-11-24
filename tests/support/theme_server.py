"""The window routes served for real, on a free loopback port.

The same `theme_routes.register` the HTTP app uses, on an app of its own, so a test can
make real requests (conditional GET, Range) without starting a whole install.
"""

from __future__ import annotations

import socket
import threading
import time

import uvicorn
from fastapi import FastAPI

from frontend import theme_routes


class ThemeServer:
    def __init__(self, mount_points: dict[str, str] | None = None) -> None:
        self.app = FastAPI()
        theme_routes.register(self.app, mount_points or {})
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port: int = probe.getsockname()[1]
        self._server = uvicorn.Server(uvicorn.Config(
            self.app, host="127.0.0.1", port=self.port, log_level="warning"))
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def start(self) -> ThemeServer:
        self._thread.start()
        deadline = time.monotonic() + 10
        while not self._server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("the theme server did not start")
            time.sleep(0.02)
        return self

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=10)
