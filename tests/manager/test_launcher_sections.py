"""Which sections a launcher's rail offers, and when.

Offering a settings editor for a program that is not on this machine is a form of
lying: there is nothing to read it out of and nothing a write could mean. Details
stays, because pointing the launcher somewhere else is how it gets fixed.
"""

from __future__ import annotations

import asyncio
import unittest
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import ANY, AsyncMock, Mock, patch
from urllib.parse import parse_qs

from nicegui import run

from common import i18n
from common.config import path_state
from common.i18n import t
from manager import page
from manager.frontend import launchers
from manager.kit import data, deeplink, offload, panel, renderers, verbs
from manager.library import games
from manager.settings import settings
from manager.settings import settings as settings_page
from manager.workbench import all_settings, app_settings, rail
from manager.workbench import file as wb_file
from manager.workbench import launcher as wb_launcher
from tests.support.clicks import press
from tests.support.workbench_patch import patch_workbench


def _launcher(state: str, *, has_config: bool = True) -> dict:
    return {"app_name": "Visual Pinball X", "has_config": has_config,
            "fields": [{"key": "bin_path", "path": "exe"}],
            "checks": {"bin_path": {"state": state}}}


def _context(state: str, groups=("displays",), *, has_config: bool = True) -> dict:
    return {
        "launcher": _launcher(state, has_config=has_config),
        "config_groups": [SimpleNamespace(key=key, label=key.title(), settings=[1])
                          for key in groups],
    }


def _shown(context: dict) -> list[str]:
    return [s.key for s in rail.sections_for("launcher")
            if s.shown is None or s.shown(context)]


class RailTests(unittest.TestCase):
    def test_a_working_launcher_offers_the_groups_its_app_declares(self) -> None:
        shown = _shown(_context(path_state.OK))

        self.assertIn("launcher_displays", shown)
        self.assertIn("launcher_details", shown)
        self.assertIn("launcher_backups", shown)

    def test_every_setting_is_in_all_settings_after_the_areas(self) -> None:
        shown = _shown(_context(path_state.OK, groups=("displays", "plugins", "more")))

        self.assertEqual(shown, ["launcher_details", "launcher_displays", "launcher_plugins",
                                 "launcher_all", "launcher_backups"])

    def test_a_program_that_is_not_there_leaves_only_what_can_fix_it(self) -> None:
        shown = _shown(_context(path_state.MISSING))

        self.assertEqual(shown, ["launcher_details", "launcher_backups"])

    def test_a_group_the_app_does_not_declare_is_absent_rather_than_empty(self) -> None:
        """An install without the plugin architecture shows fewer sections, not empty
        ones - and that follows from what the app answered rather than a version test."""
        shown = _shown(_context(path_state.OK, groups=("displays",)))

        self.assertNotIn("launcher_plugins", shown)
        self.assertNotIn("launcher_sound", shown)

    def test_a_group_declared_with_nothing_in_it_is_also_absent(self) -> None:
        context = _context(path_state.OK)
        context["config_groups"] = [SimpleNamespace(key="displays", label="Displays",
                                                    settings=[])]

        self.assertNotIn("launcher_displays", _shown(context))

    def test_an_app_with_no_settings_of_its_own_offers_no_copies(self) -> None:
        shown = _shown(_context(path_state.OK, groups=(), has_config=False))

        self.assertEqual(shown, ["launcher_details"])


def _setting(key: str, label: str = "", *, default: str = "",
             description: str = "") -> SimpleNamespace:
    return SimpleNamespace(key=key, label=label or key.rsplit(".", 1)[-1],
                           default=default, description=description, help="")


def _group(key: str, *settings: SimpleNamespace, curated=(),
           summarized: bool = False) -> SimpleNamespace:
    return SimpleNamespace(key=key, label=key.title(), settings=list(settings),
                           curated=list(curated), summarized=summarized)


class AllSettingsTests(unittest.TestCase):
    GROUPS = [
        _group("displays", _setting("Player.PlayfieldFullScreen", "Display Mode"),
               _setting("Backglass.BackglassOutput", "Output Mode", default="0")),
        _group("plugins", _setting("Plugin.PinMAME.Enable", "Enable"),
               _setting("Plugin.PinMAME.PinMAMEPath", "PinMAME Path",
                        description="Where the ROMs live")),
        _group("more", _setting("Player.BallTrail", "Ball Trail", default="1"),
               _setting("DMD.Profile1Legacy", "Legacy")),
    ]

    def _found(self, values: dict | None = None, **wanted) -> list[tuple[str, list[str]]]:
        return [(section, [f.key for f in fields]) for section, fields in
                all_settings.found_settings(self.GROUPS, values or {}, wanted)]

    def test_everything_under_its_section_in_the_order_the_areas_reach_it(self) -> None:
        self.assertEqual(self._found(), [
            ("Player", ["Player.PlayfieldFullScreen", "Player.BallTrail"]),
            ("Backglass", ["Backglass.BackglassOutput"]),
            ("Plugin.PinMAME", ["Plugin.PinMAME.Enable", "Plugin.PinMAME.PinMAMEPath"]),
            ("DMD", ["DMD.Profile1Legacy"]),
        ])

    def test_the_key_typed_from_the_file_finds_its_row(self) -> None:
        self.assertEqual(self._found(query="fullscreen"),
                         [("Player", ["Player.PlayfieldFullScreen"])])

    def test_every_word_has_to_be_somewhere_in_label_key_or_description(self) -> None:
        self.assertEqual(self._found(query="pinmame roms"),
                         [("Plugin.PinMAME", ["Plugin.PinMAME.PinMAMEPath"])])
        self.assertEqual(self._found(query="pinmame trail"), [])

    def test_one_area(self) -> None:
        self.assertEqual(self._found(area="more"), [
            ("Player", ["Player.BallTrail"]), ("DMD", ["DMD.Profile1Legacy"])])

    def test_changed_is_a_value_set_here_that_differs_from_what_it_would_follow(self) -> None:
        values = {"Player.BallTrail": {"value": "1.0", "set_here": True},
                  "Backglass.BackglassOutput": {"value": "1", "set_here": False},
                  "Player.PlayfieldFullScreen": {"value": "", "set_here": False},
                  "DMD.Profile1Legacy": {"value": "2", "set_here": True}}

        self.assertEqual(self._found(values, differs=True),
                         [("DMD", ["DMD.Profile1Legacy"])])

    def test_changed_follows_the_scope_it_is_read_from(self) -> None:
        inherited = {"value": "1", "set_here": True, "in_effect": True,
                     "fallback": "0", "fallback_scope": "launcher"}
        values = {"Player.PlayfieldFullScreen": inherited,
                  "Backglass.BackglassOutput": {"value": "0", "set_here": True,
                                                "in_effect": True, "fallback": "0",
                                                "fallback_scope": "launcher"},
                  "DMD.Profile1Legacy": {"value": "", "set_here": True, "varies": True}}

        self.assertEqual(self._found(values, differs=True), [
            ("Player", ["Player.PlayfieldFullScreen"]), ("DMD", ["DMD.Profile1Legacy"])])

    def test_a_pair_is_found_whole_where_either_of_its_rows_is(self) -> None:
        video_mode = SimpleNamespace(key="video_mode", keys=("Topper.TopperFSWidth",
                                                             "Topper.TopperFSHeight"))
        groups = [_group("displays", _setting("Topper.TopperFullScreen", "Fullscreen"),
                         _setting("Topper.TopperFSWidth", "Width", default="1920"),
                         _setting("Topper.TopperFSHeight", "Height", default="1080"),
                         curated=[_heading("topper", "Topper.TopperFullScreen",
                                           *video_mode.keys, pairs=[video_mode])])]
        values = {"Topper.TopperFSHeight": {"value": "1200", "set_here": True}}
        pair = [("Topper", ["Topper.TopperFSWidth", "Topper.TopperFSHeight"])]

        for wanted in ({"query": "height"}, {"differs": True}):
            with self.subTest(**wanted):
                self.assertEqual([(section, [f.key for f in fields]) for section, fields in
                                  all_settings.found_settings(groups, values, wanted)], pair)
        self.assertEqual(all_settings.curated_pairs(groups), [video_mode])

    def test_changed_counts_a_pair_once_and_only_in_the_area_asked(self) -> None:
        video_mode = SimpleNamespace(key="video_mode", keys=("Topper.TopperFSWidth",
                                                             "Topper.TopperFSHeight"))
        groups = [_group("displays", _setting("Topper.TopperFSWidth", "Width", default="1920"),
                         _setting("Topper.TopperFSHeight", "Height", default="1080"),
                         curated=[_heading("topper", *video_mode.keys, pairs=[video_mode])]),
                  _group("more", _setting("Player.BallTrail", "Ball Trail", default="1"))]
        values = {key: {"value": "1", "set_here": True} for key in
                  ("Topper.TopperFSWidth", "Topper.TopperFSHeight", "Player.BallTrail")}
        values["Player.BallTrail"]["value"] = "0"

        self.assertEqual(all_settings.changed_count(groups, values), 2)
        self.assertEqual(all_settings.changed_count(groups, values, area="more"), 1)

    def test_a_section_reads_as_words(self) -> None:
        self.assertEqual(all_settings.section_title("ScoreView", {}), "Score View")
        self.assertEqual(all_settings.section_title("DMD", {}), "DMD")

    def test_a_plugin_takes_the_name_its_area_gives_it(self) -> None:
        names = all_settings._plugin_names([_group("plugins", curated=[
            SimpleNamespace(key="PUP", label="Pin Up Player",
                            keys=("Plugin.PUP.Enable",)),
            SimpleNamespace(key="playfield", label="Playfield", keys=("Player.PlaySound",)),
        ])])

        self.assertEqual(names, {"PUP": "Pin Up Player"})
        self.assertEqual(all_settings.section_title("Plugin.PUP", names),
                         t("manager.workbench.plugin_section", name="Pin Up Player"))
        self.assertEqual(all_settings.section_title("Plugin.HelloWorld", names),
                         t("manager.workbench.plugin_section", name="Hello World"))


def _heading(key: str, *keys: str, enabled_by: str = "", rivals: tuple[str, ...] = (),
             label: str = "", pairs=(), switched=()) -> SimpleNamespace:
    return SimpleNamespace(key=key, label=label or key.title(), note="", keys=keys,
                           enabled_by=enabled_by, rivals=rivals, pairs=tuple(pairs),
                           switched=tuple(SimpleNamespace(enabled_by=switch, keys=rows,
                                                          on=(*on, True)[0])
                                          for switch, rows, *on in switched))


class ConflictTests(unittest.TestCase):
    RENDERERS = [_group(
        "plugins",
        _setting("Plugin.B2S.Enable", "Enable", default="1"),
        _setting("Plugin.B2SLegacy.Enable", "Enable", default="0"),
        _setting("Plugin.DOF.Enable", "Enable", default="1"),
        curated=[_heading("B2S", "Plugin.B2S.Enable", enabled_by="Plugin.B2S.Enable",
                          rivals=("Plugin.B2SLegacy.Enable",)),
                 _heading("B2SLegacy", "Plugin.B2SLegacy.Enable", label="B2S Legacy",
                          enabled_by="Plugin.B2SLegacy.Enable",
                          rivals=("Plugin.B2S.Enable",)),
                 _heading("DOF", "Plugin.DOF.Enable", enabled_by="Plugin.DOF.Enable")])]

    def test_two_rivals_on_each_name_the_other(self) -> None:
        both = all_settings.conflicts(self.RENDERERS, {"Plugin.B2SLegacy.Enable": {"value": "1"}})

        self.assertEqual(both, {"Plugin.B2S.Enable": "B2S Legacy",
                                "Plugin.B2SLegacy.Enable": "B2S"})

    def test_a_rival_off_is_no_conflict(self) -> None:
        self.assertEqual(all_settings.conflicts(self.RENDERERS, {}), {})
        self.assertEqual(all_settings.conflicts(self.RENDERERS, {
            "Plugin.B2S.Enable": {"value": "0"},
            "Plugin.B2SLegacy.Enable": {"value": "1"}}), {})

    def test_either_switch_redraws_the_other(self) -> None:
        self.assertEqual(all_settings.rival_switches(self.RENDERERS),
                         {"Plugin.B2S.Enable", "Plugin.B2SLegacy.Enable"})

    def test_rivals_travel_with_their_heading(self) -> None:
        groups = data.config_groups({"groups": [{
            "key": "plugins", "label": "Plugins", "settings": [], "curated": [
                {"key": "B2S", "label": "B2S", "keys": ["Plugin.B2S.Enable"],
                 "enabled_by": "Plugin.B2S.Enable", "rivals": ["Plugin.B2SLegacy.Enable"]},
                {"key": "DOF", "label": "DOF", "keys": ["Plugin.DOF.Enable"]}]}]})

        self.assertEqual([h.rivals for h in groups[0].curated],
                         [("Plugin.B2SLegacy.Enable",), ()])


class SettingLabelTests(unittest.TestCase):
    def test_the_program_s_label_is_title_cased_and_a_catalog_s_is_left_as_written(self) -> None:
        def setting(key: str, label: str, label_key: str = "") -> dict[str, Any]:
            return {"key": key, "label": label, "label_key": label_key, "type": "bool",
                    "default": "0", "description": ""}

        groups = data.config_groups({"groups": [{
            "key": "graphics", "label": "Graphics", "settings": [
                setting("Player.FXAA", "Post processed antialiasing"),
                setting("Plugin.DMDUtil.PIN2DMD", "PIN2DMD",
                        "app.vpx.field.Plugin.DMDUtil.PIN2DMD.label")]}]})

        self.assertEqual([one.label for one in groups[0].settings],
                         ["Post Processed Antialiasing", "PIN2DMD"])


class AFolderSettingReachesTheControl(unittest.TestCase):
    """The path a plugin setting names, from the field the app declares to the option the
    control reads, with nothing in between built by hand."""

    def test_a_declared_folder_comes_out_as_an_option_that_browses(self) -> None:
        from apps.vpx import areas
        from common.apps.contract import Field
        from common.games import launcher_ops

        key = next(iter(sorted(areas.FOLDERS)))
        described = launcher_ops._described_field(
            "vpx", Field(key=key, path="dir"))
        groups = data.config_groups({"groups": [{
            "key": "plugins", "label": "Plugins", "settings": [described]}]})

        option = all_settings._as_option(groups[0].settings[0])

        self.assertEqual(option["path"], "dir")


class CuratedAreaTests(unittest.TestCase):
    PLUGINS = _group(
        "plugins",
        _setting("Plugin.PinMAME.Enable", "Enable", default="0"),
        _setting("Plugin.PinMAME.PinMAMEPath", "PinMAME Path"),
        _setting("Plugin.PinMAME.Cheat", "Cheat"),
        _setting("Plugin.DOF.Enable", "Enable", default="1"),
        curated=[_heading("PinMAME", "Plugin.PinMAME.Enable", "Plugin.PinMAME.PinMAMEPath",
                          enabled_by="Plugin.PinMAME.Enable"),
                 _heading("DOF", "Plugin.DOF.Enable", enabled_by="Plugin.DOF.Enable"),
                 _heading("Serum", "Plugin.Serum.Enable", enabled_by="Plugin.Serum.Enable")])

    def _drawn(self, values: dict) -> list[tuple[str, list[str]]]:
        return [(heading.key, [f.key for f in fields])
                for heading, fields in all_settings.curated_blocks(self.PLUGINS, values)]

    def test_a_switch_that_is_off_draws_alone(self) -> None:
        self.assertEqual(self._drawn({}), [("PinMAME", ["Plugin.PinMAME.Enable"]),
                                           ("DOF", ["Plugin.DOF.Enable"])])

    def test_switched_on_its_rows_follow_it_in_the_heading_s_order(self) -> None:
        drawn = self._drawn({"Plugin.PinMAME.Enable": {"value": "1"}})

        self.assertEqual(drawn[0], ("PinMAME", ["Plugin.PinMAME.Enable",
                                                "Plugin.PinMAME.PinMAMEPath"]))

    def test_a_value_turned_off_over_a_default_of_on_hides_the_rest(self) -> None:
        group = _group("plugins", _setting("Plugin.B2S.Enable", default="1"),
                       _setting("Plugin.B2S.ShowGrill"),
                       curated=[_heading("B2S", "Plugin.B2S.Enable", "Plugin.B2S.ShowGrill",
                                         enabled_by="Plugin.B2S.Enable")])

        on = all_settings.curated_blocks(group, {})
        off = all_settings.curated_blocks(group, {"Plugin.B2S.Enable": {"value": "0"}})

        self.assertEqual([f.key for f in on[0][1]], ["Plugin.B2S.Enable",
                                                     "Plugin.B2S.ShowGrill"])
        self.assertEqual([f.key for f in off[0][1]], ["Plugin.B2S.Enable"])

    OVERLAY = _group(
        "plugins", _setting("Plugin.B2S.Enable", default="1"),
        _setting("Plugin.B2S.BackglassDMDOverlay", default="0"),
        _setting("Plugin.B2S.BackglassDMDX"), _setting("Plugin.B2S.ShowGrill"),
        curated=[_heading("B2S", "Plugin.B2S.Enable", "Plugin.B2S.BackglassDMDOverlay",
                          "Plugin.B2S.BackglassDMDX", "Plugin.B2S.ShowGrill",
                          enabled_by="Plugin.B2S.Enable",
                          switched=[("Plugin.B2S.BackglassDMDOverlay",
                                     ("Plugin.B2S.BackglassDMDX",))])])

    def _overlay(self, **values: dict) -> list[str]:
        held = {f"Plugin.B2S.{key}": value for key, value in values.items()}
        return [f.key.rsplit(".", 1)[-1]
                for _, fields in all_settings.curated_blocks(self.OVERLAY, held)
                for f in fields]

    def test_a_row_its_own_switch_gates_is_drawn_only_while_that_is_on(self) -> None:
        self.assertEqual(self._overlay(), ["Enable", "BackglassDMDOverlay", "ShowGrill"])
        self.assertEqual(self._overlay(BackglassDMDOverlay={"value": "1"}),
                         ["Enable", "BackglassDMDOverlay", "BackglassDMDX", "ShowGrill"])

    def test_or_while_it_varies_across_tables(self) -> None:
        self.assertIn("BackglassDMDX", self._overlay(
            BackglassDMDOverlay={"value": "", "varies": True}))

    def test_the_heading_s_switch_off_hides_a_row_switch_and_its_rows(self) -> None:
        self.assertEqual(self._overlay(Enable={"value": "0"},
                                       BackglassDMDOverlay={"value": "1"}), ["Enable"])

    def test_writing_a_row_switch_redraws_the_panel(self) -> None:
        self.assertEqual(all_settings.switches(self.OVERLAY.curated),
                         {"Plugin.B2S.Enable", "Plugin.B2S.BackglassDMDOverlay"})

    WINDOW = _group(
        "displays", _setting("Topper.TopperFullScreen", default="1"),
        _setting("Topper.TopperFSWidth"), _setting("Topper.TopperWndX"),
        curated=[_heading("topper", "Topper.TopperFullScreen", "Topper.TopperFSWidth",
                          "Topper.TopperWndX",
                          switched=[("Topper.TopperFullScreen", ("Topper.TopperFSWidth",)),
                                    ("Topper.TopperFullScreen", ("Topper.TopperWndX",),
                                     False)])])

    def _window(self, fullscreen: dict) -> list[str]:
        return [f.key.rsplit(".", 1)[-1] for _, fields in all_settings.curated_blocks(
            self.WINDOW, {"Topper.TopperFullScreen": fullscreen}) for f in fields]

    def test_a_row_switched_the_other_way_is_drawn_only_while_its_switch_is_off(self) -> None:
        self.assertEqual(self._window({}), ["TopperFullScreen", "TopperFSWidth"])
        self.assertEqual(self._window({"value": "0"}), ["TopperFullScreen", "TopperWndX"])

    def test_and_both_ways_while_the_switch_varies_across_tables(self) -> None:
        self.assertEqual(self._window({"value": "", "varies": True}),
                         ["TopperFullScreen", "TopperFSWidth", "TopperWndX"])

    def test_row_switches_travel_with_their_heading(self) -> None:
        groups = data.config_groups({"groups": [{
            "key": "plugins", "label": "Plugins", "settings": [], "curated": [
                {"key": "B2S", "label": "B2S", "keys": ["Plugin.B2S.BackglassDMDOverlay",
                                                        "Plugin.B2S.BackglassDMDAutoPos",
                                                        "Plugin.B2S.BackglassDMDX"],
                 "switched": [{"enabled_by": "Plugin.B2S.BackglassDMDOverlay",
                               "keys": ["Plugin.B2S.BackglassDMDX"], "on": True},
                              {"enabled_by": "Plugin.B2S.BackglassDMDAutoPos",
                               "keys": ["Plugin.B2S.BackglassDMDX"], "on": False}]}]}]})

        self.assertEqual([(one.enabled_by, one.keys, one.on)
                          for one in groups[0].curated[0].switched],
                         [("Plugin.B2S.BackglassDMDOverlay", ("Plugin.B2S.BackglassDMDX",),
                           True),
                          ("Plugin.B2S.BackglassDMDAutoPos", ("Plugin.B2S.BackglassDMDX",),
                           False)])

    def test_a_heading_whose_settings_the_file_does_not_have_is_left_out(self) -> None:
        self.assertNotIn("Serum", [key for key, _ in self._drawn({})])

    def test_a_heading_vpinfe_says_nothing_about_has_the_program_s_line(self) -> None:
        groups = data.config_groups({"groups": [{
            "key": "plugins", "label": "Plugins", "settings": [], "curated": [
                {"key": "WMP", "label": "WMP", "note": "", "description": "WMP audio",
                 "keys": ["Plugin.WMP.Enable"]},
                {"key": "DOF", "label": "DOF", "note": "Drives toys",
                 "description": "Direct Output Framework", "keys": ["Plugin.DOF.Enable"]}]}]})

        self.assertEqual([h.note for h in groups[0].curated], ["WMP audio", "Drives toys"])

    def test_a_row_a_switch_hides_is_still_curated_and_not_one_of_the_rest(self) -> None:
        self.assertEqual(all_settings.curated_keys(self.PLUGINS),
                         {"Plugin.PinMAME.Enable", "Plugin.PinMAME.PinMAMEPath",
                          "Plugin.DOF.Enable"})


class ProgramNoteTests(unittest.TestCase):
    def test_no_program_set_says_to_set_one(self) -> None:
        self.assertEqual(all_settings._program_note(_launcher(path_state.UNSET)),
                         t("manager.workbench.program_hint",
                           app="Visual Pinball X"))

    def test_a_path_that_finds_nothing_says_so(self) -> None:
        self.assertEqual(all_settings._program_note(_launcher(path_state.MISSING)),
                         t("manager.workbench.app_missing", app="Visual Pinball X"))

    def test_a_program_that_is_there_needs_no_note(self) -> None:
        self.assertEqual(all_settings._program_note(_launcher(path_state.OK)), "")

    def test_a_program_that_has_not_written_its_file_says_so_only_on_its_own_page(self) -> None:
        found = {**_launcher(path_state.OK), "has_settings_file": False}

        self.assertEqual(all_settings.no_file_note(found),
                         t("manager.workbench.file_not_written", app="Visual Pinball X"))
        self.assertEqual(all_settings._program_note(found), "",
                         "a table's settings read it as a reason to draw nothing")

    def test_a_program_that_wrote_no_file_says_so_in_place_of_the_run_it_again_line(self) -> None:
        found = {**_launcher(path_state.OK), "has_settings_file": False,
                 "settings_file_not_made": True}

        self.assertEqual(all_settings.no_file_note(found),
                         t("manager.workbench.file_not_made", app="Visual Pinball X"))

    def test_a_program_with_its_file_needs_no_note(self) -> None:
        found = {**_launcher(path_state.OK), "has_settings_file": True}

        self.assertEqual(all_settings.no_file_note(found), "")

    def test_a_program_that_is_not_there_is_not_told_to_run(self) -> None:
        found = {**_launcher(path_state.MISSING), "has_settings_file": False}

        self.assertEqual(all_settings.no_file_note(found), "")

    def test_an_app_with_no_settings_of_its_own_gets_none(self) -> None:
        for state in (path_state.UNSET, path_state.MISSING):
            with self.subTest(state=state):
                self.assertEqual(
                    all_settings._program_note(_launcher(state, has_config=False)), "")


class CopyVerbTests(unittest.TestCase):
    """Back up now and Restore... in a launcher's menus."""

    def _acts(self, state: str, *, has_config: bool = True, playing: bool = False,
              after: Any = True) -> list:
        async def done() -> None:
            return None

        return launchers.acts(
            Mock(), {}, [_launcher(state, has_config=has_config) | {"launcher_id": "l-1"}], 2,
            lambda: None, after=done if after else None, playing=playing)

    def test_a_launcher_with_a_program_and_a_settings_file_offers_both(self) -> None:
        labels = [one.label for one in self._acts(path_state.OK)]
        self.assertEqual(labels[1:3], [t("manager.backups.back_up_title"),
                                       t("manager.backups.restore_dialog")])

    def test_no_settings_file_or_no_program_offers_neither(self) -> None:
        for state, has_config in ((path_state.MISSING, True), (path_state.UNSET, True),
                                  (path_state.OK, False)):
            with self.subTest(state=state, has_config=has_config):
                labels = [one.label for one in self._acts(state, has_config=has_config)]
                self.assertNotIn(t("manager.backups.restore_dialog"), labels)

    def test_a_menu_with_nothing_to_redraw_offers_neither(self) -> None:
        labels = [one.label for one in self._acts(path_state.OK, after=False)]
        self.assertNotIn(t("manager.backups.restore_dialog"), labels)

    def test_a_table_playing_refuses_both_and_says_why(self) -> None:
        copy, restore = self._acts(path_state.OK, playing=True)[1:3]
        self.assertEqual((copy.run, restore.run), (None, None))
        self.assertEqual((copy.hint, restore.hint),
                         (t(all_settings.PLAYING_NOTE), t(all_settings.PLAYING_NOTE)))

    def test_the_dialog_is_named_for_the_launcher_as_people_know_it(self) -> None:
        library = Mock()
        library.play_state.return_value = {}
        made = launchers.copy_verbs(
            library, [{"launcher_id": "l-1", "display_name": "Main VPX"}], AsyncMock())
        with patch.object(launchers.backup_rows, "open_for", new=AsyncMock()) as opened:
            assert made[1].run is not None
            asyncio.run(made[1].run())
        self.assertEqual(opened.call_args.args[3], "Main VPX")

    def test_a_click_while_a_table_plays_changes_nothing(self) -> None:
        library = Mock()
        library.play_state.return_value = {"launching": True}
        made = launchers.copy_verbs(library, [{"launcher_id": "l-1"}], AsyncMock())
        with patch.object(launchers.ui, "notify") as notify:
            for verb in made:
                assert verb.run is not None
                asyncio.run(verb.run())
        library.take_backup.assert_not_called()
        library.backups.assert_not_called()
        self.assertEqual(notify.call_count, 2)


class PlayingTests(unittest.TestCase):
    def test_not_knowing_is_not_a_reason_to_refuse_to_draw(self) -> None:
        class Broken:
            def play_state(self):
                raise RuntimeError("no answer")

        self.assertFalse(all_settings._playing(cast(Any, Broken())))

    def test_a_table_playing_is_reported(self) -> None:
        class Playing:
            def play_state(self):
                return {"launching": True}

        self.assertTrue(all_settings._playing(cast(Any, Playing())))

    def test_the_reason_says_who_the_other_writer_is(self) -> None:
        """The program rewrites this file itself when a table exits, so an edit made now
        is one of two writers and the last one wins."""
        self.assertIn("rewrites this file", t(all_settings.PLAYING_WHY))


class BlankValueTests(unittest.TestCase):
    def _placeholder(self, option: dict) -> str:
        with patch.object(settings.panel, "number") as number:
            settings.control_for(option, settings.value_for(option, ""), lambda _v: True)
        return number.call_args.kwargs.get("placeholder", "")

    def test_a_blank_its_app_works_out_says_so_in_the_control(self) -> None:
        field = SimpleNamespace(key="Player.PlayfieldWidth", type="int", label="Width",
                                default="", choices=(), blank="Auto")

        self.assertEqual(self._placeholder(all_settings._as_option(field)), "Auto")

    def test_a_choice_s_blank_is_an_option_named_by_it(self) -> None:
        field = SimpleNamespace(key="TableOverride.ViewCabMode", type="int",
                                label="View mode", default="", blank="The table's own",
                                choices=(("0", "Legacy"), ("2", "Window")),
                                choice_help={"2": "The screen as a window"})

        option = all_settings._as_option(field)

        self.assertEqual(option["choices"],
                         {"": "The table's own", "0": "Legacy", "2": "Window"})
        self.assertEqual(option["describes"], {"Window": "The screen as a window"})

    def test_a_path_field_keeps_what_it_names_so_the_control_can_browse(self) -> None:
        field = SimpleNamespace(key="Plugin.PinMAME.PinMAMEPath", type="string",
                                label="PinMAME Path", default="", choices=(), path="dir")
        file = SimpleNamespace(key="ini_path", type="string", label="Settings File",
                               default="", choices=(), path="file", suffixes=(".ini",))
        plain = SimpleNamespace(key="Player.ShowFPS", type="string", label="FPS",
                                default="", choices=())

        self.assertEqual(all_settings._as_option(field)["path"], "dir")
        self.assertEqual(all_settings._as_option(file)["suffixes"], (".ini",))
        self.assertNotIn("path", all_settings._as_option(plain))

    def test_a_field_as_the_wire_sends_it_says_so_too(self) -> None:
        field = {"key": "Player.PlayfieldWidth", "type": "int", "label": "Width",
                 "default": "", "blank": "Auto"}

        self.assertEqual(self._placeholder(dict(field)), "Auto")


class VariesControlTests(unittest.TestCase):
    """A control standing for several values that differ holds none of them."""

    def test_a_switch_is_neither_on_nor_off(self) -> None:
        with patch.object(settings.panel, "switch") as switch:
            settings.control_for({"key": "k", "type": "bool", "default": "1"}, True,
                                 lambda _v: True, varies=True)

        self.assertIsNone(switch.call_args.args[0])

    def test_a_number_is_blank_without_the_words_for_a_blank(self) -> None:
        with patch.object(settings.panel, "number") as number:
            settings.control_for({"key": "k", "type": "int", "blank": "Auto"},
                                 120, lambda _v: True, varies=True)

        self.assertEqual((number.call_args.args[0], number.call_args.kwargs["placeholder"]),
                         (None, ""))


class PathControlTests(unittest.TestCase):
    OPTION = {"key": "ini_path", "type": "str", "path": "file",
              "blank": "Not found", "left_empty": "Made the first time it runs"}

    def test_an_empty_path_shows_the_word_and_hands_the_remedy_to_the_tooltip(self) -> None:
        with patch.object(settings.panel, "field") as field:
            settings.control_for(self.OPTION, "", lambda _v: True)

        self.assertEqual((field.call_args.kwargs["placeholder"],
                          field.call_args.kwargs["left_empty"]),
                         ("Not found", "Made the first time it runs"))


class ColorControlTests(unittest.TestCase):
    OPTION = {"key": "Alpha.Profile4Color", "type": "color", "label": "Color",
              "default": "#FF2315"}

    def test_a_color_is_a_swatch_of_itself_that_saves_what_is_picked(self) -> None:
        save = Mock()
        with patch.object(settings.panel, "swatch") as swatch:
            settings.control_for(self.OPTION, "#E34236", save)

        self.assertEqual(swatch.call_args.args, ("#E34236", save))
        self.assertFalse(swatch.call_args.kwargs["disabled"])

    def test_a_color_that_cannot_be_written_here_cannot_be_picked(self) -> None:
        with patch.object(settings.panel, "swatch") as swatch:
            settings.control_for(self.OPTION, "#E34236", lambda _v: True, writable=False)

        self.assertTrue(swatch.call_args.kwargs["disabled"])


class NamedNumberTests(unittest.TestCase):
    FIELD = SimpleNamespace(key="Player.MaxFramerate", type="number", label="Limit Framerate",
                            default="-1.0", choices=(),
                            named=(("-1", "Match the Display"), ("0", "No Limit")))

    def test_a_number_with_named_values_picks_among_them(self) -> None:
        save = Mock()
        option = all_settings._as_option(self.FIELD)
        with patch.object(settings.panel, "named_number") as named:
            settings.control_for(option, settings.value_for(option, ""), save)

        self.assertEqual(named.call_args.args,
                         (-1.0, {"-1": "Match the Display", "0": "No Limit"}, save))
        self.assertFalse(named.call_args.kwargs["whole"])

    def test_a_stored_value_is_named_by_its_number_not_its_spelling(self) -> None:
        named = dict(self.FIELD.named)

        self.assertEqual([panel.named_as(one, named) for one in (-1.0, "-1", "0.0", 0)],
                         ["-1", "-1", "0", "0"])

    def test_any_other_value_is_custom(self) -> None:
        named = dict(self.FIELD.named)

        self.assertEqual([panel.named_as(one, named) for one in (60.0, "", None, "x")],
                         ["", "", "", ""])


class ReportedNameTests(unittest.IsolatedAsyncioTestCase):
    """A display is matched by its exact name, and a wrong one fails without a word."""

    USED = ("External Display", "Built-in Display")
    FIELD = SimpleNamespace(key="Player.PlayfieldDisplay", type="text", label="Display",
                            default="", choices=(), blank="", reported=USED,
                            scopes=("launcher", "entry"), help="", description="")

    def test_it_is_picked_from_the_names_vpx_used_and_can_still_be_typed(self) -> None:
        option = all_settings._as_option(self.FIELD)
        with patch.object(settings.panel, "combo") as combo:
            settings.control_for(option, "Built-in Display", Mock(),
                                 suggestions={all_settings.REPORTED: dict(zip(self.USED, self.USED,
                                                                           strict=True))})

        self.assertEqual(combo.call_args.args[:2],
                         ("Built-in Display", {"External Display": "External Display",
                                               "Built-in Display": "Built-in Display"}))
        self.assertFalse(combo.call_args.kwargs["clearable"])

    def test_a_name_vpx_has_not_used_is_marked(self) -> None:
        mark = all_settings._unreported("Old Display", self.USED, "Visual Pinball X")

        self.assertEqual(mark, {"state": "missing", "reason": t(
            "manager.workbench.display_not_used", app="Visual Pinball X")})

    def test_nothing_is_marked_where_it_is_one_or_vpx_has_said_nothing(self) -> None:
        self.assertEqual([all_settings._unreported(value, used, "Visual Pinball X")
                          for value, used in (("Built-in Display", self.USED),
                                              ("", self.USED), ("Old Display", ()))],
                         [None, None, None])

    async def test_picking_one_does_not_draw_the_page_again(self) -> None:
        before = {self.FIELD.key: {"value": "Old Display", "scope": "launcher"}}
        after = {self.FIELD.key: {"value": "Built-in Display", "scope": "launcher"}}
        rebuild = AsyncMock()
        context = {"library": Mock(), "launcher": {"launcher_id": "probe"},
                   "config_scope": "launcher", "rebuild": rebuild}
        self.enterContext(patch_workbench("ui"))
        self.enterContext(patch_workbench("_config_values",
                                       new=AsyncMock(side_effect=[before, after])))
        self.enterContext(patch_workbench("_set_by_tables",
                                       new=AsyncMock(return_value={})))
        self.enterContext(patch.object(run, "io_bound",
                                       new=AsyncMock(return_value={})))
        control_for = self.enterContext(patch.object(settings_page, "control_for"))
        await all_settings._setting_entries(context, [("", "", [self.FIELD])])

        self.assertEqual(control_for.call_args.kwargs["check"]["state"], "missing")
        await press(control_for.call_args.args[2], "Built-in Display")
        await asyncio.sleep(0)

        rebuild.assert_not_awaited()


class ClearTests(unittest.IsolatedAsyncioTestCase):
    """Clear is on the value's own line, and only where this scope sets the value."""

    FIELD = SimpleNamespace(key="Player.BGSet", type="text", label="View Mode", default="",
                            description="", choices=(("0", "Desktop"), ("2", "Floating")),
                            scopes=("launcher", "entry"), help="")
    SET = {"set_here": True, "in_effect": True, "scope": "entry", "value": "0",
           "fallback_scope": "launcher", "fallback": "2"}

    def _drawn(self, held: dict, playing: bool = False) -> tuple[Mock, Mock]:
        wipe = AsyncMock()
        with patch_workbench("ui"), \
                patch.object(panel, "icon_action") as icon_action, \
                patch.object(panel, "action") as action:
            all_settings._marked([(Mock(), dict(held), self.FIELD)], "Visual Pinball X",
                              clear=wipe, playing=playing)()
            all_settings._beside(lambda: None, dict(held), self.FIELD)()
        self.wipe = wipe
        return icon_action, action

    def test_it_is_drawn_on_the_value_s_line_and_not_under_it(self) -> None:
        icon_action, action = self._drawn(self.SET)

        self.assertEqual(icon_action.call_args.args, (t("word.reset"), self.wipe))
        self.assertEqual(icon_action.call_args.kwargs["icon"], verbs.RESET)
        action.assert_not_called()

    def test_its_hover_says_what_it_goes_back_to(self) -> None:
        icon_action, _ = self._drawn(self.SET)

        self.assertEqual(icon_action.call_args.kwargs["hint"], "Back to Floating - All Tables")
        self.assertTrue(icon_action.call_args.kwargs["enabled"])

    def test_while_a_table_is_playing_it_is_off_and_says_why(self) -> None:
        icon_action, _ = self._drawn(self.SET, playing=True)

        self.assertFalse(icon_action.call_args.kwargs["enabled"])
        self.assertEqual(icon_action.call_args.kwargs["hint"],
                         t(all_settings.PLAYING_NOTE))

    def test_a_value_this_scope_does_not_set_has_none(self) -> None:
        icon_action, action = self._drawn({**self.SET, "set_here": False})

        icon_action.assert_not_called()
        action.assert_not_called()

    def test_a_value_set_to_what_it_would_be_anyway_has_none(self) -> None:
        """Nothing was changed, so there is nothing to put back."""
        icon_action, _ = self._drawn({**self.SET, "value": "2"})

        icon_action.assert_not_called()

    async def test_each_row_s_clear_empties_its_own_key(self) -> None:
        library = Mock()
        context = {"library": library, "launcher": {"launcher_id": "probe"},
                   "config_scope": "entry", "config_table": "t1", "rebuild": AsyncMock()}
        self.enterContext(patch_workbench("ui"))
        self.enterContext(patch_workbench("_config_values",
                                       new=AsyncMock(return_value={"Player.BGSet": self.SET})))
        self.enterContext(patch.object(settings_page, "control_for"))
        self.enterContext(patch.object(run, "io_bound", new=AsyncMock()))
        marked = self.enterContext(patch_workbench("_marked"))
        await all_settings._setting_entries(context, [("", "", [self.FIELD])])

        await press(marked.call_args.kwargs["clear"])

        cast(Any, run.io_bound).assert_awaited_once_with(
            library.write_launcher_config, "probe", {"Player.BGSet": ""}, table="t1",
            scope="entry")


class ScopeTests(unittest.IsolatedAsyncioTestCase):
    """A value held at a scope that does not offer its setting is read-only there, keeps
    Clear, and says why."""

    CAMERA = SimpleNamespace(key="TableOverride.ViewDTMode", type="text", label="View Mode",
                             default="", description="", choices=(), help="",
                             scopes=("folder", "entry"))
    INPUT = SimpleNamespace(**{**vars(CAMERA), "key": "Player.PlayfieldWidth",
                               "label": "Width", "scopes": ("launcher",)})
    HELD = {"set_here": True, "in_effect": True, "scope": "launcher", "value": "1"}

    async def _writable(self, field: SimpleNamespace, scope: str, table: str) -> bool:
        context = {"library": Mock(), "launcher": {"launcher_id": "probe"},
                   "config_scope": scope, "config_table": table, "rebuild": AsyncMock()}
        self.enterContext(patch_workbench("ui"))
        self.enterContext(patch_workbench("_config_values",
                                       new=AsyncMock(return_value={field.key: self.HELD})))
        self.enterContext(patch_workbench("_set_by_tables",
                                       new=AsyncMock(return_value=[])))
        control_for = self.enterContext(patch.object(settings_page, "control_for"))
        marked = self.enterContext(patch_workbench("_marked"))
        await all_settings._setting_entries(context, [("", "", [field])])
        self.assertIsNotNone(marked.call_args.kwargs["clear"])
        return control_for.call_args.kwargs["writable"]

    async def test_a_table_only_setting_is_read_only_at_the_launcher(self) -> None:
        self.assertFalse(await self._writable(self.CAMERA, "launcher", ""))

    async def test_and_one_for_all_tables_only_is_read_only_at_a_table(self) -> None:
        self.assertFalse(await self._writable(self.INPUT, "entry", "t1"))

    async def test_each_is_editable_where_it_is_offered(self) -> None:
        self.assertTrue(await self._writable(self.CAMERA, "entry", "t1"))

    def test_at_the_launcher_it_is_marked_per_table(self) -> None:
        with patch.object(panel, "state") as state:
            all_settings._mark_for(self.HELD, "launcher", self.CAMERA, offered=False)

        state.assert_called_once_with(t("manager.app_settings.per_table"), "off",
                                      hint=t("manager.app_settings.per_table.help"))

    def test_at_a_table_one_the_program_does_not_read_is_ignored(self) -> None:
        with patch.object(panel, "state") as state:
            all_settings._mark_for({**self.HELD, "scope": "launcher", "in_effect": False},
                                "entry", self.INPUT, offered=False)

        state.assert_called_once_with(t("manager.app_settings.ignored"), "warn",
                                      hint=t("manager.app_settings.all_tables_only"))


class PairTests(unittest.IsolatedAsyncioTestCase):
    """Two numbers of one kind are one row: one dot and one Clear for both, and a mark
    for each."""

    APP = "Visual Pinball X"
    WIDTH = SimpleNamespace(key="Player.PlayfieldWidth", type="int", label="Width", default="",
                            description="", choices=(), scopes=("launcher",), help="")
    HEIGHT = SimpleNamespace(**{**vars(WIDTH), "key": "Player.PlayfieldHeight",
                                "label": "Height"})
    SIZE = SimpleNamespace(key="size", label="Size", note="", joiner="×",
                           keys=("Player.PlayfieldWidth", "Player.PlayfieldHeight"))
    SET = {"set_here": True, "in_effect": True, "scope": "launcher", "value": "960"}
    UNSET = {"set_here": False, "in_effect": True, "scope": "", "value": ""}

    async def _entries(self, values: dict, fields=None, size=None,
                       ahead=None) -> tuple[list, Mock, Mock]:
        library = Mock()
        context = {"library": library, "launcher": {"launcher_id": "probe", "app_name": self.APP},
                   "config_scope": "launcher", "config_table": "", "rebuild": AsyncMock()}
        self.enterContext(patch_workbench("ui"))
        self.enterContext(patch_workbench("_config_values",
                                       new=AsyncMock(return_value=values)))
        self.enterContext(patch_workbench("_set_by_tables",
                                       new=AsyncMock(return_value=[])))
        self.enterContext(patch.object(settings_page, "control_for"))
        self.enterContext(patch.object(run, "io_bound", new=AsyncMock()))
        marked = self.enterContext(patch_workbench("_marked"))
        entries = await all_settings._setting_entries(
            context, [("Playfield", "", fields or [self.WIDTH, self.HEIGHT])],
            pairs=[size or self.SIZE], ahead=ahead)
        return entries, marked, library

    def _drawn(self, width: dict, height: dict) -> tuple[Mock, Mock]:
        with patch_workbench("ui") as ui, \
                patch.object(panel, "icon_action") as icon_action:
            all_settings._marked([(Mock(), dict(width), self.WIDTH),
                               (Mock(), dict(height), self.HEIGHT)],
                              self.APP, joiner="×", clear=AsyncMock())()
        return ui, icon_action

    @property
    def _changed(self) -> str:
        return t("manager.workbench.changed_link",
                 whose=t("manager.workbench.app_default", app=self.APP))

    def _part(self, field: SimpleNamespace, said: str) -> str:
        return t("manager.workbench.part_said", label=field.label, said=said)

    async def test_its_two_rows_are_one_under_the_pair_s_label(self) -> None:
        entries, marked, _ = await self._entries({})

        self.assertEqual([label for label, _ in entries if isinstance(label, str)], ["Size"])
        self.assertEqual([field for _, _, field in marked.call_args.args[0]],
                         [self.WIDTH, self.HEIGHT])
        self.assertEqual(marked.call_args.kwargs["joiner"], "×")

    async def test_entries_given_for_a_setting_are_drawn_above_its_row(self) -> None:
        extra = [("Screen Size", lambda: None)]

        entries, _, _ = await self._entries({}, fields=[self.WIDTH], ahead={self.WIDTH.key: extra})

        labels = [label for label, _ in entries if isinstance(label, str)]
        self.assertEqual(labels, ["Screen Size", "Width"])

    async def test_entries_given_for_a_setting_not_drawn_are_not_drawn(self) -> None:
        extra = [("Screen Size", lambda: None)]

        entries, _, _ = await self._entries({}, fields=[self.WIDTH], ahead={"Elsewhere": extra})

        self.assertEqual([label for label, _ in entries if isinstance(label, str)], ["Width"])

    async def test_the_pair_s_note_is_the_one_under_it(self) -> None:
        note = self.enterContext(patch.object(panel, "note"))
        await self._entries({}, size=SimpleNamespace(**{**vars(self.SIZE), "note": "Wide"}))

        note.assert_called_once_with("Wide")

    async def test_a_block_holding_one_of_the_two_draws_it_alone(self) -> None:
        entries, marked, _ = await self._entries({}, fields=[self.WIDTH])

        self.assertEqual([label for label, _ in entries if isinstance(label, str)], ["Width"])
        self.assertEqual(len(marked.call_args.args[0]), 1)

    async def test_clear_empties_only_the_numbers_set_here(self) -> None:
        for height, written in ((self.UNSET, {"Player.PlayfieldWidth": ""}),
                                (self.SET, {"Player.PlayfieldWidth": "",
                                            "Player.PlayfieldHeight": ""})):
            with self.subTest(height=height["set_here"]):
                _, marked, library = await self._entries(
                    {"Player.PlayfieldWidth": self.SET, "Player.PlayfieldHeight": height})

                await press(marked.call_args.kwargs["clear"])

                cast(Any, run.io_bound).assert_awaited_once_with(
                    library.write_launcher_config, "probe", written, table="",
                    scope="launcher")

    def test_the_dot_shows_where_either_is_set_here(self) -> None:
        for width, height, shown in ((self.SET, self.UNSET, True),
                                     (self.UNSET, self.SET, True),
                                     (self.UNSET, self.UNSET, False)):
            with self.subTest(width=width["set_here"], height=height["set_here"]):
                ui, _ = self._drawn(width, height)
                dot = ui.element.return_value.classes.return_value

                self.assertEqual(dot.set_visibility.call_args.args, (shown,))

    def test_its_hover_says_whose_once_where_both_agree(self) -> None:
        ui, _ = self._drawn(self.SET, self.SET)

        self.assertEqual(ui.tooltip.return_value.text, self._changed)

    def test_its_hover_says_whose_each_is_where_they_differ(self) -> None:
        ui, _ = self._drawn(self.SET, self.UNSET)

        self.assertEqual(ui.tooltip.return_value.text, "\n".join((
            self._part(self.WIDTH, self._changed),
            self._part(self.HEIGHT, t("manager.workbench.app_default", app=self.APP)))))

    def test_clear_s_hover_names_the_number_it_clears_where_it_is_one(self) -> None:
        back = t("manager.workbench.back_link",
                 whose=t("manager.workbench.app_default", app=self.APP))

        _, one = self._drawn(self.SET, self.UNSET)
        _, both = self._drawn(self.SET, self.SET)
        _, none = self._drawn(self.UNSET, self.UNSET)

        self.assertEqual(one.call_args.kwargs["hint"], self._part(self.WIDTH, back))
        self.assertEqual(both.call_args.kwargs["hint"], back)
        none.assert_not_called()

    def test_each_number_is_named_and_the_joiner_stands_between_them(self) -> None:
        with patch_workbench("ui") as ui:
            all_settings._pair([(Mock(), {}, self.WIDTH), (Mock(), {}, self.HEIGHT)], "×")
        named = ui.element.return_value.classes.return_value.__enter__.return_value

        self.assertEqual([call.args for call in named.props.__setitem__.call_args_list],
                         [("role", "group"), ("aria-label", "Width"),
                          ("data-setting", self.WIDTH.key),
                          ("role", "group"), ("aria-label", "Height"),
                          ("data-setting", self.HEIGHT.key)])
        ui.label.assert_called_once_with("×")

    def test_pairs_travel_with_their_heading(self) -> None:
        groups = data.config_groups({"groups": [{
            "key": "displays", "label": "Displays", "settings": [], "curated": [
                {"key": "playfield", "label": "Playfield", "keys": list(self.SIZE.keys),
                 "pairs": [{"key": "size", "label": "Size", "note": "", "joiner": "×",
                            "keys": list(self.SIZE.keys)}]},
                {"key": "cabinet", "label": "Cabinet", "keys": ["Player.BGSet"]}]}]})

        self.assertEqual([[vars(pair) for pair in h.pairs] for h in groups[0].curated],
                         [[vars(self.SIZE)], []])


class TableSettingsTitleTests(unittest.TestCase):
    """Every setting at one table, in the dialog Show Every Setting opens."""

    def test_it_is_titled_for_the_table_not_the_launcher(self) -> None:
        said = app_settings.title_for("Medieval Madness", "VPW 1.2", "Visual Pinball X", 1)

        self.assertEqual(said, "Medieval Madness: Visual Pinball X Settings")

    def test_a_game_of_several_tables_says_which(self) -> None:
        said = app_settings.title_for("Medieval Madness", "VPW 1.2", "Visual Pinball X", 2)

        self.assertEqual(said, "Medieval Madness - VPW 1.2: Visual Pinball X Settings")

    def test_a_file_shared_with_the_game_says_who_else_reads_it(self) -> None:
        note = app_settings.shared_note({"shared_with_game": True}, 3)

        self.assertIsNotNone(note)
        self.assertIn("the other 2 tables", _said(note))

    def test_nothing_is_said_where_the_file_is_the_table_s_alone(self) -> None:
        self.assertIsNone(app_settings.shared_note({"shared_with_game": False}, 3))

    def test_nor_where_the_game_has_no_other_table(self) -> None:
        self.assertIsNone(app_settings.shared_note({"shared_with_game": True}, 1))


class BackglassFileTests(unittest.IsolatedAsyncioTestCase):
    """A backglass file's panel has the settings of the one table it is the backglass of."""

    TABLES = [{"id": "t1", "launcher": "l1", "launcher_app_configurable": True},
              {"id": "t2", "launcher": "l1", "launcher_app_configurable": True}]
    WIRE: dict[str, Any] = {
        "groups": [{"key": "plugins", "label": "Plugins", "settings": [], "curated": [
            {"key": "B2S", "label": "B2S", "keys": ["Plugin.B2S.Enable"], "kinds": ["backglass"]},
            {"key": "PinMAME", "label": "PinMAME", "keys": ["Plugin.PinMAME.Enable"]}]}],
        "values": {}}

    def _file(self, binding: str = "table", table: str = "t1", *,
              present: bool = True) -> dict:
        return {"kind": "backglass", "binding": binding, "table": table, "present": present}

    def _served(self, row: dict, *others: dict) -> list[str]:
        return [one["id"] for one in wb_file._served(row, [row, *others], self.TABLES)]

    async def _settings(self, row: dict, tables=None, *, state: str = path_state.OK,
                        has_config: bool = True, others=(),
                        detail: dict | None = None) -> tuple[dict | None, Mock]:
        library = Mock()
        library.launchers.return_value = {"launchers": [
            {**_launcher(state, has_config=has_config), "launcher_id": "l1"}, *others]}
        library.launcher_config.return_value = self.WIRE
        library.play_state.return_value = {}
        self.enterContext(patch.object(run, "io_bound", new=AsyncMock(
            side_effect=lambda call, *args, **kwargs: call(*args, **kwargs))))
        found = await wb_file._file_settings(library, row, [row], tables or self.TABLES[:1],
                                               detail or {})
        return found, library

    def test_a_table_s_own_file_is_that_table_s(self) -> None:
        self.assertEqual(self._served(self._file()), ["t1"])

    def test_the_game_s_file_is_each_table_s_that_has_none_of_its_own(self) -> None:
        shared = self._file("game", "")

        self.assertEqual(self._served(shared), ["t1", "t2"])
        self.assertEqual(self._served(shared, self._file(table="t2")), ["t1"])

    def test_a_file_that_is_missing_or_serves_nothing_is_nobody_s(self) -> None:
        self.assertEqual(self._served(self._file(present=False)), [])
        self.assertEqual(self._served(self._file("orphaned", "")), [])

    async def test_it_holds_only_the_headings_about_its_kind(self) -> None:
        found, library = await self._settings(self._file())

        assert found is not None
        self.assertEqual([[h.key for h in g.curated] for g in found["tied"]], [["B2S"]])
        self.assertEqual((found["config_scope"], found["config_table"]), ("entry", "t1"))
        library.launcher_config.assert_called_once_with("l1", "t1", "entry")

    async def test_a_file_of_two_versions_has_the_row_that_picks_one(self) -> None:
        self.WIRE["groups"][0]["curated"][0]["versions"] = ["Plugin.B2S.Version"]
        self.addCleanup(self.WIRE["groups"][0]["curated"][0].pop, "versions")
        for two_versions, keys in ((True, ["Plugin.B2S.Enable", "Plugin.B2S.Version"]),
                                   (False, ["Plugin.B2S.Enable"])):
            with self.subTest(two_versions=two_versions):
                found, _ = await self._settings(
                    self._file(), detail={"backglass": {"two_versions": two_versions}})

                assert found is not None
                self.assertEqual(list(found["tied"][0].curated[0].keys), keys)

    async def test_a_file_several_tables_use_is_read_and_written_at_each(self) -> None:
        found, library = await self._settings(self._file("game", ""), self.TABLES)

        assert found is not None
        self.assertEqual([(one["table"]["id"], one["launcher_id"]) for one in found["shared"]],
                         [("t1", "l1"), ("t2", "l1")])
        self.assertEqual(found["config_values"], {})
        self.assertEqual((found["config_read"].func, found["config_write"].func),
                         (app_settings.as_one, app_settings.write_shared))

    async def test_nor_where_the_tables_run_different_programs(self) -> None:
        other = {**_launcher(path_state.OK), "launcher_id": "l2", "app": "fp"}
        tables = [self.TABLES[0], {**self.TABLES[1], "launcher": "l2"}]

        found, _ = await self._settings(self._file("game", ""), tables, others=[other])

        self.assertIsNone(found)

    async def test_nor_where_one_of_them_cannot_be_read(self) -> None:
        for table in ({**self.TABLES[1], "launcher": "gone"},
                      {**self.TABLES[1], "launcher_app_configurable": False}):
            with self.subTest(table=table):
                found, _ = await self._settings(self._file("game", ""),
                                                [self.TABLES[0], table])
                self.assertIsNone(found)

    async def test_a_shared_file_says_how_many_tables_it_writes_to(self) -> None:
        found, _ = await self._settings(self._file("game", ""), self.TABLES)
        assert found is not None
        tables = [{**one, "filename": f"{one['id']}.vpx"} for one in self.TABLES]
        found.update(tables=tables, shared=[{**one, "table": table}
                                            for one, table in zip(found["shared"], tables,
                                                                  strict=True)])

        with patch_workbench("_config_values", AsyncMock(return_value={})), \
                patch_workbench("_setting_entries", AsyncMock(return_value=[])), \
                patch.object(panel, "intro") as intro:
            entries = await wb_file._file_settings_entries({"file_settings": found})

        intro.assert_called_once_with(
            "Changes here apply to the 2 tables that use this file", hint="t1\nt2")
        self.assertEqual(entries, [(panel.HEADING, "Settings"), intro.return_value])

    async def test_nor_where_the_launcher_cannot_show_them(self) -> None:
        for state, has_config in ((path_state.MISSING, True), (path_state.OK, False)):
            with self.subTest(state=state, has_config=has_config):
                found, _ = await self._settings(self._file(), state=state,
                                                has_config=has_config)
                self.assertIsNone(found)

    async def test_nor_where_no_heading_is_about_its_kind(self) -> None:
        found, _ = await self._settings({**self._file(), "kind": "wheel"})

        self.assertIsNone(found)

    def test_kinds_travel_with_their_heading(self) -> None:
        groups = data.config_groups(self.WIRE)

        self.assertEqual([h.kinds for h in groups[0].curated], [("backglass",), ()])


class TableRailTests(unittest.TestCase):
    def test_a_table_s_settings_follow_the_table(self) -> None:
        keys = [s.key for s in rail.sections_for("table")]

        self.assertEqual(keys[keys.index("table_details") + 1], "table_settings")

    def test_a_game_has_none(self) -> None:
        self.assertNotIn("table_settings", [s.key for s in rail.sections_for("game")])


class LaunchReportTests(unittest.TestCase):
    """The Launch group's one line about the launcher, which leads to Settings."""

    TABLE = {"launcher_name": "Visual Pinball X", "launcher_app_configurable": True}

    def _report(self, **table: object) -> tuple[str, str]:
        context = {"state": {"view": "games", "game": "g1", "table": "t1"}}
        with patch.object(panel, "link") as link:
            label, _draw = wb_launcher._launcher_report(context, {**self.TABLE, **table})
        self.assertEqual(label, "Launcher")
        return str(link.call_args.args[0]), str(link.call_args.kwargs["to"])

    def test_its_own_settings_are_counted(self) -> None:
        self.assertEqual(self._report(launcher_settings_here=3)[0],
                         "Visual Pinball X · 3 settings of its own")

    def test_one_is_said_as_one(self) -> None:
        self.assertEqual(self._report(launcher_settings_here=1)[0],
                         "Visual Pinball X · 1 setting of its own")

    def test_values_the_game_s_file_gives_it_are_counted(self) -> None:
        self.assertEqual(self._report(launcher_settings_from_folder=2)[0],
                         "Visual Pinball X · 2 settings from This Game")

    def test_with_none_it_is_the_launcher_alone(self) -> None:
        self.assertEqual(self._report()[0], "Visual Pinball X")

    def test_a_program_that_keeps_no_settings_counts_none(self) -> None:
        self.assertEqual(self._report(launcher_app_configurable=False,
                                      launcher_settings_here=3)[0], "Visual Pinball X")

    def test_it_leads_to_the_table_s_settings(self) -> None:
        address = parse_qs(self._report()[1].split("?", 1)[1])

        self.assertEqual(address["section"], ["table_settings"])
        self.assertEqual(address["table"], ["t1"])


class SettingsColumnTests(unittest.TestCase):
    """The Tables grid's Settings column, which says of every table what Launch says of
    one."""

    ROW = {"id": "t", "launcher_name": "Visual Pinball X", "launcher_app_configurable": True}

    def _cell(self, **row: object) -> tuple[str, str, int]:
        (built,) = games.table_rows([{**self.ROW, **row}])
        return built["settings"], built["settings_said"], built["settings_count"]

    def test_a_camera_alone_is_named(self) -> None:
        self.assertEqual(self._cell(launcher_settings_here=1, launcher_point_of_view=True),
                         ("point_of_view", "Point of View", 1))

    def test_beside_other_settings_it_counts_as_one(self) -> None:
        self.assertEqual(self._cell(launcher_settings_here=3, launcher_point_of_view=True),
                         ("own", "3 of its own", 3))

    def test_one_is_said_as_one(self) -> None:
        self.assertEqual(self._cell(launcher_settings_here=1), ("own", "1 of its own", 1))

    def test_values_from_the_game_s_file_say_so(self) -> None:
        self.assertEqual(self._cell(launcher_settings_from_folder=2),
                         ("from_game", "2 from This Game", 2))

    def test_a_table_as_all_tables_play_is_blank(self) -> None:
        self.assertEqual(self._cell(), ("", "", 0))

    def test_a_program_that_keeps_no_settings_counts_none(self) -> None:
        self.assertEqual(self._cell(launcher_app_configurable=False,
                                    launcher_settings_here=3), ("", "", 0))

    def test_its_filter_offers_each_kind_a_cell_holds(self) -> None:
        column = next(one for one in games.TABLE_COLUMNS if one["field"] == "settings")
        choices = column["filterParams"]["choices"]

        self.assertEqual([one["value"] for one in choices],
                         ["own", "point_of_view", "from_game", ""])
        self.assertEqual(choices[0]["label"], "Has Settings of Its Own")

    def test_the_launch_view_shows_it_after_the_launcher(self) -> None:
        launch = games.TABLE_VIEWS["manager.game_tables.launch"]
        assert not isinstance(launch, list)
        shown = launch.columns

        self.assertEqual(shown[shown.index("launcher") + 1], "settings")

    def test_focusing_it_opens_the_table_s_settings(self) -> None:
        section = games.TABLE_COLUMN_SECTIONS["settings"]

        self.assertIn(section, [s.key for s in rail.sections_for("table")])


def _field(key: str, label: str = "", *, per_table: bool = False,
           scopes: tuple[str, ...] = ("entry",)) -> SimpleNamespace:
    return SimpleNamespace(key=key, label=label or key.rsplit(".", 1)[-1], type="text",
                           default="", description="", choices=(), scopes=scopes,
                           per_table=per_table)


class DifferencesTests(unittest.TestCase):
    """What a table's Settings list under the program: its differences and nothing else."""

    SET = {"set_here": True, "in_effect": True, "scope": "entry", "value": "1"}
    GAME = {"set_here": False, "in_effect": True, "scope": "folder", "value": "1"}
    ALL = {"set_here": False, "in_effect": True, "scope": "launcher", "value": "1"}

    def test_only_its_own_values_and_the_game_s_are_listed(self) -> None:
        groups = [_group("sound", _field("Player.A"), _field("Player.B"),
                         _field("Player.C"), _field("Player.D"))]
        values = {"Player.A": self.SET, "Player.B": self.GAME, "Player.C": self.ALL}

        found = app_settings.differences(groups, values)

        self.assertEqual([(area, [f.key for f in fields]) for area, fields in found],
                         [("Sound", ["Player.A", "Player.B"])])

    def test_an_area_s_curated_rows_come_first(self) -> None:
        fields = [_field("Player.A"), _field("Player.B"), _field("Player.C")]
        curated = (SimpleNamespace(key="", label="", keys=("Player.C", "Player.B")),)
        values = {key: self.SET for key in ("Player.A", "Player.B", "Player.C")}

        found = app_settings.differences([_group("sound", *fields, curated=curated)], values)

        self.assertEqual([f.key for f in found[0][1]], ["Player.C", "Player.B", "Player.A"])

    def test_a_plugin_s_row_leads_with_the_plugin_s_name(self) -> None:
        enable = _field("Plugin.B2SLegacy.Enable", "Enable")
        curated = (SimpleNamespace(key="B2SLegacy", label="B2S Legacy",
                                   keys=("Plugin.B2SLegacy.Enable",)),)

        found = app_settings.differences([_group("plugins", enable, curated=curated)],
                                         {enable.key: self.SET})

        self.assertEqual(found[0][1][0].label, "B2S Legacy: Enable")

    DISPLAYS = _group(
        "displays",
        _field("Player.PlayfieldFullScreen", "Display Mode"),
        _field("Player.PlayfieldWidth", "Width"),
        _field("Backglass.BackglassFullScreen", "Display Mode"),
        _field("Backglass.BackglassWidth", "Width"),
        _field("Player.PlayfieldColorDepth", "Color Depth"),
        _field("Backglass.BackglassColorDepth", "Color Depth"),
        _field("Player.BGSet", "View Mode"),
        _field("Player.ScreenWidth", "Screen Width"),
        curated=(SimpleNamespace(key="playfield", label="Playfield",
                                 keys=("Player.PlayfieldFullScreen", "Player.PlayfieldWidth")),
                 SimpleNamespace(key="backglass", label="Backglass",
                                 keys=("Backglass.BackglassFullScreen",
                                       "Backglass.BackglassWidth")),
                 SimpleNamespace(key="cabinet", label="Cabinet",
                                 keys=("Player.BGSet", "Player.ScreenWidth"))))

    def _labels(self, *keys: str, group: Any = DISPLAYS) -> list[str]:
        found = app_settings.differences([group], dict.fromkeys(keys, self.SET))
        return [field.label for field in found[0][1]]

    def test_a_row_several_windows_share_names_its_window(self) -> None:
        """Even alone: under Displays, Display Mode on its own says nothing about which
        window."""
        self.assertEqual(self._labels("Backglass.BackglassFullScreen"),
                         ["Backglass Display Mode"])

    def test_a_window_s_row_names_its_window_where_no_other_window_shares_it(self) -> None:
        preview = _group(
            "more", _field("PlayerVR.PreviewDisplay", "Display"),
            _field("PlayerVR.PreviewWidth", "Width"), _field("Player.Shadows", "Shadows"),
            curated=(SimpleNamespace(key="vr_preview", label="VR Preview",
                                     keys=("PlayerVR.PreviewDisplay",
                                           "PlayerVR.PreviewWidth")),))

        self.assertEqual(self._labels("PlayerVR.PreviewDisplay", "Player.Shadows",
                                      group=preview), ["VR Preview Display", "Shadows"])

    def test_a_heading_over_several_things_leaves_its_rows_the_program_s(self) -> None:
        self.assertEqual(self._labels("Player.BGSet", "Player.ScreenWidth"),
                         ["View Mode", "Screen Width"])

    def test_a_window_s_row_it_does_not_curate_goes_with_its_window(self) -> None:
        self.assertEqual(self._labels("Backglass.BackglassColorDepth"),
                         ["Backglass Color Depth"])

    def test_the_camera_is_not_listed_setting_by_setting(self) -> None:
        view = _field("TableOverride.ViewCabMode")

        self.assertEqual(app_settings.differences(
            [_group("point_of_view", view, summarized=True)], {view.key: self.SET}), [])


class PointOfViewTests(unittest.TestCase):
    MODE = _field("TableOverride.ViewCabMode", "View mode")
    DESKTOP_MODE = _field("TableOverride.ViewDTMode", "View mode")
    CAB = _field("TableOverride.ViewCabPlayerX")
    DESKTOP = _field("TableOverride.ViewDTPlayerX")
    GROUP = SimpleNamespace(
        key="point_of_view", label="Point of View", summarized=True, read_only=False,
        settings=[MODE, DESKTOP_MODE, CAB, DESKTOP], rows=(MODE.key, DESKTOP_MODE.key),
        curated=[SimpleNamespace(key="cabinet", label="Cabinet", keys=(MODE.key, CAB.key)),
                 SimpleNamespace(key="desktop", label="Desktop",
                                 keys=(DESKTOP_MODE.key, DESKTOP.key))])

    def _view(self, **values: dict) -> SimpleNamespace | None:
        return app_settings.point_of_view([self.GROUP], values)

    def test_its_rows_are_each_named_by_their_view(self) -> None:
        view = self._view(**{self.CAB.key: DifferencesTests.SET})
        assert view is not None

        self.assertEqual([row.label for row in view.rows],
                         ["Cabinet View mode", "Desktop View mode"])

    def test_the_camera_is_named_by_the_views_it_is_saved_in(self) -> None:
        view = self._view(**{self.CAB.key: DifferencesTests.SET})
        assert view is not None

        self.assertEqual((view.views, view.own), (["Cabinet"], [self.CAB.key]))
        self.assertEqual(app_settings.camera_said(view), "Saved for this table (Cabinet)")

    def test_one_from_the_game_says_so_and_is_not_the_table_s_to_reset(self) -> None:
        view = self._view(**{self.DESKTOP.key: DifferencesTests.GAME})
        assert view is not None

        self.assertEqual(view.own, [])
        self.assertEqual(app_settings.camera_said(view), "Saved for this game (Desktop)")

    def test_a_view_mode_alone_leaves_the_camera_the_table_s_own(self) -> None:
        view = self._view(**{self.MODE.key: DifferencesTests.SET})
        assert view is not None

        self.assertEqual((view.views, view.own, view.reaching), ([], [], False))
        self.assertEqual(app_settings.camera_said(view), "The table's own")

    def test_and_absent_when_nothing_in_it_differs(self) -> None:
        self.assertIsNone(self._view(**{self.CAB.key: DifferencesTests.ALL}))

    def test_reset_takes_the_camera_off_and_leaves_the_view_modes(self) -> None:
        view = self._view(**{self.CAB.key: DifferencesTests.SET,
                             self.MODE.key: DifferencesTests.SET})
        assert view is not None
        remove = Mock()

        with patch.object(app_settings.panel, "action") as action:
            entries = app_settings._camera_entries(view, remove, playing=False)
        action.call_args.args[1]()

        self.assertIn(app_settings.panel.ASIDE, [label for label, _draw in entries])
        remove.assert_called_once_with([self.CAB.key])

    def test_no_reset_where_the_table_does_not_hold_it(self) -> None:
        view = self._view(**{self.DESKTOP.key: DifferencesTests.GAME})
        assert view is not None

        with patch.object(app_settings.panel, "action") as action:
            app_settings._camera_entries(view, Mock(), playing=False)

        action.assert_not_called()


class TableOptionsTests(unittest.TestCase):
    SPEED = _field("TableOption.Ball_Speed", "Ball Speed")
    LIGHTS = _field("TableOption.Lights", "Lights")
    GROUP = SimpleNamespace(key="table_options", label="Table Options", summarized=False,
                            read_only=True, settings=[SPEED, LIGHTS], curated=[], rows=())

    def _resets(self, values: dict) -> list[str]:
        options = app_settings.table_options([self.GROUP], values)
        assert options is not None
        with patch.object(app_settings.panel, "action") as action:
            app_settings._option_entries(options, values, Mock(), playing=False)
        return [call.args[0] for call in action.call_args_list]

    def test_each_one_the_table_holds_has_reset_and_two_have_reset_all(self) -> None:
        values = {self.SPEED.key: DifferencesTests.SET, self.LIGHTS.key: DifferencesTests.SET}

        self.assertEqual(self._resets(values), ["Reset", "Reset", "Reset All"])

    def test_one_alone_has_no_reset_all(self) -> None:
        self.assertEqual(self._resets({self.SPEED.key: DifferencesTests.SET}), ["Reset"])

    def test_they_are_not_listed_among_the_differences(self) -> None:
        values = {self.SPEED.key: DifferencesTests.SET}

        self.assertEqual(app_settings.differences([self.GROUP], values), [])

    def test_and_absent_without_one(self) -> None:
        self.assertIsNone(app_settings.table_options([self.GROUP], {}))


class AddASettingTests(unittest.TestCase):
    SET = DifferencesTests.SET
    SOUND = _group("sound", _field("Player.A"), _field("Player.B", per_table=True))
    GRAPHICS = _group("graphics", _field("Player.C", per_table=True), _field("Player.D"))

    def _offered(self, groups, values=None, added=()) -> list[tuple[str, str]]:
        return [(str(field.label), area)
                for field, area in app_settings.addable(groups, values or {}, added)]

    def test_those_commonly_set_per_table_come_first_each_with_its_area(self) -> None:
        self.assertEqual(self._offered([self.SOUND, self.GRAPHICS]),
                         [("B", "Sound"), ("C", "Graphics"), ("A", "Sound"),
                          ("D", "Graphics")])

    def test_one_the_table_already_shows_is_not_offered(self) -> None:
        offered = self._offered([self.SOUND, self.GRAPHICS],
                                {"Player.A": self.SET, "Player.B": DifferencesTests.GAME},
                                added=["Player.C"])

        self.assertEqual(offered, [("D", "Graphics")])

    def test_one_kept_for_all_tables_alone_is_not_offered(self) -> None:
        group = _group("sound", _field("Player.A"),
                       _field("Player.ShowFPS", scopes=("launcher",)))

        self.assertEqual(self._offered([group]), [("A", "Sound")])

    def test_nor_are_the_table_s_options(self) -> None:
        self.assertEqual(self._offered([TableOptionsTests.GROUP]), [])

    def test_of_the_point_of_view_only_the_view_modes_it_draws(self) -> None:
        group = SimpleNamespace(**{**vars(PointOfViewTests.GROUP),
                                   "rows": (PointOfViewTests.MODE.key,)})

        self.assertEqual(self._offered([group]), [("Cabinet View mode", "Point of View")])

    def test_and_none_of_it_once_its_rows_are_drawn(self) -> None:
        values = {PointOfViewTests.CAB.key: self.SET}

        self.assertEqual(self._offered([PointOfViewTests.GROUP], values), [])

    def test_an_added_setting_is_listed_among_the_differences(self) -> None:
        found = app_settings.differences([self.SOUND], {}, added=["Player.B"])

        self.assertEqual([(area, [f.key for f in fields]) for area, fields in found],
                         [("Sound", ["Player.B"])])

    def test_an_added_view_mode_draws_the_point_of_view(self) -> None:
        view = app_settings.point_of_view([PointOfViewTests.GROUP], {},
                                          added=[PointOfViewTests.MODE.key])

        assert view is not None
        self.assertEqual((len(view.rows), view.views, view.own), (2, [], []))

    def test_what_was_added_is_forgotten_when_another_table_is_open(self) -> None:
        state: dict[str, Any] = {}
        app_settings._added(state, "t1")
        state[app_settings.ADDED]["keys"] = ["Player.A"]

        self.assertEqual(app_settings._added(state, "t1"), ["Player.A"])
        self.assertEqual(app_settings._added(state, "t2"), [])

    def _headings(self, offered: list) -> list[str]:
        with patch.object(app_settings.panel, "SettingPicker") as picker, \
                patch.object(app_settings.ui, "run_javascript"):
            app_settings._add_picker({"state": {}}, [], offered)
        return list(picker.call_args.kwargs["headings"].values())

    def test_a_heading_starts_each_run(self) -> None:
        offered = app_settings.addable([self.SOUND, self.GRAPHICS], {}, ())

        self.assertEqual(self._headings(offered),
                         ["Commonly Set per Table", "Everything Else"])

    def test_and_none_where_nothing_is_commonly_set_per_table(self) -> None:
        offered = app_settings.addable([_group("sound", _field("Player.A"))], {}, ())

        self.assertEqual(self._headings(offered), [])


def _pair(key: str, label: str, *keys: str) -> SimpleNamespace:
    return SimpleNamespace(key=key, label=label, note="", joiner="×", keys=keys)


class TablePairTests(unittest.TestCase):
    """A pair at a table: listed whole, named as a row away from its heading is, and
    offered once."""

    SET = DifferencesTests.SET
    MODE = ("Backglass.BackglassFSWidth", "Backglass.BackglassFSHeight")
    SIZE = ("Backglass.BackglassWidth", "Backglass.BackglassHeight")
    TOPPER = ("Topper.TopperWidth", "Topper.TopperHeight")
    DMD = ("Plugin.B2S.BackglassDMDX", "Plugin.B2S.BackglassDMDY")
    DISPLAYS = _group(
        "displays", *(_field(key, "Height" if key.endswith("Height") else "Width")
                      for key in (*MODE, *SIZE, *TOPPER)),
        curated=(_heading("backglass", *MODE, *SIZE, pairs=[
                     _pair("video_mode", "Video Mode", *MODE), _pair("size", "Size", *SIZE)]),
                 _heading("topper", *TOPPER, pairs=[_pair("size", "Size", *TOPPER)])))
    PLUGINS = _group(
        "plugins", _field("Plugin.B2S.Enable", "Enable"), *(_field(key) for key in DMD),
        curated=(_heading("B2S", "Plugin.B2S.Enable", *DMD, label="B2S",
                          pairs=[_pair("backglass_dmd_position", "Backglass DMD Position",
                                       *DMD)]),))

    def test_a_pair_is_listed_whole_where_the_table_sets_either_row(self) -> None:
        found = app_settings.differences([self.DISPLAYS], {self.SIZE[1]: self.SET})

        self.assertEqual([(f.key, f.label) for f in found[0][1]],
                         [(self.SIZE[0], "Width"), (self.SIZE[1], "Height")])

    def test_a_pair_is_named_by_its_plugin_or_by_its_window_shared_label_or_not(
            self) -> None:
        self.assertEqual([pair.label for pair in app_settings.named_pairs(
                             [self.DISPLAYS, self.PLUGINS])],
                         ["Backglass Video Mode", "Backglass Size", "Topper Size",
                          t("manager.app_settings.plugin_row", plugin="B2S",
                            label="Backglass DMD Position")])

    def test_add_a_setting_offers_a_pair_once_by_its_name(self) -> None:
        self.assertEqual([(str(field.label), field.key) for field, _ in
                          app_settings.addable([self.DISPLAYS], {}, ())],
                         [("Backglass Video Mode", self.MODE[0]),
                          ("Backglass Size", self.SIZE[0]), ("Topper Size", self.TOPPER[0])])

    def test_and_not_once_either_of_its_rows_is_drawn(self) -> None:
        offered = app_settings.addable([self.DISPLAYS], {self.SIZE[1]: self.SET},
                                       [self.TOPPER[0]])

        self.assertEqual([field.key for field, _ in offered], [self.MODE[0]])


def _other(table_id: str, value: str = "1", *, scope: str = "entry",
           shares: bool = False) -> dict:
    return {"table": {"id": table_id}, "launcher_id": "l1", "shares": shares,
            "reads_game": scope == "folder",
            "values": {"Player.X": {"value": value, "scope": scope}}}


class _Game:
    """The game's tables as the API answers for them. A write lands in the written
    table's own file, and where that file is also the game's, in what the tables reading
    it get."""

    def __init__(self, *others: dict) -> None:
        self.held = {one["table"]["id"]: dict(one) for one in others}
        self.written: list[str] = []

    def launcher_config(self, _launcher: str, table: str, _scope: str) -> dict:
        one = self.held[table]
        return {"values": one["values"], "shared_with_game": one["shares"]}

    def write_launcher_config(self, _launcher: str, values: dict, *, table: str,
                              scope: str) -> dict:
        self.written.append(f"{table}@{scope}")
        held = self.held[table]
        held["values"] = {**{key: one for key, one in held["values"].items()
                             if one["scope"] != "folder"},
                          **{key: {"value": value, "scope": "entry"}
                             for key, value in values.items()}}
        if held["shares"]:
            for one in self.held.values():
                one["values"] = {**one["values"], **{
                    key: {"value": value, "scope": "folder"} for key, value in values.items()
                    if (one["values"].get(key) or {}).get("scope") == "folder"}}
        return {}


class SetForAllTests(unittest.TestCase):
    """Set for All N Tables, beside a value one of a game's tables sets itself."""

    SET = {"set_here": True, "in_effect": True, "scope": "entry", "value": "2"}
    FIELD = _field("Player.X")

    def _verb(self, *others: dict, offered=frozenset({"Player.X"})):
        return app_settings.ForAll({"playing": False}, list(others), False, [], offered,
                                   AsyncMock(), read=True)

    def _unread(self, library: Any) -> app_settings.ForAll:
        return app_settings.ForAll({"playing": False, "library": library},
                                   [{"table": {"id": "b"}, "launcher_id": "l1"}], False, [],
                                   frozenset({"Player.X"}), AsyncMock())

    def test_the_other_tables_are_read_once_a_value_here_could_offer_it(self) -> None:
        game = _Game(_other("b", "1"))
        game.launcher_config = Mock(wraps=game.launcher_config)  # type: ignore[method-assign]
        more = self._unread(game)
        with patch.object(app_settings.offload, "io",
                          new=AsyncMock(side_effect=lambda call, *args: call(*args))):
            asyncio.run(more.ready({"Player.X": {**self.SET, "set_here": False}}))
            self.assertEqual(game.launcher_config.call_count, 0)
            asyncio.run(more.ready({"Player.X": self.SET}))
            asyncio.run(more.ready({"Player.X": self.SET}))

        self.assertEqual(game.launcher_config.call_count, 1)
        self.assertIsNotNone(more(self.SET, self.FIELD))

    def test_nothing_is_offered_before_they_are_read(self) -> None:
        self.assertIsNone(self._unread(_Game(_other("b", "1")))(self.SET, self.FIELD))

    def test_it_is_offered_where_another_table_does_not_use_the_value(self) -> None:
        self.assertIsNotNone(self._verb(_other("b", "2"), _other("c", "1"))(
            self.SET, self.FIELD))

    def test_and_not_where_every_other_table_does(self) -> None:
        self.assertIsNone(self._verb(_other("b", "2"))(self.SET, self.FIELD))

    def test_nor_beside_a_value_the_table_does_not_set_itself(self) -> None:
        held = {**self.SET, "set_here": False, "scope": "launcher"}

        self.assertIsNone(self._verb(_other("b", "1"))(held, self.FIELD))

    def test_nor_on_the_camera(self) -> None:
        camera = _field("TableOverride.ViewCabFOV")

        self.assertIsNone(self._verb(_other("b", "1"))(self.SET, camera))

    def test_the_camera_and_the_table_options_are_one_table_s_own(self) -> None:
        view = SimpleNamespace(summarized=True, rows=["TableOverride.ViewCabMode"],
                               settings=[_field("TableOverride.ViewCabMode"),
                                         _field("TableOverride.ViewCabFOV")])
        options = SimpleNamespace(summarized=False, read_only=True,
                                  settings=[_field("TableOption.Volume")])
        sound = SimpleNamespace(summarized=False, settings=[
            _field("Player.X"), _field("Player.ShowFPS", scopes=("launcher",))])

        self.assertEqual(app_settings._for_every_table([sound, view, options]),
                         {"Player.X", "TableOverride.ViewCabMode"})

    def test_a_table_reading_this_table_s_file_uses_what_it_sets(self) -> None:
        other = _other("b", "1", scope="folder")

        self.assertTrue(app_settings.already_uses(other, self.FIELD, "2", True))
        self.assertFalse(app_settings.already_uses(other, self.FIELD, "2", False))

    def test_each_table_not_using_it_gets_it_in_its_own_file(self) -> None:
        game = _Game(_other("b", "1"), _other("c", "2"))

        cut = app_settings.write_for_all(game, [_other("b", "1"), _other("c", "2")],
                                         self.FIELD, "2", False)

        self.assertEqual((game.written, cut), (["b@entry"], []))

    def test_the_table_whose_file_is_the_game_s_goes_first(self) -> None:
        """So a table reading that file has the value from it, not a file of its own."""
        others = [_other("b", "1", scope="folder"), _other("a", "1", shares=True)]
        game = _Game(*others)

        cut = app_settings.write_for_all(game, others, self.FIELD, "2", False)

        self.assertEqual((game.written, cut), (["a@entry"], []))

    def test_a_table_that_stops_reading_the_game_s_file_is_named(self) -> None:
        others = [_other("b", "1", scope="folder")]

        cut = app_settings.write_for_all(_Game(*others), others, self.FIELD, "2", False)

        self.assertEqual(cut, [{"id": "b"}])

    def test_a_row_draws_it_on_the_line_under_its_value(self) -> None:
        verb = Mock()
        more = Mock(return_value=verb)
        with patch_workbench("ui"):
            all_settings._beside(lambda: None, dict(self.SET), self.FIELD, more=more)()

        more.assert_called_once()
        verb.assert_called_once_with()

    HEIGHT = _field("Player.Y")
    UNSET = {"set_here": False, "in_effect": True, "scope": "launcher", "value": "2"}
    BOTH = frozenset({"Player.X", "Player.Y"})

    @staticmethod
    def _pair_other(table_id: str, x: str, y: str, scope: str = "entry") -> dict:
        return {**_other(table_id, x, scope=scope), "values": {
            "Player.X": {"value": x, "scope": scope}, "Player.Y": {"value": y, "scope": scope}}}

    def test_a_pair_s_row_offers_it_where_either_row_is_set_here(self) -> None:
        verb = self._verb(self._pair_other("b", "2", "1"), offered=self.BOTH)

        self.assertIsNotNone(verb(self.UNSET, self.FIELD, (self.SET, self.HEIGHT)))
        self.assertIsNone(verb(self.UNSET, self.FIELD, (self.UNSET, self.HEIGHT)))

    def test_and_not_where_every_other_table_uses_each_row_set_here(self) -> None:
        verb = self._verb(self._pair_other("b", "1", "2"), offered=self.BOTH)

        self.assertIsNone(verb(self.UNSET, self.FIELD, (self.SET, self.HEIGHT)))

    def test_a_pair_s_row_draws_it_where_only_its_second_row_is_set_here(self) -> None:
        verb = Mock()
        more = Mock(return_value=verb)
        with patch_workbench("ui"):
            all_settings._beside(lambda: None, dict(self.UNSET), self.FIELD, more=more,
                              paired=[(dict(self.SET), self.HEIGHT)])()

        more.assert_called_once_with(self.UNSET, self.FIELD, (self.SET, self.HEIGHT))
        verb.assert_called_once_with()

    def test_a_pair_writes_each_of_its_rows_set_here(self) -> None:
        other = self._pair_other("b", "1", "1", scope="folder")
        game = _Game(other)
        inner: dict[str, Any] = {"playing": False, "library": game, "rebuild": AsyncMock()}
        with patch.object(app_settings.panel, "action") as action:
            app_settings.ForAll(inner, [other], False, [], self.BOTH, inner["rebuild"],
                                read=True)(
                dict(self.SET), self.FIELD, (dict(self.SET), self.HEIGHT))
        with patch.object(app_settings, "ui"), \
                patch_workbench("no_longer_reads_game") as warned, \
                patch.object(app_settings.offload, "io",
                             new=AsyncMock(side_effect=lambda call: call())):
            asyncio.run(press(action.call_args.args[1]))

        self.assertEqual({key: one["value"] for key, one in game.held["b"]["values"].items()},
                         {"Player.X": "2", "Player.Y": "2"})
        warned.assert_called_once()

    def test_every_setting_draws_itself_again_and_not_the_section_under_it(self) -> None:
        other = _other("b", "1")
        section, dialog = AsyncMock(), AsyncMock()
        inner: dict[str, Any] = {"playing": False, "library": _Game(other), "rebuild": section}
        more = app_settings.ForAll(inner, [other], False, [], frozenset({"Player.X"}),
                                   section, read=True)
        with patch.object(app_settings.panel, "action") as action:
            replace(more, rebuild=dialog)(dict(self.SET), self.FIELD)
        with patch.object(app_settings, "ui"), \
                patch.object(app_settings.offload, "io",
                             new=AsyncMock(side_effect=lambda call: call())):
            asyncio.run(press(action.call_args.args[1]))

        dialog.assert_awaited_once()
        section.assert_not_awaited()

    def test_it_keeps_the_place_of_the_setting_it_wrote(self) -> None:
        other = _other("b", "1")
        inner: dict[str, Any] = {"playing": False, "library": _Game(other), "rebuild": AsyncMock()}
        with patch.object(app_settings.panel, "action") as action:
            app_settings.ForAll(inner, [other], False, [], frozenset({"Player.X"}),
                                inner["rebuild"], read=True)(dict(self.SET), self.FIELD)
        with patch.object(app_settings, "ui"), \
                patch_workbench("_keeping_place", new=AsyncMock()) as kept, \
                patch.object(app_settings.offload, "io",
                             new=AsyncMock(side_effect=lambda call: call())):
            asyncio.run(press(action.call_args.args[1]))

        kept.assert_awaited_once_with(ANY, inner["rebuild"], "Player.X")

    def test_a_table_reads_them_at_its_draw_and_again_after_a_write(self) -> None:
        before = {"Player.X": {**self.SET, "set_here": False, "scope": "launcher"}}
        after = {"Player.X": self.SET}
        more = Mock(ready=AsyncMock(), return_value=None)
        context = {"library": Mock(), "launcher": {"launcher_id": "probe"},
                   "config_scope": "entry", "config_table": "t1", "rebuild": AsyncMock(),
                   "config_more": more}

        async def drive() -> None:
            with patch_workbench("ui"), \
                    patch_workbench("_config_values",
                                 new=AsyncMock(side_effect=[before, after])), \
                    patch.object(run, "io_bound", new=AsyncMock(return_value={})), \
                    patch.object(settings_page, "control_for") as control_for:
                await all_settings._setting_entries(context, [("", "", [self.FIELD])])
                await press(control_for.call_args.args[2], "2")

        asyncio.run(drive())

        self.assertEqual([call.args[0] for call in more.ready.await_args_list],
                         [before, after])

    def test_a_write_typed_while_the_first_one_reads_them_is_the_one_held(self) -> None:
        unset = {"Player.X": {**self.SET, "set_here": False, "scope": "launcher"}}
        typed = [{"Player.X": {**self.SET, "value": value}} for value in ("2", "23")]
        first_read = asyncio.Event()

        async def ready(values: dict) -> None:
            if values is typed[0]:
                await first_read.wait()

        context = {"library": Mock(), "launcher": {"launcher_id": "probe"},
                   "config_scope": "entry", "config_table": "t1", "rebuild": AsyncMock(),
                   "config_more": Mock(ready=AsyncMock(side_effect=ready), return_value=None)}

        async def drive() -> dict:
            with patch_workbench("ui"), \
                    patch_workbench("_marked") as marked, \
                    patch_workbench("_config_values",
                                 new=AsyncMock(side_effect=[unset, *typed])), \
                    patch.object(run, "io_bound", new=AsyncMock(return_value={})), \
                    patch.object(settings_page, "control_for") as control_for:
                await all_settings._setting_entries(context, [("", "", [self.FIELD])])
                save = control_for.call_args.args[2]
                first = asyncio.create_task(press(save, "2"))
                await asyncio.sleep(0)
                await press(save, "23")
                first_read.set()
                await first
            return marked.call_args.args[0][0][1]

        self.assertEqual(asyncio.run(drive())["value"], "23")

    def test_the_tables_cut_off_the_game_s_file_are_named_a_line_each(self) -> None:
        cut = [{"id": "b", "name": "Addams Family, The"}, {"id": "c", "name": "Other"}]
        with patch_workbench("ui") as ui, \
                patch_workbench("_table_line", side_effect=lambda one, _: one["name"]):
            all_settings.no_longer_reads_game(cut, cut)

        self.assertEqual(ui.notify.call_args.args[0].splitlines()[1:],
                         ["Addams Family, The", "Other"])


class _Tables:
    """Each table's settings as the API answers for them, and what was written where."""

    def __init__(self, **values: dict) -> None:
        self.values = values
        self.written: list[tuple[str, dict]] = []

    def launcher_config(self, _launcher: str, table: str, _scope: str) -> dict:
        return {"values": self.values[table], "shared_with_game": False}

    def write_launcher_config(self, _launcher: str, values: dict, *, table: str,
                              scope: str) -> dict:
        self.written.append((table, values))
        return {}


class SharedFileTests(unittest.TestCase):
    """The settings of every table one file is used by, as one row a setting."""

    KEY = "Plugin.B2S.BackglassDMDX"
    GROUPS = [_group("plugins", _setting(KEY, default="0"))]

    def _targets(self, tables: _Tables) -> list[dict]:
        return [{"table": {"id": table}, "launcher_id": "l1"} for table in tables.values]

    def _as_one(self, **held: dict) -> dict:
        tables = _Tables(**{table: {self.KEY: one} for table, one in held.items()})
        return app_settings.as_one(tables, self._targets(tables), self.GROUPS)[self.KEY]

    def test_where_the_tables_differ_it_varies_and_holds_no_value(self) -> None:
        held = self._as_one(a={"value": "120", "set_here": True},
                            b={"value": "60", "set_here": True})

        self.assertEqual((held["varies"], held["value"]), (True, ""))
        self.assertEqual([(table["id"], value) for table, value in held["each"]],
                         [("a", "120"), ("b", "60")])

    def test_a_blank_is_the_default_it_stands_for(self) -> None:
        held = self._as_one(a={"value": ""}, b={"value": "0", "set_here": True})

        self.assertNotIn("varies", held)

    def test_it_is_set_here_where_any_of_them_sets_it(self) -> None:
        held = self._as_one(a={"value": "0", "scope": "launcher"},
                            b={"value": "0", "scope": "entry", "set_here": True,
                               "in_effect": False})

        self.assertEqual((held["set_here"], held["in_effect"], held["scope"]),
                         (True, False, "entry"))

    def test_a_blank_is_written_only_where_a_table_sets_one(self) -> None:
        tables = _Tables(a={self.KEY: {"value": "120", "set_here": True}},
                         b={self.KEY: {"value": "0"}})

        app_settings.write_shared(tables, self._targets(tables), self.GROUPS, {self.KEY: ""})

        self.assertEqual(tables.written, [("a", {self.KEY: ""})])

    def test_a_varying_switch_draws_the_rows_under_it(self) -> None:
        switch = _setting("Plugin.B2S.Enable", default="0")
        group = _group("plugins", switch, _setting(self.KEY), curated=[
            _heading("B2S", "Plugin.B2S.Enable", self.KEY, enabled_by="Plugin.B2S.Enable")])

        shown = all_settings.curated_blocks(group, {"Plugin.B2S.Enable": {"value": "",
                                                                      "varies": True}})

        self.assertEqual([field.key for field in shown[0][1]],
                         ["Plugin.B2S.Enable", self.KEY])


class CopyFromGameTests(unittest.IsolatedAsyncioTestCase):
    """Where a table's own file keeps the game's from reaching it."""

    REACH = {"Player.X": "1", "Player.Y": "2"}

    def test_it_says_how_many_do_not_reach_the_table(self) -> None:
        entries = app_settings._from_game_entries({"playing": False}, self.REACH)

        self.assertEqual(_said(entries[0]), "2 of this game's settings do not apply to "
                                            "this table with its own file")

    async def test_copying_writes_them_at_the_table_s_own_scope(self) -> None:
        inner: dict[str, Any] = {"library": Mock(), "launcher": {"launcher_id": "l1"},
                 "config_table": "t1", "rebuild": AsyncMock()}
        io = self.enterContext(patch.object(app_settings.offload, "io",
                                            new=AsyncMock(return_value={})))
        self.enterContext(patch.object(app_settings, "ui"))

        await press(app_settings._copy_from_game, inner, self.REACH)

        io.assert_awaited_once_with(inner["library"].write_launcher_config, "l1",
                                    self.REACH, table="t1", scope="entry")
        inner["rebuild"].assert_awaited_once()


class FollowsGameTests(unittest.IsolatedAsyncioTestCase):
    """A table with no file of its own reads its game's."""

    REACH = {"Player.X": "1", "Player.Y": "2"}

    def test_it_says_the_table_follows_its_game(self) -> None:
        entries = app_settings._follows_game_entries({"playing": False}, self.REACH)

        self.assertEqual(_said(entries[0]),
                         "No file of its own, so it follows its game's settings")

    async def test_copying_gives_the_table_a_file_with_those_values(self) -> None:
        inner: dict[str, Any] = {"library": Mock(), "launcher": {"launcher_id": "l1"},
                 "config_table": "t1", "rebuild": AsyncMock()}
        io = self.enterContext(patch.object(app_settings.offload, "io",
                                            new=AsyncMock(return_value={})))
        self.enterContext(patch.object(app_settings, "ui"))

        await press(app_settings._copy_from_game, inner, self.REACH)

        io.assert_awaited_once_with(inner["library"].write_launcher_config, "l1",
                                    self.REACH, table="t1", scope="entry")


class GoingBackTests(unittest.TestCase):
    """What clearing the last value in a table's own file hands back to the game's."""

    GROUPS = [_group("player", _setting("Player.X", "X"), _setting("Player.Y", "Y"),
                     _setting("Player.Z", "Z"))]
    REACH = {"Player.Y": "2", "Player.Z": "3"}

    def _back(self, held: dict[str, bool], cleared: list[str], reach=None) -> str:
        inner = {"config_values": {key: {"set_here": here} for key, here in held.items()}}
        return app_settings.going_back(
            self.GROUPS, self.REACH if reach is None else reach, inner, cleared)

    def test_it_names_the_rows_the_game_s_file_changes(self) -> None:
        said = self._back({"Player.X": True, "Player.Y": False}, ["Player.X"])

        self.assertEqual(said, "Then it follows the game's settings, which also changes:\nY\nZ")

    def test_nothing_while_the_file_keeps_another_value(self) -> None:
        said = self._back({"Player.X": True, "Player.Y": True}, ["Player.X"])

        self.assertEqual(said, "")

    def test_nothing_where_the_game_s_file_changes_nothing_else(self) -> None:
        said = self._back({"Player.X": True}, ["Player.X"], reach={})

        self.assertEqual(said, "")

    def test_a_row_cleared_with_it_is_not_named(self) -> None:
        said = self._back({"Player.X": True, "Player.Y": True}, ["Player.X", "Player.Y"])

        self.assertEqual(said, "Then it follows the game's settings, which also changes:\nZ")


class RedrawTests(unittest.TestCase):
    """Whether a write can be marked in place or the panel has to be drawn again."""

    GAME_CAMERA = {"TableOverride.ViewCabFOV": {"value": "30", "scope": "folder"},
                   "Player.SoundVolume": {"value": "40", "scope": "folder"}}

    def test_a_write_that_changes_only_itself_is_marked_in_place(self) -> None:
        after = {**self.GAME_CAMERA, "Player.SoundVolume": {"value": "45", "scope": "entry"}}

        self.assertFalse(all_settings._moved(self.GAME_CAMERA, after, "Player.SoundVolume"))

    def test_a_table_s_first_file_takes_the_game_s_values_off_every_setting(self) -> None:
        """The camera is not a row, and it stops reaching the table all the same."""
        after = {"TableOverride.ViewCabFOV": {"value": "", "scope": ""},
                 "Player.SoundVolume": {"value": "45", "scope": "entry"}}

        self.assertTrue(all_settings._moved(self.GAME_CAMERA, after, "Player.SoundVolume"))


class TypedRedrawTests(unittest.IsolatedAsyncioTestCase):
    """A number writes on every key, so the redraw its first write calls for waits until
    focus leaves it: drawn between two keys, the second has nowhere to go."""

    AFTER = {"TableOverride.ViewCabFOV": {"value": "", "scope": ""},
             "Player.SoundVolume": {"value": "4", "scope": "entry"},
             "Player.PlayMusic": {"value": "1", "scope": ""}}

    async def _drawn(self, kind: str) -> tuple[list, AsyncMock, Any, Any]:
        key = "Player.SoundVolume" if kind == "int" else "Player.PlayMusic"
        field = SimpleNamespace(key=key, type=kind, label="Row", default="", choices=(),
                                blank="", scopes=("launcher", "entry"), help="",
                                description="")
        rebuild = AsyncMock()
        context = {"library": Mock(), "launcher": {"launcher_id": "probe"},
                   "config_scope": "entry", "config_table": "table", "rebuild": rebuild}
        values = AsyncMock(side_effect=[dict(RedrawTests.GAME_CAMERA), dict(self.AFTER)])
        ui = self.enterContext(patch_workbench("ui"))
        self.enterContext(patch_workbench("_config_values", new=values))
        self.enterContext(patch.object(run, "io_bound",
                                       new=AsyncMock(return_value={})))
        control_for = self.enterContext(patch.object(settings_page, "control_for"))
        entries = await all_settings._setting_entries(context, [("", "", [field])])
        return entries, rebuild, control_for.call_args.args[2], ui

    async def test_a_number_is_drawn_again_once_focus_leaves_it(self) -> None:
        entries, rebuild, save, ui = await self._drawn("int")
        entries[0][1]()
        row = ui.row.return_value.classes.return_value.__enter__.return_value
        event, leave = row.on.call_args.args

        await press(save, 4)
        await asyncio.sleep(0)
        rebuild.assert_not_awaited()
        await press(leave)

        self.assertEqual(event, "focusout")
        rebuild.assert_awaited_once()

    async def test_a_number_s_writes_land_in_the_order_it_was_typed(self) -> None:
        landed: list[str] = []

        async def slower_first(_write, _launcher, values, **_kw) -> dict:
            said = values["Player.SoundVolume"]
            await asyncio.sleep(0.03 / len(said))
            landed.append(said)
            return {}

        _, _, save, _ = await self._drawn("int")
        with patch.object(run, "io_bound", new=slower_first):
            await asyncio.gather(press(save, 4), press(save, 40), press(save, 409))

        self.assertEqual(landed, ["4", "40", "409"])

    async def test_a_switch_is_drawn_again_at_once(self) -> None:
        _, rebuild, save, _ = await self._drawn("bool")

        await press(save, True)
        await asyncio.sleep(0)

        rebuild.assert_awaited_once()

    async def test_either_draw_keeps_the_place_of_the_setting_written(self) -> None:
        kept = self.enterContext(patch_workbench("_keeping_place", new=AsyncMock()))
        entries, rebuild, typed, ui = await self._drawn("int")
        entries[0][1]()
        row = ui.row.return_value.classes.return_value.__enter__.return_value
        _, leave = row.on.call_args.args
        await press(typed, 4)
        await press(leave)
        _, _, switched, _ = await self._drawn("bool")
        await press(switched, True)
        await asyncio.sleep(0)

        self.assertEqual([call.args[1:] for call in kept.await_args_list],
                         [(rebuild, "Player.SoundVolume"), (ANY, "Player.PlayMusic")])
        row.props.__setitem__.assert_any_call("data-setting", "Player.SoundVolume")

    async def test_a_clear_keeps_the_place_of_the_setting_cleared(self) -> None:
        kept = self.enterContext(patch_workbench("_keeping_place", new=AsyncMock()))
        marked = self.enterContext(patch_workbench("_marked"))
        _, rebuild, _, _ = await self._drawn("int")
        await press(marked.call_args.kwargs["clear"])

        kept.assert_awaited_once_with(ANY, rebuild, "Player.SoundVolume", in_place=False)


class KeepingPlaceTests(unittest.IsolatedAsyncioTestCase):
    """Where focus is put once the panel is drawn again."""

    async def _sent(self, where: Any) -> tuple[list[str], AsyncMock]:
        rebuild = AsyncMock()
        client = Mock()
        asked = asyncio.get_running_loop().create_future()
        if isinstance(where, Exception):
            asked.set_exception(where)
        else:
            asked.set_result(where)
        client.run_javascript.side_effect = [asked, None]
        await all_settings._keeping_place(client, rebuild, "Player.PlayMusic")
        rebuild.assert_awaited_once()
        return [call.args[0] for call in client.run_javascript.call_args_list[1:]], rebuild

    async def test_the_control_that_had_focus_comes_first_then_its_row_s_place(
            self) -> None:
        sent, _ = await self._sent([["Player.SoundVolume", 1], 3])

        self.assertEqual(len(sent), 1)
        self.assertIn('[[["Player.SoundVolume", 1], ["Player.PlayMusic", 0]], 3]', sent[0])

    async def test_focus_elsewhere_in_the_panel_goes_to_the_one_written(self) -> None:
        sent, _ = await self._sent([])

        self.assertIn('[[["Player.PlayMusic", 0]], -1]', sent[0])

    async def test_focus_outside_the_panel_is_left_where_it_went(self) -> None:
        self.assertEqual((await self._sent(None))[0], [])

    async def test_a_page_that_cannot_say_is_still_drawn(self) -> None:
        self.assertEqual((await self._sent(TimeoutError()))[0], [])


class GridBehindTests(unittest.IsolatedAsyncioTestCase):
    """The grid behind a table counts the settings it has of its own, and a write marked
    in place puts its row right where that count moved."""

    async def _saved_after(self, before: dict, after: dict) -> AsyncMock:
        field = SimpleNamespace(key="Player.PlayMusic", type="bool", label="Row", default="",
                                choices=(), blank="", scopes=("launcher", "entry"), help="",
                                description="")
        saved, rebuild = AsyncMock(), AsyncMock()
        context = {"library": Mock(), "launcher": {"launcher_id": "probe"},
                   "config_scope": "entry", "config_table": "table",
                   "rebuild": rebuild, "saved": saved}
        self.enterContext(patch_workbench("ui"))
        self.enterContext(patch_workbench("_config_values",
                                       new=AsyncMock(side_effect=[before, after])))
        self.enterContext(patch.object(run, "io_bound",
                                       new=AsyncMock(return_value={})))
        control_for = self.enterContext(patch.object(settings_page, "control_for"))
        await all_settings._setting_entries(context, [("", "", [field])])

        await press(control_for.call_args.args[2], True)
        await asyncio.sleep(0)

        rebuild.assert_not_awaited()
        return saved

    async def test_a_setting_that_becomes_the_table_s_own(self) -> None:
        saved = await self._saved_after(
            {"Player.PlayMusic": {"value": "0", "scope": ""}},
            {"Player.PlayMusic": {"value": "1", "scope": "entry"}})

        saved.assert_awaited_once()

    async def test_one_that_stops_being_it(self) -> None:
        saved = await self._saved_after(
            {"Player.PlayMusic": {"value": "0", "scope": "entry"}},
            {"Player.PlayMusic": {"value": "1", "scope": "launcher"}})

        saved.assert_awaited_once()

    async def test_a_value_changed_where_the_table_already_sets_it_leaves_the_row(
            self) -> None:
        saved = await self._saved_after(
            {"Player.PlayMusic": {"value": "0", "scope": "entry"}},
            {"Player.PlayMusic": {"value": "1", "scope": "entry"}})

        saved.assert_not_awaited()


class TableWriteReadsAgainTests(unittest.TestCase):
    """Each table the Manager read carries how many settings it has of its own."""

    def _library(self) -> tuple[data.Library, Mock]:
        client = Mock()
        client.all_tables.return_value = []
        client.tables.return_value = []
        library = data.Library(client)
        library.load_tables()
        library.tables_for("game")
        return library, client

    def test_a_write_at_a_table_reads_them_again(self) -> None:
        library, client = self._library()

        library.write_launcher_config("probe", {"Player.PlayMusic": "1"}, table="t1",
                                      scope="entry")
        library.load_tables()
        library.tables_for("game")

        self.assertEqual(2, client.all_tables.call_count)
        self.assertEqual(2, client.tables.call_count)

    def test_a_write_for_all_tables_does_not(self) -> None:
        library, client = self._library()

        library.write_launcher_config("probe", {"Player.PlayMusic": "1"})
        library.load_tables()
        library.tables_for("game")

        self.assertEqual(1, client.all_tables.call_count)
        self.assertEqual(1, client.tables.call_count)


def _said(entry) -> str:
    with patch("manager.kit.panel.ui") as ui:
        entry[1]()
    return str(ui.label.call_args.args[0])


class AddressTests(unittest.TestCase):
    """A reload lands on the launcher that was open, not on the empty panel."""

    def test_the_open_launcher_is_in_the_address(self) -> None:
        address = parse_qs(deeplink.query({"view": "launchers", "launcher": "second-vpx"}))

        self.assertEqual(address["launcher"], ["second-vpx"])

    def test_it_is_read_back_from_one(self) -> None:
        state: dict = {"view": "launchers"}

        deeplink.apply(state, {"view": "launchers", "launcher": "second-vpx"},
                       views=["launchers"], sections=[])

        self.assertEqual(state["launcher"], "second-vpx")

    def test_it_is_noise_anywhere_else(self) -> None:
        address = parse_qs(deeplink.query({"view": "games", "launcher": "second-vpx"}))

        self.assertNotIn("launcher", address)

    def test_leaving_the_page_lets_go_of_it(self) -> None:
        state = {"view": "launchers", "launcher": "second-vpx", "game": "", "table": ""}

        with patch.object(page.remembered, "put"):
            page.leave_for(state, "games")

        self.assertFalse(state["launcher"])

    def test_the_tables_grid_carries_a_launcher_and_a_setting(self) -> None:
        address = parse_qs(deeplink.query({"view": "tables", "launcher": "vpx",
                                           "sets": "Player.PlayMusic"}))

        self.assertEqual((address["launcher"], address["sets"]),
                         (["vpx"], ["Player.PlayMusic"]))

    def test_a_setting_is_read_back_as_written(self) -> None:
        state: dict = {"view": "tables"}

        deeplink.apply(state, {"view": "tables", "launcher": "vpx",
                               "sets": "Player.PlayMusic"}, views=["tables"], sections=[])

        self.assertEqual((state["launcher"], state["sets"]), ("vpx", "Player.PlayMusic"))

    def test_a_setting_is_noise_anywhere_else(self) -> None:
        address = parse_qs(deeplink.query({"view": "games", "sets": "Player.PlayMusic"}))

        self.assertNotIn("sets", address)

    def test_leaving_the_grid_lets_go_of_a_setting(self) -> None:
        state = {"view": "tables", "sets": "Player.PlayMusic", "game": "", "table": ""}

        with patch.object(page.remembered, "put"):
            page.leave_for(state, "games")

        self.assertFalse(state["sets"])


class OwnSettingsColumnTests(unittest.TestCase):
    """Which settings a table sets differently, and the grid arriving on the tables of one
    launcher that set one."""

    ROW = {"id": "t", "launcher": "vpx", "launcher_name": "Visual Pinball X",
           "launcher_app_configurable": True}

    def _own(self, **row: object) -> list[str]:
        (built,) = games.table_rows([{**self.ROW, **row}])
        return built[games.OWN_SETTINGS_COLUMN]

    def test_a_row_holds_the_settings_by_key(self) -> None:
        self.assertEqual(self._own(launcher_settings_keys=["Player.PlayMusic"]),
                         ["Player.PlayMusic"])

    def test_a_program_that_keeps_no_settings_holds_none(self) -> None:
        self.assertEqual(self._own(launcher_app_configurable=False,
                                   launcher_settings_keys=["Player.PlayMusic"]), [])

    def test_it_is_a_list_column_named_by_setting_in_no_view(self) -> None:
        column = next(one for one in games.TABLE_COLUMNS
                      if one["field"] == games.OWN_SETTINGS_COLUMN)

        self.assertEqual(column["filterParams"]["looks"], renderers.SETTING_LOOKS)
        self.assertIn(renderers.SETTING_LOOKS, renderers.LOOKS)
        for name, preset in games.TABLE_VIEWS.items():
            with self.subTest(view=name):
                self.assertNotIn(games.OWN_SETTINGS_COLUMN,
                                 getattr(preset, "columns", cast(Any, preset)))

    def test_an_address_arrives_on_the_launcher_s_tables_that_set_it(self) -> None:
        self.assertEqual(
            games.setting_their_own([self.ROW], "vpx", "Player.PlayMusic"),
            {"launcher": {"filterType": "text", "operator": "OR", "conditions": [
                {"filterType": "text", "type": "equals", "filter": "Visual Pinball X"},
                {"filterType": "text", "type": "equals",
                 "filter": f"{games.SET_HERE_MARK}Visual Pinball X"}]},
             games.OWN_SETTINGS_COLUMN: {"values": ["Player.PlayMusic"]}})

    def test_one_naming_no_launcher_a_table_uses_or_no_setting_asks_for_nothing(
            self) -> None:
        for launcher, key in (("other", "Player.PlayMusic"), ("vpx", ""),
                              ("", "Player.PlayMusic")):
            with self.subTest(launcher=launcher, key=key):
                self.assertIsNone(games.setting_their_own([self.ROW], launcher, key))


class SettingNamesTests(unittest.TestCase):
    """What a setting is called on a grid, away from the area that explains it."""

    def test_a_label_no_other_setting_has_is_its_name(self) -> None:
        self.assertEqual(
            all_settings.setting_names([_group("sound", _setting("Player.MusicVolume",
                                                              "Volume"))]),
            {"Player.MusicVolume": "Volume"})

    def test_a_shared_label_is_led_by_its_heading(self) -> None:
        names = all_settings.setting_names([_group(
            "displays", _setting("Player.PlayfieldWidth", "Width"),
            _setting("Backglass.BackglassWidth", "Width"),
            curated=[_heading("playfield", "Player.PlayfieldWidth"),
                     _heading("backglass", "Backglass.BackglassWidth")])])

        self.assertEqual(names, {"Player.PlayfieldWidth": "Playfield Width",
                                 "Backglass.BackglassWidth": "Backglass Width"})

    def test_a_window_s_setting_is_led_by_its_window_though_nothing_shares_it(
            self) -> None:
        names = all_settings.setting_names([_group(
            "more", _setting("PlayerVR.PreviewDisplay", "Display"),
            _setting("PlayerVR.PreviewWidth", "Width"), _setting("Player.Shadows", "Shadows"),
            curated=[_heading("vr_preview", "PlayerVR.PreviewDisplay", "PlayerVR.PreviewWidth",
                              label="VR Preview")])])

        self.assertEqual(names, {
            "PlayerVR.PreviewDisplay": t("manager.app_settings.window_row",
                                         window="VR Preview", label="Display"),
            "PlayerVR.PreviewWidth": t("manager.app_settings.window_row",
                                       window="VR Preview", label="Width"),
            "Player.Shadows": "Shadows"})

    def test_a_pair_s_setting_is_led_by_its_pair_where_its_window_s_name_is_shared(
            self) -> None:
        mode, size = ("Topper.TopperFSWidth",), ("Topper.TopperWidth",)
        names = all_settings.setting_names([_group(
            "displays", _setting(*mode, "Width"), _setting(*size, "Width"),
            _setting("Topper.TopperDisplay", "Display"),
            curated=[_heading("topper", *mode, *size, "Topper.TopperDisplay",
                              label="Topper", pairs=[_pair("video_mode", "Video Mode", *mode),
                                                     _pair("size", "Size", *size)])])])

        def led(pair: str) -> str:
            return t("manager.app_settings.heading_row", label="Width",
                     heading=t("manager.app_settings.window_row", window="Topper", label=pair))

        self.assertEqual(names, {
            "Topper.TopperFSWidth": led("Video Mode"), "Topper.TopperWidth": led("Size"),
            "Topper.TopperDisplay": t("manager.app_settings.window_row", window="Topper",
                                      label="Display")})

    def test_or_by_its_section_where_no_heading_holds_it(self) -> None:
        names = all_settings.setting_names([_group(
            "displays", _setting("Player.PlayfieldWidth", "Width"),
            _setting("Backglass.BackglassWidth", "Width"))])

        self.assertEqual(names, {"Player.PlayfieldWidth": "Player Width",
                                 "Backglass.BackglassWidth": "Backglass Width"})

    def test_a_translation_orders_the_heading_and_the_label(self) -> None:
        self.addCleanup(i18n.set_language, i18n.language())
        i18n.set_language("xx")
        width = (_setting("Player.PlayfieldWidth", "Width"),
                 _setting("Backglass.BackglassWidth", "Width"))
        headed = [_heading("playfield", "Player.PlayfieldWidth"),
                  _heading("backglass", "Backglass.BackglassWidth")]
        with patch.dict(i18n._catalogs, {"xx": {
                "manager.app_settings.heading_row": "{label} ({heading})"}}):
            names = [all_settings.setting_names([_group("displays", *width, curated=curated)])
                     for curated in (headed, ())]

        self.assertEqual(names, [
            {"Player.PlayfieldWidth": "Width (Playfield)",
             "Backglass.BackglassWidth": "Width (Backglass)"},
            {"Player.PlayfieldWidth": "Width (Player)",
             "Backglass.BackglassWidth": "Width (Backglass)"}])

    def test_a_plugin_s_setting_is_led_by_its_plugin_as_a_table_s_settings_lead_it(
            self) -> None:
        names = all_settings.setting_names([_group(
            "plugins", _setting("Plugin.PUP.Enable", "Enable"),
            _setting("Plugin.PUP.MainVol", "Main Volume"),
            _setting("Plugin.DOF.Enable", "Enable"),
            curated=[SimpleNamespace(key="PUP", label="Pin Up Player", note="",
                                     keys=("Plugin.PUP.Enable", "Plugin.PUP.MainVol"),
                                     enabled_by="Plugin.PUP.Enable")])])

        self.assertEqual(names, {
            "Plugin.PUP.Enable": t("manager.app_settings.plugin_row",
                                   plugin="Pin Up Player", label="Enable"),
            "Plugin.PUP.MainVol": t("manager.app_settings.plugin_row",
                                    plugin="Pin Up Player", label="Main Volume"),
            "Plugin.DOF.Enable": t("manager.app_settings.plugin_row", plugin="DOF",
                                   label="Enable")})


class _Inline:
    """A `panel.region` that draws where it is asked, for a test with no page to draw on."""

    def __init__(self, build: Any) -> None:
        self.build = build

    def __call__(self) -> None:
        return None

    async def refilled(self) -> None:
        await self.build()


class CatalogLabelTests(unittest.IsolatedAsyncioTestCase):
    PIN2DMD = SimpleNamespace(key="Plugin.DMDUtil.PIN2DMD", type="bool", label="PIN2DMD",
                              default="", description="", choices=(), scopes=("launcher",),
                              help="")

    async def test_pin2dmd_is_not_cased_as_a_word(self) -> None:
        curated = _group("plugins", self.PIN2DMD,
                         curated=[_heading("dmdutil", self.PIN2DMD.key, label="DMDUtil")])
        for group in (curated, _group("plugins", self.PIN2DMD)):
            with self.subTest(curated=bool(group.curated)):
                context = {"library": Mock(), "config_scope": "launcher", "config_table": "",
                           "launcher": {"launcher_id": "probe", "app_name": "VPX"},
                           "rebuild": AsyncMock(), "config_groups": [group]}
                with patch_workbench("ui"), \
                        patch_workbench("_config_values",
                                     new=AsyncMock(return_value={})), \
                        patch_workbench("_set_by_tables",
                                     new=AsyncMock(return_value=[])), \
                        patch.object(settings_page, "control_for"), \
                        patch.object(panel, "search"), \
                        patch.object(panel, "action"), \
                        patch.object(panel, "region", new=_Inline), \
                        patch.object(panel, "facts") as facts:
                    await all_settings._config_rows(context, group)

                self.assertIn("PIN2DMD", [label for label, _ in facts.call_args.args[1]])


class TablesSetTheirOwnTests(unittest.IsolatedAsyncioTestCase):
    """A launcher's row says how many of its tables answer over it, and goes to them."""

    async def test_it_counts_the_launcher_s_tables_by_setting(self) -> None:
        library = Mock()
        library.load_tables.return_value = [
            {"launcher": "vpx", "launcher_settings_keys": ["Player.PlayMusic", "Player.FXAA"]},
            {"launcher": "vpx", "launcher_settings_keys": ["Player.PlayMusic"]},
            {"launcher": "other", "launcher_settings_keys": ["Player.PlayMusic"]},
            {"launcher": "vpx"}]
        self.enterContext(patch.object(offload, "io",
                                       new=AsyncMock(side_effect=lambda call: call())))

        held = await all_settings._set_by_tables(
            {"library": library, "launcher": {"launcher_id": "vpx"}})

        self.assertEqual([all_settings._tables_setting(held, [key])
                          for key in ("Player.PlayMusic", "Player.FXAA", "Player.ShowFPS")],
                         [2, 1, 0])

    def test_a_table_setting_both_of_a_pair_counts_once(self) -> None:
        held = [frozenset({"Player.PlayfieldWidth", "Player.PlayfieldHeight"}),
                frozenset({"Player.PlayfieldHeight"}), frozenset({"Player.FXAA"})]

        self.assertEqual(all_settings._tables_setting(
            held, ["Player.PlayfieldWidth", "Player.PlayfieldHeight"]), 2)

    def test_its_link_goes_to_the_tables_grid_on_them(self) -> None:
        for count, said in ((1, "1 table sets its own"), (4, "4 tables set their own")):
            with self.subTest(count=count), patch("manager.kit.panel.ui") as ui:
                all_settings._tables_of_their_own({"launcher_id": "vpx"}, ["Player.PlayMusic"],
                                               count)()
                address = parse_qs(ui.link.call_args.kwargs["target"].split("?", 1)[1])

                self.assertEqual(ui.link.call_args.args[0], said)
                self.assertEqual(address, {"view": ["tables"], "launcher": ["vpx"],
                                           "sets": ["Player.PlayMusic"]})

    def test_a_pair_s_link_asks_for_tables_setting_either(self) -> None:
        with patch("manager.kit.panel.ui") as ui:
            all_settings._tables_of_their_own(
                {"launcher_id": "vpx"}, ["Player.PlayfieldWndX", "Player.PlayfieldWndY"], 2)()
        address = parse_qs(ui.link.call_args.kwargs["target"].split("?", 1)[1])
        arriving = games.setting_their_own(
            [{"launcher": "vpx", "launcher_name": "Visual Pinball X"}], "vpx",
            address["sets"][0])

        assert arriving is not None
        self.assertEqual(arriving[games.OWN_SETTINGS_COLUMN],
                         {"values": ["Player.PlayfieldWndX", "Player.PlayfieldWndY"]})

    async def _beyond(self, scope: str, table: str = "") -> list[object]:
        context = {"library": Mock(), "launcher": {"launcher_id": "vpx"},
                   "config_scope": scope, "config_table": table, "rebuild": AsyncMock()}
        self.enterContext(patch_workbench("ui"))
        self.enterContext(patch_workbench("_config_values",
                                       new=AsyncMock(return_value={})))
        self.enterContext(patch.object(settings_page, "control_for"))
        self.counted = self.enterContext(patch_workbench("_set_by_tables",
            new=AsyncMock(return_value=[frozenset({"Player.PlayMusic"})] * 2)))
        beside = self.enterContext(patch_workbench("_beside"))
        await all_settings._setting_entries(
            context, [("", "", [_field("Player.PlayMusic", scopes=("launcher", "entry")),
                                _field("Player.FXAA", scopes=("launcher", "entry"))])])
        return [call.args[5] for call in beside.call_args_list]

    async def test_a_row_for_all_tables_carries_it_where_a_table_sets_its_own(
            self) -> None:
        link, none = await self._beyond("launcher")

        self.assertIsNotNone(link)
        self.assertIsNone(none)

    async def test_a_table_s_rows_do_not_ask(self) -> None:
        self.assertEqual(await self._beyond("entry", "t1"), [None, None])
        self.counted.assert_not_awaited()


class SettingGroupsTests(unittest.TestCase):
    """The names a grid gives settings come from one read per launcher that plays a table."""

    GROUPS = {"groups": [{"key": "sound", "label": "Sound", "settings": [
        {"key": "Player.PlayMusic", "label": "Music", "type": "bool", "default": "",
         "description": ""}]}]}

    def _library(self, *answers: object) -> tuple[data.Library, Mock]:
        client = Mock()
        client.all_tables.return_value = [
            {"app": "vpx", "launcher": "a", "launcher_app_configurable": True},
            {"app": "vpx", "launcher": "b", "launcher_app_configurable": True},
            {"app": "other", "launcher": "c", "launcher_app_configurable": False}]
        client.launcher_config.side_effect = answers
        return data.Library(client), client

    def _read(self, client: Mock) -> list[str]:
        return [call.args[0] for call in client.launcher_config.call_args_list]

    def test_one_read_per_launcher_of_a_program_that_keeps_settings(self) -> None:
        library, client = self._library(self.GROUPS, self.GROUPS)

        library.load_tables()

        self.assertEqual(self._read(client), ["a", "b"])
        self.assertEqual([one.key for one in library.setting_groups()["b"]], ["sound"])

    def test_a_launcher_that_cannot_say_leaves_the_others_named(self) -> None:
        library, client = self._library(RuntimeError("gone"), self.GROUPS)

        library.load_tables()

        self.assertEqual(library.setting_groups()["a"], [])
        self.assertEqual([one.key for one in library.setting_groups()["b"]], ["sound"])

    def test_the_table_list_read_again_does_not_read_them_again(self) -> None:
        library, client = self._library(RuntimeError("gone"), self.GROUPS)
        library.load_tables()

        library.write_launcher_config("a", {"Player.PlayMusic": "1"}, table="t1",
                                      scope="entry")
        library.load_tables()

        self.assertEqual(client.all_tables.call_count, 2)
        self.assertEqual(self._read(client), ["a", "b"])


if __name__ == "__main__":
    unittest.main()
