"""Putting another file in a table's place, and deleting one table.

What is pinned is what is on disk afterwards and what the record still says: the entry keeps
its id and what a person recorded on it, the old file and whatever is not carried are in
Backups, and a refusal leaves the folder as it was found.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from common import service_errors
from common.backups import folder as backup_folder
from common.backups import ops as backup_ops
from common.backups import places as backup_places
from common.backups import table_entry
from common.games import backglass_order, game_repository, table_ops, table_swap
from common.host import launch_state
from tests.support.library import TempTree, fake_game, game_info, write_game
from tests.support.saved import save_record, saved_record

GAME_ID = "Swapped0001"
FOLDER = "Cactus"
OLD = "Cactus 1.1.vpx"
BEING = "Cactus 1.2.vpx"


class SwapCase(TempTree):
    """A game folder named Cactus with two tables: 1.1, which has been played with, and an
    unrelated one beside it."""

    def setUp(self) -> None:
        super().setUp()
        self.backups = self.root / "backups"
        self.backups.mkdir()
        self.running = launch_state.LaunchState()
        self.info = game_info("Cactus", game_id=GAME_ID, tables={
            "t1": {"id": "t1", "filename": OLD, "rating": 4, "hidden": True, "version": "1.1",
                   "tags": ["mine"],
                   "source": {"vps_file_id": "old-file", "confirmed_by": "user"}},
            "t2": {"id": "t2", "filename": "Cactus Other.vpx"},
        })
        self.info["vpinfe"]["default_table"] = OLD
        self.folder = write_game(self.root / "library", FOLDER, info=self.info, vpx=False,
                                 files={OLD: b"old", "Cactus Other.vpx": b"other"})
        self.game = fake_game(self.folder, FOLDER, meta=self.info)
        self.offered = self.root / "offered"
        self.offered.mkdir()
        self.arrives = self.offered / BEING
        self.arrives.write_bytes(b"new")
        for patcher in (
                patch.object(game_repository, "catalog", side_effect=lambda: {GAME_ID: self.game}),
                patch.object(game_repository, "all_games", side_effect=lambda **_: [self.game]),
                patch.object(table_swap, "reread_game", side_effect=lambda game: game),
                patch.object(table_swap, "read_one", return_value={"version": "1.2"}),
                patch.object(table_ops, "row_or_refuse",
                             side_effect=lambda game, table_id: {"id": table_id}),
                patch.object(backglass_order, "settle", return_value=[]),
                patch.object(backup_folder, "current", return_value=self.backups),
                patch.object(launch_state, "current", side_effect=lambda: self.running)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def put(self, name: str, data: bytes = b"x") -> Path:
        path = self.folder / name
        path.write_bytes(data)
        return path

    def names(self) -> list[str]:
        return sorted(path.name for path in self.folder.iterdir()
                      if path.is_file() and ".vpinfe-" not in path.name)

    def kept(self) -> list[str]:
        return sorted(path.name.split(".vpinfe-")[0] for path in self.backups.rglob("*")
                      if path.is_file() and ".entry.json" not in path.name)

    def kept_records(self) -> list[str]:
        return sorted(path.name.split(".vpinfe-")[0] for path in self.backups.rglob("*")
                      if path.is_file() and ".entry.json" in path.name)

    def assert_kept(self, *names: str) -> None:
        self.assertEqual(self.kept(), sorted(names))

    def kept_bytes(self) -> list[bytes]:
        return sorted(path.read_bytes() for path in self.backups.rglob("*")
                      if path.is_file() and ".entry.json" not in path.name)

    def record(self) -> dict:
        return saved_record(self.folder / f"{FOLDER}.info")

    def swap(self, **options) -> dict:
        return table_swap.replace(GAME_ID, "t1", str(self.arrives), **options)


class ReplaceTests(SwapCase):
    def test_the_table_keeps_its_id_and_what_a_person_recorded_on_it(self) -> None:
        self.swap()

        entry = self.record()["tables"]["t1"]
        self.assertEqual((entry["id"], entry["filename"]), ("t1", BEING))
        self.assertEqual((entry["rating"], entry["hidden"], entry["tags"]), (4, True, ["mine"]))
        self.assertEqual(entry["version"], "1.2")
        self.assertEqual(self.record()["vpinfe"]["default_table"], "t1")

    def test_the_old_tie_goes_with_the_old_file(self) -> None:
        self.swap()

        self.assertNotIn("source", self.record()["tables"]["t1"])

    def test_the_new_file_is_in_place_and_the_old_one_is_in_backups_only(self) -> None:
        done = self.swap()

        self.assertEqual(self.names(), [
            "Cactus 1.2.info", BEING, "Cactus Other.info", "Cactus Other.vpx", f"{FOLDER}.info"])
        self.assertEqual((self.folder / BEING).read_bytes(), b"new")
        self.assert_kept(OLD)
        self.assertEqual([one["file"] for one in done["kept"]], [OLD])

    def test_the_file_offered_is_copied_not_moved(self) -> None:
        self.swap()

        self.assertTrue(self.arrives.is_file())

    def test_a_file_with_the_same_name_replaces_it_in_place(self) -> None:
        same = self.offered / OLD
        same.write_bytes(b"newer")

        table_swap.replace(GAME_ID, "t1", str(same))

        self.assertEqual((self.folder / OLD).read_bytes(), b"newer")
        self.assert_kept(OLD)
        self.assertEqual(self.kept_bytes(), [b"old"])
        self.assertEqual(self.record()["tables"]["t1"]["filename"], OLD)

    def test_a_same_name_swap_the_entry_refuses_puts_the_old_file_back(self) -> None:
        same = self.offered / OLD
        same.write_bytes(b"newer")

        with patch.object(table_swap.MetaConfig, "swap_table", return_value=False), \
                self.assertRaises(service_errors.RefusedError):
            table_swap.replace(GAME_ID, "t1", str(same))

        self.assertEqual((self.folder / OLD).read_bytes(), b"old")

    def test_a_name_that_differs_only_in_case_refused_by_the_entry_loses_nothing(self) -> None:
        recased = self.offered / OLD.lower()
        recased.write_bytes(b"newer")

        with patch.object(table_swap.MetaConfig, "swap_table", return_value=False), \
                self.assertRaises(service_errors.RefusedError):
            table_swap.replace(GAME_ID, "t1", str(recased))

        self.assertEqual([b"old"], [path.read_bytes() for path in self.folder.iterdir()
                                    if path.name.lower() == OLD.lower()])

    def test_a_file_that_cannot_be_read_leaves_the_entry_to_be_read_again(self) -> None:
        entry = self.info["tables"]["t1"]
        entry.update({"file_hash": "stale", "rom": "oldrom", "detect_pinmame": True})
        save_record(self.folder / f"{FOLDER}.info", self.info)

        with patch.object(table_swap, "read_one", return_value=None):
            self.swap()

        written = self.record()["tables"]["t1"]
        self.assertEqual(written["filename"], BEING)
        for key in ("file_hash", "rom", "detect_pinmame", "version"):
            self.assertNotIn(key, written)

    def test_a_default_stored_as_the_table_id_stays(self) -> None:
        self.info["vpinfe"]["default_table"] = "t1"
        save_record(self.folder / f"{FOLDER}.info", self.info)

        self.swap()

        self.assertEqual(self.record()["vpinfe"]["default_table"], "t1")

    def test_another_table_is_not_touched(self) -> None:
        self.swap()

        self.assertEqual(self.record()["tables"]["t2"],
                         {"id": "t2", "filename": "Cactus Other.vpx"})
        self.assertEqual((self.folder / "Cactus Other.vpx").read_bytes(), b"other")

    def test_the_release_picked_is_tied_afterwards(self) -> None:
        with (patch.object(table_swap, "find_vps_release", return_value={}),
              patch.object(table_swap, "set_table_source") as tie):
            self.swap(vps_file_id="new-file")

        tie.assert_called_once_with(self.game, BEING, "new-file")


class CarryTests(SwapCase):
    def setUp(self) -> None:
        super().setUp()
        self.put("Cactus 1.1.ini", b"ini")
        self.put("Cactus 1.1.directb2s", b"glass")
        self.put("Cactus 1.1.vbs", b"script")
        self.put("Cactus 1.1.pov", b"pov")
        self.put("Cactus.directb2s", b"shared glass")

    def test_an_update_carries_the_settings_and_the_machine_files_but_not_the_script(
            self) -> None:
        done = self.swap()

        self.assertEqual(self.names(), [
            "Cactus 1.2.directb2s", "Cactus 1.2.info", "Cactus 1.2.ini", "Cactus 1.2.pov", BEING,
            "Cactus Other.info", "Cactus Other.vpx", "Cactus.directb2s", f"{FOLDER}.info"])
        self.assertEqual((self.folder / "Cactus 1.2.directb2s").read_bytes(), b"glass")
        self.assert_kept(OLD, "Cactus 1.1.vbs")
        self.assertEqual(len(done["kept"]), 2)

    def test_another_release_carries_only_what_was_made_for_the_machine(self) -> None:
        self.swap(instead=True)

        self.assertEqual(self.names(), [
            "Cactus 1.2.directb2s", "Cactus 1.2.info", BEING, "Cactus Other.info",
            "Cactus Other.vpx", "Cactus.directb2s", f"{FOLDER}.info"])
        self.assert_kept(OLD, "Cactus 1.1.ini", "Cactus 1.1.pov", "Cactus 1.1.vbs")

    def test_what_is_ticked_is_what_is_carried(self) -> None:
        self.swap(carry=["script"])

        self.assertIn("Cactus 1.2.vbs", self.names())
        self.assert_kept(OLD, "Cactus 1.1.directb2s", "Cactus 1.1.ini",
                                       "Cactus 1.1.pov")

    def test_a_file_named_for_the_folder_is_shared_and_stays(self) -> None:
        self.swap(carry=[])

        self.assertEqual((self.folder / "Cactus.directb2s").read_bytes(), b"shared glass")
        self.assertNotIn("Cactus.directb2s", self.kept())

    def test_a_carried_file_wins_over_one_the_new_file_brings(self) -> None:
        (self.offered / "Cactus 1.2.ini").write_bytes(b"theirs")
        (self.offered / "Cactus 1.2.pov").write_bytes(b"their pov")

        self.swap(carry=["ini"])

        self.assertEqual((self.folder / "Cactus 1.2.ini").read_bytes(), b"ini")
        self.assertEqual((self.folder / "Cactus 1.2.pov").read_bytes(), b"their pov")

    def test_what_the_new_file_brings_is_not_carried_over_by_default(self) -> None:
        (self.offered / "Cactus 1.2.ini").write_bytes(b"theirs")

        self.swap()

        self.assertEqual((self.folder / "Cactus 1.2.ini").read_bytes(), b"theirs")
        self.assertIn("Cactus 1.1.ini", self.kept())

    def test_a_new_file_named_for_the_folder_leaves_the_shared_names_alone(self) -> None:
        self.arrives = self.offered / "Cactus.vpx"
        self.arrives.write_bytes(b"new")

        self.swap(carry=["ini", "backglass", "pov"])

        self.assertEqual((self.folder / "Cactus.directb2s").read_bytes(), b"shared glass")
        self.assertNotIn("Cactus.ini", self.names())
        self.assertEqual((self.folder / "Cactus.pov").read_bytes(), b"pov")
        self.assert_kept(OLD, "Cactus 1.1.directb2s", "Cactus 1.1.ini", "Cactus 1.1.vbs")

    def test_a_file_the_new_one_brings_is_placed_where_nothing_is(self) -> None:
        (self.offered / "Cactus 1.2.ini").write_bytes(b"theirs")

        self.swap(carry=[])

        self.assertEqual((self.folder / "Cactus 1.2.ini").read_bytes(), b"theirs")

    def test_a_file_already_named_for_the_new_one_is_kept_before_it_is_replaced(self) -> None:
        self.put("Cactus 1.2.ini", b"stale")

        self.swap(carry=["ini"])

        self.assertEqual((self.folder / "Cactus 1.2.ini").read_bytes(), b"ini")
        self.assertIn("Cactus 1.2.ini", self.kept())
        self.assertIn(b"stale", self.kept_bytes())


class PlanTests(SwapCase):
    def setUp(self) -> None:
        super().setUp()
        for name in ("Cactus 1.1.ini", "Cactus 1.1.directb2s", "Cactus 1.1.vbs"):
            self.put(name)

    def ticked(self, **options) -> dict[str, bool]:
        done = table_swap.plan(GAME_ID, "t1", **options)
        return {one["kind"]: one["carry"] for one in done["files"]}

    def test_an_update_ticks_everything_but_the_script(self) -> None:
        self.assertEqual(self.ticked(), {"backglass": True, "ini": True, "script": False})

    def test_another_release_ticks_only_the_machine_files(self) -> None:
        self.assertEqual(self.ticked(instead=True),
                         {"backglass": True, "ini": False, "script": False})

    def test_a_file_the_new_one_brings_starts_unticked(self) -> None:
        (self.offered / "Cactus 1.2.ini").write_bytes(b"theirs")

        done = table_swap.plan(GAME_ID, "t1", str(self.arrives))

        brought = {one["kind"]: (one["carry"], one["brought"]) for one in done["files"]}
        self.assertEqual(brought["ini"], (False, True))
        self.assertEqual(brought["backglass"], (True, False))

    def test_a_game_that_is_running_can_still_be_asked_what_a_swap_would_do(self) -> None:
        self.running = launch_state.LaunchState(launching=True, game_id=GAME_ID)

        self.assertEqual("t1", table_swap.plan(GAME_ID, "t1")["table"])

    def test_a_new_file_named_for_the_folder_cannot_take_the_shared_kinds(self) -> None:
        (self.offered / "Cactus.vpx").write_bytes(b"new")

        self.assertEqual(self.ticked(source=str(self.offered / "Cactus.vpx")),
                         {"backglass": False, "ini": False, "script": False})

    def test_the_ticks_are_what_a_swap_without_a_choice_carries(self) -> None:
        (self.offered / "Cactus 1.2.ini").write_bytes(b"theirs")
        ticked = {one["kind"] for one in table_swap.plan(GAME_ID, "t1", str(self.arrives))["files"]
                  if one["carry"]}

        self.swap()

        self.assertEqual(ticked, {"backglass"})
        self.assertEqual(sorted(self.kept()), sorted([OLD, "Cactus 1.1.ini", "Cactus 1.1.vbs"]))

    def test_a_table_patched_from_this_one_is_named(self) -> None:
        self.info["tables"]["t2"]["source"] = {"base": {"file": OLD, "hash": "x"}}
        save_record(self.folder / f"{FOLDER}.info", self.info)

        self.assertEqual(table_swap.plan(GAME_ID, "t1")["patched"], ["Cactus Other.vpx"])


class RefusalTests(SwapCase):
    def refused(self, error: type[Exception] = service_errors.RefusedError, **options) -> None:
        before = self.names()
        with self.assertRaises(error):
            self.swap(**options)
        self.assertEqual(self.names(), before)
        self.assert_kept()
        self.assertEqual(self.record()["tables"]["t1"]["filename"], OLD)

    def test_a_file_that_is_not_there(self) -> None:
        self.arrives.unlink()

        self.refused()

    def test_a_file_no_app_plays(self) -> None:
        self.arrives = self.offered / "Cactus 1.2.txt"
        self.arrives.write_bytes(b"x")

        self.refused()

    def test_the_file_the_table_already_is(self) -> None:
        self.arrives = self.folder / OLD

        self.refused()

    def test_a_name_another_file_in_the_game_already_has(self) -> None:
        (self.folder / BEING).write_bytes(b"in the way")

        self.refused(service_errors.BlockedError)

    def test_a_game_that_is_running(self) -> None:
        self.running = launch_state.LaunchState(launching=True, game_id=GAME_ID)

        self.refused(service_errors.BlockedError)

    def test_a_table_the_game_does_not_hold(self) -> None:
        with self.assertRaises(service_errors.RefusedError):
            table_swap.replace(GAME_ID, "nope", str(self.arrives))

    def test_a_table_kept_outside_the_game_folder(self) -> None:
        self.info["tables"]["t1"]["path"] = "../elsewhere/Cactus.vpx"
        save_record(self.folder / f"{FOLDER}.info", self.info)

        with self.assertRaises(service_errors.RefusedError):
            table_swap.replace(GAME_ID, "t1", str(self.arrives))
        with self.assertRaises(service_errors.RefusedError):
            table_swap.delete(GAME_ID, "t1")

        self.assertIn(OLD, self.names())

    def test_a_game_running_under_its_folder_name_is_found_too(self) -> None:
        self.running = launch_state.LaunchState(launching=True, game_name=FOLDER)

        self.refused(service_errors.BlockedError)

    def test_a_release_the_tables_app_does_not_play(self) -> None:
        with patch.object(table_swap, "find_vps_release", return_value={"tableFormat": "FP"}):
            self.refused(vps_file_id="other-format")

    def test_a_backups_folder_that_cannot_be_written(self) -> None:
        with patch.object(backup_folder, "for_writing",
                          side_effect=service_errors.BlockedError("read only")):
            self.refused(service_errors.BlockedError)


class RestoreTests(SwapCase):
    def test_the_old_file_comes_back_from_backups(self) -> None:
        copy = self.swap()["kept"][0]

        backup_ops.restore(copy["id"])

        self.assertEqual((self.folder / OLD).read_bytes(), b"old")
        self.assertEqual((self.folder / BEING).read_bytes(), b"new")

    def test_a_game_that_is_running_is_not_restored_over(self) -> None:
        copy = self.swap()["kept"][0]
        self.running = launch_state.LaunchState(launching=True, game_id=GAME_ID)

        with self.assertRaises(service_errors.BlockedError):
            backup_ops.restore(copy["id"])

        self.assertNotIn(OLD, self.names())


class RestoreADeletedTableTests(SwapCase):
    def deleted(self) -> dict[str, dict]:
        done = table_swap.delete(GAME_ID, "t1")
        return {one["file"]: one for one in done["kept"]}

    def test_restoring_the_file_brings_the_record_back_with_its_id(self) -> None:
        copies = self.deleted()

        backup_ops.restore(copies[OLD]["id"])

        self.assertEqual((self.folder / OLD).read_bytes(), b"old")
        brought = self.record()["tables"]["t1"]
        self.assertEqual((brought["filename"], brought["rating"], brought["hidden"],
                          brought["tags"]), (OLD, 4, True, ["mine"]))

    def test_a_record_that_exists_is_left_as_it_is(self) -> None:
        copies = self.deleted()
        self.info["tables"] = {"t9": {"id": "t9", "filename": OLD, "rating": 1}}
        save_record(self.folder / f"{FOLDER}.info", self.info)

        backup_ops.restore(copies[OLD]["id"])

        tables = self.record()["tables"]
        self.assertNotIn("t1", tables)
        self.assertEqual(tables["t9"]["rating"], 1)

    def test_restoring_a_replaced_tables_copy_leaves_the_record_that_is_there(self) -> None:
        done = table_swap.replace(GAME_ID, "t1", str(self.arrives))
        before = self.record()["tables"]["t1"]

        backup_ops.restore(done["kept"][0]["id"])

        self.assertEqual(self.record()["tables"]["t1"], before)

    def test_a_file_copy_with_no_record_beside_it_brings_back_only_the_file(self) -> None:
        copies = self.deleted()
        for record in self.backups.rglob("*.entry.json.vpinfe-*"):
            record.unlink()

        backup_ops.restore(copies[OLD]["id"])

        self.assertEqual((self.folder / OLD).read_bytes(), b"old")
        self.assertNotIn("t1", self.record()["tables"])

    def test_a_record_is_found_for_a_name_with_brackets(self) -> None:
        stamped = "20260101T000000Z"
        file_copy = self.backups / f"Cactus [VPW].vpx.vpinfe-{stamped}-delete"
        entry = self.backups / f"Cactus [VPW].vpx.entry.json.vpinfe-{stamped}-delete"
        file_copy.write_bytes(b"x")
        entry.write_text("{}")

        self.assertEqual(table_entry.beside_path(file_copy), entry)

    def test_the_record_is_not_a_copy_of_its_own(self) -> None:
        copies = self.deleted()

        self.assertEqual(set(copies), {OLD})
        listed = {one.file for one in backup_places.scan()}
        self.assertNotIn(f"{OLD}.entry.json", listed)

    def test_a_copy_names_the_table_it_was_of(self) -> None:
        copies = self.deleted()

        self.assertEqual(copies[OLD]["label"], "1.1")

    def test_a_replaced_tables_copy_names_it_too(self) -> None:
        done = table_swap.replace(GAME_ID, "t1", str(self.arrives))

        self.assertEqual(done["kept"][0]["label"], "1.1")

    def test_a_file_named_for_the_table_has_no_name_of_its_own_to_show(self) -> None:
        self.put("Cactus 1.1.ini")
        done = table_swap.delete(GAME_ID, "t1")

        ini = next(one for one in done["kept"] if one["file"] == "Cactus 1.1.ini")
        self.assertEqual(ini["label"], "")

    def test_removing_the_file_copy_removes_its_record(self) -> None:
        copies = self.deleted()

        backup_ops.delete(copies[OLD]["id"])

        self.assertEqual(self.kept_records(), [])

    def test_removing_one_tables_copy_leaves_another_tables_record(self) -> None:
        copies = self.deleted()
        other = {one["file"]: one for one in table_swap.delete(GAME_ID, "t2")["kept"]}

        backup_ops.delete(copies[OLD]["id"])

        self.assertEqual(self.kept_records(), ["Cactus Other.vpx.entry.json"])
        self.assertIn("Cactus Other.vpx", other)


class DeleteTests(SwapCase):
    def test_the_table_and_its_files_go_and_are_kept(self) -> None:
        self.put("Cactus 1.1.ini")
        self.put("Cactus.directb2s")

        done = table_swap.delete(GAME_ID, "t1")

        self.assertEqual(self.names(), ["Cactus Other.vpx", "Cactus.directb2s", f"{FOLDER}.info"])
        self.assert_kept(OLD, "Cactus 1.1.ini")
        self.assertEqual(self.kept_records(), [f"{OLD}.entry.json"])
        self.assertEqual(len(done["kept"]), 2)

    def test_the_entry_goes_and_the_default_is_no_longer_this_table(self) -> None:
        table_swap.delete(GAME_ID, "t1")

        record = self.record()
        self.assertNotIn("t1", record["tables"])
        self.assertEqual(record["vpinfe"]["default_table"], "")
        self.assertIn("t2", record["tables"])

    def test_a_default_stored_as_the_table_id_is_cleared(self) -> None:
        self.info["vpinfe"]["default_table"] = "t1"
        save_record(self.folder / f"{FOLDER}.info", self.info)

        table_swap.delete(GAME_ID, "t1")

        self.assertEqual(self.record()["vpinfe"]["default_table"], "")

    def test_every_copy_says_it_was_made_before_a_delete(self) -> None:
        done = table_swap.delete(GAME_ID, "t1")

        self.assertEqual({"delete"}, {one["reason"] for one in done["kept"]})

    def test_the_kept_copy_is_the_file_as_it_was(self) -> None:
        done = table_swap.delete(GAME_ID, "t1")

        copy = next(one for one in done["kept"] if one["file"] == OLD)
        self.assertEqual(Path(copy["path"]).read_bytes(), b"old")

    def test_the_record_is_kept_with_the_file(self) -> None:
        done = table_swap.delete(GAME_ID, "t1")

        copy = next(one for one in done["kept"] if one["file"] == OLD)
        kept = table_entry.beside(Path(copy["path"]))
        assert kept is not None
        self.assertEqual((kept["id"], kept["rating"], kept["hidden"], kept["tags"]),
                         ("t1", 4, True, ["mine"]))

    def test_a_record_that_cannot_be_written_is_a_refusal_and_the_game_is_read_again(self) -> None:
        with patch.object(table_swap.MetaConfig, "drop_table", side_effect=OSError("read only")), \
                patch.object(table_swap, "reread_game") as reread, \
                self.assertRaises(service_errors.BlockedError):
            table_swap.delete(GAME_ID, "t1")

        reread.assert_called_once()

    def test_the_last_table_leaves_the_game_with_its_settings(self) -> None:
        table_swap.delete(GAME_ID, "t2")
        table_swap.delete(GAME_ID, "t1")

        self.assertEqual(self.record()["tables"], {})
        self.assertEqual(self.names(), [f"{FOLDER}.info"])

    def test_a_game_that_is_running_keeps_its_table(self) -> None:
        self.running = launch_state.LaunchState(launching=True, game_id=GAME_ID)

        with self.assertRaises(service_errors.BlockedError):
            table_swap.delete(GAME_ID, "t1")

        self.assertIn(OLD, self.names())

    def test_nothing_is_deleted_when_backups_cannot_be_written(self) -> None:
        with patch.object(backup_folder, "for_writing",
                          side_effect=service_errors.BlockedError("read only")), \
                self.assertRaises(service_errors.BlockedError):
            table_swap.delete(GAME_ID, "t1")

        self.assertIn(OLD, self.names())
        self.assertIn("t1", self.record()["tables"])


class UndoTests(SwapCase):
    def test_the_old_file_is_back_in_the_tables_place_and_the_new_one_is_kept(self) -> None:
        kept = self.swap()["kept"]

        done = table_swap.undo(GAME_ID, "t1", [one["id"] for one in kept])

        self.assertEqual((self.folder / OLD).read_bytes(), b"old")
        self.assertNotIn(BEING, self.names())
        self.assertEqual(self.record()["tables"]["t1"]["filename"], OLD)
        self.assertEqual(done["table"]["id"], "t1")
        self.assertIn(BEING, self.kept())

    def test_what_followed_the_table_follows_it_back_and_what_stayed_in_backups_returns(
            self) -> None:
        self.put("Cactus 1.1.ini")
        self.put("Cactus 1.1.vbs")
        kept = self.swap()["kept"]
        self.assertIn("Cactus 1.2.ini", self.names())

        table_swap.undo(GAME_ID, "t1", [one["id"] for one in kept])

        self.assertEqual(self.names(), sorted([
            OLD, "Cactus 1.1.info", "Cactus 1.1.ini", "Cactus 1.1.vbs", "Cactus Other.info",
            "Cactus Other.vpx", f"{FOLDER}.info"]))

    def test_the_entry_is_the_same_one_throughout(self) -> None:
        kept = self.swap()["kept"]

        table_swap.undo(GAME_ID, "t1", [one["id"] for one in kept])

        entry = self.record()["tables"]["t1"]
        self.assertEqual((entry["rating"], entry["hidden"], entry["tags"]),
                         (4, True, ["mine"]))

    def test_the_tie_the_swap_took_away_is_given_back(self) -> None:
        done = self.swap()
        self.assertIsNone(self.record()["tables"]["t1"].get("source"))

        table_swap.undo(GAME_ID, "t1", [one["id"] for one in done["kept"]], done["source"])

        self.assertEqual({"vps_file_id": "old-file", "confirmed_by": "user"},
                         self.record()["tables"]["t1"]["source"])

    def test_copies_that_are_gone_are_not_found(self) -> None:
        kept = self.swap()["kept"]
        for path in self.backups.rglob("*"):
            if path.is_file():
                path.unlink()

        with self.assertRaises(service_errors.NotFoundError):
            table_swap.undo(GAME_ID, "t1", [one["id"] for one in kept])

        self.assertIn(BEING, self.names())

    def test_a_copy_of_another_game_is_not_taken(self) -> None:
        kept = self.swap()["kept"]

        with self.assertRaises(service_errors.NotFoundError):
            table_swap.undo("Another0001", "t1", [one["id"] for one in kept])


class NamingTests(SwapCase):
    def test_a_table_is_its_version_and_authors_from_the_release_it_is_tied_to(self) -> None:
        with patch.object(table_swap, "find_vps_release",
                          return_value={"version": "1.1.0", "authors": ["Rascal", "Mfuegemann"]}):
            self.assertEqual(table_swap.describe(self.info["tables"]["t1"], OLD),
                             "1.1.0 · Rascal, Mfuegemann")

    def test_a_long_author_list_names_the_first_two_as_the_catalog_does(self) -> None:
        release = {"version": "2.3", "authors": ["Dark", "G5k", "BorgDog", "Rawd"]}
        with patch.object(table_swap, "find_vps_release", return_value=release):
            self.assertEqual(table_swap.describe(self.info["tables"]["t1"], OLD), "2.3 · Dark, G5k")

    def test_without_a_release_the_files_own_version_and_authors_name_it(self) -> None:
        entry = {"version": "1.1", "authors": "Rascal", "source": {}}
        with patch.object(table_swap, "find_vps_release", return_value={}):
            self.assertEqual(table_swap.describe(entry, OLD), "1.1 · Rascal")

    def test_without_either_the_file_name_does(self) -> None:
        with patch.object(table_swap, "find_vps_release", return_value={}):
            self.assertEqual(table_swap.describe({}, OLD), OLD)

    def test_the_game_in_a_folder_is_found_by_its_folder(self) -> None:
        self.assertEqual(table_swap.game_id_of(self.folder), GAME_ID)
        self.assertEqual(table_swap.game_id_of(self.root / "nowhere"), "")

    def test_the_tables_that_can_be_swapped_are_listed_with_the_one_the_game_plays_marked(
            self) -> None:
        self.info["tables"]["t1"]["hidden"] = False
        save_record(self.folder / f"{FOLDER}.info", self.info)

        listed = table_swap.choices(GAME_ID)

        self.assertEqual({one["id"]: (one["filename"], one["default"]) for one in listed},
                         {"t1": (OLD, True), "t2": ("Cactus Other.vpx", False)})

    def test_a_hidden_table_is_listed_but_the_game_plays_the_next_one(self) -> None:
        listed = {one["id"]: one["default"] for one in table_swap.choices(GAME_ID)}

        self.assertEqual(listed, {"t1": False, "t2": True})

    def test_a_table_whose_file_is_gone_is_not_offered(self) -> None:
        (self.folder / "Cactus Other.vpx").unlink()

        self.assertEqual([one["id"] for one in table_swap.choices(GAME_ID)], ["t1"])
