"""Where this install looks for entries: the list, and what the disk says about each."""

from __future__ import annotations

import json
import os
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from common import mounts
from common.config import path_state
from common.config.config_store import ConfigStore
from common.devices.device_registry import DeviceRegistry
from common.games import library_folders as locations
from common.games.library_folders import (
    KIND_GAME,
    KIND_ROOT,
    Location,
    LocationStore,
    state_of,
)
from tests.support.hung_folder import never_answering
from tests.support.skips import needs_posix_permissions


class _WithStore:
    """Shared setup. Not a TestCase, so subclassing it does not re-run its tests."""

    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)  # type: ignore[attr-defined]
        self.root = Path(self.tmp.name)
        self.folder = self.root / "config"
        self.store = LocationStore(self.folder)

    def _write_target(self) -> Location:
        target = self.store.write_to()
        assert target is not None
        return target

    def _held(self, location_id: str, store: LocationStore | None = None) -> Location:
        found = (store or self.store).get(location_id)
        assert found is not None
        return found

    def _choose(self, location: Location) -> None:
        self.store.save(self.store.locations(), location.location_id)

    def _location(self, name: str, kind: str = KIND_ROOT) -> Location:
        folder = self.root / name
        folder.mkdir(exist_ok=True)
        return self.store.put(Location(location_id=locations.mint_location_id(),
                                       path=str(folder), kind=kind))


class StoreTests(_WithStore, unittest.TestCase):
    def test_a_missing_file_is_an_empty_list_rather_than_an_error(self) -> None:
        """A first run has none, and that is not a fault."""
        self.assertEqual(LocationStore(self.root / "nope").locations(), [])

    def test_roots_and_single_game_folders_coexist(self) -> None:
        self._location("share")
        self._location("One Table (Bally 1990)", kind=KIND_GAME)

        self.assertEqual(sorted(one.kind for one in self.store.locations()),
                         [KIND_GAME, KIND_ROOT])

    def test_adding_the_same_folder_twice_is_one_row(self) -> None:
        """Two rows for one place would each report their own state."""
        first = self._location("share")
        again = self.store.put(Location(location_id=locations.mint_location_id(),
                                        path=str(self.root / "share")))

        self.assertEqual(len(self.store.locations()), 1)
        self.assertEqual(again.location_id, first.location_id)

    def test_putting_a_held_folder_again_keeps_its_place_in_the_order(self) -> None:
        """The order is the priority, so asking again must not send it to the back."""
        first = self._location("first")
        second = self._location("second")
        self._choose(second)

        again = self.store.put(Location(location_id=locations.mint_location_id(),
                                        path=str(self.root / "first")))

        self.assertEqual([one.location_id for one in self.store.locations()],
                         [first.location_id, second.location_id])
        self.assertEqual(again.location_id, first.location_id)
        self.assertEqual(self._write_target().location_id, second.location_id)

    def test_putting_a_new_folder_puts_it_last(self) -> None:
        first = self._location("first")
        second = self._location("second")

        self.assertEqual([one.location_id for one in self.store.locations()],
                         [first.location_id, second.location_id])

    def test_pointing_a_row_at_another_folder_changes_that_row(self) -> None:
        """An edit names the row by its id; no second row appears under it."""
        first = self._location("first")
        second = self._location("second")
        moved = self.root / "moved"
        moved.mkdir()

        self.store.put(Location(location_id=first.location_id, path=str(moved)))

        self.assertEqual([(one.location_id, one.path) for one in self.store.locations()],
                         [(first.location_id, str(moved)),
                          (second.location_id, second.path)])

    def test_moving_a_row_onto_a_folder_another_row_holds_is_refused(self) -> None:
        first = self._location("first")
        second = self._location("second")

        with self.assertRaises(locations.FolderHeldError) as refused:
            self.store.put(Location(location_id=first.location_id, path=second.path))

        self.assertEqual(refused.exception.held, second)
        self.assertEqual(self.store.locations(), [first, second])

    def test_moving_a_row_drops_the_share_it_was_seen_on(self) -> None:
        held = self.store.put(Location("id", str(self.root / "share")))
        self.store.record_origins({"id": NAS})
        self.assertEqual(self._held("id").origin, NAS)

        moved = self.store.put(Location("id", str(self.root / "elsewhere")))

        self.assertIsNone(moved.origin)
        self.assertEqual(held.location_id, moved.location_id)

    def test_a_second_row_for_one_place_is_dropped_on_read(self) -> None:
        """A file edited by hand can hold it twice."""
        folder = self.root / "share"
        folder.mkdir()
        self.folder.mkdir()
        (self.folder / "vpinfe.json").write_text(json.dumps({"locations": [
            {"id": "a", "path": str(folder), "kind": KIND_ROOT},
            {"id": "b", "path": str(folder), "kind": KIND_ROOT},
        ]}))

        self.assertEqual([one.location_id for one in LocationStore(self.folder).locations()],
                         ["a"])

    def test_a_second_row_under_one_id_is_dropped_on_read(self) -> None:
        """The id keys the state, the write target and the order, so two rows under it
        would each answer for the other."""
        first, second = self.root / "first", self.root / "second"
        first.mkdir()
        second.mkdir()
        self.folder.mkdir()
        (self.folder / "vpinfe.json").write_text(json.dumps({"locations": [
            {"id": "a", "path": str(first), "kind": KIND_ROOT},
            {"id": "a", "path": str(second), "kind": KIND_ROOT},
        ]}))

        self.assertEqual([one.path for one in LocationStore(self.folder).locations()],
                         [str(first)])

    def test_reading_the_list_resolves_no_path(self) -> None:
        folder = self._location("share")
        with patch("os.path.realpath", side_effect=AssertionError("resolved")):
            held = self.store.locations()

        self.assertEqual(held, [folder])

class WriteTargetTests(_WithStore, unittest.TestCase):
    def test_the_named_one_wins(self) -> None:
        self._location("first")
        second = self._location("second")
        self._choose(second)

        self.assertEqual(self._write_target().location_id, second.location_id)

    def test_a_target_no_longer_listed_falls_back_to_the_first_that_works(self) -> None:
        kept = self._location("kept")

        self.store.save([kept], write_to="gone")

        self.assertEqual(self.store.chosen_write_to(), "gone")
        self.assertEqual(self._write_target().location_id, kept.location_id)

    @needs_posix_permissions
    def test_an_unwritable_target_falls_back_to_one_that_works(self) -> None:
        """An install that has never been asked still has to be able to create
        something."""
        readonly = self._location("readonly")
        writable = self._location("writable")
        self._choose(readonly)
        os.chmod(readonly.path, 0o500)
        self.addCleanup(os.chmod, readonly.path, 0o700)

        self.assertEqual(self._write_target().location_id, writable.location_id)

    def test_a_single_game_folder_is_never_the_write_target(self) -> None:
        """A new entry's folder is created inside a root. There is no room in a
        location that is itself one game."""
        self._location("One Table (Bally 1990)", kind=KIND_GAME)

        self.assertIsNone(self.store.write_to())


class StateTests(unittest.TestCase):
    def test_a_folder_that_is_not_there_is_unreachable_with_a_reason(self) -> None:
        state = state_of(Location("id", "/nowhere/at/all"))

        self.assertEqual(state.state, path_state.MISSING)
        self.assertFalse(state.reachable)
        self.assertTrue(state.reason)

    @needs_posix_permissions
    def test_a_read_only_share_is_reachable_and_not_writable(self) -> None:
        """A read-only export is a real case, and it belongs on the row beside
        reachable."""
        with TemporaryDirectory() as tmp:
            os.chmod(tmp, 0o500)
            try:
                state = state_of(Location("id", tmp))
            finally:
                os.chmod(tmp, 0o700)

        self.assertTrue(state.reachable)
        self.assertFalse(state.writable)
        self.assertTrue(state.reason)

    def test_a_working_folder_reports_no_reason(self) -> None:
        with TemporaryDirectory() as tmp:
            state = state_of(Location("id", tmp))

        self.assertTrue(state.reachable and state.writable)
        self.assertEqual(state.reason, "")


class FolderThatDoesNotAnswerTests(_WithStore, unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        patcher = patch.object(path_state, "PROBE_SECONDS", 0.2)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_it_reads_not_answering_within_the_bound(self) -> None:
        hung = Location("hung", "/hung/bound")
        with never_answering(hung.path):
            started = time.monotonic()
            state = state_of(hung)
            took = time.monotonic() - started

        self.assertEqual(state.state, path_state.NOT_ANSWERING)
        self.assertFalse(state.reachable)
        self.assertTrue(state.reason)
        self.assertLess(took, 1.0)

    def test_a_question_still_out_answers_at_once_and_is_not_asked_twice(self) -> None:
        hung = Location("hung", "/hung/once")
        with never_answering(hung.path) as looked:
            state_of(hung)
            started = time.monotonic()
            state = state_of(hung)
            took = time.monotonic() - started

        self.assertEqual(state.state, path_state.NOT_ANSWERING)
        self.assertLess(took, 0.1)
        self.assertEqual(looked.call_count, 1)

    def test_the_others_are_answered_beside_it(self) -> None:
        here = self._location("here")
        hung = Location("hung", "/hung/beside")
        with never_answering(hung.path):
            states = locations.states_of([hung, here])

        self.assertEqual(states["hung"].state, path_state.NOT_ANSWERING)
        self.assertEqual(states[here.location_id].state, path_state.OK)

    def test_new_games_go_to_the_next_folder_that_answers(self) -> None:
        hung = self._location("hung")
        here = self._location("here")
        with never_answering(hung.path):
            started = time.monotonic()
            chosen = self.store.write_to()
            took = time.monotonic() - started

        self.assertEqual(chosen, here)
        self.assertLess(took, 1.0)


NAS = mounts.Origin(mounts.NFS, "nas.lan", "/export/pinball", "/mnt/nas")


class ShareTests(_WithStore, unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        patch.object(locations, "get_location_store", return_value=self.store).start()
        self.addCleanup(patch.stopall)

    def test_a_folder_whose_share_is_not_mounted_reads_not_connected(self) -> None:
        with patch.object(mounts, "where",
                          return_value=mounts.Where(NAS, connected=False)):
            state = state_of(Location("id", "/mnt/nas/not-connected"))

        self.assertEqual(state.state, path_state.NOT_CONNECTED)
        self.assertFalse(state.reachable)
        self.assertEqual(state.origin, NAS)

    def test_the_share_a_folder_is_seen_on_is_kept_for_when_it_is_down(self) -> None:
        folder = self._location("share")
        with patch.object(mounts, "where", return_value=mounts.Where(NAS)):
            self.assertEqual(state_of(folder).state, path_state.OK)
        recorded = self._held(folder.location_id)
        asked = []

        def down(path: str, origin: mounts.Origin | None) -> mounts.Where:
            asked.append(origin)
            return mounts.Where(origin, connected=False)

        with patch.object(mounts, "where", side_effect=down):
            state = state_of(recorded)

        self.assertEqual(asked, [NAS])
        self.assertEqual(state.state, path_state.NOT_CONNECTED)

    def test_the_folder_is_touched_before_the_mount_table_is_read(self) -> None:
        """Touching an automounted share is what mounts it."""
        order = []
        real_exists = Path.exists

        def exists(path: Path) -> bool:
            order.append("touched")
            return real_exists(path)

        def where(path: str, origin: mounts.Origin | None) -> mounts.Where:
            order.append("table")
            return mounts.Where()

        with patch.object(Path, "exists", exists), \
                patch.object(mounts, "where", side_effect=where):
            state_of(Location("id", str(self.root)))

        self.assertEqual(order[:2], ["touched", "table"])

    def test_a_folder_on_this_device_records_no_share(self) -> None:
        folder = self._location("share")
        with patch.object(mounts, "where", return_value=mounts.Where()):
            state_of(folder)

        self.assertIsNone(self._held(folder.location_id).origin)

    def test_changing_what_a_location_holds_keeps_its_share(self) -> None:
        held = self.store.put(Location("id", str(self.root / "share")))
        self.store.record_origins({"id": NAS})
        again = self.store.put(Location(locations.mint_location_id(), held.path,
                                        kind=KIND_GAME))

        self.assertEqual(self._held(again.location_id).origin, NAS)

    def test_a_share_is_kept_on_this_install_and_not_in_the_config(self) -> None:
        """A config file carried to another machine must not name the share the folder
        was seen on here."""
        self.store.put(Location("id", str(self.root / "share")))
        self.store.record_origins({"id": NAS})

        self.assertNotIn("nas.lan", (self.folder / "vpinfe.json").read_text())
        noted = DeviceRegistry(self.folder / "devices.json").self_get("location_origins")
        self.assertEqual(noted["id"]["origin"], NAS.as_dict())
        self.assertEqual(LocationStore(self.folder).locations()[0].origin, NAS)

    def test_a_share_noted_for_a_row_is_not_kept_for_a_path_it_no_longer_has(self) -> None:
        self.store.put(Location("id", str(self.root / "share")))
        self.store.record_origins({"id": NAS})
        registry = DeviceRegistry(self.folder / "devices.json")
        noted = registry.self_get("location_origins")
        noted["id"]["path"] = str(self.root / "somewhere-else")
        registry.set_self(location_origins=noted)

        self.assertIsNone(self._held("id", LocationStore(self.folder)).origin)

    def test_origins_for_rows_that_are_gone_are_forgotten(self) -> None:
        self.store.put(Location("id", str(self.root / "share")))
        self.store.record_origins({"id": NAS})

        self.store.save([])
        self.store.record_origins({})

        registry = DeviceRegistry(self.folder / "devices.json")
        self.assertIsNone(registry.self_get("location_origins", None))


class SeedTests(_WithStore, unittest.TestCase):
    """2.x kept the library in one setting. Converting its ini makes it the first
    location, and the setting is not written back."""

    def _converted(self, ini: str) -> ConfigStore:
        self.folder.mkdir()
        (self.folder / "vpinfe.ini").write_text(ini, encoding="utf-8")
        return ConfigStore(str(self.folder / "vpinfe.json"))

    def test_the_configured_root_becomes_the_first_location(self) -> None:
        games = self.root / "games"
        games.mkdir()

        self._converted(f"[Settings]\ntablerootdir = {games}\n")

        held = LocationStore(self.folder).locations()
        self.assertEqual([one.path for one in held], [str(games)])
        self.assertEqual(held[0].kind, KIND_ROOT)

    def test_and_it_is_where_new_games_go(self) -> None:
        games = self.root / "games"
        games.mkdir()

        self._converted(f"[Settings]\ntablerootdir = {games}\n")

        store = LocationStore(self.folder)
        self.assertEqual(store.chosen_write_to(), store.locations()[0].location_id)
        target = store.write_to()
        assert target is not None
        self.assertEqual(target.path, str(games))

    def test_the_setting_it_read_is_not_written_back(self) -> None:
        games = self.root / "games"
        games.mkdir()

        self._converted(f"[Settings]\ntablerootdir = {games}\n")

        store = ConfigStore(str(self.folder / "vpinfe.json"))
        self.assertEqual(store.value("general", "game_root_dir"), "")

    def test_an_ini_with_no_root_makes_no_location(self) -> None:
        self._converted("[Settings]\n[Display]\nrotation = 90\n")

        store = LocationStore(self.folder)
        self.assertEqual(store.locations(), [])
        self.assertEqual(store.chosen_write_to(), "")

    def test_it_runs_once(self) -> None:
        """Somebody who removes a location should not find it back on the next start."""
        games = self.root / "games"
        games.mkdir()
        self._converted(f"[Settings]\ntablerootdir = {games}\n")
        LocationStore(self.folder).save([])

        self.assertEqual(LocationStore(self.folder).locations(), [])
        self.assertEqual(ConfigStore(str(self.folder / "vpinfe.json")).get_list("locations"), [])


class ConfiguredTests(_WithStore, unittest.TestCase):
    def test_nothing_held_is_no_locations(self) -> None:
        with patch.object(locations, "get_location_store", return_value=self.store):
            self.assertEqual(locations.configured(), [])

    def test_what_the_store_holds_is_what_is_walked(self) -> None:
        held = self._location("share")
        with patch.object(locations, "get_location_store", return_value=self.store):
            self.assertEqual(locations.configured(), [held])


class WritesAreCheckedTests(_WithStore, unittest.TestCase):
    """A write to the list names a folder, an absolute one, that no other row holds."""

    def _refused(self, *items: dict) -> str:
        from common.config.config_schema import ConfigListError

        with self.assertRaises(ConfigListError) as refused:
            ConfigStore(str(self.folder / "vpinfe.json")).set_list("locations", list(items))
        return str(refused.exception)

    def setUp(self) -> None:
        super().setUp()
        self.folder.mkdir()

    def test_a_row_with_no_path_is_refused(self) -> None:
        self.assertTrue(self._refused({"id": "a", "path": "  "}))

    def test_a_relative_path_is_refused(self) -> None:
        """It would name a different folder depending on where the install started."""
        self.assertTrue(self._refused({"id": "a", "path": "testbed/tables"}))

    def test_a_second_row_for_one_place_is_refused_by_name(self) -> None:
        place = self.root / "pinball"
        said = self._refused({"id": "a", "path": str(place)},
                             {"id": "b", "path": f"{place}{os.sep}"})

        self.assertIn("pinball", said)

    def test_a_link_to_a_folder_another_row_holds_is_the_same_place(self) -> None:
        real = self.root / "real"
        real.mkdir()
        try:
            (self.root / "link").symlink_to(real, target_is_directory=True)
        except OSError:
            self.skipTest("this system will not make a symlink")

        said = self._refused({"id": "a", "path": str(real)},
                             {"id": "b", "path": str(self.root / "link")})

        self.assertIn("real", said)


if __name__ == "__main__":
    unittest.main()
