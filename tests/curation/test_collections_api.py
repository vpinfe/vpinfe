"""Collections over the wire: two kinds behind one resource.

The rules under test are the ones that keep the two kinds honest - a filter
collection has no member list to edit, membership is the table's own id, and
creating one is refused rather than guessed at when the request says both things.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from starlette.testclient import TestClient

import httpapi
from common.games import collection_filters as cf
from common.games.collection_store import CollectionStore
from common.games.game import Game
from common.i18n import t
from frontend import game_state
from tests.support.library import TempTree, fake_game, write_game

GAME_ID = "aaaa1111"
OTHER_ID = "bbbb2222"


def _game(folder: str, game_id: str) -> Game:
    return fake_game(f"/games/{folder}", folder,
                     meta={"Info": {"Title": folder, "Manufacturer": "Bally"},
                           "vpinfe": {"game_id": game_id}})


def _meta(game: Game) -> dict[str, Any]:
    assert game.meta_config is not None
    return game.meta_config


class CollectionsApiTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        self.path = f"{self.root}/collections.ini"
        open(self.path, "w", encoding="utf-8").close()

        self.catalog = {GAME_ID: _game("Cactus Canyon (Bally 1998)", GAME_ID),
                        OTHER_ID: _game("Eight Ball (Bally 1977)", OTHER_ID)}

        # One manager over a throwaway file, shared by the service and the test.
        manager = CollectionStore(self.path)
        self.manager = manager
        for target in ("common.games.collection_ops.get_collections_manager",
                       "common.games.collections_service.get_collections_manager"):
            patcher = patch(target, lambda: manager)
            patcher.start()
            self.addCleanup(patcher.stop)
        catalog_patch = patch("common.games.game_repository.catalog", lambda: self.catalog)
        catalog_patch.start()
        self.addCleanup(catalog_patch.stop)

        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    # --- how big a collection is -----------------------------------------

    def test_a_limit_that_cuts_leaves_the_size_before_it(self) -> None:
        self.client.post("/collections",
                         json={"name": "Bally", "filters": {"manufacturer": ["Bally"]}})
        self.client.patch("/collections/Bally", json={"limit": 1})

        held = self.client.get("/collections/Bally").json()

        self.assertEqual((1, 2), (held["count"], held["before_limit"]))

    def test_without_a_limit_the_size_before_it_is_the_size(self) -> None:
        self.client.post("/collections",
                         json={"name": "Bally", "filters": {"manufacturer": ["Bally"]}})

        held = self.client.get("/collections/Bally").json()

        self.assertEqual((2, 2), (held["count"], held["before_limit"]))

    def test_the_wheels_of_its_games_skip_a_game_without_one(self) -> None:
        self.catalog[OTHER_ID].wheel_image_path = "/games/Eight Ball/medias/wheel.png"
        self.client.post("/collections",
                         json={"name": "Bally", "filters": {"manufacturer": ["Bally"]}})

        held = self.client.get("/collections/Bally").json()

        self.assertEqual(1, len(held["game_wheels"]))
        self.assertRegex(held["game_wheels"][0],
                         rf"^/api/v1/games/{OTHER_ID}(/tables/[^/]+)?/media/wheel$")

    def test_the_wheels_stop_where_the_limit_does(self) -> None:
        self.catalog[OTHER_ID].wheel_image_path = "/games/Eight Ball/medias/wheel.png"
        self.client.post("/collections",
                         json={"name": "Bally", "filters": {"manufacturer": ["Bally"]}})
        self.client.patch("/collections/Bally", json={"limit": 1})

        held = self.client.get("/collections/Bally").json()

        self.assertEqual([], held["game_wheels"])

    # --- in the frontend or not ------------------------------------------

    def test_a_collection_kept_out_of_the_frontend_is_still_listed_here(self) -> None:
        self.client.post("/collections", json={"name": "Favorites"})
        self.client.post("/collections", json={"name": "Workbench"})

        kept = self.client.patch("/collections/Workbench", json={"in_frontend": False})
        listed = {one["name"]: one["in_frontend"]
                  for one in self.client.get("/collections").json()["collections"]}

        self.assertFalse(kept.json()["in_frontend"])
        self.assertEqual({"Favorites": True, "Workbench": False}, listed)

    def test_the_frontend_leaves_it_out(self) -> None:
        from common.games.collections_service import get_frontend_collections
        self.client.post("/collections", json={"name": "Favorites"})
        self.client.post("/collections", json={"name": "Workbench"})
        self.client.patch("/collections/Workbench", json={"in_frontend": False})

        self.assertEqual(["Favorites"], [row["name"] for row in get_frontend_collections()])

    def test_the_one_on_screen_stays_in_the_frontend_s_menu(self) -> None:
        from common.games.collections_service import get_frontend_collections
        self.client.post("/collections", json={"name": "Favorites"})
        self.client.post("/collections", json={"name": "Workbench"})
        self.client.patch("/collections/Workbench", json={"in_frontend": False})

        self.assertEqual(["Favorites", "Workbench"],
                         [row["name"] for row in get_frontend_collections("Workbench")])

    def test_shown_again_leaves_no_key_behind(self) -> None:
        self.client.post("/collections", json={"name": "Workbench"})
        self.client.patch("/collections/Workbench", json={"in_frontend": False})
        self.client.patch("/collections/Workbench", json={"in_frontend": True})

        stored = json.loads(Path(self.manager.path).read_text(encoding="utf-8"))

        self.assertNotIn("in_frontend", stored["collections"][0])

    # --- the order the cabinet shows them in ---------------------------

    def _names(self) -> list[str]:
        return [one["name"] for one in self.client.get("/collections").json()["collections"]]

    def test_the_whole_order_is_written_at_once(self) -> None:
        for name in ("Favorites", "Tournament", "EMs"):
            self.client.post("/collections", json={"name": name})

        answered = self.client.patch("/collections",
                                     json={"order": ["EMs", "Favorites", "Tournament"]})

        self.assertEqual(200, answered.status_code)
        self.assertEqual(["EMs", "Favorites", "Tournament"],
                         [one["name"] for one in answered.json()["collections"]])
        self.assertEqual(["EMs", "Favorites", "Tournament"], self._names())

    def test_an_order_leaving_one_out_is_refused_and_nothing_moves(self) -> None:
        for name in ("Favorites", "Tournament", "EMs"):
            self.client.post("/collections", json={"name": name})

        refused = self.client.patch("/collections", json={"order": ["EMs", "Favorites"]})

        self.assertEqual(400, refused.status_code)
        self.assertEqual(["Tournament"], refused.json()["error"]["details"]["missing"])
        self.assertEqual(["Favorites", "Tournament", "EMs"], self._names())

    def test_an_order_naming_one_twice_is_refused(self) -> None:
        for name in ("Favorites", "EMs"):
            self.client.post("/collections", json={"name": name})

        refused = self.client.patch(
            "/collections", json={"order": ["EMs", "Favorites", "Favorites"]})

        self.assertEqual(400, refused.status_code)
        self.assertEqual(["Favorites", "EMs"], self._names())

    def test_a_collection_called_order_is_still_its_own_address(self) -> None:
        self.client.post("/collections", json={"name": "order"})

        self.assertEqual("order", self.client.get("/collections/order").json()["name"])

    # --- which collections hold a game -----------------------------------

    def test_every_game_s_collections_come_in_one_read(self) -> None:
        self.client.post("/collections", json={"name": "Favorites", "games": [GAME_ID]})

        every = self.client.get("/library/game_collections")
        one = self.client.get(f"/games/{GAME_ID}/collections")

        self.assertEqual(every.status_code, 200)
        # A game's own read also says what keeps it out, which the library read does not.
        held = {key: value for key, value in one.json().items() if key != "taken_out"}
        self.assertEqual({"games": [held]}, every.json())
        self.assertEqual([("Favorites", "added")],
                         [(c["name"], c["how"]) for c in one.json()["collections"]])
        self.assertEqual([], one.json()["taken_out"])

    # --- creating -------------------------------------------------------

    def test_a_manual_collection_is_created_with_its_members(self) -> None:
        response = self.client.post("/collections",
                                    json={"name": "Favorites", "games": [GAME_ID]})

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["type"], "manual")
        self.assertEqual(body["game_count"], 1)
        self.assertIsNone(body["filters"])
        self.assertEqual(response.headers["Location"], "/api/v1/collections/Favorites")
        self.assertEqual(self.manager.get_members("Favorites"), [GAME_ID])

    def test_a_filter_collection_keeps_its_criteria(self) -> None:
        response = self.client.post("/collections", json={
            "name": "Bally 70s", "filters": {"manufacturer": "Bally", "year": "1977"}})

        body = response.json()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(body["type"], "filter")
        # Reported as lists: the many-valued axes have always been stored comma-joined
        # and split again by the matcher, and a bare string typed the schema as
        # single-valued so no generated client could produce a second value.
        self.assertEqual(body["filters"]["manufacturer"], ["Bally"])
        self.assertEqual(body["filters"]["year"], ["1977"])
        self.assertEqual(body["game_count"], 0,
                         "the stored member list, which criteria do not replace")

    def test_every_axis_the_registry_declares_survives_the_wire(self) -> None:
        samples = {"letter": ["C"], "choice": ["Bally"], "rating": "3", "flag": True,
                   "range": {"from": 1990, "to": 1999}, "none_of": ["Stern"]}
        for axis in cf.AXES:
            with self.subTest(axis=axis.name):
                sent = True if axis.name == "rating_or_higher" else samples[axis.kind]
                name = f"Rule {axis.name}"
                body = self.client.post("/collections", json={
                    "name": name, "filters": {axis.name: sent}}).json()

                stored = self.manager.get_filters(name) or {}
                kept = [value for key, value in stored.items()
                        if key == axis.name
                        and str(value).lower() not in ("", "all", "none", "false")]
                self.assertTrue(kept, stored)
                self.assertEqual(body["filters"][axis.name], sent)

    def _written(self, name: str) -> dict:
        written = json.loads((self.root / "collections.json").read_text(encoding="utf-8"))
        return next(one for one in written["collections"] if one["name"] == name)

    def test_a_saved_rule_stores_only_the_conditions_it_sets(self) -> None:
        self.client.post("/collections", json={"name": "Bally",
                                               "filters": {"played": True}})
        as_read_back = {"letter": ["All"], "theme": [], "game_type": ["All"],
                        "manufacturer": ["Bally"], "year": ["All"], "rating": "All",
                        "rating_or_higher": False, "played": False, "favorite": None,
                        "tags": ["All"]}

        self.client.patch("/collections/Bally", json={"filters": as_read_back})

        self.assertEqual({"manufacturer": "Bally", "played": False},
                         self._written("Bally")["filters"])

    def test_a_rating_floor_is_stored_beside_its_rating(self) -> None:
        self.client.post("/collections", json={
            "name": "Top Rated", "filters": {"rating": "4", "rating_or_higher": True}})

        self.assertEqual({"rating": "4", "rating_or_higher": "true"},
                         self._written("Top Rated")["filters"])

    def test_a_year_range_is_stored_with_the_ends_it_sets(self) -> None:
        body = self.client.post("/collections", json={
            "name": "Before 1980", "filters": {"year_range": {"to": 1979}}}).json()

        self.assertEqual({"year_range": {"to": 1979}},
                         self._written("Before 1980")["filters"])
        self.assertEqual({"from": None, "to": 1979}, body["filters"]["year_range"])

    def test_a_year_range_selects_by_the_year_released(self) -> None:
        _meta(self.catalog[GAME_ID])["Info"]["Year"] = "1998"
        _meta(self.catalog[OTHER_ID])["Info"]["Year"] = "1977"
        self.client.post("/collections", json={
            "name": "90s", "filters": {"year_range": {"from": 1990, "to": 1999}}})

        games = self.client.get("/collections/90s/games").json()["games"]

        self.assertEqual([GAME_ID], [one["id"] for one in games])

    def test_none_of_a_manufacturer_leaves_those_games_out(self) -> None:
        _meta(self.catalog[GAME_ID])["Info"]["Manufacturer"] = "Stern"
        _meta(self.catalog[OTHER_ID])["Info"]["Manufacturer"] = "Bally"
        self.client.post("/collections", json={
            "name": "Not Stern", "filters": {"manufacturer_none_of": ["Stern"]}})

        games = self.client.get("/collections/Not%20Stern/games").json()["games"]

        self.assertEqual({"manufacturer_none_of": "Stern"},
                         self._written("Not Stern")["filters"])
        self.assertEqual([OTHER_ID], [one["id"] for one in games])

    def test_a_type_rule_reads_back_as_written_to_the_frontend_menu(self) -> None:
        self.client.post("/collections", json={"name": "EM Only",
                                               "filters": {"game_type": ["EM"]}})

        stored = self.manager.get_filters("EM Only")
        self.assertEqual("EM", cf.criterion(stored, "game_type"))
        self.assertEqual("EM", game_state._filter_state(stored)["type"])

    def test_a_type_rule_the_frontend_saved_reads_back_through_the_api(self) -> None:
        self.manager.add_filter_collection("Saved Elsewhere", game_type="EM")

        body = self.client.get("/collections/Saved%20Elsewhere").json()

        self.assertEqual(["EM"], body["filters"]["game_type"])

    def test_the_reported_sort_is_the_one_the_collection_resolves_by(self) -> None:
        """Read off the `order` block, not the criteria: those carry a default for every
        key a collection never set, so a Last Played reported `Alpha`."""
        self.manager.add_collection("Recent")
        self.manager.make_filter_collection(
            "Recent", {"played": True},
            order={"by": "last_played", "direction": "desc"})
        self.manager.save()

        body = self.client.get("/collections/Recent").json()

        self.assertEqual(("last_played", "desc"), (body["order_by"], body["direction"]))

    def test_the_order_is_reported_beside_the_criteria_and_not_in_them(self) -> None:
        self.client.post("/collections", json={
            "name": "Recent", "filters": {"played": True, "order_by": "last_played",
                                          "direction": "desc"}})

        body = self.client.get("/collections/Recent").json()

        self.assertEqual(("last_played", "desc"), (body["order_by"], body["direction"]))
        self.assertEqual(set(), {"order_by", "direction"} & set(body["filters"]))

    def test_a_rule_save_leaves_the_order_the_paging_and_the_limit(self) -> None:
        self.client.post("/collections", json={"name": "Recent",
                                               "filters": {"played": True}})
        self.client.patch("/collections/Recent", json={
            "order_by": "last_played", "direction": "desc", "paging_group": "count",
            "limit": 20})

        self.client.patch("/collections/Recent",
                          json={"filters": {"played": True, "favorite": True}})

        body = self.client.get("/collections/Recent").json()
        self.assertEqual(("last_played", "desc", "count", 20),
                         (body["order_by"], body["direction"], body["paging_group"],
                          body["limit"]))

    def test_a_field_sent_alone_in_the_filters_keeps_the_direction(self) -> None:
        self.client.post("/collections", json={"name": "Recent",
                                               "filters": {"played": True}})
        self.client.patch("/collections/Recent",
                          json={"order_by": "last_played", "direction": "desc"})

        self.client.patch("/collections/Recent", json={
            "filters": {"played": True, "order_by": "play_count"}})

        body = self.client.get("/collections/Recent").json()
        self.assertEqual(("play_count", "desc"), (body["order_by"], body["direction"]))

    def test_a_rule_save_keeps_an_order_only_the_old_criteria_held(self) -> None:
        self.manager.add_filter_collection("Saved Elsewhere", manufacturer="Bally",
                                           sort_by="LastRun", order_by="Descending")
        self.manager.save()

        self.client.patch("/collections/Saved%20Elsewhere",
                          json={"filters": {"manufacturer": ["Williams"]}})

        body = self.client.get("/collections/Saved%20Elsewhere").json()
        self.assertEqual(("last_played", "desc"), (body["order_by"], body["direction"]))

    def test_a_2x_sort_name_is_accepted_and_reported_in_the_stored_vocabulary(self) -> None:
        """The 2.x sort *values* are what carries over. There was never a 2.x client for
        this API - the names it accepts are 3.0's - but a collection on disk holds those
        spellings, so a caller reading one back may well send them."""
        response = self.client.post("/collections", json={
            "name": "Most Played", "filters": {"order_by": "Highest StartCount"}})

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["order_by"], "play_count")

    def test_criteria_and_named_games_are_stored_together(self) -> None:
        """Not two kinds. They are combinable and the resolver
        applies members over what the criteria matched, so a collection may follow a
        rule and still hold something somebody put there by hand."""
        response = self.client.post("/collections", json={
            "name": "Both", "games": [GAME_ID], "filters": {"year": "1977"}})

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["type"], "filter")
        self.assertEqual(self.manager.get_members("Both"), [GAME_ID])
        filters = self.manager.get_filters("Both")
        assert filters is not None
        self.assertEqual(filters["year"], "1977")

    def test_a_collection_can_let_go_of_its_rules_over_the_wire(self) -> None:
        self.client.post("/collections", json={
            "name": "Both", "games": [GAME_ID], "filters": {"manufacturer": ["Bally"]}})

        body = self.client.patch("/collections/Both",
                                 json={"clear_filters": True}).json()

        self.assertEqual(("manual", None), (body["type"], body["filters"]))
        self.assertEqual(self.manager.get_members("Both"), [GAME_ID])

    def test_a_copy_holds_everything_the_original_does(self) -> None:
        self.client.post("/collections", json={
            "name": "Bally", "games": [GAME_ID], "description": "Tonight",
            "filters": {"manufacturer": ["Bally"]}})
        self.client.patch("/collections/Bally", json={
            "limit": 1, "order_by": "year", "direction": "desc"})
        self.client.put(f"/collections/Bally/excluded/{OTHER_ID}", json={})

        made = self.client.post("/collections",
                                json={"name": "Bally copy", "copy_of": "Bally"})

        self.assertEqual(201, made.status_code)
        original = self._written("Bally")
        self.assertEqual({**original, "name": "Bally copy"}, self._written("Bally copy"))

    def test_a_copy_of_nothing_is_not_found(self) -> None:
        made = self.client.post("/collections", json={"name": "Copy", "copy_of": "Gone"})

        self.assertEqual(404, made.status_code)

    def test_a_copy_with_contents_of_its_own_is_refused(self) -> None:
        self.client.post("/collections", json={"name": "Bally", "games": [GAME_ID]})

        made = self.client.post("/collections", json={
            "name": "Copy", "copy_of": "Bally", "games": [OTHER_ID]})

        self.assertEqual(400, made.status_code)
        self.assertNotIn("Copy", self.manager.get_collections_name())

    def test_an_unknown_game_id_is_named_rather_than_stored(self) -> None:
        response = self.client.post("/collections",
                                    json={"name": "Bad", "games": [GAME_ID, "nope"]})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["details"]["ids"], ["nope"])
        self.assertNotIn("Bad", self.manager.get_collections_name())

    def test_a_duplicate_name_conflicts(self) -> None:
        self.client.post("/collections", json={"name": "Favorites"})

        response = self.client.post("/collections", json={"name": "Favorites"})

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "conflict")

    def test_a_blank_name_is_refused(self) -> None:
        self.assertEqual(self.client.post("/collections", json={"name": "  "}).status_code,
                         400)

    def test_a_picture_that_is_not_an_image_is_refused_in_words(self) -> None:
        self.client.post("/collections", json={"name": "Favorites"})

        response = self.client.put("/collections/Favorites/image",
                                   files={"file": ("notes.txt", b"words", "text/plain")})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["message"],
                         t("error.collections.not_image"))

    # --- membership -----------------------------------------------------

    def test_adding_and_removing_a_member(self) -> None:
        self.client.post("/collections", json={"name": "Favorites"})

        added = self.client.put(f"/collections/Favorites/games/{GAME_ID}")
        self.assertEqual(added.status_code, 204)
        self.assertEqual(self.manager.get_members("Favorites"), [GAME_ID])

        removed = self.client.delete(f"/collections/Favorites/games/{GAME_ID}")
        self.assertEqual(removed.status_code, 204)
        self.assertEqual(self.manager.get_members("Favorites"), [])

    def test_adding_a_member_twice_is_a_success(self) -> None:
        """The caller's intent is that it be in there; it is."""
        self.client.post("/collections", json={"name": "Favorites"})
        self.client.put(f"/collections/Favorites/games/{GAME_ID}")

        again = self.client.put(f"/collections/Favorites/games/{GAME_ID}")

        self.assertEqual(again.status_code, 204)
        self.assertEqual(self.manager.get_members("Favorites"), [GAME_ID])

    def test_a_collection_that_filters_can_still_be_added_to(self) -> None:
        """A named member states what the collection holds for that game whether or not
        the criteria matched it - which is what makes "add this one too" expressible."""
        self.client.post("/collections",
                         json={"name": "Smart", "filters": {"manufacturer": "Bally"}})

        response = self.client.put(f"/collections/Smart/games/{GAME_ID}")

        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.manager.get_members("Smart"), [GAME_ID])

    def test_membership_of_an_unknown_game_or_collection_is_not_found(self) -> None:
        self.client.post("/collections", json={"name": "Favorites"})

        self.assertEqual(
            self.client.put("/collections/Favorites/games/nope").status_code, 404)
        self.assertEqual(
            self.client.put(f"/collections/Nope/games/{GAME_ID}").status_code, 404)

    def test_removing_a_game_that_is_not_a_member_is_not_found(self) -> None:
        self.client.post("/collections", json={"name": "Favorites"})

        response = self.client.delete(f"/collections/Favorites/games/{GAME_ID}")

        self.assertEqual(response.status_code, 404)

    # --- reading and deleting -------------------------------------------

    def test_listing_and_fetching(self) -> None:
        self.client.post("/collections", json={"name": "Favorites"})

        listed = self.client.get("/collections").json()["collections"]
        one = self.client.get("/collections/Favorites").json()

        self.assertEqual([row["name"] for row in listed], ["Favorites"])
        self.assertEqual(one["links"]["games"], "/api/v1/collections/Favorites/games")

    def test_resolved_membership_answers_for_both_kinds(self) -> None:
        self.client.post("/collections", json={"name": "Manual", "games": [GAME_ID]})
        self.client.post("/collections",
                         json={"name": "Smart", "filters": {"manufacturer": "Bally"}})

        manual = self.client.get("/collections/Manual/games").json()
        smart = self.client.get("/collections/Smart/games").json()

        self.assertEqual([row["id"] for row in manual["games"]], [GAME_ID])
        self.assertEqual(manual["total"], 1)
        self.assertEqual({row["id"] for row in smart["games"]}, {GAME_ID, OTHER_ID},
                         "the filter resolves against the library, not a stored list")

    def test_deleting_a_collection(self) -> None:
        self.client.post("/collections", json={"name": "Favorites"})

        response = self.client.delete("/collections/Favorites")

        self.assertEqual(response.status_code, 204)
        self.assertNotIn("Favorites", self.manager.get_collections_name())

    def test_an_unknown_collection_is_a_not_found_envelope(self) -> None:
        response = self.client.get("/collections/Nope")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "not_found")

    def test_a_name_with_a_space_survives_the_round_trip(self) -> None:
        self.client.post("/collections", json={"name": "Last Played"})

        body = self.client.get("/collections/Last%20Played").json()

        self.assertEqual(body["name"], "Last Played")
        self.assertEqual(body["links"]["self"], "/api/v1/collections/Last%20Played")

    def test_discovery_advertises_collections(self) -> None:
        self.assertEqual(self.client.get("/").json()["links"]["collections"],
                         "/api/v1/collections")


class CollectionEntriesTests(CollectionsApiTests):
    """The play lens. Same resolution the theme payload uses, serialized for REST."""

    def setUp(self) -> None:
        super().setUp()
        # The base fixture's games have no tables, which is a real state - a folder
        # nothing has parsed - but it produces no entries by design. Give them one
        # each so there is something for the play lens to answer with.
        for index, game in enumerate(self.catalog.values()):
            _meta(game)["tables"] = {
                f"tbl{index}": {"id": f"tbl{index}", "filename": f"game{index}.vpx",
                                "version": "1.0", "rom": ""}}

    def test_entries_answer_one_per_game_by_default(self) -> None:
        self.client.post("/collections", json={"name": "Manual", "games": [GAME_ID]})

        body = self.client.get("/collections/Manual/entries").json()

        self.assertEqual(body["count"], len(body["entries"]))
        for entry in body["entries"]:
            self.assertTrue(entry["table"]["id"], "every entry names a table")
            self.assertIn("launch", entry["links"])

    def test_a_collection_that_does_not_exist_is_a_404(self) -> None:
        self.assertEqual(self.client.get("/collections/Nope/entries").status_code, 404)

    def test_a_table_the_rule_matched_is_listed_as_that_table(self) -> None:
        meta = {"Info": {"Title": "Tagged"}, "vpinfe": {"game_id": "tagged"},
                "tables": {"t1": {"id": "t1", "filename": "Tagged.vpx"},
                           "vr": {"id": "vr", "filename": "Tagged VR.vpx",
                                  "user": {"tags": ["VR"]}}}}
        folder = write_game(self.root, "Tagged", info=meta, files={"Tagged VR.vpx": b"x"})
        self.catalog["tagged"] = fake_game(folder, "Tagged", meta=meta)
        self.client.post("/collections", json={"name": "VR", "filters": {"tags": ["VR"]}})

        members = self.client.get("/collections/VR/members").json()["members"]

        self.assertEqual([("tagged", "filter", "vr", "matched")],
                         [(one["game"], one["origin"], one["ref_table"],
                           one["tables"][0]["origin"]) for one in members])

    def _on_disk(self, title: str) -> str:
        game_id = title.lower()
        meta = {"Info": {"Title": title}, "vpinfe": {"game_id": game_id},
                "tables": {f"{game_id}-t": {"id": f"{game_id}-t",
                                            "filename": f"{title}.vpx"}}}
        self.catalog[game_id] = fake_game(write_game(self.root, title, info=meta), title,
                                          meta=meta)
        return game_id

    def _listed(self, name: str) -> tuple[list[str], list[str]]:
        plays = [one["game"]["id"] for one in
                 self.client.get(f"/collections/{name}/entries").json()["entries"]]
        rows = [one["game"] for one in
                self.client.get(f"/collections/{name}/members").json()["members"]]
        return plays, rows

    def test_the_list_is_in_the_order_it_plays_in(self) -> None:
        zeta, alpha = self._on_disk("Zeta"), self._on_disk("Alpha")
        self.client.post("/collections", json={"name": "Picked", "games": [zeta, alpha]})

        plays, rows = self._listed("Picked")

        self.assertEqual([alpha, zeta], plays)
        self.assertEqual(plays, rows)

    def test_a_game_added_to_a_rule_sits_where_it_plays(self) -> None:
        zeta, alpha = self._on_disk("Zeta"), self._on_disk("Alpha")
        self.client.post("/collections", json={
            "name": "Letters", "filters": {"letter": ["A"]}, "games": [zeta]})

        plays, rows = self._listed("Letters")

        self.assertEqual([alpha, zeta], plays)
        self.assertEqual(plays, rows)

    def test_an_arranged_list_keeps_its_stored_order(self) -> None:
        zeta, alpha = self._on_disk("Zeta"), self._on_disk("Alpha")
        self.client.post("/collections", json={"name": "Arranged", "games": [zeta, alpha]})
        self.client.put("/collections/Arranged/order", json={"games": [zeta, alpha]})

        plays, rows = self._listed("Arranged")

        self.assertEqual([zeta, alpha], rows)
        self.assertEqual(plays, rows)

    def _arranged(self) -> tuple[str, str]:
        zeta, alpha = self._on_disk("Zeta"), self._on_disk("Alpha")
        self.client.post("/collections", json={"name": "Arranged", "games": [zeta, alpha]})
        self.client.put("/collections/Arranged/order", json={"games": [zeta, alpha]})
        return zeta, alpha

    def test_a_rule_saved_on_a_custom_order_list_sorts_it_by_title(self) -> None:
        zeta, alpha = self._arranged()
        self.client.patch("/collections/Arranged", json={"paging_group": "count"})

        self.client.patch("/collections/Arranged", json={"filters": {"letter": ["A"]}})

        body = self.client.get("/collections/Arranged").json()
        self.assertEqual(("title", "asc", "count"),
                         (body["order_by"], body["direction"], body["paging_group"]))
        self.assertEqual(([alpha, zeta], [alpha, zeta]), self._listed("Arranged"))

    def test_a_rule_saved_on_a_custom_order_list_keeps_the_direction_it_names(self) -> None:
        self._arranged()

        self.client.patch("/collections/Arranged",
                          json={"filters": {"letter": ["A"], "direction": "desc"}})

        body = self.client.get("/collections/Arranged").json()
        self.assertEqual(("title", "desc"), (body["order_by"], body["direction"]))

    def test_the_arrangement_is_back_once_the_rules_go_and_custom_order_is_chosen(
            self) -> None:
        zeta, alpha = self._arranged()
        self.client.patch("/collections/Arranged", json={"filters": {"letter": ["A"]}})

        self.client.patch("/collections/Arranged", json={"clear_filters": True})
        self.client.patch("/collections/Arranged", json={"order_by": "manual"})

        self.assertEqual(([zeta, alpha], [zeta, alpha]), self._listed("Arranged"))

    def test_a_rule_previewed_on_a_custom_order_list_lists_it_by_title(self) -> None:
        zeta, alpha = self._arranged()

        body = self.client.post("/collections/Arranged/members/preview",
                                json={"filters": {"letter": ["A"]}}).json()

        self.assertEqual([alpha, zeta], [one["game"] for one in body["members"]])

    def test_rules_sent_with_custom_order_are_refused_and_nothing_is_written(self) -> None:
        self._arranged()
        both = {"letter": ["A"], "order_by": "manual"}

        made = self.client.post("/collections", json={"name": "Both", "filters": both})
        given = self.client.patch("/collections/Arranged", json={"filters": both})

        self.assertEqual((409, 409), (made.status_code, given.status_code))
        self.assertEqual(404, self.client.get("/collections/Both").status_code)
        body = self.client.get("/collections/Arranged").json()
        self.assertEqual(("manual", "manual"), (body["type"], body["order_by"]))

    def _rows(self, name: str) -> list[tuple[str, bool]]:
        return [(one["game"], one["past_limit"]) for one in
                self.client.get(f"/collections/{name}/members").json()["members"]]

    def test_what_a_rule_finds_past_the_limit_is_listed_and_marked(self) -> None:
        apple, alpha, avocado = (self._on_disk(title)
                                 for title in ("Apple", "Alpha", "Avocado"))
        self.client.post("/collections", json={"name": "A", "filters": {"letter": ["A"]}})
        self.client.patch("/collections/A", json={"limit": 1})

        body = self.client.get("/collections/A/members").json()

        self.assertEqual([(alpha, False), (apple, True), (avocado, True)], self._rows("A"))
        self.assertEqual(1, body["playable"])

    def test_a_hand_picked_row_past_the_limit_sits_where_it_would_play(self) -> None:
        zeta, alpha = self._on_disk("Zeta"), self._on_disk("Alpha")
        self.client.post("/collections", json={"name": "Picked", "games": [zeta, alpha]})
        self.client.patch("/collections/Picked", json={"limit": 1})

        self.assertEqual([(alpha, False), (zeta, True)], self._rows("Picked"))

    def test_no_limit_marks_nothing(self) -> None:
        zeta = self._on_disk("Zeta")
        self.client.post("/collections", json={"name": "Picked", "games": [zeta]})

        self.assertEqual([(zeta, False)], self._rows("Picked"))

    def test_a_preview_tries_other_rules_and_writes_nothing(self) -> None:
        zeta, alpha, apple = (self._on_disk(title) for title in ("Zeta", "Alpha", "Apple"))
        self.client.post("/collections", json={"name": "Picked", "games": [zeta]})

        body = self.client.post("/collections/Picked/members/preview",
                                json={"filters": {"letter": ["A"]}}).json()

        self.assertEqual([(alpha, "filter"), (apple, "filter"), (zeta, "named")],
                         [(one["game"], one["origin"]) for one in body["members"]])
        self.assertEqual(2, body["matched"])
        self.assertIsNone(self.client.get("/collections/Picked").json()["filters"])

    def test_a_preview_with_no_rules_keeps_what_was_added(self) -> None:
        zeta = self._on_disk("Zeta")
        self._on_disk("Alpha")
        self.client.post("/collections", json={
            "name": "Smart", "games": [zeta], "filters": {"letter": ["A"]}})

        body = self.client.post("/collections/Smart/members/preview", json={}).json()

        self.assertEqual([zeta], [one["game"] for one in body["members"]])
        self.assertEqual(0, body["matched"])
        self.assertEqual(["A"],
                         self.client.get("/collections/Smart").json()["filters"]["letter"])

    def test_a_row_that_follows_a_game_names_the_table_that_plays(self) -> None:
        meta = {"Info": {"Title": "Twin"},
                "vpinfe": {"game_id": "twin", "default_table": "later"},
                "tables": {"earlier": {"id": "earlier", "filename": "Twin 2.1.vpx"},
                           "later": {"id": "later", "filename": "Twin 2.3.1.vpx"}}}
        folder = write_game(self.root, "Twin", info=meta, vpx=False,
                            files={"Twin 2.1.vpx": b"x", "Twin 2.3.1.vpx": b"x"})
        self.catalog["twin"] = fake_game(folder, "Twin", meta=meta)
        self.client.post("/collections", json={"name": "Pair", "games": ["twin"]})

        plays = self.client.get("/collections/Pair/entries").json()["entries"]
        rows = self.client.get("/collections/Pair/members").json()["members"]

        self.assertEqual(["later"], [one["table"]["id"] for one in plays])
        self.assertEqual(["later"], [one["tables"][0]["id"] for one in rows])

    def test_a_filter_this_build_cannot_read_is_refused_by_name(self) -> None:
        """Answering with what is left would be a different question, silently."""
        self.client.post("/collections",
                         json={"name": "VR", "filters": {"manufacturer": "Bally"}})
        self.manager.set_filter("VR", "app", "VPX VR")
        self.manager.save()

        response = self.client.get("/collections/VR/entries")

        self.assertEqual(response.status_code, 409)
        self.assertIn("app", response.json()["error"]["details"]["unknown_filters"])

    def test_the_two_lenses_agree_on_order(self) -> None:
        """The defect this replaced: REST answered in one order, the frontend another."""
        self.client.post("/collections",
                         json={"name": "Smart", "filters": {"manufacturer": "Bally"}})

        games = [row["id"] for row in
                 self.client.get("/collections/Smart/games").json()["games"]]
        entries = [e["game"]["id"] for e in
                   self.client.get("/collections/Smart/entries").json()["entries"]]

        self.assertEqual(games, entries)

    def test_the_default_table_is_computed_not_read_off_the_entry(self) -> None:
        """`default` is the game's own choice, stored in its vpinfe section. Reading it
        off the table entry - where nothing writes it - answers false for everything."""
        game = next(iter(self.catalog.values()))
        _meta(game)["tables"] = {
            "aa": {"id": "aa", "filename": "z-later.vpx"},
            "bb": {"id": "bb", "filename": "a-earlier.vpx"},
        }
        self.client.post("/collections", json={"name": "Manual",
                                               "games": [list(self.catalog)[0]]})

        # The entry the collection resolves to is the default, and `default` on it is
        # the game's own choice rather than a flag read off the table row.
        entries = self.client.get("/collections/Manual/entries").json()["entries"]

        self.assertEqual([e["table"]["id"] for e in entries], ["bb"])
        self.assertTrue(entries[0]["table"]["default"], "the resolved default")


TABLED_ID = "Tabled000001"
FOLDER = "Cactus Canyon (Bally 1998)"
DESKTOP = f"{FOLDER}.vpx"
VR = f"{FOLDER} - VR.vpx"
TABLED_INFO: dict[str, Any] = {
    "Info": {"Name": "Cactus Canyon", "Manufacturer": "Bally"},
    "VPinFE": {"game_id": TABLED_ID},
    "tables": {
        "tbl0000001": {"id": "tbl0000001", "filename": DESKTOP, "version": "1.0"},
        "tbl0000002": {"id": "tbl0000002", "filename": VR, "version": "2.1"},
    },
}


class MemberTableTests(TempTree):
    """Which table a member names, changed in place.

    Its own fixture because the table routes enumerate the folder: a game with no
    directory behind it has no tables, so every id is refused before the collection
    is reached.
    """

    def setUp(self) -> None:
        super().setUp()
        self.folder = write_game(self.root, FOLDER, info=TABLED_INFO, vpx=False,
                                 files={DESKTOP: b"vpx", VR: b"vpx"})
        game = fake_game(self.folder, FOLDER, meta=TABLED_INFO)
        self.catalog = {TABLED_ID: game}

        self.path = f"{self.root}/collections.ini"
        open(self.path, "w", encoding="utf-8").close()
        manager = CollectionStore(self.path)
        self.manager = manager
        for target in ("common.games.collection_ops.get_collections_manager",
                       "common.games.collections_service.get_collections_manager"):
            patcher = patch(target, lambda: manager)
            patcher.start()
            self.addCleanup(patcher.stop)
        catalog_patch = patch("common.games.game_repository.catalog", lambda: self.catalog)
        catalog_patch.start()
        self.addCleanup(catalog_patch.stop)

        self.client = TestClient(httpapi.create_api_app(),
                                 raise_server_exceptions=False)
        self.client.post("/collections", json={"name": "Favorites"})

    def _refs(self) -> list[dict]:
        return self.manager.get_member_refs("Favorites")

    def test_a_member_naming_what_is_gone_is_counted_missing(self) -> None:
        self.client.put(f"/collections/Favorites/games/{TABLED_ID}")
        self.manager.add_member("Favorites", "Gone00000001")
        self.manager.add_member("Favorites", TABLED_ID, "tbl0000009")

        held = self.client.get("/collections/Favorites").json()
        members = self.client.get("/collections/Favorites/members").json()["members"]

        reported = [one for one in members if one["origin"] == "missing"
                    or any(table["origin"] == "missing" for table in one["tables"])]
        self.assertEqual(2, held["missing"])
        self.assertEqual(held["missing"], len(reported))

    def test_a_member_held_to_a_hidden_table_is_counted_hidden(self) -> None:
        self.manager.add_member("Favorites", TABLED_ID, "tbl0000002")
        tables = TABLED_INFO["tables"]
        write_game(self.root, FOLDER, vpx=False, info={
            **TABLED_INFO, "tables": {**tables, "tbl0000002": {**tables["tbl0000002"],
                                                               "hidden": True}}})

        held = self.client.get("/collections/Favorites").json()
        members = self.client.get("/collections/Favorites/members").json()["members"]

        self.assertEqual((1, 0), (held["hidden"], held["missing"]))
        self.assertEqual(
            [("named", "tbl0000002", "hidden")],
            [(one["origin"], one["ref_table"], one["tables"][0]["origin"])
             for one in members])

    def test_a_member_keeps_its_place_when_its_table_changes(self) -> None:
        """The reason this is a route and not remove-then-add: curated order is what a
        manual collection is for, and rebuilding the ref sends that row to the end."""
        self.client.put(f"/collections/Favorites/games/{TABLED_ID}")

        response = self.client.put(f"/collections/Favorites/games/{TABLED_ID}/table",
                                   json={"table": "tbl0000002"})

        self.assertEqual(response.status_code, 204)
        self.assertEqual(self._refs(),
                         [{"game": TABLED_ID, "table": "tbl0000002"}])

    def test_a_member_can_be_handed_back_its_game_default(self) -> None:
        """The return trip, which is what makes the control reversible."""
        self.client.put(f"/collections/Favorites/games/{TABLED_ID}",
                        json={"table": "tbl0000001"})

        response = self.client.put(f"/collections/Favorites/games/{TABLED_ID}/table",
                                   json={"table": "", "was": "tbl0000001"})

        self.assertEqual(response.status_code, 204)
        self.assertEqual(self._refs(), [{"game": TABLED_ID}],
                         "the key is dropped rather than written empty")

    def test_naming_a_table_of_some_other_game_is_refused(self) -> None:
        """A ref pointing at a table this game has not got resolves to nothing and
        reads as missing for good, so it is refused rather than stored."""
        self.client.put(f"/collections/Favorites/games/{TABLED_ID}")

        response = self.client.put(f"/collections/Favorites/games/{TABLED_ID}/table",
                                   json={"table": "somebody-elses"})

        self.assertEqual(response.status_code, 404)
        self.assertEqual(self._refs(), [{"game": TABLED_ID}])

    def test_a_game_named_twice_can_be_reordered(self) -> None:
        """An order is over the rows, and one game can hold several of them.

        `get_members` de-duplicates by design, so an order compared against it counted
        one row where the collection has two and refused every move in a collection
        holding two tables of a game.
        """
        for table in ("tbl0000001", "tbl0000002"):
            self.client.put(f"/collections/Favorites/games/{TABLED_ID}",
                            json={"table": table})

        response = self.client.put("/collections/Favorites/order",
                                   json={"games": [TABLED_ID, TABLED_ID]})

        self.assertEqual(response.status_code, 204)
        self.assertEqual(self._refs(),
                         [{"game": TABLED_ID, "table": "tbl0000001"},
                          {"game": TABLED_ID, "table": "tbl0000002"}],
                         "both refs survive, in their own order")

    def test_an_order_missing_one_of_a_repeated_game_is_refused(self) -> None:
        """Naming it once would drop the other ref, which is the silent removal the
        route exists to refuse."""
        for table in ("tbl0000001", "tbl0000002"):
            self.client.put(f"/collections/Favorites/games/{TABLED_ID}",
                            json={"table": table})

        response = self.client.put("/collections/Favorites/order",
                                   json={"games": [TABLED_ID]})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(len(self._refs()), 2, "and nothing was written")

    def test_naming_a_table_another_row_already_uses_is_refused(self) -> None:
        """A conflict, not a merge. The collection holds the pairing once (2.10), and
        merging would drop a row without anything on the wire saying which."""
        for table in ("tbl0000001", "tbl0000002"):
            self.client.put(f"/collections/Favorites/games/{TABLED_ID}",
                            json={"table": table})

        response = self.client.put(
            f"/collections/Favorites/games/{TABLED_ID}/table",
            json={"table": "tbl0000002", "was": "tbl0000001"})

        self.assertEqual(response.status_code, 409)
        self.assertEqual(len(self._refs()), 2, "and both rows are still there")

    def test_changing_a_member_that_is_not_there_is_not_found(self) -> None:
        response = self.client.put(f"/collections/Favorites/games/{TABLED_ID}/table",
                                   json={"table": "tbl0000001"})

        self.assertEqual(response.status_code, 404)
        self.assertEqual(self._refs(), [])


if __name__ == "__main__":
    unittest.main()
