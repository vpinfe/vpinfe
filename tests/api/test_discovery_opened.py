"""Discovery's Opened but Not Imported shelf: the games a release page was opened for that
the library still has none of."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

from starlette.testclient import TestClient

import httpapi
from common.games import library_ops, media_fill, opened_pages
from common.games import library_vps_state as rollup
from common.online import vps_home, vps_marks, vps_releases


def game(vps_id: str, name: str, *, in_library: bool = False, manufacturer: str = "",
         year: int | None = None) -> dict[str, Any]:
    return {"vps_id": vps_id, "name": name, "in_library": in_library, "img_url": "",
            "manufacturer": manufacturer, "year": year, "releases": {"tableFiles": []}}


def found(*rows: dict[str, Any], counted: str = "2026-10-17T00:00:00Z") -> dict[str, Any]:
    return {"counted": counted, "people": [], "rows": list(rows)}


def page(vps_id: str, at: str, release: str = "r1", kind: str = "tableFiles") -> dict[str, Any]:
    return {"game_id": vps_id, "kind": kind, "vps_file_id": release, "page": "https://x/p",
            "at": at}


ROWS = found(game("a", "Atlantis", manufacturer="Bally", year=1989),
             game("h", "Joust", in_library=True),
             game("b", "Batman"))


class ShelfTests(unittest.TestCase):
    def test_a_game_whose_page_was_opened_and_is_not_held_is_on_the_shelf(self) -> None:
        shelf = vps_home.opened_games(ROWS, [page("a", "2026-10-17T10:00:00Z")])

        self.assertEqual(shelf, [{"vps_id": "a", "game": "Atlantis", "img_url": "",
                                  "manufacturer": "Bally", "year": "1989",
                                  "at": "2026-10-17T10:00:00Z"}])

    def test_a_game_the_library_holds_is_not_on_it(self) -> None:
        self.assertEqual(vps_home.opened_games(ROWS, [page("h", "2026-10-17T10:00:00Z")]), [])

    def test_a_game_vps_does_not_list_is_not_on_it(self) -> None:
        self.assertEqual(vps_home.opened_games(ROWS, [page("gone", "2026-10-17T10:00:00Z")]), [])

    def test_an_entry_with_no_name_is_not_on_it(self) -> None:
        unnamed = found(game("u", "  "))

        self.assertEqual(vps_home.opened_games(unnamed, [page("u", "2026-10-17T10:00:00Z")]), [])

    def test_an_ignored_game_is_not_on_it(self) -> None:
        marks = vps_marks.Marks([{"state": "ignored", "scope": "game", "id": "a",
                                  "detail": "", "since": "x"}])

        self.assertEqual(
            vps_home.opened_games(ROWS, [page("a", "2026-10-17T10:00:00Z")], marks), [])

    def test_a_wanted_game_stays_on_it(self) -> None:
        marks = vps_marks.Marks([{"state": "wanted", "scope": "game", "id": "a",
                                  "detail": "", "since": "x"}])

        self.assertEqual(
            len(vps_home.opened_games(ROWS, [page("a", "2026-10-17T10:00:00Z")], marks)), 1)

    def test_a_library_never_compared_has_no_shelf(self) -> None:
        never = found(*ROWS["rows"], counted="")

        self.assertEqual(vps_home.opened_games(never, [page("a", "2026-10-17T10:00:00Z")]), [])

    def test_a_game_opened_for_several_releases_is_one_tile_stamped_with_the_latest(self) -> None:
        shelf = vps_home.opened_games(ROWS, [
            page("a", "2026-10-17T08:00:00Z", "r1"),
            page("a", "2026-10-17T11:00:00Z", "r2", "b2sFiles"),
            page("a", "2026-10-17T09:00:00Z", "r3")])

        self.assertEqual([(one["vps_id"], one["at"]) for one in shelf],
                         [("a", "2026-10-17T11:00:00Z")])

    def test_the_game_opened_last_comes_first(self) -> None:
        shelf = vps_home.opened_games(ROWS, [page("a", "2026-10-17T08:00:00Z"),
                                             page("b", "2026-10-17T10:00:00Z")])

        self.assertEqual([one["vps_id"] for one in shelf], ["b", "a"])

    def test_the_order_is_by_time_whatever_order_the_journal_is_in(self) -> None:
        later = [page("b", "2026-10-17T10:00:00Z"), page("a", "2026-10-17T08:00:00Z")]

        self.assertEqual([one["vps_id"] for one in vps_home.opened_games(ROWS, later)],
                         [one["vps_id"] for one in vps_home.opened_games(ROWS, later[::-1])])

    def test_a_page_with_no_readable_time_is_left_out(self) -> None:
        self.assertEqual(vps_home.opened_games(ROWS, [page("a", "")]), [])

    def test_home_shows_every_game_and_does_not_cut_the_row(self) -> None:
        many = [game(f"g{n:02d}", f"Game {n}") for n in range(vps_home.SHELF_SHOWN + 8)]
        journal = [page(one["vps_id"], f"2026-10-17T10:{n:02d}:00Z")
                   for n, one in enumerate(many)]

        shelf = vps_home.home(found(*many), ["tableFiles"], 7, opened=journal)["opened"]

        self.assertEqual(shelf["count"], len(many))
        self.assertEqual(len(shelf["games"]), len(many))

    def test_home_without_a_journal_has_an_empty_shelf(self) -> None:
        self.assertEqual(vps_home.home(ROWS, ["tableFiles"], 7)["opened"],
                         {"count": 0, "games": []})


CATALOG = [
    {"id": "E1", "name": "Alpha", "manufacturer": "Gottlieb", "year": 1995,
     "tableFiles": [{"id": "a1", "authors": ["Guet35"], "tableFormat": "VPX"}]},
    {"id": "E2", "name": "Beta", "manufacturer": "Bally", "year": 1980,
     "tableFiles": [{"id": "b1", "authors": ["Guet35"], "tableFormat": "VPX"},
                    {"id": "b2", "authors": ["Destruk"], "tableFormat": "VPX"}]},
]


class RouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        for patcher in (
                patch.object(vps_marks, "PATH", Path(folder.name) / "marks.json"),
                patch.object(opened_pages, "OPENED_PAGES_PATH",
                             Path(folder.name) / "opened_pages.json"),
                patch.object(library_ops.launchers, "playable_formats", return_value=("VPX",))):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(setattr, vps_releases, "_built", None)
        vps_releases._built = None

    def _get(self, *, counted: dict | None = None) -> Any:
        stored = {"computed": "x", "entries": {"E1": {"tables": 1, "held": []}}} \
            if counted is None else counted
        with patch.object(rollup, "stored", return_value=stored), \
                patch.object(media_fill, "offered_listing", return_value={}), \
                patch.object(media_fill, "holders", return_value={}), \
                patch.object(rollup, "load_vpsdb", return_value=CATALOG), \
                patch.object(vps_releases, "load_vpsdb", return_value=CATALOG):
            return self.client.get("/discovery/home")

    def test_the_route_serves_the_pages_opened_for_games_not_in_the_library(self) -> None:
        opened_pages.note("E2", "tableFiles", "b1", "https://vps/b1")
        opened_pages.note("E1", "tableFiles", "a1", "https://vps/a1")

        response = self._get()

        self.assertEqual(response.status_code, 200, response.text)
        shelf = response.json()["opened"]
        self.assertEqual(shelf["count"], 1)
        [one] = shelf["games"]
        self.assertEqual((one["vps_id"], one["game"], one["manufacturer"], one["year"]),
                         ("E2", "Beta", "Bally", "1980"))
        self.assertTrue(one["at"])

    def test_nothing_opened_is_an_empty_shelf(self) -> None:
        self.assertEqual(self._get().json()["opened"], {"count": 0, "games": []})

    def test_a_release_imported_and_tied_leaves_the_shelf(self) -> None:
        opened_pages.note("E2", "tableFiles", "b1", "https://vps/b1")
        opened_pages.forget("E2", "b1")

        self.assertEqual(self._get().json()["opened"], {"count": 0, "games": []})

    def test_a_page_opened_over_a_day_ago_is_not_on_the_shelf(self) -> None:
        opened_pages.note("E2", "tableFiles", "b1", "https://vps/b1")
        kept = json.loads(opened_pages.OPENED_PAGES_PATH.read_text(encoding="utf-8"))
        kept["opened"][0]["at"] = (datetime.now(UTC) - timedelta(hours=25)).isoformat(
            timespec="seconds").replace("+00:00", "Z")
        opened_pages.OPENED_PAGES_PATH.write_text(json.dumps(kept), encoding="utf-8")

        self.assertEqual(self._get().json()["opened"], {"count": 0, "games": []})

    def test_a_library_never_compared_has_no_shelf(self) -> None:
        opened_pages.note("E2", "tableFiles", "b1", "https://vps/b1")

        self.assertEqual(self._get(counted={}).json()["opened"], {"count": 0, "games": []})

    def test_marking_a_game_ignored_takes_it_off_the_shelf(self) -> None:
        opened_pages.note("E2", "tableFiles", "b1", "https://vps/b1")
        vps_marks.mark(vps_marks.IGNORED, vps_marks.GAME, "E2")

        self.assertEqual(self._get().json()["opened"], {"count": 0, "games": []})


if __name__ == "__main__":
    unittest.main()
