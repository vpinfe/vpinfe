"""List columns: what each declares, and what the rows hand it."""

from __future__ import annotations

import unittest
from typing import cast

from common.i18n import t
from manager.kit import data, grid, renderers, verbs
from manager.kit.api import ApiClient
from manager.library import games, tageditor


def _column(columns: list[dict], field: str) -> dict:
    return next(one for one in columns if one["field"] == field)


class AListColumn(unittest.TestCase):
    def setUp(self) -> None:
        self.column = grid.list_column("tags", "Tags", looks=renderers.TAG_LOOKS)

    def test_it_is_drawn_as_chips_filtered_by_value_and_sorted_by_the_first_chip(self) -> None:
        self.assertEqual(renderers.DISPATCH, self.column[":cellRenderer"])
        self.assertEqual({"drawn": "chips", "looks": renderers.TAG_LOOKS},
                         self.column["cellRendererParams"])
        self.assertEqual(grid.LIST_FILTER, self.column[":filter"])
        self.assertEqual(grid.list_comparator(renderers.TAG_LOOKS), self.column[":comparator"])

    def test_the_drawing_the_sort_and_the_filter_share_one_order(self) -> None:
        for js in (renderers.CHIPS.js, self.column[":comparator"], grid._CHOICE_FILTER_JS):
            with self.subTest(js=js[:40]):
                self.assertIn(renderers.ORDER, js)

    def test_the_drawing_the_sort_the_filter_and_a_search_read_a_look_s_label(self) -> None:
        said = renderers.named(renderers.TAG_LOOKS)
        for js in (self.column[":comparator"], self.column[":getQuickFilterText"],
                   self.column[":valueFormatter"]):
            with self.subTest(js=js[:40]):
                self.assertIn(said, js)
        for js in (renderers.CHIPS.js, grid._CHOICE_FILTER_JS):
            with self.subTest(js=js[:40]):
                self.assertIn("look.label || value", js)

    def test_the_filter_takes_its_words_and_looks_from_the_column(self) -> None:
        params = self.column["filterParams"]
        self.assertEqual(renderers.TAG_LOOKS, params["looks"])
        self.assertEqual({"none": t("word.none"), "any": t("manager.grid.any_of"),
                          "all": t("manager.grid.all_of"),
                          "search": t("manager.grid.filter_hint")}, params["words"])

    def test_a_looks_source_it_names_is_one_the_page_installs(self) -> None:
        self.assertIn(self.column["filterParams"]["looks"], renderers.LOOKS)

    def test_the_one_value_filter_keeps_its_own_shape(self) -> None:
        column = grid.choice_filter([{"value": "a", "label": "A"}])
        self.assertEqual(grid.CHOICE_FILTER, column[":filter"])
        self.assertEqual([{"value": "a", "label": "A"}], column["filterParams"]["choices"])
        self.assertEqual({"search": t("manager.grid.filter_hint")},
                         column["filterParams"]["words"])


class TheGridsListColumns(unittest.TestCase):
    def test_tags_and_game_themes_are_list_columns(self) -> None:
        for label, columns, field in (("games", games.COLUMNS, "tags"),
                                      ("games", games.COLUMNS, "themes"),
                                      ("tables", games.TABLE_COLUMNS, "tags")):
            with self.subTest(grid=label, field=field):
                self.assertEqual(grid.LIST_FILTER, _column(columns, field)[":filter"])

    def test_a_tag_takes_its_color_and_a_theme_is_a_plain_word(self) -> None:
        self.assertEqual(renderers.TAG_LOOKS,
                         _column(games.COLUMNS, "tags")["filterParams"]["looks"])
        self.assertEqual("", _column(games.COLUMNS, "themes")["filterParams"]["looks"])

    def test_a_game_s_collections_are_a_list_column_in_the_game_view(self) -> None:
        column = _column(games.COLUMNS, "collections")
        self.assertEqual(grid.LIST_FILTER, column[":filter"])
        self.assertEqual(renderers.COLLECTION_LOOKS, column["filterParams"]["looks"])
        preset = games.GAME_VIEWS["manager.view.details"]
        assert not isinstance(preset, list)
        self.assertIn("collections", preset.columns)

    def test_a_smart_collection_s_chip_carries_the_smart_mark(self) -> None:
        look = renderers.LOOKS[renderers.COLLECTION_LOOKS]
        self.assertIn(f'"{verbs.SMART}"', look)
        for js in (renderers.CHIPS.js, grid._CHOICE_FILTER_JS):
            with self.subTest(js=js[:40]):
                self.assertIn(renderers.MARK_CLASS, js)

    def test_the_tag_editor_draws_its_one_tag_through_the_same_chips(self) -> None:
        params = _column(tageditor.COLUMNS, "tag")["cellRendererParams"]
        self.assertEqual({"drawn": "chips", "list": "tag_list",
                          "looks": renderers.TAG_LOOKS}, params)


class TheRowsHoldTheListsThemselves(unittest.TestCase):
    def test_a_game_row(self) -> None:
        library = data.Library.__new__(data.Library)
        library.games = [{"id": "g1", "name": "A", "themes": ["Space", "Aliens"],
                          "user": {"tags": ["Night Owl"]}},
                         {"id": "g2", "name": "B"}]
        library.media = {}

        rows = library.game_rows()

        self.assertEqual([(["Space", "Aliens"], ["Night Owl"]), ([], [])],
                         [(row["themes"], row["tags"]) for row in rows])

    def test_a_table_row(self) -> None:
        rows = games.table_rows([{"id": "t1", "game_id": "g1",
                                  "user": {"tags": ["VR", "Night Owl"]}},
                                 {"id": "t2", "game_id": "g1"}])

        self.assertEqual([["VR", "Night Owl"], []], [row["tags"] for row in rows])


    def test_a_game_row_holds_the_collections_holding_it(self) -> None:
        library = data.Library.__new__(data.Library)
        library.games = [{"id": "g1", "name": "A"}, {"id": "g2", "name": "B"}]
        library.media = {}
        library._game_collections = {"g1": [{"name": "Friday Night", "type": "manual"},
                                            {"name": "90s Bally", "type": "filter"}]}

        self.assertEqual([["Friday Night", "90s Bally"], []],
                         [row["collections"] for row in library.game_rows()])
        self.assertEqual({"90s Bally"}, library.smart_collections())


class _Client:
    def __init__(self) -> None:
        self.reads = 0

    def library_game_collections(self) -> dict:
        self.reads += 1
        return {"g1": [{"name": "Friday Night", "type": "manual"}]}

    def add_to_collection(self, *_args: object) -> None: ...


class TheOneReadIsKeptUntilACollectionChanges(unittest.TestCase):
    def setUp(self) -> None:
        self.client = _Client()
        self.library = data.Library(cast(ApiClient, self.client))

    def test_it_is_read_once(self) -> None:
        self.library.load_game_collections()
        self.library.load_game_collections()

        self.assertEqual(1, self.client.reads)

    def test_a_collection_write_drops_it(self) -> None:
        self.library.load_game_collections()
        self.library.add_to_collection("Friday Night", "g2")

        self.assertFalse(self.library.has_game_collections())

    def test_again_reads_it_whatever_is_held(self) -> None:
        self.library.load_game_collections()
        self.library.load_game_collections(again=True)

        self.assertEqual(2, self.client.reads)


if __name__ == "__main__":
    unittest.main()
