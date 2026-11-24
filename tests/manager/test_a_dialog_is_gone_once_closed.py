"""A dialog from `manager/kit/dialog.py` is deleted once the browser says it has hidden, and
what its caller reads after `await` is still there to read."""

from __future__ import annotations

import asyncio
import unittest
from collections.abc import Callable
from typing import Any
from unittest import mock

from nicegui import ui

from manager.kit import dialog as frame
from manager.kit import walks
from manager.library import mediaview, text_editor
from manager.remote import remote
from tests.support.clicks import said

# Made at import, outside any task, where NiceGUI still hands out the script's own page.
_PAGE = ui.element()
TIMES = 5


async def _answer(box: ui.dialog) -> Any:
    return await box


async def _asked(*, persistent: bool = False, dismissed: bool = False
                 ) -> tuple[ui.dialog, Any, Any]:
    """A name asked for, typed, answered or dismissed, and hidden: the dialog, its field,
    and what the await gave back."""
    with _PAGE, frame.opened("Name", persistent=persistent) as box:
        field = frame.field()
    waiting = asyncio.create_task(_answer(box))
    await asyncio.sleep(0)
    said(field, "update:value", "Typed")
    if dismissed:
        said(box, "update:modelValue", False)
    else:
        box.submit(field.value)
    answered = await waiting
    said(box, "hide")
    return box, field, answered


def _elements() -> int:
    return len(_PAGE.client.elements)


def _dialogs() -> set[ui.dialog]:
    return {one for one in _PAGE.client.layout.descendants() if isinstance(one, ui.dialog)}


class ADialogIsGoneOnceClosed(unittest.TestCase):
    def _again_and_again(self, **asked: Any) -> None:
        async def run() -> None:
            before = _elements()
            for _ in range(TIMES):
                box, _field, _answered = await _asked(**asked)
                self.assertTrue(box.is_deleted)
            self.assertEqual(before, _elements())
        asyncio.run(run())

    def test_opening_and_closing_one_leaves_the_page_as_it_was(self) -> None:
        self._again_and_again()

    def test_a_persistent_one_goes_too(self) -> None:
        self._again_and_again(persistent=True)

    def test_one_dismissed_by_escape_or_a_click_outside_goes_too(self) -> None:
        self._again_and_again(dismissed=True)

    def test_a_field_read_after_the_await_still_reads(self) -> None:
        box, field, answered = asyncio.run(_asked())

        self.assertTrue(box.is_deleted)
        self.assertEqual(("Typed", "Typed"), (answered, field.value))

    def test_a_hide_listener_added_before_it_opens_still_runs(self) -> None:
        heard: list[bool] = []

        async def run() -> ui.dialog:
            with _PAGE, frame.opened("Name") as box:
                frame.field()
            box.on("hide", lambda: heard.append(box.is_deleted))
            waiting = asyncio.create_task(_answer(box))
            await asyncio.sleep(0)
            box.submit(True)
            await waiting
            said(box, "hide")
            return box

        box = asyncio.run(run())

        self.assertEqual([False], heard)
        self.assertTrue(box.is_deleted)

    def test_one_opened_again_before_it_hides_stays(self) -> None:
        async def run() -> tuple[bool, bool]:
            with _PAGE, frame.opened("Name") as box:
                frame.field()
            waiting = asyncio.create_task(_answer(box))
            await asyncio.sleep(0)
            box.submit(True)
            await waiting
            box.open()
            said(box, "hide")
            kept = not box.is_deleted
            box.close()
            said(box, "hide")
            return kept, box.is_deleted

        self.assertEqual((True, True), asyncio.run(run()))


class WhatBlocksADismissal(unittest.TestCase):
    def _props_of(self, **asked: Any) -> frame.Dialog:
        with _PAGE, frame.opened("Name", **asked) as box:
            frame.field()
        self.addCleanup(box.delete)
        return box

    def test_ignoring_the_outside_click_leaves_escape_working(self) -> None:
        box = self._props_of(ignore_outside_click=True)

        self.assertIn("no-backdrop-dismiss", box.props)
        self.assertNotIn("persistent", box.props)

    def test_persistent_blocks_both(self) -> None:
        box = self._props_of(persistent=True)

        self.assertIn("persistent", box.props)
        self.assertNotIn("no-backdrop-dismiss", box.props)

    def test_what_escape_does_runs_when_the_browser_says_escape(self) -> None:
        box = self._props_of(ignore_outside_click=True)
        heard: list[str] = []
        frame.on_escape(box, lambda: heard.append("escape"))

        said(box, "escapeKey")

        self.assertEqual(["escape"], heard)

    def test_what_escape_does_does_not_run_for_a_click_outside(self) -> None:
        box = self._props_of(ignore_outside_click=True)
        heard: list[str] = []
        frame.on_escape(box, lambda: heard.append("escape"))

        said(box, "click")

        self.assertEqual([], heard)


class EscapeInAWalk(unittest.TestCase):
    def _left_by_escape(self, walking: bool) -> Any:
        """What `await box` gives when the browser says Escape and then that it hid."""
        async def run() -> Any:
            with _PAGE, frame.opened("Name", ignore_outside_click=True) as box:
                with frame.footer():
                    frame.leave(box, walking)
            waiting = asyncio.create_task(_answer(box))
            await asyncio.sleep(0)
            said(box, "escapeKey")
            said(box, "update:modelValue", False)
            return await waiting

        return asyncio.run(run())

    def test_escape_stops_the_whole_run(self) -> None:
        self.assertEqual(walks.STOPPED, self._left_by_escape(True))

    def test_a_dialog_asked_alone_answers_nothing_for_escape(self) -> None:
        self.assertIsNone(self._left_by_escape(False))

    def test_a_click_outside_in_a_walk_is_not_escape(self) -> None:
        async def run() -> Any:
            with _PAGE, frame.opened("Name") as box:
                with frame.footer():
                    frame.leave(box, True)
            waiting = asyncio.create_task(_answer(box))
            await asyncio.sleep(0)
            said(box, "update:modelValue", False)
            return await waiting

        self.assertIsNone(asyncio.run(run()))


class OnesWithTheirOwnCardGoToo(unittest.TestCase):
    def setUp(self) -> None:
        for patcher in (mock.patch.object(ui, "run_javascript"),
                        mock.patch.object(remote, "where_to_find_it",
                                          return_value="http://10.0.0.9:8080/remote"),
                        mock.patch.object(remote, "where_to_scan",
                                          return_value="http://10.0.0.9:8080/remote"),
                        mock.patch.object(remote.addresses, "resolving",
                                          return_value=False)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _each_time(self, show: Callable[[], Any]) -> None:
        """Shown, dismissed as Escape or a click outside does, and hidden, again and again."""
        before = _elements()
        for _ in range(TIMES):
            was = _dialogs()
            with _PAGE:
                show()
            box = next(one for one in _dialogs() - was if one.value)
            said(box, "update:modelValue", False)
            said(box, "hide")
            self.assertTrue(box.is_deleted)
        self.assertEqual(before, _elements())

    def test_an_enlarged_picture(self) -> None:
        self._each_time(lambda: mediaview.open_image("/media/wheel.png", "Wheel"))

    def test_an_enlarged_video(self) -> None:
        self._each_time(lambda: mediaview.open_viewer("/media/loading.mp4", "loading",
                                                      "Loading"))

    def test_a_text_file_shown_whole(self) -> None:
        self._each_time(lambda: text_editor.show_whole("notes.txt", "A line"))

    def test_the_remote_sheet_for_a_game(self) -> None:
        self._each_time(lambda: remote._game_sheet(
            {"id": "g-1", "name": "Alpha"}, {"collections": []}, lambda: None, lambda: None))

    def test_the_remote_address_for_a_phone(self) -> None:
        with _PAGE:
            remote.invite([])
        way_in = next(one for one in _PAGE.descendants() if "manager-invite" in one.classes)

        self._each_time(lambda: said(way_in, "click"))


if __name__ == "__main__":
    unittest.main()
