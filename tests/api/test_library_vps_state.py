"""The library-wide rollup: every kind counted in games, not files.

The reason it exists is a negative result: a consumer can only conclude "you own zero
toppers, stop showing me toppers" if it can see the whole library. Four kinds in a real
library had zero installed anywhere.

Counted on a job rather than served live, because resolving media for every game was
measured at 650ms over 149 folders - CPU, not disk, so a warm run does not help.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from starlette.testclient import TestClient

import httpapi
from common.games import library_vps_state as rollup
from common.runtime import jobs as job_registry


class _Game:
    """Only what the count reads off a game besides its state: how many tables it has."""

    def __init__(self, tables: int = 1, gone: int = 0) -> None:
        self.meta_config = {"tables": {
            f"tbl{one:07d}": {"id": f"tbl{one:07d}",
                              **({"absent_since": "2026-10-01T00:00:00Z"} if one < gone else {})}
            for one in range(tables)}}


def _state(matched=True, vps_id="E1", **held):
    """One game's answer, in the shape `state_of` produces."""
    return {
        "matched": matched,
        "vps_id": vps_id if matched else "",
        "kinds": [{"kind": kind, "held": h, "identified": i, "listed": ll,
                   "obtainable": o}
                  for kind, (h, i, ll, o) in held.items()],
    }


class RollupCountTests(unittest.TestCase):
    def setUp(self) -> None:
        patcher = patch.object(rollup, "store")
        self.stored = patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_kind_no_game_holds_counts_zero_across_the_library(self) -> None:
        """The result the rollup exists for. Nothing per-game can say it."""
        answers = [_state(topperFiles=(False, False, 1, 1)),
                   _state(topperFiles=(False, False, 1, 0))]
        counted = rollup.compute({"g1": 1, "g2": 2}, lambda game, gid: answers.pop(0))

        tally = {item["kind"]: item for item in counted["kinds"]}["topperFiles"]
        self.assertEqual(tally["holding"], 0)
        self.assertEqual(tally["listed"], 2)

    def test_games_are_counted_not_files(self) -> None:
        """A game holding four backglasses is one game holding backglasses. Counting
        files would let one hoarder read as a library-wide answer."""
        answers = [_state(b2sFiles=(True, True, 3, 3)),
                   _state(b2sFiles=(True, False, 1, 1))]
        counted = rollup.compute({"g1": 1, "g2": 2}, lambda game, gid: answers.pop(0))

        tally = {item["kind"]: item for item in counted["kinds"]}["b2sFiles"]
        self.assertEqual(tally["holding"], 2)
        self.assertEqual(tally["identified"], 1)

    def test_a_game_that_cannot_be_read_still_counts_in_the_total(self) -> None:
        """Dropping it would shrink the denominator every ratio is read against, and
        the rollup would quietly report a healthier library than there is."""
        def answer(game, game_id):
            if game == 2:
                raise OSError("unreadable")
            return _state(b2sFiles=(True, False, 1, 1))

        counted = rollup.compute({"g1": 1, "g2": 2, "g3": 3}, answer)

        self.assertEqual(counted["games"], 3)
        self.assertEqual(counted["matched"], 2)

    def test_every_kind_is_present_even_at_zero(self) -> None:
        """So a consumer's columns do not appear and vanish with the library."""
        counted = rollup.compute({"g1": 1}, lambda game, gid: _state())

        self.assertEqual(len(counted["kinds"]), 9)
        self.assertTrue(all("holding" in item for item in counted["kinds"]))


class _Named:
    """A game that names a catalog entry, for the follow-up of one game."""

    def __init__(self, vps_id: str = "", tables: int = 1) -> None:
        self.meta_config = {"Info": {"VPSId": vps_id}, "tables": {
            f"tbl{one:07d}": {"id": f"tbl{one:07d}"} for one in range(tables)}}


class FollowTests(unittest.TestCase):
    """One game re-read: the entries it left and joined are rebuilt from every game on them."""

    def setUp(self) -> None:
        self.games: dict = {}
        self.answers: dict = {}
        self.written: list = []
        self.counted: dict = {"schema": rollup.SCHEMA, "computed": "then", "entries": {}}
        for patcher in (
                patch.object(rollup, "stored", side_effect=lambda: dict(self.counted)),
                patch.object(rollup, "store", side_effect=self.written.append),
                patch.object(rollup, "clear_met_wants"),
                patch.object(rollup.game_repository, "catalog",
                             side_effect=lambda: self.games)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _follow(self, game_id: str) -> bool:
        return rollup.follow(self.games[game_id], game_id,
                             lambda game, gid: self.answers[gid])

    def test_a_table_tied_to_a_newer_release_is_the_one_the_entry_holds_at_once(self) -> None:
        self.games = {"g1": _Named("E1")}
        self.counted["entries"] = {"E1": {"tables": 1, "held": [], "bound": ["v1"],
                                          "games": ["g1"]}}
        self.answers = {"g1": {**_state(), "tables": [{"name": "a.vpx", "vps_file_id": "v2"}]}}

        self.assertTrue(self._follow("g1"))

        [written] = self.written
        self.assertEqual(written["entries"]["E1"]["bound"], ["v2"])
        self.assertEqual(written["computed"], "then")

    def test_a_game_matched_to_another_entry_leaves_the_first_and_joins_the_second(self) -> None:
        self.games = {"g1": _Named("E2")}
        self.counted["entries"] = {"E1": {"tables": 1, "held": [], "bound": [],
                                          "games": ["g1"]}}
        self.answers = {"g1": _state(vps_id="E2")}

        self._follow("g1")

        self.assertEqual(sorted(self.written[0]["entries"]), ["E2"])

    def test_an_entry_another_game_names_keeps_what_that_game_holds(self) -> None:
        self.games = {"g1": _Named("E2", 0), "g2": _Named("E1", 1)}
        self.counted["entries"] = {"E1": {"tables": 1, "held": ["b2sFiles"], "bound": [],
                                          "games": ["g1", "g2"]}}
        self.answers = {"g1": _state(vps_id="E2"),
                        "g2": _state(b2sFiles=(True, False, 1, 1))}

        self._follow("g1")

        entries = self.written[0]["entries"]
        self.assertEqual(entries["E1"]["games"], ["g2"])
        self.assertEqual(entries["E1"]["held"], ["b2sFiles"])

    def test_a_game_matched_to_nothing_leaves_the_library_count_of_its_entry(self) -> None:
        self.games = {"g1": _Named("")}
        self.counted["entries"] = {"E1": {"tables": 1, "held": [], "bound": [],
                                          "games": ["g1"]}}
        self.answers = {"g1": _state(matched=False)}

        self._follow("g1")

        self.assertEqual(self.written[0]["entries"], {})

    def test_nothing_is_written_when_the_entry_already_reads_so(self) -> None:
        self.games = {"g1": _Named("E1")}
        self.counted["entries"] = {"E1": {"tables": 1, "held": [], "bound": [],
                                          "games": ["g1"]}}
        self.answers = {"g1": _state()}

        self.assertFalse(self._follow("g1"))
        self.assertEqual(self.written, [])

    def test_a_library_never_counted_stays_so(self) -> None:
        self.counted = {}
        self.games = {"g1": _Named("E1")}
        self.answers = {"g1": _state()}

        self.assertFalse(self._follow("g1"))
        self.assertEqual(self.written, [])

    def test_a_game_is_counted_as_it_stands_when_the_count_holds_it_under_its_entry(self) -> None:
        self.games = {"g1": _Named("E1")}
        self.counted["entries"] = {"E1": {"tables": 1, "held": [], "bound": [],
                                          "games": ["g1"]}}

        self.assertTrue(rollup.counted_as(self.games["g1"], "g1"))

    def test_a_game_is_not_counted_as_it_stands_under_another_entry_or_none(self) -> None:
        self.counted["entries"] = {"E1": {"tables": 1, "held": [], "bound": [],
                                          "games": ["g1"]},
                                   "E2": {"tables": 0, "held": [], "bound": [], "games": []}}

        self.assertFalse(rollup.counted_as(_Named("E2"), "g1"))
        self.assertFalse(rollup.counted_as(_Named(""), "g1"))

    def test_a_game_tied_to_a_release_the_count_lacks_is_not_counted_as_it_stands(self) -> None:
        self.counted["entries"] = {"E1": {"tables": 1, "held": [], "bound": ["v1"],
                                          "games": ["g1"]}}
        game = _Named("E1")
        game.meta_config["tables"]["tbl0000000"]["source"] = {"vps_file_id": "v2"}

        self.assertFalse(rollup.counted_as(game, "g1"))

    def test_a_library_never_counted_has_nothing_to_be_out_of_step_with(self) -> None:
        self.counted = {}

        self.assertTrue(rollup.counted_as(_Named("E1"), "g1"))

    def test_ties_differ_for_a_match_and_for_a_table_tied_again(self) -> None:
        one = _Named("E1")
        before = rollup.ties_of(one)
        one.meta_config["tables"]["tbl0000000"]["source"] = {"vps_file_id": "v2"}

        self.assertNotEqual(before, rollup.ties_of(one))
        self.assertNotEqual(before, rollup.ties_of(_Named("E2")))
        self.assertEqual(before, rollup.ties_of(_Named("E1")))


class RollupEntriesTests(unittest.TestCase):
    """What the library holds, per catalog entry: the part a list of the catalog reads."""

    def _entries(self, games: dict, answers: list) -> dict:
        with patch.object(rollup, "store"):
            return rollup.compute(games, lambda game, gid: answers.pop(0))["entries"]

    def test_games_naming_one_entry_hold_what_any_of_them_holds(self) -> None:
        entries = self._entries(
            {"g1": _Game(1), "g2": _Game(0)},
            [_state(b2sFiles=(True, False, 1, 1), romFiles=(False, False, 1, 1)),
             _state(romFiles=(True, False, 1, 1))])

        self.assertEqual(entries, {"E1": {"tables": 1, "held": ["b2sFiles", "romFiles"],
                                          "bound": [],
                                          "games": ["g1", "g2"]}})

    def test_a_game_with_no_table_is_still_in_the_library(self) -> None:
        entries = self._entries({"g1": _Game(0)}, [_state()])

        self.assertEqual(entries, {"E1": {"tables": 0, "held": [], "bound": [],
                                          "games": ["g1"]}})

    def test_a_table_whose_file_is_gone_is_not_a_table_held(self) -> None:
        """The record stays after the file goes, so counting records would tell a client
        the table is here and never offer it as missing."""
        entries = self._entries({"g1": _Game(2, gone=2)}, [_state()])

        self.assertEqual(entries["E1"]["tables"], 0)

    def test_a_game_matched_to_nothing_belongs_to_no_entry(self) -> None:
        entries = self._entries({"g1": _Game(1)}, [_state(matched=False)])

        self.assertEqual(entries, {})

    def test_a_game_that_cannot_be_read_belongs_to_no_entry(self) -> None:
        def answer(game, game_id):
            raise OSError("unreadable")

        with patch.object(rollup, "store"):
            counted = rollup.compute({"g1": _Game(1)}, answer)

        self.assertEqual(counted["entries"], {})


CATALOG = [
    {"id": "E2", "name": "Zed", "manufacturer": "Bally", "year": 1990, "type": "SS",
     "theme": ["Sci-Fi"], "tableFiles": [{"id": "t"}], "b2sFiles": [{"id": "b"}]},
    {"id": "E1", "name": "Alpha", "manufacturer": "Williams", "year": 1995, "type": "SS",
     "tableFiles": [{"id": "t"}], "b2sFiles": [{"id": "b"}], "romFiles": [{"id": "r"}],
     "wheelArtFiles": []},
]


class DiscoveryTests(unittest.TestCase):
    def _discovery(self, counted: dict) -> dict:
        with patch.object(rollup, "stored", return_value=counted), \
                patch.object(rollup, "load_vpsdb", return_value=CATALOG):
            return rollup.discovery()

    def test_a_library_never_counted_is_not_a_library_that_owns_nothing(self) -> None:
        body = self._discovery({})

        self.assertEqual(body["counted"], "")
        self.assertEqual([row["in_library"] for row in body["rows"]], [None, None])
        self.assertEqual([row["held"] for row in body["rows"]], [[], []])

    def test_rows_are_the_whole_catalog_by_name(self) -> None:
        body = self._discovery({"computed": "2026-10-11T00:00:00Z", "entries": {}})

        self.assertEqual([row["vps_id"] for row in body["rows"]], ["E1", "E2"])
        self.assertEqual(body["counted"], "2026-10-11T00:00:00Z")

    def test_listed_names_only_the_kinds_vps_lists(self) -> None:
        """So a consumer says Missing for a kind VPS lists and nothing for one it does not,
        including the kind whose list is present and empty."""
        rows = {row["vps_id"]: row for row in self._discovery({"computed": "x"})["rows"]}

        self.assertEqual(rows["E1"]["listed"], ["tableFiles", "b2sFiles", "romFiles"])
        self.assertEqual(rows["E2"]["listed"], ["tableFiles", "b2sFiles"])

    def test_a_counted_library_says_which_entries_it_has_and_what_of_them(self) -> None:
        counted = {"computed": "x", "entries": {"E1": {"tables": 2, "held": ["b2sFiles"]}}}
        rows = {row["vps_id"]: row for row in self._discovery(counted)["rows"]}

        self.assertTrue(rows["E1"]["in_library"])
        self.assertEqual(rows["E1"]["held"], ["tableFiles", "b2sFiles"])
        self.assertFalse(rows["E2"]["in_library"])
        self.assertEqual(rows["E2"]["held"], [])

    def test_a_game_with_no_table_holds_no_table(self) -> None:
        counted = {"computed": "x", "entries": {"E1": {"tables": 0, "held": [], "bound": []}}}
        row = self._discovery(counted)["rows"][0]

        self.assertTrue(row["in_library"])
        self.assertEqual(row["held"], [])

    def test_the_catalog_fields_are_carried(self) -> None:
        row = {r["vps_id"]: r for r in self._discovery({"computed": "x"})["rows"]}["E2"]

        self.assertEqual((row["name"], row["manufacturer"], row["year"], row["themes"]),
                         ("Zed", "Bally", 1990, ["Sci-Fi"]))
        self.assertIn("E2", row["url"])


class RollupStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        patcher = patch.object(rollup, "ROLLUP_PATH",
                               Path(self.dir.name) / "vps-rollup.json")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_never_counted_reads_as_empty_not_as_zero(self) -> None:
        self.assertEqual(rollup.stored(), {})

    def test_what_was_written_comes_back(self) -> None:
        rollup.store({"computed": "2026-09-02T00:00:00Z", "games": 3, "kinds": []})

        self.assertEqual(rollup.stored()["games"], 3)

    def test_a_rollup_from_before_entries_were_kept_reads_as_never_counted(self) -> None:
        """The first schema held totals only; reading it would show every entry as not
        owned."""
        rollup.ROLLUP_PATH.write_text(json.dumps({"schema": 1, "games": 7, "kinds": []}))

        self.assertEqual(rollup.stored(), {})

    def test_a_rollup_from_another_schema_is_ignored(self) -> None:
        """Rather than half-read: the shape is what the counts mean."""
        rollup.ROLLUP_PATH.write_text(json.dumps({"schema": 99, "games": 7}))

        self.assertEqual(rollup.stored(), {})


class RollupRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        job_registry.reset_for_tests()
        patcher = patch.object(rollup, "stored", return_value={})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(httpapi.create_api_app(),
                                 raise_server_exceptions=False)

    def test_a_library_never_counted_says_so_rather_than_404(self) -> None:
        body = self.client.get("/library/vps_state").json()

        self.assertEqual(body["computed"], "")
        self.assertEqual(body["kinds"], [])

    def test_discovery_of_a_library_never_counted_says_so(self) -> None:
        with patch.object(rollup, "load_vpsdb", return_value=CATALOG):
            body = self.client.get("/library/discovery").json()

        self.assertEqual(body["counted"], "")
        self.assertEqual(len(body["rows"]), 2)
        self.assertIsNone(body["rows"][0]["in_library"])

    def test_a_counted_discovery_passes_the_model(self) -> None:
        counted = {"computed": "x", "entries": {"E1": {"tables": 1, "held": ["b2sFiles"]}}}
        with patch.object(rollup, "stored", return_value=counted), \
                patch.object(rollup, "load_vpsdb", return_value=CATALOG):
            body = self.client.get("/library/discovery").json()

        row = {one["vps_id"]: one for one in body["rows"]}["E1"]
        self.assertEqual((row["in_library"], row["year"], row["held"]),
                         (True, 1995, ["tableFiles", "b2sFiles"]))

    def test_a_row_carries_the_picture_vps_leads_with(self) -> None:
        pictured = [{**CATALOG[0], "imgUrl": "https://vps.example/z.webp"}, CATALOG[1]]
        with patch.object(rollup, "load_vpsdb", return_value=pictured):
            body = self.client.get("/library/discovery").json()

        by_id = {one["vps_id"]: one for one in body["rows"]}
        self.assertEqual((by_id["E2"]["img_url"], by_id["E1"]["img_url"]),
                         ("https://vps.example/z.webp", ""))

    def test_counting_is_accepted_not_done(self) -> None:
        with patch("common.games.game_repository.catalog", return_value={}):
            response = self.client.post("/library/vps_state")

        self.assertEqual(response.status_code, 202, response.text)
        self.assertTrue(response.headers["Location"].startswith("/api/v1/jobs/"))

    def test_it_takes_its_own_job_kind(self) -> None:
        """Not the scan's. This only reads, so queueing it behind a write would make
        a cheap question wait on an expensive one for no reason."""
        self.assertNotEqual(job_registry.KIND_VPS_ROLLUP,
                            job_registry.KIND_LIBRARY_SCAN)


if __name__ == "__main__":
    unittest.main()
