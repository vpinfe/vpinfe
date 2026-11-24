"""A picker that draws a mark has one tooltip over its value and one over the mark.

The row's tooltip would cover the mark in the append slot, so a picker that draws a mark
takes the row's words itself and aims them at the value, the way a path field does.
"""

from __future__ import annotations

import unittest
from collections.abc import Callable
from typing import Any

from nicegui import ui

from manager.kit import panel
from tests.support.stylesheet import declarations

_PAGE = ui.element()
OPTIONS = {"0": "One", "1": "Two"}


def _drawn(make: Callable[[], Callable[[], None]]) -> tuple[ui.element, Callable[[], None]]:
    with _PAGE:
        holder = ui.element()
        with holder:
            draw = make()
            draw()
    return holder, draw


VALUE = ".q-field__control-container"


def _tips(holder: ui.element, *, on_value: bool) -> list[ui.tooltip]:
    return [one for one in holder.descendants()
            if isinstance(one, ui.tooltip) and (VALUE in one.props.get("target", "")) == on_value]


def _marked() -> Callable[[Any], None]:
    return panel.value_state("missing", "No such display")


class TheMarkIsOneSize(unittest.TestCase):
    def test_a_mark_in_any_append_slot_takes_the_mark_size(self) -> None:
        rule = declarations(".q-field__append .manager-value-state")
        self.assertEqual("var(--icon-mark)", rule.get("font-size"))


class APickerWithAMark(unittest.TestCase):
    def _each(self) -> dict[str, Callable[[], Callable[[], None]]]:
        return {
            "select": lambda: panel.select(OPTIONS, "0", lambda _e: None, status=_marked()),
            "combo": lambda: panel.combo("0", OPTIONS, lambda _e: None, status=_marked()),
        }

    def test_the_row_s_words_are_aimed_at_the_value_and_not_the_mark(self) -> None:
        for name, make in self._each().items():
            with self.subTest(name):
                holder, draw = _drawn(make)
                tip = getattr(draw, "hover_tip")  # noqa: B009
                self.assertIsInstance(tip, panel.PathTip)
                (hover,) = _tips(holder, on_value=True)
                assert hover.parent_slot is not None
                owner = hover.parent_slot.parent
                self.assertTrue(hover.props["target"].startswith(f"label[for={owner.html_id}] "))
                self.assertIn("no-parent-event", hover.props)

                tip.say("Changed from On - Visual Pinball X's default")

                self.assertEqual("Changed from On - Visual Pinball X's default", hover.text)
                self.assertNotIn("no-parent-event", hover.props)

    def test_the_mark_keeps_the_reason_it_gives(self) -> None:
        holder, _ = _drawn(self._each()["select"])
        said = [one.text for one in _tips(holder, on_value=False)]
        self.assertEqual(["No such display"], said)

    def test_a_picker_with_no_mark_leaves_the_words_to_the_row(self) -> None:
        nothing = panel.value_state("")
        for draw in (panel.select(OPTIONS, "0", lambda _e: None),
                     panel.combo("0", OPTIONS, lambda _e: None),
                     panel.select(OPTIONS, "0", lambda _e: None, status=nothing),
                     panel.combo("0", OPTIONS, lambda _e: None, status=nothing)):
            with self.subTest(draw=draw):
                self.assertIsNone(getattr(draw, "hover_tip"))  # noqa: B009


if __name__ == "__main__":
    unittest.main()
