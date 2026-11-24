"""What a launcher's Details section does after it writes."""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

from nicegui import run

from manager.frontend import launchers
from manager.kit import panel
from manager.settings import settings as settings_page
from manager.workbench import launcher
from tests.support.clicks import press
from tests.support.workbench_patch import patch_workbench

LAUNCHER = {"launcher_id": "wide", "display_name": "VPX (4K)", "app_name": "Visual Pinball X",
            "app": "vpx", "enabled": True, "settings": {}, "fields": []}


class Details:
    """A launcher's Details section drawn with its writes caught, and its controls' saves
    in hand."""

    def __init__(self, *, refused: str = "", default: bool = False,
                 enabled: bool = True, program: str = "ok") -> None:
        self.launcher = {**LAUNCHER, "enabled": enabled,
                         "fields": [{"key": "bin_path", "path": "exe", "label": "Program"}],
                         "checks": {"bin_path": {"state": program}}}
        self.recheck, self.refresh, self.retitle = AsyncMock(), AsyncMock(), Mock()
        self.put = AsyncMock(return_value={},
                             side_effect=RuntimeError(refused) if refused else None)
        self.rebuild = AsyncMock()
        self.context: dict[str, Any] = {
            "library": Mock(), "launcher": self.launcher,
            "launchers": [self.launcher, {}],
            "defaults": {"vpx": "wide" if default else "other"}, "rebuild": self.rebuild,
            "retitle": self.retitle,
            "state": {"recheck_trouble": self.recheck, "refresh_launchers": self.refresh}}

    async def __aenter__(self) -> Details:
        self._patches: list[Any] = [patch_workbench("ui"), patch_workbench("_rows"),
                                    patch.object(run, "io_bound", new=self.put),
                                    patch.object(panel, "field"),
                                    patch.object(panel, "switch"),
                                    patch.object(settings_page, "control_for")]
        _, _, _, field, switch, _ = [one.start() for one in self._patches]
        await launcher._launcher_details(self.context)
        self.rename = field.call_args.args[1]
        self.default, self.enabled = switch.call_args_list[:2]
        self.flip = self.enabled.args[1]
        return self

    async def __aexit__(self, *_exc: object) -> None:
        for one in self._patches:
            one.stop()

    def sent(self) -> list[dict[str, Any]]:
        return [one.args[2] for one in self.put.await_args_list if isinstance(one.args[2], dict)]


class TroubleRecheck(unittest.IsolatedAsyncioTestCase):
    async def test_a_saved_change_rechecks_the_badge(self) -> None:
        async with Details() as setup:
            await setup.rename("Wide")
        setup.recheck.assert_awaited_once()

    async def test_a_refused_one_does_not(self) -> None:
        async with Details(refused="refused") as setup:
            await setup.rename("Wide")
        setup.recheck.assert_not_awaited()


class Rename(unittest.IsolatedAsyncioTestCase):
    async def test_the_next_write_carries_the_new_name(self) -> None:
        async with Details() as setup:
            await setup.rename("Wide")
            await press(setup.flip, Mock(value=True))
        self.assertEqual([one["display_name"] for one in setup.sent()], ["Wide", "Wide"])

    async def test_the_grid_and_the_title_follow_it(self) -> None:
        async with Details() as setup:
            await setup.rename("Wide")
        setup.refresh.assert_awaited_once()
        setup.retitle.assert_called_once_with("Wide")

    async def test_a_refusal_is_the_fields_to_say(self) -> None:
        taken = "Another launcher is already called Wide."
        async with Details(refused=taken) as setup:
            said = await setup.rename("Wide")
        self.assertEqual(said, taken)
        self.assertEqual(setup.launcher["display_name"], "VPX (4K)")
        setup.retitle.assert_not_called()

    async def test_the_same_name_writes_nothing(self) -> None:
        async with Details() as setup:
            self.assertEqual(await setup.rename(" VPX (4K) "), "")
        setup.put.assert_not_awaited()

    async def test_blank_is_the_programs_name(self) -> None:
        async with Details() as setup:
            await setup.rename("  ")
        self.assertEqual(setup.sent()[0]["display_name"], "Visual Pinball X")


class Default(unittest.IsolatedAsyncioTestCase):
    async def test_turning_it_on_makes_it_the_default(self) -> None:
        async with Details() as setup:
            await press(setup.default.args[1], Mock(value=True))
        assert setup.put.await_args is not None
        self.assertEqual(setup.put.await_args.args,
                         (setup.context["library"].make_launcher_default, "wide"))
        setup.refresh.assert_awaited_once()
        setup.rebuild.assert_awaited_once()

    async def test_the_default_cannot_be_turned_off(self) -> None:
        """Nothing says which launcher would take over."""
        async with Details(default=True) as setup:
            pass
        self.assertIs(setup.default.args[0], True)
        self.assertIs(setup.default.kwargs["disabled"], True)

    async def test_a_switched_off_one_cannot_be_made_the_default(self) -> None:
        async with Details(enabled=False) as setup:
            pass
        self.assertIs(setup.default.kwargs["disabled"], True)
        self.assertTrue(setup.default.kwargs["hint"])

    async def test_one_with_no_program_cannot_be_made_the_default(self) -> None:
        """Said over switched off: switching it on would still leave nothing to run."""
        async with Details(enabled=False, program="unset") as setup:
            pass
        self.assertIs(setup.default.kwargs["disabled"], True)
        self.assertEqual(setup.default.kwargs["hint"], "“VPX (4K)” has no program")


class AFieldStandingForAFile(unittest.TestCase):
    FIELD = {"key": "ini_path", "path": "file", "default_path": "/prefs/VPinballX.ini",
             "default_exists": True}

    def test_it_is_whose_default_it_is(self) -> None:
        option = launchers.standing_option(self.FIELD, "Visual Pinball X")
        self.assertEqual(option["default_whose"], "Visual Pinball X's default")

    def test_a_file_not_made_yet_is_said_beside_the_field_not_in_the_hover(self) -> None:
        option = launchers.standing_option({**self.FIELD, "default_exists": False},
                                            "Visual Pinball X")
        self.assertEqual(option["default_whose"], "Visual Pinball X's default")

    def test_a_field_standing_for_nothing_is_left_as_it_came(self) -> None:
        field = {"key": "bin_path", "path": "exe"}
        self.assertIs(launchers.standing_option(field, "Visual Pinball X"), field)


if __name__ == "__main__":
    unittest.main()
