"""The top of Overview: whose plays it reads, what it shows while a table runs, and that a
read which fails leaves the rest.

`Library._read_front` is apart from the page, so this runs without one.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any, cast

from manager.kit.data import Library
from manager.overview import front

OWNER = {"id": "owner", "name": "Owner", "owner": True, "up": False}
GUEST = {"id": "guest", "name": "Guest", "owner": False, "up": True}


def game(game_id: str, last_played: str = "", **user) -> dict:
    return {"id": game_id, "name": game_id.upper(),
            "user": {"last_played": last_played, **user}}


def library(*, roster, games, **client) -> Library:
    base = {"games": lambda: games, "players": lambda: roster,
            "play_state": lambda: {"launching": False}, "jobs": lambda: [],
            "high_scores": lambda game_id, table_id="": {},
            "player_record": lambda player_id: []}
    lib = Library.__new__(Library)
    lib._client = cast(Any, SimpleNamespace(**{**base, **client}))
    lib.games = []
    lib._games_at = 0.0
    lib._front_player = ""
    return lib


class WhoseTheyAre(unittest.TestCase):
    def test_the_owners_plays_are_the_librarys_own_newest_first(self):
        lib = library(roster=[OWNER], games=[game("a", "2026-10-01T10:00:00+00:00"),
                                               game("b", "2026-10-03T10:00:00+00:00"),
                                               game("c")])

        read = lib._read_front()

        self.assertEqual(["b", "a"], read["recent"])

    def test_the_player_who_is_up_is_read_from_their_record(self):
        lib = library(roster=[OWNER, GUEST],
                      games=[game("a", "2026-10-09T10:00:00+00:00"), game("x"), game("y"),
                             game("z")],
                      player_record=lambda player_id: [
                          {"game_id": "x", "last_played": "2026-10-01T10:00:00+00:00"},
                          {"game_id": "y", "last_played": "2026-10-02T10:00:00+00:00"},
                          {"game_id": "z", "last_played": None}])

        read = lib._read_front()

        self.assertEqual("guest", read["player"]["id"])
        self.assertEqual(["y", "x"], read["recent"])

    def test_a_game_the_library_no_longer_has_is_not_a_guests_last_played(self):
        lib = library(roster=[OWNER, GUEST], games=[game("y")],
                      player_record=lambda player_id: [
                          {"game_id": "gone", "last_played": "2026-10-09T10:00:00+00:00"},
                          {"game_id": "y", "last_played": "2026-10-02T10:00:00+00:00"}])

        self.assertEqual(["y"], lib._read_front()["recent"])

    def test_a_game_played_since_the_page_opened_is_read_again(self):
        lib = library(roster=[OWNER], games=[game("a", "2026-10-01T10:00:00+00:00")])

        self.assertEqual(["a"], lib._read_front()["recent"])

    def test_a_chosen_player_wins_over_the_one_who_is_up(self):
        lib = library(roster=[OWNER, GUEST], games=[game("a", "2026-10-09T10:00:00+00:00")])
        lib.pick_front_player("owner")

        read = lib._read_front()

        self.assertEqual(["a"], read["recent"])

    def test_with_nobody_up_it_is_the_owner(self):
        lib = library(roster=[OWNER, {**GUEST, "up": False}], games=[])

        self.assertEqual("owner", lib._read_front()["player"]["id"])


class WhatItKeepsWhenAReadFails(unittest.TestCase):
    def test_an_unreadable_roster_leaves_the_owners_plays(self):
        def refuse():
            raise OSError("down")

        lib = library(roster=[], games=[game("a", "2026-10-01T10:00:00+00:00")],
                      players=refuse)

        self.assertEqual(["a"], lib._read_front()["recent"])

    def test_an_unreadable_score_still_leaves_the_game(self):
        def refuse(game_id, table_id=""):
            raise OSError("down")

        lib = library(roster=[OWNER], games=[game("a", "2026-10-01T10:00:00+00:00")],
                      high_scores=refuse)

        read = lib._read_front()

        self.assertEqual(["a"], read["recent"])
        self.assertIsNone(read["top"])

    def test_the_top_score_is_the_first_entry_of_the_game_being_shown(self):
        asked = []

        def scores(game_id, table_id=""):
            asked.append(game_id)
            return {"sections": [{"name": "Grand Champion", "entries": [
                {"initials": "ABC", "score": 1000, "text": "1,000"}]}]}

        lib = library(roster=[OWNER], games=[game("a", "2026-10-01T10:00:00+00:00"),
                                               game("b", "2026-10-03T10:00:00+00:00")],
                      high_scores=scores)

        read = lib._read_front()

        self.assertEqual(["b"], asked)
        self.assertEqual("ABC", read["top"]["initials"])

    def test_only_jobs_still_running_are_kept(self):
        lib = library(roster=[OWNER], games=[],
                      jobs=lambda: [{"id": "1", "state": "running"},
                                    {"id": "2", "state": "done"}])

        self.assertEqual(["1"], [one["id"] for one in lib._read_front()["jobs"]])


class WhatCountsAsAChange(unittest.TestCase):
    def test_a_job_moving_is_a_change_and_a_still_one_is_not(self):
        before = {"play": {}, "jobs": [{"id": "1", "pct": 10, "message": "a"}]}
        same = {"play": {}, "jobs": [{"id": "1", "pct": 10, "message": "a"}]}
        moved = {"play": {}, "jobs": [{"id": "1", "pct": 40, "message": "a"}]}

        self.assertEqual(front._signature(before), front._signature(same))
        self.assertNotEqual(front._signature(before), front._signature(moved))


if __name__ == "__main__":
    unittest.main()
