"""A column the grid has no room to pin is not offered Pin left as if it could be.

AG Grid unpins columns while the pinned ones leave less than 50px of its body unpinned.
`grid.Pinning.read` puts that rule to what the grid measures, and `grid.column_menu`
draws the answer, for every grid's header menu.

**What this cannot prove:** that the browser measures what AG Grid measures. That needs a
browser.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path
from unittest.mock import Mock

from nicegui import ui

from common.i18n import t
from manager.kit import grid
from manager.kit.grid import Pinning
from tests.support import trees

MANAGER = Path(__file__).resolve().parents[2] / "manager"
_PAGE = ui.element()

# The Games grid as a browser measured it: its name column alone at 1280x720, and the
# maker column beside the pinned tick and name at 1920x1080.
NARROW = {"pinned": None, "width": 280, "left": 0, "right": 0, "body": 324}
WIDE = {"pinned": None, "width": 188, "left": 422, "right": 0, "body": 964}


class TheRoomToPin(unittest.TestCase):
    def test_a_narrow_grid_has_no_room(self) -> None:
        self.assertEqual(Pinning(pinned=False, room=False), Pinning.read(NARROW))

    def test_a_wide_grid_has_room(self) -> None:
        self.assertEqual(Pinning(pinned=False, room=True), Pinning.read(WIDE))

    def test_the_pinned_columns_leave_50px_of_the_body(self) -> None:
        self.assertFalse(Pinning.read(dict(NARROW, width=324 - 50)).room)
        self.assertTrue(Pinning.read(dict(NARROW, width=324 - 51)).room)

    def test_both_sides_count(self) -> None:
        self.assertFalse(Pinning.read(dict(WIDE, right=400)).room)

    def test_a_pinned_column_counts_once(self) -> None:
        pinned = dict(WIDE, pinned="left", left=422 + 188)
        self.assertEqual(Pinning(pinned=True, room=True), Pinning.read(pinned))

    def test_nothing_measured_offers_the_pin(self) -> None:
        self.assertEqual(Pinning(pinned=False, room=True), Pinning.read(None))


def _items(now: Pinning) -> list[ui.menu_item]:
    with _PAGE:
        menu = ui.menu()
    with menu:
        grid.column_menu(menu, Mock(), [{"field": "name", "headerName": "Game"}], "name", now)
    return [one for one in menu.descendants() if isinstance(one, ui.menu_item)]


def _words(item: ui.element) -> list[str]:
    return [one.text for one in item.descendants() if isinstance(one, ui.item_section)]


def _clicks(item: ui.element) -> int:
    return sum(one.type == "click" for one in item._event_listeners.values())


def _tips(item: ui.element) -> list[tuple[str, list[str]]]:
    return [(one.text, list(one.classes)) for one in item.descendants()
            if isinstance(one, ui.tooltip)]


class TheHeaderMenu(unittest.TestCase):
    def test_with_no_room_pin_left_stays_refused_and_says_why(self) -> None:
        pin = _items(Pinning(pinned=False, room=False))[0]

        self.assertEqual([t("word.pin_left")], _words(pin))
        self.assertIn("manager-menu-blocked", pin.classes)
        self.assertEqual("true", pin.props.get("aria-disabled"))
        self.assertEqual(0, _clicks(pin))
        self.assertEqual([(t("manager.grid.pin_refused"), ["manager-menu-tip"])],
                         _tips(pin))

    def test_with_room_pin_left_pins(self) -> None:
        pin = _items(Pinning(pinned=False, room=True))[0]

        self.assertEqual([t("word.pin_left")], _words(pin))
        self.assertNotIn("manager-menu-blocked", pin.classes)
        self.assertGreater(_clicks(pin), 0)
        self.assertEqual([], _tips(pin))

    def test_a_pinned_column_is_offered_unpin_whatever_the_room(self) -> None:
        pin = _items(Pinning(pinned=True, room=False))[0]

        self.assertEqual([t("word.unpin")], _words(pin))
        self.assertGreater(_clicks(pin), 0)


class EveryGridAsks(unittest.TestCase):
    def test_pin_left_is_drawn_by_the_shared_header_menu_alone(self) -> None:
        """A grid with its own copy of the menu would offer a pin it cannot keep."""
        drawn = [f"manager/{path.relative_to(MANAGER).as_posix()}:{node.lineno}"
                 for path in sorted(MANAGER.rglob("*.py"))
                 for node in ast.walk(trees.tree_for(path))
                 if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "t"
                 and node.args and isinstance(node.args[0], ast.Constant)
                 and node.args[0].value == "word.pin_left"]

        self.assertEqual(["manager/kit/grid.py"], sorted({one.split(":")[0] for one in drawn}),
                         drawn)


if __name__ == "__main__":
    unittest.main()
