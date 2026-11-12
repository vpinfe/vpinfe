"""A path field with a default holds it, and Browse opens where the field points."""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import AsyncMock, patch

from nicegui import ui

from manager.kit import folder_picker, panel, verbs
from manager.settings import settings
from tests.manager.test_a_field_left_as_it_was import _drawn, _input, _ran, _settled
from tests.support.clicks import said

# `pick_path` without the page it would run under: there is none here.
_pick_path = getattr(folder_picker.pick_path, "__wrapped__")  # noqa: B009


class Enclosing(unittest.TestCase):
    def test_the_folder_that_holds_a_path_is_the_one_above(self) -> None:
        self.assertEqual(folder_picker.enclosing("/a/b/c"), "/a/b")
        self.assertEqual(folder_picker.enclosing("/a/b/c/"), "/a/b")

    def test_the_top_of_a_drive_or_root_is_kept_with_its_separator(self) -> None:
        self.assertEqual(folder_picker.enclosing("/a"), "/")
        self.assertEqual(folder_picker.enclosing("C:\\a"), "C:\\")

    def test_a_bare_name_has_nothing_above_it(self) -> None:
        self.assertEqual(folder_picker.enclosing("a"), "")


class BrowseStart(unittest.IsolatedAsyncioTestCase):
    async def _opened(self, current: str, default: str) -> tuple[str, bool]:
        with patch.object(folder_picker, "pick_path", new=AsyncMock(return_value=None)) as pick:
            browsed = settings._browse_path(lambda *_a: {}, "dir", (), default)
            assert browsed is not None
            await browsed(current)
        return pick.call_args.args[1], pick.call_args.kwargs["nearest"]

    async def test_a_field_holding_its_default_opens_at_the_nearest_folder_of_it(self) -> None:
        self.assertEqual(await self._opened("", "/cfg/backups"), ("/cfg/backups", True))
        self.assertEqual(await self._opened("/cfg/backups", "/cfg/backups"),
                         ("/cfg/backups", True))

    async def test_a_field_holding_another_path_opens_there_and_climbs_for_nothing(self) -> None:
        self.assertEqual(await self._opened("/mnt/x", "/cfg/backups"), ("/mnt/x", False))

    async def test_a_field_with_no_default_opens_where_it_always_did(self) -> None:
        self.assertEqual(await self._opened("", ""), ("", False))


class StopError(Exception):
    pass


class Climb(unittest.IsolatedAsyncioTestCase):
    async def test_a_default_that_is_not_there_opens_at_the_folder_above_it(self) -> None:
        asked: list[str] = []

        def listing(path: str, kind: str, suffixes: tuple[str, ...], *_rest: Any) -> dict:
            asked.append(path)
            if path != "/cfg":
                raise FileNotFoundError(path)
            return {"path": "/cfg", "parent": "/", "folders": [], "roots": []}

        def opened(*_a: Any, **_k: Any) -> Any:
            raise StopError

        with patch.object(folder_picker.frame, "opened", side_effect=opened):
            with self.assertRaises(StopError):
                await _pick_path(listing, "/cfg/backups/x", nearest=True)
        self.assertEqual(asked, ["/cfg/backups/x", "/cfg/backups", "/cfg"])

    async def test_without_nearest_a_missing_path_falls_back_to_the_start(self) -> None:
        asked: list[str] = []

        def listing(path: str, kind: str, suffixes: tuple[str, ...], *_rest: Any) -> dict:
            asked.append(path)
            if path:
                raise FileNotFoundError(path)
            return {"path": "/", "parent": "", "folders": [], "roots": []}

        def opened(*_a: Any, **_k: Any) -> Any:
            raise StopError

        with patch.object(folder_picker.frame, "opened", side_effect=opened):
            with self.assertRaises(StopError):
                await _pick_path(listing, "/cfg/backups")
        self.assertEqual(asked, ["/cfg/backups", ""])


DEFAULT = "/cfg/backups"
OTHER = "/mnt/elsewhere"


def _field(value: str, saved: list[str], **extra: Any) -> ui.element:
    return _drawn(value, saved, browse=AsyncMock(), default=DEFAULT, **extra)


def _buttons(holder: ui.element, icon: str) -> list[ui.button]:
    return [one for one in holder.descendants()
            if isinstance(one, ui.button) and one.props.get("icon") == icon]


class AFieldWithADefault(unittest.TestCase):
    def test_it_holds_its_default_until_given_another_path(self) -> None:
        saved: list[str] = []
        self.assertEqual(_input(_field("", saved)).value, DEFAULT)
        self.assertEqual(saved, [])

    def test_typing_the_default_clears_the_setting(self) -> None:
        saved: list[str] = []

        async def run() -> None:
            control = _input(_field(OTHER, saved))
            said(control, "update:value", DEFAULT)
            said(control, "blur")
            await _settled()

        _ran(run)
        self.assertEqual(saved, [""])

    def test_typing_another_path_saves_it(self) -> None:
        saved: list[str] = []

        async def run() -> None:
            control = _input(_field("", saved))
            said(control, "update:value", OTHER)
            said(control, "blur")
            await _settled()

        _ran(run)
        self.assertEqual(saved, [OTHER])

    def test_emptying_it_shows_the_default_again_and_saves_nothing(self) -> None:
        saved: list[str] = []
        holder = _field("", saved)
        control = _input(holder)

        async def run() -> None:
            said(control, "update:value", "")
            said(control, "blur")
            await _settled()

        _ran(run)
        self.assertEqual((control.value, saved), (DEFAULT, []))

    def test_reset_goes_back_to_the_default_and_clears_the_setting(self) -> None:
        saved: list[str] = []
        holder = _field(OTHER, saved)
        self.assertEqual(len(_buttons(holder, verbs.RESET)), 1)

        async def run() -> None:
            said(_buttons(holder, verbs.RESET)[0], "click")
            await _settled()

        _ran(run)
        self.assertEqual((_input(holder).value, saved), (DEFAULT, [""]))

    def test_reset_is_there_only_while_the_path_is_not_the_default(self) -> None:
        saved: list[str] = []
        holder = _field("", saved)
        self.assertEqual(_buttons(holder, verbs.RESET), [])

    def test_a_default_with_nothing_to_browse_with_is_not_shown_as_text(self) -> None:
        saved: list[str] = []
        self.assertEqual(_input(_drawn("", saved, default=DEFAULT)).value, "")


class APathsHover(unittest.TestCase):
    def _hover(self, holder: ui.element) -> ui.tooltip:
        return next(one for one in holder.descendants() if isinstance(one, ui.tooltip)
                    and "target" in one.props)

    def test_it_says_the_path_then_whose_it_is(self) -> None:
        saved: list[str] = []
        holder = _drawn("", saved, browse=AsyncMock(), default=DEFAULT, whose="VPinFE's default")
        self.assertEqual(self._hover(holder).text, f"{DEFAULT}\nVPinFE's default")

    def test_a_changed_path_says_only_the_path(self) -> None:
        saved: list[str] = []
        holder = _drawn(OTHER, saved, browse=AsyncMock(), default=DEFAULT,
                        whose="VPinFE's default")
        self.assertEqual(self._hover(holder).text, OTHER)

    def test_an_empty_field_with_nothing_to_say_draws_no_tooltip(self) -> None:
        saved: list[str] = []
        hover = self._hover(_drawn("", saved, browse=AsyncMock()))
        self.assertIn("no-parent-event", hover.props)

    def test_a_field_that_says_something_keeps_its_tooltip_live(self) -> None:
        saved: list[str] = []
        hover = self._hover(_drawn(OTHER, saved, browse=AsyncMock()))
        self.assertNotIn("no-parent-event", hover.props)


class APathBrowsedWithoutADefault(unittest.TestCase):
    def test_the_hover_is_handed_to_the_row_that_knows_whose_it_is(self) -> None:
        draw = panel.field("", lambda _text: None, browse=AsyncMock())
        self.assertIsInstance(getattr(draw, "hover_tip"), panel.PathTip)  # noqa: B009

    def test_a_field_that_is_not_a_path_has_no_path_hover(self) -> None:
        self.assertIsNone(getattr(panel.field("", lambda _text: None), "hover_tip"))  # noqa: B009


if __name__ == "__main__":
    unittest.main()
