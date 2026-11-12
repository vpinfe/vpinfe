"""A table's id has to outlive its filename.

That is the whole reason it exists: a collection pins a table, play stats accrue against
one, and both have to survive somebody tidying up a `.vpx` name. So the tests that matter
are the ones about what happens when a file is renamed, copied, or rebuilt.
"""

import os
import unittest
from pathlib import Path
from unittest import mock

from common import ids
from common.games import library_folders, table_identity, unwritten
from common.games.game_metadata import write_held_meta
from common.games.info_file import MetaConfig
from common.games.library_folders import KIND_ROOT, Location
from common.games.tables import (
    ADDED_KEY,
    TABLE_FILENAME_KEY,
    TABLE_ID_KEY,
    TABLES_KEY,
    entry_for_filename,
    table_id,
)
from common.timestamps import epoch_to_iso, utc_now_iso
from tests.support.library import TempTree, fake_game, write_game
from tests.support.saved import save_record, saved_record
from tests.support.skips import needs_posix_permissions


def _by_name(entries: dict, filename: str) -> dict:
    """The entry for a .vpx. Tests hold names; storage is keyed by id."""
    return entry_for_filename(entries, filename)[1]


def _game(root: Path, name: str, meta: dict | None = None):
    return fake_game(write_game(root, name, info=meta, vpx=False), name, meta=meta or {})


def _meta(*tables: tuple[str, dict]) -> dict:
    """Current shape: keyed by id, filename as a field."""
    entries = {}
    for name, entry in tables:
        entries[entry[TABLE_ID_KEY]] = {**entry, TABLE_FILENAME_KEY: name}
    return {"vpinfe": {"schema": 2}, TABLES_KEY: entries}


def _legacy_meta(*tables: tuple[str, dict]) -> dict:
    """The pre-re-key shape a real library is still sitting on: keyed by filename."""
    return {"vpinfe": {"schema": 2}, TABLES_KEY: dict(tables)}


class RebuildTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        self.info = self.root / "Example.info"

    def _rebuild(self, *files: tuple[str, dict]) -> dict:
        MetaConfig(str(self.info)).write_config_meta(
            {"vpsdata": {}, "gamefiles": dict(files)})
        return saved_record(self.info)[TABLES_KEY]

    def test_every_table_gets_an_id(self) -> None:
        built = self._rebuild(("a.vpx", {"file_hash": "aaa"}),
                              ("b.vpx", {"file_hash": "bbb"}))

        minted = {table_id(entry) for entry in built.values()}
        self.assertEqual(len(minted), 2, "two tables, two ids")
        for one in minted:
            self.assertEqual(len(one), ids.LENGTH)
            self.assertTrue(set(one) <= set(ids.ALPHABET))

    def test_a_rebuild_keeps_the_id_it_already_assigned(self) -> None:
        first = _by_name(self._rebuild(("a.vpx", {"file_hash": "aaa"})), "a.vpx")[TABLE_ID_KEY]
        again = _by_name(self._rebuild(("a.vpx", {"file_hash": "aaa"})), "a.vpx")[TABLE_ID_KEY]

        self.assertEqual(first, again)

    def test_a_renamed_file_keeps_its_id_and_everything_recorded_against_it(self) -> None:
        """The case the id exists for. Matched on content, because the name is what moved."""
        built = self._rebuild(("old.vpx", {"file_hash": "aaa"}))
        original = _by_name(built, "old.vpx")[TABLE_ID_KEY]

        # Whatever accumulated against the old name, as a rebuild would leave it.
        stored = saved_record(self.info)
        stored[TABLES_KEY][original].update(
            {"hidden": True, "user": {"start_count": 7}})
        save_record(self.info, stored)

        renamed = self._rebuild(("new.vpx", {"file_hash": "aaa"}))

        self.assertEqual(_by_name(renamed, "old.vpx"), {}, "the old name is gone")
        self.assertEqual(_by_name(renamed, "new.vpx")[TABLE_ID_KEY], original)
        self.assertTrue(_by_name(renamed, "new.vpx")["hidden"])
        self.assertEqual(_by_name(renamed, "new.vpx")["user"], {"start_count": 7})

    def test_a_copy_beside_the_original_is_a_new_table(self) -> None:
        """Both files are present, so neither is a rename - the second is genuinely new."""
        built = self._rebuild(("a.vpx", {"file_hash": "aaa"}),
                              ("copy.vpx", {"file_hash": "aaa"}))

        self.assertNotEqual(_by_name(built, "a.vpx")[TABLE_ID_KEY],
                            _by_name(built, "copy.vpx")[TABLE_ID_KEY])

    def test_a_changed_file_under_the_same_name_keeps_its_id(self) -> None:
        """Editing a .vpx is not a new table."""
        first = _by_name(self._rebuild(("a.vpx", {"file_hash": "aaa"})), "a.vpx")[TABLE_ID_KEY]
        edited = _by_name(self._rebuild(("a.vpx", {"file_hash": "zzz"})), "a.vpx")[TABLE_ID_KEY]

        self.assertEqual(first, edited)


class AddedStampTests(TempTree):
    """When the library first had a table, which is what picks a game's default."""

    def test_a_table_found_on_disk_is_dated_from_its_file(self) -> None:
        meta = _meta(("a.vpx", {TABLE_ID_KEY: "tbl0000001"}))
        game = _game(self.root, "Found", meta)
        (self.root / "Found" / "a.vpx").write_bytes(b"vpx")
        stat = os.stat(self.root / "Found" / "a.vpx")

        table_identity.ensure_unique_table_ids([game])

        stored = saved_record(self.root / "Found" / "Found.info")
        self.assertEqual(stored[TABLES_KEY]["tbl0000001"][ADDED_KEY],
                         epoch_to_iso(getattr(stat, "st_birthtime", stat.st_ctime)))

    def test_a_table_already_dated_keeps_its_date(self) -> None:
        meta = _meta(("a.vpx", {TABLE_ID_KEY: "tbl0000001",
                                ADDED_KEY: "2020-01-01T00:00:00Z"}))
        game = _game(self.root, "Dated", meta)
        (self.root / "Dated" / "a.vpx").write_bytes(b"vpx")

        table_identity.ensure_unique_table_ids([game])

        stored = saved_record(self.root / "Dated" / "Dated.info")
        self.assertEqual(stored[TABLES_KEY]["tbl0000001"][ADDED_KEY], "2020-01-01T00:00:00Z")

    def test_a_table_someone_adds_is_dated_now(self) -> None:
        folder = write_game(self.root, "Added", info=_meta(), vpx=False)
        config = MetaConfig(str(folder / "Added.info"))
        before = utc_now_iso()

        config.add_contained_table("b.vpx", "tbl0000002")
        config.add_keyed_table("pinballfx", "123", "tbl0000003")
        config.add_referenced_table("../Elsewhere/c.vpx", "tbl0000004")

        stored = saved_record(folder / "Added.info")[TABLES_KEY]
        for table in ("tbl0000002", "tbl0000003", "tbl0000004"):
            with self.subTest(table):
                self.assertGreaterEqual(stored[table][ADDED_KEY], before)
                self.assertLessEqual(stored[table][ADDED_KEY], utc_now_iso())


class BackfillTests(TempTree):

    def test_entries_written_before_ids_existed_get_one(self) -> None:
        game = _game(self.root, "Old", _legacy_meta(("a.vpx", {"file_hash": "aaa"})))

        table_identity.ensure_unique_table_ids([game])

        on_disk = saved_record(self.root / "Old" / "Old.info")
        self.assertTrue(table_id(_by_name(on_disk[TABLES_KEY], "a.vpx")))

    def test_a_filename_keyed_file_is_rewritten_keyed_by_id(self) -> None:
        """What every existing library is sitting on. The key becomes the id and the
        name it used to be keyed by becomes a field; nothing else moves."""
        game = _game(self.root, "Legacy", _legacy_meta(
            ("a.vpx", {"file_hash": "aaa", TABLE_ID_KEY: "keepthisid",
                       "hidden": True, "user": {"start_count": 4}})))

        table_identity.ensure_unique_table_ids([game])

        stored = saved_record(self.root / "Legacy" / "Legacy.info")[TABLES_KEY]
        self.assertEqual(list(stored), ["keepthisid"], "keyed by id, not by name")
        entry = stored["keepthisid"]
        self.assertEqual(entry[TABLE_FILENAME_KEY], "a.vpx")
        self.assertTrue(entry["hidden"])
        self.assertEqual(entry["user"], {"start_count": 4})

    def test_a_recorded_default_naming_a_file_becomes_the_id(self) -> None:
        """The 2.x migration seeds default_table with a filename. It has to become an
        id, or renaming that .vpx would silently change which table the game defaults to."""
        meta = _meta(("chosen.vpx", {TABLE_ID_KEY: "chosen1234"}),
                     ("other.vpx", {TABLE_ID_KEY: "other12345"}))
        meta["vpinfe"]["default_table"] = "chosen.vpx"
        game = _game(self.root, "Seeded", meta)

        table_identity.ensure_unique_table_ids([game])

        stored = saved_record(self.root / "Seeded" / "Seeded.info")
        self.assertEqual(stored["vpinfe"]["default_table"], "chosen1234")

    def test_a_default_already_an_id_is_left_alone(self) -> None:
        meta = _meta(("chosen.vpx", {TABLE_ID_KEY: "chosen1234"}))
        meta["vpinfe"]["default_table"] = "chosen1234"
        game = _game(self.root, "Done", meta)
        info = self.root / "Done" / "Done.info"
        before = info.stat().st_mtime_ns

        table_identity.ensure_unique_table_ids([game])

        self.assertEqual(info.stat().st_mtime_ns, before, "nothing to convert")

    def test_a_default_naming_no_table_is_left_alone(self) -> None:
        """default_table() already falls through to something that exists, so a stale
        name is not worth a write."""
        meta = _meta(("present.vpx", {TABLE_ID_KEY: "present123"}))
        meta["vpinfe"]["default_table"] = "deleted.vpx"
        game = _game(self.root, "Stale", meta)

        table_identity.ensure_unique_table_ids([game])

        stored = saved_record(self.root / "Stale" / "Stale.info")
        self.assertEqual(stored["vpinfe"]["default_table"], "deleted.vpx")

    def test_a_game_with_no_tables_is_not_rewritten_every_startup(self) -> None:
        """A game with no .vpx is rare but real. An empty map was being treated as
        needing conversion, so that game was rewritten on every launch."""
        game = _game(self.root, "Empty", {"vpinfe": {"schema": 2}, TABLES_KEY: {}})
        info = self.root / "Empty" / "Empty.info"
        before = info.stat().st_mtime_ns

        table_identity.ensure_unique_table_ids([game])

        self.assertEqual(info.stat().st_mtime_ns, before)

    def test_an_entry_with_no_id_and_no_name_is_dropped(self) -> None:
        """Nothing can address it, and half a record in front of a reader is worse
        than none."""
        game = _game(self.root, "Half", {"vpinfe": {"schema": 2},
                                         TABLES_KEY: {"a.vpx": "not-a-dict"}})

        table_identity.ensure_unique_table_ids([game])

        stored = saved_record(self.root / "Half" / "Half.info")[TABLES_KEY]
        self.assertEqual(stored, {})

    def test_a_copied_folder_does_not_leave_two_tables_sharing_an_id(self) -> None:
        """The only collision that happens in practice: the .info was copied with the folder."""
        shared = {"file_hash": "aaa", TABLE_ID_KEY: "dupdupdup1"}
        first = _game(self.root, "First", _meta(("a.vpx", dict(shared))))
        second = _game(self.root, "Second", _meta(("a.vpx", dict(shared))))

        by_id = table_identity.ensure_unique_table_ids([first, second])

        self.assertEqual(len(by_id), 2)
        kept = saved_record(self.root / "First" / "First.info")
        remixed = saved_record(self.root / "Second" / "Second.info")
        self.assertEqual(table_id(_by_name(kept[TABLES_KEY], "a.vpx")), "dupdupdup1")
        self.assertNotEqual(table_id(_by_name(remixed[TABLES_KEY], "a.vpx")), "dupdupdup1")

    def _held_by(self, location_id: str) -> None:
        rows = [Location(location_id=location_id, path=str(self.root), kind=KIND_ROOT)]
        patched = mock.patch.object(library_folders, "configured", return_value=rows)
        patched.start()
        self.addCleanup(patched.stop)

    def test_an_entry_with_no_id_gets_the_one_its_game_and_file_make(self) -> None:
        self._held_by("here")
        game = _game(self.root, "Old", _legacy_meta(("a.vpx", {"file_hash": "aaa"})))
        game.location_id = "here"

        by_id = table_identity.ensure_unique_table_ids([game])

        self.assertEqual(list(by_id), [ids.derive_id("here", "Old", "a.vpx")])

    def test_a_collision_is_given_the_id_its_own_game_and_file_make(self) -> None:
        self._held_by("here")
        shared = {"file_hash": "aaa", TABLE_ID_KEY: "dupdupdup1"}
        first = _game(self.root, "First", _meta(("a.vpx", dict(shared))))
        second = _game(self.root, "Second", _meta(("a.vpx", dict(shared))))
        for one in (first, second):
            one.location_id = "here"

        by_id = table_identity.ensure_unique_table_ids([first, second])

        self.assertEqual(sorted(by_id), sorted(["dupdupdup1",
                                                ids.derive_id("here", "Second", "a.vpx")]))

    def test_a_derived_id_a_later_game_already_holds_is_not_taken(self) -> None:
        self._held_by("here")
        wanted = ids.derive_id("here", "New", "a.vpx")
        new = _game(self.root, "New", _legacy_meta(("a.vpx", {"file_hash": "aaa"})))
        renamed = _game(self.root, "Renamed", _meta(("a.vpx", {TABLE_ID_KEY: wanted})))
        for one in (new, renamed):
            one.location_id = "here"

        by_id = table_identity.ensure_unique_table_ids([new, renamed])

        self.assertEqual(len(by_id), 2)
        self.assertEqual(by_id[wanted][0], renamed)

    @needs_posix_permissions
    def test_a_folder_it_cannot_write_leaves_the_others_their_ids(self) -> None:
        locked = _game(self.root, "Locked", _legacy_meta(("a.vpx", {"file_hash": "aaa"})))
        kept = _game(self.root, "Kept", _legacy_meta(("b.vpx", {"file_hash": "bbb"})))
        (self.root / "Locked").chmod(0o555)
        self.addCleanup((self.root / "Locked").chmod, 0o755)

        with mock.patch.dict(unwritten._HELD, clear=True), \
                mock.patch.object(unwritten.logger, "warning") as logged:
            by_id = table_identity.ensure_unique_table_ids([locked, kept])

        self.assertEqual(sorted(name for _game, name in by_id.values()),
                         ["a.vpx", "b.vpx"])
        on_disk = saved_record(self.root / "Kept" / "Kept.info")
        self.assertTrue(table_id(_by_name(on_disk[TABLES_KEY], "b.vpx")))
        self.assertEqual([one.args[1] for one in logged.call_args_list], ["Locked"])

    @needs_posix_permissions
    def test_a_folder_it_cannot_write_keeps_its_table_ids_when_read_again(self) -> None:
        _game(self.root, "Locked", _legacy_meta(("a.vpx", {"file_hash": "aaa"})))
        info = self.root / "Locked" / "Locked.info"
        (self.root / "Locked").chmod(0o555)
        self.addCleanup((self.root / "Locked").chmod, 0o755)
        for patcher in (mock.patch.dict(unwritten._HELD, clear=True),
                        mock.patch.object(unwritten, "logger")):
            patcher.start()
            self.addCleanup(patcher.stop)

        def read() -> dict[str, str]:
            game = fake_game(self.root / "Locked", "Locked", meta=MetaConfig(str(info)).data)
            table_identity.ensure_unique_table_ids([game])
            return table_identity.table_ids(game)

        first = read()

        self.assertTrue(first["a.vpx"])
        self.assertEqual(read(), first)

        (self.root / "Locked").chmod(0o755)
        write_held_meta()

        on_disk = saved_record(info)
        self.assertEqual(table_id(_by_name(on_disk[TABLES_KEY], "a.vpx")), first["a.vpx"])

    def test_a_game_that_needs_nothing_is_not_rewritten(self) -> None:
        """A large library on a network share: a needless write is a round trip each."""
        game = _game(self.root, "Done",
                     _meta(("a.vpx", {"file_hash": "aaa", TABLE_ID_KEY: "alreadyhere"})))
        info = self.root / "Done" / "Done.info"
        before = info.stat().st_mtime_ns

        table_identity.ensure_unique_table_ids([game])

        self.assertEqual(info.stat().st_mtime_ns, before)

    def test_an_id_addresses_one_table(self) -> None:
        game = _game(self.root, "Find",
                     _meta(("a.vpx", {TABLE_ID_KEY: "findme1234"}),
                           ("b.vpx", {TABLE_ID_KEY: "other12345"})))

        self.assertEqual(table_identity.find_table_by_id([game], "findme1234"),
                         (game, "a.vpx"))
        self.assertIsNone(table_identity.find_table_by_id([game], "nosuchid12"))


if __name__ == "__main__":
    unittest.main()
