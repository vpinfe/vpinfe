"""Coming back to the table the player last launched.

Identity is the table's id, falling back to the game's. Two reasons it is not the path it
used to be: a game folder holds several tables and a path names the folder, so an expanded
wheel came back to the wrong row; and an install reading a library elsewhere never sees
that filesystem, so a path identifies nothing there. Both ids cross the wire.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from common.config.config_store import ConfigStore
from common.devices import device_registry
from common.games.collection_store import LAST_COLLECTION
from frontend import last_game
from tests.support.library import own_registry


def _config(tmp: str) -> ConfigStore:
    return ConfigStore(str(Path(tmp) / "vpinfe.ini"))


def _game(game_id: str = "", name: str = "Game"):
    return SimpleNamespace(
        game_dir_name=name, full_path_game=f"/games/{name}",
        meta_config={"Info": {"Title": name}, "vpinfe": {"game_id": game_id}})


def _saved(key: str = "last_table") -> str:
    return device_registry.get_device_registry().self_get(key, "")


def _entry(table_id: str = "", game_id: str = "", name: str = "Game"):
    """One row of the wheel, which is what is saved and resolved."""
    return SimpleNamespace(game=_game(game_id, name), table_id=table_id)


class LastLaunchedTests(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        own_registry(self, Path(tmp.name) / "devices.json")

    def test_the_table_is_what_is_saved(self) -> None:
        with TemporaryDirectory() as tmp:
            config = _config(tmp)
            entries = [_entry("Tbl1111111", "Gme1111111", "AAA"),
                       _entry("Tbl2222222", "Gme2222222", "BBB")]

            last_game.save_last_launched(config, entries[1].game, "Tbl2222222")

            self.assertEqual(_saved(), "Tbl2222222")
            self.assertEqual(last_game.resolve_last_table_index(config, entries), 1)

    def test_an_expanded_wheel_comes_back_to_the_table_not_the_game(self) -> None:
        """The case a path could not answer: three rows, one game, three tables."""
        with TemporaryDirectory() as tmp:
            config = _config(tmp)
            entries = [_entry(f"Tbl{n}{n}{n}{n}{n}{n}{n}{n}{n}{n}", "Gme1111111", "AAA")
                       for n in (1, 2, 3)]

            last_game.save_last_launched(config, entries[2].game, entries[2].table_id)

            self.assertEqual(last_game.resolve_last_table_index(config, entries), 2)

    def test_a_game_with_no_table_ids_still_comes_back(self) -> None:
        """A folder no build has parsed has no table ids, and its row is the game."""
        with TemporaryDirectory() as tmp:
            config = _config(tmp)
            entries = [_entry("", "Gme1111111", "AAA"), _entry("", "Gme2222222", "BBB")]

            last_game.save_last_launched(config, entries[1].game)

            self.assertEqual(_saved(), "Gme2222222")
            self.assertEqual(last_game.resolve_last_table_index(config, entries), 1)

    def test_resolve_survives_reordering(self) -> None:
        """The point of saving an identity rather than a position."""
        with TemporaryDirectory() as tmp:
            config = _config(tmp)
            last_game.save_last_launched(config, _game("Gme2222222"), "Tbl2222222")

            reordered = [_entry("Tbl3333333", "Gme3333333"),
                         _entry("Tbl2222222", "Gme2222222"),
                         _entry("Tbl1111111", "Gme1111111")]

            self.assertEqual(last_game.resolve_last_table_index(config, reordered), 1)

    def test_a_saved_row_that_is_no_longer_there_starts_at_the_beginning(self) -> None:
        with TemporaryDirectory() as tmp:
            config = _config(tmp)
            device_registry.get_device_registry().set_self(last_table="Tbl9999999")

            self.assertEqual(
                last_game.resolve_last_table_index(config, [_entry("Tbl1111111")]), 0)

    def test_nothing_saved_starts_at_the_beginning(self) -> None:
        with TemporaryDirectory() as tmp:
            config = _config(tmp)

            self.assertEqual(
                last_game.resolve_last_table_index(config, [_entry("Tbl1111111")]), 0)

    def test_disabled_skips_save_and_resolve(self) -> None:
        with TemporaryDirectory() as tmp:
            config = _config(tmp)
            config.config.set("behavior", "restore_last_table", "false")
            entries = [_entry("Tbl1111111"), _entry("Tbl2222222")]

            last_game.save_last_launched(config, entries[1].game, "Tbl2222222")

            self.assertEqual(_saved(), "")
            self.assertEqual(last_game.resolve_last_table_index(config, entries), 0)

    def test_a_row_with_no_id_at_all_saves_nothing(self) -> None:
        """Rather than writing "" and matching the first row with nothing either."""
        with TemporaryDirectory() as tmp:
            config = _config(tmp)

            last_game.save_last_launched(config, _game())

            self.assertEqual(_saved(), "")


class EntryIdentityTests(unittest.TestCase):
    def test_the_table_wins_when_it_has_an_id(self) -> None:
        self.assertEqual(
            last_game.entry_identity(_entry("Tbl1111111", "Gme1111111")), "Tbl1111111")

    def test_the_game_answers_when_the_table_has_no_id(self) -> None:
        self.assertEqual(last_game.entry_identity(_entry("", "Gme1111111")), "Gme1111111")

    def test_neither_is_no_identity_rather_than_a_guess(self) -> None:
        self.assertEqual(last_game.entry_identity(_entry()), "")


class LastCollectionTests(unittest.TestCase):
    """`behavior.startup_collection` = Last Collection opens on the collection shown last."""

    def setUp(self) -> None:
        super().setUp()
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        own_registry(self, Path(tmp.name) / "devices.json")

    def _set(self, config: ConfigStore, opens_on: str) -> None:
        config.config.set("behavior", "startup_collection", opens_on)

    def test_it_is_remembered_only_while_the_setting_asks_for_it(self) -> None:
        with TemporaryDirectory() as tmp:
            config = _config(tmp)
            self._set(config, "Friday Night")

            last_game.remember_collection(config, "90s Bally")

            self.assertEqual(_saved("last_collection"), "")
            self.assertEqual(last_game.opening_collection(config), "Friday Night")

    def test_the_collection_shown_last_is_where_it_opens(self) -> None:
        with TemporaryDirectory() as tmp:
            config = _config(tmp)
            self._set(config, LAST_COLLECTION)

            last_game.remember_collection(config, "90s Bally")
            last_game.remember_collection(config, "Friday Night")

            self.assertEqual(last_game.opening_collection(config), "Friday Night")

    def test_the_setting_may_be_spelled_any_way(self) -> None:
        with TemporaryDirectory() as tmp:
            config = _config(tmp)
            self._set(config, LAST_COLLECTION.lower())

            last_game.remember_collection(config, "Friday Night")

            self.assertEqual(last_game.opening_collection(config), "Friday Night")

    def test_the_whole_library_is_remembered_as_empty(self) -> None:
        with TemporaryDirectory() as tmp:
            config = _config(tmp)
            self._set(config, LAST_COLLECTION)
            last_game.remember_collection(config, "Friday Night")

            last_game.remember_collection(config, "builtin:all")

            self.assertEqual(last_game.opening_collection(config), "")

    def test_nothing_shown_yet_opens_the_whole_library(self) -> None:
        with TemporaryDirectory() as tmp:
            config = _config(tmp)
            self._set(config, LAST_COLLECTION)

            self.assertEqual(last_game.opening_collection(config), "")

    def test_an_unchanged_collection_skips_the_disk_write(self) -> None:
        with TemporaryDirectory() as tmp:
            config = _config(tmp)
            self._set(config, LAST_COLLECTION)
            last_game.remember_collection(config, "Friday Night")

            with patch.object(device_registry.get_device_registry(), "set_self") as write:
                last_game.remember_collection(config, "Friday Night")

            write.assert_not_called()


if __name__ == "__main__":
    unittest.main()
