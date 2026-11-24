"""The devices an install knows about.

Keyed by `device_id` because it is the only thing about a device that does not change:
a display name is meant to be renamed and an address moves with DHCP. A registry that
keyed on either would lose track of a device the first time somebody used the feature.

One entry is the degenerate case of many, so nothing here treats a single device
specially - building it single-entry-only would have taken deliberate effort.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from common.devices.device_registry import (
    Device,
    DeviceRegistry,
    browser_said,
    install_url,
    keep_own_row,
    paired_header,
)
from common.security import access_keys, pinned_certificates
from tests.support.library import add_mobile


def _known(registry: DeviceRegistry, device_id: str) -> Device:
    device = registry.get(device_id)
    assert device is not None
    return device


class DeviceRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.registry = DeviceRegistry(Path(self.tmp.name) / "devices.json")

    def test_a_hub_that_has_seen_nobody_has_an_empty_registry(self) -> None:
        self.assertEqual(self.registry.devices(), [])
        self.assertIsNone(self.registry.get("anything"))
        self.assertFalse(self.registry.knows("anything"))

    def test_recording_a_device_makes_it_known(self) -> None:
        self.registry.record("Aaaa111111", display_name="basement cab",
                           features=("library", "frontend"))

        device = _known(self.registry, "Aaaa111111")
        self.assertEqual(device.display_name, "basement cab")
        self.assertEqual(device.features, ("library", "frontend"))
        self.assertTrue(self.registry.knows("Aaaa111111"))

    def test_a_device_heard_from_twice_is_still_one_device(self) -> None:
        """The whole point of keying on the id: a reconnect is not a second device."""
        self.registry.record("Aaaa111111", display_name="cab")
        self.registry.record("Aaaa111111", display_name="cab")

        self.assertEqual(len(self.registry.devices()), 1)

    def test_a_rename_does_not_lose_the_device(self) -> None:
        """`display_name` addresses nothing, which is what makes renaming safe."""
        first = self.registry.record("Aaaa111111", display_name="old name")
        assert first is not None
        self.registry.record("Aaaa111111", display_name="new name")

        devices = self.registry.devices()
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].display_name, "new name")
        self.assertEqual(devices[0].first_seen, first.first_seen,
                         "it is the same device, so it was first seen when it was")

    def test_a_new_address_is_the_same_device(self) -> None:
        """The reason the key is not an address: a phone on DHCP keeps its lease for a
        week and its identity forever. An address is an attribute that gets updated."""
        first = self.registry.record("Aaaa111111", display_name="phone",
                                     address="192.168.1.50")
        assert first is not None
        self.registry.record("Aaaa111111", display_name="phone", address="192.168.1.77")

        devices = self.registry.devices()
        self.assertEqual(len(devices), 1, "one device, not one per address it has held")
        self.assertEqual(devices[0].address, "192.168.1.77")
        self.assertEqual(devices[0].first_seen, first.first_seen)

    def test_what_the_install_owns_is_refreshed_and_what_we_own_is_not(self) -> None:
        """Name, roles and address are a cached copy of what that install last said.
        `first_seen` is ours, and is the one thing a later record must not move.

        The expected timestamp is pinned rather than read back from the first record:
        comparing two values the same code produced moves them together, so a
        `first_seen` that silently resets on every record would still look equal.
        """
        pinned = "2020-01-01T00:00:00Z"
        self.registry.record("Aaaa111111", display_name="a", features=("device",),
                           address="192.168.1.10")
        self._rewrite_first_seen("Aaaa111111", pinned)

        later = self.registry.record("Aaaa111111", display_name="b", features=("library",),
                                   address="192.168.1.99")
        assert later is not None

        self.assertEqual((later.display_name, later.features, later.address),
                         ("b", ("library",), "192.168.1.99"))
        self.assertEqual(later.first_seen, pinned, "a re-record must not move it")
        self.assertNotEqual(later.last_seen, pinned, "but last_seen is now")

    def _rewrite_first_seen(self, device_id: str, when: str) -> None:
        """Put a known timestamp on disk, so the assertion has an outside witness."""
        payload = json.loads(self.registry.path.read_text())
        for entry in payload["devices"]:
            if entry["device_id"] == device_id:
                entry["first_seen"] = when
        self.registry.path.write_text(json.dumps(payload), encoding="utf-8")

    def test_a_registry_holds_more_than_one(self) -> None:
        self.registry.record("Aaaa111111", display_name="cab")
        self.registry.record("Bbbb222222", display_name="desktop")

        self.assertEqual([p.device_id for p in self.registry.devices()],
                         ["Aaaa111111", "Bbbb222222"])

    def test_a_listed_mobile_device_is_a_device_and_stays_one_when_it_answers(self) -> None:
        add_mobile(self.registry, "Pppp444444", "iPhone", "192.168.1.50", 2113)
        self.registry.record_reachable("Pppp444444")

        device = _known(self.registry, "Pppp444444")
        self.assertEqual((device.kind, device.display_name, device.address, device.port),
                         ("vpx_mobile", "iPhone", "192.168.1.50", 2113))
        self.assertTrue(device.last_reachable, "the answer is state kept beside the entry")
        self.assertEqual((device.first_seen, device.last_seen), (device.last_reachable,) * 2,
                         "a mobile device is first seen when it first answers")

    def test_a_mobile_device_is_listed_in_the_configuration_and_not_recorded(self) -> None:
        with self.assertRaises(ValueError):
            self.registry.record("Pppp444444", kind="vpx_mobile", address="192.168.1.50")

        add_mobile(self.registry, "Pppp444444")
        with self.assertRaises(ValueError):
            self.registry.record("Pppp444444", display_name="renamed")

    def test_state_for_a_device_no_longer_listed_is_not_a_device(self) -> None:
        add_mobile(self.registry, "Pppp444444")
        self.registry.record_reachable("Pppp444444")
        store = self.registry.config()
        store.set_list("mobile_devices", [])
        store.save()

        self.assertIsNone(self.registry.get("Pppp444444"))

    def test_forgetting_never_removes_a_listed_mobile_device(self) -> None:
        add_mobile(self.registry, "Pppp444444")

        self.registry.forget("Pppp444444")

        self.assertIsNotNone(self.registry.get("Pppp444444"))

    def test_an_entry_stored_without_a_kind_reads_as_an_install(self) -> None:
        """Losing an entry is worse than mislabelling one, so an unreadable kind falls
        back rather than dropping the device."""
        self.registry.record("Aaaa111111")
        raw = json.loads(self.registry.path.read_text())
        del raw["devices"][0]["kind"]
        self.registry.path.write_text(json.dumps(raw), encoding="utf-8")

        self.assertEqual(_known(self.registry, "Aaaa111111").kind, "vpinfe")

    def test_a_device_with_no_id_is_refused(self) -> None:
        """An id is the entry's identity; without one there is nothing to key on."""
        self.assertIsNone(self.registry.record(""))
        self.assertIsNone(self.registry.record("   "))
        self.assertEqual(self.registry.devices(), [])

    def test_forgetting_a_device_says_whether_there_was_one(self) -> None:
        self.registry.record("Aaaa111111")

        self.assertTrue(self.registry.forget("Aaaa111111"))
        self.assertFalse(self.registry.forget("Aaaa111111"))
        self.assertEqual(self.registry.devices(), [])


class APairedDeviceThatMovesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.registry = DeviceRegistry(Path(self.tmp.name) / "devices.json")
        self.registry.record("Aaaa111111", address="192.168.1.30", port=8001)

    def record(self, **fields) -> None:
        with patch.object(access_keys, "forget_pairing_token") as forget:
            self.registry.record("Aaaa111111", **fields)
        self.forgotten = forget.called

    def test_a_new_address_drops_the_pairing(self) -> None:
        self.record(address="192.168.1.99")

        self.assertTrue(self.forgotten)

    def test_a_new_port_drops_the_pairing(self) -> None:
        self.record(port=9001)

        self.assertTrue(self.forgotten)

    def test_the_same_address_and_port_keep_it(self) -> None:
        self.record(address="192.168.1.30", port=8001, display_name="renamed")

        self.assertFalse(self.forgotten)

    def test_an_update_that_names_no_address_keeps_it(self) -> None:
        self.record(display_name="renamed")

        self.assertFalse(self.forgotten)


class LastReachableTests(unittest.TestCase):
    """When a device was last known to be there, from either direction.

    Announcing is the push half and a probe is the pull half, and both prove the same
    thing. `last_seen` is not the same question: an install announces once at startup,
    so it stops moving while the machine sits there running.
    """

    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.registry = DeviceRegistry(Path(self.tmp.name) / "devices.json")

    def _at(self, device_id: str) -> str:
        return _known(self.registry, device_id).last_reachable

    def test_announcing_counts_as_being_seen(self) -> None:
        self.registry.record("Aaaa111111")

        self.assertTrue(self._at("Aaaa111111"), "it just told us it was there")

    def test_a_probe_moves_it_without_a_new_announcement(self) -> None:
        """The whole reason for the pull half: an install that has been up for a week
        announced once, at the start of it."""
        self.registry.record("Aaaa111111")
        announced = _known(self.registry, "Aaaa111111").last_seen

        self.registry.record_reachable("Aaaa111111", when="2099-01-01T00:00:00Z")

        device = _known(self.registry, "Aaaa111111")
        self.assertEqual(device.last_reachable, "2099-01-01T00:00:00Z")
        self.assertEqual(device.last_seen, announced, "it has not announced again")

    def test_a_device_that_stops_answering_keeps_the_last_time_it_did(self) -> None:
        """Which is what makes it worth sorting on - the value is the age."""
        self.registry.record("Aaaa111111")
        self.registry.record_reachable("Aaaa111111", when="2026-01-01T00:00:00Z")

        self.assertEqual(self._at("Aaaa111111"), "2026-01-01T00:00:00Z")

    def test_probing_something_the_registry_never_heard_of_adds_nothing(self) -> None:
        """An entry is created by announcing or by a person, never by being probed."""
        self.assertIsNone(self.registry.record_reachable("Nope111111"))
        self.assertEqual(self.registry.devices(), [])

    def test_re_announcing_does_not_lose_an_earlier_probe(self) -> None:
        self.registry.record_reachable("Aaaa111111")
        self.registry.record("Aaaa111111")

        self.assertTrue(self._at("Aaaa111111"))

    def test_an_entry_written_before_this_field_reads_as_never_reached(self) -> None:
        self.registry.record("Aaaa111111")
        raw = json.loads(self.registry.path.read_text(encoding="utf-8"))
        for entry in raw["devices"]:
            entry.pop("last_reachable", None)
        self.registry.path.write_text(json.dumps(raw), encoding="utf-8")

        self.assertEqual(self._at("Aaaa111111"), "")


class BrowserTests(unittest.TestCase):
    """What a device's frontend browser plays, as a probe last heard it."""

    SAID = {"state": "no_h264", "name": "Chromium 145.0.7632.0",
            "checked_at": "2026-09-27T12:00:00Z"}

    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.registry = DeviceRegistry(Path(self.tmp.name) / "devices.json")
        self.registry.record("Aaaa111111")

    def _held(self) -> dict:
        return _known(self.registry, "Aaaa111111").browser

    def test_a_probe_keeps_what_the_browser_said(self) -> None:
        self.registry.record_reachable("Aaaa111111", browser=self.SAID)

        self.assertEqual(_known(DeviceRegistry(self.registry.path), "Aaaa111111").browser,
                         self.SAID)

    def test_a_probe_that_heard_nothing_about_it_keeps_the_last_answer(self) -> None:
        self.registry.record_reachable("Aaaa111111", browser=self.SAID)

        self.registry.record_reachable("Aaaa111111")

        self.assertEqual(self._held(), self.SAID)

    def test_an_empty_answer_clears_it(self) -> None:
        self.registry.record_reachable("Aaaa111111", browser=self.SAID)

        self.registry.record_reachable("Aaaa111111", browser={})

        self.assertEqual(self._held(), {})
        self.assertNotIn("browser", _known(self.registry, "Aaaa111111").as_dict())

    def test_announcing_again_keeps_it(self) -> None:
        self.registry.record_reachable("Aaaa111111", browser=self.SAID)

        self.registry.record("Aaaa111111", display_name="cab")

        self.assertEqual(self._held(), self.SAID)

    def test_it_keeps_what_each_format_was_answered(self) -> None:
        said = browser_said({"state": "no_h264", "browser": "Chromium 145.0.7632.0",
                             "formats": {"h264": False, "vp9": True, "hevc": None}})

        self.registry.record_reachable("Aaaa111111", browser=said)

        self.assertEqual(_known(DeviceRegistry(self.registry.path), "Aaaa111111")
                         .browser["formats"], {"h264": False, "vp9": True, "hevc": None})

    def test_a_format_answered_with_anything_but_yes_no_or_null_is_dropped(self) -> None:
        said = browser_said({"state": "plays", "formats": {"h264": "probably",
                                                            "vp9": True}})

        self.assertEqual(said["formats"], {"vp9": True})

    def test_it_keeps_which_library_the_frontend_plays_where_it_said(self) -> None:
        self.assertEqual(browser_said({"state": "plays", "library": "Rrrr111111"})["library"],
                         "Rrrr111111")
        self.assertNotIn("library", browser_said({"state": "plays"}))

    def test_a_library_that_did_not_answer_is_kept_as_empty_not_as_unsaid(self) -> None:
        self.assertEqual(browser_said({"state": "plays", "library": ""})["library"], "")

    def test_an_answer_from_before_formats_keeps_none(self) -> None:
        self.assertEqual(browser_said({"state": "plays"})["formats"], {})


class DeviceRegistryStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "devices.json"

    def test_it_survives_being_reopened(self) -> None:
        DeviceRegistry(self.path).record("Aaaa111111", display_name="cab")

        self.assertEqual(_known(DeviceRegistry(self.path), "Aaaa111111").display_name, "cab")

    def test_the_file_carries_its_own_schema(self) -> None:
        DeviceRegistry(self.path).record("Aaaa111111")

        self.assertEqual(json.loads(self.path.read_text())["schema"], 1)

    def test_an_unreadable_registry_is_empty_rather_than_fatal(self) -> None:
        """An install with a corrupt registry should still start. It has lost who it knew,
        which is recoverable; refusing to run is not."""
        self.path.write_text("{ not json", encoding="utf-8")

        self.assertEqual(DeviceRegistry(self.path).devices(), [])

    def test_a_field_a_newer_build_wrote_is_not_dropped(self) -> None:
        """A downgrade must not silently strip what it does not understand."""
        self.path.write_text(json.dumps({
            "schema": 99,
            "devices": [{"device_id": "Aaaa111111", "something_new": "keep me"}],
        }), encoding="utf-8")

        registry = DeviceRegistry(self.path)
        registry.record("Aaaa111111", display_name="cab")

        self.assertEqual(json.loads(self.path.read_text())["devices"][0]["something_new"],
                         "keep me")

    def test_an_entry_with_no_id_is_skipped_rather_than_crashing(self) -> None:
        self.path.write_text(json.dumps({
            "schema": 1,
            "devices": [{"display_name": "nameless"}, {"device_id": "Aaaa111111"}],
        }), encoding="utf-8")

        self.assertEqual([p.device_id for p in DeviceRegistry(self.path).devices()],
                         ["Aaaa111111"])


class SelfRowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "state" / "devices.json"
        self.registry = DeviceRegistry(self.path)

    def test_a_fresh_file_names_nobody_and_reading_writes_nothing(self) -> None:
        self.assertEqual(self.registry.self_id(), "")
        self.assertFalse(self.path.exists())

    def test_minting_names_this_install_and_files_its_row(self) -> None:
        own = self.registry.ensure_self()

        self.assertEqual(json.loads(self.path.read_text())["self"], own)
        self.assertEqual([row.device_id for row in self.registry.devices()], [own])
        self.assertEqual(DeviceRegistry(self.path).ensure_self(), own)

    def test_what_an_install_keeps_about_itself_is_on_its_row(self) -> None:
        self.registry.set_self(last_table="Taxi", port=8001)

        self.assertEqual(self.registry.self_get("last_table"), "Taxi")
        self.assertEqual(self.registry.self_get("port"), 8001)
        self.assertEqual(self.registry.self_get("absent", "none"), "none")

    def test_a_field_set_to_nothing_is_removed(self) -> None:
        self.registry.set_self(last_table="Taxi")
        self.registry.set_self(last_table=None)

        self.assertIsNone(self.registry.self_get("last_table"))

    def test_recording_other_devices_leaves_self_alone(self) -> None:
        own = self.registry.ensure_self()
        self.registry.record("Aaaa111111", display_name="cab")
        self.registry.forget("Aaaa111111")

        self.assertEqual(self.registry.self_id(), own)

    def test_this_install_cannot_be_forgotten(self) -> None:
        own = self.registry.ensure_self()

        self.assertFalse(self.registry.forget(own))
        self.assertTrue(self.registry.knows(own))


class UnreadableFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "devices.json"
        self.registry = DeviceRegistry(self.path)

    def test_a_file_that_is_not_json_is_kept_before_a_save_replaces_it(self) -> None:
        self.path.write_text("{ torn", encoding="utf-8")

        minted = self.registry.ensure_self()

        kept = [p for p in self.path.parent.iterdir() if ".unreadable-" in p.name]
        self.assertEqual([p.read_text(encoding="utf-8") for p in kept], ["{ torn"])
        self.assertEqual(self.registry.self_id(), minted)

    def test_a_file_that_cannot_be_opened_is_not_written_over(self) -> None:
        own = self.registry.ensure_self()
        before = self.path.read_text(encoding="utf-8")

        with patch.object(Path, "read_text", side_effect=PermissionError("sharing")):
            with self.assertRaises(PermissionError):
                self.registry.record("Aaaa111111", display_name="cab")

        self.assertEqual(self.path.read_text(encoding="utf-8"), before)
        self.assertEqual(self.registry.self_id(), own)


class KeepOwnRowTests(unittest.TestCase):
    """A registry put back from a backup must not make this install somebody else."""

    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.current = Path(self.tmp.name) / "current.json"
        self.incoming = Path(self.tmp.name) / "incoming.json"
        self.here = DeviceRegistry(self.current)
        self.there = DeviceRegistry(self.incoming)
        self.out = Path(self.tmp.name) / "out.json"

    def test_the_running_install_keeps_its_id_and_its_row(self) -> None:
        own = self.here.ensure_self()
        self.here.set_self(last_table="Here")
        other = self.there.ensure_self()
        self.there.record("Pppp444444", display_name="cab")

        keep_own_row(self.incoming, self.current, self.out)

        saved = DeviceRegistry(self.out)
        self.assertEqual(saved.self_id(), own)
        self.assertEqual(saved.self_get("last_table"), "Here")
        self.assertTrue(saved.knows("Pppp444444"), "the rest of the backup is restored")
        self.assertTrue(saved.knows(other), "the old install is a device like any other")
        self.assertEqual(self.there.self_id(), other, "the file it read is left alone")

    def test_a_row_the_backup_holds_for_this_install_is_replaced(self) -> None:
        own = self.here.ensure_self()
        self.here.set_self(last_table="Here")
        self.there.record(own, display_name="stale")
        self.there.set_self(last_table="Old")

        keep_own_row(self.incoming, self.current, self.out)

        saved = DeviceRegistry(self.out)
        self.assertEqual([row.device_id for row in saved.devices()
                          if row.device_id == own], [own])
        self.assertEqual(saved.self_get("last_table"), "Here")

    def test_with_no_identity_here_the_backups_is_not_adopted(self) -> None:
        self.there.ensure_self()

        keep_own_row(self.incoming, self.current, self.out)

        self.assertEqual(DeviceRegistry(self.out).self_id(), "")


class DeviceTests(unittest.TestCase):
    def test_a_device_round_trips_through_its_dict(self) -> None:
        device = Device(device_id="Aaaa111111", display_name="cab",
                        features=("library", "frontend"), address="192.168.1.10",
                        first_seen="2026-01-01T00:00:00Z", last_seen="2026-01-02T00:00:00Z",
                        browser={"state": "plays", "name": "Google Chrome 154.0.8037.58",
                                 "checked_at": "2026-01-02T00:00:00Z"})

        self.assertEqual(Device.from_dict(device.as_dict()), device)



class AnInstallThatChangesSchemeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.registry = DeviceRegistry(Path(self.tmp.name) / "devices.json")
        self.registry.record("Aaaa111111", address="192.168.1.30", port=8001, https=True)

    def record(self, **fields) -> tuple[bool, list]:
        with (patch.object(access_keys, "forget_pairing_token") as forget,
              patch.object(pinned_certificates, "forget") as unpin):
            self.registry.record("Aaaa111111", **fields)
        return forget.called, [call.args for call in unpin.call_args_list]

    def test_the_scheme_it_announced_is_kept(self) -> None:
        self.assertTrue(_known(self.registry, "Aaaa111111").https)
        self.assertEqual(install_url(_known(self.registry, "Aaaa111111").as_dict()),
                         "https://192.168.1.30:8001")

    def test_being_talked_down_to_http_drops_the_pairing_and_the_pin(self) -> None:
        forgotten, unpinned = self.record(https=False)

        self.assertTrue(forgotten)
        self.assertEqual(unpinned, [("192.168.1.30", 8001)])
        self.assertFalse(_known(self.registry, "Aaaa111111").https)

    def test_a_move_drops_the_pin_kept_for_the_old_address(self) -> None:
        forgotten, unpinned = self.record(address="192.168.1.99")

        self.assertTrue(forgotten)
        self.assertEqual(unpinned, [("192.168.1.30", 8001)])

    def test_forgetting_a_device_drops_its_pairing_and_its_pin(self) -> None:
        with (patch.object(access_keys, "forget_pairing_token") as forget,
              patch.object(pinned_certificates, "forget") as unpin):
            self.assertTrue(self.registry.forget("Aaaa111111"))

        forget.assert_called_once_with("Aaaa111111")
        unpin.assert_called_once_with("192.168.1.30", 8001)

    def test_an_update_that_does_not_know_the_scheme_leaves_it(self) -> None:
        forgotten, unpinned = self.record(display_name="renamed")

        self.assertFalse(forgotten)
        self.assertEqual(unpinned, [])
        self.assertTrue(_known(self.registry, "Aaaa111111").https)

    def test_a_token_is_sent_only_over_the_scheme_it_was_paired_over(self) -> None:
        with (patch("common.devices.device_registry.get_device_registry", lambda: self.registry),
              patch.object(access_keys, "pairing_header", lambda _id: {"X": "token"})):
            self.assertEqual(paired_header("https://192.168.1.30:8001/api"), {"X": "token"})
            self.assertIsNone(paired_header("http://192.168.1.30:8001/api"))

    def test_an_entry_with_no_scheme_said_is_plain_http(self) -> None:
        self.assertEqual(install_url({"address": "192.168.1.30", "port": 8001}),
                         "http://192.168.1.30:8001")
        self.assertEqual(install_url({"address": "", "port": 8001}), "")


if __name__ == "__main__":
    unittest.main()
