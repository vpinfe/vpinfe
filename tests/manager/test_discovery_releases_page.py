"""Discovery's Releases tab: its rows, its columns and their filters, and which tab a page is on."""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import patch

from common.online import vps_question
from manager.kit import grid, views, when
from manager.library import discovery, discovery_releases

PEOPLE = [{"name": "Guet35", "aliases": [], "releases": 2},
          {"name": "VPW", "aliases": [], "releases": 1}]


def _release(release_id: str, **over: Any) -> dict[str, Any]:
    return {"id": release_id, "by": [0], "mod": False, "features": [], "format": "VPX",
            "version": "1.0", "added": "2025-10-13T00:00:00Z", "held": False, **over}


GAMES = {
    "g1": {"id": "g1", "name": "Alpha", "said": "Bally 1990", "manufacturer": "Bally",
           "themes": [], "listed": ["tableFiles", "b2sFiles"], "is_in_library": True,
           "in_library": "yes", "img": "http://x/a.png"},
    "g2": {"id": "g2", "name": "Beta", "said": "Williams 1992", "manufacturer": "Williams",
           "themes": [], "listed": ["tableFiles"], "is_in_library": False, "in_library": "no"},
    "g3": {"id": "g3", "name": "Gamma", "said": "", "manufacturer": "", "themes": [],
           "listed": [], "is_in_library": False},
}
RELEASES = {
    "g1": {"tableFiles": [_release("t1", by=[0, 1], version="2.0", held=True, mod=True,
                                   features=["VR", "MOD"]),
                          _release("t2", version="")],
           "b2sFiles": [_release("b1", format="")]},
    "g2": {"tableFiles": [_release("t3", by=[1])]},
}


def _rows(question: dict[str, Any], *, counted: bool = True) -> list[dict[str, Any]]:
    found = vps_question.games_found(question, PEOPLE, list(GAMES.values()), RELEASES)
    return discovery_releases.rows(found, GAMES, PEOPLE, counted=counted)


class Rows(unittest.TestCase):
    def test_a_release_is_a_row_and_a_game_with_two_is_two_rows(self) -> None:
        rows = _rows({"kind": "tableFiles"})

        self.assertEqual(["g1:t1", "g1:t2", "g2:t3"], [one["id"] for one in rows])

    def test_a_game_with_no_release_has_no_row(self) -> None:
        self.assertNotIn("g3", {one["game_id"] for one in _rows({})})

    def test_the_row_carries_the_games_name_and_line_and_the_releases_own_facts(self) -> None:
        row = _rows({"kind": "tableFiles"})[0]

        self.assertEqual(("Alpha", "Bally 1990", "tableFiles", "2.0", ["Guet35", "VPW"]),
                         (row["name"], row["said"], row["kind"], row["version"],
                          row["authors"]))
        self.assertEqual(("2025-10-13T00:00:00Z", "VPX", ["VR", "MOD"], "mod"),
                         (row["added"], row["format"], row["features"], row["mod"]))

    def test_a_release_carries_whether_its_game_is_in_the_library(self) -> None:
        rows = {one["id"]: one["in_library"] for one in _rows({})}

        self.assertEqual({"g1:t1": "yes", "g1:t2": "yes", "g1:b1": "yes", "g2:t3": "no"}, rows)

    def test_a_release_of_a_game_never_compared_is_not_compared(self) -> None:
        games = {"g1": {k: v for k, v in GAMES["g1"].items() if k != "in_library"}}
        found = {"g1": [("tableFiles", _release("t1"))]}

        row = discovery_releases.rows(found, games, PEOPLE, counted=False)[0]

        self.assertEqual("not_compared", row["in_library"])

    def test_a_release_with_no_version_or_format_reads_blank(self) -> None:
        rows = {one["id"]: one for one in _rows({})}

        self.assertEqual("", rows["g1:t2"]["version"])
        self.assertEqual("", rows["g1:b1"]["format"])
        self.assertEqual("", rows["g1:t2"]["mod"])

    def test_a_held_release_reads_have_once_the_library_has_been_counted(self) -> None:
        held = {one["id"]: one["held"] for one in _rows({"kind": "tableFiles"})}
        before = {one["held"] for one in _rows({"kind": "tableFiles"}, counted=False)}

        self.assertEqual({"g1:t1": "have", "g1:t2": "missing", "g2:t3": "missing"}, held)
        self.assertEqual({"not_compared"}, before)

    def test_the_search_text_carries_the_games_type_and_themes(self) -> None:
        games = {"g1": {**GAMES["g1"], "game_type": "Solid State", "themes": ["Space", "Movie"]}}
        found = {"g1": [("tableFiles", _release("t1"))]}

        row = discovery_releases.rows(found, games, PEOPLE, counted=True)[0]

        self.assertEqual("Solid State Space Movie", row["find"])

    def test_old_releases_added_on_one_date_say_it_in_one_call(self) -> None:
        found = {"g1": [("tableFiles", _release("a", added="2020-03-04T01:00:00Z")),
                        ("tableFiles", _release("b", added="2020-03-04T01:00:09Z"))]}

        with patch.object(when, "ago", wraps=when.ago) as ago:
            said = [one["added_said"] for one
                    in discovery_releases.rows(found, GAMES, PEOPLE, counted=True)]

        self.assertEqual(1, ago.call_count)
        self.assertEqual(said[0], said[1])
        self.assertNotEqual("", said[0])

    def test_a_game_the_rows_do_not_know_is_not_a_row(self) -> None:
        found = {"gone": [("tableFiles", _release("x"))]}

        self.assertEqual([], discovery_releases.rows(found, GAMES, PEOPLE, counted=True))


def _everything() -> views.Preset:
    view = discovery_releases.VIEWS["manager.view.everything"]
    assert isinstance(view, views.Preset)
    return view


KINDS = {"tableFiles": "word.table", "b2sFiles": "asset.kind.backglass.label"}


def _column(field: str, *, release_rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return next(one for one in discovery_releases.columns(KINDS, release_rows or [])
                if one["field"] == field)


def _choices(field: str, **how: Any) -> list[tuple[str, str]]:
    return [(one["value"], one["label"])
            for one in _column(field, **how)["filterParams"]["choices"]]


class Columns(unittest.TestCase):
    def test_every_column_a_view_shows_is_one_the_grid_has(self) -> None:
        fields = {one["field"] for one in discovery_releases.columns(KINDS)}

        self.assertTrue(set(_everything().columns) <= fields)

    def test_the_default_order_is_newest_added_first(self) -> None:
        sort = _everything().sort[0]

        self.assertEqual(("added", "desc"), (sort["colId"], sort["sort"]))

    def test_the_kind_column_filters_by_the_kinds_the_library_collects(self) -> None:
        self.assertEqual(["tableFiles", "b2sFiles"], [value for value, _label in _choices("kind")])

    def test_authors_and_features_are_typed_into_and_hold_a_list(self) -> None:
        for field in ("authors", "features"):
            with self.subTest(field=field):
                column = _column(field)

                self.assertEqual(grid.LIST_FILTER, column[":filter"])
                self.assertNotIn(":cellRenderer", column)
                self.assertIn("join(', ')", column[":valueFormatter"])

    def test_added_filters_on_a_window_of_days_and_the_cell_keeps_the_date(self) -> None:
        column = _column("added")

        self.assertEqual(grid.WINDOW_FILTER, column[":filter"])
        self.assertEqual([("Past Week", 7), ("Past 2 Weeks", 14), ("Past Month", 30)],
                         [(one["label"], one["value"])
                          for one in column["filterParams"]["windows"]])
        self.assertEqual("Any time", column["filterParams"]["words"]["any"])

    def test_mod_filters_on_mod_or_original(self) -> None:
        self.assertEqual([("mod", "Mod"), ("", "Original")], _choices("mod"))

    def test_in_library_filters_on_yes_no_and_not_compared_yet(self) -> None:
        self.assertEqual([("yes", "Yes"), ("no", "No"), ("not_compared", "Not Compared Yet")],
                         _choices("in_library"))

    def test_have_filters_on_have_missing_and_not_compared_yet(self) -> None:
        self.assertEqual([("have", "Have"), ("missing", "Missing"),
                          ("not_compared", "Not Compared Yet")], _choices("held"))

    def test_format_offers_what_the_releases_carry_most_used_first_and_none_named(self) -> None:
        release_rows = [{"format": "VPX"}, {"format": "FP"}, {"format": "VPX"}, {"format": ""}]

        self.assertEqual([("VPX", "VPX"), ("FP", "FP"), ("", "Not recorded")],
                         _choices("format", release_rows=release_rows))
        self.assertEqual([], _choices("format", release_rows=[]))

    def test_every_filtered_column_is_one_a_view_can_show(self) -> None:
        filtered = {one["field"] for one in discovery_releases.columns(KINDS)
                    if ":filter" in one or "filterParams" in one}

        self.assertEqual({"kind", "authors", "added", "held", "format", "features", "mod",
                          "in_library", "mark"}, filtered)
        self.assertTrue(filtered <= set(_everything().columns))


class ArrivalFilters(unittest.TestCase):
    def test_an_authors_page_becomes_filters_on_the_columns(self) -> None:
        question = {"by": ["Wildman"], "kind": "b2sFiles", "owned": "yes", "held": "missing",
                    "mod": "originals", "format": ["VPX"]}

        self.assertEqual(
            {"authors": {"values": ["Wildman"]}, "kind": {"values": ["b2sFiles"]},
             "in_library": {"values": ["yes"]}, "held": {"values": ["missing"]},
             "mod": {"values": [""]}, "format": {"values": ["VPX"]}},
            discovery_releases.arrival_filters(question, counted=True))

    def test_a_release_filed_under_no_format_stays_where_a_format_is_asked_for(self) -> None:
        filters = discovery_releases.arrival_filters(
            {"by": ["Wildman"], "format": ["VPX"]}, counted=True, filed={"VPX", "FP", ""})

        self.assertEqual(["VPX", ""], filters["format"]["values"])

    def test_the_spelling_a_release_is_filed_under_is_the_one_asked_for(self) -> None:
        filters = discovery_releases.arrival_filters(
            {"format": ["VPX"]}, counted=True, filed={"vpx", "VPX", "FPX"})

        self.assertEqual(["VPX", "vpx"], filters["format"]["values"])

    def test_have_and_in_library_wait_for_a_count(self) -> None:
        question = {"by": ["Wildman"], "owned": "yes", "held": "missing"}

        self.assertEqual({"authors": {"values": ["Wildman"]}},
                         discovery_releases.arrival_filters(question, counted=False))

    def test_a_question_that_asks_nothing_filters_nothing(self) -> None:
        self.assertEqual({}, discovery_releases.arrival_filters({}, counted=True))

    def test_every_filter_it_sets_is_on_a_column_the_everything_view_shows(self) -> None:
        question = {"by": ["A"], "kind": "k", "owned": "yes", "held": "have", "mod": "mods",
                    "format": ["VPX"]}

        filters = discovery_releases.arrival_filters(question, counted=True)

        self.assertTrue(set(filters) <= set(_everything().columns))


class Tab(unittest.TestCase):
    def test_a_page_opens_on_the_tab_chosen(self) -> None:
        self.assertEqual(discovery.BROWSE, discovery.tab_of({"discovery_tab": discovery.BROWSE}))
        self.assertEqual(discovery.RELEASES,
                         discovery.tab_of({"discovery_tab": discovery.RELEASES}))

    def test_a_subject_page_is_a_games_page_whatever_tab_was_chosen(self) -> None:
        state = {"discovery_tab": discovery.RELEASES,
                 "subject": {"type": "author", "name": "Guet35"}}

        self.assertEqual(discovery.BROWSE, discovery.tab_of(state))


class Summary(unittest.TestCase):
    def test_it_counts_releases(self) -> None:
        self.assertEqual("1 VPS release", discovery_releases.summary(1))
        self.assertEqual("37 VPS releases", discovery_releases.summary(37))


if __name__ == "__main__":
    unittest.main()
