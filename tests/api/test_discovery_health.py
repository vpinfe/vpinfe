"""Discovery's Library Health shelf: the entries two or more games of the library are matched
to, and the games with no table at all."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from starlette.testclient import TestClient

import httpapi
from common.games import game_repository, library_ops, media_fill
from common.games import library_vps_state as rollup
from common.online import vps_home, vps_marks, vps_releases
from tests.support.home_shelf import held_game


def row(vps_id: str, name: str, *, manufacturer: str = "", year: int | None = None,
        img_url: str = "") -> dict[str, Any]:
    return {"vps_id": vps_id, "name": name, "in_library": True, "img_url": img_url,
            "manufacturer": manufacturer, "year": year, "releases": {"tableFiles": []}}


def found(*rows: dict[str, Any], counted: str = "2026-10-17T00:00:00Z") -> dict[str, Any]:
    return {"counted": counted, "people": [], "rows": list(rows)}


def _ids(shelf: dict[str, Any], key: str) -> list[str]:
    return [one[key] for one in shelf["games"]]


class DuplicateTests(unittest.TestCase):
    def test_two_games_matched_to_one_entry_are_one_duplicate_naming_both(self) -> None:
        shelf = vps_home.health(
            found(row("E1", "Atlantis", manufacturer="Gottlieb", year=1975, img_url="pic")),
            [held_game("g1", "Atlantis", "E1"), held_game("g2", "Atlantis copy", "E1"),
             held_game("g3", "Batman", "E2")])["duplicates"]

        self.assertEqual(shelf["count"], 1)
        [one] = shelf["games"]
        self.assertEqual((one["vps_id"], one["game"], one["manufacturer"], one["year"],
                          one["img_url"], one["game_ids"]),
                         ("E1", "Atlantis", "Gottlieb", "1975", "pic", ["g1", "g2"]))

    def test_one_game_on_an_entry_is_not_a_duplicate(self) -> None:
        shelf = vps_home.health(found(row("E1", "Atlantis")),
                                [held_game("g1", "Atlantis", "E1")])

        self.assertEqual(shelf["duplicates"], {"count": 0, "games": []})

    def test_games_matched_to_nothing_are_not_duplicates_of_each_other(self) -> None:
        shelf = vps_home.health(found(), [held_game("g1", "A"), held_game("g2", "B")])

        self.assertEqual(shelf["duplicates"]["count"], 0)

    def test_the_entry_with_the_most_games_comes_first_then_name_then_catalog_order(self) -> None:
        rows = found(row("E9", "Joust"), row("E2", "Joust"), row("E5", "Atlantis"),
                     row("E4", "Batman"))
        games = [held_game(f"{vps_id}{n}", "x", vps_id)
                 for vps_id in ("E2", "E4", "E5", "E9") for n in range(2)]
        games += [held_game("E4z", "x", "E4"), held_game("A1", "Joust", "A0"),
                  held_game("A2", "Joust", "A0")]

        shelf = vps_home.health(rows, games)["duplicates"]

        self.assertEqual(_ids(shelf, "vps_id"), ["E4", "E5", "E9", "E2", ""])
        self.assertEqual(shelf["games"][-1]["game_ids"], ["A1", "A2"])

    def test_an_ignored_entry_is_left_out(self) -> None:
        marks = vps_marks.Marks([{"state": "ignored", "scope": "game", "id": "E1",
                                  "detail": "", "since": "x"}])

        shelf = vps_home.health(found(row("E1", "Atlantis")),
                                [held_game("g1", "A", "E1"), held_game("g2", "B", "E1")],
                                marks)

        self.assertEqual(shelf["duplicates"]["count"], 0)

    def test_it_does_not_wait_for_the_library_to_be_compared(self) -> None:
        shelf = vps_home.health(found(row("E1", "Atlantis"), counted=""),
                                [held_game("g1", "A", "E1"), held_game("g2", "B", "E1")])

        self.assertEqual(shelf["duplicates"]["count"], 1)

    def test_home_counts_every_entry_and_shows_a_row_of_them(self) -> None:
        rows = found(*(row(f"E{n:02d}", f"Game {n:02d}") for n in range(20)))
        games = [held_game(f"g{n}{k}", "x", f"E{n:02d}") for n in range(20) for k in range(2)]

        shelf = vps_home.home(rows, ["tableFiles"], 7, library=games)["health"]["duplicates"]

        self.assertEqual(shelf["count"], 20)
        self.assertEqual(len(shelf["games"]), vps_home.SHELF_SHOWN)


class NoTableTests(unittest.TestCase):
    def test_a_game_with_no_table_is_listed_with_what_its_entry_says(self) -> None:
        shelf = vps_home.health(
            found(row("E1", "Atlantis", manufacturer="Gottlieb", year=1975, img_url="pic")),
            [held_game("g1", "My Atlantis", "E1", tables=0),
             held_game("g2", "Batman", "E2")])["no_tables"]

        self.assertEqual(shelf["count"], 1)
        [one] = shelf["games"]
        self.assertEqual((one["game_id"], one["vps_id"], one["game"], one["manufacturer"],
                          one["year"], one["img_url"]),
                         ("g1", "E1", "My Atlantis", "Gottlieb", "1975", "pic"))

    def test_a_game_matched_to_no_entry_is_listed_bare(self) -> None:
        [one] = vps_home.health(found(), [held_game("g1", "Mine", "", tables=0)]
                                )["no_tables"]["games"]

        self.assertEqual((one["vps_id"], one["manufacturer"], one["year"], one["img_url"]),
                         ("", "", "", ""))

    def test_a_game_matched_to_an_entry_the_catalog_does_not_list_has_no_entry_to_open(
            self) -> None:
        shelf = vps_home.health(found(row("E1", "Atlantis")), [
            held_game("g1", "Gone", "GONE", tables=0),
            held_game("g2", "Listed", "E1", tables=0)])["no_tables"]

        self.assertEqual([(one["game_id"], one["vps_id"]) for one in shelf["games"]],
                         [("g1", ""), ("g2", "E1")])

    def test_games_come_by_name_and_those_that_tie_keep_the_librarys_order(self) -> None:
        shelf = vps_home.health(found(), [
            held_game("g3", "joust", tables=0), held_game("g1", "Zorro", tables=0),
            held_game("g2", "Atlantis", tables=0), held_game("g0", "Joust", tables=0),
            held_game("g9", "Full")])["no_tables"]

        self.assertEqual(_ids(shelf, "game_id"), ["g2", "g3", "g0", "g1"])

    def test_a_game_matched_to_an_ignored_entry_is_left_out(self) -> None:
        marks = vps_marks.Marks([{"state": "ignored", "scope": "game", "id": "E1",
                                  "detail": "", "since": "x"}])

        shelf = vps_home.health(found(), [held_game("g1", "A", "E1", tables=0),
                                          held_game("g2", "B", "", tables=0)], marks)

        self.assertEqual(_ids(shelf["no_tables"], "game_id"), ["g2"])

    def test_it_does_not_wait_for_the_library_to_be_compared(self) -> None:
        shelf = vps_home.health(found(counted=""), [held_game("g1", "A", tables=0)])

        self.assertEqual(shelf["no_tables"]["count"], 1)

    def test_a_library_that_could_not_be_read_has_no_findings(self) -> None:
        self.assertEqual(vps_home.home(found(), ["tableFiles"], 7)["health"],
                         {"duplicates": {"count": 0, "games": []},
                          "no_tables": {"count": 0, "games": []}})


def _game(title: str, vps_id: str = "", tables: tuple[str, ...] = ("a.vpx",),
          override: str | None = "") -> SimpleNamespace:
    meta: dict[str, Any] = {
        "Info": {"VPSId": vps_id, "Title": title},
        "tables": {name: {"id": name, "filename": name} for name in tables}}
    if override is None:
        meta["vpinfe"] = {"alt_vpsid": None}
    elif override:
        meta["vpinfe"] = {"alt_vpsid": override}
    return SimpleNamespace(meta_config=meta, game_dir_name=title,
                           full_path_game=f"/library/{title}")


class LibraryGamesTests(unittest.TestCase):
    def _read(self, held: dict[str, Any]) -> list[dict[str, Any]]:
        with patch.object(game_repository, "catalog", return_value=held):
            return library_ops.library_games()

    def test_each_game_carries_its_entry_in_force_and_its_table_count(self) -> None:
        got = self._read({"g1": _game("Atlantis", "E1", ("a.vpx", "b.vpx")),
                          "g2": _game("Empty", "E2", ()),
                          "g3": _game("Moved", "E3", override="E9"),
                          "g4": _game("Declared none", "E4", override=None)})

        self.assertEqual([(one["game_id"], one["vps_id"], one["tables"]) for one in got],
                         [("g1", "E1", 2), ("g2", "E2", 0), ("g3", "E9", 1), ("g4", "", 1)])

    def test_it_follows_the_catalogs_order_and_names_each_game_as_the_list_does(self) -> None:
        got = self._read({"b": _game("Zed"), "a": _game("Alpha")})

        self.assertEqual([(one["game_id"], one["name"]) for one in got],
                         [("b", "Zed"), ("a", "Alpha")])

    def test_a_library_that_cannot_be_read_gives_no_games(self) -> None:
        with patch.object(game_repository, "catalog", side_effect=OSError("gone")):
            self.assertEqual(library_ops.library_games(), [])


CATALOG = [
    {"id": "E1", "name": "Alpha", "manufacturer": "Gottlieb", "year": 1995,
     "tableFiles": [{"id": "a1", "authors": ["Guet35"], "tableFormat": "VPX"}]},
    {"id": "E2", "name": "Beta", "manufacturer": "Bally", "year": 1980,
     "tableFiles": [{"id": "b1", "authors": ["Guet35"], "tableFormat": "VPX"}]},
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
        self.games = [held_game("g1", "Alpha", "E1"), held_game("g2", "Alpha 2", "E1"),
                      held_game("g3", "Shell", "E2", tables=0)]

    def _get(self) -> Any:
        with patch.object(rollup, "stored", return_value={"computed": "x", "entries": {}}), \
                patch.object(media_fill, "offered_listing", return_value={}), \
                patch.object(media_fill, "holders", return_value={}), \
                patch.object(library_ops, "library_games", return_value=self.games), \
                patch.object(rollup, "load_vpsdb", return_value=CATALOG), \
                patch.object(vps_releases, "load_vpsdb", return_value=CATALOG):
            return self.client.get("/discovery/home")

    def test_the_route_serves_both_findings_with_what_the_catalog_says(self) -> None:
        response = self._get()

        self.assertEqual(response.status_code, 200, response.text)
        health = response.json()["health"]
        self.assertEqual(health["duplicates"], {"count": 1, "games": [{
            "vps_id": "E1", "game": "Alpha", "img_url": health["duplicates"]["games"][0][
                "img_url"], "manufacturer": "Gottlieb", "year": "1995",
            "game_ids": ["g1", "g2"]}]})
        [bare] = health["no_tables"]["games"]
        self.assertEqual((bare["game_id"], bare["vps_id"], bare["game"], bare["manufacturer"]),
                         ("g3", "E2", "Shell", "Bally"))

    def test_marking_an_entry_ignored_takes_its_games_off_both(self) -> None:
        vps_marks.mark(vps_marks.IGNORED, vps_marks.GAME, "E1")
        vps_marks.mark(vps_marks.IGNORED, vps_marks.GAME, "E2")

        self.assertEqual(self._get().json()["health"],
                         {"duplicates": {"count": 0, "games": []},
                          "no_tables": {"count": 0, "games": []}})


if __name__ == "__main__":
    unittest.main()
