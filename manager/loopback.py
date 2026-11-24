"""The plain-HTTP listener on this device alone, beside the HTTPS one.

One uvicorn server applies one TLS setting, so while the network port answers HTTPS the
windows VPinFE opens itself and the Manager's calls to the API need a second listener.
It is bound to 127.0.0.1 only, so what reaches it is this device by construction.
"""

from __future__ import annotations

import asyncio
import logging
import socket

import uvicorn
from nicegui import app

from common.devices import serving

logger = logging.getLogger("vpinfe.manager.loopback")

_server: uvicorn.Server | None = None
_task: asyncio.Task | None = None


def bind() -> socket.socket:
    """Take a free loopback port now, so the port is known before the server starts."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.set_inheritable(True)
    serving.bound("loopback_port", sock.getsockname()[1])
    return sock


def _report(task: asyncio.Task[None]) -> None:
    if not task.cancelled() and task.exception() is not None:
        logger.error("The loopback listener stopped; VPinFE's own windows cannot reach it",
                     exc_info=task.exception())


def serve_on_startup(sock: socket.socket) -> None:
    """Start serving `sock` when the app starts, and stop with it.

    The app's own lifespan runs under the first server; this one runs with `lifespan="off"`
    so the app starts once. `proxy_headers` is off because a header on this listener is
    never believed: nothing here is a proxy.
    """

    async def start() -> None:
        global _server, _task
        _server = uvicorn.Server(uvicorn.Config(app, ws="wsproto", lifespan="off",
                                                proxy_headers=False, log_config=None))
        _task = asyncio.create_task(_server.serve(sockets=[sock]))
        _task.add_done_callback(_report)

    async def stop() -> None:
        if _server is not None:
            _server.should_exit = True
        if _task is not None:
            try:
                await asyncio.wait_for(_task, timeout=5)
            except (TimeoutError, asyncio.CancelledError):
                logger.warning("The loopback listener did not stop in time")

    app.on_startup(start)
    app.on_shutdown(stop)
