"""Who is allowed to open the device channel.

The channel reaches `shutdown_system`, `launch_table` and `build_metadata`, and binding it
to loopback does not keep other pages out: a WebSocket handshake is not subject to the
same-origin policy the way XHR is, so any page in any browser on this machine could
connect. The origin check is what actually keeps them out, so it is asserted against a
real handshake rather than only against the predicate.
"""

from __future__ import annotations

import unittest

import websockets

from common.devices import serving as bound_servers
from frontend.device_channel import DeviceChannel
from tests.support.channel_answer import registered, serving


class OriginPredicateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.channel = DeviceChannel(port=0)
        bound_servers.bound("http_port", 8001)
        bound_servers.bound("loopback_port", 8003)
        self.addCleanup(bound_servers.forget)

    def test_a_window_served_from_this_machine_is_allowed(self) -> None:
        for origin in ("http://127.0.0.1:8001", "http://localhost:8001",
                       "http://[::1]:8001", "http://127.0.0.1:8003"):
            with self.subTest(origin=origin):
                self.assertTrue(self.channel._origin_allowed(origin))

    def test_another_program_on_this_machine_is_refused(self) -> None:
        """A page served from a port VPinFE did not open is some other program's page,
        and loopback does not make it ours."""
        for origin in ("http://127.0.0.1:54321", "http://localhost:3000",
                       "http://127.0.0.1", "http://127.0.0.1:notaport"):
            with self.subTest(origin=origin):
                self.assertFalse(self.channel._origin_allowed(origin))

    def test_a_page_from_anywhere_else_is_refused(self) -> None:
        for origin in ("http://evil.example.com", "https://mail.google.com",
                       "http://192.168.1.10:8001", "null",
                       # A name is not a prefix match: these resolve wherever their
                       # owner points them, and both read as loopback at a glance.
                       "http://127.0.0.1.evil.com", "http://localhost.evil.com"):
            with self.subTest(origin=origin):
                self.assertFalse(self.channel._origin_allowed(origin))

    def test_no_origin_at_all_is_allowed(self) -> None:
        """A non-browser client sends none. It already runs code here, so refusing it
        stops nothing and would break scripts that legitimately drive the channel."""
        self.assertTrue(self.channel._origin_allowed(None))


class OriginHandshakeTests(unittest.IsolatedAsyncioTestCase):
    """Against a running server, because the predicate being right is only half of it -
    the header has to be read off the handshake, and that is websockets-version specific."""

    async def asyncSetUp(self) -> None:
        from tests.support.browser_session import free_port

        bound_servers.bound("http_port", 8001)
        self.addCleanup(bound_servers.forget)
        self.channel = DeviceChannel(port=free_port())
        # A real window is one this process opened, so it has an API registered before
        # its browser is launched. Without that the channel refuses the name outright
        # and the origin check never runs.
        self.channel.register_api("bg", object())  # type: ignore[arg-type]
        self.channel.start()
        self.addCleanup(self.channel.stop)
        await serving(self.channel)

    async def _connect(self, origin):
        """Open a connection and say whether the channel holds it as the window.

        Raises `ConnectionClosed` when the channel refuses it instead.
        """
        extra = {"additional_headers": {"Origin": origin}} if origin else {}
        url = f"ws://127.0.0.1:{self.channel.port}/?window=bg"
        async with await registered(self.channel, "bg", url, **extra):
            # While it is still open: registration is undone on disconnect.
            return self.channel.is_window_connected("bg")

    async def test_a_real_window_connects(self) -> None:
        self.assertTrue(await self._connect("http://127.0.0.1:8001"))

    async def test_a_hostile_page_is_closed_and_never_registers(self) -> None:
        with self.assertRaises(websockets.exceptions.ConnectionClosed) as caught:
            await self._connect("https://mail.google.com")

        assert caught.exception.rcvd is not None
        self.assertEqual(caught.exception.rcvd.code, 1008)
        self.assertFalse(self.channel.is_window_connected("bg"),
                         "a refused connection must not displace a real window")


if __name__ == "__main__":
    unittest.main()
