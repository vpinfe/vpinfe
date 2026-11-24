"""The index of a settings page is drawn again where it stands, with the rows in order."""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import MagicMock, patch

from manager.kit import panel


class _Element:
    def __init__(self) -> None:
        self.is_deleted = False
        self.cleared = 0
        self.id = 7

    def __enter__(self) -> _Element:
        return self

    def __exit__(self, *_: Any) -> None:
        return None

    def classes(self, *_: Any, **__: Any) -> _Element:
        return self

    def style(self, *_: Any, **__: Any) -> _Element:
        return self

    def clear(self) -> None:
        self.cleared += 1


class SectionsRedrawTests(unittest.TestCase):
    def setUp(self) -> None:
        self.made: list[_Element] = []

        def element(*_: Any, **__: Any) -> _Element:
            self.made.append(_Element())
            return self.made[-1]

        self.ui = self.enterContext(patch.object(panel, "ui"))
        self.ui.element.side_effect = element
        self.rows = self.enterContext(patch.object(panel, "_rail_row"))
        self.enterContext(patch.object(panel, "_rail_group"))
        self.enterContext(patch.object(panel, "t", side_effect=lambda key: key))
        self.pick = MagicMock()

    def _drawn(self) -> list[tuple[str, int]]:
        return [(call.args[0], call.kwargs["order"]) for call in self.rows.call_args_list]

    def test_the_rows_after_the_open_one_are_ordered_past_the_work(self) -> None:
        panel.sections([("a", "A"), ("b", "B"), ("c", "C")], "b", self.pick)

        self.assertEqual(self._drawn(),
                         [("a", 0), ("b", 0), ("c", panel.AFTER_WORK_ORDER)])

    def test_drawing_again_replaces_the_rows_with_the_new_ones(self) -> None:
        _work, draw = panel.sections([("a", "A"), ("b", "B")], "a", self.pick)
        rail = self.made[2]
        self.rows.reset_mock()

        draw([("a", "A")], "a")

        self.assertEqual(rail.cleared, 2)
        self.assertEqual(self._drawn(), [("a", 0)])

    def test_a_kept_rail_wires_its_scroll_and_an_unkept_one_does_not(self) -> None:
        panel.sections([("a", "A")], "a", self.pick)
        self.ui.run_javascript.assert_called_once_with(panel._OPEN_ROW_IN_VIEW_JS)
        self.ui.run_javascript.reset_mock()

        panel.sections([("a", "A")], "a", self.pick, keep="settings")

        sent = [call.args[0] for call in self.ui.run_javascript.call_args_list]
        self.assertIn(panel.KEEP_RAIL_SCROLL_JS % {"key": '"settings"', "id": '"c7"'}, sent)

    def test_a_rail_that_has_gone_is_left_alone(self) -> None:
        _work, draw = panel.sections([("a", "A")], "a", self.pick)
        self.made[2].is_deleted = True
        self.rows.reset_mock()

        draw([("a", "A"), ("b", "B")], "a")

        self.rows.assert_not_called()


if __name__ == "__main__":
    unittest.main()
