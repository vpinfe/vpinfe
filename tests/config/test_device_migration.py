"""The mobile device a 2.x ini held, read out as a listed one.

The seed runs while a 2.x ini is converted, so these drive it against a store the way the
conversion does: the keys are put in the parser, the seed runs, and what the file holds
afterwards is what a person gets.
"""

from __future__ import annotations

import os
import unittest
from tempfile import TemporaryDirectory

from common.config.config_store import ConfigStore
from common.devices import device_migration


class SeedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = ConfigStore(os.path.join(self.tmp.name, "vpinfe.json"))

    def _hold(self, **mobile: object) -> None:
        if not self.store.config.has_section("vpxmobile"):
            self.store.config.add_section("vpxmobile")
        for key, value in mobile.items():
            self.store.config.set("vpxmobile", key, str(value))

    def _seed(self) -> list[dict]:
        device_migration.seed(self.store)
        return self.store.get_list("mobile_devices")

    def test_an_address_becomes_a_listed_device(self) -> None:
        self._hold(device_ip="192.168.1.50", device_port=2113)

        device, = self._seed()

        self.assertEqual((device["name"], device["address"], device["port"]),
                         ("VPX Mobile", "192.168.1.50", 2113))
        self.assertTrue(device["id"])

    def test_the_port_defaults_when_it_was_never_set_or_is_not_a_number(self) -> None:
        self._hold(device_ip="192.168.1.50")
        self.assertEqual(self._seed()[0]["port"], 2112)

        self.store.set_list("mobile_devices", [])
        self._hold(device_ip="192.168.1.50", device_port="soon")
        self.assertEqual(self._seed()[0]["port"], 2112)

    def test_the_2x_spelling_is_read_too(self) -> None:
        self._hold(deviceip="10.0.0.9", deviceport=2200)

        device, = self._seed()

        self.assertEqual((device["address"], device["port"]), ("10.0.0.9", 2200))

    def test_both_keys_are_consumed_so_it_never_converts_twice(self) -> None:
        self._hold(device_ip="192.168.1.50", device_port=2112, chunk_size=4096)

        self._seed()

        self.assertFalse(self.store.config.has_option("vpxmobile", "device_ip"))
        self.assertFalse(self.store.config.has_option("vpxmobile", "device_port"))
        self.assertTrue(self.store.config.has_option("vpxmobile", "chunk_size"),
                        "what is not a device stays")

    def test_no_address_lists_nothing_and_still_clears_a_stray_port(self) -> None:
        self._hold(device_port=2200)

        self.assertEqual(self._seed(), [])
        self.assertFalse(self.store.config.has_option("vpxmobile", "device_port"))

    def test_a_scheme_a_path_or_a_port_in_the_address_is_dropped_from_it(self) -> None:
        for typed in ("http://192.168.1.50:2112/", "192.168.1.50:2112", "192.168.1.50"):
            self.store.set_list("mobile_devices", [])
            self._hold(device_ip=typed)
            self.assertEqual(self._seed()[0]["address"], "192.168.1.50", typed)

    def test_a_port_out_of_range_falls_back_to_the_default(self) -> None:
        self._hold(device_ip="192.168.1.50", device_port=70000)

        self.assertEqual(self._seed()[0]["port"], 2112)

    def test_an_address_that_cannot_be_repaired_lists_nothing_and_is_cleared(self) -> None:
        self._hold(device_ip="fe80::1")

        self.assertEqual(self._seed(), [])
        self.assertFalse(self.store.config.has_option("vpxmobile", "device_ip"))

    def test_a_file_with_no_mobile_section_is_left_alone(self) -> None:
        self.assertEqual(self._seed(), [])


if __name__ == "__main__":
    unittest.main()
