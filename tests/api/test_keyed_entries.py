"""A game folder that holds something with no file.

A ROM its emulator looks up, a Pinball FX table id. It is the one kind of entry a folder
scan can never find, so adding it is the only way it arrives - and once it is there
everything else has to treat it as an entry like any other, because a library where one
kind of thing is second class is a library with two of everything.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from starlette.testclient import TestClient

import httpapi
from common.games import launchers
from common.games.info_file import INFO_SCHEMA
from tests.support.library import TempTree, fake_game, own_launchers, write_game
from tests.support.saved import saved_record

GAME_ID = "Keyed0000001"
FOLDER = "Medieval Madness (Williams 1997)"

INFO = {
    "Info": {"Name": "Medieval Madness", "Manufacturer": "Williams", "Year": "1997"},
    "vpinfe": {"game_id": GAME_ID, "schema": INFO_SCHEMA},
    "tables": {},
}


class _Keyed(TempTree):
    def setUp(self) -> None:
        super().setUp()
        self.folder = write_game(self.root, FOLDER, info=INFO, vpx=False)
        self.game = fake_game(self.folder, FOLDER, meta=INFO)
        patcher = patch("common.games.game_repository.catalog", return_value={GAME_ID: self.game})
        patcher.start()
        self.addCleanup(patcher.stop)

        self.store = own_launchers(self, str(self.root))
        # Building the app runs the startup seed pass, which would replace this store
        # with a shipped launcher. A real install is in this state from its second
        # start onwards.
        self.store.save([], {})
        self.store.put(launchers.Launcher(
            launcher_id="gen1", app="generic", display_name="MAME",
            settings={"bin_path": "/usr/bin/mame"}))
        store_patch = patch.object(launchers, "get_launcher_store",
                                   return_value=self.store)
        store_patch.start()
        self.addCleanup(store_patch.stop)

        self.client = TestClient(httpapi.create_api_app(),
                                 raise_server_exceptions=False)

    def _add(self, app="generic", key="mm"):
        return self.client.post(f"/games/{GAME_ID}/tables",
                                json={"app": app, "key": key})

    def _tables(self):
        got = self.client.get(f"/games/{GAME_ID}/tables")
        self.assertEqual(got.status_code, 200, got.text)
        return got.json()["tables"]

    def _info(self) -> dict:
        return saved_record(self.folder / f"{FOLDER}.info")


class AddingTests(_Keyed):
    def test_a_folder_with_no_file_can_hold_an_entry(self) -> None:
        made = self._add()

        self.assertEqual(made.status_code, 201, made.text)
        self.assertEqual(made.json()["key"], "mm")
        self.assertEqual(made.json()["form"], "keyed")
        self.assertEqual(made.json()["filename"], "",
                         "there is no file, and saying one would invent it")

    def test_it_is_written_where_every_other_entry_lives(self) -> None:
        """One record home, and it is the game folder."""
        self._add()

        entries = list(self._info()["tables"].values())
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["app"], "generic")
        self.assertEqual(entries[0]["key"], "mm")
        self.assertNotIn("filename", entries[0])

    def test_the_same_one_twice_is_refused(self) -> None:
        self._add()

        self.assertEqual(self._add().status_code, 409)

    def test_an_app_that_plays_files_is_refused(self) -> None:
        """Visual Pinball has no idea what to do with a name."""
        said = self._add(app="vpx")

        self.assertEqual(said.status_code, 400)
        self.assertIn("files", said.json()["error"]["message"])

    def test_an_app_this_build_does_not_know_is_refused(self) -> None:
        self.assertEqual(self._add(app="future-pinball").status_code, 400)

    def test_a_key_nobody_typed_is_refused(self) -> None:
        self.assertEqual(self._add(key="   ").status_code, 400)


class ListingTests(_Keyed):
    def test_it_shows_up_as_one_of_the_game_s_tables(self) -> None:
        self._add()

        rows = self._tables()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["form"], "keyed")

    def test_it_is_the_default_because_it_is_the_only_one(self) -> None:
        """The thing that must not happen: a folder holding only this reading as a game
        with nothing to play."""
        self._add()

        self.assertTrue(self._tables()[0]["default"])

    def test_the_launcher_comes_from_the_app_it_declares(self) -> None:
        """There is no suffix to resolve through."""
        self._add()

        self.assertEqual(self._tables()[0]["launcher_name"], "MAME")

    def test_a_rom_and_a_flexdmd_folder_are_not_its_business(self) -> None:
        """Both are Visual Pinball's. Reporting them as unknown would put a dependency
        on something that can never have one."""
        self._add()

        self.assertIsNone(self._tables()[0]["dependencies"])

    def test_it_reads_as_available_where_something_can_play_it(self) -> None:
        """A file is there or it is not; a key has nothing here to check it against, so
        what stands in is whether this machine could play it at all."""
        self._add()

        self.assertTrue(self._tables()[0]["available"])


class PlayRecordTests(_Keyed):
    """Playing one has to count against the entry that was played, and only it."""

    def test_a_play_lands_on_the_entry_rather_than_inventing_a_second(self) -> None:
        """Matched on the name the launch path uses. Matching on the filename alone
        found nothing, and the create-if-absent branch then added a phantom entry whose
        filename was the key - one more of them on every launch."""
        from common.games import game_play_service

        table_id = self._add().json()["id"]
        game_play_service.add_play_time(self.game, 12, "generic:mm")

        entries = self._info()["tables"]
        self.assertEqual(list(entries), [table_id], "one entry, not two")
        self.assertEqual(entries[table_id]["user"]["run_time_seconds"], 12)

    def test_a_file_that_has_no_record_still_gets_one(self) -> None:
        """The create branch is for files, and it stays: a table on disk that nothing
        has described yet is counted from its first launch."""
        from common.games import game_play_service

        game_play_service.add_play_time(self.game, 5, "Somebody Else.vpx")

        entries = list(self._info()["tables"].values())
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["filename"], "Somebody Else.vpx")


class ForgettingTests(_Keyed):
    def test_it_can_be_forgotten_because_nothing_will_mint_it_again(self) -> None:
        """Which is the opposite of a table whose file is on disk: that record comes
        straight back on the next scan, so dropping it is refused."""
        table_id = self._add().json()["id"]

        gone = self.client.delete(f"/games/{GAME_ID}/tables/{table_id}")

        self.assertEqual(gone.status_code, 200, gone.text)
        self.assertEqual(self._info()["tables"], {})


if __name__ == "__main__":
    unittest.main()
