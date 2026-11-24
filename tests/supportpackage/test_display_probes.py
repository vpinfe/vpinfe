"""Each display probe against what its desktop prints, and the contract they all keep: a
program that is missing, slow, failing or unreadable is a reading, never an exception."""

from __future__ import annotations

import importlib.util
import json
import re
import unittest
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from unittest.mock import patch

from common.capture import geometry
from common.capture.adapters import Window
from common.support.collect.displays import model
from common.support.collect.displays.probes import (
    Probe,
    distro,
    gnome,
    kde,
    macos,
    wayland_generic,
    windows,
    wlr,
    x11,
)
from common.support.collect.displays.run import Ran, Run

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "displays"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def runner(answers: Mapping[str, Ran]) -> Callable[[Sequence[str]], Ran]:
    """A `Run` that answers by program name, and says not installed for any other."""
    def run(argv: Sequence[str]) -> Ran:
        return answers.get(" ".join(argv)) or answers.get(argv[0]) or Ran(model.NOT_HERE)
    return run


def ok(text: str) -> Ran:
    return Ran(model.OK, text)


def by_connector(screens: Sequence[model.ScreenFact]) -> dict[str | None, model.ScreenFact]:
    return {one.connector: one for one in screens}


def _mutter_without_a_bus(env: Mapping[str, str], run: Run) -> model.Reading:
    def no_bus(_: Mapping[str, str]) -> object:
        raise OSError

    return gnome.probe(env, run, call=no_bus)


class Xrandr(unittest.TestCase):
    def test_one_screen_is_read_with_its_primary_and_refresh(self) -> None:
        screens, off = x11.parse_outputs(fixture("xrandr/one.txt")) or ([], -1)
        self.assertEqual(off, 1)
        (one,) = screens
        self.assertEqual((one.connector, one.x, one.y, one.width, one.height),
                         ("HDMI-1", 0, 0, 1920, 1080))
        self.assertEqual((one.primary, one.refresh_hz, one.said), (True, 60.0, "normal"))
        self.assertFalse(one.is_turned)

    def test_a_screen_turned_left_is_a_quarter_turn_and_keeps_the_word(self) -> None:
        screens, _ = x11.parse_outputs(fixture("xrandr/two_portrait_left.txt")) or ([], 0)
        second = by_connector(screens)["DP-1"]
        self.assertEqual((second.width, second.height, second.x), (1080, 1920, 1920))
        self.assertEqual((second.turn, second.said), (geometry.Turn(ccw=90), "left"))
        self.assertEqual(second.shape, "portrait")

    def test_a_reflected_axis_is_a_mirror_and_an_output_with_no_mode_is_off(self) -> None:
        parsed = x11.parse_outputs(fixture("xrandr/inverted_reflected_off.txt"))
        screens, off = parsed or ([], 0)
        found = by_connector(screens)
        self.assertEqual(found["HDMI-1"].turn, geometry.Turn(True, 180))
        self.assertEqual(found["HDMI-1"].said, "inverted X axis")
        self.assertEqual((found["DP-1"].enabled, found["DP-1"].width), (False, None))
        self.assertEqual(off, 1)

    def test_text_that_is_not_xrandrs_is_none(self) -> None:
        self.assertIsNone(x11.parse_outputs(fixture("xrandr/garbage.txt")))

    def test_the_probe_reads_the_window_manager_and_never_asks_for_the_edid(self) -> None:
        asked: list[str] = []
        answers = {
            "xrandr --query": ok(fixture("xrandr/two_portrait_left.txt")),
            "xrandr --listmonitors": ok("Monitors: 2\n 0: +*HDMI-1 1920/527x1080/296+0+0\n"),
            "xprop -root _NET_SUPPORTING_WM_CHECK _NET_WM_CM_S0": ok(
                "_NET_SUPPORTING_WM_CHECK(WINDOW): window id # 0x400003\n"
                "_NET_WM_CM_S0:  not found.\n"),
            "xprop -id 0x400003 _NET_WM_NAME": ok('_NET_WM_NAME(UTF8_STRING) = "Openbox"\n'),
        }
        base = runner(answers)

        def run(argv: Sequence[str]) -> Ran:
            asked.append(" ".join(argv))
            return base(argv)

        reading = x11.probe({}, run)
        self.assertTrue(reading.ok)
        self.assertEqual(len(reading.screens), 2)
        self.assertIn(("Window manager", "Openbox"), reading.facts)
        self.assertIn(("Compositing", "Not running"), reading.facts)
        self.assertIn(("Logical monitors", "2"), reading.facts)
        self.assertFalse([one for one in asked if "--props" in one or "--verbose" in one])

    def test_xrandr_that_prints_text_it_cannot_read_keeps_the_first_lines_to_show(self) -> None:
        reading = x11.probe({}, runner({"xrandr": ok("line\n" * 50)}))
        self.assertEqual(reading.state, model.FAILED)
        self.assertEqual(len(reading.unread), model.UNREAD_LINES)


class KScreen(unittest.TestCase):
    def test_json_gives_each_connected_output_with_its_rotation_word(self) -> None:
        screens = kde.parse_json(fixture("kscreen/plasma6_two_rotated.json")) or []
        found = by_connector(screens)
        self.assertEqual(sorted(found, key=str), ["DP-2", "HDMI-A-1"])
        self.assertEqual((found["HDMI-A-1"].primary, found["HDMI-A-1"].refresh_hz), (True, 60.0))
        self.assertEqual((found["DP-2"].turn, found["DP-2"].said),
                         (geometry.Turn(ccw=90), "left"))
        self.assertEqual((found["DP-2"].width, found["DP-2"].height), (1080, 1920))

    def test_text_form_matches_the_json_for_the_same_outputs(self) -> None:
        text = kde.parse_text(fixture("kscreen/plasma6_text.txt")) or []
        self.assertEqual([one.connector for one in text], ["HDMI-A-1", "DP-2"])
        self.assertEqual((text[1].x, text[1].width, text[1].height), (1920, 1080, 1920))
        self.assertEqual((text[1].turn, text[1].primary), (geometry.Turn(ccw=90), False))
        self.assertEqual(text[0].refresh_hz, 60.0)

    def test_json_that_is_not_kscreens_is_none(self) -> None:
        self.assertIsNone(kde.parse_json("not json"))
        self.assertIsNone(kde.parse_json("[1, 2]"))
        self.assertIsNone(kde.parse_json('{"outputs": 3}'))

    def test_rule_groups_counts_only_the_rules_and_keeps_nothing_else(self) -> None:
        rules = "[General]\ncount=2\nrules=a,b\n\n[a]\nTitle=secret\n\n[b]\nTitle=other\n"
        self.assertEqual(kde.rule_groups(rules), 2)

    def test_the_probe_falls_back_to_text_when_json_is_refused(self) -> None:
        answers = {"kscreen-doctor -j": Ran(model.FAILED, "", 1),
                   "kscreen-doctor -o": ok(fixture("kscreen/plasma6_text.txt")),
                   "plasmashell --version": ok("plasmashell 6.1.5\n"),
                   "kwin_wayland --version": ok("kwin 6.1.5\n")}
        reading = kde.probe({"XDG_CONFIG_HOME": "/nowhere"}, runner(answers),
                            read=lambda path: "[General]\n\n[one]\nx=1\n")
        self.assertTrue(reading.ok)
        self.assertEqual(len(reading.screens), 2)
        self.assertIn(("Plasma", "6.1.5"), reading.facts)
        self.assertIn(("KWin", "6.1.5 (wayland)"), reading.facts)
        self.assertIn(("Window rules defined in KWin", "1"), reading.facts)

    def test_no_kscreen_doctor_is_not_installed(self) -> None:
        self.assertEqual(kde.probe({}, runner({})).state, model.NOT_HERE)


class Mutter(unittest.TestCase):
    def _state(self, name: str) -> object:
        return json.loads(fixture(name))["data"]

    def test_logical_monitors_become_screens_and_a_monitor_in_none_is_off(self) -> None:
        screens = gnome.parse_state(self._state("mutter/two_transformed_disabled.json")) or []
        found = by_connector(screens)
        self.assertEqual(sorted(found, key=str), ["DP-1", "DP-2", "HDMI-1"])
        self.assertEqual((found["HDMI-1"].primary, found["HDMI-1"].width), (True, 1920))
        self.assertEqual((found["DP-1"].turn, found["DP-1"].width, found["DP-1"].height),
                         (geometry.Turn(ccw=90), 1080, 1920))
        self.assertEqual((found["DP-2"].enabled, found["DP-2"].width), (False, None))
        self.assertEqual(found["HDMI-1"].model, "P2419H")

    def test_a_fractional_scale_gives_the_logical_size(self) -> None:
        (one,) = gnome.parse_state(self._state("mutter/fractional.json")) or []
        self.assertEqual((one.width, one.height, one.scale), (2304, 1440, 1.25))
        self.assertEqual(one.refresh_hz, 120.0)

    def test_the_serial_and_vendor_are_never_read(self) -> None:
        screens = gnome.parse_state(self._state("mutter/two_transformed_disabled.json")) or []
        self.assertNotIn("0000", repr(screens))
        self.assertNotIn("DEL", repr([one.model for one in screens]))

    def test_a_reply_that_is_not_the_state_is_none(self) -> None:
        self.assertIsNone(gnome.parse_state("nothing"))
        self.assertIsNone(gnome.parse_state([1, [], "x"]))

    def test_a_jeepney_variant_unwraps_like_a_busctl_one(self) -> None:
        self.assertEqual(gnome.unwrapped({"is-current": ("b", True)}), {"is-current": True})
        self.assertEqual(gnome.unwrapped({"is-current": {"type": "b", "data": True}}),
                         {"is-current": True})

    def test_the_probe_adds_the_shell_version_and_the_experimental_flag(self) -> None:
        answers = {"gnome-shell --version": ok("GNOME Shell 46.0\n"),
                   "gsettings get org.gnome.mutter experimental-features":
                       ok("['scale-monitors-framebuffer']\n")}
        reading = gnome.probe({}, runner(answers),
                              call=lambda env: self._state("mutter/fractional.json"))
        self.assertTrue(reading.ok)
        self.assertIn(("GNOME Shell", "46.0"), reading.facts)
        self.assertIn(("Experimental scale-monitors-framebuffer", "On"), reading.facts)

    def test_without_jeepney_busctl_is_asked_instead(self) -> None:
        def no_jeepney(env: Mapping[str, str]) -> object:
            raise ImportError

        answer = fixture("mutter/fractional.json")
        reading = gnome.probe({}, runner({
            "busctl --user --json=short call org.gnome.Mutter.DisplayConfig "
            "/org/gnome/Mutter/DisplayConfig org.gnome.Mutter.DisplayConfig GetCurrentState":
                ok(answer)}), call=no_jeepney)
        self.assertTrue(reading.ok)
        self.assertEqual(len(reading.screens), 1)

    def test_with_neither_the_reading_says_busctl_is_not_installed(self) -> None:
        def no_bus(env: Mapping[str, str]) -> object:
            raise OSError

        self.assertEqual(gnome.probe({}, runner({}), call=no_bus).state, model.NOT_HERE)


class Sway(unittest.TestCase):
    def test_every_output_is_read_active_or_not(self) -> None:
        screens = wlr.parse_sway(json.loads(fixture("sway/outputs_two_rotated.json"))) or []
        found = by_connector(screens)
        self.assertEqual((found["DP-2"].turn, found["DP-2"].said),
                         (geometry.Turn(ccw=90), "90"))
        self.assertEqual((found["DP-2"].width, found["DP-2"].height), (1080, 1920))
        self.assertEqual((found["DP-3"].enabled, found["DP-3"].asleep), (False, True))
        self.assertEqual(found["HDMI-A-1"].refresh_hz, 60.0)

    def test_only_our_windows_are_named_and_the_rest_are_counted(self) -> None:
        mine, others = wlr.own_windows([
            Window("chromium-browser-4f2a__app_backglass-Default", "Backglass", "DP-2"),
            Window("firefox", "Private tab - secret", "HDMI-A-1"),
            Window("", "Visual Pinball Player", "HDMI-A-1")])
        self.assertEqual(others, 1)
        self.assertIn("Backglass (VPinFE)", [one.name for one in mine])
        self.assertNotIn("secret", repr(mine))

    def test_the_probe_with_no_socket_says_so(self) -> None:
        reading = wlr.sway({}, runner({}))
        self.assertEqual(reading.state, model.FAILED)
        self.assertEqual(reading.reason, "SWAYSOCK is not set")

    def test_a_socket_that_will_not_open_is_the_compositor_not_answering(self) -> None:
        def refuse(path: str) -> object:
            raise ConnectionRefusedError

        reading = wlr.sway({"SWAYSOCK": "/x"}, runner({}), connect=refuse)  # type: ignore[arg-type]
        self.assertEqual((reading.state, reading.reason),
                         (model.FAILED, "The compositor did not answer"))


class Hyprland(unittest.TestCase):
    def test_a_numbered_transform_a_sleeping_monitor_and_a_mirror_are_read(self) -> None:
        said = json.loads(fixture("hyprland/monitors_two_rotated.json"))
        screens = wlr.parse_hyprland(said) or []
        found = by_connector(screens)
        self.assertEqual((found["DP-2"].turn, found["DP-2"].said),
                         (geometry.Turn(ccw=90), "1"))
        self.assertEqual((found["DP-2"].width, found["DP-2"].height), (1080, 1920))
        self.assertEqual((found["DP-3"].asleep, found["DP-3"].mirrors), (True, "HDMI-A-1"))
        self.assertIsNone(found["HDMI-A-1"].mirrors)

    def test_the_scale_divides_the_mode(self) -> None:
        (one,) = wlr.parse_hyprland([{"name": "eDP-1", "width": 2880, "height": 1800,
                                      "x": 0, "y": 0, "scale": 2.0, "transform": 0}]) or []
        self.assertEqual((one.width, one.height), (1440, 900))

    def test_a_list_of_strings_is_not_monitors(self) -> None:
        self.assertIsNone(wlr.parse_hyprland(["a"]))
        self.assertIsNone(wlr.parse_sway({"a": 1}))


class OtherWayland(unittest.TestCase):
    def test_wlr_randr_json_is_read_with_its_transform(self) -> None:
        screens = wayland_generic.parse_wlr_randr(fixture("wlr-randr/two_rotated.json")) or []
        found = by_connector(screens)
        self.assertEqual((found["DP-2"].width, found["DP-2"].height, found["DP-2"].x),
                         (1080, 1920, 1920))
        self.assertEqual(found["DP-2"].turn, geometry.Turn(ccw=90))
        self.assertEqual(found["HDMI-A-1"].refresh_hz, 60.0)

    def test_wayland_info_gives_the_output_and_the_protocols_worth_naming(self) -> None:
        text = fixture("wayland-info/one_output.txt")
        (one,) = wayland_generic.parse_wayland_info(text) or []
        self.assertEqual((one.width, one.height, one.turn), (1080, 1920, geometry.Turn(ccw=90)))
        self.assertEqual(one.model, "DELL P2419H")
        self.assertEqual(wayland_generic.protocols(text),
                         ["zwlr_layer_shell_v1", "zxdg_output_manager_v1", "xdg_wm_base"])

    def test_wlr_randr_is_tried_first_then_wayland_info(self) -> None:
        reading = wayland_generic.probe({}, runner(
            {"wayland-info": ok(fixture("wayland-info/one_output.txt"))}))
        self.assertTrue(reading.ok)
        self.assertEqual(reading.source, "wayland-info")
        self.assertIn("Protocols offered", dict(reading.facts))

    def test_neither_program_is_a_note_about_what_installing_one_would_add(self) -> None:
        reading = wayland_generic.probe({}, runner({}))
        self.assertEqual(reading.state, model.NOT_HERE)
        self.assertTrue(reading.notes)


class Appliances(unittest.TestCase):
    def test_steamos_is_named_with_its_channel_and_game_mode_is_noted(self) -> None:
        reading = distro.probe(
            {"GAMESCOPE_WAYLAND_DISPLAY": "gamescope-0"},
            runner({"steamos-select-branch -c": ok("stable\n"),
                    "gamescope --version": ok("gamescope version 3.15.9\n")}),
            read=lambda path: fixture("os-release/steamos.txt"))
        self.assertIn(("Distribution", "SteamOS"), reading.facts)
        self.assertIn(("Update channel", "stable"), reading.facts)
        self.assertIn(("Gamescope", "3.15.9"), reading.facts)
        self.assertIn(distro.GAME_MODE, reading.notes)

    def test_a_distribution_that_is_neither_is_a_failure_with_a_reason(self) -> None:
        reading = distro.probe({}, runner({}), read=lambda path: fixture("os-release/ubuntu.txt"))
        self.assertEqual(reading.state, model.FAILED)

    def test_os_release_parses_quotes_and_blank_lines(self) -> None:
        self.assertEqual(distro.parse_os_release('A="x y"\n\nB=\'z\'\nC=1\n'),
                         {"A": "x y", "B": "z", "C": "1"})


class EveryProbeKeepsTheContract(unittest.TestCase):
    """Missing, slow, failing, or garbage: a reading each time, never a raise."""

    PROBES: dict[str, Probe] = {
        "xrandr": x11.probe, "kscreen": kde.probe, "Mutter": _mutter_without_a_bus,
        "wlr-randr": wayland_generic.probe}

    def test_not_installed_is_not_here(self) -> None:
        for name, probe in self.PROBES.items():
            with self.subTest(name):
                self.assertEqual(probe({}, runner({})).state, model.NOT_HERE)

    def test_a_program_that_times_out_is_timed_out(self) -> None:
        slow = runner({"xrandr": Ran(model.TIMED_OUT), "kscreen-doctor": Ran(model.TIMED_OUT),
                       "busctl": Ran(model.TIMED_OUT), "wlr-randr": Ran(model.TIMED_OUT)})
        for name, probe in self.PROBES.items():
            with self.subTest(name):
                self.assertEqual(probe({}, slow).state, model.TIMED_OUT)

    def test_a_program_that_exits_nonzero_is_failed_with_its_name_in_the_reason(self) -> None:
        broken = runner({"xrandr": Ran(model.FAILED, "", 1),
                         "kscreen-doctor": Ran(model.FAILED, "", 1),
                         "busctl": Ran(model.FAILED, "", 1),
                         "wlr-randr": Ran(model.FAILED, "", 1)})
        for name, probe in self.PROBES.items():
            with self.subTest(name):
                reading = probe({}, broken)
                self.assertEqual(reading.state, model.FAILED)
                self.assertFalse(reading.screens)

    def test_garbage_is_failed_and_keeps_at_most_twenty_lines(self) -> None:
        garbage = runner({"xrandr": ok("x\n" * 99), "kscreen-doctor": ok("x\n" * 99),
                          "busctl": ok("x\n" * 99), "wlr-randr": ok("x\n" * 99)})
        for name, probe in self.PROBES.items():
            with self.subTest(name):
                reading = probe({}, garbage)
                self.assertEqual(reading.state, model.FAILED)
                self.assertLessEqual(len(reading.unread), model.UNREAD_LINES)


class UnreadAnswersNameNoUnit(unittest.TestCase):
    def test_serial_description_and_uuid_are_replaced_in_json_and_in_a_printed_value(self) -> None:
        said = ('{"name": "DP-1", "serial": "SN-123", "description": "Maker Panel 27"}\n'
                "{'make': 'Maker', 'serial': \"S'9\"}\nOutput: 1 DP-2 "
                "8f2c1a2b-1111-2222-3333-444455556666")
        text = "\n".join(model.Reading.failed("x", "y", said).unread)
        for leaked in ("SN-123", "Maker", "S'9", "8f2c1a2b"):
            self.assertNotIn(leaked, text)
        self.assertIn("DP-1", text)

    def test_mutter_keeps_the_shape_of_what_it_could_not_read_without_vendor_or_serial(
            self) -> None:
        state = [1, [[["DP-1", "VENDOR", "Panel 27", "SERIAL9"], [], {}]], "not logical"]
        reading = gnome.probe({}, runner({}), call=lambda env: state)
        self.assertEqual(reading.state, model.FAILED)
        text = "\n".join(reading.unread)
        self.assertNotIn("VENDOR", text)
        self.assertNotIn("SERIAL9", text)
        self.assertIn("DP-1", text)

    def test_an_unreadable_busctl_reply_is_not_kept(self) -> None:
        reading = _mutter_without_a_bus({}, runner({"busctl": ok("{ SERIAL9 not json")}))
        self.assertEqual((reading.state, reading.unread), (model.FAILED, ()))

    @unittest.skipUnless(importlib.util.find_spec("jeepney"), "jeepney is not installed")
    def test_a_bus_that_refuses_the_login_is_the_desktop_not_answering(self) -> None:
        from jeepney.auth import AuthenticationError

        with patch("jeepney.io.blocking.open_dbus_connection",
                   side_effect=AuthenticationError(b"x")), self.assertRaises(OSError):
            gnome._call_jeepney({"DBUS_SESSION_BUS_ADDRESS": "unix:path=/nowhere"})


def _adapter(name: str, x: int, y: int, width: int, height: int, orientation: int = 0, *,
             flags: int = windows.ATTACHED, monitor: str = "Model A", hz: int = 60,
             dpi: int | None = 96) -> windows.Device:
    return windows.Device(name, flags, monitor,
                          windows.Mode(x, y, width, height, hz, orientation), dpi)


def _windows(*devices: windows.Device, awareness: int | None = 2) -> model.Reading:
    return windows.probe({}, runner({}), lambda: windows.Snapshot(devices, awareness),
                         lambda: ("10", "10.0.22631", "", ""))


class WindowsAdapters(unittest.TestCase):
    def test_an_adapter_on_the_desktop_is_a_screen_with_its_mode_and_scale(self) -> None:
        one = _windows(_adapter(r"\\.\DISPLAY1", 0, 0, 3840, 2160, flags=0x5, dpi=144)).screens[0]
        self.assertEqual((one.connector, one.model, one.width, one.height),
                         (r"\\.\DISPLAY1", "Model A", 3840, 2160))
        self.assertEqual((one.scale, one.refresh_hz, one.primary), (1.5, 60.0, True))

    def test_a_turned_screen_keeps_the_degrees_windows_said(self) -> None:
        one = _windows(_adapter("a", 0, 0, 1080, 1920, 3)).screens[0]
        self.assertEqual((one.turn, one.said, one.shape), (geometry.Turn(ccw=270), "270°",
                                                           "portrait"))
        self.assertFalse(_windows(_adapter("a", 0, 0, 1920, 1080)).screens[0].is_turned)

    def test_an_adapter_off_the_desktop_or_a_mirroring_driver_is_not_a_screen(self) -> None:
        reading = _windows(_adapter("a", 0, 0, 1920, 1080),
                           _adapter("b", 0, 0, 800, 600, flags=0),
                           _adapter("c", 0, 0, 800, 600, flags=windows.ATTACHED
                                    | windows.MIRRORING_DRIVER),
                           windows.Device("d", windows.ATTACHED, "", None, None))
        self.assertEqual([one.connector for one in reading.screens], ["a"])

    def test_two_screens_in_one_place_are_duplicated_and_two_apart_are_extended(self) -> None:
        extended = _windows(_adapter("a", 0, 0, 1920, 1080), _adapter("b", 1920, 0, 1920, 1080))
        copied = _windows(_adapter("a", 0, 0, 1920, 1080), _adapter("b", 0, 0, 1920, 1080))
        self.assertIn(("Multiple displays", "Extended"), extended.facts)
        self.assertIn(("Multiple displays", "Duplicated"), copied.facts)
        self.assertEqual([one.mirrors for one in copied.screens], [None, "a"])
        self.assertNotIn("Multiple displays", dict(_windows(_adapter("a", 0, 0, 1, 2)).facts))

    def test_a_hardware_default_refresh_is_not_a_rate(self) -> None:
        self.assertIsNone(_windows(_adapter("a", 0, 0, 1, 2, hz=1)).screens[0].refresh_hz)

    def test_the_build_names_windows_11_where_the_release_says_10(self) -> None:
        self.assertEqual(windows.release_line("10", "10.0.22631"), "Windows 11, build 22631 (DWM)")
        self.assertEqual(windows.release_line("10", "10.0.19045"), "Windows 10, build 19045 (DWM)")
        self.assertEqual(windows.release_line("", ""), "Windows  (DWM)")
        self.assertIn(("Desktop", "Windows 11, build 22631 (DWM)"), _windows().facts)

    def test_a_program_not_told_each_screens_scale_does_not_report_one(self) -> None:
        for awareness in (windows.UNAWARE, windows.SYSTEM_AWARE):
            reading = _windows(_adapter("a", 0, 0, 1, 2, dpi=96), awareness=awareness)
            self.assertIsNone(reading.screens[0].scale, awareness)
            self.assertEqual(len(reading.notes), 1)
        told = _windows(_adapter("a", 0, 0, 1, 2, dpi=144), awareness=2)
        self.assertEqual((told.screens[0].scale, told.notes), (1.5, ()))

    def test_a_call_windows_refuses_is_windows_would_not_say(self) -> None:
        def refuses() -> windows.Snapshot:
            raise OSError

        reading = windows.probe({}, runner({}), refuses)
        self.assertEqual((reading.state, reading.reason), (model.FAILED, "Windows would not say"))

    def test_any_failure_reading_windows_is_windows_would_not_say(self) -> None:
        def refuses() -> windows.Snapshot:
            raise TypeError("ArgumentError stands in here")

        self.assertEqual(windows.probe({}, runner({}), refuses).state, model.FAILED)

    def test_off_windows_the_reader_refuses_and_the_probe_still_returns(self) -> None:
        with patch("sys.platform", "linux"):
            self.assertEqual(windows.probe({}, runner({})).state, model.FAILED)


def _display(number: int, name: str, x: int, y: int, width: int, height: int, *,
             rotation: int = 0, main: bool = False, mirror_of: int = 0,
             asleep: bool = False) -> macos.Display:
    return macos.Display(number, name, x, y, width, height, 2.0, 60, rotation, main, asleep,
                         mirror_of)


def _mac(*displays: macos.Display, spaces: Ran | None = None) -> model.Reading:
    return macos.probe({}, runner({"defaults": spaces or Ran(model.NOT_HERE)}),
                       lambda: list(displays), lambda: ("15.6", ("", "", ""), "arm64"))


class MacScreens(unittest.TestCase):
    def test_a_screen_has_its_name_scale_refresh_and_rotation(self) -> None:
        one = _mac(_display(1, "Model B", 0, 0, 3008, 1692, main=True)).screens[0]
        self.assertEqual((one.model, one.width, one.scale, one.refresh_hz, one.primary),
                         ("Model B", 3008, 2.0, 60.0, True))
        self.assertEqual((one.turn, one.said), (geometry.Turn(), "0°"))
        self.assertIsNone(one.connector)

    def test_a_turned_display_keeps_the_degrees_quartz_said(self) -> None:
        one = _mac(_display(1, "Model B", 0, 0, 1080, 1920, rotation=90)).screens[0]
        self.assertEqual((one.turn, one.said), (geometry.Turn(ccw=90), "90°"))

    def test_a_mirror_names_the_display_it_copies_and_a_sleeping_one_is_asleep(self) -> None:
        first, second = _mac(_display(1, "Model B", 0, 0, 1, 2),
                             _display(2, "Model C", 0, 0, 1, 2, mirror_of=1, asleep=True)).screens
        self.assertEqual((first.mirrors, first.asleep), (None, None))
        self.assertEqual((second.mirrors, second.asleep), ("Model B", True))

    def test_the_session_line_is_the_release_the_toolkit_and_the_architecture(self) -> None:
        with patch("platform.machine", return_value="arm64"):
            self.assertIn(("Desktop", "macOS 15.6 (Quartz), arm64"), _mac().facts)

    def test_spaces_are_separate_where_the_key_is_zero_or_missing_and_shared_at_one(self) -> None:
        for said, expected in ((Ran(model.OK, "0\n"), "On"), (Ran(model.FAILED, "", 1), "On"),
                               (Ran(model.OK, "1\n"), "Off, one Space spans all displays")):
            with self.subTest(said.text):
                self.assertIn(("Displays have separate Spaces", expected),
                              _mac(spaces=said).facts)

    def test_spaces_say_nothing_when_defaults_will_not_answer(self) -> None:
        for said in (Ran(model.NOT_HERE), Ran(model.TIMED_OUT), Ran(model.OK, "maybe")):
            with self.subTest(said.state):
                self.assertNotIn("Displays have separate Spaces", dict(_mac(spaces=said).facts))

    def test_a_toolkit_that_is_not_installed_is_macos_would_not_say(self) -> None:
        def missing() -> list[macos.Display]:
            raise ImportError

        reading = macos.probe({}, runner({}), missing)
        self.assertEqual((reading.state, reading.reason), (model.FAILED, "macOS would not say"))


class DegreesAreQuarterTurns(unittest.TestCase):
    def test_a_quarter_turn_is_counted_counter_clockwise_and_anything_else_is_none(self) -> None:
        self.assertEqual([geometry.degrees_turn(one).ccw for one in (0, 90, 180, 270)],
                         [0, 90, 180, 270])
        self.assertEqual([geometry.degrees_turn(one) for one in (45, "x", None)],
                         [geometry.NONE] * 3)


class XrandrAxes(unittest.TestCase):
    def test_x_mirrors_y_mirrors_and_turns_half_way_and_both_are_a_half_turn(self) -> None:
        turn = geometry.xrandr_turn
        self.assertEqual(turn("normal", " X axis"), geometry.Turn(True, 0))
        self.assertEqual(turn("normal", " Y axis"), geometry.Turn(True, 180))
        self.assertEqual(turn("normal", " X and Y axis"), geometry.Turn(False, 180))
        self.assertEqual(turn("left", " X and Y axis"), geometry.Turn(False, 270))
        self.assertEqual(turn("left"), geometry.Turn(ccw=90))


class FixturesAreAccountedFor(unittest.TestCase):
    ROOT = FIXTURES

    def test_every_fixture_has_a_row_in_the_provenance_file(self) -> None:
        listed = (self.ROOT / "PROVENANCE.md").read_text(encoding="utf-8")
        files = sorted(p.relative_to(self.ROOT).as_posix() for p in self.ROOT.rglob("*")
                       if p.is_file() and p.name != "PROVENANCE.md")
        self.assertTrue(files)
        self.assertEqual([name for name in files if f"`{name}`" not in listed], [])

    def test_every_row_names_a_file_that_exists(self) -> None:
        listed = (self.ROOT / "PROVENANCE.md").read_text(encoding="utf-8")
        rows = re.findall(r"^\| `([^`]+)` \|", listed, re.MULTILINE)
        self.assertEqual([name for name in rows if not (self.ROOT / name).is_file()], [])


if __name__ == "__main__":
    unittest.main()
