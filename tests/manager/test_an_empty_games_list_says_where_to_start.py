"""A games or tables list with nothing in it says where rows come from and can be rescanned,
instead of drawing a grid with no rows."""

from __future__ import annotations

import unittest
from typing import Any
from unittest import mock
from unittest.mock import Mock

from nicegui import ui

from common.i18n import t
from manager.library import games
from manager.settings import sections


def _words(body: ui.element) -> str:
    return " ".join(str(getattr(one, "text", "")) for one in (body, *body.descendants()))


class EmptyListTests(unittest.TestCase):
    def _drawn(self, build: Any, state: dict[str, Any], rescan: Any = None) -> ui.element:
        library = Mock()
        with mock.patch.object(sections, "metadata_line") as line, ui.column() as body:
            build([], library, lambda _row: None, state, None, rescan=rescan)
        line.assert_called_once()
        library.tag_looks.assert_not_called()
        return body

    def _games(self, rows: list[Any], library: Any, on_select: Any, state: Any,
               rerender: Any, rescan: Any = None) -> None:
        games.build(rows, [], library, on_select, state, rerender, rescan=rescan)

    def test_games_with_no_rows_point_at_library_folders(self) -> None:
        body = self._drawn(self._games, {"view": "games"})

        self.assertIn(t("manager.games.empty", page=t("manager.section.library_folders")),
                      _words(body))

    def test_tables_say_tables(self) -> None:
        body = self._drawn(games.build_tables, {"view": "tables"})

        self.assertIn(t("manager.games.empty_tables",
                        page=t("manager.section.library_folders")), _words(body))

    def test_a_scan_can_be_asked_for_from_the_empty_page(self) -> None:
        body = self._drawn(self._games, {"view": "games"}, rescan=lambda: None)

        buttons = [one.text for one in body.descendants() if isinstance(one, ui.button)]
        self.assertEqual([t("manager.add.games"), t("manager.games.rescan")], buttons)

    def test_a_filter_a_link_asked_for_is_spent_not_held(self) -> None:
        state: dict[str, Any] = {"view": "games", "arriving": {"x": 1}, "sets": "a"}
        self._drawn(self._games, state)

        self.assertNotIn("arriving", state)
        self.assertNotIn("sets", state)


if __name__ == "__main__":
    unittest.main()
