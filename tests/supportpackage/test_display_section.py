"""What session this is, the parallel runner, and the Displays And Layout section that reads
through them."""

from __future__ import annotations

import time
import unittest
from collections.abc import Mapping, Sequence
from types import SimpleNamespace
from unittest.mock import patch

from common.capture import geometry
from common.support.collect.displays import compare, gather, model, section
from common.support.collect.displays import desktop as desk
from common.support.collect.displays.probes import chosen
from common.support.collect.displays.run import PROBE_SECONDS, Ran, Run
from common.support.collect.displays.run import run as run_program


def no_program(argv: Sequence[str]) -> Ran:
    return Ran(model.NOT_HERE)


class Resolving(unittest.TestCase):
    def resolve(self, env: Mapping[str, str], system: str = "linux", vm: str = "",
                os_release: str = "", paths: Sequence[str] = ()) -> desk.Desktop:
        return desk.resolve(
            env, system, exists=lambda path: path in paths,
            read=lambda path: os_release if path == "/etc/os-release" else "",
            run=lambda argv: Ran(model.OK, vm) if vm else Ran(model.NOT_HERE))

    def test_windows_and_macos_need_no_environment(self) -> None:
        self.assertEqual(self.resolve({}, "windows"), desk.Desktop("windows", "dwm", "explorer"))
        self.assertEqual(self.resolve({}, "darwin"), desk.Desktop("macos", "quartz", "aqua"))

    def test_the_compositor_variables_win_over_the_desktop_name(self) -> None:
        env = {"WAYLAND_DISPLAY": "wayland-1", "SWAYSOCK": "/s", "XDG_CURRENT_DESKTOP": "GNOME"}
        found = self.resolve(env)
        self.assertEqual((found.server, found.shell), (desk.WAYLAND, desk.SWAY))
        self.assertTrue(found.native_wayland)

    def test_gamescope_is_game_mode(self) -> None:
        found = self.resolve({"GAMESCOPE_WAYLAND_DISPLAY": "gamescope-0",
                              "WAYLAND_DISPLAY": "wayland-0"})
        self.assertTrue(found.game_mode)

    def test_a_desktop_name_with_several_words_is_taken_by_the_first_it_knows(self) -> None:
        found = self.resolve({"DISPLAY": ":0", "XDG_CURRENT_DESKTOP": "ubuntu:GNOME"})
        self.assertEqual((found.server, found.shell), (desk.X11, desk.GNOME))

    def test_no_display_variable_is_no_display_session(self) -> None:
        self.assertEqual(self.resolve({}).server, desk.NO_SERVER)
        self.assertEqual(self.resolve({}).shell, desk.UNKNOWN)

    def test_a_session_type_alone_is_enough_to_name_the_server(self) -> None:
        self.assertEqual(self.resolve({"XDG_SESSION_TYPE": "wayland"}).server, desk.WAYLAND)
        self.assertEqual(self.resolve({"XDG_SESSION_TYPE": "x11"}).server, desk.X11)

    def test_the_container_is_named_by_the_first_sign_of_it(self) -> None:
        self.assertEqual(self.resolve({"WSL_DISTRO_NAME": "Ubuntu"}).container, "wsl")
        self.assertEqual(self.resolve({"FLATPAK_ID": "x"}).container, "flatpak")
        self.assertEqual(self.resolve({}, paths=["/.dockerenv"]).container, "docker")
        self.assertEqual(self.resolve({"container": "podman"}).container, "podman")
        self.assertEqual(self.resolve({}).container, desk.NO_CONTAINER)

    def test_a_virtual_machine_is_named_and_none_is_not_one(self) -> None:
        self.assertEqual(self.resolve({}, vm="qemu\n").vm, "qemu")
        self.assertEqual(self.resolve({}, vm="none\n").vm, "")
        self.assertIsNone(self.resolve({}).vm)

    def test_the_distribution_is_os_releases_id(self) -> None:
        self.assertEqual(self.resolve({}, os_release='NAME="x"\nID="steamos"\n').distro, "steamos")


class Choosing(unittest.TestCase):
    def names(self, **fields: str) -> list[str]:
        return [name for name, _ in chosen(desk.Desktop(desk.LINUX, **fields))]

    def test_each_session_asks_what_it_can_answer_to(self) -> None:
        self.assertEqual(self.names(server=desk.X11, shell=desk.OTHER), ["xrandr"])
        self.assertEqual(self.names(server=desk.WAYLAND, shell=desk.SWAY), ["sway"])
        self.assertEqual(self.names(server=desk.WAYLAND, shell=desk.HYPRLAND), ["Hyprland"])
        self.assertEqual(self.names(server=desk.WAYLAND, shell=desk.KDE), ["kscreen-doctor"])
        self.assertEqual(self.names(server=desk.X11, shell=desk.KDE),
                         ["xrandr", "kscreen-doctor"])
        self.assertEqual(self.names(server=desk.WAYLAND, shell=desk.GNOME), ["Mutter"])
        self.assertEqual(self.names(server=desk.WAYLAND, shell=desk.OTHER), ["wlr-randr"])

    def test_game_mode_asks_for_the_appliance_and_not_for_a_compositor(self) -> None:
        self.assertEqual(self.names(server=desk.WAYLAND, shell=desk.GAMESCOPE), ["os-release"])

    def test_an_appliance_adds_its_own_reading(self) -> None:
        self.assertEqual(self.names(server=desk.WAYLAND, shell=desk.SWAY, distro="vpinos"),
                         ["sway", "os-release"])

    def test_windows_and_macos_each_ask_their_one_probe(self) -> None:
        windows = chosen(desk.Desktop(desk.WINDOWS, desk.DWM, "explorer"))
        macos = chosen(desk.Desktop(desk.MACOS, desk.QUARTZ, "aqua"))
        self.assertEqual([name for name, _ in windows], ["Windows"])
        self.assertEqual([name for name, _ in macos], ["Quartz"])


class Gathering(unittest.TestCase):
    here = desk.Desktop(desk.LINUX, desk.X11, desk.OTHER)

    def test_no_probe_is_no_reading(self) -> None:
        self.assertEqual(gather.readings(self.here, {}, no_program, probes=[]), [])

    def test_readings_come_back_in_the_order_the_probes_were_chosen(self) -> None:
        probes = [("b", lambda env, run: model.Reading("b")),
                  ("a", lambda env, run: model.Reading("a"))]
        found = gather.readings(self.here, {}, no_program, probes=probes)
        self.assertEqual([one.source for one in found], ["b", "a"])

    def test_a_probe_that_raises_is_a_gap_with_the_kind_of_failure_and_no_detail(self) -> None:
        def boom(env: Mapping[str, str], run: object) -> model.Reading:
            raise RuntimeError("secret detail")

        with self.assertLogs("vpinfe.common.support.collect.displays", "WARNING"):
            (found,) = gather.readings(self.here, {}, no_program, probes=[("x", boom)])
        self.assertEqual((found.state, found.reason), (model.FAILED, "RuntimeError"))
        self.assertNotIn("secret", repr(found))

    def test_a_probe_still_running_when_time_is_up_is_timed_out_and_the_rest_are_kept(self) -> None:
        def slow(env: Mapping[str, str], run: object) -> model.Reading:
            time.sleep(1)
            return model.Reading("slow")

        started = time.monotonic()
        found = gather.readings(self.here, {}, no_program, seconds=0.1, probes=[
            ("slow", slow), ("quick", lambda env, run: model.Reading("quick"))])
        self.assertLess(time.monotonic() - started, 0.9)
        self.assertEqual([(one.source, one.state) for one in found],
                         [("slow", model.TIMED_OUT), ("quick", model.OK)])

    def test_no_program_starts_near_the_end_so_the_screens_already_read_are_kept(self) -> None:
        def several(env: Mapping[str, str], run: Run) -> model.Reading:
            first = run(["first"])
            time.sleep(0.3)
            second = run(["second"])
            return model.Reading("several", screens=(model.ScreenFact(connector="A"),),
                                 facts=(("first", first.state), ("second", second.state)))

        asked: list[Sequence[str]] = []

        def noting(argv: Sequence[str]) -> Ran:
            asked.append(argv)
            return Ran(model.OK, "1")

        (found,) = gather.readings(self.here, {}, noting, seconds=PROBE_SECONDS + 0.2,
                                   probes=[("several", several)])
        self.assertEqual(asked, [["first"]])
        self.assertEqual((found.state, len(found.screens)), (model.OK, 1))
        self.assertEqual(dict(found.facts), {"first": model.OK, "second": model.TIMED_OUT})


class Running(unittest.TestCase):
    def test_a_program_that_is_not_installed_is_not_here(self) -> None:
        self.assertEqual(run_program(["definitely-not-a-program-here"]), Ran(model.NOT_HERE))

    def test_the_output_of_an_installed_program_is_kept(self) -> None:
        found = run_program(["python3", "-c", "print('hi')"]) \
            if run_program(["python3", "--version"]).ok else None
        if found is not None:
            self.assertEqual((found.state, found.text.strip()), (model.OK, "hi"))


HERE = desk.Desktop(desk.LINUX, desk.X11, desk.OTHER)
SEEN = (model.Seen(0, "Monitor A", 0, 0, 1920, 1080),
        model.Seen(1, "Monitor B", 1920, 0, 1080, 1920))


def collected(found: list[model.Reading], here: desk.Desktop = HERE,
              vp: compare.VpFacts | None = None, rotation: int = 0,
              orientation: str = "landscape", seen: Sequence[model.Seen] = SEEN,
              windows: tuple[str, str, str] = ("0", "1", "")) -> str:
    config = SimpleNamespace(
        playfield_orientation=orientation, playfield_rotation=rotation, extra_screen_ids={},
        window_screen_id=lambda key: dict(zip(
            ("playfieldscreenid", "backglassscreenid", "scoreviewscreenid"), windows,
            strict=True))[key])
    with patch.object(section.desk, "resolve", return_value=here), \
            patch.object(section.gather, "readings", return_value=found), \
            patch.object(section, "screens_seen", return_value=list(seen)), \
            patch.object(section.DisplayConfig, "from_config", return_value=config), \
            patch.object(section, "_visual_pinball", return_value=(vp, "No launcher.")), \
            patch.object(section, "_rules_added", return_value=None), \
            patch.object(section.about, "windowing_system", return_value="X11"), \
            patch.object(section.session, "read", return_value=[]), \
            patch.object(section.screen_size, "reported_mm", return_value=None):
        return "\n".join(section.collect().lines)


def xrandr_reading() -> model.Reading:
    turned = model.ScreenFact(
        connector="DP-1", x=1920, y=0, width=1080, height=1920, turn=geometry.Turn(ccw=90),
        said="left", source="xrandr", enabled=True)

    first = model.ScreenFact(connector="HDMI-1", x=0, y=0, width=1920, height=1080,
                             turn=geometry.NONE, said="normal", source="xrandr",
                             primary=True, enabled=True)
    return model.Reading("xrandr", screens=(first, turned), facts=(("Window manager", "Openbox"),))


class TheSection(unittest.TestCase):
    def test_each_screen_gets_a_block_that_names_it_and_what_uses_it(self) -> None:
        text = collected([xrandr_reading()], rotation=0)
        self.assertIn("### Screen 0: HDMI-1 · Monitor A · primary", text)
        self.assertIn("### Screen 1: DP-1 · Monitor B", text)
        self.assertIn("- **Used for:** Playfield (VPinFE)", text)
        self.assertIn("- **Used for:** Backglass (VPinFE)", text)

    def test_rotation_is_three_rows_and_what_that_makes(self) -> None:
        text = collected([xrandr_reading()], rotation=90, orientation="portrait")
        self.assertIn("  - **Set in VPinFE:** Orientation Portrait, Rotation 90°", text)
        self.assertIn("  - **Reported by the device:** Not rotated (landscape, 1920x1080)", text)
        self.assertIn("  - **What that makes:**", text)

    def test_the_operating_systems_word_prints_beside_the_degrees(self) -> None:
        text = collected([xrandr_reading()], windows=("1", "0", ""))
        self.assertIn("90° (xrandr says left)", text)

    def test_a_probe_that_found_nothing_says_why_in_one_line(self) -> None:
        text = collected([model.Reading.not_here("kscreen-doctor"),
                          model.Reading.timed_out("xrandr"),
                          model.Reading.failed("Mutter", "The desktop did not answer")])
        self.assertIn("- **kscreen-doctor:** Not installed", text)
        self.assertIn("- **xrandr:** Timed out", text)
        self.assertIn("- **Mutter:** Could not read: The desktop did not answer", text)

    def test_what_a_probe_could_not_read_is_fenced_under_its_own_heading(self) -> None:
        text = collected([model.Reading.failed("xrandr", "unreadable", "line one\nline two")])
        self.assertIn("### Could Not Read: xrandr", text)
        self.assertIn("```text\nline one\nline two\n```", text)

    def test_with_no_probes_the_section_still_has_the_screens_and_no_findings(self) -> None:
        text = collected([])
        self.assertIn("### Screen 0: Monitor A", text)
        self.assertIn("Nothing found that looks wrong in what could be compared.", text)
        self.assertNotIn("What The Desktop Was Asked", text)

    def test_a_window_set_to_a_missing_screen_is_a_problem_and_is_in_the_window_list(self) -> None:
        text = collected([], seen=SEEN[:1])
        self.assertIn("- **Problem:** VPinFE's backglass window is set to screen 1", text)
        self.assertIn("- **Backglass:** Set to screen 1, but only 1 screen was found", text)

    def test_no_screens_at_all_is_said_plainly(self) -> None:
        self.assertIn("No screens were found.", collected([], seen=(), windows=("", "", "")))

    def test_a_screen_the_desktop_reports_and_vpinfe_does_not_is_listed_apart(self) -> None:
        extra = model.ScreenFact(connector="DP-3", enabled=False, source="xrandr")
        reading = model.Reading("xrandr", screens=(*xrandr_reading().screens, extra))
        text = collected([reading])
        self.assertIn("### Other Screens The Desktop Reports", text)
        self.assertIn("- **DP-3:** Off", text)

    def test_the_desktop_line_names_the_container_and_the_virtual_machine(self) -> None:
        here = desk.Desktop(desk.LINUX, desk.WAYLAND, desk.KDE, "flatpak", "qemu", "ubuntu")
        text = collected([], here=here)
        self.assertIn("- **Desktop:** KDE on Wayland", text)
        self.assertIn("- **Container:** flatpak", text)
        self.assertIn("- **Virtual machine:** qemu. Screen sizes may be placeholders", text)

    def test_windows_the_desktop_reports_are_listed_by_their_names(self) -> None:
        reading = model.Reading("sway", windows=(model.WindowFact("Backglass (VPinFE)", "DP-2"),))
        self.assertIn("- **Backglass (VPinFE):** on DP-2", collected([reading]))

    def test_without_a_launcher_the_visual_pinball_block_says_why(self) -> None:
        text = collected([])
        self.assertIn("### Visual Pinball Against Screens\n\nNo launcher.", text)


if __name__ == "__main__":
    unittest.main()
