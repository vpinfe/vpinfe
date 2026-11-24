"""Whether a request is the machine's own, which is the one question that decides trust
without a credential.

Each condition is asserted alone, because the failure that matters is one condition quietly
standing in for another: a loopback peer is not enough when a proxy forwarded it, and a
loopback Host is not enough when the page that sent it belongs to another site.
"""

from __future__ import annotations

import threading
import time
import unittest
from unittest import mock

from common.devices import serving
from common.host import this_machine


class Machine(unittest.TestCase):
    def setUp(self) -> None:
        patched = mock.patch.object(
            this_machine, "own_names",
            return_value=frozenset({"localhost", "127.0.0.1", "::1", "192.168.1.20",
                                    "cab", "cab.local"}))
        patched.start()
        self.addCleanup(patched.stop)
        interfaces = mock.patch.object(this_machine, "_interface_addresses",
                                       return_value={"192.168.1.20"})
        interfaces.start()
        self.addCleanup(interfaces.stop)
        this_machine.forget()
        self.addCleanup(this_machine.forget)
        serving.bound("http_port", 8001)
        self.addCleanup(serving.forget)


class ThePeerTests(Machine):
    def test_loopback_and_the_machines_own_addresses_are_the_machine(self) -> None:
        for peer in ("127.0.0.1", "::1", "127.4.5.6", "192.168.1.20", "localhost"):
            with self.subTest(peer=peer):
                self.assertTrue(this_machine.request_is_this_machine(peer, {}))

    def test_another_address_is_not(self) -> None:
        for peer in ("192.168.1.99", "10.0.0.5", "203.0.113.9"):
            with self.subTest(peer=peer):
                self.assertFalse(this_machine.request_is_this_machine(peer, {}))


class TheNameServiceTests(unittest.TestCase):
    def test_an_address_a_name_resolves_to_is_not_the_machine(self) -> None:
        this_machine.forget()
        self.addCleanup(this_machine.forget)
        with mock.patch.object(this_machine, "_interface_addresses",
                               return_value={"192.168.1.20"}), \
                mock.patch.object(this_machine.addresses, "hosts",
                                  return_value=["192.168.1.20", "cab", "192.168.1.1"]):
            self.assertFalse(this_machine.peer_is_this_machine("192.168.1.1"))
            self.assertTrue(this_machine.peer_is_this_machine("192.168.1.20"))
            self.assertTrue(this_machine.names_this_machine("192.168.1.1"))


class TheForwardingTests(Machine):
    def test_a_loopback_request_that_a_proxy_forwarded_is_not_the_machine(self) -> None:
        for name in ("X-Forwarded-For", "x-forwarded-host", "Forwarded", "X-Real-IP",
                     "X-Forwarded-Proto"):
            with self.subTest(header=name):
                self.assertFalse(
                    this_machine.request_is_this_machine("127.0.0.1", {name: "203.0.113.9"}))


class TheHostTests(Machine):
    def test_a_name_the_machine_answers_to_is_accepted(self) -> None:
        for host in ("localhost:8001", "127.0.0.1:8001", "[::1]:8001", "cab.local",
                     "192.168.1.20:8001", "app.localhost"):
            with self.subTest(host=host):
                self.assertTrue(
                    this_machine.request_is_this_machine("127.0.0.1", {"host": host}))

    def test_a_name_that_points_here_but_is_not_ours_is_refused(self) -> None:
        """DNS rebinding: the name resolves to loopback, so the socket looks local."""
        for host in ("evil.example.com", "127.0.0.1.evil.com", "rebind.attacker.test:8001"):
            with self.subTest(host=host):
                self.assertFalse(
                    this_machine.request_is_this_machine("127.0.0.1", {"host": host}))


class TheOriginTests(Machine):
    def asked(self, origin: str, host: str = "localhost:8001") -> bool:
        return this_machine.request_is_this_machine(
            "127.0.0.1", {"host": host, "origin": origin})

    def test_the_page_the_manager_served_is_accepted(self) -> None:
        self.assertTrue(self.asked("http://localhost:8001"))

    def test_a_theme_page_is_the_same_origin_as_the_manager(self) -> None:
        self.assertTrue(self.asked("http://127.0.0.1:8001", host="127.0.0.1:8001"))

    def test_the_retired_theme_server_port_is_refused(self) -> None:
        self.assertFalse(self.asked("http://127.0.0.1:8000"))

    def test_a_page_of_another_site_is_refused(self) -> None:
        for origin in ("https://evil.example.com", "http://evil.example.com:8001", "null",
                       "http://localhost:9999", "file://", ""):
            with self.subTest(origin=origin):
                self.assertFalse(self.asked(origin))

    def test_no_origin_is_not_a_browser_and_is_accepted(self) -> None:
        self.assertTrue(this_machine.request_is_this_machine("127.0.0.1", {"host": "localhost"}))


class TheBrowsersOwnNoteTests(Machine):
    def test_a_request_the_browser_says_another_site_began_is_refused(self) -> None:
        """An image or a navigation sends no Origin, only this."""
        headers = {"host": "localhost:8001", "sec-fetch-site": "cross-site"}

        self.assertFalse(this_machine.request_is_this_machine("127.0.0.1", headers))

    def test_the_managers_own_requests_are_accepted(self) -> None:
        for note in ("same-origin", "same-site", "none"):
            with self.subTest(note=note):
                self.assertTrue(this_machine.request_is_this_machine(
                    "127.0.0.1", {"host": "localhost:8001", "sec-fetch-site": note}))


class TheSpellingTests(Machine):
    def test_an_address_in_its_ipv4_mapped_form_is_the_same_address(self) -> None:
        self.assertTrue(this_machine.request_is_this_machine("::ffff:192.168.1.20", {}))
        self.assertFalse(this_machine.request_is_this_machine("::ffff:192.168.1.99", {}))

    def test_an_ipv6_literal_host_is_read_whole(self) -> None:
        self.assertTrue(this_machine.request_is_this_machine(
            "::1", {"host": "[::1]:8001", "origin": "http://[::1]:8001"}))


class TheNamesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.addCleanup(this_machine.forget)
        this_machine.forget()

    def test_the_fully_qualified_name_is_one_this_machine_answers_to(self) -> None:
        with mock.patch.object(this_machine.socket, "gethostname", return_value="cab"), \
                mock.patch.object(this_machine.socket, "getfqdn", return_value="cab.example.lan"), \
                mock.patch.object(this_machine.addresses, "hosts", return_value=[]):
            self.assertTrue(this_machine.names_this_machine("cab.example.lan"))
            self.assertTrue(this_machine.names_this_machine("cab"))
            self.assertTrue(this_machine.names_this_machine("cab.local"))

    def test_only_the_first_ask_waits_for_the_names(self) -> None:
        calls = []

        def slow() -> frozenset[str]:
            calls.append(1)
            return frozenset({"cab"})

        with mock.patch.object(this_machine, "_collect", side_effect=slow):
            this_machine.own_names()
            this_machine._taken -= 3600
            held = this_machine.own_names()

        self.assertEqual(held, frozenset({"cab"}))


class TheTestClientTests(Machine):
    def test_the_test_client_is_the_machine_only_as_the_test_client(self) -> None:
        self.assertTrue(this_machine.request_is_this_machine(
            "testclient", {"host": "testserver"}))
        self.assertFalse(this_machine.request_is_this_machine(
            "127.0.0.1", {"host": "testserver"}))



class TheWarmingTests(unittest.TestCase):
    def test_warming_takes_the_names_off_the_calling_thread(self) -> None:
        this_machine.forget()
        self.addCleanup(this_machine.forget)
        taken = threading.Event()
        callers: list[threading.Thread] = []

        def collect() -> frozenset[str]:
            callers.append(threading.current_thread())
            taken.set()
            return frozenset({"cab"})

        with mock.patch.object(this_machine, "_collect", collect):
            # A names lookup another test left in flight lands after `forget()` and makes
            # the warm thread find names already held; forgetting again clears it.
            for _ in range(5):
                this_machine.warm()
                if taken.wait(1):
                    break
                this_machine.forget()
            self.assertTrue(taken.is_set())
            self.assertIsNot(callers[0], threading.current_thread())
            for _ in range(100):
                if this_machine._names:
                    break
                time.sleep(0.05)
            self.assertEqual(this_machine.own_names(), frozenset({"cab"}))


if __name__ == "__main__":
    unittest.main()
