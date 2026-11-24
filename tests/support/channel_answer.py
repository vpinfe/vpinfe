"""What a running `DeviceChannel` said to a connection, read off the channel.

An accepted connection is one the channel has registered as its window; a refused one is
closed. Each wait here ends on one of those, and its limit only ever ends a failure.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any

import websockets
from websockets.asyncio.client import ClientConnection

from frontend.device_channel import DeviceChannel

ANSWER_S = 10.0
_POLL_S = 0.01


async def _until(done, what: str) -> None:
    deadline = asyncio.get_running_loop().time() + ANSWER_S
    while not done():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"not within {ANSWER_S}s: {what}")
        await asyncio.sleep(_POLL_S)


async def serving(channel: DeviceChannel) -> None:
    await _until(lambda: channel._server is not None, "the channel serving")


async def registered(channel: DeviceChannel, window: str, url: str,
                     **connect: Any) -> ClientConnection:
    """Connect to `url` as `window`; the socket, once the channel holds it as that window.

    Raises the channel's `ConnectionClosed` when it refuses the connection instead.
    """
    if channel.is_window_connected(window):
        raise AssertionError(f"{window} is connected already, so its registration "
                             "cannot say anything about this connection")
    socket = await websockets.connect(url, **connect)
    said = asyncio.ensure_future(socket.recv())
    try:
        await _until(lambda: said.done() or channel.is_window_connected(window),
                     f"the channel registering or refusing {window}")
        if said.done():
            message = said.result()     # a refusal raises its ConnectionClosed here
            raise AssertionError(f"the channel said {message!r} before being asked")
    finally:
        if not said.done():
            said.cancel()
            with suppress(asyncio.CancelledError):
                await said
    return socket


async def refused(url: str, **connect: Any) -> websockets.exceptions.ConnectionClosed:
    """Connect to `url`; the close the channel answers with."""
    socket = await websockets.connect(url, **connect)
    try:
        said = await asyncio.wait_for(socket.recv(), timeout=ANSWER_S)
    except websockets.exceptions.ConnectionClosed as closed:
        return closed
    except TimeoutError:
        await socket.close()
        raise AssertionError(f"the channel held the connection for {ANSWER_S}s "
                             "instead of refusing it") from None
    raise AssertionError(f"the channel said {said!r} instead of refusing")


async def released(channel: DeviceChannel, window: str,
                   close: Callable[[], Awaitable[object]]) -> None:
    """`close()`, then until the channel no longer holds `window`."""
    if not channel.is_window_connected(window):
        raise AssertionError(f"{window} is not connected, so its release proves nothing")
    await close()
    await _until(lambda: not channel.is_window_connected(window),
                 f"the channel releasing {window}")
