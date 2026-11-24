"""What the Details tab says about a device, and what the Devices grid holds for it.

Pure: the rows are built without drawing, so each label and each value is read as data.
"""

import asyncio
import unittest
from unittest import mock

from common.devices import device_client
from manager.devices import devices
from manager.kit import panel, renderers

LOCAL = "Aaaa111111"
OTHER = "Bbbb222222"


def _context(device_id: str = OTHER, **device: object) -> dict:
    return {"device": {"device_id": device_id, "kind": "vpinfe", **device},
            "local_device_id": LOCAL, "reach": None, "library": None}


def _labels(rows: list) -> list:
    return [label for label, _value in rows]


class ConnectionRowsTests(unittest.TestCase):
    def test_connection_reads_state_last_seen_address_then_first_seen(self) -> None:
        rows = devices._connection_rows(_context(), "10.0.0.4")

        self.assertEqual([label for label in _labels(rows) if isinstance(label, str)],
                         ["State", "Last seen", "Address", "First seen"])

    def test_there_is_no_announced_row_and_no_entry_heading(self) -> None:
        shown = [str(label) for label in _labels(devices._connection_rows(_context(), ""))]

        self.assertNotIn("Announced", shown)
        self.assertNotIn("This entry", shown)

    def test_the_address_is_the_one_given(self) -> None:
        rows = dict(devices._connection_rows(_context(), "10.0.0.4"))

        self.assertEqual(rows["Address"], "10.0.0.4")

    def test_no_address_says_not_known(self) -> None:
        rows = dict(devices._connection_rows(_context(), ""))

        self.assertEqual(rows["Address"], "Not known")

    def test_last_seen_is_there_for_a_device_that_answers(self) -> None:
        context = {**_context(), "reach": {"state": device_client.ANSWERING}}

        self.assertIn("Last seen", _labels(devices._connection_rows(context, "")))

    def test_the_reason_stays_under_a_state_that_is_not_on(self) -> None:
        context = {**_context(), "reach": {"state": device_client.UNREACHABLE,
                                           "reason": "It did not answer in time"}}
        rows = devices._connection_rows(context, "")

        self.assertIs(_labels(rows)[1], panel.ASIDE)

    def test_first_seen_carries_the_note_about_this_install(self) -> None:
        rows = devices._connection_rows(_context(LOCAL), "")

        self.assertEqual(_labels(rows)[-2], "First seen")
        self.assertIs(_labels(rows)[-1], panel.ASIDE)


class PairingRowTests(unittest.TestCase):
    def rows(self, reach: dict) -> list:
        context = {**_context(address="10.0.0.4", port=8001, https=True), "reach": reach}
        return devices._pairing_rows(context)

    def test_an_answering_install_that_is_not_paired_offers_the_door(self) -> None:
        rows = self.rows({"state": device_client.ANSWERING, "paired": False})

        self.assertEqual(_labels(rows), ["Access"])

    def test_a_changed_certificate_keeps_the_door_open_though_it_does_not_answer(self) -> None:
        rows = self.rows({"state": device_client.UNREACHABLE, "paired": False,
                          "certificate_changed": True})

        self.assertEqual(_labels(rows), ["Access"])

    def test_an_install_that_does_not_answer_for_any_other_reason_offers_nothing(self) -> None:
        self.assertEqual(self.rows({"state": device_client.UNREACHABLE, "paired": False}), [])


class OwnAddressTests(unittest.TestCase):
    def test_it_is_the_first_host_that_is_not_loopback(self) -> None:
        with mock.patch("common.host.addresses.hosts",
                        return_value=["192.168.1.9", "cab", "localhost"]):
            self.assertEqual(devices.own_address(), "192.168.1.9")

    def test_nothing_but_loopback_is_no_address(self) -> None:
        with mock.patch("common.host.addresses.hosts", return_value=["localhost"]):
            self.assertEqual(devices.own_address(), "")


class DetailGroupsTests(unittest.TestCase):
    def test_the_tab_is_identity_then_connection_then_settings(self) -> None:
        context = {**_context(LOCAL, address="10.0.0.4"), "library": None}
        with mock.patch("manager.devices.devices.own_address", return_value="10.0.0.4"), \
                mock.patch("manager.devices.devices.settings_door", return_value=[]):
            rows = asyncio.run(devices.detail_groups(context))

        headings = [value for label, value in rows if label is panel.HEADING]
        self.assertEqual(headings, ["Identity", "Connection", "Settings"])
        self.assertNotIn("Kind", [label for label, _ in rows])

    def test_this_device_shows_its_own_address(self) -> None:
        context = _context(LOCAL, address="")
        with mock.patch("manager.devices.devices.own_address", return_value="10.0.0.4"), \
                mock.patch("manager.devices.devices.settings_door", return_value=[]):
            rows = dict(asyncio.run(devices.detail_groups(context)))

        self.assertEqual(rows["Address"], "10.0.0.4")

    def test_another_device_shows_the_address_the_registry_holds(self) -> None:
        context = _context(OTHER, address="10.0.0.7")
        with mock.patch("manager.devices.devices.settings_door", return_value=[]):
            rows = dict(asyncio.run(devices.detail_groups(context)))

        self.assertEqual(rows["Address"], "10.0.0.7")

    def test_another_devices_name_is_text_not_a_field(self) -> None:
        context = _context(OTHER, display_name="basement cab")
        with mock.patch("manager.devices.devices.settings_door", return_value=[]):
            rows = dict(asyncio.run(devices.detail_groups(context)))

        self.assertEqual(rows["Name"], "basement cab")


class GridRowTests(unittest.TestCase):
    def test_a_row_holds_the_version_the_probe_reported(self) -> None:
        found = [{"device_id": OTHER, "kind": "vpinfe", "display_name": "cab"}]
        reach = {OTHER: {"state": device_client.ANSWERING, "version": "3.0.1"}}

        self.assertEqual(devices.rows(found, reach, LOCAL)[0]["version"], "3.0.1")

    def test_a_phone_has_no_version(self) -> None:
        found = [{"device_id": OTHER, "kind": "vpx_mobile"}]
        reach = {OTHER: {"state": device_client.ANSWERING, "version": ""}}

        self.assertEqual(devices.rows(found, reach, LOCAL)[0]["version"], "")

    def test_features_are_a_list_without_core(self) -> None:
        found = [{"device_id": OTHER, "kind": "vpinfe",
                  "features": ["core", "library", "frontend"]}]

        self.assertEqual(devices.rows(found, None, LOCAL)[0]["features"],
                         ["library", "frontend"])


class GridColumnTests(unittest.TestCase):
    def test_features_are_drawn_as_chips_with_their_own_looks(self) -> None:
        column = next(one for one in devices.COLUMNS if one["field"] == "features")

        self.assertEqual(column["cellRendererParams"]["looks"], renderers.FEATURE_LOOKS)
        self.assertIn(renderers.FEATURE_LOOKS, renderers.LOOKS)

    def test_the_version_column_replaces_running(self) -> None:
        fields = [one["field"] for one in devices.COLUMNS]

        self.assertIn("version", fields)
        self.assertNotIn("what", fields)


class ProbeTests(unittest.TestCase):
    def test_this_install_reports_its_version(self) -> None:
        found = device_client.LocalDevice().probe()

        self.assertEqual(found["state"], device_client.ANSWERING)
        self.assertTrue(found["version"])
        self.assertNotIn("what", found)

    def test_a_phone_reports_no_version(self) -> None:
        with mock.patch("common.security.http_client.get_json", return_value={}):
            found = device_client.MobileDevice("http://10.0.0.5:8080").probe()

        self.assertEqual(found["version"], "")


if __name__ == "__main__":
    unittest.main()
