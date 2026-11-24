"""Refreshing one game reads one folder.

Setting a rating, importing a file or renaming a table changes one folder. Rescanning
the library to notice costs the whole library, which on a network share is minutes.
"""

from __future__ import annotations

import configparser
import unittest
from pathlib import Path
from unittest import mock

from common import events
from common.games import game_repository
from common.games.game_parser import GameParser
from common.games.library_folders import KIND_ROOT, Location
from tests.support.library import TempTree, write_game


def _game_dir(root: Path, name: str, rating: int = 0) -> Path:
    return write_game(root, name, info={"Info": {"Title": name},
                                        "User": {"Rating": rating},
                                        "vpinfe": {"schema": 2}})


class SingleGameRefreshTests(TempTree):
    def setUp(self):
        super().setUp()
        for name in ("Alpha", "Bravo", "Charlie"):
            _game_dir(self.root, name)
        config = configparser.ConfigParser()
        config.read_dict({"Settings": {"gamerootdir": str(self.root)}, "Media": {}})
        self.parser = GameParser(str(self.root), config)
        self.parser.load_games(reload=True)

    def _rating(self, game):
        return game.meta_config.get("User", {}).get("Rating")

    def _by_name(self, name):
        return next(t for t in self.parser.get_all_games() if t.game_dir_name == name)

    def test_a_changed_folder_is_picked_up(self):
        _game_dir(self.root, "Bravo", rating=5)

        self.parser.reload_game(str(self.root / "Bravo"))

        self.assertEqual(self._rating(self._by_name("Bravo")), 10)

    def test_the_rest_of_the_library_is_not_re_read(self):
        """The point of the change. Charlie is edited on disk and must stay stale,
        because refreshing Bravo has no business reading Charlie's folder."""
        _game_dir(self.root, "Charlie", rating=4)

        self.parser.reload_game(str(self.root / "Bravo"))

        self.assertEqual(self._rating(self._by_name("Charlie")), 0,
                         "refreshing one table read another one's folder")

    def test_the_game_count_does_not_drift(self):
        for _ in range(3):
            self.parser.reload_game(str(self.root / "Alpha"))

        self.assertEqual(self.parser.get_game_count(), 3)

    def test_a_folder_named_a_different_way_is_replaced_not_added(self):
        """The caller rarely spells a folder the way the listing did - a root reached
        through a symlink is the ordinary case, and on macOS every path under /var is
        one. Matching on the spelling appended a second copy on every refresh."""
        before = self.parser.get_game_count()

        self.parser.reload_game(Path(str(self.root / "Bravo")).resolve())

        self.assertEqual(self.parser.get_game_count(), before)
        self.assertEqual([g.game_dir_name for g in self.parser.get_all_games()].count("Bravo"),
                         1)

    def test_a_folder_that_appeared_is_added(self):
        _game_dir(self.root, "Delta")

        added = self.parser.reload_game(str(self.root / "Delta"))

        self.assertIsNotNone(added)
        self.assertEqual(self.parser.get_game_count(), 4)

    def test_a_folder_that_went_away_is_dropped(self):
        for path in (self.root / "Alpha").iterdir():
            path.unlink()
        (self.root / "Alpha").rmdir()

        gone = self.parser.reload_game(str(self.root / "Alpha"))

        self.assertIsNone(gone)
        self.assertEqual(self.parser.get_game_count(), 2)
        self.assertNotIn("Alpha", [t.game_dir_name for t in self.parser.get_all_games()])

    def test_a_folder_with_no_table_is_not_a_game(self):
        empty = self.root / "Empty"
        empty.mkdir()
        (empty / "readme.txt").write_text("nothing here", encoding="utf-8")

        self.assertIsNone(self.parser.reload_game(str(empty)))
        self.assertEqual(self.parser.get_game_count(), 3)

    def test_a_game_that_loses_its_info_is_reported_missing(self):
        (self.root / "Bravo" / "Bravo.info").unlink()

        self.parser.reload_game(str(self.root / "Bravo"))

        missing = [row["folder"] for row in self.parser.get_missing_games()]
        self.assertEqual(missing, ["Bravo"])

    def test_a_game_that_regains_its_info_stops_being_missing(self):
        (self.root / "Bravo" / "Bravo.info").unlink()
        self.parser.reload_game(str(self.root / "Bravo"))

        _game_dir(self.root, "Bravo", rating=2)
        self.parser.reload_game(str(self.root / "Bravo"))

        self.assertEqual(self.parser.get_missing_games(), [])


class ChangeAnnouncementTests(TempTree):
    """Re-reading a game replaces the object, so whoever is holding the old one is
    stale and has no way to find out. The announcement is that way."""

    def setUp(self):
        super().setUp()
        for name in ("Alpha", "Bravo"):
            _game_dir(self.root, name)
        config = configparser.ConfigParser()
        config.read_dict({"Settings": {"gamerootdir": str(self.root)}, "Media": {}})
        parser = GameParser(str(self.root), config)   # constructing loads

        events.clear()
        self.addCleanup(events.clear)
        self.seen = []
        events.subscribe(events.GAME_CHANGED, lambda **payload: self.seen.append(payload))

        # Injected as the only location, so the repository keeps it rather than
        # dropping it as a parser for somewhere that is no longer configured.
        held = mock.patch.object(
            game_repository.library_folders, "configured",
            return_value=[Location(location_id="test", path=str(self.root))])
        held.start()
        self.addCleanup(held.stop)

        previous = dict(game_repository._PARSERS)
        game_repository._PARSERS.clear()
        game_repository._PARSERS[(str(self.root), KIND_ROOT)] = parser
        self.addCleanup(game_repository._PARSERS.update, previous)
        self.addCleanup(game_repository._PARSERS.clear)

    def test_a_refreshed_game_is_announced_with_the_new_object(self):
        _game_dir(self.root, "Bravo", rating=5)

        refreshed = game_repository.refresh_game(str(self.root / "Bravo"))

        self.assertEqual(len(self.seen), 1)
        self.assertIs(self.seen[0]["game"], refreshed[0])

    def test_a_game_that_went_away_is_still_announced(self):
        """The wheel has to drop it, which it cannot do if this stays quiet."""
        for path in (self.root / "Alpha").iterdir():
            path.unlink()
        (self.root / "Alpha").rmdir()

        game_repository.refresh_game(str(self.root / "Alpha"))

        self.assertEqual(len(self.seen), 1)
        self.assertIsNone(self.seen[0]["game"])


if __name__ == "__main__":
    unittest.main()
