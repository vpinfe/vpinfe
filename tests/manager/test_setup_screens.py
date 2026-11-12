"""Setup's Screens step: what it mounts, and the half of its answer Visual Pinball keeps."""

from __future__ import annotations

import asyncio
import unittest
from typing import Any
from unittest import mock

from nicegui import ui

from apps.vpx import screens as vpx
from common.settings import config_service
from manager.settings import displays, vpx_displays, vpx_find
from manager.settings.screens import Screen
from manager.settings.vpx_find import Find
from manager.setup import setup, setup_screens, setup_vpx
from tests.manager.test_setup_vpx import LAUNCHER, NEW, OLD

SCHEMA = config_service.schema()["sections"]
_PAGE = ui.element()

TALL = Screen(1, "SAMSUNG", 2160, 3840, 0, 0)
BACK = Screen(2, "DELL P2419H", 1920, 1080, 2160, 0)
HERE = [TALL, BACK]


def _ran(coro: Any) -> Any:
    return asyncio.run(coro)


def _held(**keys: tuple[str, bool]) -> dict[str, Any]:
    """A launcher's settings as read: each key's value and whether the file sets it."""
    return {key.replace("__", "."): {"value": value, "set_here": here}
            for key, (value, here) in keys.items()}


def _standing(on: dict | None = None, turn: tuple[str, str] = ("portrait", "0")) -> Any:
    return displays.Standing({"playfield": 1, "backglass": 2, "scoreview": None,
                              **(on or {})}, turn)


class FillingTests(unittest.TestCase):
    def test_a_launcher_with_nothing_answered_gets_each_part_the_answer_places(self) -> None:
        keys = vpx_displays.filling(_standing(), HERE, [], {}, "")

        self.assertEqual("SAMSUNG [0, 0]", keys["Player.PlayfieldDisplay"])
        self.assertEqual("DELL P2419H [2160, 0]", keys["Backglass.BackglassDisplay"])
        self.assertEqual("1", keys["Backglass.BackglassOutput"])
        self.assertIn("TableOverride.ViewCabRotation", keys)

    def test_a_part_the_file_already_answers_stays_where_it_was_put(self) -> None:
        held = _held(Player__PlayfieldDisplay=("SAMSUNG [0, 0]", True),
                     Backglass__BackglassOutput=("0", True))

        keys = vpx_displays.filling(_standing(), HERE, [], held, "")

        self.assertNotIn("Player.PlayfieldDisplay", keys)
        self.assertNotIn("Backglass.BackglassOutput", keys)
        self.assertNotIn("Backglass.BackglassDisplay", keys)

    def test_a_screen_the_file_names_for_a_part_is_not_written_over_though_its_output_is_blank(
            self) -> None:
        held = _held(Player__PlayfieldDisplay=("SAMSUNG [0, 0]", True),
                     Backglass__BackglassDisplay=("CVT DVI [4080, 0]", True))

        keys = vpx_displays.filling(_standing(), HERE, [], held, "")

        self.assertNotIn("Backglass.BackglassDisplay", keys)
        self.assertNotIn("Backglass.BackglassOutput", keys)

    def test_a_turn_the_file_sets_is_not_written_over_when_the_playfield_is_filled(
            self) -> None:
        held = _held(TableOverride__ViewCabRotation=("90", True))

        keys = vpx_displays.filling(_standing(), HERE, [], held, "")

        self.assertIn("Player.PlayfieldDisplay", keys)
        self.assertNotIn("TableOverride.ViewCabRotation", keys)

    def test_the_turn_goes_with_the_playfield_and_only_then(self) -> None:
        held = _held(Player__PlayfieldDisplay=("SAMSUNG [0, 0]", True))

        keys = vpx_displays.filling(_standing(), HERE, [], held, "")

        self.assertNotIn("TableOverride.ViewCabRotation", keys)
        self.assertIn("Backglass.BackglassDisplay", keys)

    def test_the_topper_goes_to_vpinfe_s_topper_screen_floating(self) -> None:
        strip = Screen(3, "CVT DVI", 1280, 390, 4080, 0)

        keys = vpx_displays.filling(_standing({"topper": 3}), [*HERE, strip], [], {}, "")

        self.assertEqual("CVT DVI [4080, 0]", keys["Topper.TopperDisplay"])
        self.assertEqual("1", keys["Topper.TopperOutput"])

    def test_no_topper_screen_in_vpinfe_leaves_the_topper_as_the_person_set_it(self) -> None:
        keys = vpx_displays.filling(_standing({"topper": None}), HERE, [], {}, "")

        self.assertFalse([key for key in keys if key.startswith("Topper")])

    def test_a_part_with_no_screen_writes_nothing(self) -> None:
        keys = vpx_displays.filling(_standing({"backglass": None}), HERE, [], {}, "")

        self.assertNotIn("Backglass.BackglassDisplay", keys)
        self.assertNotIn("Backglass.BackglassOutput", keys)

    def test_a_panel_kind_is_written_unless_the_file_already_names_one(self) -> None:
        with mock.patch.object(vpx.sys, "platform", "linux"):
            fresh = vpx_displays.filling(_standing(), HERE, [], {}, vpx.ZEDMD)
            named = vpx_displays.filling(
                _standing(), HERE, [], _held(Plugin__DMDUtil__PIN2DMD=("1", True)), vpx.ZEDMD)

        self.assertEqual("1", fresh["Plugin.DMDUtil.ZeDMD"])
        self.assertNotIn("Plugin.DMDUtil.ZeDMD", named)

    def test_no_panel_writes_no_kind(self) -> None:
        keys = vpx_displays.filling(_standing(), HERE, [], {}, "")

        self.assertFalse([key for key in keys if key.startswith(vpx.DMD_UTIL)])


class FillTests(unittest.TestCase):
    def test_the_file_is_made_where_the_program_has_never_run(self) -> None:
        client = mock.Mock()
        client.launcher_config.return_value = {"values": {}}

        keys = _ran(vpx_displays.fill(client, "vpx-1", _standing(), HERE, ""))

        client.write_launcher_config.assert_called_once_with("vpx-1", keys, create=True)

    def test_nothing_to_write_writes_nothing(self) -> None:
        client = mock.Mock()
        client.launcher_config.return_value = {"values": {}}

        _ran(vpx_displays.fill(client, "vpx-1", _standing({
            "playfield": None, "backglass": None}), HERE, ""))

        client.write_launcher_config.assert_not_called()


class AnswerTests(unittest.TestCase):
    def test_the_answer_writes_nothing_to_visual_pinball_before_its_step(self) -> None:
        state = setup_screens.Screens()

        with mock.patch.object(setup_screens, "fill", new=mock.AsyncMock()) as fill:
            _ran(setup_screens.answer(state))

        fill.assert_not_awaited()
        self.assertTrue(state.answered)

    def test_a_step_not_answered_writes_nothing(self) -> None:
        with mock.patch.object(setup_screens, "_gather", new=mock.AsyncMock()) as gather:
            _ran(setup_screens.fill(setup_screens.Screens(), "vpx-1"))

        gather.assert_not_awaited()

    def test_the_kind_is_the_one_picked_while_the_panel_is_on(self) -> None:
        client = mock.Mock()

        for seen_values, wanted in (({"real_dmd": {"enabled": True}}, vpx.ZEDMD),
                                    ({"real_dmd": {"enabled": False}}, ""),
                                    ({"real_dmd": {"enabled": True,
                                                   "pin2dmd_enabled": True}}, vpx.PIN2DMD)):
            with self.subTest(values=seen_values), \
                 mock.patch.object(setup_screens, "_gather",
                                   new=mock.AsyncMock(return_value=setup_screens.Seen(
                                       seen_values, SCHEMA, [], [], None))), \
                 mock.patch.object(setup_screens, "ApiClient", return_value=client), \
                 mock.patch.object(vpx_displays, "fill", new=mock.AsyncMock()) as fill, \
                 mock.patch.object(vpx_displays, "disagreeing",
                                   new=mock.AsyncMock(return_value=[])), \
                 _PAGE:
                _ran(setup_screens.fill(
                    setup_screens.Screens(answered=True, kind=vpx.ZEDMD), "vpx-1"))

            self.assertEqual(wanted, fill.call_args.args[4])


class _Box:
    """A dialog that answers as soon as it is awaited."""
    def __init__(self, answer: Any) -> None:
        self.answer = answer
        self.escape_answer: Any = None

    def __enter__(self) -> _Box:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def __await__(self) -> Any:
        async def said() -> Any:
            return self.answer
        return said().__await__()


class AskTests(unittest.TestCase):
    def _fill(self, apart: list[str], **state: Any) -> tuple[mock.Mock, mock.AsyncMock]:
        values = setup_screens.Seen({}, SCHEMA, [], [], None)
        with mock.patch.object(setup_screens, "_gather",
                               new=mock.AsyncMock(return_value=values)), \
             mock.patch.object(setup_screens, "ApiClient"), \
             mock.patch.object(vpx_displays, "fill", new=mock.AsyncMock()), \
             mock.patch.object(vpx_displays, "disagreeing",
                               new=mock.AsyncMock(return_value=apart)), \
             mock.patch.object(setup_screens, "_ask_which", new=mock.AsyncMock()) as ask, \
             mock.patch.object(setup_screens.background_tasks, "create") as later, \
             _PAGE:
            _ran(setup_screens.fill(setup_screens.Screens(answered=True, **state), "vpx-1"))
        return later, ask

    def test_a_launcher_that_holds_its_own_screens_is_asked_without_holding_setup_up(
            self) -> None:
        later, ask = self._fill(["backglass"])

        later.assert_called_once()
        ask.assert_called_once()
        self.assertEqual(ask.call_args.args[3], ["backglass"])
        later.call_args.args[0].close()

    def test_a_launcher_that_agrees_is_not_asked(self) -> None:
        later, _ = self._fill([])

        later.assert_not_called()

    def test_a_launcher_that_has_been_asked_about_is_not_asked_again(self) -> None:
        later, _ = self._fill(["backglass"], asked=True)

        later.assert_not_called()

    def _which(self, answer: bool, written: Any = None) -> mock.AsyncMock:
        match = mock.AsyncMock(return_value=written or vpx.Written({}))
        self.box = _Box(answer)
        with mock.patch.object(setup_screens.frame, "opened", return_value=self.box), \
             mock.patch.object(setup_screens.frame, "footer"), \
             mock.patch.object(setup_screens.frame, "quiet"), \
             mock.patch.object(setup_screens.frame, "answer"), \
             mock.patch.object(setup_screens.ui, "label"), \
             mock.patch.object(setup_screens, "ApiClient"), \
             mock.patch.object(vpx_displays, "match_launcher", new=match), \
             _PAGE:
            _ran(setup_screens._ask_which("vpx-1", _standing(), HERE, ["backglass"]))
        return match

    def test_match_vpinfe_matches_the_launcher(self) -> None:
        match = self._which(True)

        match.assert_awaited_once()
        self.assertEqual(match.call_args.args[1], "vpx-1")

    def test_leave_as_is_leaves_the_file_as_it_is(self) -> None:
        match = self._which(False)

        match.assert_not_awaited()

    def test_escape_answers_leave_as_is(self) -> None:
        self._which(False)

        self.assertIs(self.box.escape_answer, False)

    def test_a_comparison_that_cannot_be_read_after_the_write_asks_nothing_and_says_nothing(
            self) -> None:
        values = setup_screens.Seen({}, SCHEMA, [], [], None)
        with mock.patch.object(setup_screens, "_gather",
                               new=mock.AsyncMock(return_value=values)), \
             mock.patch.object(setup_screens, "ApiClient"), \
             mock.patch.object(vpx_displays, "fill", new=mock.AsyncMock()), \
             mock.patch.object(vpx_displays, "disagreeing",
                               new=mock.AsyncMock(side_effect=RuntimeError("gone"))), \
             mock.patch.object(setup_screens.background_tasks, "create") as later, \
             mock.patch.object(setup_screens.ui, "notify") as notify, \
             self.assertLogs(setup_screens.logger, "WARNING"), _PAGE:
            _ran(setup_screens.fill(setup_screens.Screens(answered=True), "vpx-1"))

        later.assert_not_called()
        notify.assert_not_called()

    def test_a_screen_visual_pinball_has_no_name_for_is_said(self) -> None:
        with mock.patch.object(setup_screens.ui, "notify") as notify:
            self._which(True, vpx.Written({}, unnamed=("backglass",)))

        self.assertIn("Backglass", notify.call_args.args[0])


class ProgramStepTests(unittest.TestCase):
    def _answer(self, vpx_state: setup_vpx.Vpx, then: mock.AsyncMock) -> None:
        client = mock.Mock()
        client.launcher_config.return_value = {"values": {}}
        with mock.patch.object(setup_vpx, "ApiClient", return_value=client):
            _ran(setup_vpx.answer_program(vpx_state, {setup_vpx.PROGRAM: "0"}, cabinet=True,
                                          then=then))

    def test_the_launcher_this_answer_made_is_handed_on(self) -> None:
        then = mock.AsyncMock()

        self._answer(setup_vpx.Vpx(finds=[Find(NEW, vpx_find.USUAL, 0)]),
                     then)

        then.assert_awaited_once()
        self.assertTrue(then.mock_calls[0].args[0])

    def test_a_launcher_that_was_already_there_is_handed_on_too(self) -> None:
        then = mock.AsyncMock()

        self._answer(setup_vpx.Vpx(launcher=LAUNCHER, stored=OLD, chosen=OLD,
                                   finds=[Find(OLD, vpx_find.USUAL, 0)]),
                     then)

        then.assert_awaited_once()


class StepTests(unittest.TestCase):
    def test_the_step_is_asked_of_either_between_the_look_and_the_controls(self) -> None:
        session = setup.Session(again=False)
        keys = [setup.HOW, setup.APPEARANCE, setup.SCREENS, setup.CONTROLS]
        for cabinet in (True, False):
            with self.subTest(cabinet=cabinet), \
                 mock.patch.object(setup, "_cabinet", return_value=cabinet):
                found = [one.key for one in setup._applicable(session)]
                self.assertEqual(keys, found[:len(keys)])

    def test_a_desktop_does_not_have_the_screens_written_to_its_launcher(self) -> None:
        for cabinet in (True, False):
            with self.subTest(cabinet=cabinet), \
                 mock.patch.object(setup, "_cabinet", return_value=cabinet), \
                 mock.patch.object(setup_vpx, "answer_program",
                                   new=mock.AsyncMock(return_value="")) as program:
                _ran(setup._answer_vpx(setup.Session(again=False), {}))

            self.assertEqual(cabinet, program.call_args.kwargs["then"] is not None)

    def test_answering_it_hands_the_step_its_own_state_alone(self) -> None:
        session = setup.Session(again=False, vpx=setup_vpx.Vpx(launcher=LAUNCHER))

        with mock.patch.object(setup_screens, "answer", new=mock.AsyncMock()) as answered:
            _ran(setup._answer_screens(session, {}))

        answered.assert_awaited_once_with(session.screens)


if __name__ == "__main__":
    unittest.main()
