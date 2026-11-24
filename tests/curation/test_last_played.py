"""Last Played derives itself from the play dates, instead of being maintained by hand.

The launcher used to push a game id onto the front of a member array on every launch and
trim it to 30. It is an ordinary filter collection now, so the cases that matter are the
conversion of a file that still holds the old array, and the promise that the conversion
happens exactly once - a user who deletes the collection, or turns it back into a list
they picked, must not find it rebuilt at the next start.
"""

import json
from types import SimpleNamespace

from common.games.collection_migration import (
    LAST_PLAYED_MIGRATION,
    LAST_PLAYED_NAME,
    ensure_last_played,
)
from common.games.collection_resolver import resolve
from common.games.collection_store import CollectionStore
from tests.support.library import TempTree


def _game(gid, title, last_run=0):
    return SimpleNamespace(
        game_dir_name=title,
        creation_time=0,
        meta_config={
            "Info": {"Title": title, "Manufacturer": "Bally", "Year": "1995",
                     "Type": "SS", "Themes": []},
            "User": {"Rating": 0, "LastRun": last_run, "StartCount": 1 if last_run else 0},
            "vpinfe": {"game_id": gid},
            "tables": {f"{gid}-t": {"id": f"{gid}-t", "filename": f"{title}.vpx"}},
        },
    )


class LastPlayedInstallTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        self.path = self.root / "collections.json"

    def _store(self):
        return CollectionStore(str(self.path))

    def _record(self, store, name=LAST_PLAYED_NAME):
        return next(r for r in store.records if r["name"] == name)

    def _write(self, payload):
        self.path.write_text(json.dumps(payload), encoding="utf-8")

    def test_a_fresh_install_is_seeded_with_it(self) -> None:
        """Nothing creates it on first launch any more, so without this a new user would
        have no Last Played at all."""
        store = self._store()

        self.assertTrue(ensure_last_played(store))

        reopened = self._store()
        self.assertEqual(reopened.get_collections_name(), [LAST_PLAYED_NAME])
        self.assertTrue(reopened.is_filter_based(LAST_PLAYED_NAME))
        self.assertEqual(reopened.get_filters(LAST_PLAYED_NAME)["played"], True)
        self.assertEqual(reopened.get_order(LAST_PLAYED_NAME),
                         {"by": "last_played", "direction": "desc", "paging_group": None})
        self.assertEqual(reopened.get_limit(LAST_PLAYED_NAME), 30)

    def test_a_maintained_row_is_converted_in_place(self) -> None:
        """Its name, icon and position are the user's; only the definition changes."""
        self._write({"schema": 2, "collections": [
            {"name": "Favorites", "type": "manual", "image": "",
             "members": [{"game": "a"}]},
            {"name": LAST_PLAYED_NAME, "type": "manual", "image": "recent.png",
             "members": [{"game": "b"}, {"game": "a"}],
             "order": {"by": "manual", "direction": "asc"}},
            {"name": "Tournament", "type": "manual", "image": "", "members": []},
        ]})

        ensure_last_played(self._store())

        reopened = self._store()
        self.assertEqual(reopened.get_collections_name(),
                         ["Favorites", LAST_PLAYED_NAME, "Tournament"])
        record = self._record(reopened)
        self.assertEqual(record["image"], "recent.png")
        self.assertEqual(record["type"], "filter")
        self.assertNotIn("members", record)
        self.assertEqual(record["order"], {"by": "last_played", "direction": "desc"},
                         "the stored row says nothing about paging, so it follows the player")

    def test_it_does_not_run_a_second_time(self) -> None:
        store = self._store()
        ensure_last_played(store)

        self.assertFalse(ensure_last_played(self._store()))

    def test_a_collection_the_user_deleted_is_not_rebuilt(self) -> None:
        """The whole reason the run is recorded: seeding on every start would put back
        something the user removed on purpose."""
        ensure_last_played(self._store())
        with self._store().mutate() as store:
            store.delete_collection(LAST_PLAYED_NAME)

        ensure_last_played(self._store())

        self.assertEqual(self._store().get_collections_name(), [])

    def test_a_collection_the_user_made_manual_again_is_left_alone(self) -> None:
        ensure_last_played(self._store())
        with self._store().mutate() as store:
            record = self._record(store)
            record["type"] = "manual"
            record["members"] = [{"game": "a"}]

        ensure_last_played(self._store())

        self.assertEqual(self._record(self._store())["members"], [{"game": "a"}])

    def test_the_run_is_recorded_in_the_file(self) -> None:
        ensure_last_played(self._store())

        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertIn(LAST_PLAYED_MIGRATION, saved["migrations"])

    def test_a_file_from_a_newer_vpinfe_is_left_alone(self) -> None:
        """An older build must not rewrite data it does not understand."""
        self._write({"schema": 99, "collections": [
            {"name": LAST_PLAYED_NAME, "type": "manual", "image": "",
             "members": [{"game": "b"}]}]})

        self.assertFalse(ensure_last_played(self._store()))
        self.assertEqual(self._record(self._store())["type"], "manual")


class LastPlayedResolutionTests(TempTree):
    """What the collection now answers, once it is a definition rather than a list."""

    def setUp(self) -> None:
        super().setUp()
        self.path = self.root / "collections.json"
        self.games = [_game("a", "Alpha", last_run=300),
                      _game("b", "Bravo", last_run=100),
                      _game("c", "Charlie", last_run=200),
                      _game("d", "Delta")]
        ensure_last_played(CollectionStore(str(self.path)))
        self.collections = CollectionStore(str(self.path))

    def _titles(self):
        return [e.game.meta_config["Info"]["Title"]
                for e in resolve(LAST_PLAYED_NAME, self.collections, self.games)]

    def test_it_holds_the_games_with_a_play_date_most_recent_first(self) -> None:
        self.assertEqual(self._titles(), ["Alpha", "Charlie", "Bravo"])

    def test_a_game_that_has_never_been_played_is_left_out(self) -> None:
        """Not merely sorted last. Ordering the library by date and taking the first 30
        pads a short list with games nobody has touched, which is what the axis fixes."""
        self.assertNotIn("Delta", self._titles())

    def test_a_fresh_install_resolves_to_nothing_rather_than_failing(self) -> None:
        """A seeded collection on a machine that has played nothing is empty by design,
        and it is the first thing that ships that way."""
        self.assertEqual(resolve(LAST_PLAYED_NAME, self.collections,
                                 [_game("d", "Delta")]), [])

    def test_it_reproduces_the_order_the_maintained_array_held(self) -> None:
        """The acceptance test, as a unit.

        The launcher wrote the array and the play date on the same launch, so the array
        it left behind is rebuilt here as a manual collection and resolved beside the
        derived one. Same games, same order, from the two records of the same launches.
        """
        with self.collections.mutate() as store:
            store.add_collection("As Maintained",
                                 [{"game": "a"}, {"game": "c"}, {"game": "b"}])
            store.set_order("As Maintained", "manual")
        collections = CollectionStore(str(self.path))

        stored = [e.game.meta_config["Info"]["Title"]
                  for e in resolve("As Maintained", collections, self.games)]

        self.assertEqual(self._titles(), stored)

    def test_it_keeps_no_more_than_the_collection_says(self) -> None:
        many = [_game(f"g{i}", f"Game {i:02d}", last_run=1000 + i) for i in range(40)]

        resolved = resolve(LAST_PLAYED_NAME, self.collections, many)

        self.assertEqual(len(resolved), 30)
        self.assertEqual(resolved[0].game.meta_config["Info"]["Title"], "Game 39")
