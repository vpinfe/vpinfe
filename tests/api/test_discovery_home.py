"""Discovery's front page: what VPS added lately, the games lacking a kind, whose work the
library holds - computed from one comparison and served in a single read."""

from __future__ import annotations

import tempfile
import time
import unittest
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import patch

from starlette.testclient import TestClient

import httpapi
from common.games import library_updates, media_fill
from common.games import library_vps_state as rollup
from common.online import vps_home, vps_kinds, vps_marks, vps_releases, vps_updates

TODAY = date(2026, 10, 18)
KEPT = ("tableFiles", "b2sFiles", "romFiles", "ruleFiles")
KEPT_WHEELS = (*KEPT, "wheelArtFiles")
PEOPLE = [{"name": "Alpha", "aliases": []}, {"name": "Beta", "aliases": []}]


def release(rid: str, added: str, *, by: tuple[int, ...] = (0,), held: bool = False,
            mod: bool = False, version: str = "") -> dict:
    return {"id": rid, "by": list(by), "added": added, "held": held, "mod": mod,
            "version": version}


FOUND: dict[str, Any] = {
    "counted": "2026-10-17T00:00:00Z",
    "people": PEOPLE,
    "rows": [
        {"vps_id": "g1", "name": "Alien", "in_library": True, "img_url": "a.png",
         "listed": ["tableFiles", "b2sFiles", "romFiles"], "held": ["tableFiles"],
         "releases": {
             "tableFiles": [release("t1", "2026-10-17T10:00:00Z", held=True, version="1.2"),
                            release("t0", "2026-08-01T10:00:00Z")],
             "b2sFiles": [release("b1", "2026-10-12T10:00:00Z", by=(1,)),
                          release("b2", "2026-10-02T10:00:00Z", by=(1,))],
             "romFiles": [release("r1", "2026-10-16T10:00:00Z")],
             "ruleFiles": [release("x1", "2026-10-17T10:00:00Z")]}},
        {"vps_id": "g2", "name": "Batman", "in_library": False,
         "listed": ["tableFiles", "b2sFiles"], "held": [],
         "releases": {"tableFiles": [release("t2", "2026-10-17T11:00:00Z", by=(0, 1),
                                             mod=True)]}},
        {"vps_id": "g3", "name": "", "in_library": False, "listed": ["tableFiles"],
         "held": [], "releases": {"tableFiles": [release("t3", "2026-10-17T11:00:00Z")]}},
    ],
}


WHEELS: dict[str, Any] = {**FOUND, "rows": [{
    **FOUND["rows"][0], "listed": ["wheelArtFiles"],
    "releases": {"wheelArtFiles": [release("w1", "2026-10-12T10:00:00Z")]}}]}


def _ids(shelf: list[dict[str, Any]]) -> list[str]:
    return [one["id"] for one in shelf]


class WindowTests(unittest.TestCase):
    def test_since_is_the_day_a_window_starts(self) -> None:
        self.assertEqual(vps_home.since(7, TODAY), "2026-10-11")

    def test_a_release_added_on_the_first_day_of_the_window_is_in_it(self) -> None:
        found = {"people": PEOPLE, "rows": [{
            "vps_id": "g1", "name": "Alien", "releases": {"tableFiles": [
                release("edge", "2026-10-11T00:00:00Z"),
                release("out", "2026-10-10T23:59:59Z")]}}]}

        self.assertEqual(_ids(vps_home.arrivals_in(found, 7, KEPT, TODAY)), ["edge"])


class ArrivalsTests(unittest.TestCase):
    def test_it_lists_what_was_added_in_the_window_newest_first(self) -> None:
        self.assertEqual(_ids(vps_home.arrivals_in(FOUND, 7, KEPT, TODAY)),
                         ["t2", "t1", "r1", "b1"])

    def test_rule_sheets_and_unnamed_entries_are_left_out(self) -> None:
        ids = _ids(vps_home.arrivals_in(FOUND, 30, KEPT, TODAY))

        self.assertNotIn("x1", ids)
        self.assertNotIn("t3", ids)

    def test_a_kind_the_library_does_not_collect_is_left_out(self) -> None:
        self.assertEqual(set(_ids(vps_home.arrivals_in(FOUND, 30, ("tableFiles",), TODAY))),
                         {"t2", "t1"})

    def test_a_release_carries_its_authors_by_name(self) -> None:
        shelf = {one["id"]: one for one in vps_home.arrivals_in(FOUND, 7, KEPT, TODAY)}

        self.assertEqual(shelf["t2"]["authors"], ["Alpha", "Beta"])
        self.assertTrue(shelf["t2"]["mod"])
        self.assertTrue(shelf["t1"]["held"])

    def test_a_re_dated_release_says_what_it_was(self) -> None:
        with patch.object(vps_home.arrivals, "was",
                          side_effect=lambda _source, item: {"t1": "1.0", "b1": ""}.get(item, "")):
            shelf = {one["id"]: one for one in vps_home.arrivals_in(FOUND, 7, KEPT, TODAY)}

        self.assertEqual((shelf["t1"]["version"], shelf["t1"]["was"]), ("1.2", "1.0"))
        self.assertEqual(shelf["b1"]["was"], "")

    def test_a_release_that_never_changed_was_nothing(self) -> None:
        with patch.object(vps_home.arrivals, "was", return_value=""):
            shelf = vps_home.arrivals_in(FOUND, 7, KEPT, TODAY)

        self.assertEqual({one["was"] for one in shelf}, {""})

    def test_a_previous_version_equal_to_the_current_one_says_nothing(self) -> None:
        with patch.object(vps_home.arrivals, "was", return_value="1.2"):
            shelf = {one["id"]: one for one in vps_home.arrivals_in(FOUND, 7, KEPT, TODAY)}

        self.assertEqual(shelf["t1"]["was"], "")


class AuthorsTests(unittest.TestCase):
    def test_the_most_held_come_first_with_what_is_new(self) -> None:
        shelf = vps_home.arrivals_in(FOUND, 7, KEPT, TODAY)

        self.assertEqual(vps_home.authors_collected(FOUND, shelf),
                         [{"name": "Alpha", "held": 1, "new": 3}])

    def test_nobody_is_collected_before_the_library_is_compared(self) -> None:
        """Before a comparison nothing is held and no entry is in the library."""
        uncounted = {"counted": "", "people": PEOPLE, "rows": [
            {**FOUND["rows"][0], "in_library": None, "held": [],
             "releases": {"tableFiles": [release("t1", "2026-10-17T10:00:00Z")]}}]}

        shelf = vps_home.arrivals_in(uncounted, 7, KEPT, TODAY)

        self.assertEqual(_ids(shelf), ["t1"])
        self.assertEqual(vps_home.authors_collected(uncounted, shelf), [])
        self.assertEqual(vps_home.home(uncounted, KEPT, 7, TODAY)["authors"], [])


class LackingTests(unittest.TestCase):
    def test_a_game_that_lists_a_kind_and_holds_none_of_it_is_lacking(self) -> None:
        self.assertEqual(vps_home.lacking(FOUND, "romFiles"), [
            {"vps_id": "g1", "game_id": "", "name": "Alien", "img_url": "a.png", "vps": 1,
             "source": 0}])

    def test_a_kind_the_library_holds_is_not_lacking(self) -> None:
        self.assertEqual(vps_home.lacking(FOUND, "tableFiles"), [])

    def test_a_game_outside_the_library_is_left_out_though_its_entry_lists_the_kind(self) -> None:
        self.assertIn("b2sFiles", FOUND["rows"][1]["listed"])

        self.assertEqual([one["name"] for one in vps_home.lacking(FOUND, "b2sFiles")], ["Alien"])

    def test_a_game_with_no_name_is_not_listed(self) -> None:
        nameless = {"rows": [{"vps_id": "g9", "name": " ", "in_library": True,
                              "listed": ["romFiles"], "held": [], "releases": {}}]}

        self.assertEqual(vps_home.lacking(nameless, "romFiles"), [])


def offered(game_id: str, vps_id: str, name: str = "", available: int = 1) -> dict:
    return {"game_id": game_id, "vps_id": vps_id, "name": name or game_id, "available": available}


class LackingMediaTests(unittest.TestCase):
    def test_a_game_a_source_has_a_file_for_is_listed_with_how_many(self) -> None:
        self.assertEqual(vps_home.lacking_media(FOUND, [offered("i1", "g1", "Alien", 2)]), [
            {"vps_id": "g1", "game_id": "i1", "name": "Alien", "img_url": "a.png",
             "vps": 0, "source": 2, "picture": "", "take": None}])

    def test_the_file_taking_it_would_fill_the_slot_with_rides_on_the_game(self) -> None:
        taking = {**offered("i1", "g1", "Alien"), "kind": "wheel", "source": "vpinmediadb",
                  "size": "", "take": True, "picture": "https://media.invalid/wheel.png"}

        [game] = vps_home.lacking_media(FOUND, [taking])

        self.assertEqual(game["picture"], "https://media.invalid/wheel.png")
        self.assertEqual(game["take"], {"kind": "wheel", "source": "vpinmediadb", "size": ""})

    def test_a_game_that_cannot_be_filled_by_taking_carries_no_take(self) -> None:
        refused = {**offered("i1", "g1", "Alien"), "kind": "playfield", "source": "vpinmediadb",
                   "size": "4k", "take": False, "picture": "https://media.invalid/fss.png"}

        [game] = vps_home.lacking_media(FOUND, [refused])

        self.assertIsNone(game["take"])
        self.assertEqual(game["picture"], "https://media.invalid/fss.png")

    def test_a_game_the_comparison_does_not_count_as_held_is_listed_all_the_same(self) -> None:
        """The library's own record decides, not the last comparison's rollup."""
        self.assertFalse(FOUND["rows"][1]["in_library"])

        games = vps_home.lacking_media(FOUND, [offered("i2", FOUND["rows"][1]["vps_id"], "Beta")])

        self.assertEqual([one["game_id"] for one in games], ["i2"])

    def test_a_game_is_listed_before_any_comparison_has_run(self) -> None:
        uncompared = {"counted": "", "rows": []}

        shelf = vps_home.home(uncompared, (), 7, TODAY, media={"playfield": [
            offered("i1", "g1", "Alien")]})["missing"]

        self.assertEqual(shelf["playfield"]["games"], [
            {"vps_id": "g1", "game_id": "i1", "name": "Alien", "img_url": "", "vps": 0,
             "source": 1, "picture": "", "take": None}])

    def test_the_games_with_most_offered_come_first(self) -> None:
        names = [one["name"] for one in vps_home.lacking_media({"rows": []}, [
            offered("a", "a", "Zed", 3), offered("b", "b", "Abe", 1), offered("c", "c", "Mid", 3)])]

        self.assertEqual(names, ["Mid", "Zed", "Abe"])

    def test_two_games_on_one_entry_are_two_tiles(self) -> None:
        games = vps_home.lacking_media({"rows": []}, [offered("a", "e1", "Twin"),
                                                      offered("b", "e1", "Twin")])

        self.assertEqual([one["game_id"] for one in games], ["a", "b"])

    def test_home_puts_the_media_gaps_after_the_vps_kinds_it_keeps(self) -> None:
        shelf = vps_home.home(FOUND, KEPT, 7, TODAY,
                              media={"playfield": [offered("i1", "g1", "Alien")]})["missing"]

        self.assertEqual(list(shelf), ["b2sFiles", "romFiles", "playfield"])
        self.assertEqual(shelf["playfield"]["count"], 1)

    def test_a_media_kind_and_the_vps_kind_of_the_same_thing_are_one_tab(self) -> None:
        shelf = vps_home.home(WHEELS, KEPT_WHEELS, 7, TODAY,
                              media={"wheel": [offered("i1", "g1", "Alien")]})["missing"]

        self.assertEqual(list(shelf), ["b2sFiles", "romFiles", "wheelArtFiles"])
        self.assertNotIn("wheel", shelf)

    def test_a_backglass_picture_is_not_the_backglass_file(self) -> None:
        """A b2s file is an asset and a picture is media, so each has a tab of its own."""
        shelf = vps_home.home(FOUND, KEPT, 7, TODAY,
                              media={"backglass": [offered("i1", "g1", "Alien", 3)]})["missing"]

        self.assertEqual(list(shelf), ["b2sFiles", "romFiles", "backglass"])
        self.assertEqual((shelf["b2sFiles"]["count"], shelf["b2sFiles"]["games"][0]["source"]),
                         (1, 0))
        self.assertEqual((shelf["backglass"]["count"], shelf["backglass"]["on_vps"]), (1, 0))

    def test_a_game_both_name_is_one_tile_with_both_counts(self) -> None:
        [alien] = vps_home.lacking(WHEELS, "wheelArtFiles")
        shelf = vps_home.home(WHEELS, KEPT_WHEELS, 7, TODAY, media={"wheel": [
            offered("i1", alien["vps_id"], "Alien", 3)]})["missing"]["wheelArtFiles"]

        self.assertEqual(shelf["count"], 1)
        self.assertEqual([(one["game_id"], one["vps"], one["source"]) for one in shelf["games"]],
                         [("i1", alien["vps"], 3)])

    def test_the_tab_counts_every_game_and_shows_every_one_a_source_fills(self) -> None:
        listed = [{"vps_id": f"v{n}", "game_id": "", "name": f"Listed {n:02d}", "img_url": "",
                   "vps": 1, "source": 0} for n in range(vps_home.GAPS_SHOWN + 4)]
        filled = vps_home.lacking_media({"rows": []}, [
            offered(f"i{n}", f"s{n}", f"Filled {n:02d}") for n in range(vps_home.GAPS_SHOWN + 4)])

        tab = vps_home.gaps(listed, filled)

        self.assertEqual(tab["count"], len(listed) + len(filled))
        self.assertEqual(tab["on_vps"], len(listed))
        self.assertEqual(sum(1 for one in tab["games"] if one["source"]), len(filled))
        self.assertEqual(sum(1 for one in tab["games"] if not one["source"]), vps_home.GAPS_SHOWN)

    def test_a_tab_with_no_vps_part_says_so(self) -> None:
        shelf = vps_home.home(FOUND, KEPT, 7, TODAY,
                              media={"playfield": [offered("i1", "g1", "Alien")]})["missing"]

        self.assertEqual((shelf["playfield"]["on_vps"], shelf["romFiles"]["on_vps"]), (0, 1))

    def test_see_all_names_every_game_the_tab_counts_each_once(self) -> None:
        holders = {"g1": ["a", "b"]}
        shelf = vps_home.home(WHEELS, KEPT_WHEELS, 7, TODAY, holders=holders, media={
            "wheel": [offered("b", "g1", "Alien"), offered("c", "zz", "Zed")]})["missing"]

        tab = shelf["wheelArtFiles"]
        self.assertEqual(sorted(tab["game_ids"]), ["a", "b", "c"])
        self.assertEqual(tab["count"], len(tab["game_ids"]))
        self.assertEqual(len(set(tab["game_ids"])), tab["count"])

    def test_two_games_on_one_entry_are_two_rows_of_a_vps_listed_tab(self) -> None:
        shelf = vps_home.home(FOUND, KEPT, 7, TODAY, holders={"g1": ["a", "b"]})["missing"]

        self.assertEqual((shelf["romFiles"]["count"], shelf["romFiles"]["game_ids"]),
                         (2, ["a", "b"]))

    def test_an_entry_no_game_is_matched_to_is_not_counted(self) -> None:
        shelf = vps_home.home(FOUND, KEPT, 7, TODAY, holders={})["missing"]

        self.assertEqual(shelf["romFiles"]["count"], 0)

    def test_a_media_kind_nothing_offers_has_no_tab(self) -> None:
        shelf = vps_home.home(FOUND, KEPT, 7, TODAY, media={"backglass": []})["missing"]

        self.assertNotIn("backglass", shelf)

    def test_a_media_tab_lists_every_game_a_source_can_fill_not_the_first_few(self) -> None:
        many = [offered(f"i{n}", f"v{n}", f"Game {n:02d}") for n in range(vps_home.GAPS_SHOWN * 5)]

        shelf = vps_home.home({"rows": []}, (), 7, TODAY, media={"wheel": many})["missing"]
        tab = shelf["wheelArtFiles"]

        self.assertEqual(tab["count"], len(many))
        self.assertEqual(len(tab["games"]), len(many))


class HomeTests(unittest.TestCase):
    def test_every_chip_holds_what_it_filters_to_and_says_how_many(self) -> None:
        new = vps_home.home(FOUND, KEPT, 7, TODAY)["new"]

        self.assertEqual({chip: shelf["count"] for chip, shelf in new.items()},
                         {"all": 4, "yours": 3, "tableFiles": 2, "b2sFiles": 1,
                          "wheelArtFiles": 0, "topperFiles": 0, "pupPackFiles": 0})
        self.assertEqual(_ids(new["yours"]["releases"]), ["t1", "r1", "b1"])

    def test_a_kind_with_no_chip_is_in_all_and_nowhere_else(self) -> None:
        new = vps_home.home(FOUND, KEPT, 7, TODAY)["new"]

        self.assertIn("r1", _ids(new["all"]["releases"]))
        self.assertTrue(all("r1" not in _ids(shelf["releases"])
                            for chip, shelf in new.items() if chip not in ("all", "yours")))

    def test_a_shelf_is_cut_to_a_row_of_tiles_but_counts_all_of_it(self) -> None:
        many = {"people": PEOPLE, "rows": [
            {"vps_id": f"g{n}", "name": f"Game {n}", "releases": {
                "tableFiles": [release(f"t{n}", "2026-10-17T10:00:00Z")]}}
            for n in range(vps_home.SHELF_SHOWN + 5)]}

        shelf = vps_home.home(many, KEPT, 7, TODAY)["new"]["all"]

        self.assertEqual(len(shelf["releases"]), vps_home.SHELF_SHOWN)
        self.assertEqual(shelf["count"], vps_home.SHELF_SHOWN + 5)

    def test_a_gap_is_offered_only_for_a_kind_the_library_keeps(self) -> None:
        missing = vps_home.home(FOUND, ("tableFiles", "romFiles"), 7, TODAY)["missing"]

        self.assertEqual(list(missing), ["romFiles"])
        self.assertEqual(missing["romFiles"]["count"], 1)

    def test_the_comparison_is_passed_on(self) -> None:
        self.assertEqual(vps_home.home(FOUND, KEPT, 7, TODAY)["counted"], FOUND["counted"])
        self.assertEqual(vps_home.home({"rows": []}, KEPT, 7, TODAY)["counted"], "")


class KeptTests(unittest.TestCase):
    def _kept(self, *, assets: tuple[str, ...], media: tuple[str, ...]) -> list[str]:
        from common.games import library_policy
        with patch.object(library_policy, "kept_kinds",
                          return_value={"asset": set(assets), "media": set(media)}):
            return rollup.kept_listings()

    def test_a_fresh_install_keeps_every_kind_with_the_table_first(self) -> None:
        """Through the real policy, with nothing hidden."""
        kept = rollup.kept_listings()

        self.assertEqual(kept[0], "tableFiles")
        self.assertEqual(set(kept[1:]), {kind.listed_as for kind in vps_kinds.KINDS})

    def test_the_table_is_always_kept_and_comes_first(self) -> None:
        self.assertEqual(self._kept(assets=(), media=()), ["tableFiles"])

    def test_a_kind_is_kept_while_any_of_ours_behind_it_is(self) -> None:
        kept = self._kept(assets=("backglass", "altcolor_vni"), media=("wheel",))

        self.assertEqual(kept, ["tableFiles", "b2sFiles", "altColorFiles", "wheelArtFiles"])


def _days_ago(days: int) -> int:
    return int((time.time() - days * 86400) * 1000)


CATALOG = [
    {"id": "E1", "name": "Alpha", "manufacturer": "Gottlieb", "year": 1995,
     "tableFiles": [{"id": "t1", "authors": ["Guet35"], "version": "1.0",
                     "createdAt": _days_ago(2)},
                    {"id": "t0", "authors": ["Guet35"], "createdAt": _days_ago(90)}],
     "romFiles": [{"id": "r1", "authors": ["Destruk"]}]},
]


class RouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)
        marks = tempfile.TemporaryDirectory()
        self.addCleanup(marks.cleanup)
        patcher = patch.object(vps_marks, "PATH", Path(marks.name) / "marks.json")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(setattr, vps_releases, "_built", None)
        vps_releases._built = None

    def _get(self, query: str = "", *, counted: dict | None = None,
             kept: list[str] | None = None, media: dict | None = None) -> Any:
        """The route over a catalog; `kept` stands in for the policy, and None runs it.
        `media` is what the sources offer for gaps, none by default."""
        stored = {"computed": "x", "entries": {"E1": {"tables": 1, "held": []}}} \
            if counted is None else counted
        with patch.object(rollup, "stored", return_value=stored), \
                patch.object(media_fill, "offered_listing", return_value=media or {}), \
                patch.object(media_fill, "holders", return_value={"E1": ["game-e1"]}), \
                patch.object(rollup, "load_vpsdb", return_value=CATALOG), \
                patch.object(vps_releases, "load_vpsdb", return_value=CATALOG):
            if kept is None:
                return self.client.get("/discovery/home" + query)
            with patch.object(rollup, "kept_listings", return_value=kept):
                return self.client.get("/discovery/home" + query)

    def test_the_library_policy_decides_which_gaps_are_offered(self) -> None:
        """Run through the real policy: a fresh install keeps every kind."""
        body = self._get().json()

        self.assertEqual(list(body["missing"]), ["b2sFiles", "romFiles", "povFiles",
                                                 "wheelArtFiles", "topperFiles"])
        self.assertEqual(body["missing"]["romFiles"]["count"], 1)
        self.assertEqual(body["missing"]["b2sFiles"]["count"], 0)

    def test_the_gaps_a_source_can_fill_are_shelved_after_the_vps_kinds(self) -> None:
        body = self._get(media={"playfield": [offered("i1", "E1", "Alpha", 2)]}).json()

        self.assertEqual(list(body["missing"])[-1], "playfield")
        self.assertEqual(body["missing"]["playfield"], {"count": 1, "games": [
            {"vps_id": "E1", "game_id": "i1", "name": "Alpha", "img_url": "", "vps": 0,
             "source": 2, "picture": "", "take": None}], "on_vps": 0, "game_ids": ["i1"]})

    def test_the_route_answers_what_taking_a_gap_is(self) -> None:
        taking = {**offered("i1", "E1", "Alpha", 1), "kind": "playfield",
                  "source": "vpinmediadb", "size": "4k", "take": True,
                  "picture": "https://media.invalid/table.png"}

        [game] = self._get(media={"playfield": [taking]}).json()["missing"]["playfield"]["games"]

        self.assertEqual(game["picture"], "https://media.invalid/table.png")
        self.assertEqual(game["take"], {"kind": "playfield", "source": "vpinmediadb",
                                        "size": "4k"})

    def test_it_answers_the_shelves_in_one_read(self) -> None:
        response = self._get(kept=["tableFiles", "romFiles"])

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(set(body), {"counted", "days", "since", "new", "authors", "missing",
                                     "wanted", "dont_have", "family", "unmatched", "health",
                                     "opened", "updates"})
        self.assertEqual(body["days"], 7)
        self.assertEqual(list(body["missing"]), ["romFiles"])
        self.assertEqual(body["missing"]["romFiles"]["games"][0]["name"], "Alpha")

    def test_a_release_is_new_only_inside_the_window(self) -> None:
        week = self._get().json()["new"]["all"]
        year = self._get("?days=366").json()["new"]["all"]

        self.assertEqual((week["count"], [one["id"] for one in week["releases"]]), (1, ["t1"]))
        self.assertEqual(week["releases"][0]["version"], "1.0")
        self.assertEqual(year["count"], 2)

    def test_the_window_is_the_query(self) -> None:
        self.assertEqual(self._get("?days=30").json()["days"], 30)

    def test_a_window_outside_a_year_is_refused(self) -> None:
        self.assertEqual(self._get("?days=0").status_code, 422)
        self.assertEqual(self._get("?days=400").status_code, 422)

    def test_a_library_never_compared_says_so_and_lacks_nothing(self) -> None:
        body = self._get(counted={}).json()

        self.assertEqual(body["counted"], "")
        self.assertEqual(body["authors"], [])
        self.assertEqual({gap["count"] for gap in body["missing"].values()}, {0})


    def _missing(self, **kwargs: Any) -> Any:
        with patch.object(rollup, "stored", return_value={
                "computed": "x", "entries": {"E1": {"tables": 1, "held": []}}}), \
                patch.object(media_fill, "offered_listing",
                             return_value=kwargs.get("media") or {}), \
                patch.object(media_fill, "holders", return_value={"E1": ["game-e1"]}), \
                patch.object(rollup, "load_vpsdb", return_value=CATALOG), \
                patch.object(vps_releases, "load_vpsdb", return_value=CATALOG):
            return self.client.get("/discovery/missing")

    def test_the_missing_route_counts_what_home_counts_and_carries_none_of_the_rows(self) -> None:
        media = {"playfield": [offered("i1", "E1", "Alpha", 2)]}
        home = self._get(media=media).json()

        response = self._missing(media=media)

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["counted"], home["counted"])
        self.assertEqual(body["missing"], {
            tab: {key: one[key] for key in ("count", "on_vps", "game_ids")}
            for tab, one in home["missing"].items()})
        self.assertIn("playfield", body["missing"])

    def test_the_missing_route_names_the_games_a_source_can_fill_by_kind(self) -> None:
        media = {"playfield": [offered("i2", "E1"), offered("i1", "E1")],
                 "wheel": [offered("i3", "E1")]}

        body = self._missing(media=media).json()

        self.assertEqual({"playfield": ["i1", "i2"], "wheel": ["i3"]}, body["offered"])

    def test_the_missing_route_of_a_library_never_compared_counts_nothing(self) -> None:
        with patch.object(rollup, "stored", return_value={}), \
                patch.object(media_fill, "offered_listing", return_value={}), \
                patch.object(media_fill, "holders", return_value={}), \
                patch.object(rollup, "load_vpsdb", return_value=CATALOG), \
                patch.object(vps_releases, "load_vpsdb", return_value=CATALOG):
            body = self.client.get("/discovery/missing").json()

        self.assertEqual(body["counted"], "")
        self.assertEqual({tab["count"] for tab in body["missing"].values()}, {0})
        self.assertEqual({}, body["offered"])


class UpdatesRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)
        marks = tempfile.TemporaryDirectory()
        self.addCleanup(marks.cleanup)
        patcher = patch.object(vps_marks, "PATH", Path(marks.name) / "marks.json")
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def found(game: str, vps_id: str, release: str, now: str) -> dict[str, Any]:
        return {"game_id": f"id-{game}", "vps_id": vps_id, "game": game, "img_url": "",
                "moves": [{"source": "vps", "kind": "tableFiles", "release_id": release,
                           "vps_id": vps_id, "file": f"{game}.vpx", "then": "1.0",
                           "now": now}]}

    def get(self, path: str, found: list[dict[str, Any]]) -> Any:
        with patch.object(library_updates, "found", return_value=found):
            return self.client.get(path)

    def test_it_answers_the_games_and_how_many(self) -> None:
        found = [self.found("Batman", "g2", "t2", "1.1"), self.found("Alien", "g1", "t1", "1.2")]

        body = self.get("/discovery/updates", found).json()

        self.assertEqual((2, ["id-Alien", "id-Batman"]), (body["count"], body["game_ids"]))
        self.assertEqual(["Alien", "Batman"], [one["game"] for one in body["games"]])
        self.assertEqual("1.2", body["games"][0]["moves"][0]["now"])

    def test_a_skipped_version_is_not_counted_and_the_next_one_is(self) -> None:
        self.client.put("/discovery/marks", json={
            "state": "ignored", "scope": "version", "id": "t1", "detail": "1.2"})

        self.assertEqual(0, self.get(
            "/discovery/updates", [self.found("Alien", "g1", "t1", "1.2")]).json()["count"])
        self.assertEqual(1, self.get(
            "/discovery/updates", [self.found("Alien", "g1", "t1", "1.3")]).json()["count"])

    def test_ignoring_updates_leaves_every_later_version(self) -> None:
        self.client.put("/discovery/marks", json={
            "state": "ignored", "scope": "updates", "id": "t1"})

        self.assertEqual(0, self.get(
            "/discovery/updates", [self.found("Alien", "g1", "t1", "9.9")]).json()["count"])

    def test_home_carries_the_first_games_and_counts_them_all(self) -> None:
        found = [self.found(f"Game {n:02d}", f"v{n}", f"t{n}", "1.1")
                 for n in range(vps_updates.SHELF_SHOWN + 3)]

        with patch.object(rollup, "stored", return_value={}), \
                patch.object(media_fill, "offered_listing", return_value={}), \
                patch.object(media_fill, "holders", return_value={}), \
                patch.object(rollup, "load_vpsdb", return_value=[]), \
                patch.object(vps_releases, "load_vpsdb", return_value=[]), \
                patch.object(library_updates, "found", return_value=found):
            shelf = self.client.get("/discovery/home").json()["updates"]

        self.assertEqual(vps_updates.SHELF_SHOWN + 3, shelf["count"])
        self.assertEqual(vps_updates.SHELF_SHOWN, len(shelf["games"]))
        self.assertEqual(vps_updates.SHELF_SHOWN + 3, len(shelf["game_ids"]))

    def test_home_goes_without_the_shelf_when_the_updates_cannot_be_found(self) -> None:
        with patch.object(rollup, "stored", return_value={}), \
                patch.object(media_fill, "offered_listing", return_value={}), \
                patch.object(media_fill, "holders", return_value={}), \
                patch.object(rollup, "load_vpsdb", return_value=[]), \
                patch.object(vps_releases, "load_vpsdb", return_value=[]), \
                patch.object(library_updates, "found", side_effect=RuntimeError("no index")):
            response = self.client.get("/discovery/home")

        self.assertEqual(200, response.status_code)
        self.assertEqual(0, response.json()["updates"]["count"])

    def test_a_game_named_is_the_only_one_looked_at(self) -> None:
        one = [self.found("Alien", "g1", "t1", "1.2")]

        with patch.object(library_updates, "found", side_effect=AssertionError("whole library")), \
                patch.object(library_updates, "found_for", return_value=one) as asked:
            body = self.client.get("/discovery/updates?game_id=id-Alien").json()

        asked.assert_called_once_with("id-Alien")
        self.assertEqual(["id-Alien"], body["game_ids"])


if __name__ == "__main__":
    unittest.main()
