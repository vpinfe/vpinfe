"""The Collections grid: its rows, its columns and its two views."""

from __future__ import annotations

import unittest
from typing import cast

from common.games.collection_store import BUILTIN_ALL
from common.i18n import t
from manager.kit import data, grid, verbs, views
from manager.kit.api import ApiClient
from manager.library import collections

_SMART = {"name": "90s Bally", "type": "filter", "count": 6, "before_limit": 6,
          "added": 1, "matched": 5, "excluded": 1, "missing": 0}
_CUT = {"name": "Five Bally", "type": "filter", "count": 5, "before_limit": 21,
        "added": 0, "matched": 21, "excluded": 0, "missing": 0}
_GONE = {"name": "Friday Night", "type": "manual", "count": 4, "before_limit": 4,
         "added": 4, "matched": 0, "excluded": 0, "missing": 1}
_HIDDEN = {"name": "Tournament", "type": "manual", "count": 0, "before_limit": 0,
           "added": 1, "matched": 0, "excluded": 0, "missing": 0, "hidden": 1}


def _row(built: list[dict], name: str) -> dict:
    return next(one for one in built if one["name"] == name)


def _column(field: str) -> dict:
    return next(one for one in collections.COLUMNS if one["field"] == field)


class TheRows(unittest.TestCase):
    def setUp(self) -> None:
        self.built = collections.rows([_SMART, _CUT, _GONE, _HIDDEN],
                                      opens_on="Friday Night")

    def test_kind_holds_the_wire_s_token(self) -> None:
        self.assertEqual({"filter", "manual"}, {one["kind"] for one in self.built})

    def test_a_limit_that_cuts_says_what_it_cuts_from(self) -> None:
        self.assertEqual(t("manager.collections.count_of", count=5, whole=21),
                         _row(self.built, "Five Bally")["games_said"])
        self.assertEqual("6", _row(self.built, "90s Bally")["games_said"])

    def test_missing_games_carry_a_chip_and_nothing_else_does(self) -> None:
        self.assertEqual(t("manager.collections.count_missing", count=1),
                         _row(self.built, "Friday Night")["missing_said"])
        self.assertEqual("", _row(self.built, "90s Bally")["missing_said"])

    def test_a_hidden_table_carries_its_own_chip(self) -> None:
        self.assertEqual(t("manager.collections.count_hidden", count=1),
                         _row(self.built, "Tournament")["hidden_said"])
        self.assertEqual("", _row(self.built, "Friday Night")["hidden_said"])
        self.assertIn("d.hidden_said", _column("count")[":cellRenderer"])

    def test_needing_attention_is_a_game_taken_out_gone_or_hidden(self) -> None:
        needed = collections.ATTENTION_NEEDED
        self.assertEqual({"90s Bally": needed, "Five Bally": "", "Friday Night": needed,
                          "Tournament": needed},
                         {one["name"]: one["attention"] for one in self.built})
        preset = collections.COLLECTION_VIEWS["manager.collections.needs_attention"]
        assert not isinstance(preset, list)
        self.assertIn("hidden", preset.columns)

    def test_only_the_collection_the_cabinet_opens_on_is_marked(self) -> None:
        self.assertEqual(["Friday Night"],
                         [one["name"] for one in self.built if one["opens_on"]])

    def test_opening_on_all_games_marks_none(self) -> None:
        self.assertFalse(any(one["opens_on"] for one in collections.rows([_SMART, _GONE])))

    def test_only_a_collection_kept_out_of_the_frontend_says_so(self) -> None:
        built = collections.rows([{**_SMART, "in_frontend": False}, _GONE])

        self.assertEqual({"90s Bally": False, "Friday Night": True},
                         {one["name"]: one["in_frontend"] for one in built})
        self.assertEqual({"90s Bally": "", "Friday Night": collections.MENU_IN},
                         {one["name"]: one["in_menu"] for one in built})
        self.assertIn(verbs.HIDE, _column("name")[":cellRenderer"])

    def test_an_order_reads_in_its_field_s_own_words(self) -> None:
        orders = [{**_SMART, "order_by": "last_played", "direction": "desc"},
                  {**_CUT, "order_by": "title", "direction": "asc"},
                  {**_GONE, "order_by": "manual", "direction": "asc"}]

        self.assertEqual({"90s Bally": "Last Played · Newest first",
                          "Five Bally": "Title · A to Z",
                          "Friday Night": "Custom Order"},
                         {one["name"]: one["order"] for one in collections.rows(orders)})

    def test_a_ranked_order_reads_as_its_community_page_and_view(self) -> None:
        ranked = {"extension": "site", "display_name": "Site", "title": "Site Tables",
                  "name": "Top Rated", "offered": True}
        orders = [{**_SMART, "order_by": "site/tables/top", "direction": "asc",
                   "ranking": ranked},
                  {**_CUT, "order_by": "site/tables/top", "direction": "asc",
                   "ranking": {**ranked, "title": "", "name": "", "offered": False}}]

        self.assertEqual({"90s Bally": "Site Tables: Top Rated",
                          "Five Bally": "Site, not running"},
                         {one["name"]: one["order"] for one in collections.rows(orders)})


class TheAllGamesRow(unittest.TestCase):
    def test_it_is_the_whole_library_and_is_not_a_collection(self) -> None:
        row = collections.all_games_row(149)

        self.assertEqual((BUILTIN_ALL, t("manager.collections.all_games"), 149, True),
                         (row["id"], row["name"], row["count"], row["pinned"]))

    def test_it_carries_the_home_mark_only_while_the_frontend_opens_on_all_games(self) -> None:
        self.assertTrue(collections.all_games_row(3, "")["opens_on"])
        self.assertFalse(collections.all_games_row(3, "Friday Night")["opens_on"])

    def test_it_is_not_drawn_with_a_drag_handle_and_only_it_is_kept_first(self) -> None:
        self.assertIn("pinned", str(_column("name")[":rowDrag"]))
        self.assertIn("pinned", collections._KEEP_FIRST)

    def test_collection_rows_do_not_include_it(self) -> None:
        self.assertNotIn(BUILTIN_ALL, [one["id"] for one in collections.rows([_SMART])])


class TheColumns(unittest.TestCase):
    def test_kind_filters_on_the_token_and_shows_the_word(self) -> None:
        kind = _column("kind")
        self.assertEqual([("manual", t("manager.collections.hand_picked")),
                          ("filter", t("manager.collections.smart"))],
                         [(one["value"], one["label"])
                          for one in kind["filterParams"]["choices"]])
        self.assertIn(t("manager.collections.smart"), kind[":valueFormatter"])

    def test_no_limit_reads_all_except_on_the_all_games_row(self) -> None:
        shown = _column("limit")[":valueFormatter"]

        self.assertIn(t("word.all"), shown)
        self.assertIn("pinned ? ''", shown)

    def test_smart_wears_its_mark_and_the_one_the_cabinet_opens_on_wears_its_own(self) -> None:
        self.assertIn(verbs.SMART, _column("kind")[":cellRenderer"])
        self.assertIn(verbs.OPENS_ON, _column("name")[":cellRenderer"])

    def test_kind_keeps_its_floor(self) -> None:
        self.assertGreaterEqual(_column("kind")["width"], 120)


class TheViews(unittest.TestCase):
    def setUp(self) -> None:
        self.built = views.builtins(collections.COLLECTION_VIEWS)
        self.fields = {one["field"] for one in collections.COLUMNS}

    def test_everything_leads_and_needs_attention_follows(self) -> None:
        self.assertEqual([t("manager.view.everything"),
                          t("manager.collections.needs_attention")],
                         [one.name for one in self.built])

    def test_everything_is_what_the_cabinet_shows(self) -> None:
        self.assertEqual(("name", "kind", "count", "order"), self.built[0].columns)
        self.assertEqual({}, self.built[0].filters)

    def test_the_picture_is_in_the_name_and_has_no_column_of_its_own(self) -> None:
        self.assertNotIn("icon", self.fields)
        self.assertEqual([], [one.name for one in self.built if "icon" in one.columns])

    def test_a_saved_view_naming_the_picture_column_shows_the_rest(self) -> None:
        saved = views.from_record({"id": "mine", "name": "Mine",
                                   "columns": ["icon", "name", "kind"]})

        self.assertEqual(["name", "kind"],
                         views.visible_columns(saved, [one["field"]
                                                       for one in collections.COLUMNS]))

    def test_needs_attention_filters_on_a_column_the_grid_has(self) -> None:
        wanted = self.built[1]
        self.assertEqual({"attention": {"values": [collections.ATTENTION_NEEDED]}},
                         wanted.filters)
        self.assertLessEqual(set(wanted.filters) | set(wanted.columns), self.fields)
        self.assertEqual(grid.CHOICE_FILTER, _column("attention")[":filter"])


class _Client:
    def __init__(self) -> None:
        self.opens_on = "Friday Night"

    def collections(self) -> list[dict]:
        return [_GONE]

    def config_values(self) -> dict:
        return {"behavior": {"startup_collection": self.opens_on}}

    def put_config(self, changes: dict) -> dict:
        self.opens_on = changes["behavior"]["startup_collection"]
        return {}


class WhatTheCabinetOpensOn(unittest.TestCase):
    def test_it_is_read_with_the_list(self) -> None:
        library = data.Library(cast(ApiClient, _Client()))
        library.load_collections()

        self.assertEqual("Friday Night", library.opens_on())

    def test_changing_the_setting_reads_it_again(self) -> None:
        library = data.Library(cast(ApiClient, _Client()))
        library.load_collections()
        library.put_config({"behavior": {"startup_collection": ""}})
        library.load_collections()

        self.assertEqual("", library.opens_on())


if __name__ == "__main__":
    unittest.main()
