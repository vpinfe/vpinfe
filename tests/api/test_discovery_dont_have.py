"""Discovery's Games You Don't Have shelf: the games VPS lists that the library has none of,
the ones the most people made a table for first."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from starlette.testclient import TestClient

import httpapi
from common.games import library_ops, media_fill
from common.games import library_vps_state as rollup
from common.online import vps_home, vps_marks, vps_releases

PEOPLE: list[dict[str, Any]] = [{"name": name, "aliases": []}
                                for name in ("Ann", "Bo", "Cy", "Di")]


def table(rid: str, by: tuple[int, ...], fmt: str = "VPX") -> dict[str, Any]:
    return {"id": rid, "by": list(by), "format": fmt, "added": "2026-10-01T00:00:00Z",
            "held": False, "mod": False, "version": ""}


def game(vps_id: str, name: str, *tables: dict[str, Any], in_library: bool = False,
         **more: Any) -> dict[str, Any]:
    return {"vps_id": vps_id, "name": name, "in_library": in_library, "img_url": "",
            "manufacturer": "", "year": None, "releases": {"tableFiles": list(tables)}, **more}


def found(*rows: dict[str, Any], counted: str = "2026-10-17T00:00:00Z") -> dict[str, Any]:
    return {"counted": counted, "people": PEOPLE, "rows": list(rows)}


def _names(shelf: list[dict[str, Any]]) -> list[str]:
    return [one["game"] for one in shelf]


class ShelfTests(unittest.TestCase):
    def test_the_games_with_the_most_authors_come_first(self) -> None:
        shelf = vps_home.dont_have(found(
            game("a", "Alien", table("1", (0,))),
            game("b", "Batman", table("2", (0,)), table("3", (1,)), table("4", (2,))),
            game("c", "Cactus", table("5", (0,)), table("6", (1,)))))

        self.assertEqual([(one["game"], one["authors"]) for one in shelf],
                         [("Batman", 3), ("Cactus", 2), ("Alien", 1)])

    def test_games_with_as_many_authors_come_in_name_order(self) -> None:
        shelf = vps_home.dont_have(found(
            game("t", "Twilight Zone", table("1", (0,))),
            game("p", "Pirates", table("2", (1,))),
            game("m", "medieval", table("3", (2,)))))

        self.assertEqual(_names(shelf), ["medieval", "Pirates", "Twilight Zone"])

    def test_games_with_the_same_name_and_authors_keep_the_catalog_order(self) -> None:
        shelf = vps_home.dont_have(found(game("z", "Alien", table("1", (0,))),
                                         game("b", "Alien", table("2", (0,))),
                                         game("m", "Alien", table("3", (0,)))))

        self.assertEqual([one["vps_id"] for one in shelf], ["z", "b", "m"])

    def test_a_person_counts_once_however_many_tables_they_made(self) -> None:
        shelf = vps_home.dont_have(found(
            game("a", "Alien", table("1", (0,)), table("2", (0,)), table("3", (0,)))))

        self.assertEqual(shelf[0]["authors"], 1)

    def test_a_person_is_counted_by_the_author_a_release_names_first(self) -> None:
        """A table made by four people is not four authors of the game."""
        shelf = vps_home.dont_have(found(
            game("a", "Alien", table("1", (0, 1, 2, 3)), table("2", (0, 1)))))

        self.assertEqual(shelf[0]["authors"], 1)

    def test_a_release_naming_nobody_adds_no_author(self) -> None:
        shelf = vps_home.dont_have(found(game("a", "Alien", table("1", ()), table("2", (1,)))))

        self.assertEqual(shelf[0]["authors"], 1)

    def test_a_game_the_library_holds_is_left_out(self) -> None:
        shelf = vps_home.dont_have(found(
            game("a", "Alien", table("1", (0,)), in_library=True),
            game("b", "Batman", table("2", (0,)))))

        self.assertEqual(_names(shelf), ["Batman"])

    def test_only_a_table_in_the_format_the_library_plays_counts(self) -> None:
        shelf = vps_home.dont_have(found(
            game("a", "Alien", table("1", (0,), "VPX"), table("2", (1,), "FP"),
                 table("3", (2,), "VP9")),
            game("b", "Batman", table("4", (0,), "FP"))), formats=("VPX",))

        self.assertEqual([(one["game"], one["authors"]) for one in shelf], [("Alien", 1)])

    def test_a_format_other_than_vpx_counts_where_a_launcher_plays_it(self) -> None:
        shelf = vps_home.dont_have(found(
            game("a", "Alien", table("1", (0,), "VPX"), table("2", (1,), "FP"),
                 table("3", (2,), "VP9")),
            game("b", "Batman", table("4", (0,), "FP"))), formats=("VPX", "FP"))

        self.assertEqual([(one["game"], one["authors"]) for one in shelf],
                         [("Alien", 2), ("Batman", 1)])

    def test_no_format_to_narrow_to_counts_every_format(self) -> None:
        shelf = vps_home.dont_have(found(
            game("a", "Alien", table("1", (0,), "VPX"), table("2", (1,), "FP"),
                 table("3", (2,), "VP9")),
            game("b", "Batman", table("4", (0,), "FP"))))

        self.assertEqual([(one["game"], one["authors"]) for one in shelf],
                         [("Alien", 3), ("Batman", 1)])

    def test_a_game_with_tables_only_in_formats_no_launcher_plays_is_left_out(self) -> None:
        shelf = vps_home.dont_have(found(
            game("b", "Batman", table("4", (0,), "FP"), table("5", (1,), "VP9"))),
            formats=("VPX",))

        self.assertEqual(shelf, [])

    def test_the_format_is_matched_without_regard_to_case(self) -> None:
        shelf = vps_home.dont_have(found(game("a", "Alien", table("1", (0,), "fp"))),
                                   formats=("FP",))

        self.assertEqual(_names(shelf), ["Alien"])

    def test_the_home_answer_counts_the_formats_it_was_given(self) -> None:
        row = game("b", "Batman", table("4", (0,), "FP"))

        played = vps_home.home(found(row), [], 7, formats=("FP",))["dont_have"]
        other = vps_home.home(found(row), [], 7, formats=("VPX",))["dont_have"]

        self.assertEqual((played["count"], other["count"]), (1, 0))

    def test_a_table_filed_under_no_format_counts_where_playable_only_would_keep_it(self) -> None:
        shelf = vps_home.dont_have(found(game("a", "Alien", table("1", (0,), ""))),
                                   formats=("VPX",))

        self.assertEqual(_names(shelf), ["Alien"])

    def test_a_game_with_no_table_is_not_a_game_to_have(self) -> None:
        shelf = vps_home.dont_have(found(
            {**game("a", "Alien"), "releases": {"b2sFiles": [table("1", (0,))]}}))

        self.assertEqual(shelf, [])

    def test_a_game_with_no_name_is_not_listed(self) -> None:
        self.assertEqual(vps_home.dont_have(found(game("a", " ", table("1", (0,))))), [])

    def test_an_ignored_game_is_left_out_and_a_wanted_one_stays(self) -> None:
        marks = vps_marks.Marks([
            {"state": "ignored", "scope": "game", "id": "a", "detail": "", "since": "x"},
            {"state": "wanted", "scope": "game", "id": "b", "detail": "", "since": "x"}])

        shelf = vps_home.dont_have(found(game("a", "Alien", table("1", (0,))),
                                         game("b", "Batman", table("2", (0,)))), marks)

        self.assertEqual(_names(shelf), ["Batman"])

    def test_one_ignored_release_does_not_take_the_game_with_it(self) -> None:
        marks = vps_marks.Marks([
            {"state": "ignored", "scope": "release", "id": "1", "detail": "", "since": "x"}])

        shelf = vps_home.dont_have(found(game("a", "Alien", table("1", (0,)))), marks)

        self.assertEqual(_names(shelf), ["Alien"])

    def test_nothing_is_a_game_you_lack_before_the_library_is_compared(self) -> None:
        uncounted = found(game("a", "Alien", table("1", (0,))), counted="")
        uncounted["rows"][0]["in_library"] = None

        self.assertEqual(vps_home.dont_have(uncounted), [])
        self.assertEqual(vps_home.home(uncounted, ("tableFiles",), 7)["dont_have"],
                         {"count": 0, "games": []})

    def test_a_tile_carries_who_made_the_game_and_when(self) -> None:
        row = game("a", "Alien", table("1", (0,)), manufacturer="Williams", year=1997)

        self.assertEqual(vps_home.dont_have(found(row))[0], {
            "vps_id": "a", "game": "Alien", "img_url": "", "manufacturer": "Williams",
            "year": "1997", "authors": 1})

    def test_a_game_with_no_year_says_none(self) -> None:
        self.assertEqual(vps_home.dont_have(found(game("a", "Alien", table("1", (0,)))))[0]["year"],
                         "")

    def test_home_counts_every_game_and_shows_a_row_of_them(self) -> None:
        many = [game(f"g{n:02d}", f"Game {n:02d}", table(f"t{n}", (0,))) for n in range(20)]

        shelf = vps_home.home(found(*many), ("tableFiles",), 7)["dont_have"]

        self.assertEqual(shelf["count"], 20)
        self.assertEqual(len(shelf["games"]), vps_home.SHELF_SHOWN)
        self.assertEqual(shelf["games"][0]["game"], "Game 00")


CATALOG = [
    {"id": "E1", "name": "Alpha", "manufacturer": "Gottlieb", "year": 1995,
     "tableFiles": [{"id": "a1", "authors": ["Guet35"], "tableFormat": "VPX"}]},
    {"id": "E2", "name": "Beta", "manufacturer": "Williams", "year": 1997, "theme": [],
     "tableFiles": [{"id": "b1", "authors": ["Guet35", "Destruk"], "tableFormat": "VPX"},
                    {"id": "b2", "authors": ["Destruk"], "tableFormat": "VPX"},
                    {"id": "b3", "authors": ["Wildman"], "tableFormat": "FP"}]},
    {"id": "E3", "name": "Gamma", "manufacturer": "Bally", "year": 1993,
     "tableFiles": [{"id": "c1", "authors": ["Guet35"], "tableFormat": "VPX"}]},
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
        plays = patch.object(library_ops.launchers, "playable_formats", return_value=("VPX",))
        plays.start()
        self.addCleanup(plays.stop)

    def _get(self, *, counted: dict | None = None, catalog: list | None = None) -> Any:
        stored = {"computed": "x", "entries": {"E1": {"tables": 1, "held": []}}} \
            if counted is None else counted
        with patch.object(rollup, "stored", return_value=stored), \
                patch.object(media_fill, "offered_listing", return_value={}), \
                patch.object(media_fill, "holders", return_value={}), \
                patch.object(rollup, "load_vpsdb", return_value=catalog or CATALOG), \
                patch.object(vps_releases, "load_vpsdb", return_value=catalog or CATALOG):
            return self.client.get("/discovery/home")

    def test_the_route_serves_the_games_the_library_lacks_with_the_most_authors_first(self) -> None:
        response = self._get()

        self.assertEqual(response.status_code, 200, response.text)
        shelf = response.json()["dont_have"]
        self.assertEqual(shelf["count"], 2)
        self.assertEqual([(one["game"], one["authors"], one["manufacturer"], one["year"])
                          for one in shelf["games"]],
                         [("Beta", 2, "Williams", "1997"), ("Gamma", 1, "Bally", "1993")])

    def test_the_route_counts_the_formats_the_enabled_launchers_play(self) -> None:
        for plays, beta in ((("VPX",), 2), (("VPX", "FP"), 3), ((), 3)):
            with self.subTest(plays=plays), patch.object(
                    library_ops.launchers, "playable_formats", return_value=plays):
                shelf = self._get().json()["dont_have"]

                self.assertEqual([(one["game"], one["authors"]) for one in shelf["games"]],
                                 [("Beta", beta), ("Gamma", 1)])

    def test_marking_a_game_ignored_takes_it_off_the_shelf(self) -> None:
        vps_marks.mark(vps_marks.IGNORED, vps_marks.GAME, "E2")

        shelf = self._get().json()["dont_have"]

        self.assertEqual([one["game"] for one in shelf["games"]], ["Gamma"])
        self.assertEqual(shelf["count"], 1)

    def test_a_wanted_game_stays_on_the_shelf(self) -> None:
        vps_marks.mark(vps_marks.WANTED, vps_marks.GAME, "E2")

        shelf = self._get().json()["dont_have"]

        self.assertEqual([one["game"] for one in shelf["games"]], ["Beta", "Gamma"])

    def test_one_author_under_several_spellings_counts_once(self) -> None:
        """Case and spacing do not tell people apart, and VPW's credits are one author."""
        catalog = [{"id": "E9", "name": "Delta", "manufacturer": "Bally", "year": 1990,
                    "tableFiles": [
                        {"id": "d1", "authors": ["Guet35"], "tableFormat": "VPX"},
                        {"id": "d2", "authors": ["  guet35 "], "tableFormat": "VPX"},
                        {"id": "d3", "authors": ["VPW"], "tableFormat": "VPX"},
                        {"id": "d4", "authors": ["VPinWorkshop"], "tableFormat": "VPX"},
                        {"id": "d5", "authors": ["VPW Team"], "tableFormat": "VPX"}]}]

        shelf = self._get(catalog=catalog).json()["dont_have"]

        self.assertEqual([(one["game"], one["authors"]) for one in shelf["games"]],
                         [("Delta", 2)])

    def test_a_library_never_compared_has_no_shelf(self) -> None:
        shelf = self._get(counted={}).json()["dont_have"]

        self.assertEqual(shelf, {"count": 0, "games": []})


if __name__ == "__main__":
    unittest.main()
