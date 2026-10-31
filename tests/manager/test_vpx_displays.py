"""Launchers > Visual Pinball X > Displays: the picture over the launcher's ini, and the
launchers that move with VPinFE's screens."""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock, patch

from screeninfo import Monitor

from apps.vpx import screens as vpx
from common.i18n import t
from common.settings import config_service
from manager.kit import panel
from manager.settings import displays, vpx_displays
from manager.settings import screens_picture as picture
from manager.settings.screens import Screen
from tests.support.clicks import press

SCHEMA = config_service.schema()["sections"]

# A tall screen the OS has turned, the backglass, a score strip, and a second tall one.
TALL = Screen(1, "SAMSUNG", 2160, 3840, 0, 0)
BACK = Screen(2, "DELL P2419H", 1920, 1080, 2160, 0)
STRIP = Screen(3, "CVT DVI", 1280, 390, 4080, 0)
TALL_TOO = Screen(4, "LG ULTRAFINE", 2160, 3840, 5360, 0)
NAMELESS = Screen(5, "", 1280, 720, 7520, 0)
HERE = [TALL, BACK, STRIP, TALL_TOO, NAMELESS]

SAMSUNG = "SAMSUNG [0, 0]"
DELL = "DELL P2419H [2160, 0]"
CVT = "CVT DVI [4080, 0]"
LG = "LG ULTRAFINE [5360, 0]"


def _flat(**over: str) -> dict[str, str]:
    base = {"Player.PlayfieldDisplay": SAMSUNG, "Backglass.BackglassOutput": "1",
            "Backglass.BackglassDisplay": DELL, "ScoreView.ScoreViewOutput": "0",
            "Topper.TopperOutput": "0", "TableOverride.ViewCabRotation": "0.0"}
    base.update({key.replace("__", "."): value for key, value in over.items()})
    return base


def _standing(on: dict | None = None, turn: tuple[str, str] = ("portrait", "0")) -> Any:
    return displays.Standing({"playfield": 1, "backglass": 2, "scoreview": None,
                              **(on or {})}, turn)


class _Library:
    def __init__(self, flat: dict[str, str] | None = None) -> None:
        self.flat = flat if flat is not None else _flat()
        self.write_launcher_config = Mock()
        self.put_config = Mock()
        self.launcher_program = Mock(return_value=None)
        self.launchers = Mock(return_value={"launchers": [
            {"launcher_id": "vpx-1", "app": "vpx", "enabled": True, "has_config": True}]})
        self.launcher_config_values = Mock(side_effect=self._values)
        self.launcher_config_groups = Mock(return_value=[SimpleNamespace(
            key="displays", settings=[SimpleNamespace(key="Player.PlayfieldDisplay",
                                                      reported=[])])])

    def _values(self, _launcher_id: str) -> dict[str, dict[str, str]]:
        return {key: {"value": value} for key, value in self.flat.items()}

    def launcher_config(self, launcher_id: str) -> dict[str, Any]:
        """The one read the follow makes. Its groups stay as the fakes above build them, so
        a test reads them back through `config_groups` patched to hand them over."""
        return {"values": self.launcher_config_values(launcher_id),
                "program": self.launcher_program(launcher_id),
                "groups": self.launcher_config_groups(launcher_id)}


async def _inline(function: Any, *args: Any, **kwargs: Any) -> Any:
    return function(*args, **kwargs)


def _page(library: Any = None, flat: dict | None = None, *, vpinfe: Any = None,
          cabinet: bool = True, units_now: str = "cm", logged: tuple = ()) -> Any:
    library = library or _Library()
    page = vpx_displays.Page(
        library, "vpx-1", Mock(), flat=dict(_flat() if flat is None else flat),
        vpinfe=vpinfe or _standing(), here=HERE, logged=logged, cabinet=cabinet,
        units_now=units_now, defaults={"Player.ScreenWidth": "95.89",
                                       "Player.ScreenHeight": "53.94"})
    return page


def _heading(key: str, *keys: str, pairs: tuple = ()) -> tuple[Any, list[Any]]:
    heading = SimpleNamespace(key=key, label=key.title(), note="", pairs=pairs)
    return heading, [SimpleNamespace(key=one, label=one) for one in keys]


class AppliesTests(unittest.TestCase):
    def test_a_displays_group_that_holds_the_playfield_s_display_is_drawn(self) -> None:
        group = SimpleNamespace(key="displays",
                                settings=[SimpleNamespace(key="Player.PlayfieldDisplay")])

        self.assertTrue(vpx_displays.applies(group))

    def test_another_group_or_another_program_is_not(self) -> None:
        mine = [SimpleNamespace(key="Player.PlayfieldDisplay")]

        self.assertFalse(vpx_displays.applies(SimpleNamespace(key="sound", settings=mine)))
        self.assertFalse(vpx_displays.applies(
            SimpleNamespace(key="displays", settings=[SimpleNamespace(key="Window.Left")])))


class NamedTests(unittest.TestCase):
    def test_parts_are_read_out_as_a_sentence_would(self) -> None:
        one, two, three = ("backglass", "scoreview", "topper")
        labels = {part.key: part.label for part in vpx_displays.picture.vpx_parts()}

        self.assertEqual(vpx_displays.named([one]), labels[one])
        self.assertEqual(vpx_displays.named([one, two]), f"{labels[one]} and {labels[two]}")
        self.assertEqual(vpx_displays.named([one, two, three]),
                         f"{labels[one]}, {labels[two]} and {labels[three]}")


class TrimmedTests(unittest.TestCase):
    def _shown(self) -> list:
        return [_heading("playfield", "Player.PlayfieldDisplay", "Player.PlayfieldFullScreen"),
                _heading("backglass", "Backglass.BackglassOutput", "Backglass.BackglassDisplay",
                         "Backglass.BackglassFullScreen"),
                _heading("topper", "Topper.TopperOutput", "Topper.TopperDisplay",
                         "Topper.TopperWndX"),
                _heading("cabinet", "Player.ScreenWidth", "Player.ScreenHeight",
                         "Player.BGSet")]

    def _kept(self, on: dict) -> dict[str, list[str]]:
        return {heading.key: [one.key for one in fields]
                for heading, fields in vpx_displays.trimmed(self._shown(), on)}

    def test_what_the_picture_draws_is_taken_out_of_a_window(self) -> None:
        kept = self._kept({"playfield": 1, "backglass": 2, "topper": None})

        self.assertEqual(kept["playfield"], ["Player.PlayfieldFullScreen"])
        self.assertEqual(kept["backglass"], ["Backglass.BackglassFullScreen"])

    def test_a_part_held_on_no_screen_has_no_rows(self) -> None:
        self.assertNotIn("topper", self._kept({"playfield": 1, "topper": None}))

    def test_a_part_on_a_screen_or_inside_the_playfield_keeps_its_rows(self) -> None:
        self.assertEqual(self._kept({"topper": 3})["topper"], ["Topper.TopperWndX"])
        self.assertEqual(self._kept({"topper": picture.INSIDE})["topper"],
                         ["Topper.TopperWndX"])

    def test_the_playfield_always_shows(self) -> None:
        self.assertIn("playfield", self._kept({"playfield": None}))

    def test_the_size_row_is_drawn_apart_so_the_cabinet_loses_its_two_fields(self) -> None:
        self.assertEqual(self._kept({})["cabinet"], ["Player.BGSet"])

    def test_a_heading_left_with_nothing_is_dropped(self) -> None:
        shown = [_heading("topper", "Topper.TopperOutput", "Topper.TopperDisplay")]

        self.assertEqual(vpx_displays.trimmed(shown, {"topper": 3}), [])


class PairsForTests(unittest.TestCase):
    def test_a_part_inside_the_playfield_says_its_position_is_the_playfield_s(self) -> None:
        pair = SimpleNamespace(key="position", label="Position", note="", joiner="x",
                               keys=("a", "b"))
        shown = [_heading("topper", "a", "b", pairs=(pair,))]

        [found] = vpx_displays.pairs_for(shown, {"topper": picture.INSIDE})

        self.assertEqual(found.note, t("manager.vpx_displays.in_the_playfield_window"))
        self.assertEqual(found.keys, ("a", "b"))

    def test_a_part_on_a_screen_keeps_its_pair_as_it_was(self) -> None:
        pair = SimpleNamespace(key="position", label="Position", note="", joiner="x",
                               keys=("a", "b"))
        shown = [_heading("topper", "a", "b", pairs=(pair,))]

        self.assertEqual(vpx_displays.pairs_for(shown, {"topper": 3}), [pair])

    def test_another_pair_of_a_part_inside_is_left_alone(self) -> None:
        pair = SimpleNamespace(key="size", label="Size", note="", joiner="x", keys=("a", "b"))
        shown = [_heading("topper", "a", "b", pairs=(pair,))]

        self.assertEqual(vpx_displays.pairs_for(shown, {"topper": picture.INSIDE}), [pair])


class SizeRowGoesBeforeTests(unittest.TestCase):
    def test_it_goes_before_the_row_that_follows_the_size(self) -> None:
        shown = [_heading("cabinet", "Player.BGSet", "Player.ScreenWidth", "Player.ScreenHeight",
                          "Player.ScreenInclination")]

        self.assertEqual(vpx_displays.size_row_goes_before(shown), "Player.ScreenInclination")

    def test_with_nothing_after_it_goes_last(self) -> None:
        shown = [_heading("cabinet", "Player.BGSet", "Player.ScreenWidth", "Player.ScreenHeight")]

        self.assertEqual(vpx_displays.size_row_goes_before(shown), "")

    def test_with_no_size_fields_it_goes_last(self) -> None:
        self.assertEqual(vpx_displays.size_row_goes_before(
            [_heading("cabinet", "Player.BGSet")]), "")
        self.assertEqual(vpx_displays.size_row_goes_before([]), "")


class TurnedTests(unittest.TestCase):
    def _turns(self, **over: str) -> list[str | None]:
        return [_page(flat=_flat(TableOverride__ViewCabRotation=turn, **over)).turned()
                for turn in ("0", "90", "180", "270")]

    def test_a_tall_screen_left_at_0_is_not_turned(self) -> None:
        self.assertEqual(_page(flat=_flat(TableOverride__ViewCabRotation="0.0")).turned(), "0")
        self.assertEqual(_page(flat=_flat(TableOverride__ViewCabRotation="")).turned(), "0")

    def test_a_tall_screen_is_turned_clockwise_a_quarter_at_a_time(self) -> None:
        self.assertEqual(self._turns(), ["0", "90", "180", "270"])

    def test_a_wide_screen_at_0_faces_its_right_edge_to_the_player(self) -> None:
        self.assertEqual(self._turns(Player__PlayfieldDisplay=DELL), ["270", "0", "90", "180"])

    def test_a_turn_that_faces_no_edge_is_none(self) -> None:
        self.assertIsNone(_page(flat=_flat(TableOverride__ViewCabRotation="45")).turned())

    def test_a_playfield_with_no_screen_is_none(self) -> None:
        self.assertIsNone(_page(flat=_flat(Player__PlayfieldDisplay="")).turned())


class DifferencesTests(unittest.TestCase):
    def test_a_launcher_that_matches_differs_in_nothing(self) -> None:
        self.assertEqual(_page().differences(), [])

    def test_a_part_on_another_screen_differs(self) -> None:
        page = _page(flat=_flat(Backglass__BackglassDisplay=CVT))

        self.assertEqual(page.differences(), ["backglass"])

    def test_a_part_vpinfe_draws_and_visual_pinball_does_not_differs(self) -> None:
        page = _page(flat=_flat(Backglass__BackglassOutput="0"))

        self.assertEqual(page.differences(), ["backglass"])

    def test_a_topper_with_no_screen_in_vpinfe_is_visual_pinball_s_alone(self) -> None:
        page = _page(flat=_flat(Topper__TopperOutput="1", Topper__TopperDisplay=CVT))

        self.assertEqual(page.differences(), [])

    def test_a_topper_on_another_screen_than_vpinfe_s_differs(self) -> None:
        page = _page(flat=_flat(Topper__TopperOutput="1", Topper__TopperDisplay=CVT),
                     vpinfe=_standing({"topper": 4}))

        self.assertEqual(page.differences(), ["topper"])

    def test_a_playfield_turned_otherwise_than_vpinfe_turns_it_differs(self) -> None:
        page = _page(flat=_flat(TableOverride__ViewCabRotation="90"))

        self.assertEqual(page.differences(), ["playfield"])

    def test_the_playfield_on_another_screen_is_named_once_whatever_its_turn(self) -> None:
        page = _page(flat=_flat(Player__PlayfieldDisplay=LG, TableOverride__ViewCabRotation="90"))

        self.assertEqual(page.differences(), ["playfield"])

    def test_a_turn_that_differs_is_named_on_a_desk_too(self) -> None:
        page = _page(flat=_flat(TableOverride__ViewCabRotation="90"), cabinet=False)

        self.assertEqual(page.differences(), ["playfield"])

    def test_a_turn_vpinfe_holds_that_faces_no_edge_is_not_a_difference(self) -> None:
        page = _page(flat=_flat(TableOverride__ViewCabRotation="90"),
                     vpinfe=_standing(turn=("portrait", "45")))

        self.assertEqual(page.differences(), [])


class MovingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.library = _Library()
        self.run = self.enterContext(  # type: ignore[method-assign]
            patch.object(vpx_displays.run, "io_bound", new=AsyncMock(side_effect=_inline)))
        self.notify = self.enterContext(patch.object(vpx_displays.ui, "notify"))

    def _written(self) -> dict[str, str]:
        return self.library.write_launcher_config.call_args.args[1]

    async def test_a_part_moved_is_written_by_the_name_visual_pinball_stores(self) -> None:
        page = _page(self.library)

        self.assertTrue(await press(page.move, {"backglass": 3}))

        self.assertEqual(self.library.write_launcher_config.call_args.args[0], "vpx-1")
        self.assertEqual(self._written()["Backglass.BackglassDisplay"], CVT)
        self.assertEqual(self._written()["Backglass.BackglassOutput"], "1")
        self.assertEqual(page.on["backglass"], 3)
        page.rebuild.assert_called_once_with()

    async def test_the_name_visual_pinball_logged_for_a_screen_wins(self) -> None:
        page = _page(self.library, logged=("Panel 3 [4080, 0]",))

        await press(page.move, {"backglass": 3})

        self.assertEqual(self._written()["Backglass.BackglassDisplay"], "Panel 3 [4080, 0]")

    async def test_a_part_put_inside_the_playfield_is_embedded(self) -> None:
        page = _page(self.library)

        await press(page.move, {"backglass": picture.INSIDE})

        self.assertEqual(self._written()["Backglass.BackglassOutput"], "2")
        self.assertEqual(page.on["backglass"], picture.INSIDE)

    async def test_a_part_put_inside_takes_a_region_of_the_playfield_s_window(self) -> None:
        page = _page(self.library)

        await press(page.move, {"scoreview": picture.INSIDE})

        written = self._written()
        self.assertEqual(
            (written["ScoreView.ScoreViewWndX"], written["ScoreView.ScoreViewWndY"],
             written["ScoreView.ScoreViewWidth"], written["ScoreView.ScoreViewHeight"]),
            ("108", "3246", "1944", "486"))

    async def test_a_part_put_inside_a_playfield_with_no_screen_is_left_where_it_was(self) -> None:
        page = _page(self.library, flat=_flat(Player__PlayfieldDisplay=""))

        self.assertFalse(await press(page.move, {"scoreview": picture.INSIDE}))

        self.library.write_launcher_config.assert_not_called()
        self.assertNotEqual(page.on["scoreview"], picture.INSIDE)
        self.assertEqual(self.notify.call_args.args[0], t(
            "manager.vpx_displays.unsized", parts=t("manager.screens.scoreview")))

    async def test_a_part_cleared_is_disabled(self) -> None:
        page = _page(self.library)

        await press(page.move, {"backglass": None})

        self.assertEqual(self._written()["Backglass.BackglassOutput"], "0")

    async def test_a_screen_visual_pinball_has_no_name_for_is_not_written(self) -> None:
        page = _page(self.library)

        self.assertFalse(await press(page.move, {"backglass": 5}))

        self.library.write_launcher_config.assert_not_called()
        self.assertEqual(page.on["backglass"], 2)
        self.notify.assert_called_once()
        self.assertEqual(self.notify.call_args.args[0],
                         t("manager.vpx_displays.unnamed", parts=t("manager.screens.backglass")))

    async def test_the_playfield_moved_to_a_tall_screen_keeps_the_turn_it_had(self) -> None:
        page = _page(self.library, flat=_flat(TableOverride__ViewCabRotation="90"))

        await press(page.move, {"playfield": 4})

        self.assertEqual(self._written()["Player.PlayfieldDisplay"], LG)
        self.assertEqual(self._written()["TableOverride.ViewCabRotation"], "90.0")

    async def test_the_playfield_on_a_desk_is_turned_for_its_screen_too(self) -> None:
        page = _page(self.library, flat=_flat(TableOverride__ViewCabRotation="90"),
                     cabinet=False)

        await press(page.move, {"playfield": 4})

        self.assertEqual(self._written()["TableOverride.ViewCabRotation"], "90.0")

    async def test_the_playfield_moved_to_a_wide_screen_is_turned_for_its_shape(self) -> None:
        page = _page(self.library)

        await press(page.move, {"playfield": 2})

        self.assertEqual(self._written()["TableOverride.ViewCabRotation"], "90.0")

    async def test_a_write_that_fails_says_so_and_holds_nothing(self) -> None:
        cast(Any, self.run).side_effect = RuntimeError("read-only")
        page = _page(self.library)

        self.assertFalse(await press(page.move, {"backglass": 3}))

        self.assertEqual(page.on["backglass"], 2)
        page.rebuild.assert_not_called()
        self.assertEqual(self.notify.call_args.args[0], t("said.save_failed"))
        self.assertIn("read-only", self.notify.call_args.kwargs["caption"])

    async def test_render_priorities_are_written_once_a_score_screen_is_placed(self) -> None:
        page = _page(self.library)

        await press(page.move, {"scoreview": 3})

        self.assertEqual(self._written()["ScoreView.Priority.PUP"], "3")
        self.assertEqual(self._written()["Plugin.B2SLegacy.B2SHideGrill"], "1")

    async def test_a_priority_visual_pinball_already_holds_is_left_alone(self) -> None:
        page = _page(self.library, flat=_flat(ScoreView__Priority__PUP="9"))

        await press(page.move, {"scoreview": 3})

        self.assertNotIn("ScoreView.Priority.PUP", self._written())


    async def test_a_program_with_nixie_ranks_it_and_names_no_legacy_key(self) -> None:
        program = {"plugins": ["B2S", "Nixie", "PUP"], "build": 6300,
                   "defaults": {"Plugin.B2S.Enable": "1", "Plugin.B2SLegacy.Enable": "0"}}
        page = _page(self.library)
        page.program = program

        await press(page.move, {"scoreview": 3})

        written = self._written()
        self.assertEqual(written["ScoreView.Priority.Nixie"], "2")
        self.assertNotIn("Plugin.B2SLegacy.B2SHideGrill", written)
        self.assertNotIn("ScoreView.Priority.B2SLegacyDMD", written)


class MatchingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.library = _Library()
        self.enterContext(patch.object(vpx_displays.run, "io_bound",
                                       new=AsyncMock(side_effect=_inline)))
        self.notify = self.enterContext(patch.object(vpx_displays.ui, "notify"))

    def _written(self) -> dict[str, str]:
        return self.library.write_launcher_config.call_args.args[1]

    async def test_visual_pinball_is_put_where_vpinfe_has_its_parts(self) -> None:
        page = _page(self.library, flat=_flat(Backglass__BackglassDisplay=CVT))

        await press(page.match)

        self.assertEqual(self._written()["Backglass.BackglassDisplay"], DELL)
        self.assertEqual(page.on["backglass"], 2)
        page.rebuild.assert_called_once_with()

    async def test_a_part_vpinfe_does_not_draw_is_disabled(self) -> None:
        page = _page(self.library, flat=_flat(ScoreView__ScoreViewOutput="1",
                                              ScoreView__ScoreViewDisplay=CVT))

        await press(page.match)

        self.assertEqual(self._written()["ScoreView.ScoreViewOutput"], "0")

    async def test_the_topper_stays_unless_a_part_now_needs_its_screen(self) -> None:
        page = _page(self.library, flat=_flat(Topper__TopperOutput="1",
                                              Topper__TopperDisplay=CVT,
                                              Backglass__BackglassDisplay=CVT))
        self.assertEqual(page.on["topper"], 3)

        await press(page.match)

        self.assertNotIn("Topper.TopperOutput", self._written())
        self.assertEqual(page.on["topper"], 3)

    async def test_vpinfe_s_topper_screen_is_matched_floating(self) -> None:
        page = _page(self.library, vpinfe=_standing({"topper": 4}))

        await press(page.match)

        written = self._written()
        self.assertEqual(written["Topper.TopperDisplay"], LG)
        self.assertEqual(written["Topper.TopperOutput"], "1")
        self.assertEqual(page.on["topper"], 4)

    async def test_a_topper_on_a_screen_vpinfe_uses_is_cleared(self) -> None:
        page = _page(self.library, flat=_flat(Topper__TopperOutput="1",
                                              Topper__TopperDisplay=DELL,
                                              Backglass__BackglassDisplay=CVT))

        await press(page.match)

        self.assertEqual(self._written()["Topper.TopperOutput"], "0")
        self.assertIsNone(page.on["topper"])

    async def test_the_turn_is_matched_too_on_a_cabinet(self) -> None:
        page = _page(self.library, flat=_flat(TableOverride__ViewCabRotation="90"))

        await press(page.match)

        self.assertEqual(self._written()["TableOverride.ViewCabRotation"], "0.0")

    async def test_the_turn_is_matched_on_a_desk_too(self) -> None:
        page = _page(self.library, flat=_flat(TableOverride__ViewCabRotation="90"),
                     cabinet=False)

        await press(page.match)

        self.assertEqual(self._written()["TableOverride.ViewCabRotation"], "0.0")

    async def test_a_launcher_that_already_matches_writes_only_the_turn(self) -> None:
        page = _page(self.library)

        await press(page.match)

        self.assertEqual(self._written(), {"TableOverride.ViewCabRotation": "0.0"})

    async def test_a_failed_write_changes_nothing_on_the_page(self) -> None:
        self.library.write_launcher_config.side_effect = RuntimeError("read-only")
        page = _page(self.library, flat=_flat(Backglass__BackglassDisplay=CVT))

        await press(page.match)

        self.assertEqual(page.on["backglass"], 3)
        page.rebuild.assert_not_called()


class DrawnTests(unittest.TestCase):
    def test_the_picture_is_visual_pinball_s_parts_over_its_own_ini(self) -> None:
        page = _page(flat=_flat(Backglass__BackglassDisplay=CVT))
        with patch.object(vpx_displays.picture, "Picture") as control:
            [heading, (kind, draw)] = page.picture_rows()
            draw()

        self.assertEqual(heading, (panel.HEADING, t("manager.settings.section_screens")))
        self.assertIs(kind, panel.FULL)
        screens_, held = control.call_args.args
        self.assertEqual(screens_, HERE)
        self.assertEqual(held["backglass"], 3)
        kwargs = control.call_args.kwargs
        self.assertEqual([part.key for part in kwargs["parts"]],
                         ["topper", "backglass", "scoreview", "playfield"])
        self.assertEqual(kwargs["differs"], ["backglass"])
        self.assertEqual(kwargs["rotation"], "0")
        self.assertEqual(kwargs["match"], page.match)

    def test_a_turn_that_is_not_vpinfe_s_is_drawn_and_said_to_differ(self) -> None:
        page = _page(flat=_flat(TableOverride__ViewCabRotation="90"))
        with patch.object(vpx_displays.picture, "Picture") as control:
            page.picture_rows()[1][1]()

        self.assertEqual(control.call_args.kwargs["differs"], ["playfield"])
        self.assertEqual(control.call_args.kwargs["rotation"], "90")

    def test_the_page_draws_the_card_and_never_asks_the_question(self) -> None:
        page = _page()
        with patch.object(vpx_displays.picture, "Picture") as control:
            page.picture_rows()[1][1]()

        self.assertTrue(control.call_args.kwargs["cabinet"])
        self.assertNotIn("point", control.call_args.kwargs)
        self.assertNotIn("test", control.call_args.kwargs)

    def test_a_desk_is_not_a_cabinet(self) -> None:
        page = _page(cabinet=False)
        with patch.object(vpx_displays.picture, "Picture") as control:
            page.picture_rows()[1][1]()

        self.assertFalse(control.call_args.kwargs["cabinet"])


class SizeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.library = _Library()
        self.enterContext(patch.object(vpx_displays.run, "io_bound",
                                       new=AsyncMock(side_effect=_inline)))
        self.notify = self.enterContext(patch.object(vpx_displays.ui, "notify"))

    def test_a_size_is_read_from_the_ini_else_from_visual_pinball_s_default(self) -> None:
        page = _page(flat=_flat(Player__ScreenWidth="100"))

        self.assertEqual(page.size, {"Player.ScreenWidth": 100.0, "Player.ScreenHeight": 53.94})

    def test_a_size_that_is_neither_is_nothing(self) -> None:
        page = vpx_displays.Page(_Library(), "vpx-1", Mock(), flat={"Player.ScreenWidth": "x"},
                                 vpinfe=_standing(), here=HERE, logged=(), cabinet=True,
                                 units_now="cm", defaults={})

        self.assertEqual(page.size, {"Player.ScreenWidth": 0.0, "Player.ScreenHeight": 0.0})

    async def test_a_size_typed_in_inches_is_written_in_centimeters(self) -> None:
        page = _page(self.library, units_now="in")

        await press(page._sized, "Player.ScreenWidth", SimpleNamespace(value=40.0))

        self.library.write_launcher_config.assert_called_once_with(
            "vpx-1", {"Player.ScreenWidth": "101.6"})
        self.assertEqual(page.size["Player.ScreenWidth"], 101.6)

    async def test_a_size_typed_in_centimeters_is_written_as_typed(self) -> None:
        page = _page(self.library)

        await press(page._sized, "Player.ScreenHeight", SimpleNamespace(value=60))

        self.library.write_launcher_config.assert_called_once_with(
            "vpx-1", {"Player.ScreenHeight": "60"})

    async def test_a_size_left_as_it_was_writes_nothing(self) -> None:
        page = _page(self.library)

        await press(page._sized, "Player.ScreenWidth", SimpleNamespace(value=95.9))
        await press(page._sized, "Player.ScreenWidth", SimpleNamespace(value=None))

        self.library.write_launcher_config.assert_not_called()

    async def test_a_size_that_was_not_kept_is_drawn_again_as_it_was(self) -> None:
        self.library.write_launcher_config.side_effect = RuntimeError("read-only")
        page = _page(self.library)
        page.refill = Mock()

        await press(page._sized, "Player.ScreenWidth", SimpleNamespace(value=60))

        self.assertEqual(page.size["Player.ScreenWidth"], 95.89)
        page.refill.assert_called_once_with()

    async def test_the_units_chosen_are_kept_for_every_page(self) -> None:
        page = _page(self.library)
        page.refill = Mock()

        await press(page._chose_units, SimpleNamespace(value="in"))

        self.library.put_config.assert_called_once_with({"manager": {"units": "in"}})
        self.assertEqual(page.units, "in")
        page.refill.assert_called_once_with()

    async def test_units_that_were_not_kept_stay_as_they_were(self) -> None:
        self.library.put_config.side_effect = RuntimeError("offline")
        page = _page(self.library)
        page.refill = Mock()

        await press(page._chose_units, SimpleNamespace(value="in"))

        self.assertEqual(page.units, "cm")
        self.assertEqual(self.notify.call_args.args[0], t("manager.settings.save_failed"))


class ConflictsTests(unittest.TestCase):
    SET = {"Player.PlayfieldDisplay": SAMSUNG, "Backglass.BackglassOutput": "1",
           "Backglass.BackglassDisplay": DELL}

    def _conflicts(self, **over: str) -> list[str]:
        held = {**self.SET, **{key.replace("__", "."): value for key, value in over.items()}}
        return vpx_displays.conflicts(_standing(), HERE, held)

    def test_a_file_that_puts_every_part_where_vpinfe_does_conflicts_in_nothing(self) -> None:
        self.assertEqual(self._conflicts(), [])

    def test_a_part_the_file_puts_on_another_screen_conflicts(self) -> None:
        self.assertEqual(self._conflicts(Backglass__BackglassDisplay=CVT), ["backglass"])

    def test_a_part_the_file_has_turned_off_conflicts_with_one_vpinfe_draws(self) -> None:
        self.assertEqual(self._conflicts(Backglass__BackglassOutput="0"), ["backglass"])

    def test_a_part_the_file_holds_nothing_for_is_not_a_conflict(self) -> None:
        for blank in ("", " "):
            with self.subTest(blank=blank):
                self.assertEqual(self._conflicts(Backglass__BackglassOutput=blank,
                                                 Backglass__BackglassDisplay=blank), [])
        self.assertEqual(self._conflicts(Backglass__BackglassOutput=""), ["backglass"])
        held = {"Player.PlayfieldDisplay": SAMSUNG}
        self.assertEqual(vpx_displays.conflicts(_standing(), HERE, held), [])

    def test_a_turn_the_file_holds_that_is_not_vpinfe_s_conflicts(self) -> None:
        self.assertEqual(self._conflicts(TableOverride__ViewCabRotation="90"), ["playfield"])

    def test_no_turn_in_the_file_is_not_a_conflict(self) -> None:
        self.assertEqual(self._conflicts(TableOverride__ViewCabRotation=""), [])
        self.assertEqual(vpx_displays.conflicts(_standing(), HERE, self.SET), [])


class FollowingTests(unittest.TestCase):
    def _keys(self, before: Any, after: Any, flat: dict | None = None,
              logged: tuple = ()) -> dict[str, str]:
        return vpx_displays.following(before, after, HERE, logged,
                                      _flat() if flat is None else flat)

    def test_a_part_vpinfe_moved_moves_a_launcher_that_had_it_there(self) -> None:
        keys = self._keys(_standing(), _standing({"backglass": 3}))

        self.assertEqual(keys["Backglass.BackglassDisplay"], CVT)

    def test_the_keys_that_follow_are_those_of_the_program_it_is_given(self) -> None:
        program = {"plugins": ["B2S", "Nixie"], "build": 6300, "defaults": {}}
        before, after = _standing(), _standing({"scoreview": 3})

        plain = vpx_displays.following(before, after, HERE, (), _flat())
        nixie = vpx_displays.following(before, after, HERE, (), _flat(), program)

        self.assertIn("Plugin.B2SLegacy.B2SHideGrill", plain)
        self.assertNotIn("Plugin.B2SLegacy.B2SHideGrill", nixie)

    def test_a_part_the_launcher_holds_elsewhere_stays_where_it_was_put(self) -> None:
        keys = self._keys(_standing(), _standing({"backglass": 3}),
                          _flat(Backglass__BackglassDisplay=LG))

        self.assertNotIn("Backglass.BackglassDisplay", keys)

    def test_a_topper_vpinfe_moved_moves_a_launcher_that_had_it_there(self) -> None:
        keys = self._keys(_standing({"topper": 3}), _standing({"topper": 4}),
                          _flat(Topper__TopperOutput="1", Topper__TopperDisplay=CVT))

        self.assertEqual(keys["Topper.TopperDisplay"], LG)
        self.assertEqual(keys["Topper.TopperOutput"], "1")

    def test_a_topper_vpinfe_stops_having_is_left_alone(self) -> None:
        keys = self._keys(_standing({"topper": 3}), _standing({"topper": None}),
                          _flat(Topper__TopperOutput="1", Topper__TopperDisplay=CVT))

        self.assertFalse([key for key in keys if key.startswith("Topper")])

    def test_a_topper_vpinfe_stops_having_is_cleared_when_a_part_takes_its_screen(self) -> None:
        keys = self._keys(_standing({"topper": 3}),
                          _standing({"topper": None, "backglass": 3}),
                          _flat(Topper__TopperOutput="1", Topper__TopperDisplay=CVT))

        self.assertEqual(keys["Topper.TopperOutput"], "0")

    def test_nothing_changed_is_nothing_to_write(self) -> None:
        self.assertEqual(self._keys(_standing(), _standing()), {})

    def test_the_turn_goes_along_where_the_launcher_still_held_the_one_it_was_given(
            self) -> None:
        keys = self._keys(_standing(), _standing({"playfield": 4}),
                          _flat(TableOverride__ViewCabRotation="0.0"))

        self.assertEqual(keys["Player.PlayfieldDisplay"], LG)
        self.assertEqual(keys["TableOverride.ViewCabRotation"], "0.0")

    def test_a_turn_someone_set_in_visual_pinball_is_left(self) -> None:
        keys = self._keys(_standing(), _standing({"playfield": 4}),
                          _flat(TableOverride__ViewCabRotation="90"))

        self.assertEqual(keys["Player.PlayfieldDisplay"], LG)
        self.assertNotIn("TableOverride.ViewCabRotation", keys)

    def test_a_turn_on_a_launcher_whose_playfield_stayed_is_left(self) -> None:
        keys = self._keys(_standing(), _standing(turn=("landscape", "0")),
                          _flat(Player__PlayfieldDisplay=LG))

        self.assertEqual(keys, {})

    def test_a_screen_with_no_name_is_left_out(self) -> None:
        keys = self._keys(_standing(), _standing({"backglass": 5}))

        self.assertNotIn("Backglass.BackglassDisplay", keys)

    def test_a_part_vpinfe_cleared_is_disabled_where_it_was_the_same(self) -> None:
        keys = self._keys(_standing(), _standing({"backglass": None}))

        self.assertEqual(keys["Backglass.BackglassOutput"], "0")

    def test_a_part_inside_the_playfield_goes_with_it_to_the_new_screen(self) -> None:
        keys = self._keys(_standing(), _standing({"playfield": 4}),
                          _flat(ScoreView__ScoreViewOutput="2"))

        self.assertEqual(keys["ScoreView.ScoreViewOutput"], "2")
        self.assertEqual([keys[key] for key in vpx.REGION["scoreview"]],
                         [str(one) for one in vpx.embedded_region(TALL_TOO)])

    def test_a_part_the_launcher_holds_nothing_for_follows_the_move(self) -> None:
        for blank in ({"Backglass__BackglassOutput": ""}, {"Backglass__BackglassOutput": "  "}):
            with self.subTest(blank=blank):
                keys = self._keys(_standing(), _standing({"backglass": 3}), _flat(
                    Backglass__BackglassDisplay="", **blank))

                self.assertEqual(keys["Backglass.BackglassDisplay"], CVT)

    def test_a_part_set_to_disabled_stays_disabled_when_vpinfe_moves_it(self) -> None:
        keys = self._keys(_standing(), _standing({"backglass": 3}),
                          _flat(Backglass__BackglassOutput="0", Backglass__BackglassDisplay=""))

        self.assertNotIn("Backglass.BackglassDisplay", keys)

    def test_a_turn_the_launcher_holds_nothing_for_goes_along(self) -> None:
        keys = self._keys(_standing(), _standing({"playfield": 4}),
                          _flat(TableOverride__ViewCabRotation=""))

        self.assertEqual(keys["Player.PlayfieldDisplay"], LG)
        self.assertEqual(keys["TableOverride.ViewCabRotation"], "0.0")


class LaunchersTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.library = _Library()
        self.enterContext(patch.object(vpx_displays, "config_groups",
                                       side_effect=lambda found: found["groups"]))
        self.enterContext(patch.object(vpx_displays.run, "io_bound",
                                       new=AsyncMock(side_effect=_inline)))
        self.enterContext(patch.object(vpx_displays.offload, "io",
                                       new=AsyncMock(side_effect=_inline)))
        self.notify = self.enterContext(patch.object(vpx_displays.ui, "notify"))

    def _launchers(self, *found: dict) -> None:
        self.library.launchers.return_value = {"launchers": list(found)}

    @staticmethod
    def _one(launcher_id: str, **over: Any) -> dict:
        return {"launcher_id": launcher_id, "app": "vpx", "enabled": True, "has_config": True,
                **over}

    async def test_each_enabled_visual_pinball_launcher_with_a_file_follows(self) -> None:
        self._launchers(self._one("a"), self._one("b", app="fpx"),
                        self._one("c", enabled=False), self._one("d", has_config=False),
                        self._one("e"))

        await press(vpx_displays.follow, self.library, _standing(), _standing({"backglass": 3}),
                    HERE)

        self.assertEqual([call.args[0] for call in
                          self.library.write_launcher_config.call_args_list], ["a", "e"])
        self.assertEqual(self.library.write_launcher_config.call_args.args[1]
                         ["Backglass.BackglassDisplay"], CVT)

    async def test_a_program_that_has_never_run_gets_its_settings_file_made(self) -> None:
        await press(vpx_displays.follow, self.library, _standing(), _standing({"backglass": 3}),
                    HERE)

        self.assertIs(self.library.write_launcher_config.call_args.kwargs["create"], True)

    async def test_a_launcher_that_already_has_it_is_not_written(self) -> None:
        await press(vpx_displays.follow, self.library, _standing(), _standing(), HERE)

        self.library.write_launcher_config.assert_not_called()

    async def test_the_names_visual_pinball_logged_are_the_ones_written(self) -> None:
        self.library.launcher_config_groups.return_value = [SimpleNamespace(
            key="displays", settings=[
                SimpleNamespace(key="Player.PlayfieldDisplay", reported=[]),
                SimpleNamespace(key="Backglass.BackglassDisplay",
                                reported=["Panel 3 [4080, 0]"])])]

        await press(vpx_displays.follow, self.library, _standing(), _standing({"backglass": 3}),
                    HERE)

        self.assertEqual(self.library.write_launcher_config.call_args.args[1]
                         ["Backglass.BackglassDisplay"], "Panel 3 [4080, 0]")

    async def test_one_that_cannot_be_read_is_named_and_the_rest_still_follow(self) -> None:
        self._launchers(self._one("a"), self._one("b"))
        real = self.library.launcher_config_values.side_effect

        def values(launcher_id: str) -> dict:
            if launcher_id == "a":
                raise RuntimeError("no such file")
            return real(launcher_id)

        self.library.launcher_config_values.side_effect = values

        with self.assertLogs(vpx_displays.logger, "WARNING"):
            await press(vpx_displays.follow, self.library, _standing(),
                        _standing({"backglass": 3}), HERE)

        self.assertEqual([call.args[0] for call in
                          self.library.write_launcher_config.call_args_list], ["b"])
        self.assertEqual(self.notify.call_args.args[0],
                         t("manager.vpx_displays.follow_failed", count=1, name="a"))
        self.assertIn("no such file", self.notify.call_args.kwargs["caption"])

    async def test_launchers_that_cannot_be_listed_leave_vpinfe_s_own_change_alone(self) -> None:
        self.library.launchers.side_effect = RuntimeError("offline")

        with self.assertLogs(vpx_displays.logger, "WARNING"):
            await press(vpx_displays.follow, self.library, _standing(),
                        _standing({"backglass": 3}), HERE)

        self.library.write_launcher_config.assert_not_called()


class ApartTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.library = _Library()
        self.enterContext(patch.object(vpx_displays, "config_groups",
                                       side_effect=lambda found: found["groups"]))
        self.enterContext(patch.object(vpx_displays.offload, "io",
                                       new=AsyncMock(side_effect=_inline)))
        self.notify = self.enterContext(patch.object(vpx_displays.ui, "notify"))

    def _set(self, **keys: tuple[str, bool]) -> Mock:
        """A client whose launcher holds `keys`, each with whether the file sets it."""
        client = Mock()
        client.launcher_config.return_value = {
            "values": {key.replace("__", "."): {"value": value, "set_here": here}
                       for key, (value, here) in keys.items()},
            "program": None, "groups": []}
        return client

    async def test_only_what_the_file_sets_is_asked_about(self) -> None:
        client = self._set(Player__PlayfieldDisplay=(SAMSUNG, True),
                           Backglass__BackglassOutput=("1", False),
                           Backglass__BackglassDisplay=(CVT, False))

        got = await vpx_displays.disagreeing(client, "vpx-1", _standing(), HERE)

        self.assertEqual(got, [])

    async def test_a_part_the_file_sets_elsewhere_is_asked_about(self) -> None:
        client = self._set(Player__PlayfieldDisplay=(SAMSUNG, True),
                           Backglass__BackglassOutput=("1", True),
                           Backglass__BackglassDisplay=(CVT, True))

        got = await vpx_displays.disagreeing(client, "vpx-1", _standing(), HERE)

        self.assertEqual(got, ["backglass"])

    async def test_matching_a_launcher_writes_vpinfe_s_screens_into_its_file(self) -> None:
        client = self._set(Player__PlayfieldDisplay=(SAMSUNG, True),
                           Backglass__BackglassOutput=("1", True),
                           Backglass__BackglassDisplay=(CVT, True))

        written = await vpx_displays.match_launcher(client, "vpx-1", _standing(), HERE)

        self.assertEqual(client.write_launcher_config.call_args.args[:1], ("vpx-1",))
        self.assertEqual(client.write_launcher_config.call_args.args[1]
                         ["Backglass.BackglassDisplay"], DELL)
        self.assertIs(client.write_launcher_config.call_args.kwargs["create"], True)
        self.assertEqual(written.unnamed, ())

    async def test_a_screen_visual_pinball_has_no_name_for_is_left_and_said(self) -> None:
        client = self._set(Player__PlayfieldDisplay=(SAMSUNG, True),
                           Backglass__BackglassOutput=("1", True),
                           Backglass__BackglassDisplay=(CVT, True))

        written = await vpx_displays.match_launcher(
            client, "vpx-1", _standing({"backglass": 5}), HERE)

        self.assertEqual(written.unnamed, ("backglass",))
        self.assertNotIn("Backglass.BackglassDisplay",
                         client.write_launcher_config.call_args.args[1]
                         if client.write_launcher_config.called else {})

    async def test_a_launcher_that_agrees_has_no_row(self) -> None:
        self.assertEqual(await vpx_displays.apart(self.library, _standing(), HERE), [])

    async def test_a_launcher_that_differs_is_named_with_its_parts(self) -> None:
        self.library.flat = _flat(Backglass__BackglassDisplay=CVT)
        self.library.launchers.return_value = {"launchers": [
            {"launcher_id": "vpx-1", "app": "vpx", "enabled": True, "has_config": True,
             "display_name": "Visual Pinball X"}]}

        [one] = await vpx_displays.apart(self.library, _standing(), HERE)

        self.assertEqual((one.launcher_id, one.name, one.parts, one.can_match),
                         ("vpx-1", "Visual Pinball X", ("backglass",), True))

    async def test_a_part_with_no_screen_name_yet_waits_and_cannot_be_matched(self) -> None:
        self.library.flat = _flat(Backglass__BackglassDisplay=CVT)

        [one] = await vpx_displays.apart(self.library, _standing({"backglass": 5}), HERE)

        self.assertEqual(one.waiting, ("backglass",))
        self.assertFalse(one.can_match)

    async def test_there_is_no_row_where_the_launchers_agree_or_cannot_be_read(self) -> None:
        self.assertEqual(await vpx_displays.apart_rows(
            self.library, _standing(), HERE, Mock()), [])
        self.library.launchers.side_effect = RuntimeError("offline")

        with self.assertLogs(vpx_displays.logger, "WARNING"):
            self.assertEqual(await vpx_displays.apart_rows(
                self.library, _standing(), HERE, Mock()), [])

    async def test_the_row_is_drawn_under_the_screens_and_match_writes_and_redraws(self) -> None:
        self.library.flat = _flat(Backglass__BackglassDisplay=CVT)
        refill = Mock()
        [(kind, draw)] = await vpx_displays.apart_rows(
            self.library, _standing(), HERE, refill)

        with patch.object(vpx_displays.panel, "action") as action:
            await press(_drawn, draw)

        self.assertIs(kind, panel.FULL)
        self.assertEqual(action.call_args.args[0], t("manager.screens.match_vpinfe"))
        self.assertTrue(action.call_args.kwargs["enabled"])
        await press(action.call_args.args[1])
        self.assertEqual(self.library.write_launcher_config.call_args.args[1]
                         ["Backglass.BackglassDisplay"], DELL)
        refill.assert_called_once_with()


async def _drawn(draw: Any) -> None:
    draw()


class PanelKindTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.library = _Library()
        self.enterContext(patch.object(vpx_displays.run, "io_bound",
                                       new=AsyncMock(side_effect=_inline)))
        self.enterContext(patch.object(vpx_displays.offload, "io",
                                       new=AsyncMock(side_effect=_inline)))
        self.enterContext(patch.object(vpx_displays.ui, "notify"))

    async def test_each_launcher_is_told_the_kind_on_linux_and_macos(self) -> None:
        for platform in ("linux", "darwin"):
            with self.subTest(platform=platform), patch("apps.vpx.screens.sys",
                                                        platform=platform):
                self.library.write_launcher_config.reset_mock()

                await press(vpx_displays.follow_dmd, self.library, "ZeDMD")

                self.library.write_launcher_config.assert_called_once_with(
                    "vpx-1", {"Plugin.DMDUtil.Enable": "1", "Plugin.DMDUtil.ZeDMD": "1"},
                    create=True)

    async def test_windows_is_told_nothing(self) -> None:
        with patch("apps.vpx.screens.sys", platform="win32"):
            await press(vpx_displays.follow_dmd, self.library, "ZeDMD")

        self.library.write_launcher_config.assert_not_called()

    async def test_another_kind_already_on_is_not_written(self) -> None:
        self.library.flat = _flat(Plugin__DMDUtil__ZeDMD="1")

        with patch("apps.vpx.screens.sys", platform="linux"):
            await press(vpx_displays.follow_dmd, self.library, "PIN2DMD")

        self.assertEqual(self.library.write_launcher_config.call_args.args[1],
                         {"Plugin.DMDUtil.Enable": "1", "Plugin.DMDUtil.PIN2DMD": "1"})


class EntriesTests(unittest.IsolatedAsyncioTestCase):
    MONITORS = [Monitor(x=0, y=0, width=2160, height=3840, width_mm=336, height_mm=597,
                        name="SAMSUNG", is_primary=True),
                Monitor(x=2160, y=0, width=1920, height=1080, width_mm=527, height_mm=296,
                        name="DELL P2419H", is_primary=False)]

    def _group(self) -> Any:
        keys = ("Player.PlayfieldDisplay", "Backglass.BackglassOutput",
                "Backglass.BackglassDisplay", "Player.ScreenWidth", "Player.ScreenHeight")
        return SimpleNamespace(key="displays", label="Displays", settings=[
            SimpleNamespace(key=key, label=key, reported=[], default="95.89"
                            if key == "Player.ScreenWidth" else "53.94" if "Height" in key
                            else "") for key in keys])

    def _values(self) -> dict[str, dict[str, str]]:
        return {key: {"value": value} for key, value in _flat().items()}

    async def _entries(self, shown: list, rows: Any) -> tuple[list, dict]:
        context: dict[str, Any] = {"library": _Library(), "launcher": {"launcher_id": "vpx-1"},
                                   "rebuild": AsyncMock(), "config_values": {"stale": True}}
        beside: tuple[Any, ...] = ({}, SCHEMA, self.MONITORS, ["", ""])
        with patch.object(vpx_displays, "_beside", new=AsyncMock(return_value=beside)), \
                patch.object(vpx_displays.offload, "io", new=AsyncMock(side_effect=_inline)):
            return await vpx_displays.entries(context, self._group(), shown, self._values(),
                                              rows), context

    async def test_the_page_answers_for_the_program_the_launcher_reports(self) -> None:
        program = {"plugins": ["B2S", "Nixie"], "build": 6300, "defaults": {}}
        context: dict[str, Any] = {"library": _Library(), "launcher": {"launcher_id": "vpx-1"},
                                   "rebuild": AsyncMock()}
        context["library"].launcher_program = Mock(return_value=program)
        beside: tuple[Any, ...] = ({}, SCHEMA, self.MONITORS, ["", ""])
        with patch.object(vpx_displays, "_beside", new=AsyncMock(return_value=beside)), \
                patch.object(vpx_displays.offload, "io", new=AsyncMock(side_effect=_inline)), \
                patch.object(vpx_displays, "Page", wraps=vpx_displays.Page) as made:
            await vpx_displays.entries(context, self._group(), [], self._values(),
                                       AsyncMock(return_value=[]))

        self.assertEqual(made.call_args.kwargs["program"], program)
        context["library"].launcher_program.assert_called_once_with("vpx-1")

    async def test_a_program_that_cannot_be_read_leaves_the_page_without_one(self) -> None:
        context: dict[str, Any] = {"library": _Library(), "launcher": {"launcher_id": "vpx-1"},
                                   "rebuild": AsyncMock()}
        context["library"].launcher_program = Mock(side_effect=OSError("gone"))
        beside: tuple[Any, ...] = ({}, SCHEMA, self.MONITORS, ["", ""])
        with patch.object(vpx_displays, "_beside", new=AsyncMock(return_value=beside)), \
                patch.object(vpx_displays.offload, "io", new=AsyncMock(side_effect=_inline)), \
                patch.object(vpx_displays, "Page", wraps=vpx_displays.Page) as made:
            await vpx_displays.entries(context, self._group(), [], self._values(),
                                       AsyncMock(return_value=[]))

        self.assertIsNone(made.call_args.kwargs["program"])

    async def test_the_heading_comes_first_then_the_intro_the_picture_and_the_rows(
            self) -> None:
        rows = AsyncMock(return_value=[("a row", None)])
        shown = [_heading("playfield", "Player.PlayfieldDisplay"),
                 _heading("cabinet", "Player.ScreenWidth", "Player.ScreenHeight", "Player.BGSet")]

        out, _ = await self._entries(shown, rows)

        self.assertEqual([kind for kind, _ in out[:4]],
                         [panel.HEADING, panel.FULL, panel.FULL, panel.FULL])
        self.assertEqual(out[0][1], t("manager.settings.section_screens"))
        self.assertIn(("a row", None), out)

    async def test_the_rows_are_given_what_the_picture_does_not_draw(self) -> None:
        rows = AsyncMock(return_value=[])
        shown = [_heading("playfield", "Player.PlayfieldDisplay", "Player.PlayfieldFullScreen"),
                 _heading("cabinet", "Player.ScreenWidth", "Player.ScreenHeight", "Player.BGSet")]

        await self._entries(shown, rows)

        assert rows.await_args is not None
        [blocks] = rows.await_args.args
        self.assertEqual([[one.key for one in fields] for _, _, fields in blocks],
                         [["Player.PlayfieldFullScreen"], ["Player.BGSet"]])

    async def test_the_size_row_is_drawn_before_the_row_that_follows_it(self) -> None:
        rows = AsyncMock(return_value=[])
        shown = [_heading("cabinet", "Player.ScreenWidth", "Player.ScreenHeight", "Player.BGSet")]

        await self._entries(shown, rows)

        assert rows.await_args is not None
        self.assertEqual(list(rows.await_args.kwargs["ahead"]), ["Player.BGSet"])
        [(label, _)] = rows.await_args.kwargs["ahead"]["Player.BGSet"]
        self.assertEqual(label, t("manager.screens.screen_size"))

    async def test_with_no_row_after_it_the_size_row_goes_last(self) -> None:
        rows = AsyncMock(return_value=[("a row", None)])
        shown = [_heading("cabinet", "Player.ScreenWidth", "Player.ScreenHeight")]

        out, _ = await self._entries(shown, rows)

        assert rows.await_args is not None
        self.assertEqual(rows.await_args.kwargs["ahead"], {})
        self.assertEqual(out[-1][0], t("manager.screens.screen_size"))

    async def test_what_cannot_be_read_is_said_in_place_of_the_page(self) -> None:
        context: dict[str, Any] = {"library": _Library(), "launcher": {"launcher_id": "vpx-1"},
                                   "rebuild": AsyncMock()}
        rows = AsyncMock()
        with patch.object(vpx_displays, "_beside", new=AsyncMock(side_effect=RuntimeError("x"))):
            out = await vpx_displays.entries(context, self._group(), [], self._values(), rows)

        self.assertEqual(len(out), 2)
        self.assertEqual(out[0], (panel.HEADING, "Displays"))
        self.assertIs(out[1][0], panel.FULL)
        rows.assert_not_awaited()

    async def test_a_change_to_the_page_reads_the_ini_again(self) -> None:
        rows = AsyncMock(return_value=[])
        with patch.object(vpx_displays, "Page") as page:
            page.return_value.size_row.return_value = []
            page.return_value.picture_rows.return_value = [(panel.HEADING, "Screens"),
                                                           (panel.FULL, None)]
            page.return_value.on = {}
            _, context = await self._entries([], rows)
            rebuild = page.call_args.args[2]
            rebuild()
            await asyncio.sleep(0)

        self.assertNotIn("config_values", context)
        context["rebuild"].assert_awaited_once_with()


if __name__ == "__main__":
    unittest.main()
