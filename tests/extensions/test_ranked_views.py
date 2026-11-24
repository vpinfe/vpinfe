"""A ranked view of a Community list: the order it puts what it relates to in, the read
that keeps that order current, and a collection ordered by it."""

from __future__ import annotations

import configparser
import json
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from common import events
from common.games import community_lists, rankings
from common.games.collection_resolver import resolve, resolve_games
from common.games.collection_store import CollectionStore
from common.games.game_identity import game_id
from frontend import game_state
from frontend.api import API
from frontend.library_resolver import LibraryResolver
from tests.extensions.test_derived_tags import DerivedTagCase, _game
from tests.support.library_loader import library_of

TOP_RATED_TOKEN = "challenge/ratings/top"
BY_BUILD = "challenge/builds/top"

LISTING = {"columns": [{"field": "id", "kind": "text"},
                       {"field": "rating", "kind": "number"},
                       {"field": "ratings", "kind": "number"},
                       {"field": "year", "kind": "number"},
                       {"field": "played", "kind": "date"}],
           "relation": {"field": "id", "keys": "vps_entry"}}
TOP_RATED = {"key": "top", "ranks": True,
             "sort": [{"field": "rating", "desc": True}, {"field": "ratings", "desc": True}]}


def _ranked(rows: list[dict], view: dict = TOP_RATED) -> list[tuple[str, int]]:
    return sorted(rankings.ranks(rows, LISTING, view).items(), key=lambda one: one[1])


class TheOrder(unittest.TestCase):
    def test_each_field_sorts_in_its_own_direction(self) -> None:
        view = {"sort": [{"field": "rating", "desc": True}, {"field": "year"}]}

        self.assertEqual([("b", 1), ("a", 2), ("c", 3)],
                         _ranked([{"id": "a", "rating": 8, "year": 1995},
                                  {"id": "b", "rating": 8, "year": 1992},
                                  {"id": "c", "rating": 7, "year": 1980}], view))

    def test_a_row_with_no_number_ranks_nowhere(self) -> None:
        self.assertEqual([("a", 1)],
                         _ranked([{"id": "a", "rating": 7.5, "ratings": 3},
                                  {"id": "b", "rating": None, "ratings": 9},
                                  {"id": "c", "ratings": 4}]))

    def test_a_missing_later_field_sorts_after_one_that_has_it(self) -> None:
        self.assertEqual([("a", 1), ("b", 2)],
                         _ranked([{"id": "b", "rating": 8}, {"id": "a", "rating": 8,
                                                             "ratings": 1}]))

    def test_rows_that_sort_the_same_share_a_rank(self) -> None:
        self.assertEqual([("a", 1), ("b", 1), ("c", 2)],
                         _ranked([{"id": "a", "rating": 8, "ratings": 2},
                                  {"id": "b", "rating": 8, "ratings": 2},
                                  {"id": "c", "rating": 6, "ratings": 9}]))

    def test_an_id_on_several_rows_takes_its_best(self) -> None:
        """A leaderboard lists a machine once for every score on it."""
        self.assertEqual([("a", 1), ("b", 2)],
                         _ranked([{"id": "b", "rating": 9}, {"id": "a", "rating": 5},
                                  {"id": "a", "rating": 9.5}]))

    def test_dates_rank_newest_first_when_the_view_says_so(self) -> None:
        view = {"sort": [{"field": "played", "desc": True}]}

        self.assertEqual([("new", 1), ("old", 2)],
                         _ranked([{"id": "old", "played": "2026-01-02T10:00:00Z"},
                                  {"id": "new", "played": "2026-09-20T10:00:00Z"},
                                  {"id": "never", "played": ""}], view))

    def test_a_list_that_relates_to_nothing_ranks_nothing(self) -> None:
        self.assertEqual([], rankings.views_of({"views": [TOP_RATED]}))


class TheRead(DerivedTagCase):
    def told(self) -> list[dict]:
        heard: list[dict] = []

        def hear(**payload) -> None:
            heard.append(payload)

        events.subscribe(events.COLLECTIONS_CHANGED, hear)
        self.addCleanup(events.unsubscribe, events.COLLECTIONS_CHANGED, hear)
        return heard

    def test_a_ranked_list_is_kept_by_the_same_read_as_the_tags(self) -> None:
        self.week(ratings="afm-entry=8.1,mm-entry=")
        self.read()

        said = community_lists.kept("challenge", "ratings")
        self.assertEqual(["afm-entry", "mm-entry"], [one["vps_id"] for one in said["rows"]])
        self.assertTrue(said["read_at"])

    def test_a_read_that_moves_the_order_tells_vpinfe(self) -> None:
        heard = self.told()
        self.week(ratings="afm-entry=8.1,mm-entry=7.9")
        self.read()
        self.week(ratings="afm-entry=8.1,mm-entry=8.4")

        self.assertTrue(self.read())
        self.assertEqual(2, len(heard))

    def test_new_numbers_in_the_same_order_tell_nobody(self) -> None:
        self.week(ratings="afm-entry=8.1,mm-entry=7.9")
        self.read()
        heard = self.told()
        self.week(ratings="afm-entry=8.3,mm-entry=7.2")

        self.assertFalse(self.read())
        self.assertEqual([], heard)

    def test_a_failed_read_keeps_the_last_good_list(self) -> None:
        self.week(ratings="afm-entry=8.1")
        self.read()
        self.week(ratings="mm-entry=9", fail=True)
        self.read()

        self.assertEqual(["afm-entry"], [one["vps_id"] for one in
                                         community_lists.kept("challenge", "ratings")["rows"]])

    def test_a_stopped_extension_s_lists_are_not_read(self) -> None:
        self.registry.disable("challenge", "switched off")

        self.assertEqual([], community_lists.reading())

    def test_the_community_page_s_own_read_moves_the_order_too(self) -> None:
        community_lists.keep("challenge", "ratings",
                             [{"vps_id": "afm-entry", "rating": 8.0}])
        heard = self.told()

        community_lists.keep("challenge", "ratings",
                             [{"vps_id": "afm-entry", "rating": 8.0},
                              {"vps_id": "mm-entry", "rating": 9.0}])

        self.assertEqual(1, len(heard))


class RankedCase(DerivedTagCase):
    """Four games: two the list rates, one it holds with no rating, one it does not hold."""

    def setUp(self) -> None:
        super().setUp()
        self.bk = _game("bk", "Black Knight", "bk-entry", {"vpw": "bk-vpw"}, default="vpw")
        self.cv = _game("cv", "Cirqus Voltaire", "cv-entry", {"vpw": "cv-vpw"},
                        default="vpw")
        self.games.extend([self.cv, self.bk])
        self.week(ratings="afm-entry=8.1,mm-entry=9,bk-entry=")
        self.read()
        self.collections = CollectionStore(str(self.root / "collections.json"))
        self.order("Top Rated", TOP_RATED_TOKEN)

    def order(self, name: str, by: str) -> None:
        self.collections.add_collection(name)
        self.collections.make_filter_collection(name, {}, order={"by": by})

    def ids(self, name: str = "Top Rated") -> list[str]:
        return [game_id(entry.game) for entry in resolve(name, self.collections, self.games)]


class InACollection(RankedCase):
    def test_rated_games_lead_in_the_view_s_order_and_the_rest_follow_by_title(self) -> None:
        self.assertEqual(["mm", "afm", "bk", "cv"], self.ids())

    def test_a_limit_fills_from_rated_games_first(self) -> None:
        self.collections.set_limit("Top Rated", 2)

        self.assertEqual(["mm", "afm"], self.ids())

    def test_a_direction_stored_beside_it_does_not_turn_it_around(self) -> None:
        self.collections.set_order("Top Rated", TOP_RATED_TOKEN, "desc")

        self.assertEqual(["mm", "afm", "bk", "cv"], self.ids())

    def test_the_management_lens_holds_the_same_order(self) -> None:
        self.assertEqual(["mm", "afm", "bk", "cv"],
                         [game_id(one) for one in
                          resolve_games("Top Rated", self.collections, self.games)])

    def test_the_next_read_moves_it(self) -> None:
        self.week(ratings="afm-entry=9.5,mm-entry=9")
        self.read()

        self.assertEqual(["afm", "mm", "bk", "cv"], self.ids())

    def test_a_stopped_extension_leaves_it_in_title_order(self) -> None:
        self.registry.disable("challenge", "switched off")

        self.assertEqual(["afm", "bk", "cv", "mm"], self.ids())

    def test_a_view_by_release_ranks_the_table_the_collection_shows(self) -> None:
        """The newer build of Attack from Mars tops the list, and the collection shows
        its default, the older one, which the list does not rank."""
        self.week(builds="afm-1-3=9,mm-vpw=8,bk-vpw=7")
        self.read()
        self.order("Builds", BY_BUILD)

        self.assertEqual(["mm", "bk", "afm", "cv"], self.ids("Builds"))
        self.assertEqual(["mm", "bk", "afm", "cv"],
                         [game_id(one) for one in
                          resolve_games("Builds", self.collections, self.games)])

    def test_a_view_over_vpinfe_ids_ranks_by_vpinfe_s_own_ids(self) -> None:
        by_game = {"challenge/kept/top": ("vpinfe_game", {"mm": 0, "afm": 1})}
        by_table = {"challenge/kept/top": ("vpinfe_table", {"vpw": 0})}

        with patch.object(rankings, "_index", return_value=by_game):
            self.assertEqual(["mm", "afm", "bk", "cv"],
                             [game_id(one) for one in
                              sorted([self.afm, self.bk, self.cv, self.mm],
                                     key=rankings.rank_key("challenge/kept/top"))])
        with patch.object(rankings, "_index", return_value=by_table):
            self.assertEqual(["cv", "mm", "bk", "afm"],
                             [game_id(one) for one in
                              sorted([self.cv, self.mm, self.bk, self.afm],
                                     key=rankings.rank_key("challenge/kept/top"))])

    def test_the_frontend_is_handed_it_in_that_order(self) -> None:
        with patch("frontend.library_resolver.get_collections_manager",
                   lambda: self.collections):
            ini: Any = SimpleNamespace(config=configparser.ConfigParser(), save=lambda: None)
            ini.config.add_section("general")
            api = API.__new__(API)
            api._ini_config = ini
            api.library = LibraryResolver(ini, games=list(self.games))
            game_state.apply_collection(api, "Top Rated")

            said = json.loads(api.library.payload(game_state.CURRENT_CONTRACT,
                                                  collection="Top Rated"))

        self.assertEqual(["mm", "afm", "bk", "cv"],
                         [one["game"]["id"] for one in said["entries"]])
        self.assertEqual("", said["group_by"])

    def test_the_wheel_re_sorted_ranks_the_table_each_entry_shows(self) -> None:
        """The collection holds the newer Attack from Mars build, which the list ranks
        first, and not the older default the game offers, which it does not rank."""
        self.week(builds="afm-1-3=9,mm-vpw=8,bk-vpw=7")
        self.read()
        self.collections.add_collection("Builds")
        self.collections.set_order("Builds", BY_BUILD)
        self.collections.add_member("Builds", "afm", table_id="new")
        for gid in ("cv", "bk", "mm"):
            self.collections.add_member("Builds", gid)
        ranked = ["afm/new", "mm/vpw", "bk/vpw", "cv/vpw"]

        with patch("frontend.library_resolver.get_collections_manager",
                   lambda: self.collections), library_of(self.games):
            ini: Any = SimpleNamespace(config=configparser.ConfigParser(), save=lambda: None)
            ini.config.add_section("general")
            api = API.__new__(API)
            api._ini_config = ini
            api.library = LibraryResolver(ini, games=list(self.games))
            game_state.apply_collection(api, "Builds")

            def shown() -> list[str]:
                return [f"{game_id(entry.game)}/{entry.table_id}" for entry in api.entries]

            game_state.refresh_view(api)
            self.assertEqual(ranked, shown())
            api.apply_sort(BY_BUILD)
            self.assertEqual(ranked, shown())


class OverTheApi(RankedCase):
    def setUp(self) -> None:
        super().setUp()
        self.collections.save()
        for target in ("common.games.collection_ops.get_collections_manager",
                       "common.games.collections_service.get_collections_manager"):
            patcher = patch(target, lambda: self.collections)
            patcher.start()
            self.addCleanup(patcher.stop)
        catalog = patch("common.games.game_repository.catalog",
                        lambda: {game_id(one): one for one in self.games})
        catalog.start()
        self.addCleanup(catalog.stop)

    def test_a_collection_says_which_view_orders_it_and_how_old_the_read_is(self) -> None:
        said = self.client.get("/collections/Top%20Rated").json()

        self.assertEqual((TOP_RATED_TOKEN, "asc"), (said["order_by"], said["direction"]))
        ranking = said["ranking"]
        self.assertEqual(("challenge", "ratings", "top", "Ratings", "Top Rated", True),
                         tuple(ranking[key] for key in ("extension", "list", "view",
                                                        "title", "name", "offered")))
        self.assertEqual(community_lists.kept("challenge", "ratings")["read_at"],
                         ranking["read_at"])

    def test_a_collection_in_a_built_in_order_has_no_ranking(self) -> None:
        self.client.patch("/collections/Top%20Rated", json={"order_by": "title"})

        self.assertIsNone(self.client.get("/collections/Top%20Rated").json()["ranking"])

    def test_one_is_made_ordered_by_a_view(self) -> None:
        made = self.client.post("/collections", json={
            "name": "Builds", "filters": {"order_by": BY_BUILD}})

        self.assertEqual(201, made.status_code)
        self.assertEqual((BY_BUILD, "asc"),
                         (made.json()["order_by"], made.json()["direction"]))

    def test_a_ranked_order_run_from_the_bottom_is_refused(self) -> None:
        for sent in ({"direction": "desc"},
                     {"order_by": TOP_RATED_TOKEN, "direction": "desc"}):
            with self.subTest(sent=sent):
                refused = self.client.patch("/collections/Top%20Rated", json=sent)

                self.assertEqual(400, refused.status_code)
        made = self.client.post("/collections", json={
            "name": "Bottom", "filters": {"order_by": BY_BUILD, "direction": "desc"}})
        self.assertEqual(400, made.status_code)

    def test_moving_from_a_descending_order_to_a_ranked_one_runs_from_the_top(self) -> None:
        self.client.patch("/collections/Top%20Rated",
                          json={"order_by": "rating", "direction": "desc"})

        said = self.client.patch("/collections/Top%20Rated",
                                 json={"order_by": TOP_RATED_TOKEN}).json()

        self.assertEqual((TOP_RATED_TOKEN, "asc"), (said["order_by"], said["direction"]))

    def test_a_view_nobody_offers_is_refused_and_the_offered_ones_are_named(self) -> None:
        refused = self.client.patch("/collections/Top%20Rated",
                                    json={"order_by": "challenge/ratings/bottom"})

        self.assertEqual(400, refused.status_code)
        self.assertIn(BY_BUILD, refused.json()["error"]["details"]["choices"])

    def test_a_stopped_extension_s_order_stays_and_says_so(self) -> None:
        self.registry.disable("challenge", "switched off")

        paged = self.client.patch("/collections/Top%20Rated", json={"paging_group": "count"})

        self.assertEqual(200, paged.status_code)
        said = paged.json()
        self.assertEqual(TOP_RATED_TOKEN, said["order_by"])
        self.assertEqual(("challenge", "Challenge", False),
                         tuple(said["ranking"][key]
                               for key in ("extension", "display_name", "offered")))


if __name__ == "__main__":
    unittest.main()
