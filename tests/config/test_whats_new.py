"""When an install opens the review of an update, and what it holds."""

from __future__ import annotations

import json
import unittest

from common.config.config_store import ConfigStore
from common.devices import device_registry, install_setup
from common.release import migration_ledger, whats_new
from common.release.upgrade_needs import Need
from tests.support.library import TempTree


class NoteStartTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        self.path = str(self.root / "vpinfe.ini")

    def store(self, *, setup_done: bool = True) -> ConfigStore:
        store = ConfigStore(self.path)
        if setup_done:
            install_setup.finish(store)
        return store

    def ran(self, store: ConfigStore, version: str) -> None:
        device_registry.get_device_registry().set_self(**{whats_new.LAST_KEY: version})

    def test_a_new_install_records_its_version_and_opens_nothing(self) -> None:
        store = self.store(setup_done=False)

        whats_new.note_start(store, "v3.0.0")

        self.assertEqual(whats_new.last_version(), "v3.0.0")
        self.assertFalse(whats_new.due(store))

    def test_the_same_version_again_opens_nothing(self) -> None:
        store = self.store()
        self.ran(store, "v3.0.0")
        whats_new.note_start(store, "v3.0.0")

        self.assertFalse(whats_new.due(store))

    def test_a_later_version_opens_the_review_once_from_the_version_it_last_ran(self) -> None:
        store = self.store()
        self.ran(store, "v3.0.0")

        whats_new.note_start(store, "v3.1.0")

        self.assertTrue(whats_new.due(store))
        self.assertEqual(whats_new.since(), "v3.0.0")
        self.assertEqual(whats_new.last_version(), "v3.1.0")

    def test_an_install_with_no_record_of_a_version_opens_it_from_nothing(self) -> None:
        store = self.store()

        whats_new.note_start(store, "v3.1.0")

        self.assertTrue(whats_new.due(store))
        self.assertEqual(whats_new.since(), "")

    def test_skipping_a_review_keeps_the_earliest_version_not_yet_read(self) -> None:
        store = self.store()
        self.ran(store, "v3.0.0")
        whats_new.note_start(store, "v3.1.0")

        whats_new.note_start(store, "v3.2.0")

        self.assertEqual(whats_new.since(), "v3.0.0")

    def test_reading_it_clears_it_until_the_next_update(self) -> None:
        store = self.store()
        self.ran(store, "v3.0.0")
        whats_new.note_start(store, "v3.1.0")

        whats_new.mark_seen()

        self.assertFalse(whats_new.due(store))
        whats_new.note_start(store, "v3.2.0")
        self.assertEqual(whats_new.since(), "v3.1.0")

    def test_a_build_that_is_not_a_release_opens_nothing_and_records_nothing(self) -> None:
        store = self.store()
        self.ran(store, "v3.0.0")

        whats_new.note_start(store, "dev-abc1234")

        self.assertFalse(whats_new.due(store))
        self.assertEqual(whats_new.last_version(), "v3.0.0")

    def test_going_back_to_an_earlier_version_opens_nothing(self) -> None:
        store = self.store()
        self.ran(store, "v3.1.0")

        whats_new.note_start(store, "v3.0.0")

        self.assertFalse(whats_new.due(store))
        self.assertEqual(whats_new.last_version(), "v3.0.0")

    def test_setup_still_pending_is_not_due_whatever_was_recorded(self) -> None:
        store = self.store(setup_done=False)
        device_registry.get_device_registry().set_self(**{whats_new.PENDING_KEY: True})

        self.assertFalse(whats_new.due(store))

    def test_it_survives_a_reload(self) -> None:
        store = self.store()
        self.ran(store, "v3.0.0")
        whats_new.note_start(store, "v3.1.0")

        device_registry.reset_for_tests(self.registry_path)

        self.assertTrue(whats_new.due(ConfigStore(self.path)))
        payload = json.loads((self.root / "vpinfe.json").read_text(encoding="utf-8"))
        self.assertNotIn("last_version", payload["settings"]["device"])
        row = json.loads((self.registry_path).read_text(encoding="utf-8"))["devices"]
        self.assertEqual([one["last_version"] for one in row], ["v3.1.0"])


class ReviewTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        self.config = ConfigStore(str(self.root / "vpinfe.ini"))
        install_setup.finish(self.config)
        self.folder = self.root / "releases"
        self.folder.mkdir()
        first = "v3.0.0"
        (self.folder / f"{first}.md").write_text("### Summary\nFirst.\n", encoding="utf-8")
        self.current = self.root / "RELEASE_NOTES.md"
        self.current.write_text("### Summary\nSecond.\n", encoding="utf-8")
        self.needs = (Need("pick_thing", "v3.1.0", lambda config: False),)

    def review(self) -> whats_new.Review:
        return whats_new.review(self.config, "v3.1.0", folder=self.folder,
                                current=self.current, needs=self.needs)

    def test_it_holds_the_notes_the_files_and_the_needs_since_the_last_version(self) -> None:
        device_registry.get_device_registry().set_self(**{whats_new.LAST_KEY: "v3.0.0"})
        whats_new.note_start(self.config, "v3.1.0")
        migration_ledger.record("order_direction", "v3.1.0", 2)

        found = self.review()

        self.assertEqual([r.version for r in found.releases], ["v3.1.0"])
        self.assertEqual([e.name for e in found.files], ["order_direction"])
        self.assertEqual([n.key for n in found.needs], ["pick_thing"])

    def test_asked_again_it_is_the_running_version_alone(self) -> None:
        migration_ledger.record("old", "v3.0.0", 1)
        migration_ledger.record("new", "v3.1.0", 2)
        device_registry.get_device_registry().set_self(**{whats_new.SINCE_KEY: "v2.0.0"})

        found = whats_new.review(self.config, "v3.1.0", again=True, folder=self.folder,
                                 current=self.current, needs=self.needs)

        self.assertEqual([r.version for r in found.releases], ["v3.1.0"])
        self.assertEqual([e.name for e in found.files], ["new"])

    def test_a_part_with_nothing_in_it_is_empty_not_missing(self) -> None:
        device_registry.get_device_registry().set_self(**{whats_new.LAST_KEY: "v3.0.0"})
        whats_new.note_start(self.config, "v3.1.0")

        found = self.review()

        self.assertEqual(found.files, [])


if __name__ == "__main__":
    unittest.main()
