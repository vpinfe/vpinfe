from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

from nicegui import run
from screeninfo import Monitor

from common.config import config_schema
from common.host import display_service
from common.i18n import t
from common.settings import config_service
from frontend.chromium_manager import MonitorInfo
from manager.kit import panel
from manager.settings import screens, settings
from manager.settings import settings as settings_page
from manager.workbench import all_settings
from tests.support.workbench_patch import patch_workbench

WINDOWS = ("windows.playfield", "windows.backglass", "windows.scoreview")

# As screeninfo reports them, which is the list the frontend opens windows by on Linux and
# Windows...
WIDE = Monitor(x=0, y=0, width=2560, height=1440, width_mm=597, height_mm=336,
               name="DP-1", is_primary=True)
TALL = Monitor(x=2560, y=0, width=1080, height=1920, width_mm=336, height_mm=597,
               name="HDMI-1", is_primary=False)
# ...and as the frontend reads them for itself on macOS.
BUILT_IN = MonitorInfo(x=0, y=0, width=1728, height=1117)
BESIDE = MonitorInfo(x=1728, y=-200, width=1920, height=1080)


def _option(section: str) -> dict[str, Any]:
    return next(option for block in config_service.schema()["sections"]
                if block["name"] == section
                for option in block["options"] if option["key"] == "screen_id")


class PickedFromTheScreensTests(unittest.IsolatedAsyncioTestCase):
    def _drawn(self, section: str, value: Any, found: list[Any]) -> tuple[Mock, Mock, Mock]:
        save = AsyncMock(return_value=True)
        self.refill = Mock()
        with patch.object(panel, "select") as select, patch.object(panel, "number") as number:
            settings.control_for(_option(section), value, save, refill=self.refill,
                                 suggestions={config_schema.SUGGEST_SCREENS: found})
        return select, number, save

    def test_each_window_s_screen_is_one_of_the_screens_and_not_a_number(self) -> None:
        for section in WINDOWS:
            with self.subTest(section=section):
                select, number, _ = self._drawn(section, 0, [WIDE, TALL])

                number.assert_not_called()
                self.assertEqual(select.call_args.args[1], "0")
                self.assertEqual(
                    {key: label for key, label in select.call_args.args[0].items() if key},
                    {"0": t("manager.screens.at", width=2560, height=1440, x=0, y=0),
                     "1": t("manager.screens.at", width=1080, height=1920, x=2560, y=0)})

    def test_the_frontend_s_own_list_on_macos_is_described_the_same_way(self) -> None:
        select, _, _ = self._drawn("windows.playfield", 0, [BUILT_IN, BESIDE])

        self.assertEqual(select.call_args.args[0],
                         {"0": "1728 x 1117 at 0, 0", "1": "1920 x 1080 at 1728, -200"})

    def test_none_is_offered_where_no_window_is_the_default(self) -> None:
        playfield, _, _ = self._drawn("windows.playfield", 0, [WIDE])
        backglass, _, _ = self._drawn("windows.backglass", "", [WIDE])

        self.assertNotIn("", playfield.call_args.args[0])
        self.assertEqual(backglass.call_args.args[0][""], t("word.none"))
        self.assertEqual(backglass.call_args.args[1], "")

    async def test_a_pick_stores_the_screen_s_number(self) -> None:
        select, _, save = self._drawn("windows.backglass", "", [WIDE, TALL])
        pick = select.call_args.args[2]

        await pick(SimpleNamespace(value="1"))
        await pick(SimpleNamespace(value=""))

        self.assertEqual([one.args for one in save.await_args_list], [(1,), ("",)])

    async def test_a_pick_away_from_a_screen_that_is_not_connected_draws_it_again(self) -> None:
        select, _, _ = self._drawn("windows.backglass", 2, [WIDE])

        await select.call_args.args[2](SimpleNamespace(value="0"))

        self.refill.assert_called_once_with()

    async def test_a_pick_between_connected_screens_leaves_the_page_where_it_is(self) -> None:
        select, _, _ = self._drawn("windows.backglass", 1, [WIDE, TALL])

        await select.call_args.args[2](SimpleNamespace(value="0"))

        self.refill.assert_not_called()

    def test_a_stored_screen_that_is_not_connected_stays_chosen_and_is_marked(self) -> None:
        with patch.object(panel, "value_state") as value_state:
            select, _, _ = self._drawn("windows.backglass", 2, [WIDE])

        self.assertEqual(select.call_args.args[1], "2")
        self.assertEqual(select.call_args.args[0]["2"],
                         t("manager.screens.numbered", number="3"))
        value_state.assert_called_with("missing", t("manager.screens.not_connected"))

    def test_a_connected_screen_or_none_is_not_marked(self) -> None:
        for value in (1, ""):
            with self.subTest(value=value), \
                    patch.object(panel, "value_state") as value_state:
                self._drawn("windows.backglass", value, [WIDE, TALL])

                self.assertEqual({one.args for one in value_state.call_args_list}, {("", "")})

    def test_where_the_screens_cannot_be_read_it_is_the_number_it_was(self) -> None:
        select, number, _ = self._drawn("windows.backglass", 2, [])

        select.assert_not_called()
        number.assert_called_once()


class TheScreensAreReadForThePageTests(unittest.IsolatedAsyncioTestCase):
    def test_every_window_the_schema_declares_offers_the_screens(self) -> None:
        self.assertEqual({section: _option(section)["suggest"] for section in WINDOWS},
                         dict.fromkeys(WINDOWS, config_schema.SUGGEST_SCREENS))

    async def test_they_are_read_again_off_the_loop_for_a_page_that_picks_one(self) -> None:
        blocks = config_service.schema()["sections"]
        self.enterContext(patch.object(
            settings.run, "io_bound",
            new=AsyncMock(side_effect=lambda call, *args, **kwargs: call(*args, **kwargs))))
        read = self.enterContext(patch.object(display_service, "get_display_monitors",
                                              return_value=[WIDE, TALL]))

        offered = await settings._suggestions(Mock(), blocks, WINDOWS)

        self.assertEqual(offered[config_schema.SUGGEST_SCREENS], [WIDE, TALL])
        read.assert_called_once_with(refresh=True)


class AReportedDisplayShowsItsScreenTests(unittest.IsolatedAsyncioTestCase):
    NAMED = "Built-in Retina Display [0, 0]"
    FIELD = SimpleNamespace(key="Player.PlayfieldDisplay", type="text", label="Display",
                            default="", choices=(), blank="",
                            reported=(NAMED, "Built-in Retina Display"),
                            scopes=("launcher", "entry"), help="", description="")

    def test_a_name_with_a_position_shows_the_screen_there(self) -> None:
        self.assertEqual(
            screens.reported([self.NAMED, "Studio Display [1728, -200]"], [BUILT_IN, BESIDE]),
            {self.NAMED: "Built-in Retina Display - 1728 x 1117 at 0, 0",
             "Studio Display [1728, -200]": "Studio Display - 1920 x 1080 at 1728, -200"})

    def test_a_bare_name_or_one_where_no_screen_is_stays_as_vpx_said_it(self) -> None:
        names = ["Built-in Retina Display", "LG TV [3648, 0]"]

        self.assertEqual(screens.reported(names, [BUILT_IN, BESIDE]),
                         dict(zip(names, names, strict=True)))

    def test_the_playfield_reads_the_same_on_both_pages(self) -> None:
        on_hardware = screens.choices([BUILT_IN, BESIDE], "0", blank=False)["0"]

        self.assertIn(on_hardware, screens.reported([self.NAMED], [BUILT_IN, BESIDE])[self.NAMED])

    async def _offered(self, field: Any) -> tuple[Mock, Mock]:
        values = {field.key: {"value": self.NAMED, "scope": "launcher"}}
        context = {"library": Mock(), "launcher": {"launcher_id": "probe"},
                   "config_scope": "launcher", "rebuild": AsyncMock()}
        self.enterContext(patch_workbench("ui"))
        self.enterContext(patch_workbench("_config_values",
                                       new=AsyncMock(return_value=values)))
        self.enterContext(patch_workbench("_set_by_tables",
                                       new=AsyncMock(return_value={})))
        self.enterContext(patch.object(
            run, "io_bound",
            new=AsyncMock(side_effect=lambda call, *args, **kwargs: call(*args, **kwargs))))
        read = self.enterContext(patch.object(display_service, "get_display_monitors",
                                              return_value=[BUILT_IN, BESIDE]))
        control_for = self.enterContext(patch.object(settings_page, "control_for"))
        await all_settings._setting_entries(context, [("", "", [field])])
        return control_for, read

    async def test_the_launcher_page_offers_each_name_with_its_screen(self) -> None:
        control_for, _ = await self._offered(self.FIELD)

        self.assertEqual(control_for.call_args.kwargs["suggestions"][all_settings.REPORTED],
                         {self.NAMED: "Built-in Retina Display - 1728 x 1117 at 0, 0",
                          "Built-in Retina Display": "Built-in Retina Display"})

    async def test_the_screens_are_not_read_for_a_setting_vpx_reports_nothing_for(self) -> None:
        _, read = await self._offered(SimpleNamespace(**{**vars(self.FIELD), "reported": ()}))

        read.assert_not_called()


if __name__ == "__main__":
    unittest.main()
