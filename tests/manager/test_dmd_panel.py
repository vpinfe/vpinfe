"""The DMD panel switch: what it writes for VPinFE, and what it tells Visual Pinball."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

from nicegui import ui

from common.host import tools
from common.i18n import t
from common.settings import config_service
from manager.settings import dmd_panel

SCHEMA = config_service.schema()["sections"]


def _held(**keys: Any) -> dict[str, dict[str, Any]]:
    return {"real_dmd": keys}


class IsOnTests(unittest.TestCase):
    def test_it_is_what_is_stored(self) -> None:
        for stored, expected in (("true", True), (True, True), ("false", False),
                                 (False, False), ("", False), ("off", False)):
            with self.subTest(stored=stored):
                self.assertEqual(dmd_panel.is_on(_held(enabled=stored), SCHEMA), expected)

    def test_it_is_off_until_it_is_set(self) -> None:
        self.assertFalse(dmd_panel.is_on({}, SCHEMA))


class KindOfTests(unittest.TestCase):
    def test_pin2dmd_is_the_only_kind_vpinfe_holds(self) -> None:
        self.assertEqual(dmd_panel.kind_of(_held(pin2dmd_enabled="true"), SCHEMA), "PIN2DMD")
        self.assertEqual(dmd_panel.kind_of(_held(zedmd_serial_port="/dev/ttyUSB0"), SCHEMA), "")
        self.assertEqual(dmd_panel.kind_of({}, SCHEMA), "")


class WritesTests(unittest.TestCase):
    def test_off_writes_the_switch_and_nothing_else(self) -> None:
        self.assertEqual(dmd_panel.writes(False, "PIN2DMD"), {"real_dmd": {"enabled": False}})

    def test_on_with_no_kind_writes_the_switch_alone(self) -> None:
        self.assertEqual(dmd_panel.writes(True, ""), {"real_dmd": {"enabled": True}})

    def test_pin2dmd_sets_its_own_setting(self) -> None:
        self.assertEqual(dmd_panel.writes(True, "PIN2DMD"),
                         {"real_dmd": {"enabled": True, "pin2dmd_enabled": True}})

    def test_another_kind_turns_pin2dmd_off(self) -> None:
        for kind in ("ZeDMD", "Pixelcade"):
            with self.subTest(kind=kind):
                self.assertEqual(dmd_panel.writes(True, kind),
                                 {"real_dmd": {"enabled": True, "pin2dmd_enabled": False}})

    def test_a_kind_nobody_offers_is_not_written(self) -> None:
        self.assertEqual(dmd_panel.writes(True, "Other"), {"real_dmd": {"enabled": True}})

    def test_every_key_it_writes_is_in_the_schema(self) -> None:
        declared = {one["key"] for block in SCHEMA if block.get("name") == "real_dmd"
                    for one in block["options"]}

        for kind in ("", "ZeDMD", "PIN2DMD", "Pixelcade"):
            self.assertLessEqual(set(dmd_panel.writes(True, kind)["real_dmd"]), declared)


class RowsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.write = AsyncMock(return_value=True)
        self.refill = Mock()
        self.follow = AsyncMock()
        self.switch = self.enterContext(patch.object(dmd_panel.panel, "switch"))
        self.choice = self.enterContext(patch.object(dmd_panel.panel, "choice"))
        self.ffmpeg = self.enterContext(patch.object(
            dmd_panel.tools, "resolve", return_value=tools.Found(tools.FFMPEG, tools.State.FOUND)))

    def _rows(self, values: dict | None = None, **kwargs: Any) -> list:
        return dmd_panel.rows(values or {}, SCHEMA, self.write, self.refill, self.follow,
                              **kwargs)

    def _on(self, event: Any) -> Any:
        return SimpleNamespace(value=event)

    def test_off_it_is_the_switch_alone(self) -> None:
        entries = self._rows()

        self.assertEqual([label for label, _ in entries], [t("manager.dmd_panel.switch")])
        self.assertIs(self.switch.call_args.args[0], False)
        self.choice.assert_not_called()

    def test_on_it_asks_which_panel(self) -> None:
        entries = self._rows(_held(enabled="true"))

        self.assertEqual([label for label, _ in entries],
                         [t("manager.dmd_panel.switch"), t("manager.dmd_panel.kind")])
        [options, picked] = self.choice.call_args.args[:2]
        self.assertEqual([key for key, _, _ in options], ["ZeDMD", "PIN2DMD", "Pixelcade"])
        self.assertEqual(picked, "")

    def test_a_missing_ffmpeg_is_said_beside_the_panel_that_is_on(self) -> None:
        self.ffmpeg.return_value = tools.Found(tools.FFMPEG, tools.State.MISSING)

        entries = self._rows(_held(enabled="true"))

        self.assertEqual(entries[-1][0], dmd_panel.panel.FULL)

    def test_it_is_not_said_while_the_panel_is_off(self) -> None:
        self.ffmpeg.return_value = tools.Found(tools.FFMPEG, tools.State.MISSING)

        self.assertEqual([label for label, _ in self._rows()], [t("manager.dmd_panel.switch")])

    def test_the_kind_it_is_told_is_the_one_shown(self) -> None:
        self._rows(_held(enabled="true"), kind="Pixelcade")

        self.assertEqual(self.choice.call_args.args[1], "Pixelcade")

    def test_the_kind_vpinfe_holds_wins_over_the_one_it_is_told(self) -> None:
        self._rows(_held(enabled="true", pin2dmd_enabled="true"), kind="ZeDMD")

        self.assertEqual(self.choice.call_args.args[1], "PIN2DMD")

    async def test_switching_on_is_written_and_the_question_drawn(self) -> None:
        self._rows()

        await self.switch.call_args.args[1](self._on(True))

        self.write.assert_awaited_once_with({"real_dmd": {"enabled": True}})
        self.refill.assert_called_once_with()
        self.follow.assert_not_awaited()

    async def test_switching_on_with_a_kind_known_tells_visual_pinball(self) -> None:
        self._rows(kind="ZeDMD")

        await self.switch.call_args.args[1](self._on(True))

        self.write.assert_awaited_once_with(
            {"real_dmd": {"enabled": True, "pin2dmd_enabled": False}})
        self.follow.assert_awaited_once_with("ZeDMD")

    async def test_switching_off_tells_visual_pinball_nothing(self) -> None:
        self._rows(_held(enabled="true"), kind="ZeDMD")

        await self.switch.call_args.args[1](self._on(False))

        self.write.assert_awaited_once_with({"real_dmd": {"enabled": False}})
        self.follow.assert_not_awaited()

    async def test_a_switch_that_was_not_kept_is_put_back_and_tells_nothing(self) -> None:
        self.write.return_value = False
        self._rows(kind="ZeDMD")

        await self.switch.call_args.args[1](self._on(True))

        self.follow.assert_not_awaited()
        self.refill.assert_called_once_with()

    async def test_a_kind_picked_is_written_and_told(self) -> None:
        self._rows(_held(enabled="true"))

        await self.choice.call_args.args[2](self._on("PIN2DMD"))

        self.write.assert_awaited_once_with(
            {"real_dmd": {"enabled": True, "pin2dmd_enabled": True}})
        self.follow.assert_awaited_once_with("PIN2DMD")
        self.refill.assert_not_called()

    async def test_the_kind_already_picked_is_not_written_again(self) -> None:
        self._rows(_held(enabled="true"), kind="ZeDMD")

        await self.choice.call_args.args[2](self._on("ZeDMD"))

        self.write.assert_not_awaited()
        self.follow.assert_not_awaited()

    async def test_a_kind_that_was_not_kept_is_put_back_and_tells_nothing(self) -> None:
        self.write.return_value = False
        self._rows(_held(enabled="true"), kind="ZeDMD")

        await self.choice.call_args.args[2](self._on("Pixelcade"))

        self.follow.assert_not_awaited()
        self.refill.assert_called_once_with()

    async def test_with_nobody_to_tell_the_answer_is_still_kept(self) -> None:
        entries = dmd_panel.rows(_held(enabled="true"), SCHEMA, self.write, self.refill)
        self.assertEqual(len(entries), 2)

        await self.choice.call_args.args[2](self._on("ZeDMD"))

        self.write.assert_awaited_once()


class DrawnTests(unittest.TestCase):
    def _radios(self, values: dict, **kwargs: Any) -> list:
        with ui.column() as body:
            for _, draw in dmd_panel.rows(values, SCHEMA, AsyncMock(), Mock(), **kwargs):
                draw()
        return [one for one in body.descendants() if isinstance(one, ui.radio)]

    def test_no_kind_picked_yet_draws_none_picked(self) -> None:
        [radio] = self._radios(_held(enabled="true"))

        self.assertIsNone(radio.value)
        self.assertEqual(list(radio.options), ["ZeDMD", "PIN2DMD", "Pixelcade"])

    def test_the_kind_known_is_the_one_picked(self) -> None:
        [radio] = self._radios(_held(enabled="true"), kind="Pixelcade")

        self.assertEqual(radio.value, "Pixelcade")

    def test_off_draws_no_question(self) -> None:
        self.assertEqual(self._radios({}), [])


if __name__ == "__main__":
    unittest.main()
