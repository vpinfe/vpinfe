"""Launchers > Visual Pinball X > Input: the binding table over the launcher's ini."""

from __future__ import annotations

import asyncio
import dataclasses
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from nicegui import ui

from apps.vpx import areas, bindings, controllers
from apps.vpx.config import VPXConfig
from common.apps.contract import SCOPE_LAUNCHER
from common.i18n import t
from manager.settings import vpx_input
from manager.workbench import all_settings
from tests.support.clicks import press

INI = ("[Input]\nMapping.LeftFlipper = Key;225 | SDLJoy_0300_1;4\nMapping.RightFlipper = \n"
       "Mapping.Tilt = Key;99999\nMapping.Custom1 = \n")


async def _now(callback: Any, *args: Any, **kwargs: Any) -> Any:
    return callback(*args, **kwargs)


class _Library:
    """What `write_launcher_config` does to the launcher's own file."""

    def __init__(self, settings: dict[str, Any]) -> None:
        self.settings = settings
        self.config = VPXConfig()
        self.calls: list[dict[str, str]] = []

    def write_launcher_config(self, _launcher_id: str, values: dict[str, str]) -> None:
        self.calls.append(dict(values))
        self.config.write(SCOPE_LAUNCHER, "", values, self.settings)


class _PageCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        held = TemporaryDirectory()
        self.addCleanup(held.cleanup)
        self.path = Path(held.name) / "VPinballX.ini"
        self.path.write_text(INI, encoding="utf-8")
        self.library = _Library({"ini_path": str(self.path)})
        self.enterContext(patch("nicegui.run.io_bound", new=_now))
        self.notify = self.enterContext(patch.object(ui, "notify"))
        self.rebuilt = AsyncMock()
        self.context: dict[str, Any] = {
            "launcher": {"launcher_id": "vpx"}, "library": self.library,
            "rebuild": self.rebuilt, "config_values": {"stale": 1}}
        [self.group] = [g for g in self.library.config.groups(self.library.settings)
                        if g.key == areas.INPUT]
        read = self.library.config.read(SCOPE_LAUNCHER, "", self.library.settings)
        self.flat = {key: str(held.value or "") for key, held in read.items()}
        self.page = vpx_input.Page(self.library, self.context, self.group, dict(self.flat))

    def lines(self) -> list[str]:
        return self.path.read_text(encoding="utf-8").splitlines()


class ThePage(_PageCase):
    def test_the_page_is_for_a_group_of_actions_only(self) -> None:
        self.assertTrue(vpx_input.applies(self.group))
        other = Mock(key="input", settings=[Mock(key="Input.JoyCustom1")])
        self.assertFalse(vpx_input.applies(other))
        self.assertFalse(vpx_input.applies(Mock(key="sound", settings=self.group.settings)))

    def test_all_settings_does_not_list_the_actions_a_second_time(self) -> None:
        sound = Mock(key="sound", summarized=False, read_only=False,
                     settings=[Mock(key="Player.PlaySound")])

        listed = all_settings._listed_groups({"config_groups": [self.group, sound]})

        self.assertEqual(listed, [sound])

    def test_a_row_per_action_under_its_heading_in_the_order_drawn(self) -> None:
        rows = vpx_input.table_rows(self.group, self.flat)

        self.assertEqual([row.name for row in rows], [one.name for one in bindings.ACTIONS])
        self.assertFalse(any(row.holds for row in rows))

    def test_a_row_ships_with_the_program_s_keys(self) -> None:
        rows = {row.name: row for row in vpx_input.table_rows(self.group, self.flat)}

        self.assertEqual(rows["LeftFlipper"].defaults, ("key:ShiftLeft",))
        self.assertEqual(rows["GenTournament"].defaults, ("chord(key:AltLeft+key:Digit1)",))

    def test_a_controller_button_in_the_file_is_a_chip_that_is_kept(self) -> None:
        rows = {row.name: row for row in vpx_input.table_rows(self.group, self.flat)}

        self.assertEqual([one.label for one in rows["LeftFlipper"].kept],
                         [t("manager.vpx_input.controller_button")])
        self.assertEqual(rows["RightFlipper"].kept, ())

    def test_a_chord_with_a_controller_button_in_it_reads_as_one(self) -> None:
        self.assertEqual(vpx_input._kept("Key;225 & SDLJoy_0300_1;3").label,
                         t("manager.vpx_input.controller_button"))

    def test_a_key_with_no_name_here_is_a_chip_that_is_kept_too(self) -> None:
        rows = {row.name: row for row in vpx_input.table_rows(self.group, self.flat)}

        [one] = rows["Tilt"].kept
        self.assertEqual((one.label, one.pad), (t("manager.vpx_input.other_key"), False))

    def test_the_table_is_told_what_each_action_is_bound_to(self) -> None:
        bound = self.page.bound()

        self.assertEqual(bound["LeftFlipper"], ["key:ShiftLeft"])
        self.assertEqual(bound["RightFlipper"], ["key:ShiftRight"])
        self.assertEqual(bound["Custom1"], [])

    def test_a_controller_press_is_refused_and_a_key_is_not(self) -> None:
        self.assertEqual(self.page.refuse("pad:standard/button:1"),
                         t("manager.vpx_input.controllers_note"))
        self.assertEqual(self.page.refuse("key:KeyA"), "")

    def test_a_key_the_program_has_no_number_for_is_refused(self) -> None:
        self.assertEqual(self.page.refuse("key:IntlRo"),
                         t("manager.vpx_input.unknown_key"))

    async def test_a_new_key_is_one_line_and_the_controller_button_stays(self) -> None:
        kept = await press(self.page.put, {"LeftFlipper": ["key:KeyA"]})

        self.assertTrue(kept)
        self.assertEqual(self.library.calls,
                         [{"Input.Mapping.LeftFlipper": "Key;4 | SDLJoy_0300_1;4"}])
        self.assertEqual(self.lines()[1], "Mapping.LeftFlipper = Key;4 | SDLJoy_0300_1;4")
        self.assertEqual(self.lines()[2:], INI.splitlines()[2:])
        self.notify.assert_not_called()

    async def test_the_default_is_written_blank(self) -> None:
        await press(self.page.put, {"RightFlipper": ["key:KeyA"]})
        await press(self.page.put, {"RightFlipper": ["key:ShiftRight"]})

        self.assertEqual(self.lines()[2], "Mapping.RightFlipper = ")

    async def test_a_chord_is_written_with_its_parts_together(self) -> None:
        await press(self.page.put, {"Custom1": ["chord(key:AltLeft+key:KeyA)"]})

        self.assertIn("Mapping.Custom1 = Key;226 & Key;4", self.lines())

    async def test_a_row_left_as_it_is_writes_nothing(self) -> None:
        await press(self.page.put, {"Custom1": []})

        self.assertEqual(self.library.calls, [])

    async def test_a_save_that_fails_says_so_and_keeps_nothing(self) -> None:
        cast(Any, self.library).write_launcher_config = Mock(side_effect=OSError("read-only"))

        kept = await press(self.page.put, {"Custom1": ["key:KeyA"]})

        self.assertFalse(kept)
        self.assertEqual(self.notify.call_args.args, (t("said.save_failed"),))
        self.assertEqual(self.page.flat, self.flat)

    async def test_a_row_emptied_that_has_a_default_comes_back_with_it_and_says_so(
            self) -> None:
        self.page.by_name["RightFlipper"] = dataclasses.replace(
            self.page.by_name["RightFlipper"], label="Right Flipper")
        await press(self.page.put, {"RightFlipper": ["key:KeyA"]})
        self.notify.reset_mock()

        await press(self.page.put, {"RightFlipper": []})
        await asyncio.sleep(0)

        self.assertEqual(self.lines()[2], "Mapping.RightFlipper = ")
        self.assertEqual(self.notify.call_args.args[0], t(
            "manager.vpx_input.default_back", rows="Right Flipper"))
        self.assertIn("Right Flipper", self.notify.call_args.args[0])
        self.rebuilt.assert_awaited_once()
        self.assertNotIn("config_values", self.context)

    async def test_a_row_emptied_that_has_a_button_kept_keeps_just_the_button(self) -> None:
        await press(self.page.put, {"LeftFlipper": []})

        self.assertEqual(self.lines()[1], "Mapping.LeftFlipper = SDLJoy_0300_1;4")
        self.notify.assert_not_called()
        self.rebuilt.assert_not_awaited()


PAD = "SDLJoy_0300_1"
INFO = bindings.DeviceInfo("Pad #0", ((3, False, "Left But. 1"), (4, False, "Left But. 2")))


class _Tap:
    """What the program's listener hands over."""

    def __init__(self) -> None:
        self.waiting: list[controllers.Press] = []
        self.seen: dict[str, bindings.DeviceInfo] = {}
        self.closed = 0

    def drain(self) -> list[controllers.Press]:
        out, self.waiting = self.waiting, []
        return out

    def devices(self) -> dict[str, bindings.DeviceInfo]:
        return dict(self.seen)

    def close(self) -> None:
        self.closed += 1


class ThePadsPage(_PageCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.page.v2 = True
        self.page.pads.known = {PAD: INFO}

    def test_a_controller_button_in_the_file_is_a_chip_that_can_be_changed(self) -> None:
        rows = {row.name: row for row in self.page.rows()}

        self.assertEqual(rows["LeftFlipper"].kept, ())
        self.assertEqual(self.page.bound()["LeftFlipper"], ["key:ShiftLeft", f"vpx:{PAD};4"])

    def test_a_button_of_a_kind_that_is_not_a_joystick_stays_kept(self) -> None:
        self.page.flat["Input.Mapping.Custom1"] = "Mouse;1 | Key;4"

        rows = {row.name: row for row in self.page.rows()}

        self.assertEqual(len(rows["Custom1"].kept), 1)
        self.assertEqual(self.page.bound()["Custom1"], ["key:KeyA"])

    def test_a_controller_press_is_taken_and_a_browser_pad_press_is_not(self) -> None:
        self.assertEqual(self.page.refuse(f"vpx:{PAD};3"), "")
        self.assertEqual(self.page.refuse(f"chord(key:KeyA+vpx:{PAD};3)"), "")
        self.assertEqual(self.page.refuse("pad:standard/button:1"),
                         t("manager.vpx_input.controllers_note"))

    def test_without_controllers_a_joystick_member_is_not_a_press_the_page_takes(self) -> None:
        self.page.v2 = False

        self.assertEqual(self.page.refuse(f"vpx:{PAD};3"), t("manager.vpx_input.unknown_key"))

    async def test_a_button_is_written_with_the_program_s_record_of_the_joystick(self) -> None:
        kept = await press(self.page.put, {"Custom1": [f"vpx:{PAD};3"]})

        self.assertTrue(kept)
        [written] = self.library.calls
        self.assertEqual(written["Input.Mapping.Custom1"], f"{PAD};3")
        self.assertEqual(written[f"Input.Device.{PAD}.NoAutoLayout"], "1")
        self.assertEqual(written[f"Input.Device.{PAD}.Name"], "Pad #0")
        self.assertEqual(written["Input.Devices"], PAD)
        self.assertIn(f"Mapping.Custom1 = {PAD};3", self.lines())

    async def test_a_joystick_already_recorded_is_not_written_again(self) -> None:
        await press(self.page.put, {"Custom1": [f"vpx:{PAD};3"]})
        self.library.calls.clear()

        await press(self.page.put, {"Custom1": [f"vpx:{PAD};3", f"vpx:{PAD};4"]})

        [written] = self.library.calls
        self.assertEqual(list(written), ["Input.Mapping.Custom1"])
        self.assertEqual(written["Input.Mapping.Custom1"], f"{PAD};3 | {PAD};4")

    async def test_a_chord_of_a_key_and_a_button_is_written_together(self) -> None:
        await press(self.page.put, {"Custom1": [f"chord(key:AltLeft+vpx:{PAD};3)"]})

        self.assertIn(f"Mapping.Custom1 = Key;226 & {PAD};3", self.lines())

    async def test_a_key_alone_writes_no_joystick_record(self) -> None:
        await press(self.page.put, {"Custom1": ["key:KeyA"]})

        [written] = self.library.calls
        self.assertEqual(list(written), ["Input.Mapping.Custom1"])

    async def test_a_row_left_as_it_is_writes_nothing_even_with_buttons(self) -> None:
        await press(self.page.put, {"LeftFlipper": ["key:ShiftLeft", f"vpx:{PAD};4"]})

        self.assertEqual(self.library.calls, [])


class ThePads(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tap = _Tap()
        self.listen = self.enterContext(patch.object(
            controllers, "listen", return_value=(self.tap, "")))
        self.enterContext(patch("nicegui.run.io_bound", new=_now))

    async def test_a_program_whose_library_loads_is_heard(self) -> None:
        pads = vpx_input.Pads("/Applications/Program.app")

        self.assertFalse(pads.is_open())
        self.assertTrue(await pads.open())

        self.assertTrue(pads.is_open())
        self.listen.assert_called_once_with("/Applications/Program.app")

    async def test_a_library_that_will_not_load_is_keys_only_and_says_why(self) -> None:
        self.listen.return_value = (None, "no SDL library beside the program")
        pads = vpx_input.Pads("/Applications/Program.app")

        with self.assertLogs("manager.settings.vpx_input", "INFO") as logged:
            self.assertFalse(await pads.open())

        self.assertFalse(pads.is_open())
        self.assertIn("no SDL library beside the program", logged.output[0])

    async def test_the_library_stays_loaded_until_the_last_use_is_closed(self) -> None:
        pads = vpx_input.Pads("p")
        await pads.open()
        await pads.open()

        pads.close()
        self.assertTrue(pads.is_open())
        self.assertEqual(self.tap.closed, 0)
        pads.close()

        self.assertFalse(pads.is_open())
        self.assertEqual(self.tap.closed, 1)
        self.listen.assert_called_once()

    async def test_it_can_be_opened_again_after_the_last_close(self) -> None:
        pads = vpx_input.Pads("p")
        await pads.open()
        pads.close()

        self.assertTrue(await pads.open())
        self.assertEqual(self.listen.call_count, 2)

    async def test_presses_are_handed_over_as_the_members_the_page_maps(self) -> None:
        pads = vpx_input.Pads("p")
        await pads.open()
        self.tap.waiting = [controllers.Press(f"vpx:{PAD};3", True),
                            controllers.Press(f"vpx:{PAD};3", False)]

        presses, _ = pads.poll()

        self.assertEqual(presses, [(f"vpx:{PAD};3", True), (f"vpx:{PAD};3", False)])
        self.assertEqual(pads.poll(), ([], False))

    async def test_a_controller_appearing_is_said_once_and_named_after(self) -> None:
        pads = vpx_input.Pads("p")
        await pads.open()
        self.tap.seen = {PAD: INFO}

        _, first = pads.poll()
        _, again = pads.poll()

        self.assertEqual((first, again), (True, False))
        self.assertEqual(pads.label(f"vpx:{PAD};3"), ("Left But. 1", "Pad #0 - Left But. 1"))

    async def test_what_was_learned_is_kept_after_the_last_close(self) -> None:
        pads = vpx_input.Pads("p")
        await pads.open()
        self.tap.seen = {PAD: INFO}
        pads.poll()
        pads.close()

        self.assertEqual(pads.known, {PAD: INFO})
        self.assertEqual(pads.poll(), ([], False))

    def test_it_owns_the_joystick_members_and_no_others(self) -> None:
        pads = vpx_input.Pads("p")

        self.assertTrue(pads.owns(f"vpx:{PAD};3"))
        self.assertFalse(pads.owns("key:KeyA"))


class TheTableTheyAreDrawnIn(_PageCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.page.pads.program = "p"
        self.holder = MagicMock()
        self.holder.is_deleted = False
        self.table = self.enterContext(patch.object(vpx_input, "Table"))
        self.tap = _Tap()
        self.listen = self.enterContext(patch.object(
            controllers, "listen", return_value=(self.tap, "")))

    async def test_a_program_that_can_be_heard_gets_a_table_with_its_controllers(self) -> None:
        await self.page._show(self.holder, True)

        kwargs = self.table.call_args.kwargs
        self.assertTrue(self.page.v2)
        self.assertIs(kwargs["host"], self.page.pads)
        self.assertEqual(kwargs["words"].how_body, "")
        self.assertEqual(kwargs["words"].how_step_2, "manager.vpx_input.how_step_buttons")
        self.assertEqual(t("manager.vpx_input.how_step_buttons"),
                         "Press the button or key you want, or two together. "
                         "It's saved as you press.")

    async def test_a_program_that_cannot_be_heard_gets_the_keys_only_page(self) -> None:
        self.listen.return_value = (None, "no SDL library")

        await self.page._show(self.holder, True)

        kwargs = self.table.call_args.kwargs
        self.assertFalse(self.page.v2)
        self.assertIsNone(kwargs["host"])
        self.assertEqual(kwargs["words"].how_body, "manager.vpx_input.how_body")

    async def test_no_program_is_keys_only_without_asking_the_library(self) -> None:
        self.page.pads.program = ""

        await self.page._show(self.holder, True)

        self.listen.assert_not_called()
        self.assertIsNone(self.table.call_args.kwargs["host"])

    async def test_a_table_that_fails_to_draw_falls_back_to_keys_and_lets_go(self) -> None:
        self.table.side_effect = [RuntimeError("boom"), Mock()]

        with self.assertLogs("manager.settings.vpx_input", "WARNING"):
            await self.page._show(self.holder, True)

        self.assertFalse(self.page.v2)
        self.assertEqual(self.tap.closed, 1)
        self.assertIsNone(self.table.call_args.kwargs["host"])

    async def test_a_page_gone_before_the_library_loaded_lets_go_of_it(self) -> None:
        self.holder.is_deleted = True

        await self.page._show(self.holder, True)

        self.table.assert_not_called()
        self.assertEqual(self.tap.closed, 1)

    async def test_the_setup_page_draws_no_how_mapping_works(self) -> None:
        await self.page._show(self.holder, False)

        self.assertIsNone(self.table.call_args.kwargs["how_open"])


if __name__ == "__main__":
    unittest.main()
