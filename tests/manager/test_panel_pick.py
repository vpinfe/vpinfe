"""A fact row that can be picked: the third element of a `panel.facts` entry."""

from __future__ import annotations

import unittest

from nicegui import ui

from manager.kit import panel

# Made at import, outside any task, where NiceGUI still hands out the script's own page.
_PAGE = ui.element()


def _drawn(entries: list[tuple]) -> ui.element:
    with _PAGE:
        holder = ui.element("div")
        with holder:
            panel.facts(ui, entries)
    return holder


def _wrappers(holder: ui.element) -> list[ui.element]:
    return [one for one in holder.descendants() if "manager-fact-pick" in one._classes]


class PickableRows(unittest.TestCase):
    def test_only_an_entry_with_a_pick_is_wrapped(self) -> None:
        holder = _drawn([("Name", "a"), ("Other", "b", panel.Pick(lambda: None))])

        self.assertEqual(len(_wrappers(holder)), 1)

    def test_a_wrapper_is_a_button_that_answers_click_and_keys(self) -> None:
        holder = _drawn([("Name", "a", panel.Pick(lambda: None))])
        (wrapper,) = _wrappers(holder)

        self.assertEqual(wrapper.props["role"], "button")
        self.assertEqual(wrapper.props["tabindex"], "0")
        self.assertEqual({listener.type for listener in wrapper._event_listeners.values()},
                         {"click", "keydown.enter.self", "keydown.space.self.prevent"})

    def test_a_click_on_a_control_inside_the_row_does_not_pick(self) -> None:
        holder = _drawn([("Name", "a", panel.Pick(lambda: None))])
        (wrapper,) = _wrappers(holder)
        (click,) = [one for one in wrapper._event_listeners.values() if one.type == "click"]

        self.assertIn("closest('button", click.js_handler or "")

    def test_the_picked_row_is_marked_and_the_others_are_not(self) -> None:
        holder = _drawn([("A", "1", panel.Pick(lambda: None, picked=True)),
                         ("B", "2", panel.Pick(lambda: None))])
        first, second = _wrappers(holder)

        self.assertIn("manager-fact-pick--on", first._classes)
        self.assertEqual(first.props["aria-pressed"], "true")
        self.assertNotIn("manager-fact-pick--on", second._classes)
        self.assertNotIn("aria-pressed", second.props)
