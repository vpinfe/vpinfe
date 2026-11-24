import os
import tempfile
import unittest
from pathlib import Path
from typing import Any

from common.games.tables import (
    ADDED_KEY,
    default_entry,
    entry_filename,
    is_parsed,
    offered_tables,
    stamp_added,
    survey,
    table_names,
)

OLDER = "2025-03-01T10:00:00Z"
NEWER = "2026-06-01T10:00:00Z"


def _table(table_id: str, filename: str, added: str = "", **more: object) -> dict:
    entry = {"id": table_id, "filename": filename, **more}
    if added:
        entry[ADDED_KEY] = added
    return entry


def _entries(*tables: dict) -> dict:
    return {one["id"]: one for one in tables}


class TableNamesTests(unittest.TestCase):
    def test_only_vpx_files_count(self) -> None:
        names = ["Table.vpx", "Table.vbs", "Table.directb2s", "notes.txt", "Table.VPX"]

        self.assertEqual(table_names(names), ["Table.vpx", "Table.VPX"])

    def test_order_does_not_depend_on_the_listing(self) -> None:
        forwards = table_names(["b.vpx", "A.vpx", "c.vpx"])
        backwards = table_names(["c.vpx", "A.vpx", "b.vpx"])

        self.assertEqual(forwards, backwards)
        self.assertEqual(forwards, ["A.vpx", "b.vpx", "c.vpx"])


class DefaultTableTests(unittest.TestCase):
    """Which table a game plays when nothing names one. Every caller has to agree, or
    metadata describes one file while another launches."""

    def _default(self, entries: dict, recorded: str = "", listing: list | None = None) -> str:
        return entry_filename(default_entry(entries, recorded, listing)[1])

    def test_the_most_recently_added_wins_whatever_the_names_say(self) -> None:
        entries = _entries(_table("tbl0000001", "AFM (bigus1 1.3.0).vpx", OLDER),
                           _table("tbl0000002", "AFM (cyberpez Beta4).vpx", NEWER))

        self.assertEqual(self._default(entries), "AFM (cyberpez Beta4).vpx")

    def test_a_tie_goes_to_the_first_by_name(self) -> None:
        entries = _entries(_table("tbl0000001", "b build.vpx", OLDER),
                           _table("tbl0000002", "a build.vpx", OLDER))

        self.assertEqual(self._default(entries), "a build.vpx")

    def test_a_table_with_no_date_is_older_than_one_with_a_date(self) -> None:
        entries = _entries(_table("tbl0000001", "a build.vpx"),
                           _table("tbl0000002", "b build.vpx", OLDER))

        self.assertEqual(self._default(entries), "b build.vpx")

    def test_the_recorded_table_wins(self) -> None:
        entries = _entries(_table("tbl0000001", "Alt Build.vpx", OLDER),
                           _table("tbl0000002", "The Table.vpx", NEWER))

        self.assertEqual(self._default(entries, "tbl0000001"), "Alt Build.vpx")

    def test_a_filename_recorded_by_2x_is_read(self) -> None:
        entries = _entries(_table("tbl0000001", "Alt Build.vpx", OLDER),
                           _table("tbl0000002", "The Table.vpx", NEWER))

        self.assertEqual(self._default(entries, "Alt Build.vpx"), "Alt Build.vpx")

    def test_a_recorded_table_that_is_gone_is_ignored(self) -> None:
        entries = _entries(_table("tbl0000001", "Alt Build.vpx", OLDER),
                           _table("tbl0000002", "The Table.vpx", NEWER))

        self.assertEqual(self._default(entries, "gone.vpx"), "The Table.vpx")

    def test_a_hidden_table_is_never_the_default(self) -> None:
        entries = _entries(_table("tbl0000001", "Base.vpx", OLDER),
                           _table("tbl0000002", "Patched.vpx", NEWER, hidden=True))

        self.assertEqual(self._default(entries), "Base.vpx")
        self.assertEqual(self._default(entries, "tbl0000002"), "Base.vpx",
                         "not even the recorded one")

    def test_a_table_whose_file_is_gone_is_never_the_default(self) -> None:
        entries = _entries(_table("tbl0000001", "Base.vpx", OLDER),
                           _table("tbl0000002", "New.vpx", NEWER,
                                  absent_since="2026-07-01T00:00:00Z"))

        self.assertEqual(self._default(entries), "Base.vpx")

    def test_with_a_listing_a_file_not_in_it_is_passed_over(self) -> None:
        entries = _entries(_table("tbl0000001", "Base.vpx", OLDER),
                           _table("tbl0000002", "New.vpx", NEWER))

        self.assertEqual(self._default(entries, listing=["Base.vpx"]), "Base.vpx")

    def test_with_a_listing_a_file_nothing_describes_is_offered(self) -> None:
        offered = offered_tables({}, listing=["notes.txt", "Only.vpx"])

        self.assertEqual(offered, [("", {"filename": "Only.vpx"})])

    def test_every_table_hidden_offers_nothing(self) -> None:
        entries = _entries(_table("tbl0000001", "A.vpx", hidden=True))

        self.assertEqual(default_entry(entries), ("", {}))

    def test_a_folder_with_no_tables_resolves_to_nothing(self) -> None:
        self.assertEqual(self._default({}, listing=["readme.txt"]), "")

    def test_the_rest_follow_by_name(self) -> None:
        entries = _entries(_table("tbl0000001", "c.vpx", OLDER),
                           _table("tbl0000002", "a.vpx", OLDER),
                           _table("tbl0000003", "b.vpx", NEWER))

        self.assertEqual([entry_filename(e) for _i, e in offered_tables(entries)],
                         ["b.vpx", "a.vpx", "c.vpx"])

    def test_the_answer_never_depends_on_listing_order(self) -> None:
        names = ["c.vpx", "a.vpx", "Folder.vpx", "b.vpx"]

        self.assertEqual(self._default({}, listing=names),
                         self._default({}, listing=list(reversed(names))))


class StampAddedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = self.enterContext(tempfile.TemporaryDirectory())
        with open(os.path.join(self.folder, "Table.vpx"), "wb") as handle:
            handle.write(b"vpx")

    def test_a_table_on_disk_is_dated_from_its_file(self) -> None:
        entry = {"id": "tbl0000001", "filename": "Table.vpx"}

        self.assertTrue(stamp_added(self.folder, entry))
        self.assertRegex(entry[ADDED_KEY], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")

    def test_a_date_already_held_is_kept(self) -> None:
        entry = {"id": "tbl0000001", "filename": "Table.vpx", ADDED_KEY: OLDER}

        self.assertFalse(stamp_added(self.folder, entry))
        self.assertEqual(entry[ADDED_KEY], OLDER)

    def test_a_file_that_is_not_there_leaves_it_undated(self) -> None:
        entry = {"id": "tbl0000001", "filename": "Gone.vpx"}

        self.assertFalse(stamp_added(self.folder, entry))
        self.assertNotIn(ADDED_KEY, entry)

    def test_a_table_with_no_file_leaves_it_undated(self) -> None:
        entry = {"id": "tbl0000001", "app": "pinballfx", "key": "123"}

        self.assertFalse(stamp_added(self.folder, entry))


class IsParsedTests(unittest.TestCase):
    """An entry exists for two different reasons: because we read the .vpx, or because
    somebody decided something about it. Only the first says anything about the build."""

    def test_a_parsed_entry_is_parsed(self) -> None:
        self.assertTrue(is_parsed({"file_hash": "3a77427e", "rom": "afm_113b"}))

    def test_an_empty_rom_still_counts_as_parsed(self) -> None:
        """An EM game declares no ROM. That is an answer, not an absence of one."""
        self.assertTrue(is_parsed({"file_hash": "3a77427e", "rom": ""}))

    def test_a_decision_alone_is_not_a_parse(self) -> None:
        """Hiding a table records what the user wants, not what the file says."""
        self.assertFalse(is_parsed({"hidden": True}))

    def test_junk_is_not_a_parse(self) -> None:
        junk: tuple[Any, ...] = (None, {}, [], "nope")
        for bad in junk:
            with self.subTest(entry=bad):
                self.assertFalse(is_parsed(bad))


class SurveyTests(unittest.TestCase):
    """What a folder holds, asked of core because what makes a folder a game is core's
    rule."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _game(self, name: str) -> None:
        (self.root / name).mkdir()
        (self.root / name / f"{name}.vpx").write_bytes(b"table")

    def test_game_folders_and_loose_tables_are_told_apart(self) -> None:
        self._game("Harbor Lights")
        (self.root / "Quarry Run.vpx").write_bytes(b"table")
        (self.root / "notes.txt").write_text("not a table")
        (self.root / "Plain folder").mkdir()

        self.assertEqual(survey(self.root), {"game_folders": ["Harbor Lights"],
                                             "loose": ["Quarry Run.vpx"]})

    def test_a_folder_with_only_an_info_file_is_a_game(self) -> None:
        """A game whose tables are all hidden or elsewhere still has its `.info`."""
        (self.root / "Orchard").mkdir()
        (self.root / "Orchard" / "Orchard.info").write_text("{}")

        self.assertEqual(survey(self.root)["game_folders"], ["Orchard"])

    def test_a_dot_folder_is_not_looked_into(self) -> None:
        self._game(".hidden")
        self._game("Visible")

        self.assertEqual(survey(self.root)["game_folders"], ["Visible"])

    def test_a_dot_file_is_not_a_loose_table(self) -> None:
        """macOS leaves `._Name.vpx` beside the real one on a volume it cannot label."""
        (self.root / "Quarry Run.vpx").write_bytes(b"table")
        (self.root / "._Quarry Run.vpx").write_bytes(b"\x00\x05\x16\x07")
        (self.root / ".hidden.vpx").write_bytes(b"table")

        self.assertEqual(survey(self.root)["loose"], ["Quarry Run.vpx"])

    def test_the_names_are_sorted_without_regard_to_case(self) -> None:
        for name in ("beta", "Alpha", "gamma"):
            self._game(name)

        self.assertEqual(survey(self.root)["game_folders"], ["Alpha", "beta", "gamma"])

    def test_a_folder_that_is_not_there_holds_nothing(self) -> None:
        self.assertEqual(survey(self.root / "absent"),
                         {"game_folders": [], "loose": []})

    def test_only_the_folder_itself_is_read(self) -> None:
        """A table inside a game folder is that game's, not a loose one."""
        self._game("Harbor Lights")

        self.assertEqual(survey(self.root)["loose"], [])


if __name__ == "__main__":
    unittest.main()
