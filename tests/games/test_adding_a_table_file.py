"""A game file arriving in a game's folder: copied, or moved out of where it was.

A move is the one place the importer writes to its source, so what is pinned here is that
nothing is lost on the way - the file and what travels with it arrive together, and a file
that cannot be recorded goes back rather than being the only copy, gone.
"""

import errno
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from common.games import game_service
from common.games.info_file import MetaConfig


class AddTableFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        # The files here are not tables VPX could open; what they say about themselves is
        # not what is under test.
        patcher = mock.patch("common.games.library_enrichment.read_one", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)
        root = Path(self._tmp.name)
        self.source = root / "Tables"
        self.source.mkdir()
        self.game = root / "Library" / "Harbor Lights"
        self.game.mkdir(parents=True)
        self.table = self.source / "Harbor Lights.vpx"
        self.table.write_bytes(b"table")
        (self.source / "Harbor Lights.directb2s").write_bytes(b"backglass")
        (self.source / "Quarry Run.directb2s").write_bytes(b"someone else's")

    def test_a_copy_leaves_the_source_as_it_was(self) -> None:
        found = game_service.add_table_file(self.game, self.table, "tbl0000001")

        self.assertTrue(self.table.exists())
        self.assertTrue((self.source / "Harbor Lights.directb2s").exists())
        self.assertTrue((self.game / "Harbor Lights.vpx").exists())
        self.assertEqual(found["companions"], ["Harbor Lights.directb2s"])

    def test_a_move_takes_the_file_and_what_belongs_to_it(self) -> None:
        found = game_service.add_table_file(self.game, self.table, "tbl0000001", move=True)

        self.assertFalse(self.table.exists())
        self.assertFalse((self.source / "Harbor Lights.directb2s").exists())
        self.assertEqual((self.game / "Harbor Lights.vpx").read_bytes(), b"table")
        self.assertEqual((self.game / "Harbor Lights.directb2s").read_bytes(), b"backglass")
        self.assertEqual(found["companions"], ["Harbor Lights.directb2s"])

    def test_a_move_leaves_a_neighbours_files_alone(self) -> None:
        game_service.add_table_file(self.game, self.table, "tbl0000001", move=True)

        self.assertTrue((self.source / "Quarry Run.directb2s").exists())

    def test_a_file_that_cannot_be_recorded_goes_back_when_moved(self) -> None:
        """The recording is what refuses a second file of the same name. After a move the
        file is the only copy, so a refusal must not be the end of it."""
        with mock.patch.object(game_service, "record_arrived_table",
                               side_effect=ValueError("already")):
            with self.assertRaises(ValueError):
                game_service.add_table_file(self.game, self.table, "tbl0000001", move=True)

        self.assertEqual(self.table.read_bytes(), b"table")
        self.assertFalse((self.game / "Harbor Lights.vpx").exists())

    def test_a_file_the_record_lists_and_the_folder_lost_comes_back_under_its_entry(self) -> None:
        first = game_service.add_table_file(self.game, self.table, "tbl0000001")
        (self.game / "Harbor Lights.vpx").unlink()

        again = game_service.add_table_file(self.game, self.table, "tbl0000002")

        self.assertEqual(again["table_id"], first["table_id"])
        self.assertEqual((self.game / "Harbor Lights.vpx").read_bytes(), b"table")
        listed = MetaConfig(str(self.game / "Harbor Lights.info")).listed_table_id(
            "Harbor Lights.vpx")
        self.assertEqual(listed, first["table_id"])

    def _across_volumes_with_a_source_that_cannot_be_removed(self):
        """A rename that crosses volumes fails, so the file is copied, and then the
        original is refused removal - a read-only source, as on a share."""
        real_rename, real_unlink = os.rename, os.unlink

        def rename(src, dst, *args, **kwargs):
            if Path(src) == self.table:
                raise OSError(errno.EXDEV, "Invalid cross-device link")
            return real_rename(src, dst, *args, **kwargs)

        def unlink(path, *args, **kwargs):
            if Path(path) == self.table:
                raise PermissionError(errno.EACCES, "Permission denied")
            return real_unlink(path, *args, **kwargs)

        return mock.patch("os.rename", rename), mock.patch("os.unlink", unlink)

    def test_a_move_that_cannot_remove_its_source_leaves_no_copy_behind(self) -> None:
        rename, unlink = self._across_volumes_with_a_source_that_cannot_be_removed()
        with rename, unlink:
            with self.assertRaises(PermissionError):
                game_service.add_table_file(self.game, self.table, "tbl0000001", move=True)

        self.assertEqual(self.table.read_bytes(), b"table")
        self.assertFalse((self.game / "Harbor Lights.vpx").exists())
        self.assertTrue((self.source / "Harbor Lights.directb2s").exists())

    def test_a_move_that_failed_can_be_tried_again(self) -> None:
        rename, unlink = self._across_volumes_with_a_source_that_cannot_be_removed()
        with rename, unlink:
            with self.assertRaises(PermissionError):
                game_service.add_table_file(self.game, self.table, "tbl0000001", move=True)

        found = game_service.add_table_file(self.game, self.table, "tbl0000001", move=True)

        self.assertEqual(found["table"], "Harbor Lights.vpx")
        self.assertFalse(self.table.exists())
        self.assertEqual((self.game / "Harbor Lights.vpx").read_bytes(), b"table")

    def test_a_refused_move_is_not_followed_by_its_companions(self) -> None:
        with mock.patch.object(game_service, "record_arrived_table",
                               side_effect=ValueError("already")):
            with self.assertRaises(ValueError):
                game_service.add_table_file(self.game, self.table, "tbl0000001", move=True)

        self.assertTrue((self.source / "Harbor Lights.directb2s").exists())

    def test_the_recording_does_not_delete_a_moved_file_it_refuses(self) -> None:
        """The same name twice in one game: a copy's extra is removed because the source
        still holds the file; a moved one is kept because nothing else does."""
        game_service.add_table_file(self.game, self.table, "tbl0000001")
        again = self.source / "Harbor Lights.vpx"
        landing = self.game / "Harbor Lights.vpx"

        with self.assertRaises(ValueError):
            game_service.record_arrived_table(self.game, landing, "tbl0000002", keep=True)
        self.assertTrue(landing.exists())
        with self.assertRaises(ValueError):
            game_service.record_arrived_table(self.game, landing, "tbl0000002")
        self.assertFalse(landing.exists())
        self.assertTrue(again.exists())

    def test_a_file_already_in_the_folder_is_never_replaced(self) -> None:
        (self.game / "Harbor Lights.vpx").write_bytes(b"theirs")

        with self.assertRaises(FileExistsError):
            game_service.add_table_file(self.game, self.table, "tbl0000001", move=True)

        self.assertEqual((self.game / "Harbor Lights.vpx").read_bytes(), b"theirs")
        self.assertEqual(self.table.read_bytes(), b"table")


class NamesInTheScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.game = root / "Library" / "Harbor Lights"
        self.game.mkdir(parents=True)
        self.table = root / "Harbor Lights.vpx"
        self.table.write_bytes(b"table")

    def test_the_names_the_script_mentions_come_back_with_the_rom(self) -> None:
        parsed = {"rom": "hl_l1", "game_data": 'LoadValue "Harbor", "Hi"\nx = "Scores.txt"'}

        with mock.patch("common.games.library_enrichment.read_one", return_value=parsed):
            found = game_service.add_table_file(
                self.game, self.table, "tbl0000001", names=["Harbor", "scores", "Other"])

        self.assertEqual((found["rom"], found["named"]), ("hl_l1", ("Harbor", "scores")))

    def test_no_names_asked_for_none_come_back(self) -> None:
        parsed = {"rom": "hl_l1", "game_data": 'x = "Scores.txt"'}

        with mock.patch("common.games.library_enrichment.read_one", return_value=parsed):
            found = game_service.add_table_file(self.game, self.table, "tbl0000001")

        self.assertEqual(found["named"], ())


if __name__ == "__main__":
    unittest.main()
