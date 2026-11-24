"""The DMD rows of a backglass file's panel, for a build that keeps the DMD in the file.

What is pinned: which rows appear and when, what a write sends and what the panel holds
after it, that a failed write leaves the panel alone, and that Undo asks for the token the
write returned.
"""

from __future__ import annotations

import unittest
from typing import Any, cast
from unittest.mock import ANY, AsyncMock, Mock, patch

from common.games.formats import directb2s_displays
from common.i18n import t
from manager.kit import offload, panel
from manager.library import backglass_view
from manager.workbench import file as wb_file
from tests.support.workbench_patch import patch_workbench

IN_FILE = {"plugins": ["B2S"], "build": 6267, "dmd_in_file": True,
           "defaults": {"Plugin.B2S.Enable": "1"}}
IN_SETTINGS = {**IN_FILE, "build": 6266, "dmd_in_file": False}
BOX = {"target": "Backglass", "x": 10, "y": 20, "w": 300, "h": 80}


def _glass(*displays: dict, scoreview: Any = None, damaged: bool = False) -> dict:
    return {"size": [1000, 800], "grill": 0, "scoreview_art": scoreview, "damaged": damaged,
            "version": "abc", "displays": list(displays)}


def _context(program: dict | None = IN_FILE, *, playing: bool = False, served: int = 2) -> dict:
    return {"file_settings": {"program": program, "playing": playing, "fits": {},
                              "state": {}},
            "served": [{}] * served, "game_id": "g", "library": Mock(), "rebuild": AsyncMock()}


class TheRows(unittest.IsolatedAsyncioTestCase):
    async def entries(self, glass: dict | None, context: dict | None = None):
        context = context or _context()
        with patch_workbench("_config_values", AsyncMock(return_value={})):
            return await wb_file._dmd_file_entries(context, glass, "g.directb2s")

    async def test_a_build_that_keeps_the_dmd_in_the_settings_has_none(self) -> None:
        self.assertEqual([], await self.entries(_glass(BOX), _context(IN_SETTINGS)))

    async def test_nor_a_panel_without_the_programs_settings(self) -> None:
        self.assertEqual([], await self.entries(_glass(BOX), {"served": []}))

    async def test_nor_a_file_that_cannot_be_read(self) -> None:
        self.assertEqual([], await self.entries(_glass(BOX, damaged=True)))
        self.assertEqual([], await self.entries(None))

    async def test_a_file_with_no_dmd_offers_the_switch_alone(self) -> None:
        entries = await self.entries(_glass())

        self.assertEqual([panel.HEADING, "DMD"], [one[0] for one in entries[:2]])
        self.assertEqual("Backglass DMD", entries[0][1])
        self.assertNotIn("Position", [one[0] for one in entries])

    async def test_a_dmd_in_the_file_adds_its_position_size_and_fit(self) -> None:
        entries = await self.entries(_glass(BOX))

        self.assertEqual([panel.HEADING, "DMD", "Position", "Size", panel.FULL],
                         [one[0] for one in entries[:5]])

    async def test_the_score_view_has_rows_when_the_file_has_art_for_it_or_a_dmd(self) -> None:
        for glass, expected in ((_glass(), False),
                                (_glass(scoreview=[400, 100]), True),
                                (_glass({**BOX, "target": "ScoreView"}), True)):
            with self.subTest(expected=expected):
                headings = [one[1] for one in await self.entries(glass)
                            if one[0] is panel.HEADING]

                self.assertEqual("Score View DMD" in headings, expected)

    async def test_the_line_under_the_rows_counts_the_tables_that_use_the_file(self) -> None:
        for served, said in ((1, "Saved in the backglass file, for the 1 table that uses it"),
                             (3, "Saved in the backglass file, for the 3 tables that use it")):
            with self.subTest(served=served):
                entries = await self.entries(_glass(BOX), _context(served=served))

                with patch.object(panel, "line") as line:
                    entries[-2][1]()

                self.assertEqual(said, line.call_args.args[0])

    async def test_an_updated_backglass_is_said_to_replace_the_placement(self) -> None:
        entries = await self.entries(_glass(BOX))

        with patch.object(panel, "line") as line:
            entries[-1][1]()

        self.assertEqual("An updated backglass replaces this placement", line.call_args.args[0])

    async def test_while_a_table_plays_the_rows_say_so(self) -> None:
        entries = await self.entries(_glass(BOX), _context(playing=True))

        with patch.object(panel, "line") as line:
            entries[-1][1]()

        self.assertEqual(t("manager.workbench.playing_lock"), line.call_args.args[0])


class TheWrite(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.context = _context()
        self.glass = _glass()
        self.offered: list[tuple] = []
        self.notified: list[dict] = []
        self.again = AsyncMock(return_value=True)
        for target in (
                patch.object(wb_file.undo, "offer",
                             lambda said, reverse, **kwargs: self.offered.append((said, reverse))),
                patch.object(wb_file.ui, "notify",
                             lambda message, **kwargs: self.notified.append(
                                 {"message": message, **kwargs})),
                patch.object(offload, "io", AsyncMock(side_effect=lambda call, *a: call(*a))),
                patch.object(wb_file, "_form_again", self.again),
                patch("manager.kit.on_page.ui")):
            target.start()
            self.addCleanup(target.stop)
        self.library = self.context["library"]

    async def place(self, part: str = "Backglass", box: Any = (10, 20, 300, 80)) -> None:
        await cast(Any, wb_file._place_displays).__wrapped__(
            self.context, self.glass, "g.directb2s", part, box)

    async def test_the_box_goes_to_the_library_with_its_window_and_file(self) -> None:
        self.library.place_backglass_displays.return_value = {
            "displays": [BOX], "version": "v2", "undo": "tok"}

        await self.place()

        self.library.place_backglass_displays.assert_called_once_with(
            "g", "g.directb2s", "Backglass", (10, 20, 300, 80))

    async def test_the_panel_holds_what_the_file_now_has_and_keeps_its_version(self) -> None:
        self.library.place_backglass_displays.return_value = {
            "displays": [BOX], "version": "v2", "undo": "tok"}

        await self.place()

        self.assertEqual(([BOX], "abc"), (self.glass["displays"], self.glass["version"]))
        self.again.assert_awaited_once_with(self.context["file_settings"])

    async def test_a_dmd_taken_out_is_sent_as_no_box(self) -> None:
        self.library.place_backglass_displays.return_value = {
            "displays": [], "version": "v2", "undo": "tok"}

        await self.place(box=None)

        self.assertEqual(None, self.library.place_backglass_displays.call_args.args[3])
        self.assertEqual([], self.glass["displays"])

    async def test_undo_is_offered_and_puts_back_what_the_write_returned(self) -> None:
        self.library.place_backglass_displays.return_value = {
            "displays": [BOX], "version": "v2", "undo": "tok"}
        self.library.undo_backglass_displays.return_value = {
            "displays": [], "version": "v3", "undo": ""}

        await self.place()
        said, reverse = self.offered[0]
        await reverse()

        self.assertEqual("Saved in the backglass file", said)
        self.library.undo_backglass_displays.assert_called_once_with("g", "tok")
        self.assertEqual([], self.glass["displays"])

    async def test_a_table_the_write_could_not_order_is_named_for_the_write_and_its_undo(
            self) -> None:
        gone = [{"table": "t1", "name": "t1.vpx", "reason": "read-only"}]
        self.library.place_backglass_displays.return_value = {
            "displays": [BOX], "version": "v2", "undo": "tok", "order_failed": gone}
        self.library.undo_backglass_displays.return_value = {
            "displays": [], "version": "v3", "undo": "", "order_failed": gone}

        with patch.object(wb_file.order_notice, "tell") as tell:
            await self.place()
            await self.offered[0][1]()

        self.assertEqual([((gone,),), ((gone,),)], tell.call_args_list)

    async def test_a_write_that_changed_nothing_offers_no_undo(self) -> None:
        self.library.place_backglass_displays.return_value = {
            "displays": [], "version": "v1", "undo": ""}

        await self.place(box=None)

        self.assertEqual([], self.offered)

    async def test_a_refused_write_says_so_and_leaves_the_panel_as_it_was(self) -> None:
        self.glass["displays"] = [BOX]
        self.library.place_backglass_displays.side_effect = RuntimeError("playing")

        await self.place()

        self.assertEqual(([BOX], []), (self.glass["displays"], self.offered))
        self.assertEqual("negative", self.notified[0]["type"])
        self.again.assert_awaited_once()


class TheSwitch(unittest.IsolatedAsyncioTestCase):
    """Turning the DMD on fills it from the art, and where the art has no window the switch
    goes back off."""

    def setUp(self) -> None:
        self.context = _context()
        self.inner = self.context["file_settings"]
        self.again = AsyncMock(return_value=True)
        self.placed = AsyncMock()
        for target in (
                patch.object(wb_file, "_form_again", self.again),
                patch.object(wb_file, "_place_displays", self.placed)):
            target.start()
            self.addCleanup(target.stop)

    def switch(self, glass: dict):
        """The handler the switch is drawn with."""
        with patch.object(panel, "switch") as made:
            wb_file._dmd_file_rows(self.context, self.inner, glass, "g.directb2s",
                                   backglass_view.GLASS_PART, "Backglass DMD", False)
        return made.call_args.args[1]

    async def test_off_takes_the_dmd_out(self) -> None:
        turned = self.switch(_glass(BOX))

        await turned(Mock(value=False))

        self.placed.assert_awaited_once_with(
            self.context, ANY, "g.directb2s", "Backglass", None)

    async def test_on_asks_the_picture_to_fit_and_draws_off_again_when_it_finds_nothing(
            self) -> None:
        fit = AsyncMock()
        self.inner["fits"]["Backglass"] = fit
        turned = self.switch(_glass())

        await turned(Mock(value=True))

        fit.assert_awaited_once()
        self.again.assert_awaited_once_with(self.inner)

    async def test_on_leaves_the_form_alone_once_the_fit_has_placed_the_dmd(self) -> None:
        glass = _glass()

        async def fit() -> None:
            glass["displays"].append(BOX)

        self.inner["fits"]["Backglass"] = fit
        turned = self.switch(glass)

        await turned(Mock(value=True))

        self.again.assert_not_awaited()


class TheNumbers(unittest.IsolatedAsyncioTestCase):
    """The four numbers the rows hold, by the controls they are drawn with."""

    def setUp(self) -> None:
        self.context = _context()
        self.inner = self.context["file_settings"]
        self.glass = _glass(BOX)
        self.placed = AsyncMock()
        self.number = Mock(return_value=lambda: None)
        for target in (patch.object(wb_file, "_place_displays", self.placed),
                       patch.object(panel, "number", self.number),
                       patch.object(wb_file, "_pair", Mock())):
            target.start()
            self.addCleanup(target.stop)
        wb_file._dmd_file_rows(self.context, self.inner, self.glass, "g.directb2s",
                               backglass_view.GLASS_PART, "Backglass DMD", False)
        self.controls = {call.args[0]: call for call in self.number.call_args_list}

    def handler(self, value: int):
        return {key: call.args[1] for key, call in self.controls.items()}[value]

    def test_the_controls_stop_at_the_limits_the_file_takes(self) -> None:
        calls = self.number.call_args_list

        self.assertEqual(
            [(call.args[0], call.kwargs["low"], call.kwargs["high"]) for call in calls],
            [(10, 0, backglass_view.FILE_LIMIT), (20, 0, backglass_view.FILE_LIMIT),
             (300, 1, backglass_view.FILE_LIMIT), (80, 1, backglass_view.FILE_LIMIT)])

    def test_the_limit_is_the_one_the_file_is_written_with(self) -> None:
        self.assertEqual(backglass_view.FILE_LIMIT, directb2s_displays.LIMIT)

    async def test_leaving_a_field_unchanged_writes_nothing(self) -> None:
        for value in (10, 20, 300, 80):
            await self.handler(value)(Mock(value=value))

        self.placed.assert_not_awaited()

    async def test_a_changed_field_writes_the_box_with_that_number(self) -> None:
        await self.handler(20)(Mock(value=25))

        self.placed.assert_awaited_once_with(
            self.context, self.glass, "g.directb2s", "Backglass", (10, 25, 300, 80))

    async def test_a_second_edit_before_the_redraw_starts_from_what_the_file_now_holds(
            self) -> None:
        self.glass["displays"] = [{**BOX, "x": 50}]

        await self.handler(20)(Mock(value=25))

        assert self.placed.await_args is not None
        self.assertEqual((50, 25, 300, 80), self.placed.await_args.args[4])

    async def test_a_dmd_taken_out_meanwhile_is_not_written_back_by_a_late_edit(self) -> None:
        self.glass["displays"] = []

        await self.handler(20)(Mock(value=25))

        self.placed.assert_not_awaited()


class TheNoticeWhenNothingWasComputed(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.context = _context()
        self.inner = self.context["file_settings"]
        self.again = AsyncMock(return_value=True)
        self.notified = Mock()
        for target in (patch.object(wb_file, "_form_again", self.again),
                       patch.object(wb_file.ui, "notify", self.notified)):
            target.start()
            self.addCleanup(target.stop)

    def switch(self, glass: dict):
        with patch.object(panel, "switch") as made:
            wb_file._dmd_file_rows(self.context, self.inner, glass, "g.directb2s",
                                   backglass_view.GLASS_PART, "Backglass DMD", False)
        return made.call_args.args[1]

    async def test_a_switch_pressed_before_the_picture_has_a_fit_does_not_call_it_damaged(
            self) -> None:
        turned = self.switch(_glass())

        await turned(Mock(value=True))

        self.notified.assert_not_called()
        self.again.assert_awaited_once_with(self.inner)

    async def test_a_fit_that_places_nothing_leaves_the_notice_to_the_fit(
            self) -> None:
        async def fit() -> None:
            return None

        self.inner["fits"]["Backglass"] = fit
        turned = self.switch(_glass())

        await turned(Mock(value=True))

        self.notified.assert_not_called()
        self.again.assert_awaited_once_with(self.inner)


class TheWriteThatCannotBeUndone(unittest.IsolatedAsyncioTestCase):
    async def test_a_change_too_big_to_keep_says_so(self) -> None:
        context, glass = _context(), _glass()
        notified = Mock()
        context["library"].place_backglass_displays.return_value = {
            "displays": [BOX], "version": "v2", "undo": "", "undo_refused": "too_large"}
        offered: list[Any] = []
        for target in (patch.object(wb_file.ui, "notify", notified),
                       patch.object(wb_file.undo, "offer", lambda *a, **k: offered.append(a)),
                       patch.object(offload, "io",
                                    AsyncMock(side_effect=lambda call, *a: call(*a))),
                       patch.object(wb_file, "_form_again", AsyncMock(return_value=True))):
            target.start()
            self.addCleanup(target.stop)

        await cast(Any, wb_file._place_displays).__wrapped__(
            context, glass, "g.directb2s", "Backglass", (10, 20, 300, 80))

        self.assertEqual(([], t("manager.backglass.dmd_file.no_undo")),
                         (offered, notified.call_args.args[0]))


if __name__ == "__main__":
    unittest.main()
