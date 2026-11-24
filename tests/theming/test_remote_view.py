"""A view whose library is on another machine.

`network.library_url` is the one setting that decides it: empty - the default, and every
single-machine setup - and this install reads its own disk exactly as it always has. Set,
and the list it holds is entries the other install already resolved.

The two are different kinds of thing, which is the whole of what the remote path has to
get right: a local view resolves a collection into entries, and a remote one holds entries
that cannot be re-resolved, because the table dicts the resolver reads stayed over there.
"""

from __future__ import annotations

import configparser
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import quote

from common.games import remote_library
from common.games.collection_resolver import Entry
from common.games.collection_store import BUILTIN_ALL, CollectionStore
from common.games.wire_entry import WireGame
from common.service_errors import BlockedError
from frontend import game_state
from frontend import library_resolver as frontend_library
from frontend.api import API
from frontend.library_resolver import LibraryResolver

TITLES = ("Attack from Mars", "Medieval Madness", "Twilight Zone")


def _ini(url: str = "") -> SimpleNamespace:
    config = configparser.ConfigParser()
    config.read_string(f"[general]\n[network]\nlibrary_url = {url}\n")
    return SimpleNamespace(config=config)


def _wire_entry(title: str, created: str) -> dict:
    """What a library sends: resolved, one row per entry, and carrying no local path."""
    return {"game": {"id": title[:4], "name": title, "manufacturer": "Bally",
                     "year": "1995", "type": "SS", "themes": ["Space"],
                     "dir_name": f"{title} (Bally 1995)", "created_at": created,
                     "user": {"rating": 4, "favorite": False, "tags": [],
                              "last_played": None, "play_count": 0,
                              "play_time_seconds": 0}},
            "table": {"id": f"t-{title[:4]}", "filename": f"{title}.vpx",
                      "version": "", "rom": "", "default": True, "authors": [],
                      "detects": {}, "user": {}},
            "assets": {"pup_pack": False, "alt_color": False, "alt_sound": False},
            "media": ["wheel"], "siblings": 1}


PAYLOAD = {"entries": [_wire_entry(title, f"2026-0{index + 1}-01T00:00:00Z")
                       for index, title in enumerate(TITLES)]}


class LibraryUrlTests(unittest.TestCase):
    def test_no_library_set_is_the_default_and_stays_local(self) -> None:
        """The parity requirement: an install that says nothing behaves as it always has."""
        for text in ("[general]\n", "[network]\nlibrary_url =\n", "[network]\nlibrary_url =    \n"):
            with self.subTest(config=text):
                config = configparser.ConfigParser()
                config.read_string(text)
                ini = SimpleNamespace(config=config)

                self.assertEqual(frontend_library.library_url(ini), "")
                self.assertFalse(LibraryResolver(ini, games=[])._remote)

    def test_a_library_url_makes_the_view_remote(self) -> None:
        self.assertEqual(frontend_library.library_url(_ini("http://elsewhere:8001")),
                         "http://elsewhere:8001")

    def test_config_that_cannot_be_read_is_local_rather_than_fatal(self) -> None:
        """An install that cannot read its setting holds its own library, which is the
        behavior that needs no network to work."""
        self.assertEqual(frontend_library.library_url(SimpleNamespace(config=None)), "")


class RemoteViewTests(unittest.TestCase):
    def setUp(self) -> None:
        patcher = patch.object(remote_library.http_client, "get_json",
                               lambda *a, **k: PAYLOAD)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.library = LibraryResolver(_ini("http://library.example:8001"))

    def test_it_holds_what_the_library_sent(self) -> None:
        self.assertTrue(self.library._remote)
        self.assertEqual(len(self.library.entries), len(TITLES))
        self.assertTrue(all(isinstance(entry, Entry) for entry in self.library.entries))
        self.assertTrue(all(isinstance(entry.game, WireGame)
                            for entry in self.library.entries))

    def test_the_entries_are_kept_rather_than_re_derived(self) -> None:
        """The resolver reads a game's table dicts out of its `.info`, and the library
        kept those - re-resolving would quietly produce an empty wheel."""
        self.assertEqual([entry.table_id for entry in self.library.entries],
                         [f"t-{title[:4]}" for title in TITLES])
        self.assertEqual([entry.filename for entry in self.library.entries],
                         [f"{title}.vpx" for title in TITLES])

    def test_it_serializes_a_theme_payload(self) -> None:
        """What the wheel actually renders, built here from the library's answer."""
        payload = json.loads(self.library.payload(2))

        self.assertEqual(payload["count"], len(TITLES))
        self.assertEqual([entry["game"]["name"] for entry in payload["entries"]],
                         sorted(TITLES))
        for entry in payload["entries"]:
            self.assertEqual(entry["game"]["themes"], ["Space"])
            self.assertEqual(entry["media"], ["wheel"])

    def test_no_path_from_the_library_reaches_the_payload(self) -> None:
        payload = json.loads(self.library.payload(2))

        for entry in payload["entries"]:
            self.assertEqual(entry["game"]["path"], "")
            self.assertEqual(entry["table"]["path"], "")

    def test_every_sort_works_on_what_arrived(self) -> None:
        """The sorts read a title and a creation time off whatever they are handed, and
        an entry forwards both - so an install sorts its wheel without a local library."""
        for sort in ("Alpha", "Newest", "LastRun", "Highest StartCount", "RunTime"):
            with self.subTest(sort=sort):
                self.library.current_sort = sort
                self.library.current_order = "Ascending"
                self.library.reset_to_default()

                self.assertEqual(len(self.library.entries), len(TITLES))

    def test_newest_orders_by_what_the_library_stamped(self) -> None:
        """The one sort that cannot work without `created_at` crossing: the timestamps
        decide it, so a reversed order is the field arriving rather than a tiebreak."""
        rows = list(self.library.entries)
        game_state.apply_sort(rows, "Newest", "Descending")

        self.assertEqual([row.game.meta_config["Info"]["Title"] for row in rows],
                         list(reversed(TITLES)))


LIBRARY = "http://library.example:8001"


def _resource(name: str, count: int, *, image: str | None = None,
              in_frontend: bool = True, wheels: tuple[str, ...] = ()) -> dict:
    """A collection as the library's GET /api/v1/collections lists it."""
    return {"name": name, "type": "manual", "image": image,
            "image_version": "7" if image else None, "in_frontend": in_frontend,
            "count": count, "game_count": count, "game_wheels": list(wheels),
            "order_by": "title", "direction": "asc", "paging_group": ""}


class LibraryCollectionTests(unittest.TestCase):
    """A collection the library install holds, ranked there and chosen here - where this
    install's own file holds another by the same name."""

    RANKED = ("Twilight Zone", "Attack from Mars")

    def setUp(self) -> None:
        self.sent = {"entries": [_wire_entry(title, "2026-01-01T00:00:00Z")
                                 for title in self.RANKED]}
        self.resource = _resource("Top Ranked", 2) | {
            "order_by": "challenge/ratings/top", "direction": "desc",
            "paging_group": "count"}
        self.reachable = True
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = CollectionStore(str(Path(tmp.name) / "collections.json"))
        store.add_collection("Top Ranked", ["Medi"])
        store.set_order("Top Ranked", "year", "asc", paging_group="sort")
        for patcher in (patch.object(remote_library.http_client, "get_json", self._answer),
                        patch("frontend.library_resolver.get_collections_manager",
                              lambda: store)):
            patcher.start()
            self.addCleanup(patcher.stop)

        ini = _ini(LIBRARY)
        self.api = API.__new__(API)
        self.api._ini_config = ini  # type: ignore[assignment]
        self.api.library = LibraryResolver(ini)
        game_state.apply_collection(self.api, "Top Ranked")

    def _answer(self, url: str, **_: object) -> dict:
        path = url.removeprefix(LIBRARY)
        if not self.reachable:
            raise OSError("nothing answers")
        if path == "/api/v1/library/entries":
            return PAYLOAD
        if path == "/api/v1/collections/Top%20Ranked/entries":
            return self.sent
        if path == "/api/v1/collections/Top%20Ranked":
            return self.resource
        raise OSError(f"404 for {path}")

    def _titles(self) -> list[str]:
        return [entry.game.meta_config["Info"]["Title"] for entry in self.api.entries]

    def _refreshed(self) -> list[str]:
        self.api.library.mark_stale()
        self.api.get_tables()
        return self._titles()

    def test_choosing_it_shows_its_entries_at_once(self) -> None:
        self.assertEqual(self._titles(), list(self.RANKED))

    def test_a_refresh_keeps_the_order_it_arrived_in(self) -> None:
        self.assertEqual(self._refreshed(), list(self.RANKED))
        self.assertEqual(self._refreshed(), list(self.RANKED))

    def test_a_sort_picked_here_outlives_a_refresh(self) -> None:
        self.api.apply_sort("title", "asc")

        self.assertEqual(self._refreshed(), sorted(self.RANKED))

    def test_the_librarys_order_and_paging_are_taken_rather_than_this_installs(self) -> None:
        self.resource |= {"order_by": "year", "direction": "desc", "paging_group": "count"}
        game_state.apply_collection(self.api, "Top Ranked")

        self.assertEqual((self.api.current_sort, self.api.current_order), ("year", "desc"))
        self.assertEqual(self.api.paging_state()["group"], "count")

    def test_all_games_is_the_whole_library_again(self) -> None:
        game_state.apply_collection(self.api, "")

        self.assertEqual(self._titles(), sorted(TITLES))

    def test_a_collection_the_library_dropped_falls_back_to_all_games(self) -> None:
        self.api.library._gone = "Top Ranked"

        game_state.rebuild_view(self.api)

        self.assertEqual(self.api.current_collection, BUILTIN_ALL)
        self.assertEqual(self._titles(), sorted(TITLES))

    def test_a_library_that_goes_quiet_after_dropping_one_keeps_the_wheel(self) -> None:
        self.api.library._gone = "Top Ranked"
        self.reachable = False

        game_state.rebuild_view(self.api)

        self.assertEqual(self.api.current_collection, "Top Ranked")
        self.assertEqual(self._titles(), list(self.RANKED))

    def test_a_pick_the_library_cannot_answer_leaves_the_wheel_as_it_was(self) -> None:
        self.reachable = False

        with self.assertRaises(BlockedError):
            game_state.apply_collection(self.api, "")

        self.assertEqual(self.api.current_collection, "Top Ranked")
        self.assertEqual(self._titles(), list(self.RANKED))


class LibraryPickerTests(unittest.TestCase):
    """What a player offers to pick from, when its library is another install's."""

    WHEEL = "/api/v1/games/Twil/tables/t-Twil/media/wheel"
    LISTED = [_resource("Favorites", 2, image="favorites.png", wheels=(WHEEL,)),
              _resource("Hub Picks", 1),
              _resource("Kept Back", 3, in_frontend=False)]

    def setUp(self) -> None:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = CollectionStore(str(Path(tmp.name) / "collections.json"))
        store.add_collection("Favorites", ["Atta"])
        store.add_collection("Only Here", ["Medi"])
        self.reachable = True
        for patcher in (patch.object(remote_library.http_client, "get_json", self._answer),
                        patch("frontend.library_resolver.get_collections_manager",
                              lambda: store),
                        patch("common.games.collections_service.get_collections_manager",
                              lambda: store)):
            patcher.start()
            self.addCleanup(patcher.stop)

        ini = _ini(LIBRARY)
        self.api = API.__new__(API)
        self.api._ini_config = ini  # type: ignore[assignment]
        self.api.library = LibraryResolver(ini)

    def _answer(self, url: str, **_: object) -> dict:
        path = url.removeprefix(LIBRARY)
        if path == "/api/v1/library/entries":
            return PAYLOAD
        if path.endswith("/entries"):
            return {"entries": PAYLOAD["entries"][:1]}
        if not self.reachable:
            raise OSError("nothing answers")
        if path == "/api/v1/collections":
            return {"collections": self.LISTED}
        for resource in self.LISTED:
            if path == f"/api/v1/collections/{quote(resource['name'])}":
                return resource
        raise OSError(f"404 for {path}")

    def _row(self, name: str) -> dict:
        return next(item for item in self.api.get_collection_picker_items()
                    if item["name"] == name)

    def test_the_picker_offers_the_librarys_collections_and_none_of_its_own(self) -> None:
        self.assertEqual([item["name"] for item in self.api.get_collection_picker_items()],
                         ["", "Favorites", "Hub Picks"])
        self.assertEqual(self.api.get_collections(), ["Favorites", "Hub Picks"])

    def test_a_row_is_counted_and_drawn_by_the_library(self) -> None:
        favorites = self._row("Favorites")

        self.assertEqual(favorites["table_count"], 2)
        self.assertEqual(favorites["image_url"],
                         "/library/api/v1/collections/Favorites/image?v=7")
        self.assertEqual(favorites["game_wheel_urls"], [f"/library{self.WHEEL}"])
        self.assertEqual(self._row("Hub Picks")["image_url"], "")

    def test_all_games_is_counted_and_drawn_from_the_whole_library(self) -> None:
        wheels = [f"/library/api/v1/games/{title[:4]}/tables/t-{title[:4]}/media/wheel"
                  for title in TITLES]
        game_state.apply_collection(self.api, "Hub Picks")
        self.assertEqual(len(self.api.library.entries), 1)

        whole = self._row("")
        self.assertEqual(whole["table_count"], len(TITLES))
        self.assertEqual(whole["game_wheel_urls"], wheels)

    def test_a_collections_image_is_the_librarys(self) -> None:
        self.assertEqual(self.api.get_collection_image_url("Favorites"),
                         "/library/api/v1/collections/Favorites/image?v=7")
        self.assertEqual(self.api.get_collection_image_url("Only Here"), "")

    def test_a_library_that_cannot_be_reached_offers_none(self) -> None:
        self.reachable = False

        with self.assertLogs("vpinfe.frontend.library_resolver", "WARNING"):
            self.assertEqual(self.api.get_collections(), [])
            self.assertEqual([item["name"] for item in
                              self.api.get_collection_picker_items()], [""])


if __name__ == "__main__":
    unittest.main()
