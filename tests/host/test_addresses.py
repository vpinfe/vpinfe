"""The addresses this machine offers somebody standing somewhere else."""

from __future__ import annotations

import configparser
import threading
import time
import unittest
from unittest import mock

from common.host import addresses


class UsableTests(unittest.TestCase):
    def test_an_address_something_else_can_dial(self) -> None:
        self.assertTrue(addresses.usable_ipv4("192.168.1.20"))
        self.assertTrue(addresses.usable_ipv4("10.0.0.4"))

    def test_loopback_is_not_an_address_for_anybody_else(self) -> None:
        self.assertFalse(addresses.usable_ipv4("127.0.0.1"))

    def test_a_failed_lease_is_not_an_answer(self) -> None:
        """169.254 means the machine never got an address. Offering it reads as one."""
        self.assertFalse(addresses.usable_ipv4("169.254.13.9"))

    def test_nothing_and_nonsense_are_not_addresses(self) -> None:
        self.assertFalse(addresses.usable_ipv4(""))
        self.assertFalse(addresses.usable_ipv4("a-hostname-not-an-address"))
        self.assertFalse(addresses.usable_ipv4("::1"))


def _dns(**answers: list[str]):
    """A resolver that knows only the names given, so a name the test did not mention fails
    to resolve, as it does on a device with no mDNS."""
    return lambda name: answers.get(name, [])


class OrderTests(unittest.TestCase):
    """Which one goes in front of a person, and which one never does."""

    def test_the_name_leads_when_it_resolves_to_this_machine(self) -> None:
        with mock.patch.object(addresses, "primary_ipv4", return_value="192.168.1.20"), \
                mock.patch.object(addresses, "_hostname", return_value="cab"), \
                mock.patch.object(addresses, "_resolved", side_effect=_dns(cab=["192.168.1.20"])):
            self.assertEqual(addresses.hosts(), ["cab", "192.168.1.20", "localhost"])

    def test_the_mdns_name_takes_the_names_place_when_it_resolves_to_this_machine(self) -> None:
        """A phone resolves cab.local through mDNS and often cannot resolve cab."""
        with mock.patch.object(addresses, "primary_ipv4", return_value="192.168.1.20"), \
                mock.patch.object(addresses, "_hostname", return_value="cab"), \
                mock.patch.object(addresses, "_resolved",
                                  side_effect=_dns(cab=["192.168.1.20"],
                                                   **{"cab.local": ["192.168.1.20"]})):
            self.assertEqual(addresses.hosts(), ["cab.local", "192.168.1.20", "localhost"])

    def test_the_mdns_name_comes_from_the_first_label_of_a_dotted_name(self) -> None:
        with mock.patch.object(addresses, "primary_ipv4", return_value="192.168.1.20"), \
                mock.patch.object(addresses, "_hostname", return_value="cab.example.lan"), \
                mock.patch.object(addresses, "_resolved",
                                  side_effect=_dns(**{"cab.local": ["192.168.1.20"]})):
            self.assertEqual(addresses.hosts(), ["cab.local", "192.168.1.20", "localhost"])

    def test_an_mdns_name_that_points_elsewhere_is_left_out(self) -> None:
        with mock.patch.object(addresses, "primary_ipv4", return_value="192.168.1.20"), \
                mock.patch.object(addresses, "_hostname", return_value="cab"), \
                mock.patch.object(addresses, "_interface_ipv4s", return_value=set()), \
                mock.patch.object(addresses, "_resolved",
                                  side_effect=_dns(cab=["192.168.1.20"],
                                                   **{"cab.local": ["192.168.1.99"]})):
            self.assertEqual(addresses.hosts(), ["cab", "192.168.1.20", "localhost"])

    def test_a_name_already_ending_in_local_is_not_given_a_second_one(self) -> None:
        with mock.patch.object(addresses, "primary_ipv4", return_value="192.168.1.20"), \
                mock.patch.object(addresses, "_hostname", return_value="cab.local"), \
                mock.patch.object(addresses, "_resolved",
                                  side_effect=_dns(**{"cab.local": ["192.168.1.20"]})):
            self.assertEqual(addresses.hosts(), ["cab.local", "192.168.1.20", "localhost"])

    def test_a_name_on_another_interface_of_this_machine_still_leads(self) -> None:
        with mock.patch.object(addresses, "primary_ipv4", return_value="192.168.1.20"), \
                mock.patch.object(addresses, "_hostname", return_value="cab"), \
                mock.patch.object(addresses, "_interface_ipv4s", return_value={"10.0.0.4"}), \
                mock.patch.object(addresses, "_resolved", side_effect=_dns(cab=["10.0.0.4"])):
            self.assertEqual(addresses.hosts()[0], "cab")

    def test_the_routed_address_leads_while_the_name_points_elsewhere(self) -> None:
        """A name that resolves to another machine would send a person there."""
        with mock.patch.object(addresses, "primary_ipv4", return_value="192.168.1.20"), \
                mock.patch.object(addresses, "_hostname", return_value="cab"), \
                mock.patch.object(addresses, "_interface_ipv4s", return_value=set()), \
                mock.patch.object(addresses, "_resolved", return_value=["192.168.1.99"]):
            self.assertEqual(addresses.hosts(), ["192.168.1.20", "localhost"])

    def test_no_routed_address_and_a_foreign_name_offers_neither(self) -> None:
        with mock.patch.object(addresses, "primary_ipv4", return_value=""), \
                mock.patch.object(addresses, "_hostname", return_value="cab"), \
                mock.patch.object(addresses, "_interface_ipv4s", return_value=set()), \
                mock.patch.object(addresses, "_resolved", return_value=["192.168.1.99"]):
            self.assertEqual(addresses.hosts(), ["localhost"])

    def test_loopback_is_last_and_never_offered_first(self) -> None:
        """It is the one address certain to be listening and certain not to work from
        anywhere else, so a list that led with it would lead with the wrong answer."""
        with mock.patch.object(addresses, "hosts",
                               return_value=["192.168.1.20", "localhost"]):
            self.assertEqual(addresses.best(8001, "/remote"),
                             "http://192.168.1.20:8001/remote")

    def test_loopback_is_still_offered_when_it_is_all_there_is(self) -> None:
        """A machine with no network still has a Manager on it, and an address that
        works there beats no address at all."""
        with mock.patch.object(addresses, "hosts", return_value=["localhost"]):
            self.assertEqual(addresses.best(8001, "/remote"),
                             "http://localhost:8001/remote")

    def test_nothing_to_offer_says_nothing(self) -> None:
        with mock.patch.object(addresses, "hosts", return_value=[]):
            self.assertEqual(addresses.best(8001), "")

    def test_a_name_that_does_not_resolve_does_not_take_the_list_with_it(self) -> None:
        """A machine whose own name does not resolve is common - it is what a fresh
        container does - and the routed address is still worth having."""
        with mock.patch.object(addresses, "primary_ipv4", return_value="10.0.0.4"), \
                mock.patch.object(addresses, "_hostname", return_value="cab"), \
                mock.patch.object(addresses, "_resolved", return_value=[]):
            self.assertEqual(addresses.hosts(), ["10.0.0.4", "cab", "localhost"])

    def test_the_same_address_twice_is_listed_once(self) -> None:
        """The routed address is usually also what the hostname resolves to."""
        with mock.patch.object(addresses, "primary_ipv4", return_value="10.0.0.4"), \
                mock.patch.object(addresses, "_hostname", return_value="cab"), \
                mock.patch.object(addresses, "_resolved", return_value=["10.0.0.4"]):
            self.assertEqual(addresses.hosts().count("10.0.0.4"), 1)


class MdnsLookupTests(unittest.TestCase):
    """A `.local` name that mDNS leaves waiting must never hold a page's build: the answer
    held so far comes back at once and the lookup runs beside the page."""

    def setUp(self) -> None:
        addresses.forget()
        self.addCleanup(addresses.forget)
        self.release = threading.Event()
        self.addCleanup(self.release.set)
        self.asked: list[str] = []

    def _slow(self, name: str) -> list[str]:
        self.asked.append(name)
        self.release.wait(5)
        return ["192.168.1.20"]

    def _answered(self, name: str) -> list[str]:
        """The held answer, once the lookup has finished and no longer holds the lock."""
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and name.lower() in addresses._mdns_asking:
            time.sleep(0.01)
        return addresses._resolved(name)

    def test_a_name_still_being_asked_is_none_at_once(self) -> None:
        with mock.patch.object(addresses, "_lookup", side_effect=self._slow):
            started = time.monotonic()
            self.assertEqual(addresses._resolved("cab.local"), [])
            self.assertLess(time.monotonic() - started, 0.5)

    def test_the_answer_that_comes_late_is_used_on_the_next_lookup(self) -> None:
        with mock.patch.object(addresses, "_lookup", side_effect=self._slow):
            addresses._resolved("cab.local")
            self.release.set()
            self.assertEqual(self._answered("cab.local"), ["192.168.1.20"])

    def test_a_name_asked_twice_while_waiting_is_looked_up_once(self) -> None:
        with mock.patch.object(addresses, "_lookup", side_effect=self._slow):
            addresses._resolved("cab.local")
            addresses._resolved("CAB.local")
            time.sleep(0.05)
        self.assertEqual(self.asked, ["cab.local"])

    def test_a_name_asked_twice_inside_the_hold_is_looked_up_once(self) -> None:
        with mock.patch.object(addresses, "_lookup", return_value=["10.0.0.4"]) as asked:
            addresses._resolved("cab.local")
            self._answered("cab.local")
            addresses._resolved("cab.local")
        self.assertEqual(asked.call_count, 1)

    def test_an_answer_past_its_hold_is_returned_while_it_is_asked_again(self) -> None:
        with mock.patch.object(addresses, "_lookup", side_effect=self._slow):
            with addresses._mdns_lock:
                addresses._mdns_held["cab.local"] = (
                    time.monotonic() - addresses._MDNS_HOLD_SECONDS - 1, ["10.0.0.4"])
            started = time.monotonic()
            self.assertEqual(addresses._resolved("cab.local"), ["10.0.0.4"])
            self.assertLess(time.monotonic() - started, 0.5)
            time.sleep(0.05)
        self.assertEqual(self.asked, ["cab.local"])

    def test_a_name_that_is_not_mdns_is_not_held(self) -> None:
        with mock.patch.object(addresses, "_lookup", return_value=["10.0.0.4"]) as asked:
            addresses._resolved("cab")
            addresses._resolved("cab")
        self.assertEqual(asked.call_count, 2)


class ResolvingTests(unittest.TestCase):
    """The screens that show the list ask whether to look again for the name."""

    def setUp(self) -> None:
        addresses.forget()
        self.addCleanup(addresses.forget)

    def test_a_name_that_has_not_answered_is_resolving(self) -> None:
        with mock.patch.object(addresses, "_hostname", return_value="cab"):
            self.assertTrue(addresses.resolving())

    def test_a_name_that_answered_is_not(self) -> None:
        with mock.patch.object(addresses, "_hostname", return_value="cab"):
            with addresses._mdns_lock:
                addresses._mdns_held["cab.local"] = (time.monotonic(), ["192.168.1.20"])
            self.assertFalse(addresses.resolving())

    def test_a_name_that_answered_with_nothing_is_not_either(self) -> None:
        """No answer is an answer: a device with no mDNS responder is not worth waiting on."""
        with mock.patch.object(addresses, "_hostname", return_value="cab"):
            with addresses._mdns_lock:
                addresses._mdns_held["cab.local"] = (time.monotonic(), [])
            self.assertFalse(addresses.resolving())

    def test_a_device_with_no_name_is_not(self) -> None:
        with mock.patch.object(addresses, "_hostname", return_value=""):
            self.assertFalse(addresses.resolving())

    def test_a_name_that_already_ends_in_local_is_waited_for_as_it_is(self) -> None:
        with mock.patch.object(addresses, "_hostname", return_value="cab.local"):
            self.assertTrue(addresses.resolving())
            with addresses._mdns_lock:
                addresses._mdns_held["cab.local"] = (time.monotonic(), [])
            self.assertFalse(addresses.resolving())


class WarmTests(unittest.TestCase):
    def setUp(self) -> None:
        addresses.forget()
        self.addCleanup(addresses.forget)
        addresses._watching = False
        self.addCleanup(setattr, addresses, "_watching", False)

    def test_warming_asks_for_this_machines_name_and_only_once(self) -> None:
        with mock.patch.object(addresses, "_hostname", return_value="cab"), \
                mock.patch.object(addresses, "_watch_network"), \
                mock.patch.object(addresses, "_resolved_by_mdns") as asked:
            addresses.warm()
            addresses.warm()

        asked.assert_called_once_with("cab.local")

    def test_a_network_change_forgets_the_answers_and_asks_again(self) -> None:
        class StopError(Exception):
            pass

        with mock.patch.object(addresses, "_hostname", return_value="cab"), \
                mock.patch.object(addresses, "primary_ipv4",
                                  side_effect=["192.168.1.20", "192.168.1.20", "10.0.0.4"]), \
                mock.patch.object(addresses.time, "sleep",
                                  side_effect=[None, None, StopError]), \
                mock.patch.object(addresses, "forget") as forgot, \
                mock.patch.object(addresses, "_resolved_by_mdns") as asked:
            with self.assertRaises(StopError):
                addresses._watch_network()

        forgot.assert_called_once_with()
        asked.assert_called_once_with("cab.local")

    def test_a_name_that_already_ends_in_local_is_warmed_as_it_is(self) -> None:
        with mock.patch.object(addresses, "_hostname", return_value="Cab.local"), \
                mock.patch.object(addresses, "_watch_network"), \
                mock.patch.object(addresses, "_resolved_by_mdns") as asked:
            addresses.warm()

        asked.assert_called_once_with("Cab.local")


class ForgettingAnAnswerInFlightTests(unittest.TestCase):
    """A lookup that was running when the network changed answers for the old network."""

    def setUp(self) -> None:
        addresses.forget()
        self.addCleanup(addresses.forget)
        self.release = threading.Event()
        self.addCleanup(self.release.set)

    def test_the_old_answer_is_dropped_and_the_name_is_asked_again(self) -> None:
        answers = [["10.0.0.4"], ["192.168.1.20"]]
        gate = [self.release, threading.Event()]
        asked: list[str] = []

        def lookup(name: str) -> list[str]:
            asked.append(name)
            number = len(asked) - 1
            gate[number].wait(5)
            return answers[number]

        with mock.patch.object(addresses, "_lookup", side_effect=lookup):
            addresses._resolved("cab.local")
            addresses.forget()
            addresses._resolved("cab.local")
            self.release.set()
            gate[1].set()
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and not addresses._resolved("cab.local"):
                time.sleep(0.01)
            time.sleep(0.05)
            self.assertEqual(addresses._resolved("cab.local"), ["192.168.1.20"])
        self.assertEqual(asked, ["cab.local", "cab.local"])


class ToScanTests(unittest.TestCase):
    def test_the_address_is_scanned_where_the_name_leads(self) -> None:
        with mock.patch.object(addresses, "served_names", return_value=[]):
            self.assertEqual(
                addresses.to_scan(["http://cab.local:8001/x", "http://192.168.1.20:8001/x",
                                   "http://localhost:8001/x"]),
                "http://192.168.1.20:8001/x")

    def test_nothing_to_scan_says_nothing(self) -> None:
        self.assertEqual(addresses.to_scan([]), "")


class FrontendRemoteLinkTests(unittest.TestCase):
    """The main menu's QR is drawn from the same list, but it carries the address: a
    phone resolves that without help, and the name stays in the list to read."""

    def test_the_qr_carries_the_address_and_the_pass(self) -> None:
        from common.devices import serving
        from common.security import access_keys
        from frontend import config_api

        with mock.patch.object(serving, "reachable_from_elsewhere", return_value=True), \
                mock.patch.object(access_keys, "remote_pass", return_value=("id", "s3cret")), \
                mock.patch.object(addresses, "hosts",
                                  return_value=["cab", "192.168.1.20", "localhost"]), \
                mock.patch.object(addresses, "scheme", return_value="http"), \
                mock.patch.object(addresses, "served_names", return_value=[]):
            link = config_api.get_remote_link(configparser.ConfigParser())

        self.assertEqual(link["url"],
                         f"http://192.168.1.20:8001/remote?{access_keys.PASS_PARAM}=s3cret")
        self.assertEqual(link["urls"][0],
                         f"http://cab:8001/remote?{access_keys.PASS_PARAM}=s3cret")
        self.assertEqual(len(link["urls"]), 3)


class FrontendManagerLinkTests(unittest.TestCase):
    """The main menu's Manager code opens the Manager route, with no pass in it."""

    def _link(self, *, reachable: bool, resolving: bool = False) -> dict:
        from common.devices import serving
        from frontend import config_api

        with mock.patch.object(serving, "reachable_from_elsewhere", return_value=reachable), \
                mock.patch.object(addresses, "hosts",
                                  return_value=["cab", "192.168.1.20", "localhost"]), \
                mock.patch.object(addresses, "resolving", return_value=resolving), \
                mock.patch.object(addresses, "scheme", return_value="http"), \
                mock.patch.object(addresses, "served_names", return_value=[]):
            return config_api.get_manager_link(configparser.ConfigParser())

    def test_the_name_leads_the_list_and_the_address_is_scanned(self) -> None:
        link = self._link(reachable=True)

        self.assertEqual(link["url"], "http://192.168.1.20:8001/manager")
        self.assertEqual(link["urls"], ["http://cab:8001/manager",
                                        "http://192.168.1.20:8001/manager",
                                        "http://localhost:8001/manager"])
        self.assertIn("<svg", link["qr_svg"])

    def test_an_install_that_answers_only_itself_offers_nothing(self) -> None:
        self.assertEqual(self._link(reachable=False, resolving=True),
                         {"url": "", "urls": [], "qr_svg": "", "resolving": False})

    def test_the_link_says_while_the_name_is_still_being_asked_for(self) -> None:
        self.assertTrue(self._link(reachable=True, resolving=True)["resolving"])
        self.assertFalse(self._link(reachable=True, resolving=False)["resolving"])

    def _scanned(self, *, scheme: str, vouched: list[str]) -> str:
        from common.devices import serving
        from frontend import config_api

        with mock.patch.object(serving, "reachable_from_elsewhere", return_value=True), \
                mock.patch.object(addresses, "hosts",
                                  return_value=["cab", "192.168.1.20", "localhost"]), \
                mock.patch.object(addresses, "scheme", return_value=scheme), \
                mock.patch.object(addresses, "served_names", return_value=vouched):
            return config_api.get_manager_link(configparser.ConfigParser())["url"]

    def test_https_scans_the_address_only_when_the_certificate_covers_it(self) -> None:
        """An address the certificate does not name opens on a warning, so the name stays."""
        self.assertEqual(self._scanned(scheme="https", vouched=["cab"]),
                         "https://cab:8001/manager")
        self.assertEqual(self._scanned(scheme="https", vouched=["cab", "192.168.1.20"]),
                         "https://192.168.1.20:8001/manager")
