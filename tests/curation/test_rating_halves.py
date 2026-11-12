"""Protects: (a) a saved Rating rule keeps finding the games it found when it named whole stars.

A rule used to name 1 to 5 stars and now names a half-star step, 1 to 10, the scale a game is
rated on. The one-time conversion doubles what is on disk, and the one writer a theme can reach
still takes whole stars.
"""

import json
import unittest
from types import SimpleNamespace

from common.games import collection_filters as cf
from common.games.collection_migration import RATING_HALVES_MIGRATION, ensure_rating_halves
from common.games.collection_store import COLLECTIONS_SCHEMA, CollectionStore
from common.games.game_metadata import star_words
from frontend import game_state
from tests.support.library import TempTree


class RatingHalvesMigrationTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        self.path = self.root / "collections.json"

    def _store(self) -> CollectionStore:
        return CollectionStore(str(self.path))

    def _with_rule(self, name: str, rating: str, *, or_higher: bool = False) -> None:
        store = self._store()
        store.add_filter_collection(name)
        store.set_filter(name, "rating", rating)
        store.set_filter(name, "rating_or_higher", or_higher)
        store.save()

    def _rating(self, name: str) -> object:
        return self._store().get_filters(name).get("rating")

    def test_a_whole_star_rule_is_doubled(self) -> None:
        self._with_rule("Great", "4", or_higher=True)
        self._with_rule("Perfect", "5")

        self.assertEqual(ensure_rating_halves(self._store()), 2)

        self.assertEqual((self._rating("Great"), self._rating("Perfect")), ("8", "10"))
        self.assertIs(self._store().get_filters("Great").get("rating_or_higher"), True)

    def test_the_games_it_found_are_the_games_it_still_finds(self) -> None:
        self._with_rule("Great", "4", or_higher=True)
        library = {stars: SimpleNamespace(meta_config={"User": {"Rating": stars * 2}})
                   for stars in range(0, 6)}

        ensure_rating_halves(self._store())
        rule = self._store().get_filters("Great")

        self.assertEqual({stars for stars, game in library.items() if cf.matches(rule, game)},
                         {4, 5})

    def test_it_runs_once_so_a_rule_saved_in_halves_is_not_doubled_again(self) -> None:
        self._with_rule("Great", "4")
        ensure_rating_halves(self._store())

        self.assertEqual(ensure_rating_halves(self._store()), 0)
        self.assertEqual(self._rating("Great"), "8")

    def test_a_rule_that_names_no_rating_is_left_alone(self) -> None:
        self._with_rule("Anything", "All")

        self.assertEqual(ensure_rating_halves(self._store()), 0)
        self.assertEqual(self._rating("Anything"), "All")

    def test_it_records_that_it_ran_even_when_nothing_moved(self) -> None:
        self._with_rule("Anything", "All")

        ensure_rating_halves(self._store())

        self.assertTrue(self._store().has_migrated(RATING_HALVES_MIGRATION))

    def test_a_file_from_a_newer_build_is_left_alone(self) -> None:
        self.path.write_text(json.dumps({
            "schema": COLLECTIONS_SCHEMA + 1,
            "collections": [{"name": "Great", "type": "filter", "filters": {"rating": "4"}}],
        }), encoding="utf-8")

        self.assertEqual(ensure_rating_halves(self._store()), 0)
        self.assertEqual(self._rating("Great"), "4")


class WhatATextSaysTests(unittest.TestCase):
    def test_a_step_is_named_in_stars(self) -> None:
        self.assertEqual([star_words(step) for step in (0, 1, 2, 7, 8, 10)],
                         ["0", "½", "1", "3½", "4", "5"])


class ThemeWriterTests(unittest.TestCase):
    """A theme names whole stars through `save_filter_collection`, as it always has."""

    def _saved(self, rating: str) -> dict:
        saved: dict = {}
        api = SimpleNamespace(library=SimpleNamespace(
            save_filter=lambda name, filters, order_by, direction: saved.update(filters)))

        game_state.save_current_filter_collection(
            api, "Mine", "All", "All", "All", "All", "All", "title", rating, "true")
        return saved

    def test_whole_stars_are_stored_as_steps(self) -> None:
        self.assertEqual(self._saved("4")["rating"], "8")

    def test_all_is_stored_as_all(self) -> None:
        self.assertEqual(self._saved("All")["rating"], "All")


if __name__ == "__main__":
    unittest.main()
