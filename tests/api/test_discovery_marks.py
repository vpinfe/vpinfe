"""Wanted and Ignored: one store in which a thing is unmarked, Wanted or Ignored and never
two, what an ignored thing leaves off Home, and the Wanted shelf."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import patch

from starlette.testclient import TestClient

import httpapi
from common import service_errors
from common.games import library_vps_state
from common.online import vps_home, vps_marked, vps_marks

TODAY = date(2026, 10, 18)
KEPT = ("tableFiles", "b2sFiles", "romFiles")
PEOPLE = [{"name": "Alpha", "aliases": []}, {"name": "Beta", "aliases": []}]


def release(rid: str, added: str, *, by: tuple[int, ...] = (0,), version: str = "") -> dict:
    return {"id": rid, "by": list(by), "added": added, "held": False, "mod": False,
            "version": version}


FOUND: dict[str, Any] = {
    "counted": "2026-10-17T00:00:00Z",
    "people": PEOPLE,
    "rows": [
        {"vps_id": "g1", "name": "Alien", "in_library": True, "img_url": "a.png",
         "listed": ["tableFiles", "b2sFiles", "romFiles"], "held": ["tableFiles"],
         "releases": {
             "tableFiles": [release("t1", "2026-10-17T10:00:00Z", version="1.2")],
             "b2sFiles": [release("b1", "2026-10-16T10:00:00Z", by=(1,))],
             "romFiles": [release("r1", "2026-10-16T10:00:00Z")]}},
        {"vps_id": "g2", "name": "Batman", "in_library": True, "img_url": "b.png",
         "listed": ["tableFiles", "romFiles"], "held": ["tableFiles"],
         "releases": {"tableFiles": [release("t2", "2026-10-17T11:00:00Z")]}},
    ],
}


def offered(game_id: str, vps_id: str, name: str, available: int) -> dict[str, Any]:
    return {"game_id": game_id, "vps_id": vps_id, "name": name, "available": available}


def marks(*found: tuple[str, str, str, str]) -> vps_marks.Marks:
    return vps_marks.Marks([{"state": state, "scope": scope, "id": one, "detail": detail,
                             "since": "2026-10-18T00:00:00Z"}
                            for state, scope, one, detail in found])


def _ids(shelf: list[dict[str, Any]]) -> list[str]:
    return [one["id"] for one in shelf]


class InAFolder(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / "marks.json"
        patcher = patch.object(vps_marks, "PATH", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)


class StoreTests(InAFolder):
    def test_a_thing_nobody_marked_holds_nothing(self) -> None:
        self.assertEqual(vps_marks.listed(), [])
        self.assertEqual(vps_marks.read().state(vps_marks.GAME, "g1"), "")

    def test_marking_a_thing_answers_the_state_it_held_and_replaces_it(self) -> None:
        self.assertEqual(vps_marks.mark(vps_marks.WANTED, vps_marks.GAME, "g1"), "")
        self.assertEqual(vps_marks.mark(vps_marks.IGNORED, vps_marks.GAME, "g1"),
                         vps_marks.WANTED)

        self.assertEqual([(one["state"], one["id"]) for one in vps_marks.listed()],
                         [(vps_marks.IGNORED, "g1")])

    def test_marking_a_state_it_already_holds_keeps_the_day_it_was_first_marked(self) -> None:
        vps_marks.mark(vps_marks.IGNORED, vps_marks.GAME, "g1")
        first = vps_marks.listed()[0]["since"]

        with patch.object(vps_marks.timestamps, "utc_now_iso", return_value="later"):
            vps_marks.mark(vps_marks.IGNORED, vps_marks.GAME, "g1")

        self.assertEqual(vps_marks.listed()[0]["since"], first)

    def test_clearing_answers_what_the_thing_held_and_nothing_for_an_unmarked_one(self) -> None:
        vps_marks.mark(vps_marks.IGNORED, vps_marks.RELEASE, "t1")

        self.assertEqual(vps_marks.clear(vps_marks.RELEASE, "t1"), vps_marks.IGNORED)
        self.assertEqual(vps_marks.clear(vps_marks.RELEASE, "t1"), "")
        self.assertEqual(vps_marks.listed(), [])

    def test_a_game_and_its_release_are_marked_apart(self) -> None:
        vps_marks.mark(vps_marks.IGNORED, vps_marks.GAME, "same")
        vps_marks.mark(vps_marks.WANTED, vps_marks.RELEASE, "same")

        found = vps_marks.read()

        self.assertEqual((found.state(vps_marks.GAME, "same"),
                          found.state(vps_marks.RELEASE, "same")),
                         (vps_marks.IGNORED, vps_marks.WANTED))

    def test_a_gap_is_marked_by_game_and_kind(self) -> None:
        vps_marks.mark(vps_marks.IGNORED, vps_marks.GAP, "g1", "romFiles")

        found = vps_marks.read()

        self.assertTrue(found.gap_ignored("g1", "romFiles"))
        self.assertFalse(found.gap_ignored("g1", "b2sFiles"))
        self.assertFalse(found.gap_ignored("g2", "romFiles"))

    def test_only_a_game_or_a_release_can_be_wanted(self) -> None:
        for scope, detail in ((vps_marks.GAP, "romFiles"), (vps_marks.UPDATES, ""),
                              (vps_marks.VERSION, "1.2")):
            with self.subTest(scope), self.assertRaises(service_errors.RefusedError):
                vps_marks.mark(vps_marks.WANTED, scope, "x", detail)

    def test_a_detail_belongs_to_a_gap_or_a_skipped_version_and_to_nothing_else(self) -> None:
        with self.assertRaises(service_errors.RefusedError):
            vps_marks.mark(vps_marks.IGNORED, vps_marks.GAP, "g1")
        with self.assertRaises(service_errors.RefusedError):
            vps_marks.mark(vps_marks.IGNORED, vps_marks.GAME, "g1", "romFiles")

    def test_a_state_a_scope_or_an_id_that_is_not_one_is_refused(self) -> None:
        for args in (("liked", vps_marks.GAME, "g1"), (vps_marks.IGNORED, "table", "g1"),
                     (vps_marks.IGNORED, vps_marks.GAME, " ")):
            with self.subTest(args), self.assertRaises(service_errors.RefusedError):
                vps_marks.mark(*args)
        self.assertEqual(vps_marks.listed(), [])

    def test_a_store_from_another_schema_or_one_that_cannot_be_read_is_empty(self) -> None:
        self.path.write_text(json.dumps({"schema": 99, "marks": [
            {"state": "ignored", "scope": "game", "id": "g1"}]}), encoding="utf-8")
        self.assertEqual(vps_marks.listed(), [])

        self.path.write_text("{not json", encoding="utf-8")
        self.assertEqual(vps_marks.listed(), [])

    def test_a_file_that_cannot_be_used_is_moved_aside_before_the_next_write(self) -> None:
        other = json.dumps({"schema": 99, "marks": [
            {"state": "ignored", "scope": "game", "id": "old"}]})
        for text in (other, "{not json"):
            with self.subTest(text[:12]):
                self.path.write_text(text, encoding="utf-8")

                vps_marks.mark(vps_marks.IGNORED, vps_marks.GAME, "g1")

                self.assertEqual(vps_marks.aside().read_text(encoding="utf-8"), text)
                self.assertEqual([one["id"] for one in vps_marks.listed()], ["g1"])
                vps_marks.clear(vps_marks.GAME, "g1")

    def test_one_copy_is_kept_and_a_sound_file_is_never_moved(self) -> None:
        self.path.write_text("{first", encoding="utf-8")
        vps_marks.mark(vps_marks.IGNORED, vps_marks.GAME, "g1")
        vps_marks.mark(vps_marks.IGNORED, vps_marks.GAME, "g2")

        self.assertEqual(vps_marks.aside().read_text(encoding="utf-8"), "{first")
        self.assertEqual(sorted(one["id"] for one in vps_marks.listed()), ["g1", "g2"])

        self.path.write_text("{second", encoding="utf-8")
        vps_marks.mark(vps_marks.IGNORED, vps_marks.GAME, "g3")

        self.assertEqual(vps_marks.aside().read_text(encoding="utf-8"), "{second")

    def test_a_read_leaves_a_file_that_cannot_be_used_where_it_is(self) -> None:
        self.path.write_text("{not json", encoding="utf-8")

        vps_marks.listed()
        vps_marks.clear(vps_marks.GAME, "g1")

        self.assertEqual(self.path.read_text(encoding="utf-8"), "{not json")
        self.assertFalse(vps_marks.aside().exists())

    def test_an_id_and_a_detail_are_stored_without_surrounding_blanks(self) -> None:
        vps_marks.mark(vps_marks.IGNORED, vps_marks.GAP, " g1 ", " b2sFiles ")

        self.assertEqual([(one["id"], one["detail"]) for one in vps_marks.listed()],
                         [("g1", "b2sFiles")])
        self.assertEqual(vps_marks.clear(vps_marks.GAP, "g1 ", " b2sFiles"), vps_marks.IGNORED)

    def test_a_blank_detail_is_refused_where_one_is_needed_and_dropped_where_none_is(self) -> None:
        for args in ((vps_marks.GAP, "g1", "  "), (vps_marks.VERSION, "t1", "")):
            with self.subTest(args), self.assertRaises(service_errors.RefusedError):
                vps_marks.mark(vps_marks.IGNORED, *args)
        vps_marks.mark(vps_marks.IGNORED, vps_marks.GAME, "g1", "  ")

        self.assertEqual(vps_marks.listed()[0]["detail"], "")

    def test_taking_a_mark_off_names_the_thing_as_putting_it_on_does(self) -> None:
        for args in (("table", "g1", ""), (vps_marks.GAME, " ", ""),
                     (vps_marks.GAME, "g1", "romFiles"), (vps_marks.GAP, "g1", "")):
            with self.subTest(args), self.assertRaises(service_errors.RefusedError):
                vps_marks.clear(*args)

    def test_an_entry_that_is_not_a_mark_is_left_out_of_the_read(self) -> None:
        self.path.write_text(json.dumps({"schema": vps_marks.SCHEMA, "marks": [
            {"state": "ignored", "scope": "game", "id": "g1", "since": "a"},
            {"state": "liked", "scope": "game", "id": "g2"},
            {"state": "ignored", "scope": "game", "id": ""}, "junk"]}), encoding="utf-8")

        self.assertEqual([one["id"] for one in vps_marks.listed()], ["g1"])

    def test_an_ignored_game_leaves_off_its_releases_and_its_gaps(self) -> None:
        found = marks(("ignored", "game", "g1", ""))

        self.assertTrue(found.release_ignored("t1", "g1"))
        self.assertTrue(found.gap_ignored("g1", "romFiles"))
        self.assertFalse(found.release_ignored("t2", "g2"))


class MetTests(InAFolder):
    def test_a_wanted_release_the_library_holds_leaves_wanted(self) -> None:
        vps_marks.mark(vps_marks.WANTED, vps_marks.RELEASE, "t1")
        vps_marks.mark(vps_marks.WANTED, vps_marks.RELEASE, "t2")

        self.assertEqual(vps_marks.met({"t1"}), ["t1"])

        self.assertEqual([one["id"] for one in vps_marks.listed()], ["t2"])

    def test_an_ignored_release_and_a_wanted_game_stay(self) -> None:
        vps_marks.mark(vps_marks.IGNORED, vps_marks.RELEASE, "t1")
        vps_marks.mark(vps_marks.WANTED, vps_marks.GAME, "t1")

        self.assertEqual(vps_marks.met({"t1"}), [])

        self.assertEqual(len(vps_marks.listed()), 2)

    def test_a_recount_takes_the_wanted_off_what_it_found_bound(self) -> None:
        vps_marks.mark(vps_marks.WANTED, vps_marks.RELEASE, "t1")
        vps_marks.mark(vps_marks.WANTED, vps_marks.RELEASE, "t2")
        vps_marks.mark(vps_marks.WANTED, vps_marks.GAME, "g1")
        counted = {"entries": {"g1": {"tables": 1, "held": ["tableFiles"], "bound": ["t1"]}}}

        with patch.object(library_vps_state, "compute", return_value=counted), \
                patch.object(library_vps_state, "store"):
            library_vps_state.recompute({}, lambda game, gid: {})

        self.assertEqual(sorted((one["scope"], one["id"]) for one in vps_marks.listed()),
                         [("game", "g1"), ("release", "t2")])

    def test_a_store_that_cannot_be_written_does_not_fail_the_recount(self) -> None:
        counted = {"entries": {"g1": {"tables": 1, "held": [], "bound": ["t1"]}}}

        with patch.object(library_vps_state, "compute", return_value=counted), \
                patch.object(library_vps_state, "store"), \
                patch.object(vps_marks, "met", side_effect=OSError("read-only")):
            self.assertIs(library_vps_state.recompute({}, lambda game, gid: {}), counted)


class ListedTests(unittest.TestCase):
    def _listed(self, *found: tuple[str, str, str, str]) -> list[dict[str, Any]]:
        return vps_marked.listed(FOUND, marks(*found))

    def test_every_scope_names_its_game(self) -> None:
        listed = self._listed(("wanted", "game", "g1", ""),
                              ("ignored", "release", "b1", ""),
                              ("ignored", "gap", "g2", "romFiles"),
                              ("ignored", "version", "t1", "1.2"),
                              ("ignored", "updates", "t2", ""))

        self.assertEqual([(one["scope"], one["game"]) for one in listed],
                         [("game", "Alien"), ("release", "Alien"), ("gap", "Batman"),
                          ("version", "Alien"), ("updates", "Batman")])

    def test_a_release_says_its_kind_version_and_who_made_it(self) -> None:
        one = self._listed(("ignored", "release", "b1", ""))[0]

        self.assertEqual((one["listing"], one["authors"], one["vps_id"]),
                         ("b2sFiles", ["Beta"], "g1"))
        self.assertEqual(self._listed(("ignored", "release", "t1", ""))[0]["version"], "1.2")

    def test_a_gap_carries_the_kind_it_is_of_and_a_skipped_version_the_one_skipped(self) -> None:
        gap = self._listed(("ignored", "gap", "g1", "romFiles"))[0]
        skipped = self._listed(("ignored", "version", "t1", "9.9"))[0]

        self.assertEqual(gap["listing"], "romFiles")
        self.assertEqual(skipped["version"], "9.9")

    def test_a_picture_a_source_replaced_is_found_by_its_entry(self) -> None:
        one = self._listed(("ignored", "updates", "g2:backglass", ""))[0]

        self.assertEqual((one["vps_id"], one["game"], one["media"]), ("g2", "Batman", "backglass"))

    def test_a_mark_the_catalog_no_longer_lists_is_left_out(self) -> None:
        self.assertEqual(self._listed(("ignored", "game", "gone", ""),
                                      ("ignored", "release", "gone", ""),
                                      ("ignored", "updates", "gone:backglass", "")), [])

    def test_a_state_keeps_only_the_marks_in_it(self) -> None:
        listed = vps_marked.listed(FOUND, marks(("wanted", "game", "g1", ""),
                                                ("ignored", "game", "g2", "")),
                                   vps_marks.IGNORED)

        self.assertEqual([one["id"] for one in listed], ["g2"])

    def test_a_release_in_another_state_does_not_walk_the_catalog(self) -> None:
        held = marks(("wanted", "release", "t1", ""))

        with patch.object(vps_marked, "_releases_by_id") as walked:
            vps_marked.listed(FOUND, held, vps_marks.IGNORED)

        walked.assert_not_called()

    def test_the_walk_stops_once_every_release_asked_for_is_found(self) -> None:
        class Untouchable(dict):
            def get(self, *_args: Any) -> Any:
                raise AssertionError("walked past the last release asked for")

        found = {"rows": [FOUND["rows"][0], Untouchable(vps_id="x", name="X")]}

        walked = vps_marked._releases_by_id(found, {"t1", "b1"})

        self.assertEqual(sorted(walked), ["b1", "r1", "t1"])

    def test_the_newest_mark_comes_first(self) -> None:
        older = {"state": "ignored", "scope": "game", "id": "g1", "detail": "",
                 "since": "2026-10-01T00:00:00Z"}
        newer = {**older, "id": "g2", "since": "2026-10-05T00:00:00Z"}

        listed = vps_marked.listed(FOUND, vps_marks.Marks([older, newer]))

        self.assertEqual([one["id"] for one in listed], ["g2", "g1"])


class HomeTests(unittest.TestCase):
    def _home(self, found: vps_marks.Marks) -> dict[str, Any]:
        return vps_home.home(FOUND, KEPT, 7, TODAY, marks=found)

    def test_nothing_ignored_leaves_home_as_it_was(self) -> None:
        home = self._home(marks())

        self.assertEqual(_ids(home["new"]["all"]["releases"]), ["t2", "t1", "b1", "r1"])
        self.assertEqual(home["wanted"], {"count": 0, "items": []})

    def test_an_ignored_game_leaves_the_new_shelf_the_counts_and_the_gaps(self) -> None:
        home = self._home(marks(("ignored", "game", "g1", "")))

        self.assertEqual(_ids(home["new"]["all"]["releases"]), ["t2"])
        self.assertEqual(home["new"]["all"]["count"], 1)
        self.assertEqual([one["vps_id"] for one in home["missing"]["romFiles"]["games"]],
                         ["g2"])
        self.assertEqual(home["missing"]["romFiles"]["count"], 1)

    def test_an_ignored_release_leaves_only_itself(self) -> None:
        home = self._home(marks(("ignored", "release", "t1", "")))

        self.assertEqual(_ids(home["new"]["all"]["releases"]), ["t2", "b1", "r1"])

    def test_an_ignored_gap_leaves_the_game_off_that_kind_only(self) -> None:
        home = self._home(marks(("ignored", "gap", "g2", "romFiles")))

        self.assertEqual([one["vps_id"] for one in home["missing"]["romFiles"]["games"]],
                         ["g1"])
        self.assertEqual(_ids(home["new"]["all"]["releases"]), ["t2", "t1", "b1", "r1"])

    def test_a_gap_is_ignored_by_the_tab_and_the_tab_counts_what_is_left(self) -> None:
        holders = {"g1": ["gid1"], "g2": ["gid2"]}
        media = {"backglass": [offered("gid1", "g1", "Alien", 2),
                               offered("gid2", "g2", "Batman", 1)]}

        def tab(*found: tuple[str, str, str, str]) -> dict[str, Any]:
            return vps_home.home(FOUND, KEPT, 7, TODAY, media=media, holders=holders,
                                 marks=marks(*found))["missing"]["backglass"]

        self.assertEqual((tab()["count"], tab()["on_vps"], tab()["game_ids"]),
                         (2, 0, ["gid1", "gid2"]))
        left = tab(("ignored", "gap", "g1", "backglass"))
        self.assertEqual((left["count"], left["on_vps"], left["game_ids"]), (1, 0, ["gid2"]))
        self.assertEqual([one["vps_id"] for one in left["games"]], ["g2"])
        self.assertEqual(tab(("ignored", "gap", "g1", "b2sFiles"))["count"], 2,
                         "the backglass file is not the key of the backglass pictures tab")

    def test_a_tab_only_a_source_fills_is_ignored_by_its_media_kind(self) -> None:
        media = {"playfield": [offered("gid1", "g1", "Alien", 1)]}

        shown = vps_home.home(FOUND, KEPT, 7, TODAY, media=media, marks=marks())["missing"]
        left = vps_home.home(FOUND, KEPT, 7, TODAY, media=media,
                             marks=marks(("ignored", "gap", "g1", "playfield")))["missing"]

        self.assertEqual(shown["playfield"]["count"], 1)
        self.assertNotIn("playfield", left)

    def test_a_wanted_release_of_an_ignored_game_stays_on_the_wanted_shelf(self) -> None:
        home = self._home(marks(("ignored", "game", "g1", ""), ("wanted", "release", "b1", "")))

        self.assertEqual([(one["id"], one["in_library"]) for one in home["wanted"]["items"]],
                         [("b1", True)])
        self.assertNotIn("b1", _ids(home["new"]["all"]["releases"]))

    def test_a_wanted_game_is_not_ignored_and_stays_on_the_shelves(self) -> None:
        home = self._home(marks(("wanted", "game", "g1", "")))

        self.assertEqual(_ids(home["new"]["all"]["releases"]), ["t2", "t1", "b1", "r1"])


class WantedTests(unittest.TestCase):
    def test_a_wanted_game_and_a_wanted_release_are_listed_with_what_they_are(self) -> None:
        shelf = vps_marked.wanted(FOUND, marks(("wanted", "game", "g2", ""),
                                               ("wanted", "release", "b1", "")))

        self.assertEqual([(one["scope"], one["id"], one["vps_id"], one["game"]) for one in shelf],
                         [("game", "g2", "g2", "Batman"), ("release", "b1", "g1", "Alien")])
        self.assertEqual((shelf[1]["listing"], shelf[1]["authors"]), ("b2sFiles", ["Beta"]))

    def test_what_the_catalog_no_longer_lists_is_not_drawn(self) -> None:
        shelf = vps_marked.wanted(FOUND, marks(("wanted", "game", "gone", ""),
                                               ("wanted", "release", "gone", "")))

        self.assertEqual(shelf, [])

    def test_an_ignored_thing_is_not_wanted(self) -> None:
        shelf = vps_marked.wanted(FOUND, marks(("ignored", "game", "g1", "")))

        self.assertEqual(shelf, [])

    def test_home_counts_all_of_them_and_shows_a_row(self) -> None:
        home = vps_home.home(FOUND, KEPT, 7, TODAY, marks=marks(("wanted", "game", "g1", ""),
                                                                 ("wanted", "game", "g2", "")))

        self.assertEqual(home["wanted"]["count"], 2)
        self.assertEqual({one["game"] for one in home["wanted"]["items"]}, {"Alien", "Batman"})


class RouteTests(InAFolder):
    def setUp(self) -> None:
        super().setUp()
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def _put(self, state: str, scope: str, one: str, detail: str = "") -> Any:
        return self.client.put("/discovery/marks", json={"state": state, "scope": scope,
                                                         "id": one, "detail": detail})

    def test_a_mark_is_put_listed_and_taken_off(self) -> None:
        put = self._put("ignored", "game", "g1")
        self.assertEqual(put.status_code, 200, put.text)
        self.assertEqual(put.json()["replaced"], "")

        listed = self.client.get("/discovery/marks").json()
        self.assertEqual([(one["state"], one["scope"], one["id"]) for one in listed["marks"]],
                         [("ignored", "game", "g1")])

        gone = self.client.delete("/discovery/marks", params={"scope": "game", "id": "g1"})
        self.assertEqual((gone.status_code, gone.json()["replaced"], gone.json()["marks"]),
                         (200, "ignored", []))

    def test_putting_the_other_state_says_what_it_replaced(self) -> None:
        self._put("wanted", "release", "t1")

        self.assertEqual(self._put("ignored", "release", "t1").json()["replaced"], "wanted")

    def test_taking_the_mark_off_an_unmarked_thing_is_not_an_error(self) -> None:
        gone = self.client.delete("/discovery/marks", params={"scope": "game", "id": "g9"})

        self.assertEqual((gone.status_code, gone.json()["replaced"]), (200, ""))

    def test_a_gap_is_put_and_deleted_by_its_kind(self) -> None:
        self._put("ignored", "gap", "g1", "romFiles")

        gone = self.client.delete("/discovery/marks",
                                  params={"scope": "gap", "id": "g1", "detail": "romFiles"})

        self.assertEqual(gone.json()["replaced"], "ignored")

    def test_taking_off_a_mark_that_cannot_exist_is_refused_like_putting_it_on(self) -> None:
        for params in ({"scope": "table", "id": "g1"}, {"scope": "game", "id": " "},
                       {"scope": "game", "id": "g1", "detail": "romFiles"},
                       {"scope": "gap", "id": "g1"}):
            with self.subTest(params):
                self.assertEqual(self.client.delete("/discovery/marks",
                                                    params=params).status_code, 400)

    def test_a_missing_parameter_is_unprocessable(self) -> None:
        self.assertEqual(self.client.delete("/discovery/marks",
                                            params={"scope": "game"}).status_code, 422)
        self.assertEqual(self.client.delete("/discovery/marks",
                                            params={"id": "g1"}).status_code, 422)
        self.assertEqual(self.client.put("/discovery/marks",
                                         json={"state": "ignored"}).status_code, 422)

    def test_a_blank_detail_on_a_gap_is_refused(self) -> None:
        self.assertEqual(self._put("ignored", "gap", "g1", "  ").status_code, 400)

    def test_a_mark_that_cannot_be_is_refused(self) -> None:
        self.assertEqual(self._put("wanted", "gap", "g1", "romFiles").status_code, 400)
        self.assertEqual(self._put("ignored", "gap", "g1").status_code, 400)
        self.assertEqual(self._put("liked", "game", "g1").status_code, 400)


if __name__ == "__main__":
    unittest.main()
