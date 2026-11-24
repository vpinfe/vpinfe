"""Discovery's Games to Match shelf: the library's games with no VPS match that have one
likely entry, by name and year."""

from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from starlette.testclient import TestClient

import httpapi
from common.games import game_lens, library_ops, media_fill
from common.games import library_vps_state as rollup
from common.games.game_parser import GameParser
from common.games.game_repository import game_to_row
from common.online import vps_home, vps_marks, vps_releases
from tests.support.library import TempTree, write_game


def entry(vps_id: str, name: str, *, manufacturer: str = "", year: int | None = None,
          img_url: str = "") -> dict[str, Any]:
    return {"vps_id": vps_id, "name": name, "in_library": False, "img_url": img_url,
            "manufacturer": manufacturer, "year": year, "releases": {}}


def mine(game_id: str, name: str, *, manufacturer: str = "", year: object = "") -> dict[str, Any]:
    return {"game_id": game_id, "name": name, "manufacturer": manufacturer, "year": year}


def found(*rows: dict[str, Any]) -> dict[str, Any]:
    return {"counted": "2026-10-17T00:00:00Z", "people": [], "rows": list(rows)}


def _ids(shelf: list[dict[str, Any]]) -> list[str]:
    return [one["game_id"] for one in shelf]


def _likely(shelf: list[dict[str, Any]]) -> list[str]:
    return [one["suggestion"]["vps_id"] for one in shelf]


class SuggestionTests(unittest.TestCase):
    def test_a_game_whose_name_and_year_match_an_entry_is_suggested_it(self) -> None:
        shelf = vps_home.unmatched(
            found(entry("e1", "Joust", manufacturer="Williams", year=1983, img_url="/j.png")),
            [mine("g1", "Joust", manufacturer="Williams", year=1983)])

        self.assertEqual(shelf, [{
            "game_id": "g1", "game": "Joust", "manufacturer": "Williams", "year": "1983",
            "suggestion": {"vps_id": "e1", "game": "Joust", "img_url": "/j.png",
                           "manufacturer": "Williams", "year": "1983"}}])

    def test_the_name_is_matched_as_the_family_shelf_matches_titles(self) -> None:
        shelf = vps_home.unmatched(found(entry("e1", "Atlántis (Pro)", year=1975)),
                                   [mine("g1", "ATLANTIS!", year=1975)])

        self.assertEqual(_likely(shelf), ["e1"])

    def test_a_different_title_is_not_suggested(self) -> None:
        rows = found(entry("e1", "Star Trek TNG", year=1993))

        self.assertEqual(vps_home.unmatched(rows, [mine("g1", "Star Trek", year=1993)]), [])

    def test_the_year_may_be_one_either_side_and_no_more(self) -> None:
        rows = found(entry("e1", "Joust", year=1983))
        for year, listed in ((1982, True), (1983, True), (1984, True), (1981, False),
                             (1985, False)):
            with self.subTest(year=year):
                shelf = vps_home.unmatched(rows, [mine("g1", "Joust", year=year)])
                self.assertEqual(bool(shelf), listed)

    def test_a_game_with_no_year_is_not_suggested_anything(self) -> None:
        rows = found(entry("e1", "Joust", year=1983))

        for year in ("", None, "n/a"):
            with self.subTest(year=year):
                self.assertEqual(vps_home.unmatched(rows, [mine("g1", "Joust", year=year)]), [])

    def test_an_entry_with_no_year_is_not_suggested(self) -> None:
        rows = found(entry("e1", "Joust"))

        self.assertEqual(vps_home.unmatched(rows, [mine("g1", "Joust", year=1983)]), [])

    def test_the_nearest_year_wins(self) -> None:
        rows = found(entry("a", "Joust", year=1984), entry("b", "Joust", year=1983),
                     entry("c", "Joust", year=1982))

        shelf = vps_home.unmatched(rows, [mine("g1", "Joust", year=1983)])

        self.assertEqual(_likely(shelf), ["b"])

    def test_one_entry_is_offered_however_many_fit(self) -> None:
        rows = found(entry("a", "Joust", year=1982), entry("b", "Joust", year=1984))

        self.assertEqual(len(vps_home.unmatched(rows, [mine("g1", "Joust", year=1983)])), 1)

    def test_a_tie_on_the_year_goes_to_the_same_maker_then_the_id(self) -> None:
        rows = found(entry("z", "Joust", manufacturer="Bally", year=1982),
                     entry("c", "Joust", manufacturer="Williams", year=1984),
                     entry("b", "Joust", manufacturer="Williams", year=1984))

        by_maker = vps_home.unmatched(rows, [mine("g1", "Joust", manufacturer="bally ",
                                                  year=1983)])
        by_id = vps_home.unmatched(rows, [mine("g1", "Joust", year=1983)])

        self.assertEqual(_likely(by_maker), ["z"])
        self.assertEqual(_likely(by_id), ["b"])

    def test_a_nearer_year_beats_the_same_maker(self) -> None:
        rows = found(entry("a", "Joust", manufacturer="Bally", year=1982),
                     entry("b", "Joust", manufacturer="Williams", year=1983))

        shelf = vps_home.unmatched(rows, [mine("g1", "Joust", manufacturer="Bally", year=1983)])

        self.assertEqual(_likely(shelf), ["b"])

    def test_versions_of_one_game_in_one_year_by_one_maker_give_the_lowest_id(self) -> None:
        rows = found(*(entry(one, "Batman", manufacturer="Stern", year=2008)
                       for one in ("v3", "v1", "v2")))

        shelf = vps_home.unmatched(rows, [mine("g1", "Batman", manufacturer="Stern", year=2008)])

        self.assertEqual(_likely(shelf), ["v1"])

    def test_an_ignored_entry_is_never_suggested_and_the_next_one_is(self) -> None:
        rows = found(entry("a", "Joust", year=1983), entry("b", "Joust", year=1984))
        marks = vps_marks.Marks([{"state": "ignored", "scope": "game", "id": "a",
                                  "detail": "", "since": "x"}])

        shelf = vps_home.unmatched(rows, [mine("g1", "Joust", year=1983)], marks)

        self.assertEqual(_likely(shelf), ["b"])

    def test_a_game_whose_only_entry_is_ignored_is_not_listed(self) -> None:
        marks = vps_marks.Marks([{"state": "ignored", "scope": "game", "id": "a",
                                  "detail": "", "since": "x"}])

        shelf = vps_home.unmatched(found(entry("a", "Joust", year=1983)),
                                   [mine("g1", "Joust", year=1983)], marks)

        self.assertEqual(shelf, [])

    def test_a_year_is_four_digits(self) -> None:
        rows = found(entry("a", "Joust", year=1983))

        for said in ("19", "19830", "198x", "+1983", "1_983", "1983.0"):
            with self.subTest(said=said):
                self.assertEqual(vps_home.unmatched(rows, [mine("g1", "Joust", year=said)]), [])

    def test_the_games_come_by_name_then_id(self) -> None:
        rows = found(entry("e1", "Alien", year=1980), entry("e2", "batman", year=1991),
                     entry("e3", "Cactus", year=1970))

        shelf = vps_home.unmatched(rows, [
            mine("z", "Cactus", year=1970), mine("b", "Batman", year=1991),
            mine("a", "Batman", year=1991), mine("c", "Alien", year=1980)])

        self.assertEqual(_ids(shelf), ["c", "a", "b", "z"])

    def test_an_entry_held_by_another_game_is_still_suggested(self) -> None:
        held = {**entry("e1", "Joust", year=1983), "in_library": True}

        shelf = vps_home.unmatched(found(held), [mine("g1", "Joust", year=1983)])

        self.assertEqual(_likely(shelf), ["e1"])

    def test_an_entry_with_no_name_is_never_suggested(self) -> None:
        shelf = vps_home.unmatched(found(entry("e1", " ", year=1983)),
                                   [mine("g1", "(Pro)", year=1983)])

        self.assertEqual(shelf, [])


class HomeTests(unittest.TestCase):
    def test_home_counts_every_game_and_shows_a_row_of_them(self) -> None:
        rows = found(*(entry(f"e{n:02d}", f"Game {n:02d}", year=1990) for n in range(20)))
        games = [mine(f"g{n:02d}", f"Game {n:02d}", year=1990) for n in range(20)]

        shelf = vps_home.home(rows, ["tableFiles"], 7, unmatched_games=games)["unmatched"]

        self.assertEqual(shelf["count"], 20)
        self.assertEqual(len(shelf["games"]), vps_home.SHELF_SHOWN)

    def test_no_unmatched_game_is_an_empty_shelf(self) -> None:
        self.assertEqual(vps_home.home(found(entry("e1", "Joust", year=1983)), ["tableFiles"], 7
                                       )["unmatched"], {"count": 0, "games": []})

    def test_home_leaves_out_an_entry_the_marks_ignore(self) -> None:
        marks = vps_marks.Marks([{"state": "ignored", "scope": "game", "id": "e1",
                                  "detail": "", "since": "x"}])

        shelf = vps_home.home(found(entry("e1", "Joust", year=1983)), ["tableFiles"], 7,
                              marks=marks, unmatched_games=[mine("g1", "Joust", year=1983)]
                              )["unmatched"]

        self.assertEqual(shelf, {"count": 0, "games": []})

    def test_it_needs_no_comparison_of_the_library(self) -> None:
        rows = {**found(entry("e1", "Joust", year=1983)), "counted": ""}

        shelf = vps_home.home(rows, ["tableFiles"], 7,
                              unmatched_games=[mine("g1", "Joust", year=1983)])["unmatched"]

        self.assertEqual(shelf["count"], 1)


CATALOG = [
    {"id": "E1", "name": "Joust", "manufacturer": "Williams", "year": 1983,
     "tableFiles": [{"id": "a1", "authors": ["Ann"], "tableFormat": "VPX"}]},
    {"id": "E2", "name": "Batman", "manufacturer": "Data East", "year": 1991},
]


class _Game:
    def __init__(self, meta: dict[str, Any]) -> None:
        self.meta_config = meta


def _library(**games: dict[str, Any]) -> dict[str, Any]:
    return {key: _Game(meta) for key, meta in games.items()}


class LibraryTests(unittest.TestCase):
    """The games `discovery_home` hands the shelf are those the library has no match for."""

    def _games(self, library: dict[str, Any]) -> list[dict[str, Any]]:
        row = {"Joust": ("Williams", 1983)}

        def to_row(game: Any) -> dict[str, Any]:
            name = game.meta_config["name"]
            maker, year = row.get(name, ("", ""))
            return {"name": name, "manufacturer": maker, "year": year}

        with patch.object(library_ops.game_repository, "catalog", return_value=library), \
                patch.object(library_ops.game_repository, "game_to_row", side_effect=to_row):
            return library_ops._unmatched_games()

    def test_a_game_with_no_match_is_listed_with_its_name_maker_and_year(self) -> None:
        library = _library(g1={"name": "Joust"})

        self.assertEqual(self._games(library), [
            {"game_id": "g1", "name": "Joust", "manufacturer": "Williams", "year": 1983}])

    def test_a_matched_game_is_not_listed(self) -> None:
        library = _library(g1={"name": "Joust", "Info": {"VPSId": "E1"}},
                           g2={"name": "Other"})

        self.assertEqual([one["game_id"] for one in self._games(library)], ["g2"])

    def test_a_library_that_cannot_be_read_gives_home_no_shelf_and_not_an_error(self) -> None:
        library = _library(g1={"name": "Joust"}, g2={"name": "Broken"})

        def to_row(game: Any) -> dict[str, Any]:
            if game.meta_config["name"] == "Broken":
                raise ValueError("malformed")
            return {"name": "Joust", "manufacturer": "", "year": 1983}

        with patch.object(library_ops.game_repository, "catalog", return_value=library), \
                patch.object(library_ops.game_repository, "game_to_row", side_effect=to_row):
            self.assertEqual(library_ops._unmatched_games(), [])

    def test_a_game_declared_to_be_in_no_catalog_is_not_listed(self) -> None:
        library = _library(g1={"name": "Joust", "vpinfe": {"alt_vpsid": None}})

        self.assertEqual(self._games(library), [])


def _info(game_id: str, title: str, **vpinfe: Any) -> dict[str, Any]:
    return {"Info": {"Title": title, "Manufacturer": "Williams", "Year": "1983"},
            "vpinfe": {"schema": 2, "game_id": game_id, **vpinfe},
            "tables": {"tbl" + game_id[-7:]: {"id": "tbl" + game_id[-7:], "filename": "a.vpx"}}}


class AgainstTheGamesGridTests(TempTree):
    """The shelf and the Games grid's Unmatched view read the same library: the grid lists
    every game with no entry in force, and the shelf all of those but the ones somebody
    declared to be in no catalog."""

    def test_the_shelf_lists_what_the_unmatched_view_lists_less_the_declared(self) -> None:
        folders = {
            "none": _info("Game00000001", "Joust"),
            "found": {**_info("Game00000002", "Joust"),
                      "Info": {"Title": "Joust", "VPSId": "E1"}},
            "chosen": _info("Game00000003", "Joust", alt_vpsid="E1"),
            "declared": _info("Game00000004", "Joust", alt_vpsid=None),
            "blank": _info("Game00000005", "Joust", alt_vpsid=""),
        }
        for name, info in folders.items():
            write_game(self.root, name, info=info, vpx=False, files={"a.vpx": b"vpx"})
        games = {str((game.meta_config or {})["vpinfe"]["game_id"]): game
                 for game in GameParser(str(self.root)).get_all_games()}

        with patch.object(library_ops.game_repository, "catalog", return_value=games):
            shelf = {one["game_id"] for one in library_ops._unmatched_games()}
        grid = {game_id for game_id, game in games.items()
                if not game_lens.game_resource(game_to_row(game), game_id)["vps_id"]}

        self.assertEqual(grid, {"Game00000001", "Game00000004", "Game00000005"})
        self.assertEqual(shelf, grid - {"Game00000004"})

    def test_a_game_carries_its_name_maker_and_year_from_the_same_row_as_the_grid(self) -> None:
        write_game(self.root, "Joust", info=_info("Game00000001", "Joust"), vpx=False,
                   files={"a.vpx": b"vpx"})
        games = {"Game00000001": GameParser(str(self.root)).get_all_games()[0]}

        with patch.object(library_ops.game_repository, "catalog", return_value=games):
            [one] = library_ops._unmatched_games()

        self.assertEqual(one, {"game_id": "Game00000001", "name": "Joust",
                               "manufacturer": "Williams", "year": "1983"})


class RouteTests(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)
        marks = tempfile.TemporaryDirectory()
        self.addCleanup(marks.cleanup)
        patcher = patch.object(vps_marks, "PATH", Path(marks.name) / "marks.json")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(setattr, vps_releases, "_built", None)
        vps_releases._built = None
        plays = patch.object(library_ops.launchers, "playable_formats", return_value=("VPX",))
        plays.start()
        self.addCleanup(plays.stop)

    def _get(self, games: list[dict[str, Any]]) -> Any:
        with patch.object(rollup, "stored", return_value={"computed": "x", "entries": {}}), \
                patch.object(media_fill, "offered_listing", return_value={}), \
                patch.object(media_fill, "holders", return_value={}), \
                patch.object(library_ops, "_unmatched_games", return_value=games), \
                patch.object(rollup, "load_vpsdb", return_value=CATALOG), \
                patch.object(vps_releases, "load_vpsdb", return_value=CATALOG):
            return self.client.get("/discovery/home")

    def test_the_route_serves_each_game_with_the_entry_it_is_likely_to_be(self) -> None:
        response = self._get([mine("g1", "joust", manufacturer="Williams", year=1984),
                              mine("g2", "Unknown", year=1990)])

        self.assertEqual(response.status_code, 200, response.text)
        shelf = response.json()["unmatched"]
        self.assertEqual(shelf["count"], 1)
        [one] = shelf["games"]
        self.assertEqual((one["game_id"], one["game"], one["manufacturer"], one["year"]),
                         ("g1", "joust", "Williams", "1984"))
        self.assertEqual(one["suggestion"], {"vps_id": "E1", "game": "Joust", "img_url": "",
                                             "manufacturer": "Williams", "year": "1983"})

    def test_a_library_with_nothing_unmatched_has_no_shelf(self) -> None:
        self.assertEqual(self._get([]).json()["unmatched"], {"count": 0, "games": []})


if __name__ == "__main__":
    unittest.main()
