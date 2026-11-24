"""Window rules: which session gets them, what they say, and the one line in the config."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from common.host import placement, tools

ROWS = [
    placement.Row("playfield", "playfield", "DP-1"),
    placement.Row("bg", "backglass", "DP-2"),
    placement.Row("dmd", "scoreview", ""),
]
VPX = [
    placement.AppWindow("Visual Pinball Player", "playfield"),
    placement.AppWindow("Visual Pinball Backglass", "backglass"),
    placement.AppWindow("Visual Pinball Score View", "scoreview"),
]
WAYLAND = {"WAYLAND_DISPLAY": "wayland-1"}


class SessionTests(unittest.TestCase):
    def test_each_session_is_known_by_what_it_sets(self) -> None:
        cases = {
            "sway": {**WAYLAND, "SWAYSOCK": "/run/sway.sock"},
            "hyprland": {**WAYLAND, "HYPRLAND_INSTANCE_SIGNATURE": "abc"},
            "kde": {**WAYLAND, "XDG_CURRENT_DESKTOP": "KDE"},
            "gnome": {**WAYLAND, "XDG_CURRENT_DESKTOP": "ubuntu:GNOME"},
        }
        for kind, env in cases.items():
            with self.subTest(kind=kind):
                self.assertEqual(placement.session(env, tools.LINUX), kind)

    def test_nothing_is_offered_off_wayland_or_off_linux_or_for_another_desktop(self) -> None:
        sway = {**WAYLAND, "SWAYSOCK": "/run/sway.sock"}
        self.assertEqual(placement.session({"SWAYSOCK": "/run/sway.sock"}, tools.LINUX), "")
        self.assertEqual(placement.session(sway, tools.WINDOWS), "")
        self.assertEqual(placement.session({**WAYLAND, "XDG_CURRENT_DESKTOP": "XFCE"},
                                           tools.LINUX), "")
        self.assertIsNone(placement.offer({}, tools.LINUX))


class RulesTests(unittest.TestCase):
    def test_sway_sends_each_window_to_its_output_and_leaves_one_without_a_screen(self) -> None:
        text = placement.rules(placement.SWAY, ROWS, VPX)

        self.assertIn('assign [app_id="^chrom(e|ium)-.*__app_playfield-Default$"] '
                      'output DP-1', text)
        self.assertIn('assign [app_id="^chrom(e|ium)-.*__app_bg-Default$"] output DP-2', text)
        self.assertIn('assign [title="^Visual\\ Pinball\\ Backglass$"] output DP-2', text)
        self.assertNotIn('assign [title="^Visual\\ Pinball\\ Score\\ View$"]', text)
        self.assertNotIn("app_dmd-Default$\"] output", text)

    def test_a_scoreview_with_a_screen_sends_visual_pinballs_there_too(self) -> None:
        rows = [*ROWS[:2], placement.Row("dmd", "scoreview", "HDMI-A-1")]

        text = placement.rules(placement.SWAY, rows, VPX)

        self.assertIn('assign [title="^Visual\\ Pinball\\ Score\\ View$"] output HDMI-A-1',
                      text)

    def test_a_topper_with_a_screen_sends_visual_pinballs_there_too(self) -> None:
        rows = [*ROWS, placement.Row("topper", "topper", "HDMI-A-2")]
        vpx = [*VPX, placement.AppWindow("Visual Pinball Topper", "topper")]

        text = placement.rules(placement.SWAY, rows, vpx)

        self.assertIn('assign [app_id="^chrom(e|ium)-.*__app_topper-Default$"] '
                      'output HDMI-A-2', text)
        self.assertIn('assign [title="^Visual\\ Pinball\\ Topper$"] output HDMI-A-2', text)

    def test_every_window_loses_its_border_including_one_with_no_screen(self) -> None:
        text = placement.rules(placement.SWAY, ROWS, VPX)

        self.assertIn("(playfield|bg|dmd)-Default$\"] border none", text)
        self.assertIn('for_window [title="^(Visual\\ Pinball\\ Player|Visual\\ Pinball\\ '
                      'Backglass|Visual\\ Pinball\\ Score\\ View)$"] border none', text)

    def test_the_playfield_takes_focus_and_the_others_of_visual_pinball_never_do(self) -> None:
        text = placement.rules(placement.SWAY, ROWS, VPX)

        self.assertIn('app_playfield-Default$"] focus', text)
        self.assertIn('title="^Visual\\ Pinball\\ Player$"] focus', text)
        self.assertIn('no_focus [title="^(Visual\\ Pinball\\ Backglass|Visual\\ Pinball\\ '
                      'Score\\ View)$"]', text)
        self.assertNotIn('no_focus [title="^Visual\\ Pinball\\ Player', text)
        self.assertNotIn("app_bg-Default$\"] focus", text)

    def test_the_file_says_it_is_vpinfes_and_where_screens_are_changed(self) -> None:
        for kind in (placement.SWAY, placement.HYPRLAND):
            head = placement.rules(kind, ROWS, VPX).splitlines()[:3]
            with self.subTest(kind=kind):
                self.assertTrue(all(line.startswith("#") for line in head))
                self.assertIn("VPinFE writes this file", head[1])
                self.assertIn("Displays", head[2])

    def test_hyprland_uses_its_own_rule_names(self) -> None:
        text = placement.rules(placement.HYPRLAND, ROWS, VPX)

        self.assertIn("windowrulev2 = monitor DP-1, class:^chrom(e|ium)-.*__app_playfield-Default$",
                      text)
        self.assertIn("windowrulev2 = noborder, class:", text)
        self.assertIn("windowrulev2 = noinitialfocus, title:", text)
        self.assertNotIn("assign", text)
        self.assertNotIn("for_window", text)

    def test_an_output_with_a_space_is_quoted(self) -> None:
        text = placement.rules(placement.SWAY, [placement.Row("bg", "backglass", "Dell Inc 1")],
                               [])

        self.assertIn('output "Dell Inc 1"', text)

    def test_the_rules_are_short(self) -> None:
        self.assertLessEqual(len(placement.rules(placement.SWAY, ROWS, VPX).splitlines()), 16)


class LineTests(unittest.TestCase):
    def test_sway_includes_and_hyprland_sources(self) -> None:
        path = Path("/cfg/vpinfe/window-rules.conf")

        self.assertEqual(placement.include_line(placement.SWAY, path), f"include {path}")
        self.assertEqual(placement.include_line(placement.HYPRLAND, path), f"source = {path}")

    def test_a_path_with_a_space_is_quoted_on_sway(self) -> None:
        path = Path("/home/some one/vpinfe.conf")

        self.assertEqual(placement.include_line(placement.SWAY, path), f'include "{path}"')


class ConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.home = Path(self.dir.name) / "home"
        self.rules = Path(self.dir.name) / "rules"
        self.rules.mkdir()
        self.env = {**WAYLAND, "SWAYSOCK": "/run/sway.sock"}

    def _config(self, text: str, relative: str = ".config/sway/config") -> Path:
        path = self.home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def _offer(self, env: dict | None = None) -> placement.Offer:
        found = placement.offer(env or self.env, tools.LINUX, self.home, self.rules)
        assert found is not None
        return found

    def test_no_config_of_the_persons_is_ever_made(self) -> None:
        found = self._offer()

        self.assertIsNone(found.config)
        with self.assertRaises(OSError):
            placement.add(found, "x\n", self.rules)
        self.assertFalse((self.home / ".config").exists())

    def test_the_older_sway_location_is_found_when_the_other_is_not_there(self) -> None:
        old = self._config("", ".sway/config")

        self.assertEqual(self._offer().config, old)

    def test_the_config_sway_loads_first_is_the_one_found(self) -> None:
        old = self._config("", ".sway/config")
        self._config("")

        self.assertEqual(self._offer().config, old)

    def test_xdg_config_home_is_where_the_newer_location_is(self) -> None:
        other = Path(self.dir.name) / "xdg" / "sway" / "config"
        other.parent.mkdir(parents=True)
        other.write_text("", encoding="utf-8")

        found = self._offer({**self.env, "XDG_CONFIG_HOME": str(other.parent.parent)})

        self.assertEqual(found.config, other)

    def test_a_read_only_config_is_not_swapped_for_one_sway_does_not_load(self) -> None:
        self._config("", ".sway/config").chmod(0o444)
        self._config("")

        self.assertIsNone(self._offer().config)

    def test_add_writes_the_rules_and_appends_one_line_to_the_config(self) -> None:
        config = self._config("set $mod Mod4\n")

        placement.add(self._offer(), "rules\n", self.rules)

        self.assertEqual((self.rules / "window-rules-sway.conf").read_text(), "rules\n")
        self.assertEqual(config.read_text(),
                         f"set $mod Mod4\n\n# VPinFE window rules\n"
                         f"include {self.rules / 'window-rules-sway.conf'}\n")

    def test_a_config_without_a_final_newline_keeps_its_last_line_whole(self) -> None:
        config = self._config("set $mod Mod4")

        placement.add(self._offer(), "rules\n", self.rules)

        self.assertTrue(config.read_text().startswith("set $mod Mod4\n\n# VPinFE"))

    def test_once_added_it_says_so_and_is_not_added_twice(self) -> None:
        config = self._config("")
        placement.add(self._offer(), "rules\n", self.rules)

        self.assertTrue(self._offer().added)
        self.assertEqual(config.read_text().count("window-rules-sway.conf"), 1)

    def test_adding_twice_adds_once_even_from_a_stale_offer(self) -> None:
        config = self._config("")
        stale = self._offer()
        placement.add(stale, "rules\n", self.rules)

        placement.add(stale, "rules\n", self.rules)

        self.assertEqual(config.read_text().count("window-rules-sway.conf"), 1)

    def test_a_config_that_is_not_utf8_is_added_to_and_left_intact(self) -> None:
        config = self._config("")
        config.write_bytes(b"# caf\xe9\nset $mod Mod4")

        placement.add(self._offer(), "rules\n", self.rules)

        self.assertTrue(config.read_bytes().startswith(b"# caf\xe9\nset $mod Mod4\n\n# VPinFE"))

    def test_a_hyprland_line_with_a_trailing_comment_counts_as_added(self) -> None:
        env = {**WAYLAND, "HYPRLAND_INSTANCE_SIGNATURE": "abc"}
        path = self.rules / "window-rules-hyprland.conf"
        self._config(f"source = {path} # window rules\n", ".config/hypr/hyprland.conf")

        self.assertTrue(self._offer(env).added)

    def test_a_home_variable_in_the_include_counts_as_added(self) -> None:
        with mock.patch.dict(os.environ, {"U5_RULES": str(self.rules)}):
            self._config("include $U5_RULES/window-rules-sway.conf\n")
            self.assertTrue(self._offer().added)

    def test_a_commented_out_line_is_not_added(self) -> None:
        path = self.rules / "window-rules-sway.conf"
        self._config(f"# include {path}\n")

        self.assertFalse(self._offer().added)

    def test_hyprland_reads_its_own_file_and_its_own_line(self) -> None:
        env = {**WAYLAND, "HYPRLAND_INSTANCE_SIGNATURE": "abc"}
        config = self._config("", ".config/hypr/hyprland.conf")

        found = self._offer(env)
        placement.add(found, "rules\n", self.rules)

        self.assertEqual(found.config, config)
        self.assertIn(f"source = {self.rules / 'window-rules-hyprland.conf'}", config.read_text())
        self.assertTrue(self._offer(env).added)

    def test_gnome_and_kde_have_no_rules_file_and_nothing_to_add(self) -> None:
        found = self._offer({**WAYLAND, "XDG_CURRENT_DESKTOP": "GNOME"})

        self.assertEqual(found.kind, placement.GNOME)
        self.assertIsNone(found.rules)
        self.assertIsNone(found.config)


if __name__ == "__main__":
    unittest.main()
