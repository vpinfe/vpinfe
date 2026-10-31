"""The binding table: one field for everything an action is mapped to, and a question when a
press is already somebody else's."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from nicegui import ui

from common.i18n import t
from manager.settings import binding_table as table
from manager.settings.binding_table import Row, Table
from tests.support.clicks import press, said

ROWS = (
    Row("previous", "Previous", group="Navigation",
        defaults=("key:ShiftLeft", "pad:standard/button:14")),
    Row("next", "Next", group="Navigation", defaults=("key:ShiftRight",)),
    Row("select", "Select", group="Navigation", defaults=("key:Enter",)),
    Row("back", "Back", group="Navigation", defaults=("key:Escape",)),
    Row("exit", "Quit", group="Navigation", defaults=("key:Escape@hold:1500",)),
    Row("take_picture", "Take Picture", group="During Play", holds=False),
)
SHIPPED = {"previous": ["key:ShiftLeft", "pad:standard/button:14"], "next": ["key:ShiftRight"],
           "select": ["key:Enter"], "back": ["key:Escape"],
           "exit": ["key:Escape@hold:1500"], "take_picture": []}
SOURCE = "VPinFE defaults"


async def _kept(_changes: dict[str, list[str]]) -> bool:
    return True


def _drawn(bound: dict[str, Any] | None = None, *, into: ui.element | None = None,
           rows: tuple[Row, ...] = ROWS, **kwargs: Any) -> tuple[Table, ui.element]:
    with patch.object(ui, "run_javascript"), into or ui.column() as body:
        control = Table(rows, SHIPPED if bound is None else bound,
                        write=kwargs.pop("write", _kept), source=SOURCE, **kwargs)
    return control, body


def _labels(body: ui.element) -> list[str]:
    return [one.text for one in body.descendants() if isinstance(one, ui.label)]


def _classes(body: ui.element) -> list[str]:
    return [name for one in body.descendants() for name in one.classes]


def _wrappers(body: ui.element) -> dict[str, ui.element]:
    """Each row's wrapper, by the row's label."""
    found = {}
    for one in body.descendants():
        if "manager-binding-row" in one.classes:
            [name] = [label.text for label in one.descendants()
                      if isinstance(label, ui.label) and "manager-binding-label" in label.classes]
            found[name] = one
    return found


def _chips(wrapper: ui.element) -> list[str]:
    """What a row's field reads, without the label before it or the marks on each chip."""
    [field] = [one for one in wrapper.descendants() if "manager-binding-field" in one.classes]
    return [one.text for one in field.descendants()
            if isinstance(one, ui.label) and one.text != "✕"]


def _field_of(wrapper: ui.element) -> list[ui.element]:
    [field] = [one for one in wrapper.descendants() if "manager-binding-field" in one.classes]
    return list(field.descendants())


class PureHelperTests(unittest.TestCase):
    def test_adding_puts_a_binding_on_the_row_and_nowhere_else(self) -> None:
        after = table.after_adding({"a": ["key:KeyA"], "b": []}, "b", "key:KeyB")

        self.assertEqual(after, {"a": ["key:KeyA"], "b": ["key:KeyB"]})

    def test_a_press_taken_leaves_the_row_that_held_it(self) -> None:
        after = table.after_adding({"a": ["key:KeyA", "key:KeyC"], "b": []}, "b", "key:KeyA",
                                   take=True)

        self.assertEqual(after, {"a": ["key:KeyC"], "b": ["key:KeyA"]})

    def test_a_hold_taken_is_taken_at_whatever_length_the_other_row_held_it(self) -> None:
        after = table.after_adding({"a": ["key:Escape@hold:1500"], "b": []}, "b",
                                   "key:Escape@hold:2000", take=True)

        self.assertEqual(after, {"a": [], "b": ["key:Escape@hold:2000"]})

    def test_a_new_length_takes_the_place_of_the_old_one(self) -> None:
        after = table.after_adding({"a": ["key:KeyA", "key:Escape"]}, "a",
                                   "key:Escape@hold:1500", old="key:Escape")

        self.assertEqual(after, {"a": ["key:KeyA", "key:Escape@hold:1500"]})

    def test_what_it_is_given_is_not_changed(self) -> None:
        bound = {"a": ["key:KeyA"], "b": []}

        table.after_adding(bound, "b", "key:KeyA", take=True)

        self.assertEqual(bound, {"a": ["key:KeyA"], "b": []})

    def test_a_revert_that_takes_nothing_from_a_row_gives_nothing_up(self) -> None:
        after, given = table.after_reverting(
            {"a": ["key:KeyZ"], "b": ["key:KeyB"]}, "a", ("key:KeyA",))

        self.assertEqual(after, {"a": ["key:KeyA"], "b": ["key:KeyB"]})
        self.assertEqual(given, {})

    def test_a_revert_takes_back_a_default_another_row_has_since_taken(self) -> None:
        after, given = table.after_reverting(
            {"a": [], "b": ["key:KeyA", "key:KeyB"]}, "a", ("key:KeyA",))

        self.assertEqual(after, {"a": ["key:KeyA"], "b": ["key:KeyB"]})
        self.assertEqual(given, {"b": ["key:KeyA"]})

    def test_a_default_the_row_still_holds_takes_nothing_from_another(self) -> None:
        _, given = table.after_reverting(
            {"a": ["key:KeyA", "key:KeyZ"], "b": []}, "a", ("key:KeyA",))

        self.assertEqual(given, {})

    def test_only_the_rows_that_differ_are_changes(self) -> None:
        self.assertEqual(
            table.changed({"a": ["x"], "b": ["y"]}, {"a": ["x"], "b": []}), {"b": []})

    def test_a_length_set_some_other_way_joins_the_choices_where_it_falls(self) -> None:
        self.assertEqual(table.stops(0), list(table.HOLD_STOPS))
        self.assertEqual(table.stops(1200), [0, 500, 1000, 1200, 1500, 2000, 3000])

    def test_names_are_read_out_in_a_sentence(self) -> None:
        self.assertEqual(table.listed([]), "")
        self.assertEqual(table.listed(["A"]), "A")
        self.assertEqual(table.listed(["A", "B"]), t("manager.binding_table.and_last",
                                                     most="A", last="B"))
        self.assertEqual(table.listed(["A", "B", "C"]),
                         t("manager.binding_table.and_last", most="A, B", last="C"))

    def test_a_held_binding_closes_its_own_aside_with_a_comma(self) -> None:
        self.assertEqual(table.said(["key:Escape@hold:1500"]), "Esc, held 1.5s,")
        self.assertEqual(table.said(["key:Escape"]), "Esc")


class ElsewhereTests(unittest.TestCase):
    def test_this_device_is_not_another_one(self) -> None:
        for peer in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
            with self.subTest(peer=peer):
                self.assertFalse(table.elsewhere(peer))

    def test_another_address_is_another_device(self) -> None:
        for peer in ("192.168.1.20", "10.0.0.4", "fe80::1", "::ffff:192.168.1.20"):
            with self.subTest(peer=peer):
                self.assertTrue(table.elsewhere(peer))

    def test_no_address_is_no_other_device(self) -> None:
        for peer in ("", "testclient"):
            with self.subTest(peer=peer):
                self.assertFalse(table.elsewhere(peer))


class DrawnTests(unittest.TestCase):
    def test_every_row_is_drawn_with_its_label_and_what_it_is_mapped_to(self) -> None:
        _, body = _drawn()

        rows = {name: _chips(wrapper) for name, wrapper in _wrappers(body).items()}
        self.assertEqual(rows["Previous"], ["Left Shift", "D-pad Left"])
        self.assertEqual(rows["Quit"], ["Esc, held 1.5s"])
        self.assertEqual(list(rows), ["Previous", "Next", "Select", "Back", "Quit",
                                      "Take Picture"])

    def test_each_heading_is_drawn_once_above_its_rows(self) -> None:
        _, body = _drawn()

        headings = [one.text for one in body.descendants()
                    if isinstance(one, ui.label) and "manager-fact-heading" in one.classes
                    and "manager-binding-tester" not in one.classes]
        self.assertEqual(headings, ["Navigation", "During Play"])

    def test_a_row_with_nothing_mapped_says_so_in_a_warning_chip(self) -> None:
        _, body = _drawn()

        [empty] = [one for one in _field_of(_wrappers(body)["Take Picture"])
                   if isinstance(one, ui.label)]
        self.assertEqual(empty.text, t("manager.binding_table.nothing_mapped"))
        self.assertIn("manager-tier--warn", empty.classes)

    def test_a_row_with_something_mapped_does_not(self) -> None:
        _, body = _drawn()

        self.assertNotIn(t("manager.binding_table.nothing_mapped"), _chips(
            _wrappers(body)["Next"]))

    def test_keys_come_before_controller_buttons_whichever_order_they_were_mapped(self) -> None:
        _, body = _drawn({**SHIPPED, "previous": ["pad:standard/button:14", "key:ShiftLeft"]})

        self.assertEqual(_chips(_wrappers(body)["Previous"]), ["Left Shift", "D-pad Left"])

    def test_a_controller_button_carries_the_controller_icon_and_a_key_does_not(self) -> None:
        _, body = _drawn()

        icons = [one for one in _field_of(_wrappers(body)["Previous"])
                 if isinstance(one, ui.icon)]
        self.assertEqual(len(icons), 1)
        self.assertIn("manager-chip-glyph", icons[0].classes)
        self.assertFalse([one for one in _field_of(_wrappers(body)["Next"])
                          if isinstance(one, ui.icon)])

    def test_no_chip_on_a_standard_pad_puts_the_layout_s_name_on_screen(self) -> None:
        _, body = _drawn({**SHIPPED, "select": ["pad:standard/button:0", "pad:standard/button:16"]})

        self.assertEqual(_chips(_wrappers(body)["Select"]), ["Button 0", "Button 16"])
        self.assertFalse([text for text in _labels(body) if "standard" in text.lower()])

    def test_a_press_two_rows_hold_marks_both_chips_and_names_the_other_row(self) -> None:
        _, body = _drawn({**SHIPPED, "select": ["key:Escape"]})

        for name in ("Select", "Back"):
            chips = [one for one in _field_of(_wrappers(body)[name])
                     if "manager-chip-warn" in one.classes]
            self.assertEqual(len(chips), 1, name)

    def test_a_press_held_by_one_row_is_not_a_clash(self) -> None:
        _, body = _drawn()

        self.assertNotIn("manager-chip-warn", _classes(body))

    def test_the_same_key_pressed_and_held_is_a_clash_with_no_one(self) -> None:
        """Back is the tap of Esc and Quit is its hold: one key, two different presses."""
        _, body = _drawn()

        for name in ("Back", "Quit"):
            self.assertFalse([one for one in _field_of(_wrappers(body)[name])
                              if "manager-chip-warn" in one.classes], name)

    def test_a_row_that_is_not_what_it_ships_with_carries_the_revert_mark(self) -> None:
        _, body = _drawn({**SHIPPED, "next": ["key:KeyN"]})

        marked = [name for name, wrapper in _wrappers(body).items()
                  if [one for one in wrapper.descendants() if "manager-revert" in one.classes]]
        self.assertEqual(marked, ["Next"])

    def test_a_row_the_same_as_it_ships_has_none(self) -> None:
        _, body = _drawn()

        self.assertNotIn("manager-revert", _classes(body))

    def test_a_row_with_the_same_bindings_in_another_order_has_none(self) -> None:
        _, body = _drawn({**SHIPPED, "previous": ["pad:standard/button:14", "key:ShiftLeft"]})

        self.assertNotIn("manager-revert", _classes(body))

    def test_a_row_with_no_default_and_something_mapped_can_be_reverted_to_nothing(
            self) -> None:
        _, body = _drawn({**SHIPPED, "take_picture": ["key:KeyP"]})

        [marked] = [name for name, wrapper in _wrappers(body).items()
                    if [one for one in wrapper.descendants() if "manager-revert" in one.classes]]
        self.assertEqual(marked, "Take Picture")

    def test_a_note_and_a_finding_are_drawn_under_their_row(self) -> None:
        def finding() -> None:
            ui.label("It cannot be heard")

        rows = (Row("a", "A", note="What it does", findings=(finding,)),)
        _, body = _drawn({"a": []}, rows=rows)

        self.assertIn("What it does", _labels(body))
        self.assertIn("It cannot be heard", _labels(body))

    def test_the_live_line_waits_for_a_press_until_one_is_heard(self) -> None:
        _, body = _drawn()

        self.assertIn("Press anything to see what it does", _labels(body))

    def test_the_table_is_headed_as_the_tester_above_the_assignments(self) -> None:
        _, body = _drawn()

        [heading] = [one for one in body.descendants()
                     if isinstance(one, ui.label) and "manager-binding-tester" in one.classes]
        self.assertEqual(heading.text, "Live Input Tester")
        self.assertEqual(_labels(body)[0], "Live Input Tester")

    def test_a_press_let_go_leaves_the_line_and_the_rows_it_lit(self) -> None:
        control, body = _drawn()
        control._heard(SimpleNamespace(args="key:Enter"))
        self.assertNotIn("Press anything to see what it does", _labels(body))

        control._let_go(SimpleNamespace(args=""))

        self.assertIn("Press anything to see what it does", _labels(body))
        self.assertEqual(control.lit, "")
        self.assertNotIn("manager-binding-lit", _classes(body))

    def test_a_press_let_go_while_another_is_down_shows_the_one_still_down(self) -> None:
        control, body = _drawn()
        control._heard(SimpleNamespace(args="key:Enter"))
        control._heard(SimpleNamespace(args="key:Escape"))

        control._let_go(SimpleNamespace(args="key:Enter"))

        self.assertIn("Enter", _labels(body))
        self.assertNotIn("Escape", _labels(body))

    def test_a_release_while_a_row_is_listening_lights_nothing(self) -> None:
        control, body = _drawn()
        control._heard(SimpleNamespace(args="key:Enter"))
        control._heard(SimpleNamespace(args="key:Escape"))
        control._listening(SimpleNamespace(args="next"))

        control._let_go(SimpleNamespace(args="key:Enter"))

        self.assertEqual(control.lit, "")
        self.assertNotIn("manager-binding-lit", _classes(body))

    def test_the_box_holds_only_the_readout_on_the_serving_device_and_elsewhere(self) -> None:
        for elsewhere in (False, True):
            with patch.object(table, "opened_elsewhere", return_value=elsewhere):
                control, _ = _drawn()

            words = [one.text for one in control.line.descendants() if isinstance(one, ui.label)]
            self.assertEqual(words, ["Press anything to see what it does"])

    def test_a_string_default_is_not_read_letter_by_letter(self) -> None:
        _, body = _drawn({**SHIPPED, "next": "key:ArrowRight,key:KeyD"})

        self.assertEqual(_chips(_wrappers(body)["Next"]), ["Right Arrow", "D"])


class PadsWhenHeardTests(unittest.TestCase):
    def test_a_controller_button_is_not_drawn_until_a_controller_is_heard(self) -> None:
        _, body = _drawn(pads_when_heard=True)

        self.assertEqual(_chips(_wrappers(body)["Previous"]), ["Left Shift"])

    def test_the_first_controller_press_brings_every_controller_chip_in(self) -> None:
        control, body = _drawn(pads_when_heard=True)

        control._heard(SimpleNamespace(args="pad:standard/button:0"))

        self.assertEqual(_chips(_wrappers(body)["Previous"]), ["Left Shift", "D-pad Left"])

    def test_a_key_press_leaves_the_controller_chips_out(self) -> None:
        control, body = _drawn(pads_when_heard=True)

        control._heard(SimpleNamespace(args="key:KeyA"))

        self.assertEqual(_chips(_wrappers(body)["Previous"]), ["Left Shift"])

    def test_a_table_not_asked_to_wait_shows_them_from_the_start(self) -> None:
        _, body = _drawn()

        self.assertEqual(_chips(_wrappers(body)["Previous"]), ["Left Shift", "D-pad Left"])


class WhatCanBeChangedTests(unittest.TestCase):
    def test_a_row_carries_its_name_and_whether_it_can_be_held_for_the_listener(self) -> None:
        _, body = _drawn()

        wrappers = _wrappers(body)
        for row in ROWS:
            with self.subTest(row=row.name):
                props = wrappers[row.label].props
                self.assertEqual(props["data-row"], row.name)
                self.assertEqual(props["data-holds"], "1" if row.holds else "0")

    def test_no_row_is_marked_as_listening_until_one_is_asked_to(self) -> None:
        _, body = _drawn()

        for wrapper in _wrappers(body).values():
            self.assertNotIn("manager-binding-row--listening", wrapper.classes)

    def test_a_click_on_a_row_is_the_browser_s_to_hear(self) -> None:
        """The listener works from the page's markup, so a row has no click handler to
        stack another listen on."""
        _, body = _drawn()

        for wrapper in _wrappers(body).values():
            self.assertFalse([one for one in wrapper._event_listeners.values()
                              if one.type == "click"])

    def test_the_table_hears_the_listener_through_its_line(self) -> None:
        control, _ = _drawn()

        kinds = {one.type for one in control.line._event_listeners.values()}
        self.assertLessEqual({"heard", "released", "listen", "captured"}, kinds)

    def test_a_chip_opens_its_hold_menu_on_a_row_that_can_hold(self) -> None:
        _, body = _drawn()

        self.assertTrue([one for one in _field_of(_wrappers(body)["Back"])
                         if isinstance(one, ui.menu)])
        self.assertTrue([one for one in _field_of(_wrappers(body)["Back"])
                         if isinstance(one, ui.toggle)])

    def test_a_row_that_cannot_hold_has_no_hold_menu(self) -> None:
        _, body = _drawn({**SHIPPED, "take_picture": ["key:KeyP"]})

        self.assertFalse([one for one in _field_of(_wrappers(body)["Take Picture"])
                          if isinstance(one, (ui.menu, ui.toggle))])

    def test_every_hold_length_is_a_stop_and_none_is_the_first(self) -> None:
        _, body = _drawn()

        [toggle] = [one for one in _field_of(_wrappers(body)["Back"])
                    if isinstance(one, ui.toggle)]
        self.assertEqual(list(toggle.options), list(table.HOLD_STOPS))
        self.assertEqual(toggle.options[0], t("manager.binding_table.no_hold"))
        self.assertEqual(toggle.options[1500], t("manager.binding_table.seconds",
                                                 seconds="1.5"))
        self.assertEqual(toggle.value, 0)

    def test_a_chip_already_held_starts_on_its_length(self) -> None:
        _, body = _drawn()

        [toggle] = [one for one in _field_of(_wrappers(body)["Quit"])
                    if isinstance(one, ui.toggle)]
        self.assertEqual(toggle.value, 1500)

    def test_a_table_that_is_only_shown_cannot_be_changed(self) -> None:
        control, body = _drawn(writable=False, bound={**SHIPPED, "next": ["key:KeyN"]})

        for wrapper in _wrappers(body).values():
            self.assertNotIn("data-row", wrapper.props)
        self.assertFalse({"listen", "captured"} & {
            one.type for one in control.line._event_listeners.values()})
        self.assertNotIn("✕", _labels(body))
        self.assertNotIn("manager-revert", _classes(body))
        self.assertFalse([one for one in body.descendants()
                          if isinstance(one, (ui.menu, ui.toggle))])


class AnActedOnTable(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        # Outside the test's task, which has no page to draw into.
        self.body = ui.column()
        self.written: list[dict[str, list[str]]] = []

    async def write(self, changes: dict[str, list[str]]) -> bool:
        self.written.append(changes)
        return True

    def drawn(self, bound: dict[str, Any] | None = None, **kwargs: Any) -> Table:
        control, _ = _drawn(bound, into=self.body, write=kwargs.pop("write", self.write),
                            **kwargs)
        return control

    def asking(self, *answers: bool) -> Any:
        return patch("manager.kit.confirm.ask", new=AsyncMock(side_effect=answers))

    async def test_a_press_nobody_holds_is_added_without_a_question(self) -> None:
        control = self.drawn()

        with self.asking() as ask:
            await press(control._mapped, control.by_name["next"], "key:KeyN")

        ask.assert_not_awaited()
        self.assertEqual(self.written, [{"next": ["key:ShiftRight", "key:KeyN"]}])
        self.assertEqual(control.bound["next"], ["key:ShiftRight", "key:KeyN"])

    async def test_a_press_the_row_already_has_is_not_added_twice(self) -> None:
        control = self.drawn()

        with self.asking() as ask:
            await press(control._mapped, control.by_name["next"], "key:ShiftRight")

        ask.assert_not_awaited()
        self.assertEqual(self.written, [])
        self.assertEqual(control.live.nothing,
                         t("manager.binding_table.already_does", row="Next"))

    async def test_a_hold_the_row_already_has_at_another_length_is_not_added_either(
            self) -> None:
        control = self.drawn()

        await press(control._mapped, control.by_name["exit"], "key:Escape@hold:2000")

        self.assertEqual(self.written, [])
        self.assertEqual(control.live.nothing,
                         t("manager.binding_table.already_does", row="Quit"))

    async def test_a_press_somebody_holds_is_a_question_to_move_it(self) -> None:
        control = self.drawn()

        with self.asking(True) as ask:
            await press(control._mapped, control.by_name["select"], "key:Escape")

        [call] = ask.await_args_list
        self.assertEqual(call.args, (t("manager.binding_table.move_question",
                                       binding="Esc", row="Select"),))
        self.assertEqual(call.kwargs["confirm"], t("word.move"))
        self.assertFalse(call.kwargs["danger"])
        self.assertEqual(self.written, [{"select": ["key:Enter", "key:Escape"], "back": []}])

    async def test_the_question_says_who_has_it_and_what_would_be_left_with_nothing(
            self) -> None:
        control = self.drawn()

        with self.asking(False) as ask:
            await press(control._mapped, control.by_name["select"], "key:Escape")

        [said_here, left] = ask.await_args.kwargs["detail"]
        self.assertEqual(said_here, t("manager.binding_table.mapped_to", binding="Esc",
                                      rows="Back"))
        self.assertEqual(left, t("manager.binding_table.unmapped", rows="Back"))

    async def test_a_row_that_keeps_something_is_not_said_to_be_left_with_nothing(
            self) -> None:
        control = self.drawn({**SHIPPED, "back": ["key:Escape", "key:Backspace"]})

        with self.asking(False) as ask:
            await press(control._mapped, control.by_name["select"], "key:Escape")

        self.assertEqual(len(ask.await_args.kwargs["detail"]), 1)

    async def test_a_move_not_confirmed_changes_nothing(self) -> None:
        control = self.drawn()

        with self.asking(False):
            await press(control._mapped, control.by_name["select"], "key:Escape")

        self.assertEqual(self.written, [])
        self.assertEqual(control.bound, SHIPPED)

    async def test_a_move_says_where_it_came_from_in_the_live_line(self) -> None:
        control = self.drawn()

        with self.asking(True):
            await press(control._mapped, control.by_name["select"], "key:Escape")

        self.assertEqual(control.live.does, t("manager.binding_table.moved",
                                              source="Back", target="Select"))

    async def test_a_press_held_by_two_rows_moves_off_both(self) -> None:
        control = self.drawn({**SHIPPED, "next": ["key:Escape"]})

        with self.asking(True):
            await press(control._mapped, control.by_name["select"], "key:Escape")

        self.assertEqual(self.written, [{"select": ["key:Enter", "key:Escape"],
                                         "next": [], "back": []}])

    async def test_a_write_that_was_not_kept_leaves_the_table_as_it_was(self) -> None:
        control = self.drawn(write=AsyncMock(return_value=False))

        await press(control._mapped, control.by_name["next"], "key:KeyN")

        self.assertEqual(control.bound, SHIPPED)

    async def test_a_press_that_would_slow_scrolling_asks_before_adding(self) -> None:
        control = self.drawn()

        with self.asking(True) as ask:
            await press(control._mapped, control.by_name["exit"], "key:ShiftLeft@hold:1500")

        [call] = ask.await_args_list
        self.assertEqual(call.args, (t("manager.binding_table.hold_question",
                                       binding="Left Shift", row="Quit"),))
        self.assertEqual(call.kwargs["confirm"], t("manager.binding_table.add_hold"))
        self.assertEqual(call.kwargs["detail"], [t(
            "manager.binding_table.slowed", row="Previous", binding="Left Shift")])
        self.assertEqual(self.written, [{"exit": ["key:Escape@hold:1500",
                                                  "key:ShiftLeft@hold:1500"]}])

    async def test_declining_the_slower_scroll_adds_nothing(self) -> None:
        control = self.drawn()

        with self.asking(False):
            await press(control._mapped, control.by_name["exit"], "key:ShiftLeft@hold:1500")

        self.assertEqual(self.written, [])

    async def test_a_hold_already_on_the_key_is_named_as_the_cause(self) -> None:
        control = self.drawn({**SHIPPED, "previous": ["pad:standard/button:14"],
                              "exit": ["key:ShiftLeft@hold:1500"]})

        with self.asking(True) as ask:
            await press(control._mapped, control.by_name["next"], "key:ShiftLeft")

        [call] = ask.await_args_list
        self.assertEqual(call.args, (t("manager.binding_table.add_question",
                                       binding="Left Shift", row="Next"),))
        self.assertEqual(call.kwargs["detail"], [t(
            "manager.binding_table.slowed_by", hold="Left Shift, held 1.5s,",
            holder="Quit", row="Next", binding="Left Shift")])

    async def test_a_press_that_costs_nothing_is_added_without_a_question(self) -> None:
        control = self.drawn()

        with self.asking() as ask:
            await press(control._mapped, control.by_name["exit"], "key:KeyQ@hold:1500")

        ask.assert_not_awaited()

    async def test_removing_what_a_press_can_make_again_asks_nothing(self) -> None:
        control = self.drawn()

        with self.asking() as ask:
            await press(control._remove, control.by_name["previous"], "key:ShiftLeft")

        ask.assert_not_awaited()
        self.assertEqual(self.written, [{"previous": ["pad:standard/button:14"]}])

    async def test_removing_what_nothing_can_make_again_asks_first(self) -> None:
        control = self.drawn({**SHIPPED, "next": ["pad:0/axis:1"]})

        with self.asking(False) as ask:
            await press(control._remove, control.by_name["next"], "pad:0/axis:1")

        ask.assert_awaited_once()
        self.assertEqual(self.written, [])

    async def test_removing_what_nothing_can_make_again_goes_on_a_yes(self) -> None:
        control = self.drawn({**SHIPPED, "next": ["pad:0/axis:1"]})

        with self.asking(True):
            await press(control._remove, control.by_name["next"], "pad:0/axis:1")

        self.assertEqual(self.written, [{"next": []}])

    async def test_a_hold_is_set_by_picking_its_length(self) -> None:
        control = self.drawn()
        menu = MagicMock()

        with self.asking() as ask:
            await press(control._held, control.by_name["select"], "key:Enter", 1500, menu)

        menu.close.assert_called_once_with()
        ask.assert_not_awaited()
        self.assertEqual(self.written, [{"select": ["key:Enter@hold:1500"]}])

    async def test_no_hold_takes_the_hold_off(self) -> None:
        control = self.drawn({**SHIPPED, "select": ["key:Enter@hold:1000"]})

        await press(control._held, control.by_name["select"], "key:Enter@hold:1000", 0,
                    MagicMock())

        self.assertEqual(self.written, [{"select": ["key:Enter"]}])

    async def test_taking_a_hold_off_a_key_another_row_taps_is_a_move(self) -> None:
        """Quit is the hold of Esc and Back is its tap: a held key with no hold is Back's."""
        control = self.drawn()

        with self.asking(False) as ask:
            await press(control._held, control.by_name["exit"], "key:Escape@hold:1500", 0,
                        MagicMock())

        ask.assert_awaited_once()
        self.assertEqual(self.written, [])

    async def test_picking_the_length_it_has_changes_nothing(self) -> None:
        control = self.drawn()

        await press(control._held, control.by_name["exit"], "key:Escape@hold:1500", 1500,
                    MagicMock())

        self.assertEqual(self.written, [])

    async def test_a_hold_given_to_a_key_that_is_a_row_s_tap_is_not_a_clash(self) -> None:
        """Back is the tap of Esc, and Select asking for its hold takes nothing from it."""
        control = self.drawn()

        with self.asking() as ask:
            await press(control._held, control.by_name["select"], "key:Enter", 1000,
                        MagicMock())

        ask.assert_not_awaited()

    async def test_a_hold_on_a_key_somebody_holds_at_another_length_is_a_move(self) -> None:
        control = self.drawn()

        with self.asking(True) as ask:
            await press(control._held, control.by_name["select"], "key:Enter", 0, MagicMock())
            await press(control._mapped, control.by_name["select"], "key:Escape@hold:2500")

        [call] = ask.await_args_list
        self.assertIn(t("manager.binding_table.one_hold", binding="Esc"),
                      call.kwargs["detail"][0])
        self.assertEqual(self.written[-1]["exit"], [])

    async def test_a_revert_that_takes_nothing_from_a_row_just_goes_back(self) -> None:
        control = self.drawn({**SHIPPED, "next": ["key:KeyN"]})

        with self.asking() as ask:
            await press(control._revert, control.by_name["next"])

        ask.assert_not_awaited()
        self.assertEqual(self.written, [{"next": ["key:ShiftRight"]}])

    async def test_a_revert_that_takes_a_press_from_another_row_asks_first(self) -> None:
        control = self.drawn({**SHIPPED, "back": [], "select": ["key:Enter", "key:Escape"]})

        with self.asking(True) as ask:
            await press(control._revert, control.by_name["back"])

        [call] = ask.await_args_list
        self.assertEqual(call.args, (t("manager.binding_table.revert_question",
                                       row="Back"),))
        self.assertEqual(call.kwargs["confirm"], t("word.revert"))
        self.assertEqual(self.written, [{"back": ["key:Escape"], "select": ["key:Enter"]}])

    async def test_a_revert_not_confirmed_changes_nothing(self) -> None:
        control = self.drawn({**SHIPPED, "back": [], "select": ["key:Enter", "key:Escape"]})

        with self.asking(False):
            await press(control._revert, control.by_name["back"])

        self.assertEqual(self.written, [])

    async def test_a_row_with_no_default_is_reverted_to_nothing(self) -> None:
        control = self.drawn({**SHIPPED, "take_picture": ["key:KeyP"]})

        await press(control._revert, control.by_name["take_picture"])

        self.assertEqual(self.written, [{"take_picture": []}])

    def _line(self, control: Table) -> list[str]:
        return [one.text for one in control.line.descendants() if isinstance(one, ui.label)]

    def _send(self, control: Table) -> list[str]:
        return [one.text for one in control.send.descendants() if isinstance(one, ui.label)]

    async def test_a_row_listening_is_marked_and_the_line_says_what_for(self) -> None:
        control = self.drawn()

        said(control.line, "listen", "back")

        self.assertIn("manager-binding-row--listening", control._rows["back"].classes)
        self.assertNotIn("manager-binding-row--listening", control._rows["next"].classes)
        self.assertEqual(self._line(control), ["Press a button or key for", "Back"])
        self.assertEqual(self._send(control)[-1], "Click away to stop")

    async def test_a_row_that_cannot_be_held_says_the_same_beside_the_switch(self) -> None:
        control = self.drawn()

        said(control.line, "listen", "take_picture")

        self.assertEqual(self._send(control)[-1], "Click away to stop")

    async def test_listening_moves_from_one_row_to_the_next(self) -> None:
        control = self.drawn()

        said(control.line, "listen", "back")
        said(control.line, "listen", "next")

        self.assertEqual([name for name, cells in control._rows.items()
                          if "manager-binding-row--listening" in cells.classes], ["next"])
        self.assertEqual(self._line(control)[1], "Next")

    async def test_clicking_away_goes_back_to_waiting_for_a_press(self) -> None:
        control = self.drawn()
        said(control.line, "heard", "key:Escape")
        said(control.line, "listen", "back")

        said(control.line, "listen", "")

        self.assertFalse([name for name, cells in control._rows.items()
                          if "manager-binding-row--listening" in cells.classes])
        self.assertEqual(self._line(control),
                         ["Press anything to see what it does"])

    async def test_listening_puts_out_what_the_last_press_lit(self) -> None:
        control = self.drawn()
        said(control.line, "heard", "key:Escape")

        said(control.line, "listen", "next")

        self.assertFalse([name for name, label in control._labels.items()
                          if "manager-binding-lit" in label.classes])
        self.assertEqual(control.lit, "")

    async def test_a_row_still_listens_after_the_table_is_drawn_again(self) -> None:
        """Taking a binding off the listening row redraws every row under it."""
        control = self.drawn()
        said(control.line, "listen", "back")

        control.draw()

        self.assertIn("manager-binding-row--listening", control._rows["back"].classes)

    async def test_a_name_that_is_no_row_listens_to_nothing(self) -> None:
        control = self.drawn()

        said(control.line, "listen", "nonsense")

        self.assertEqual(control.listening, "")

    @staticmethod
    def _captured(row: str, token: str) -> Any:
        return SimpleNamespace(args={"row": row, "token": token})

    async def test_what_a_row_hears_is_added_and_the_listening_ends(self) -> None:
        control = self.drawn()
        said(control.line, "listen", "next")

        with self.asking() as ask:
            await press(control._captured, self._captured("next", "key:KeyN"))

        ask.assert_not_awaited()
        self.assertEqual(self.written, [{"next": ["key:ShiftRight", "key:KeyN"]}])
        self.assertEqual(control.listening, "")
        self.assertNotIn("manager-binding-row--listening", control._rows["next"].classes)

    async def test_hearing_nothing_ends_the_listening_and_writes_nothing(self) -> None:
        control = self.drawn()
        said(control.line, "listen", "next")

        await press(control._captured, self._captured("next", ""))

        self.assertEqual(control.listening, "")
        self.assertEqual(self.written, [])

    async def test_a_standard_button_is_a_move_from_the_numbered_one_a_row_holds(self) -> None:
        """A numbered binding fires on a pad that reports the standard layout, so Button 0
        pressed for Back takes the button Select holds as `pad:0/button:0`."""
        control = self.drawn({**SHIPPED, "select": ["key:Enter", "pad:0/button:0"]})

        with self.asking(True) as ask:
            await press(control._captured, self._captured("back", "pad:standard/button:0"))

        [call] = ask.await_args_list
        self.assertEqual(call.args, (t("manager.binding_table.move_question",
                                       binding="Button 0", row="Back"),))
        self.assertEqual(call.kwargs["detail"][0], t(
            "manager.binding_table.mapped_to", binding="Button 0", rows="Select"))
        self.assertEqual(self.written, [{"select": ["key:Enter"],
                                         "back": ["key:Escape", "pad:standard/button:0"]}])

    async def test_a_numbered_button_is_a_move_from_the_standard_one_a_row_holds(self) -> None:
        control = self.drawn({**SHIPPED, "select": ["key:Enter", "pad:standard/button:0"]})

        with self.asking(False) as ask:
            await press(control._captured, self._captured("back", "pad:2/button:0"))

        [call] = ask.await_args_list
        self.assertEqual(call.args, (t("manager.binding_table.move_question",
                                       binding="Button 0", row="Back"),))
        self.assertEqual(self.written, [])

    async def test_a_press_is_named_and_the_rows_that_hold_it_are_lit(self) -> None:
        control = self.drawn()

        said(control.line, "heard", "key:Escape")

        self.assertEqual(control.live.name, "Esc")
        self.assertFalse(control.live.pad)
        self.assertEqual(control.live.does, t(
            "manager.binding_table.and_last", most="Back",
            last=t("manager.binding_table.when_held", row="Quit")))
        lit = {name for name, label in control._labels.items()
               if "manager-binding-lit" in label.classes}
        self.assertEqual(lit, {"back", "exit"})

    async def test_the_chips_of_the_press_are_lit_with_their_rows(self) -> None:
        control = self.drawn()

        said(control.line, "heard", "key:ShiftLeft")

        lit = {key for key, chip in control._chips.items()
               if "manager-binding-lit" in chip.classes}
        self.assertEqual(lit, {("previous", "key:ShiftLeft")})

    async def test_a_press_nothing_holds_says_it_does_nothing_here(self) -> None:
        control = self.drawn()

        said(control.line, "heard", "key:KeyZ")

        self.assertEqual(control.live.does, "")
        self.assertIn(t("manager.binding_table.no_action"), _labels(self.body))

    async def test_a_controller_press_is_drawn_as_one(self) -> None:
        control = self.drawn()

        said(control.line, "heard", "pad:standard/button:14")

        self.assertEqual(control.live.name, "D-pad Left")
        self.assertTrue(control.live.pad)
        self.assertEqual(control.live.does, "Previous")

    async def test_the_same_button_on_any_numbered_pad_is_lit(self) -> None:
        control = self.drawn({**SHIPPED, "select": ["pad:0/button:3"]})

        said(control.line, "heard", "pad:2/button:3")

        self.assertEqual(control.live.does, "Select")

    async def test_a_press_on_a_numbered_pad_reads_as_the_binding_does(self) -> None:
        control = self.drawn({**SHIPPED, "select": ["pad:0/button:3"]})

        said(control.line, "heard", "pad:2/button:3")

        self.assertEqual(control.live.name, "Button 3")
        self.assertEqual(_chips(_wrappers(self.body)["Select"]), ["Button 3"])

    async def test_nothing_is_said_for_an_empty_press(self) -> None:
        control = self.drawn()

        said(control.line, "heard", "")

        self.assertEqual(control.live.name, "")

    async def test_a_table_installs_one_script_for_the_line_and_one_for_listening(
            self) -> None:
        with patch.object(ui, "run_javascript") as run:
            with self.body:
                control = Table(ROWS, SHIPPED, write=_kept, source=SOURCE)

        self.assertEqual(run.call_count, 2)
        self.assertEqual({one.type for one in control.line._event_listeners.values()},
                         {"heard", "released", "listen", "captured", "taken", "visible", "touched"})

    async def test_a_table_that_is_only_shown_listens_for_no_row(self) -> None:
        with patch.object(ui, "run_javascript") as run:
            with self.body:
                control = Table(ROWS, SHIPPED, write=_kept, source=SOURCE, writable=False)

        self.assertEqual(run.call_count, 1)
        self.assertEqual({one.type for one in control.line._event_listeners.values()},
                         {"heard", "released", "taken", "visible", "touched"})

    async def test_drawing_after_the_page_is_gone_draws_nothing(self) -> None:
        control = self.drawn()
        control.container.delete()

        control.draw()

        self.assertEqual(list(control.grid.default_slot.children), [])


class ASharingTable(unittest.IsolatedAsyncioTestCase):
    """Where the program does everything a press is mapped to, a press another row holds
    is used for both, and nothing is slowed or left without."""

    def setUp(self) -> None:
        self.body = ui.column()
        self.written: list[dict[str, list[str]]] = []

    async def write(self, changes: dict[str, list[str]]) -> bool:
        self.written.append(changes)
        return True

    def drawn(self, bound: dict[str, Any] | None = None, **kwargs: Any) -> Table:
        control, _ = _drawn(bound, into=self.body, write=self.write, shares=True, **kwargs)
        return control

    def asking(self, *answers: Any) -> Any:
        return patch("manager.kit.confirm.ask", new=AsyncMock(side_effect=answers))

    async def test_a_press_somebody_holds_can_be_used_for_both(self) -> None:
        control = self.drawn()

        with self.asking(True) as ask:
            await press(control._mapped, control.by_name["select"], "key:Escape")

        self.assertEqual(ask.await_args.kwargs["confirm"],
                         t("manager.binding_table.share", count=1))
        self.assertNotIn("second", ask.await_args.kwargs)
        self.assertEqual(self.written, [{"select": ["key:Enter", "key:Escape"]}])
        self.assertEqual(control.bound["back"], ["key:Escape"])

    async def test_a_press_three_rows_hold_is_used_for_all(self) -> None:
        control = self.drawn({**SHIPPED, "next": ["key:Escape"]})

        with self.asking(False) as ask:
            await press(control._mapped, control.by_name["select"], "key:Escape")

        self.assertEqual(ask.await_args.kwargs["confirm"],
                         t("manager.binding_table.share", count=2))

    async def test_the_question_is_about_sharing_and_there_is_no_move(self) -> None:
        control = self.drawn()

        with self.asking(False) as ask:
            await press(control._mapped, control.by_name["select"], "key:Escape")

        self.assertEqual(ask.await_args.args[0], t(
            "manager.binding_table.share_question", binding="Esc", row="Select"))
        self.assertEqual(ask.await_args.kwargs["detail"],
                         [t("manager.binding_table.mapped_to", binding="Esc", rows="Back")])

    async def test_a_cancelled_question_changes_nothing(self) -> None:
        control = self.drawn()

        with self.asking(False):
            await press(control._mapped, control.by_name["select"], "key:Escape")

        self.assertEqual(self.written, [])
        self.assertEqual(control.bound, SHIPPED)

    async def test_a_revert_is_not_asked_about_and_leaves_the_other_row_its_press(
            self) -> None:
        control = self.drawn({**SHIPPED, "select": ["key:Escape"]})

        with self.asking() as ask:
            await press(control._revert, control.by_name["select"])

        ask.assert_not_awaited()
        self.assertEqual(self.written, [{"select": ["key:Enter"]}])
        self.assertEqual(control.bound["back"], ["key:Escape"])

    async def test_a_press_the_caller_refuses_is_said_and_not_mapped(self) -> None:
        control = self.drawn(refuses=lambda token: "No number for that")

        with self.asking() as ask, patch.object(ui, "notify") as notify:
            await press(control._mapped, control.by_name["next"], "key:KeyN")

        ask.assert_not_awaited()
        notify.assert_called_once_with("No number for that", type="warning")
        self.assertEqual(self.written, [])

    async def test_a_chip_whose_press_two_rows_share_says_who_else_has_it(self) -> None:
        self.drawn({**SHIPPED, "select": ["key:Escape"]})

        tips = [one.text for one in _wrappers(self.body)["Back"].descendants()
                if isinstance(one, ui.tooltip)]

        self.assertIn(t("manager.binding_table.also_does", others="Select"), tips)

    async def test_a_chip_kept_is_shown_with_no_way_to_remove_it(self) -> None:
        kept = Row("tilt", "Tilt", holds=False,
                   kept=(table.Kept("Controller button", "Set elsewhere"),))
        control = self.drawn({"tilt": []}, rows=(kept,))

        wrapper = _wrappers(self.body)["Tilt"]

        self.assertEqual(_chips(wrapper), ["Controller button"])
        self.assertNotIn(t("manager.binding_table.nothing_mapped"), _chips(wrapper))
        self.assertEqual(control.bound["tilt"], [])


if __name__ == "__main__":
    unittest.main()
