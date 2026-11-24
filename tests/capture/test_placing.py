"""Which output each window is recorded from: the desktop once the table is up, then the
app's own settings, then VPinFE's screens."""

from __future__ import annotations

import configparser
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from apps.vpx.capture import VPXCapture
from common import apps
from common.apps.contract import VIEW_FSS
from common.capture import adapters, placing
from common.capture.adapters import Window, wlr
from common.host import launch


def _output(name: str, x: int, width: int, height: int, transform: str = "normal") -> dict:
    return {"name": name, "active": True, "transform": transform,
            "rect": {"x": x, "y": 0, "width": width, "height": height},
            "current_mode": {"width": 1920, "height": 1080, "refresh": 60000}}


# Three outputs under sway, the portrait one turned 270.
OUTPUTS = wlr.sway_outputs([_output("DVI-D-1", 3000, 1920, 1080),
                            _output("DP-1", 0, 1080, 1920, "270"),
                            _output("HDMI-A-1", 1080, 1920, 1080)])
# VPinFE's screen ids index its own monitor list, which is in its own order.
MONITORS = [SimpleNamespace(x=3000, y=0, width=1920, height=1080, name="DVI-D-1"),
            SimpleNamespace(x=0, y=0, width=1080, height=1920, name="DP-1"),
            SimpleNamespace(x=1080, y=0, width=1920, height=1080, name="HDMI-A-1")]


def _config(playfield: str = "2", backglass: str = "1", scoreview: str = "0"
            ) -> configparser.ConfigParser:
    held = configparser.ConfigParser()
    for window, value in (("playfield", playfield), ("backglass", backglass),
                          ("scoreview", scoreview)):
        held[f"windows.{window}"] = {"screen_id": value}
    return held


# What VPX's settings name for each window there.
FROM_VPX = placing.Shown("Visual Pinball X",
                         {"playfield": "DP-1", "backglass": "HDMI-A-1",
                          "scoreview": "DVI-D-1", "topper": ""},
                         VPXCapture().window)


def _placing(shown: placing.Shown | None = FROM_VPX, **config: str) -> placing.Placing:
    return placing.Placing(OUTPUTS, _config(**config), MONITORS, shown)


def _names(screens: dict[str, adapters.Screen]) -> dict[str, str]:
    return {window: screen.output.name if screen.output else screen.reason
            for window, screen in screens.items()}


class PrecedenceTests(unittest.TestCase):
    def test_vpinfes_screen_ids_answer_only_where_nothing_else_does(self) -> None:
        self.assertEqual(_names(_placing(None).screens()),
                         {"playfield": "HDMI-A-1", "backglass": "DP-1",
                          "scoreview": "DVI-D-1", "topper": adapters.NO_SCREEN})

    def test_the_apps_settings_answer_over_vpinfes_screen_ids(self) -> None:
        self.assertEqual(_names(_placing().screens()),
                         {"playfield": "DP-1", "backglass": "HDMI-A-1",
                          "scoreview": "DVI-D-1", "topper": placing.NOT_SHOWN})

    def test_a_window_the_app_shows_nowhere_says_so_with_the_apps_name(self) -> None:
        topper = _placing().screen("topper")

        self.assertEqual((topper.output, topper.reason, dict(topper.params)),
                         (None, placing.NOT_SHOWN, {"app": "Visual Pinball X"}))

    def test_the_desktop_answers_over_the_apps_settings(self) -> None:
        seen = {"playfield": "DP-1", "backglass": "DVI-D-1"}

        self.assertEqual(_names(_placing().screens(seen)),
                         {"playfield": "DP-1", "backglass": "DVI-D-1",
                          "scoreview": placing.NOT_SHOWN, "topper": placing.NOT_SHOWN})

    def test_an_output_the_desktop_does_not_have_passes_to_the_next(self) -> None:
        said = placing.Shown("Visual Pinball X", {"playfield": "DP-9", "backglass": "DP-9"})

        found = _placing(said).screens({"playfield": "DP-7", "backglass": "DP-9"})

        playfield, backglass = found["playfield"].output, found["backglass"].output
        assert playfield is not None and backglass is not None
        self.assertEqual((playfield.name, backglass.name),
                         ("HDMI-A-1", "DP-1"))

    def test_a_window_the_app_does_not_name_is_found_by_vpinfes_screen_id(self) -> None:
        said = placing.Shown("Visual Pinball X", {"playfield": "DP-1"})

        self.assertEqual(_names(_placing(said).screens())["backglass"], "DP-1")


class _Desktop:
    def __init__(self, windows: list[Window] | Exception) -> None:
        self._windows = windows

    def windows(self) -> list[Window]:
        if isinstance(self._windows, Exception):
            raise self._windows
        return self._windows


class SeenTests(unittest.TestCase):
    def test_the_apps_windows_are_found_by_title_on_their_outputs(self) -> None:
        desktop = _Desktop([Window("chromium", "VPinFE Table", "DP-1"),
                            Window("VPinballX_BGFX", "Visual Pinball Player", "DP-1"),
                            Window("VPinballX_BGFX", "Visual Pinball Backglass", "HDMI-A-1")])

        self.assertEqual(placing.seen(desktop, FROM_VPX),  # type: ignore[arg-type]
                         {"playfield": "DP-1", "backglass": "HDMI-A-1"})

    def test_a_desktop_that_shows_no_playfield_window_says_nothing(self) -> None:
        for windows in ([Window("VPinballX_BGFX", "Visual Pinball Backglass", "DP-1")], [],
                        OSError("refused")):
            with self.subTest(windows=windows):
                self.assertIsNone(placing.seen(_Desktop(windows), FROM_VPX))  # type: ignore[arg-type]


class ShownTests(unittest.TestCase):
    def test_the_app_that_would_play_the_table_is_asked(self) -> None:
        vpx = apps.get("vpx")
        entry = apps.Entry(table="/games/Example/Example.vpx")
        with patch.object(launch, "launched_by", return_value=(vpx, entry, {})) as found, \
                patch.object(VPXCapture, "outputs", return_value={"topper": ""}), \
                patch.object(VPXCapture, "placed_by", return_value="settings-1"):
            said = placing.shown("game", "Example.vpx")

        assert said is not None
        found.assert_called_once_with("game", "Example.vpx")
        self.assertEqual((said.app, dict(said.outputs)), ("Visual Pinball X", {"topper": ""}))
        self.assertEqual(said.window("", "Visual Pinball Player"), "playfield")
        self.assertEqual(said.placer, placing.Placer("Visual Pinball X",
                                                     "/games/Example/Example.vpx",
                                                     "settings-1"))

    def test_what_placed_a_tables_windows_is_asked_without_where_they_go(self) -> None:
        vpx = apps.get("vpx")
        entry = apps.Entry(table="/games/Example/Example.vpx")
        with patch.object(launch, "launched_by", return_value=(vpx, entry, {})), \
                patch.object(VPXCapture, "outputs") as outputs, \
                patch.object(VPXCapture, "placed_by", return_value="settings-1"):
            said = placing.placer("game", "Example.vpx")

        outputs.assert_not_called()
        self.assertEqual(said, ("Visual Pinball X", "/games/Example/Example.vpx", "settings-1"))

    def test_nothing_is_said_without_a_launcher_or_a_capture_hook(self) -> None:
        answer: Any
        for answer in (None, (apps.get("generic"), apps.Entry(), {}),
                       launch.LaunchUnavailableError("no table")):
            with self.subTest(answer=answer), patch.object(
                    launch, "launched_by",
                    **({"side_effect": answer} if isinstance(answer, Exception)
                       else {"return_value": answer})):
                self.assertIsNone(placing.shown())
                self.assertIsNone(placing.placer("game"))

    def test_the_views_the_app_can_start_the_table_in_are_asked(self) -> None:
        entry = apps.Entry(table="/games/Example/Example.vpx")
        with patch.object(launch, "launched_by", return_value=(apps.get("vpx"), entry, {})), \
                patch.object(VPXCapture, "views", return_value=frozenset({VIEW_FSS})):
            self.assertEqual(placing.views("game", "Example.vpx"), frozenset({VIEW_FSS}))

    def test_no_views_are_offered_without_a_launcher_or_a_capture_hook(self) -> None:
        answer: Any
        for answer in (None, (apps.get("generic"), apps.Entry(), {}),
                       launch.LaunchUnavailableError("no table")):
            with self.subTest(answer=answer), patch.object(
                    launch, "launched_by",
                    **({"side_effect": answer} if isinstance(answer, Exception)
                       else {"return_value": answer})):
                self.assertEqual(placing.views("game"), frozenset())

    def test_the_default_launchers_app_with_no_table_has_no_placer(self) -> None:
        with patch.object(launch, "launched_by",
                          return_value=(apps.get("vpx"), apps.Entry(), {})), \
                patch.object(VPXCapture, "outputs", return_value={}), \
                patch.object(VPXCapture, "placed_by") as asked:
            said = placing.shown()

        assert said is not None
        self.assertIsNone(said.placer)
        asked.assert_not_called()


if __name__ == "__main__":
    unittest.main()
