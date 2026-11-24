"""A start the port settings cannot satisfy says which setting to change."""

from __future__ import annotations

import contextlib
import errno
import io
import socket
import unittest
from unittest import mock

from common.config.config_access import NetworkConfig
from common.devices import port_check
from tests.support.browser_session import free_port


def _free(address: str, port: int) -> OSError | None:
    return None


def _held(*ports: int):
    """A bind that finds these ports taken and every other one free."""
    def bind(address: str, port: int) -> OSError | None:
        return OSError(errno.EADDRINUSE, "in use") if port in ports else None
    return bind


def _network(http: int = 9101, ws: int = 9102) -> NetworkConfig:
    return NetworkConfig(http_port=http, ws_port=ws)


class ProblemsTests(unittest.TestCase):
    def test_free_ports_have_nothing_to_say(self) -> None:
        self.assertEqual(port_check.problems(_network(), bind=_free), [])

    def test_a_port_two_settings_share_names_both(self) -> None:
        found = port_check.problems(_network(http=9101, ws=9101), bind=_free)

        self.assertEqual(len(found), 1)
        self.assertIn("Port", found[0])
        self.assertIn("Window Port", found[0])
        self.assertIn("9101", found[0])

    def test_a_taken_port_names_the_setting_and_its_key(self) -> None:
        found = port_check.problems(_network(), bind=_held(9101), pause=lambda _: None)

        self.assertEqual(len(found), 1)
        self.assertIn("Port", found[0])
        self.assertIn("9101", found[0])
        self.assertIn("network.http_port", found[0])

    def test_each_taken_port_gets_its_own_line(self) -> None:
        found = port_check.problems(_network(), bind=_held(9101, 9102),
                                    pause=lambda _: None)

        self.assertEqual(len(found), 2)
        self.assertIn("network.ws_port", found[1])

    def test_a_port_the_last_run_is_still_closing_is_waited_for(self) -> None:
        answers = [OSError(errno.EADDRINUSE, "in use"), OSError(errno.EADDRINUSE, "in use"),
                   None]
        paused: list[float] = []

        def bind(address: str, port: int) -> OSError | None:
            return answers.pop(0) if answers else None

        found = port_check.problems(_network(), ("http_port",), bind=bind,
                                    pause=paused.append)

        self.assertEqual((found, len(paused)), ([], 2))

    def test_a_port_the_device_refuses_says_why_and_where(self) -> None:
        def bind(address: str, port: int) -> OSError | None:
            return PermissionError(errno.EACCES, "denied")

        found = port_check.problems(_network(http=80), ("http_port",), bind=bind)

        self.assertEqual(len(found), 1)
        self.assertIn("0.0.0.0 port 80", found[0])
        self.assertIn("network.http_port", found[0])

    def test_the_window_channel_is_asked_on_loopback_whatever_the_http_address(self) -> None:
        asked: list[tuple[str, int]] = []

        def bind(address: str, port: int) -> OSError | None:
            asked.append((address, port))
            return None

        network = NetworkConfig(http_port=9101, ws_port=9102, http_bind="192.168.1.20")
        port_check.problems(network, bind=bind)

        self.assertEqual(asked, [("192.168.1.20", 9101), ("127.0.0.1", 9102)])

    def test_only_the_ports_asked_for_are_checked(self) -> None:
        found = port_check.problems(_network(), ("ws_port",), bind=_held(9101))

        self.assertEqual(found, [])


class AgainstARealSocketTests(unittest.TestCase):
    def test_a_port_another_socket_listens_on_is_reported(self) -> None:
        port = free_port()
        holder = socket.socket()
        self.addCleanup(holder.close)
        holder.bind(("127.0.0.1", port))
        holder.listen()

        found = port_check.problems(NetworkConfig(http_port=port, http_bind="127.0.0.1"),
                                    ("http_port",), pause=lambda _: None)

        self.assertEqual(len(found), 1)
        self.assertIn(str(port), found[0])

    def test_a_number_that_is_not_a_port_is_refused_not_a_traceback(self) -> None:
        found = port_check.problems(NetworkConfig(http_port=70000, http_bind="127.0.0.1"),
                                    ("http_port",))

        self.assertEqual(len(found), 1)
        self.assertIn("network.http_port", found[0])

    def test_a_free_port_is_reported_as_nothing(self) -> None:
        port = free_port()

        self.assertEqual(
            port_check.problems(NetworkConfig(http_port=port, http_bind="127.0.0.1"),
                                ("http_port",)), [])


class RequireTests(unittest.TestCase):
    def test_a_start_that_cannot_be_served_prints_and_exits(self) -> None:
        port = free_port()
        holder = socket.socket()
        self.addCleanup(holder.close)
        holder.bind(("127.0.0.1", port))
        holder.listen()
        shown = io.StringIO()

        with (contextlib.redirect_stderr(shown), mock.patch.object(port_check, "_PAUSE", 0),
              self.assertRaises(SystemExit) as caught):
            port_check.require(NetworkConfig(http_port=port, http_bind="127.0.0.1"),
                               ("http_port",))

        self.assertEqual(caught.exception.code, 1)
        self.assertIn("network.http_port", shown.getvalue())


if __name__ == "__main__":
    unittest.main()
