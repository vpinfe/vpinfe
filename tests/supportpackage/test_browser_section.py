"""Frontend Browser, and the windows as they opened in Displays And Layout."""

from __future__ import annotations

import configparser
import unittest
from typing import Any
from unittest.mock import patch

from common.host import frontend_browser as fb
from common.host import frontend_state
from common.support import collect
from common.support.collect import browser
from common.support.collect.displays import section as displays_section
from common.support.sections import OK

FEATURES: dict[str, Any] = {
    "webgl": True, "webgl2": True, "webgpu": False, "offscreen_canvas": True,
    "renderer": "ANGLE (Apple, ANGLE Metal Renderer: Apple M2, Unspecified Version)",
    "decodes": {"h264": {"supported": True, "smooth": True, "power_efficient": True},
                "vp9": {"supported": False, "smooth": False, "power_efficient": False}},
    "gamepads": 1, "took_ms": 212, "screen": None,
}


def report(state: str = fb.PLAYS, features: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"state": state, "browser": "Chromium 145.0.7632.0", "bundled": True,
            "plays": ["H.264", "MP3"], "does_not_play": ["HEVC"], "features": features}


def config(**settings: str) -> configparser.ConfigParser:
    parsed = configparser.ConfigParser()
    parsed.add_section("Settings")
    for key, value in settings.items():
        parsed.set("Settings", key, value)
    return parsed


class Collecting(unittest.TestCase):
    def lines(self, held: dict[str, Any], **settings: str) -> list[str]:
        with patch.object(fb, "current", return_value=held), \
                patch.object(browser, "get_ini_config", return_value=config(**settings)):
            found = browser.collect()
        self.assertEqual(found.status, OK)
        return list(found.lines)

    def test_it_sits_between_displays_and_settings(self) -> None:
        order = [one.id for one in collect.SECTIONS]
        self.assertLess(order.index("displays"), order.index("browser"))
        self.assertLess(order.index("browser"), order.index("settings"))

    def test_a_device_with_no_browser_says_so(self) -> None:
        self.assertEqual(self.lines(report(fb.NO_BROWSER)), ["No browser on this device."])

    def test_what_was_probed_reads_as_available_or_not(self) -> None:
        text = "\n".join(self.lines(report(features=FEATURES)))
        self.assertIn("- **WebGL 2:** Available", text)
        self.assertIn("- **WebGPU:** Not available", text)
        self.assertIn("- **H.264 at 1080p, 60 frames a second:** Available, smooth, hardware",
                      text)
        self.assertIn("- **VP9 at 1080p, 60 frames a second:** Not available", text)
        self.assertIn("- **Drawn on the processor:** No", text)
        self.assertIn("- **Gamepads seen by the browser:** 1", text)
        self.assertIn("- **Probe time:** 212 ms", text)
        self.assertIn("Chromium 145.0.7632.0 (bundled)", text)

    def test_a_software_renderer_is_called_out(self) -> None:
        held = {**FEATURES, "renderer": "llvmpipe (LLVM 15, 256 bits)"}
        self.assertIn("- **Drawn on the processor:** Yes",
                      self.lines(report(features=held)))

    def test_nothing_reported_is_not_tested_and_not_not_available(self) -> None:
        text = "\n".join(self.lines(report(fb.UNKNOWN)))
        self.assertIn("- **Plays:** Not tested", text)
        self.assertIn("- **Graphics and decoding:** Not tested", text)
        self.assertNotIn("Not available", text)

    def test_an_answer_that_did_not_come_is_not_tested(self) -> None:
        held = {**FEATURES, "webgpu": None,
                "decodes": {"h264": None, "vp9": {"supported": None}}}
        text = "\n".join(self.lines(report(features=held)))
        self.assertIn("- **WebGPU:** Not tested", text)
        self.assertIn("- **H.264 at 1080p, 60 frames a second:** Not tested", text)
        self.assertIn("- **VP9 at 1080p, 60 frames a second:** Not tested", text)

    def test_the_options_added_are_listed_and_a_value_is_shown_only_for_a_known_one(self) -> None:
        text = "\n".join(self.lines(
            report(), chromeoptions='--use-gl=angle --user-data-dir="/home/player/profile" '
                                    '--disable-gpu-vsync'))
        self.assertIn("- **Options added:** --use-gl=angle, --user-data-dir, "
                      "--disable-gpu-vsync", text)
        self.assertNotIn("/home/player", text)

    def test_a_value_after_a_space_is_not_printed_as_an_option(self) -> None:
        text = "\n".join(self.lines(
            report(), chromeoptions="--user-data-dir /home/player/profile "
                                    "--proxy-server http://name:pass@host:3128 --disable-gpu",
            chromeoptionsexclude="--lang de"))
        self.assertIn("- **Options added:** --user-data-dir, --proxy-server, --disable-gpu", text)
        self.assertIn("- **Own options turned off:** --lang", text)
        for leaked in ("/home/player", "pass@", "3128", "lang de"):
            self.assertNotIn(leaked, text)

    def test_no_added_options_says_none(self) -> None:
        self.assertIn("- **Options added:** None", self.lines(report()))

    def test_the_defaults_dropped_are_named_without_their_values(self) -> None:
        text = "\n".join(self.lines(report(), chromeoptionsexclude="--kiosk --lang=de"))
        self.assertIn("- **Own options turned off:** --kiosk, --lang", text)

    def test_an_unbalanced_quote_is_no_options_not_an_error(self) -> None:
        self.assertIn("- **Options added:** None", self.lines(report(), chromeoptions='--a="b'))


class Opened(unittest.TestCase):
    HELD = {"backglass": {"x": 3840, "y": 0, "width": 1920, "height": 1080, "scale": 1.0,
                          "reported_at": "2026-10-25T08:12:44Z"},
            "scoreview": {"x": None, "y": None, "width": None, "height": None, "scale": None,
                          "reported_at": ""}}

    def lines(self, held: dict[str, Any], running: bool, wayland: bool = False) -> list[str]:
        state = frontend_state.FrontendState(running=running)
        with patch.object(displays_section.frontend_windows, "read", return_value=held), \
                patch.object(displays_section.frontend_state, "current", return_value=state):
            return displays_section._opened_block(wayland)

    def test_a_window_reads_where_it_opened_and_when_it_was_last_seen(self) -> None:
        self.assertIn("- **Backglass:** 1920x1080 · at 3840,0 · scale 1. Last seen "
                      "2026-10-25 08:12 UTC", self.lines(self.HELD, running=True))

    def test_a_native_wayland_window_prints_no_position(self) -> None:
        self.assertIn("- **Backglass:** 1920x1080 · scale 1. Last seen 2026-10-25 08:12 UTC",
                      self.lines(self.HELD, running=True, wayland=True))

    def test_a_window_that_measured_nothing_says_so(self) -> None:
        self.assertIn("- **Scoreview:** Nothing measured", self.lines(self.HELD, running=True))

    def test_the_frontend_not_running_is_said_beside_every_window(self) -> None:
        lines = self.lines(self.HELD, running=False)
        self.assertTrue(all(line.endswith(". Not running") for line in lines[3:]))

    def test_no_reports_is_no_block(self) -> None:
        self.assertEqual(self.lines({}, running=True), [])


if __name__ == "__main__":
    unittest.main()
