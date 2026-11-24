"""A game with no table: the view, the line above the Tables grid, the Overview row and
the empty state in the panel."""

from __future__ import annotations

import unittest
from unittest import mock

from nicegui import ui

from common.i18n import t
from manager.kit import views
from manager.library import games
from manager.overview import needs
from manager.settings import sections


def _game(game_id: str, **rest):
    return {"id": game_id, "name": game_id, "year": "1992", "vps_id": "v",
            "table_count": 1, **rest}


class Library:
    def __init__(self, games_, hidden=()):
        self.games = list(games_)
        self._hidden = set(hidden)

    def fillable_media(self):
        return getattr(self, "offered", {})

    def hidden_checks(self):
        return self._hidden


class TheView(unittest.TestCase):
    def test_it_is_a_built_in_games_view_that_filters_to_no_tables(self) -> None:
        built = {one.id: one for one in views.builtins(games.GAME_VIEWS)}
        shown = built[views.builtin_id(games.NO_TABLES_VIEW)]

        self.assertEqual(games.NO_TABLES, shown.filters)
        self.assertEqual(t("manager.view.no_tables"), shown.name)
        self.assertIn("table_count", shown.columns)

    def test_the_count_column_filters_as_a_number(self) -> None:
        column = next(one for one in games.COLUMNS if one["field"] == "table_count")
        self.assertEqual("agNumberColumnFilter", column["filter"])
        self.assertEqual(0, games.NO_TABLES["table_count"]["filter"])


    def test_arriving_makes_it_the_view_the_grid_opens_on(self) -> None:
        library = mock.Mock()
        library.preferences.return_value = {}

        games.arrive_on(library, games.NO_TABLES_VIEW)

        saved = library.put_preferences.call_args.args[1]
        self.assertEqual(views.builtin_id(games.NO_TABLES_VIEW), saved["active"])


class TheLine(unittest.TestCase):
    def draw(self, library, state=None):
        said, shown = [], []
        label = ui.label
        with mock.patch.object(sections.ui, "label",
                               side_effect=lambda text, *a, **k: said.append(text)
                               or label(text)), \
                mock.patch.object(sections.panel, "action",
                                  side_effect=lambda text, call, **kw:
                                  lambda: shown.append(call)):
            with ui.column():
                sections.no_tables_line(library, state or {})
        return said, shown

    def test_it_counts_the_games_with_none(self) -> None:
        said, _ = self.draw(Library([_game("a", table_count=0), _game("b"),
                                     _game("c", table_count=0)]))
        self.assertEqual([t("manager.sections.no_tables_line", count=2)], said)

    def test_a_library_with_a_table_each_draws_nothing(self) -> None:
        said, shown = self.draw(Library([_game("a"), _game("b")]))
        self.assertEqual(([], []), (said, shown))

    def test_a_check_the_library_turned_off_draws_nothing(self) -> None:
        said, _ = self.draw(Library([_game("a", table_count=0)], hidden=["no_tables"]))
        self.assertEqual([], said)

    def test_show_opens_games_on_the_no_tables_view(self) -> None:
        state = {"open_on": mock.Mock()}
        _, shown = self.draw(Library([_game("a", table_count=0)]), state)
        shown[0]()

        state["open_on"].assert_called_once_with(needs.no_tables_opens())


class OnOverview(unittest.TestCase):
    def test_the_row_counts_games_and_opens_the_same_filter(self) -> None:
        from tests.manager.test_needs import FakeLibrary, present

        library = FakeLibrary([_game("a", table_count=0, **present("playfield", "backglass")),
                               _game("b", **present("playfield", "backglass"))])
        rows = {one.key: one for one in needs.collect(library, {})}

        self.assertEqual(("a",), rows["no_tables"].ids)
        self.assertEqual("1 game", rows["no_tables"].counted())
        self.assertEqual(needs.no_tables_opens(), rows["no_tables"].opens)

    def test_a_game_whose_count_is_unknown_is_not_a_game_without_tables(self) -> None:
        from tests.manager.test_needs import FakeLibrary, game, present

        library = FakeLibrary([game("a", **present("playfield", "backglass"))])
        self.assertNotIn("no_tables", {one.key for one in needs.collect(library, {})})

    def test_it_can_be_set_aside_and_says_when_it_comes_back(self) -> None:
        self.assertTrue(needs.can_set_aside("no_tables"))
        self.assertEqual(t("manager.needs.back.no_tables"), needs.comes_back("no_tables"))
        self.assertEqual(t("manager.sections.no_tables"), needs.name_of("no_tables"))
