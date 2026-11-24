"""Re-deriving the wheel's view after something changed the library underneath it.

A view is a collection resolved over game objects, and both go stale from outside: a
finished session moves a game up a LastRun wheel, and a Manager edit replaces the game
object rather than mutating the one the view holds. Rebuilding only the payload leaves
every one of those showing what it showed at boot.
"""

from __future__ import annotations

import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from common.games.collection_resolver import resolve
from common.games.collection_store import BUILTIN_ALL, CollectionStore
from frontend import game_state


def _game(name, last_run=0, game_id=""):
    return types.SimpleNamespace(
        game_dir_name=name,
        full_path_game=f"/games/{name}",
        full_path_vpx_file=f"/games/{name}/{name}.vpx",
        meta_config={"Info": {"Title": name}, "User": {"LastRun": last_run},
                     "vpinfe": {"game_id": game_id or name.lower()}},
    )


class _Library:
    """Just the part `refresh_view` reaches for: where the library comes from, and the
    collection resolved off it. A remote library is somebody else's, so asking the view rather
    than the disk is what keeps the two answers from diverging."""

    def __init__(self, games, store):
        self.all_games = list(games)
        self.store = store

    def reload(self):
        return self.all_games

    def show_all_if_gone(self):
        return False

    def resolve_view(self, collection, criteria=None):
        if criteria:
            self.store.set_view_filters(criteria)
        return resolve(collection, self.store, self.all_games)


class _Api:
    """The parts of the frontend API a resolved list is derived from."""

    def __init__(self, games, store, sort="title", order="asc",
                 collection=BUILTIN_ALL):
        self.library = _Library(games, store)
        self.all_games = list(games)
        self.filtered_games = []
        self.current_filters = game_state.default_filter_state()
        self.current_collection = collection
        self.current_sort = sort
        self.current_order = order
        self.rebuilds = 0

    def _rebuild_entries(self):
        self.rebuilds += 1


class ViewRefreshTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = CollectionStore(str(Path(tmp.name) / "collections.json"))

    def _api(self, games, **kwargs):
        return _Api(games, self.store, **kwargs)

    def _refresh(self, api, library):
        # The resolver is what reads the library now, so that is what a test stands
        # up: `refresh_view` asks it rather than loading a second time behind its back.
        api.library.all_games = list(library)
        game_state.refresh_view(api)

    def _shown(self, api):
        return [entry.game.game_dir_name for entry in api.filtered_games]

    def test_the_view_picks_up_the_replacement_game_object(self):
        """refresh_game swaps the object out. A view holding the old one never sees the
        edit, however many times it rebuilds its payload."""
        api = self._api([_game("Alpha")])
        fresh = _game("Alpha")

        self._refresh(api, [fresh])

        self.assertIs(api.all_games[0], fresh)
        self.assertIs(api.filtered_games[0].game, fresh)

    def test_a_play_axis_reorders_the_wheel(self):
        library = [_game("Alpha", last_run=10), _game("Bravo", last_run=20)]
        api = self._api(library, sort="last_played", order="desc")

        self._refresh(api, library)
        self.assertEqual(self._shown(api), ["Bravo", "Alpha"])

        # Alpha was just played, which is the whole point of refreshing.
        library[0].meta_config["User"]["LastRun"] = 30
        self._refresh(api, library)

        self.assertEqual(self._shown(api), ["Alpha", "Bravo"])

    def test_collection_membership_is_re_resolved(self):
        """A collection gains a game while it is on screen, and a view of it has to
        notice - it holds what the collection says now, not what it said at boot."""
        library = [_game("Alpha"), _game("Bravo")]
        self.store.add_collection("Favorites", ["alpha", "bravo"])
        api = self._api([library[0]], collection="Favorites")

        self._refresh(api, library)

        self.assertEqual(self._shown(api), ["Alpha", "Bravo"])

    def test_a_collection_does_not_reimpose_its_own_sort(self):
        """Choosing a collection applies its sort once. Sorting differently afterwards is
        the player's choice and outlives a refresh."""
        library = [_game("Alpha"), _game("Bravo")]
        self.store.add_collection("Favorites", ["alpha", "bravo"])
        self.store.set_order("Favorites", "title", "asc")
        api = self._api(library, sort="title", order="desc",
                        collection="Favorites")

        self._refresh(api, library)

        self.assertEqual(api.current_sort, "title")
        self.assertEqual(api.current_order, "desc")
        self.assertEqual(self._shown(api), ["Bravo", "Alpha"])

    def test_a_curated_order_survives_a_refresh(self):
        """`Manual` is not a sort `apply_sort` knows, which is what leaves the curator's
        order alone when the library moves under it."""
        library = [_game("Alpha"), _game("Bravo")]
        self.store.add_collection("Tournament", ["bravo", "alpha"])
        self.store.set_order("Tournament", "manual")
        api = self._api(library, sort="manual",
                        collection="Tournament")

        self._refresh(api, library)

        self.assertEqual(self._shown(api), ["Bravo", "Alpha"])

    def test_the_filters_the_player_set_still_apply(self):
        library = [_game("Alpha"), _game("Bravo")]
        api = self._api(library)
        api.current_filters["letter"] = "B"

        self._refresh(api, library)

        self.assertEqual(self._shown(api), ["Bravo"])

    def test_the_entries_are_rebuilt(self):
        """The entry list caches the table dict out of the meta, so a refresh that left
        it alone would keep reporting the old counters at contract 2."""
        api = self._api([_game("Alpha")])

        self._refresh(api, [_game("Alpha")])

        self.assertEqual(api.rebuilds, 1)


if __name__ == "__main__":
    unittest.main()
