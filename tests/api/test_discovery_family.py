"""Discovery's Other Versions of Your Games shelf: the games VPS lists that the library has
none of, that share a title with a game it holds."""

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


def table(rid: str, by: tuple[int, ...] = (0,), fmt: str = "VPX") -> dict[str, Any]:
    return {"id": rid, "by": list(by), "format": fmt, "added": "2026-10-01T00:00:00Z",
            "held": False, "mod": False, "version": ""}


def game(vps_id: str, name: str, *tables: dict[str, Any], in_library: bool = False,
         manufacturer: str = "", year: int | None = None) -> dict[str, Any]:
    return {"vps_id": vps_id, "name": name, "in_library": in_library, "img_url": "",
            "manufacturer": manufacturer, "year": year,
            "releases": {"tableFiles": list(tables or (table(vps_id + "t"),))}}


def found(*rows: dict[str, Any], counted: str = "2026-10-17T00:00:00Z") -> dict[str, Any]:
    return {"counted": counted, "people": [{"name": "Ann", "aliases": []}], "rows": list(rows)}


def _names(shelf: list[dict[str, Any]]) -> list[str]:
    return [one["vps_id"] for one in shelf]


class TitleKeyTests(unittest.TestCase):
    def test_case_accents_punctuation_and_spacing_do_not_tell_titles_apart(self) -> None:
        for name in ("Atlantis", "ATLANTIS", " atlantis ", "Atlántis", "Atlantis!"):
            with self.subTest(name=name):
                self.assertEqual(vps_home.title_key(name), vps_home.title_key("Atlantis"))

    def test_a_parenthesised_part_is_not_part_of_the_title(self) -> None:
        self.assertEqual(vps_home.title_key("Star Trek (Pro)"), vps_home.title_key("Star Trek"))

    def test_a_different_word_is_a_different_title(self) -> None:
        self.assertNotEqual(vps_home.title_key("Star Trek"), vps_home.title_key("Star Trek TNG"))
        self.assertNotEqual(vps_home.title_key("Joust"), vps_home.title_key("Jousts"))

    def test_a_name_of_only_punctuation_has_no_title(self) -> None:
        self.assertEqual(vps_home.title_key("(Pro) !"), "")


class ShelfTests(unittest.TestCase):
    def test_a_game_sharing_a_title_with_a_held_one_is_on_the_shelf(self) -> None:
        shelf = vps_home.family(found(
            game("a1", "Atlantis", in_library=True, manufacturer="Gottlieb", year=1975),
            game("a2", "Atlantis", manufacturer="LTD", year=1978),
            game("b1", "Batman")))

        self.assertEqual(_names(shelf), ["a2"])
        self.assertEqual(shelf[0]["held"],
                         [{"vps_id": "a1", "game": "Atlantis", "manufacturer": "Gottlieb",
                           "year": "1975"}])

    def test_a_game_the_library_holds_is_not_a_version_of_itself(self) -> None:
        self.assertEqual(vps_home.family(found(game("a1", "Atlantis", in_library=True))), [])

    def test_two_held_games_of_one_title_are_not_on_the_shelf(self) -> None:
        shelf = vps_home.family(found(game("a1", "Atlantis", in_library=True),
                                      game("a2", "Atlantis", in_library=True)))

        self.assertEqual(shelf, [])

    def test_a_title_the_library_has_none_of_is_not_on_it(self) -> None:
        shelf = vps_home.family(found(game("a1", "Atlantis"), game("a2", "Atlantis")))

        self.assertEqual(shelf, [])

    def test_every_held_entry_of_the_title_is_listed_oldest_first(self) -> None:
        shelf = vps_home.family(found(
            game("a1", "Atlantis", in_library=True, year=1989),
            game("a2", "Atlantis", in_library=True, year=1975),
            game("a3", "Atlantis", year=1978)))

        self.assertEqual([one["vps_id"] for one in shelf[0]["held"]], ["a2", "a1"])

    def test_a_title_spelled_another_way_still_matches(self) -> None:
        shelf = vps_home.family(found(game("a1", "Star Trek", in_library=True),
                                      game("a2", "STAR TREK (Pro)")))

        self.assertEqual(_names(shelf), ["a2"])

    def test_the_games_with_the_most_authors_come_first_then_name_then_catalog_order(self) -> None:
        shelf = vps_home.family(found(
            game("h", "Joust", in_library=True),
            game("j2", "Joust", table("1", (0,))),
            game("j3", "Joust", table("2", (0,)), table("3", (1,))),
            game("j1", "Joust", table("4", (0,)))))

        self.assertEqual(_names(shelf), ["j3", "j2", "j1"])

    def test_a_game_with_no_table_a_launcher_plays_is_left_out(self) -> None:
        rows = found(game("h", "Joust", in_library=True),
                     game("j2", "Joust", table("1", (0,), "FP")))

        self.assertEqual(vps_home.family(rows, formats=("VPX",)), [])
        self.assertEqual(_names(vps_home.family(rows, formats=("VPX", "FP"))), ["j2"])

    def test_an_ignored_game_is_left_out_and_a_wanted_one_stays(self) -> None:
        marks = vps_marks.Marks([
            {"state": "ignored", "scope": "game", "id": "a2", "detail": "", "since": "x"},
            {"state": "wanted", "scope": "game", "id": "a3", "detail": "", "since": "x"}])

        shelf = vps_home.family(found(game("a1", "Atlantis", in_library=True),
                                      game("a2", "Atlantis"), game("a3", "Atlantis")), marks)

        self.assertEqual(_names(shelf), ["a3"])

    def test_nothing_is_a_version_of_what_you_hold_before_the_library_is_compared(self) -> None:
        rows = found(game("a1", "Atlantis", in_library=True), game("a2", "Atlantis"), counted="")

        self.assertEqual(vps_home.family(rows), [])
        self.assertEqual(vps_home.home(rows, ["tableFiles"], 7)["family"],
                         {"count": 0, "games": []})

    def test_home_counts_every_game_and_shows_a_row_of_them(self) -> None:
        many = [game(f"g{n:02d}", "Joust", table(f"t{n}")) for n in range(20)]

        shelf = vps_home.home(found(game("h", "Joust", in_library=True), *many),
                              ["tableFiles"], 7)["family"]

        self.assertEqual(shelf["count"], 20)
        self.assertEqual(len(shelf["games"]), vps_home.SHELF_SHOWN)

    def test_it_is_part_of_the_games_you_do_not_have(self) -> None:
        rows = found(game("h", "Joust", in_library=True), game("j2", "Joust"),
                     game("b", "Batman"))

        shelf = vps_home.home(rows, ["tableFiles"], 7)

        self.assertEqual(shelf["dont_have"]["count"], 2)
        self.assertEqual(shelf["family"]["count"], 1)


CATALOG = [
    {"id": "E1", "name": "Alpha", "manufacturer": "Gottlieb", "year": 1995,
     "tableFiles": [{"id": "a1", "authors": ["Guet35"], "tableFormat": "VPX"}]},
    {"id": "E2", "name": "alpha", "manufacturer": "Bally", "year": 1980,
     "tableFiles": [{"id": "b1", "authors": ["Guet35", "Destruk"], "tableFormat": "VPX"},
                    {"id": "b2", "authors": ["Destruk"], "tableFormat": "VPX"}]},
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

    def _get(self, *, counted: dict | None = None) -> Any:
        stored = {"computed": "x", "entries": {"E1": {"tables": 1, "held": []}}} \
            if counted is None else counted
        with patch.object(rollup, "stored", return_value=stored), \
                patch.object(media_fill, "offered_listing", return_value={}), \
                patch.object(media_fill, "holders", return_value={}), \
                patch.object(rollup, "load_vpsdb", return_value=CATALOG), \
                patch.object(vps_releases, "load_vpsdb", return_value=CATALOG):
            return self.client.get("/discovery/home")

    def test_the_route_serves_the_other_versions_with_what_the_library_holds(self) -> None:
        response = self._get()

        self.assertEqual(response.status_code, 200, response.text)
        shelf = response.json()["family"]
        self.assertEqual(shelf["count"], 1)
        [one] = shelf["games"]
        self.assertEqual((one["vps_id"], one["game"], one["manufacturer"], one["year"],
                          one["authors"]), ("E2", "alpha", "Bally", "1980", 2))
        self.assertEqual(one["held"], [{"vps_id": "E1", "game": "Alpha",
                                        "manufacturer": "Gottlieb", "year": "1995"}])

    def test_marking_a_game_ignored_takes_it_off_the_shelf(self) -> None:
        vps_marks.mark(vps_marks.IGNORED, vps_marks.GAME, "E2")

        self.assertEqual(self._get().json()["family"], {"count": 0, "games": []})

    def test_a_library_never_compared_has_no_shelf(self) -> None:
        self.assertEqual(self._get(counted={}).json()["family"], {"count": 0, "games": []})


if __name__ == "__main__":
    unittest.main()
