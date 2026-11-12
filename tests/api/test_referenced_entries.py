"""A game whose table is somewhere else.

A .vpx on a read-only share, or one file two games both point at. The record and the
media are the game folder's; the file is not, and that is the whole of the difference.

The state that needs the most care is the one in the middle: the file is not reachable
right now. That is not the table being gone - nothing here is lost, the usual cause is a
share that has not mounted, and the answer is to make it reachable rather than to forget
the entry.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from starlette.testclient import TestClient

import httpapi
from common.games import launchers, library_folders, order_record
from common.games.info_file import INFO_SCHEMA
from tests.support.library import TempTree, fake_game, own_launchers, write_game
from tests.support.saved import saved_record

GAME_ID = "Ref000000001"
FOLDER = "Attack from Mars (Bally 1995)"

INFO = {
    "Info": {"Name": "Attack from Mars", "Manufacturer": "Bally", "Year": "1995"},
    "vpinfe": {"game_id": GAME_ID, "schema": INFO_SCHEMA},
    "tables": {},
}


class _Referenced(TempTree):
    def setUp(self) -> None:
        super().setUp()
        self.folder = write_game(self.root, FOLDER, info=INFO, vpx=False)
        self.game = fake_game(self.folder, FOLDER, meta=INFO)
        patcher = patch("common.games.game_repository.catalog", return_value={GAME_ID: self.game})
        patcher.start()
        self.addCleanup(patcher.stop)

        self.store = own_launchers(self, str(self.root))
        self.store.save([], {})
        self.store.put(launchers.Launcher(
            launcher_id="vpx1", app="vpx", display_name="Visual Pinball X",
            settings={"bin_path": "/opt/vpx"}))
        store_patch = patch.object(launchers, "get_launcher_store",
                                   return_value=self.store)
        store_patch.start()
        self.addCleanup(store_patch.stop)

        # Outside the game folder and outside every configured location, so the stored
        # form is absolute unless a test says otherwise.
        self.away = Path(self.enterContext(TemporaryDirectory())) / "afm.vpx"
        self.away.write_bytes(b"vpx")

        self.client = TestClient(httpapi.create_api_app(),
                                 raise_server_exceptions=False)

    def _add(self, path=None):
        return self.client.post(f"/games/{GAME_ID}/tables",
                                json={"path": str(path if path is not None
                                                  else self.away)})

    def _tables(self):
        got = self.client.get(f"/games/{GAME_ID}/tables")
        self.assertEqual(got.status_code, 200, got.text)
        return got.json()["tables"]

    def _info(self) -> dict:
        return saved_record(self.folder / f"{FOLDER}.info")


class AddingTests(_Referenced):
    def test_a_game_can_point_at_a_file_that_is_not_in_its_folder(self) -> None:
        made = self._add()

        self.assertEqual(made.status_code, 201, made.text)
        self.assertEqual(made.json()["form"], "referenced")
        self.assertEqual(made.json()["reference"]["resolved"], str(self.away))
        self.assertTrue(made.json()["reference"]["reachable"])

    def test_a_table_pointed_at_settles_the_order_of_its_game(self) -> None:
        with patch("common.games.backglass_order.settle") as settle:
            made = self._add()

        self.assertEqual(made.status_code, 201, made.text)
        settle.assert_called_once_with(GAME_ID)

    def test_the_file_it_points_at_says_which_app_plays_it(self) -> None:
        """The same question a filename in the folder answers, asked somewhere else."""
        self.assertEqual(self._add().json()["app"], "vpx")

    def test_a_path_that_is_wrong_when_it_is_typed_is_refused(self) -> None:
        """As against one that stops resolving later, which is a different thing and
        leaves the entry standing."""
        self.assertEqual(self._add(self.root / "nope.vpx").status_code, 404)

    def test_a_relative_path_is_refused_because_it_is_ambiguous(self) -> None:
        self.assertEqual(self._add("../share/afm.vpx").status_code, 400)

    def test_a_file_nothing_plays_is_refused(self) -> None:
        other = self.away.parent / "notes.txt"
        other.write_text("hello")

        self.assertEqual(self._add(other).status_code, 400)

    def test_a_file_already_in_the_folder_is_refused(self) -> None:
        """It is one of this game's tables, not something it points at."""
        (self.folder / "afm.vpx").write_bytes(b"vpx")

        said = self._add(self.folder / "afm.vpx")

        self.assertEqual(said.status_code, 400)
        self.assertIn("already in this game's folder", said.json()["error"]["message"])

    def test_the_same_file_twice_is_refused(self) -> None:
        self._add()

        self.assertEqual(self._add().status_code, 409)

    def test_it_is_stored_relative_where_that_survives_the_library_moving(self) -> None:
        """Relative is anchored on the game folder, and it is only used where both ends
        are inside one configured location - which is exactly when moving that whole
        tree keeps the reference pointing at the same file."""
        inside = self.root / "Shared" / "afm.vpx"
        inside.parent.mkdir(parents=True, exist_ok=True)
        inside.write_bytes(b"vpx")
        here = library_folders.Location(location_id="l1", path=str(self.root), kind="root")

        with patch.object(library_folders, "configured", return_value=[here]):
            made = self._add(inside)

        self.assertEqual(made.status_code, 201, made.text)
        stored = made.json()["reference"]["path"]
        self.assertFalse(Path(stored).is_absolute(), stored)
        self.assertEqual(made.json()["reference"]["resolved"], str(inside))

    def test_and_absolute_where_it_would_not(self) -> None:
        """A relative path out of the library is a chain of `..` that means nothing once
        the library has moved, which is worse than failing honestly."""
        here = library_folders.Location(location_id="l1", path=str(self.root), kind="root")

        with patch.object(library_folders, "configured", return_value=[here]):
            made = self._add()

        self.assertTrue(Path(made.json()["reference"]["path"]).is_absolute())


class RecordShapeTests(_Referenced):
    def test_it_is_not_given_a_filename_it_does_not_have(self) -> None:
        """The re-key fills a map key in as the filename for the shape that predates it.
        An entry that already names itself is not that shape, whichever way it does it -
        and a stray filename here answered for a file this folder does not hold, which
        is how the app that plays it stopped resolving."""
        self._add()

        entry = next(iter(self._info()["tables"].values()))
        self.assertNotIn("filename", entry)
        self.assertIn("path", entry)


class UnreachableTests(_Referenced):
    def test_a_reference_that_stops_resolving_keeps_its_entry(self) -> None:
        """Nothing here is lost - the record, the media and the play record all stand."""
        self._add()
        self.away.unlink()

        rows = self._tables()

        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]["reference"]["reachable"])
        self.assertFalse(rows[0]["available"])

    def test_and_is_not_reported_as_a_table_that_has_gone(self) -> None:
        """`absent_since` is discovery stamping a file this folder had and no longer
        has. A share that has not mounted is neither that nor fine."""
        self._add()
        self.away.unlink()

        self.assertIsNone(self._tables()[0]["absent_since"])


class ContainingTests(_Referenced):
    def _contain(self, table_id):
        return self.client.post(f"/games/{GAME_ID}/tables/{table_id}/contain")

    def test_the_file_is_copied_in_and_the_reference_dropped(self) -> None:
        table_id = self._add().json()["id"]

        got = self._contain(table_id)

        self.assertEqual(got.status_code, 200, got.text)
        self.assertEqual(got.json()["form"], "contained")
        self.assertEqual(got.json()["filename"], "afm.vpx")
        self.assertTrue((self.folder / "afm.vpx").is_file())

    def test_the_id_survives_it(self) -> None:
        """A collection that named this table, and its play record, both stay pointed
        at it - which is the whole reason this is a conversion and not a new entry."""
        table_id = self._add().json()["id"]

        self.assertEqual(self._contain(table_id).json()["id"], table_id)

    def test_the_file_it_pointed_at_is_left_alone(self) -> None:
        """It may be read-only, shared with another game, or somebody else's."""
        table_id = self._add().json()["id"]

        self._contain(table_id)

        self.assertTrue(self.away.is_file())

    def test_a_name_the_folder_already_uses_is_refused(self) -> None:
        (self.folder / "afm.vpx").write_bytes(b"different")
        table_id = self._add().json()["id"]

        said = self._contain(table_id)

        self.assertEqual(said.status_code, 409)
        self.assertEqual((self.folder / "afm.vpx").read_bytes(), b"different")

    def test_a_reference_to_nothing_has_nothing_to_copy(self) -> None:
        table_id = self._add().json()["id"]
        self.away.unlink()

        self.assertEqual(self._contain(table_id).status_code, 409)

    def test_the_tables_of_its_game_are_settled_once_it_is_in(self) -> None:
        table_id = self._add().json()["id"]

        with patch("common.games.backglass_order.settle") as settle:
            self._contain(table_id)

        settle.assert_called_once_with(GAME_ID)

    def test_containing_something_that_is_not_a_reference_is_a_404(self) -> None:
        self.assertEqual(self._contain("nosuchid12").status_code, 404)


class ForgettingTests(_Referenced):
    def test_a_reference_can_be_forgotten_and_its_file_left_alone(self) -> None:
        """Nothing in this folder will mint it back, so refusing would make it
        permanent - and what it pointed at was never ours to delete."""
        table_id = self._add().json()["id"]

        gone = self.client.delete(f"/games/{GAME_ID}/tables/{table_id}")

        self.assertEqual(gone.status_code, 200, gone.text)
        self.assertEqual(self._info()["tables"], {})
        self.assertTrue(self.away.is_file())

    def test_forgetting_takes_the_table_out_of_the_record_of_draw_orders(self) -> None:
        pair = {"ScoreView.Priority.B2S": "2", "ScoreView.Priority.Nixie": "1"}
        patcher = patch.object(order_record, "PATH", self.root / "draw_order.json")
        patcher.start()
        self.addCleanup(patcher.stop)
        table_id = self._add().json()["id"]
        order_record.keep({table_id: pair, "other": pair})

        self.client.delete(f"/games/{GAME_ID}/tables/{table_id}")

        self.assertEqual(order_record.held(), {"other": pair})


if __name__ == "__main__":
    unittest.main()
