"""Who may claim to be a window.

A client named itself in the query string and the channel believed it. Two consequences,
both reached live during a diagnostic session rather than in theory:

  - Any name was accepted, including one this process never opened.
  - A second client naming an open window *replaced* it. The real window's events went
    to the impostor, and the impostor inherited its whole API surface - `shutdown_system`
    and `build_metadata` included.

Every real window is registered before its browser is launched, so the set of valid names
is known up front and neither needed a new mechanism to fix.
"""

from __future__ import annotations

import unittest

from common.devices import serving as bound_servers
from frontend.device_channel import DeviceChannel
from tests.support.browser_session import free_port
from tests.support.channel_answer import refused, registered, released, serving

LOCAL = {"Origin": "http://127.0.0.1:8001"}


class WindowIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        bound_servers.bound("http_port", 8001)
        self.addCleanup(bound_servers.forget)
        self.channel = DeviceChannel(port=free_port())
        self.channel.register_api("playfield", object())  # type: ignore[arg-type]
        self.channel.start()
        self.addCleanup(self.channel.stop)
        await serving(self.channel)

    def _url(self, window: str) -> str:
        return f"ws://127.0.0.1:{self.channel.port}/?window={window}"

    async def _open(self, window: str):
        """Connect and hold it. Returns the socket, or raises ConnectionClosed."""
        return await registered(self.channel, window, self._url(window),
                                additional_headers=LOCAL)

    async def _refusal(self, window: str) -> str:
        closed = await refused(self._url(window), additional_headers=LOCAL)
        assert closed.rcvd is not None
        self.assertEqual(closed.rcvd.code, 1008)
        return closed.rcvd.reason

    async def test_a_window_this_process_opened_connects(self) -> None:
        socket = await self._open("playfield")
        self.addAsyncCleanup(socket.close)

        self.assertTrue(self.channel.is_window_connected("playfield"))

    async def test_a_window_this_process_never_opened_is_refused(self) -> None:
        """The valid names are known before any browser starts, so a name outside the
        set can only be something else dialling in."""
        self.assertEqual(await self._refusal("scoreview"), "unknown window")

    async def test_a_second_client_cannot_displace_an_open_window(self) -> None:
        """This is the one that was reachable: the impostor took the real window's
        events and its API surface, and the real window never noticed."""
        real = await self._open("playfield")
        self.addAsyncCleanup(real.close)

        self.assertEqual(await self._refusal("playfield"), "window already connected")

    async def test_the_real_window_survives_the_attempt(self) -> None:
        real = await self._open("playfield")
        self.addAsyncCleanup(real.close)

        await self._refusal("playfield")

        self.assertTrue(self.channel.is_window_connected("playfield"),
                        "the window that was already there keeps its connection")

    async def test_a_window_that_dropped_can_come_back(self) -> None:
        """Refusing a duplicate must not refuse a reconnect: a window whose socket died
        is deregistered on the way out, so its name is free again."""
        first = await self._open("playfield")
        await released(self.channel, "playfield", first.close)

        second = await self._open("playfield")
        self.addAsyncCleanup(second.close)

        self.assertTrue(self.channel.is_window_connected("playfield"))


if __name__ == "__main__":
    unittest.main()
