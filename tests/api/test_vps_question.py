"""Asking Discovery's games about their releases, and finding a name from a few letters."""

from __future__ import annotations

import datetime
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from common.online import vps_find, vps_question

PEOPLE = [
    {"name": "Guet35", "aliases": ["guet35"], "releases": 4},
    {"name": "VPW", "aliases": ["VPinWorkshop", "VPW Team"], "releases": 9},
    {"name": "Wildman", "aliases": [], "releases": 30},
]


def release(id_: str, by: list[int], *, mod: bool = False, features: tuple = (),
            format_: str = "VPX", added: str = "", held: bool = False) -> dict:
    return {"id": id_, "by": by, "mod": mod, "features": list(features),
            "format": format_, "added": added, "held": held}


ROWS: list[dict[str, Any]] = [
    {"id": "A", "name": "Alpha", "manufacturer": "Bally", "themes": ["Space"],
     "is_in_library": True, "listed": ["tableFiles", "b2sFiles"],
     "releases": {"tableFiles": [release("t1", [0, 1], mod=True, added="2026-10-12T00:00:00Z"),
                                 release("t2", [2], held=True)],
                  "b2sFiles": [release("b1", [2], features=("2Screens",), format_="")]}},
    {"id": "B", "name": "Beta", "manufacturer": "Williams", "themes": ["Space", "Sport"],
     "is_in_library": False, "listed": ["tableFiles"],
     "releases": {"tableFiles": [release("t3", [1], format_="FP")]}},
    {"id": "C", "name": "Gamma", "manufacturer": "Bally", "themes": [],
     "is_in_library": False, "listed": [], "releases": {}},
]


RELEASES: dict[str, dict[str, list[dict[str, Any]]]] = {row["id"]: row["releases"] for row in ROWS}


def _today(year: int, month: int, day: int):
    class Day(datetime.date):
        @classmethod
        def today(cls) -> Day:
            return cls(year, month, day)

    return patch.object(vps_question, "datetime",
                        SimpleNamespace(date=Day, timedelta=datetime.timedelta))


def ids_raw(question: dict) -> dict[str, int]:
    return vps_question.games_fitting(question, PEOPLE, ROWS, RELEASES)


def ids(question: dict) -> dict[str, int]:
    return vps_question.games_fitting(vps_question.clean(question), PEOPLE, ROWS, RELEASES)


class QuestionTests(unittest.TestCase):
    def test_an_empty_question_fits_every_game_including_one_with_no_release(self) -> None:
        self.assertEqual(ids({}), {"A": 3, "B": 1, "C": 0})

    def test_clean_drops_what_asks_nothing(self) -> None:
        self.assertEqual(vps_question.clean({"by": [" ", ""], "kind": "", "mod": None,
                                             "since": "7", "x": 1}),
                         {"since": "7"})

    def test_a_window_that_is_not_a_count_of_days_asks_nothing(self) -> None:
        for window in ("", "0", "-3", "2026-10-01", "week", "7.5", "1000000", "9" * 5000):
            self.assertEqual({}, vps_question.clean({"since": window}), window)

    def test_one_release_has_to_meet_every_release_level_key(self) -> None:
        self.assertEqual(ids({"by": ["Wildman"], "mod": "mods"}), {})
        self.assertEqual(ids({"by": ["Guet35"], "mod": "mods"}), {"A": 1})
        self.assertEqual(ids({"by": ["VPW"], "mod": "originals"}), {"B": 1})

    def test_a_credit_in_any_position_counts(self) -> None:
        self.assertEqual(ids({"by": ["VPW"]}), {"A": 1, "B": 1})

    def test_a_name_credited_under_another_spelling_asks_for_the_same_person(self) -> None:
        self.assertEqual(ids({"by": ["vpinworkshop"]}), ids({"by": ["VPW"]}))
        self.assertEqual((vps_question.person_named("VPW Team", PEOPLE) or {})["name"], "VPW")
        self.assertIsNone(vps_question.person_named("Nobody", PEOPLE))

    def test_kind_narrows_to_that_listing_and_to_games_that_list_it(self) -> None:
        self.assertEqual(ids({"kind": "b2sFiles"}), {"A": 1})
        self.assertEqual(ids({"kind": "romFiles"}), {})

    def test_format_feature_since_and_held_are_asked_of_the_release(self) -> None:
        self.assertEqual(ids({"kind": "tableFiles", "format": ["FP"]}), {"B": 1})
        self.assertEqual(ids({"feature": ["2Screens"]}), {"A": 1})
        with _today(2026, 10, 14):
            self.assertEqual(ids({"since": "7"}), {"A": 1})
        self.assertEqual(ids({"kind": "tableFiles", "held": "missing"}), {"A": 1, "B": 1})
        self.assertEqual(ids({"kind": "tableFiles", "held": "have"}), {"A": 1})

    def test_a_release_filed_under_no_format_fits_whatever_format_is_asked(self) -> None:
        self.assertEqual(ids({"kind": "b2sFiles", "format": ["FP"]}), {"A": 1})
        self.assertEqual(ids({"format": ["FP"]}), {"A": 1, "B": 1})

    def test_a_window_is_kept_as_a_plain_count_of_days(self) -> None:
        self.assertEqual({"since": "7"}, vps_question.clean({"since": "0007"}))
        self.assertEqual({"since": "7"}, vps_question.clean({"since": 7}))

    def test_a_window_a_saved_view_holds_unclean_is_ignored_and_not_a_crash(self) -> None:
        for window in ("2026-10-01", "1000000", "9" * 20):
            self.assertEqual({"A": 3, "B": 1, "C": 0}, ids_raw({"since": window}), window)

    def test_a_window_rolls_forward_with_the_date(self) -> None:
        question = {"since": "7"}

        with _today(2026, 10, 14):
            self.assertEqual({"A": 1}, ids(question))
        with _today(2026, 10, 20):
            self.assertEqual({}, ids(question))
        with _today(2026, 10, 19):
            self.assertEqual({"A": 1}, ids(question))

    def test_the_game_level_keys_do_not_need_a_release(self) -> None:
        self.assertEqual(ids({"maker": "Bally"}), {"A": 3, "C": 0})
        self.assertEqual(ids({"theme": "Sport"}), {"B": 1})
        self.assertEqual(ids({"owned": "no"}), {"B": 1, "C": 0})

    def test_the_releases_that_fit_are_handed_back_with_the_games(self) -> None:
        found = vps_question.games_found(vps_question.clean({"by": ["Wildman"]}),
                                         PEOPLE, ROWS, RELEASES)
        self.assertEqual({game: [one["id"] for _listing, one in releases]
                          for game, releases in found.items()}, {"A": ["t2", "b1"]})

    def test_a_name_nobody_has_fits_nothing(self) -> None:
        self.assertEqual(ids({"by": ["Nobody"]}), {})

    def test_the_vocabulary_counts_what_releases_carry(self) -> None:
        self.assertEqual(vps_question.vocabulary(RELEASES),
                         {"feature": ["2Screens"], "format": ["VPX", "FP"]})


class FindTests(unittest.TestCase):
    def test_a_credit_under_another_name_finds_the_person(self) -> None:
        found = vps_find.find("vpinwork", PEOPLE, ROWS)[vps_find.AUTHOR]
        self.assertEqual([(one["name"], one["via"]) for one in found], [("VPW", "VPinWorkshop")])

    def test_a_name_that_starts_with_the_letters_comes_before_one_that_contains_them(self) -> None:
        names = [one["name"] for one in vps_find.find("gam", PEOPLE, ROWS)[vps_find.GAME]]
        self.assertEqual(names, ["Gamma"])
        self.assertEqual([one["name"] for one in vps_find.find("a", PEOPLE, ROWS)[vps_find.GAME]],
                         ["Alpha", "Beta", "Gamma"])

    def test_two_games_of_one_name_are_told_apart_by_what_the_games_grid_says(self) -> None:
        rows = [{"id": "X", "name": "Twin", "said": "Bally 1980"},
                {"id": "Y", "name": "Twin", "said": "Williams 1994"},
                {"id": "Z", "name": "Twin Peaks"}]
        found = vps_find.find("twin", [], rows)[vps_find.GAME]

        self.assertEqual({one["id"]: one["said"] for one in found},
                         {"X": "Bally 1980", "Y": "Williams 1994", "Z": ""})

    def test_makers_and_themes_count_their_games(self) -> None:
        found = vps_find.find("ba", PEOPLE, ROWS)
        self.assertEqual(found[vps_find.MAKER], [{"name": "Bally", "count": 2}])
        self.assertEqual(vps_find.find("spa", PEOPLE, ROWS)[vps_find.THEME],
                         [{"name": "Space", "count": 2}])

    def test_nothing_typed_finds_nothing(self) -> None:
        self.assertEqual(sum(len(one) for one in vps_find.find("  ", PEOPLE, ROWS).values()), 0)


if __name__ == "__main__":
    unittest.main()
