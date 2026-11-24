"""A table the picker refuses says why, where an open menu still shows it."""

from __future__ import annotations

import unittest

from nicegui import ui

from manager.workbench import collection

_PAGE = ui.element()


class ARefusedTableSaysWhy(unittest.TestCase):
    def test_the_reason_is_a_tooltip_the_open_menu_shows(self) -> None:
        with _PAGE, ui.menu() as menu:
            collection._table_menu_item({}, {}, "t-2", "Alpha 2", chosen=False,
                                       blocked="Played by another game in this collection")
        item = next(one for one in menu.descendants() if isinstance(one, ui.menu_item))

        tips = [(one.text, list(one.classes)) for one in item.descendants()
                if isinstance(one, ui.tooltip)]
        self.assertEqual([("Played by another game in this collection", ["manager-menu-tip"])],
                         tips)
        self.assertEqual("true", item.props.get("aria-disabled"))


if __name__ == "__main__":
    unittest.main()
