"""An index from a theme is converted before it means anything to anyone else.

A theme addresses games by position in *its own* filtered list, so the same number names
different games in two windows filtered differently. Everything that leaves the window -
a launch, a rating, an event - goes through `entry_at`/`game_id_at` first, which is what
makes those operations expressible by id rather than only from inside a window.

The five index-taking methods had no test of their own, which is how a return shape could
change without anything noticing.
"""

from __future__ import annotations

import configparser
import unittest
from unittest.mock import patch

from common.games.game_metadata import game_rating
from frontend.api import API
from tests.support.library import TempTree, fake_game, write_game
from tests.support.library_loader import library_of, start_library_of
from tests.support.saved import saved_record

GAME_IDS = ["Aaaaaaaaa1", "Bbbbbbbbb2", "Ccccccccc3"]
NAMES = ["Attack from Mars", "Congo", "Medieval Madness"]


def _ini():
    parser = configparser.ConfigParser()
    parser.add_section("general")
    parser.set("general", "startup_collection", "")

    class DummyIni:
        def __init__(self, config):
            self.config = config

        def save(self):
            pass

    return DummyIni(parser)


class IndexAddressingTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        self.games = []
        self.info_paths = []
        for game_id, name in zip(GAME_IDS, NAMES, strict=True):
            meta = {"Info": {"Name": name, "Title": name}, "User": {},
                    "vpinfe": {"game_id": game_id}}
            folder = write_game(self.root, name, info=meta)
            self.info_paths.append(folder / f"{name}.info")
            self.games.append(fake_game(folder, name, meta=meta))

        start_library_of(self, self.games)
        self.api = API(_ini(), window_name="playfield")

    def _stored_rating(self, position: int) -> int:
        user = saved_record(self.info_paths[position]).get("User") or {}
        return int(user.get("Rating") or 0)

    # -- the conversion itself ------------------------------------------------

    def test_an_index_resolves_to_the_game_it_names(self) -> None:
        self.assertEqual(self.api.game_id_at(0), GAME_IDS[0])
        self.assertEqual(self.api.game_id_at(2), GAME_IDS[2])

    def test_an_index_past_the_end_names_nothing(self) -> None:
        """A theme can ask about a row that is no longer there - a refilter mid-step."""
        self.assertIsNone(self.api.entry_at(99))
        self.assertEqual(self.api.game_id_at(99), "")

    def test_something_that_is_not_an_index_names_nothing(self) -> None:
        values: tuple[object, ...] = (None, "", "seven", [], -99)
        for value in values:
            with self.subTest(value=value):
                self.assertIsNone(self.api.entry_at(value))

    def test_a_negative_index_is_not_read_from_the_end(self) -> None:
        """Python would give -1 the last game. A theme counting down from zero would
        then rate a game it never named."""
        self.assertIsNone(self.api.entry_at(-1))

    def test_an_index_is_read_the_same_however_it_was_spelled(self) -> None:
        """Themes send it over JSON, so it arrives as a string as often as a number."""
        self.assertEqual(self.api.game_id_at("1"), GAME_IDS[1])
        self.assertEqual(self.api.game_id_at(1.0), GAME_IDS[1])

    # -- what the conversion is for -------------------------------------------

    def test_rating_by_index_writes_to_the_game_that_index_named(self) -> None:
        result = self.api.set_game_rating(1, 4)

        self.assertEqual(result, {"success": True, "rating": 4})
        self.assertEqual(self._stored_rating(1), 4)
        self.assertEqual(self._stored_rating(0), 0, "no other game was touched")

    def test_rating_reads_back_what_was_written(self) -> None:
        self.api.set_game_rating(2, 3)

        self.assertEqual(self.api.get_game_rating(2), 3)
        self.assertEqual(game_rating(self.games[2]), 3)

    def test_rating_an_index_that_names_nothing_is_refused_not_guessed(self) -> None:
        result = self.api.set_game_rating(99, 5)

        self.assertEqual(result, {"success": False, "reason": "invalid_index"})
        self.assertEqual([self._stored_rating(i) for i in range(3)], [0, 0, 0])

    def test_reading_a_rating_for_nothing_is_unrated(self) -> None:
        self.assertEqual(self.api.get_game_rating(99), 0)

    def test_launching_an_index_that_names_nothing_is_refused(self) -> None:
        result = self.api.launch_table(99)

        self.assertEqual(result, {"success": False, "reason": "invalid_index"})

    def test_selecting_an_index_that_names_nothing_raises_no_event(self) -> None:
        with patch("frontend.api.events.emit") as emit:
            result = self.api.notify_table_selected(99)

        self.assertEqual(result, {"success": False, "reason": "invalid_index"})
        emit.assert_not_called()

    def test_selecting_a_real_index_announces_that_game(self) -> None:
        with patch("frontend.api.events.emit") as emit:
            self.api.notify_table_selected(1)

        self.assertEqual(emit.call_args.kwargs["game"], self.games[1])


class WindowIndependenceTests(TempTree):
    """Two windows, filtered differently, give the same index to different games.

    This is why an index cannot leave the window that issued it, and the reason the
    conversion exists at all rather than being a tidy-up.
    """

    def test_the_same_index_names_different_games_in_two_windows(self) -> None:
        games = []
        for game_id, name in zip(GAME_IDS, NAMES, strict=True):
            meta = {"Info": {"Name": name, "Title": name}, "User": {},
                    "vpinfe": {"game_id": game_id}}
            games.append(fake_game(write_game(self.root, name, info=meta), name, meta=meta))

        # The library has to stay patched past construction: `entries` resolves through
        # library_resolver, which holds its own reference to the loader.
        with library_of(games):
            wheel = API(_ini(), window_name="playfield")
            backglass = API(_ini(), window_name="backglass")
            backglass.filtered_games = list(reversed(backglass.entries))

        self.assertNotEqual(wheel.game_id_at(0), backglass.game_id_at(0))


if __name__ == "__main__":
    unittest.main()
