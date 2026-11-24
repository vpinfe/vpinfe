"""A welcome step is centered, and a choice that carries pictures is drawn as tiles on it
and as rows anywhere else. Proven with a fake flow, both through `ext_action` (which
never draws VPinFE's mark) and through the dialog host when core asks for it."""

from __future__ import annotations

import asyncio
import contextlib
import unittest
from collections.abc import Callable
from typing import Any
from unittest import mock

from nicegui import core, ui

from manager.extensions import ext_action
from manager.kit import appearance, offload, wizard
from tests.support.clicks import said
from tests.support.stylesheet import declarations

# Made at import, outside any task, where NiceGUI still hands out the script's own page.
_PAGE = ui.element()

WELCOME = {
    "step": "welcome", "welcome": True, "title": "Welcome", "help": "A few minutes",
    "fields": [{
        "key": "play", "type": "choice", "label": "How do you play?", "value": "desktop",
        "choices": [["desktop", "Desktop", "One screen", "desktop"],
                   ["cabinet", "Cabinet", "Several screens", "cabinet"]]}],
}
PLAIN = {**WELCOME, "step": "tables", "welcome": False, "title": "Where are your tables?"}


def _ran(run: Callable[[], Any]) -> Any:
    async def inside() -> Any:
        was = core.loop
        core.loop = asyncio.get_running_loop()
        try:
            return await run()
        finally:
            core.loop = was

    return asyncio.run(inside())


def _calls(first: dict[str, Any]) -> wizard.Calls:
    async def first_call(step: str | None = None) -> dict:
        return first

    async def never(*_args: Any) -> dict:
        raise AssertionError("no test here goes past the first step")

    return wizard.Calls(first_call, never, never, never, never)


async def _settled() -> None:
    for _ in range(8):
        await asyncio.sleep(0)


async def _drawn(opening: Callable[[], Any]) -> tuple[asyncio.Task, ui.dialog]:
    was = set(_PAGE.client.layout.descendants())

    async def go() -> None:
        with _PAGE:
            await opening()

    task = asyncio.create_task(go())
    await _settled()
    dialog = next(one for one in set(_PAGE.client.layout.descendants()) - was
                  if isinstance(one, ui.dialog))
    return task, dialog


async def _finished(task: asyncio.Task) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def _dialog(first: dict[str, Any], *, mark: bool = False) -> tuple[asyncio.Task, ui.dialog]:
    return await _drawn(lambda: wizard.open_dialog(
        label="Test", calls=_calls(first), under="test.action", mark=mark))


async def _through_ext_action(first: dict[str, Any]) -> tuple[asyncio.Task, ui.dialog]:
    class Client:
        def ext_post(self, _path: str, _body: dict, _timeout: int | None = None) -> dict:
            return first

    async def direct(callback: Callable[..., Any], *args: Any) -> Any:
        return callback(*args)

    with mock.patch.object(ext_action, "ApiClient", Client), \
            mock.patch.object(offload, "io", direct):
        return await _drawn(lambda: ext_action.open_action(
            "demo", {"key": "setup", "label": "Setup", "base": "/setup"}))


def _classed(root: ui.element, name: str) -> list[ui.element]:
    return [one for one in root.descendants() if name in one.classes]


def _marks(root: ui.element) -> list[ui.element]:
    return [one for one in root.descendants()
            if isinstance(one, ui.image) and one._props.get("src") == appearance.LOGO]


class WelcomeTests(unittest.TestCase):
    def test_a_welcome_through_ext_action_is_centered_with_its_question_and_no_mark(self) -> None:
        async def run() -> tuple[bool, list[str], list[ui.element]]:
            task, dialog = await _through_ext_action(WELCOME)
            scroll = _classed(dialog, "manager-wizard-scroll")[0]
            found = ("manager-wizard--welcome" in scroll.classes,
                     [one.text for one in _classed(dialog, "manager-wizard-question")
                      if isinstance(one, ui.label)],
                     _marks(dialog))
            await _finished(task)
            return found

        centered, questions, marks = _ran(run)

        self.assertTrue(centered)
        self.assertEqual(["How do you play?"], questions)
        self.assertEqual([], marks)

    def test_the_mark_is_drawn_when_core_asks_for_it(self) -> None:
        async def run() -> int:
            task, dialog = await _dialog(WELCOME, mark=True)
            found = len(_marks(dialog))
            await _finished(task)
            return found

        self.assertEqual(1, _ran(run))

    def test_a_step_that_is_not_a_welcome_is_not_centered_and_draws_no_mark(self) -> None:
        async def run() -> tuple[bool, list[ui.element], list[ui.element]]:
            task, dialog = await _dialog(PLAIN, mark=True)
            scroll = _classed(dialog, "manager-wizard-scroll")[0]
            found = ("manager-wizard--welcome" in scroll.classes,
                     _marks(dialog), _classed(dialog, "manager-wizard-question"))
            await _finished(task)
            return found

        centered, marks, questions = _ran(run)

        self.assertFalse(centered)
        self.assertEqual([], marks)
        self.assertEqual([], questions)

    def test_the_welcome_title_takes_the_display_size_and_is_centered(self) -> None:
        title = declarations(".manager-wizard--welcome .manager-dialog-title.manager-wizard-title")
        self.assertEqual("var(--fs-display)", title.get("font-size"))
        self.assertEqual("center", declarations(".manager-wizard--welcome").get("text-align"))


class PictureTests(unittest.TestCase):
    _field = {"key": "pick", "type": "choice", "label": "Pick", "value": "",
              "choices": [["one", "One", "First", "desktop"],
                         ["two", "Two", "", "folder"],
                         ["three", "Three", "", "img:https://example.test/x.png"],
                         ["four", "Four", "", ""]]}

    def _drawn(self, field: dict[str, Any], *, welcome: bool = False,
               errors: dict[str, Any] | None = None, beside: tuple[dict[str, Any], ...] = ()
              ) -> tuple[dict[str, Any], dict[str, tuple[str, Any]], ui.element]:
        from manager.kit.api import ApiClient

        values: dict[str, Any] = {}
        with _PAGE:
            holder = ui.column()
            with holder:
                held = wizard._controls([field, *beside], values,
                                        client=mock.Mock(spec=ApiClient),
                                        errors=errors or {}, welcome=welcome)
        return values, held, holder

    def _rows(self, holder: ui.element) -> list[ui.element]:
        return _classed(holder, "manager-wizard-pick")

    def test_a_picture_name_draws_core_art_or_an_icon_and_nothing_an_extension_drew(self) -> None:
        _values, _held, holder = self._drawn(self._field)

        drawn = []
        for row in self._rows(holder):
            art = [one for one in row.descendants() if isinstance(one, ui.html)]
            icons = [one for one in row.descendants() if isinstance(one, ui.icon)
                    and "manager-wizard-icon" in one.classes]
            drawn.append(("art" if art and "<svg" in art[0].content else "",
                          icons[0]._props["name"] if icons else ""))

        self.assertEqual([("art", ""), ("", "folder"), ("", ""), ("", "")], drawn)

    def test_a_welcome_lays_its_pictures_as_tiles_in_a_grid(self) -> None:
        _values, _held, holder = self._drawn(self._field, welcome=True)

        group = _classed(holder, "manager-wizard-picks")[0]
        self.assertIn("manager-wizard-picks--tiles", group.classes)
        self.assertTrue(all("manager-wizard-pick--tile" in row.classes
                            for row in self._rows(holder)))
        self.assertEqual("grid", declarations(".manager-wizard-picks--tiles").get("display"))
        self.assertEqual(["Pick"], [one.text for one in _classed(holder, "manager-wizard-question")
                                    if isinstance(one, ui.label)])

    def test_any_other_step_lays_them_as_rows_and_draws_no_question(self) -> None:
        _values, _held, holder = self._drawn(self._field)

        group = _classed(holder, "manager-wizard-picks")[0]
        self.assertNotIn("manager-wizard-picks--tiles", group.classes)
        self.assertFalse(any("manager-wizard-pick--tile" in row.classes
                             for row in self._rows(holder)))
        self.assertEqual([], _classed(holder, "manager-wizard-question"))
        self.assertEqual("column", declarations(".manager-wizard-picks").get("flex-direction"))
        self.assertEqual("Pick", group.props["aria-label"])

    def test_rows_beside_another_field_keep_the_question_left_and_unspaced(self) -> None:
        name = {"key": "name", "type": "string", "label": "Name", "value": ""}

        _values, _held, holder = self._drawn(self._field, beside=(name,))

        questions = [one for one in _classed(holder, "manager-wizard-question")
                     if isinstance(one, ui.label)]
        self.assertEqual(["Pick"], [one.text for one in questions])
        self.assertEqual("Pick", _classed(holder, "manager-wizard-picks")[0].props["aria-label"])
        rules = declarations(".manager-wizard--welcome .manager-wizard-question")
        self.assertEqual("center", rules.get("text-align"))
        self.assertNotIn("text-align",
                         declarations(".manager-wizard-question", whole=True))

    def test_a_choice_whose_pictures_are_all_null_stays_a_radio(self) -> None:
        """`null` is none; read as a string it is the word, and a picture to draw."""
        field = {**self._field, "choices": [["a", "A", "", None], ["b", "B", "", None]]}

        _values, _held, holder = self._drawn(field)

        self.assertTrue(any(isinstance(one, ui.radio) for one in holder.descendants()))
        self.assertEqual([], self._rows(holder))

    def test_the_rows_win_the_source_row_rules_that_come_after_them(self) -> None:
        """Both rules the picks must beat sit later in the sheet at (0,1,0) and (0,2,0), so
        the picks' own are written one class deeper."""
        row = declarations(".manager-wizard-picks .manager-wizard-pick")
        tile = declarations(".manager-wizard-picks .manager-wizard-pick--tile")
        name = declarations(".manager-wizard-picks .manager-wizard-pick .manager-source-name")

        self.assertEqual("10px 14px", row.get("padding"))
        self.assertEqual("16px 12px 14px", tile.get("padding"))
        self.assertEqual("normal", name.get("white-space"))
        self.assertEqual("100%", declarations(".manager-wizard-picks").get("width"))

    def test_both_layouts_use_the_pick_row_edge_and_wash(self) -> None:
        for welcome in (False, True):
            _values, _held, holder = self._drawn({**self._field, "value": "two"}, welcome=welcome)

            rows = self._rows(holder)
            self.assertTrue(all("manager-source-row--pick" in row.classes for row in rows))
            self.assertEqual([False, True, False, False],
                             ["manager-source-row--chosen" in row.classes for row in rows])

    def test_a_choice_with_no_pictures_stays_a_radio(self) -> None:
        plain = {**self._field, "choices": [["a", "A", "", ""], ["b", "B", ""]]}

        _values, _held, holder = self._drawn(plain)

        self.assertTrue(any(isinstance(one, ui.radio) for one in holder.descendants()))
        self.assertEqual([], self._rows(holder))

    def test_picking_a_row_moves_the_wash_and_the_answer(self) -> None:
        values, held, holder = self._drawn({**self._field, "value": "one"})
        rows = self._rows(holder)

        said(rows[1], "click")

        self.assertEqual("two", values["pick"])
        wizard._sync(values, held)
        self.assertEqual("two", values["pick"])
        self.assertEqual([False, True, False, False],
                         ["manager-source-row--chosen" in row.classes for row in rows])
        self.assertEqual(["false", "true", "false", "false"],
                         [row.props["aria-checked"] for row in rows])
        marks = [one for row in rows for one in row.descendants()
                if "manager-wizard-pick-mark" in one.classes]
        self.assertEqual([False, True, False, False], [one.visible for one in marks])

    def test_the_space_bar_picks_a_row_too(self) -> None:
        values, _held, holder = self._drawn({**self._field, "value": "one"})
        rows = self._rows(holder)

        said(rows[3], "keydown.space.prevent")

        self.assertEqual("four", values["pick"])

    def test_a_refusal_is_drawn_under_the_pictures(self) -> None:
        _values, _held, holder = self._drawn({**self._field, "value": ""},
                                             errors={"pick": "Pick one to go on"})

        self.assertEqual(["Pick one to go on"],
                         [one.text for one in holder.descendants()
                          if isinstance(one, ui.label) and "text-negative" in one.classes])


if __name__ == "__main__":
    unittest.main()
