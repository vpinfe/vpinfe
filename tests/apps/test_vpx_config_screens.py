"""What Visual Pinball's settings file takes for the screens answer."""

from __future__ import annotations

import itertools
import unittest
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from unittest import mock

from apps.vpx import ini as vini
from apps.vpx import screens as vs
from apps.vpx.config import preferences_file
from common.apps.contract import SCOPE_ENTRY, SCOPE_LAUNCHER, Program, TableOrder
from tests.apps.test_vpx_config import APP_INI, _Case

LAUNCHER = SCOPE_LAUNCHER


@dataclass(frozen=True)
class Monitor:
    number: int
    name: str
    width: int
    height: int
    x: int
    y: int


def _ticking() -> AbstractContextManager[Any]:
    """The clock `apps.vpx.config` stamps an aside from, a second later at every look."""
    ticks = itertools.count()
    kwargs: dict[str, Any] = {
        "now.side_effect": lambda tz=None: datetime(2026, 10, 2, 12, 0, next(ticks), tzinfo=tz)}
    return mock.patch("apps.vpx.config.datetime", **kwargs)


class KeepingTheFirstTests(_Case):
    def asides(self) -> list:
        return sorted(self.root.glob("VPinballX.ini.vpinfe-*"))

    def test_the_first_write_copies_the_file_as_it_was(self) -> None:
        self.config.write(LAUNCHER, "", {"Backglass.BackglassOutput": "0"}, self.settings)

        (kept,) = self.asides()
        self.assertEqual(kept.read_text(), APP_INI)
        self.assertIn("BackglassOutput = 0", self.app_ini.read_text())

    def test_the_stamp_is_utc_and_has_no_colons(self) -> None:
        self.config.write(LAUNCHER, "", {"Backglass.BackglassOutput": "0"}, self.settings)

        (kept,) = self.asides()
        self.assertRegex(kept.name, r"VPinballX\.ini\.vpinfe-\d{8}T\d{6}Z$")

    def test_a_file_there_already_written_twice_leaves_one_aside_of_the_original(self) -> None:
        with _ticking():
            self.config.write(LAUNCHER, "", {"Backglass.BackglassOutput": "0"}, self.settings)
            self.config.write(LAUNCHER, "", {"Backglass.BackglassOutput": "2"}, self.settings)

        kept = self.asides()
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].read_text(), APP_INI)
        self.assertIn("BackglassOutput = 2", self.app_ini.read_text())

    def test_a_table_s_file_is_not_copied_and_does_not_count(self) -> None:
        self.config.write(SCOPE_ENTRY, str(self.table), {"DMD.Profile1Legacy": "0"},
                          self.settings)

        self.assertEqual(self.asides(), [])
        self.assertEqual(sorted(self.game.glob("*.vpinfe-*")), [])

    def test_a_program_with_no_file_yet_is_refused_and_copies_nothing(self) -> None:
        """Which version folder the program will use is not known here, so a file that
        has not been made is not made by a write."""
        with mock.patch("apps.vpx.config._machine_folders", return_value=(None, None)):
            with self.assertRaises(ValueError):
                self.config.write(LAUNCHER, "", {"Backglass.BackglassOutput": "0"},
                                  {"bin_path": str(self.root / "bin" / "VPinballX_BGFX")})

        self.assertEqual(self.asides(), [])

    def test_a_file_named_for_the_launcher_is_made_by_the_first_write_with_an_empty_aside(
            self) -> None:
        made = self.root / "new" / "VPinballX.ini"

        with _ticking():
            self.config.write(LAUNCHER, "", {"Backglass.BackglassOutput": "1"},
                              {"ini_path": str(made)})
            (kept,) = sorted(made.parent.glob("*.vpinfe-*"))
            self.config.write(LAUNCHER, "", {"Backglass.BackglassOutput": "2"},
                              {"ini_path": str(made)})

        self.assertIn("BackglassOutput = 2", made.read_text())
        self.assertEqual(kept.read_text(), "")
        self.assertEqual(sorted(made.parent.glob("*.vpinfe-*")), [kept])


class MakingTheFileTests(_Case):
    """A program that has never run has no file. Which version it is must be said."""

    def setUp(self) -> None:
        super().setUp()
        self.data = self.root / "data" / "VPinballX"
        self.enterContext(mock.patch("apps.vpx.config._machine_folders",
                                     return_value=(self.data, None)))
        self.bare = {"bin_path": str(self.root / "bin" / "VPinballX_BGFX")}

    def test_the_file_a_version_looks_for_is_in_its_own_folder(self) -> None:
        self.assertEqual(preferences_file("10.8"), self.data / "10.8" / "VPinballX.ini")

    def test_a_version_that_is_not_major_dot_minor_names_no_folder(self) -> None:
        for said in ("", "10", "10.8.1", "latest", "../10.8"):
            with self.subTest(said=said):
                self.assertIsNone(preferences_file(said))

    def test_no_preferences_folder_names_no_file(self) -> None:
        with mock.patch("apps.vpx.config._machine_folders", return_value=(None, None)):
            self.assertIsNone(preferences_file("10.8"))

    def test_any_keys_make_the_file_holding_only_those(self) -> None:
        made = self.config.create("10.8", {"Player.BGSet": "1"}, self.bare)

        self.assertEqual(made, self.data / "10.8" / "VPinballX.ini")
        self.assertEqual(vini.parse(made.read_text()).settings.keys(), {"Player.BGSet"})
        self.assertEqual(self.config.read(LAUNCHER, "", self.bare)["Player.BGSet"].value, "1")

    def test_the_screens_answer_makes_it_the_same_way(self) -> None:
        screen = Monitor(1, "SAMSUNG", 1080, 1920, 0, 0)
        written = vs.answer({"playfield": 1}, {"playfield": 1}, [screen], [], {},
                            turn=("portrait", "0"))

        made = self.config.create("10.8", written.keys, self.bare)

        values = self.config.read(LAUNCHER, "", self.bare)
        self.assertEqual(values["Player.PlayfieldDisplay"].value, "SAMSUNG [0, 0]")
        self.assertEqual(vini.parse(made.read_text()).settings.keys(), set(written.keys))

    def test_a_file_made_here_has_one_empty_aside_however_often_it_is_written(self) -> None:
        with _ticking():
            made = self.config.create("10.8", {"Player.BGSet": "1"}, self.bare)
            self.config.write(LAUNCHER, "", {"Player.BGSet": "2"}, self.bare)
            self.config.write(LAUNCHER, "", {"Player.BGSet": "3"}, self.bare)

        (kept,) = sorted(made.parent.glob("VPinballX.ini.vpinfe-*"))
        self.assertRegex(kept.name, r"VPinballX\.ini\.vpinfe-\d{8}T\d{6}Z$")
        self.assertEqual(kept.read_text(), "")
        self.assertIn("BGSet = 3", made.read_text())

    def test_nothing_made_leaves_no_aside(self) -> None:
        made = self.config.create("10.8", {}, self.bare)

        self.assertFalse(made.exists())
        self.assertEqual(sorted(made.parent.glob("*.vpinfe-*")), [])

    def test_a_file_there_already_is_written_in_place_and_kept_first(self) -> None:
        made = self.config.create("10.8", {"Player.BGSet": "1"}, self.bare)
        (aside,) = sorted(made.parent.glob("VPinballX.ini.vpinfe-*"))

        with _ticking():
            again = self.config.create("10.9", {"Player.BGSet": "2"}, self.bare)

        self.assertEqual(again, made)
        self.assertFalse((self.data / "10.9").exists())
        self.assertIn("BGSet = 2", made.read_text())
        self.assertEqual(sorted(made.parent.glob("VPinballX.ini.vpinfe-*")), [aside])

    def test_a_launcher_file_named_and_not_yet_there_is_made_with_an_empty_aside(self) -> None:
        named = self.root / "mine" / "VPinballX.ini"

        with _ticking():
            self.config.create("10.8", {"Player.BGSet": "1"}, {**self.bare, "ini_path": str(named)})
            self.config.write(LAUNCHER, "", {"Player.BGSet": "2"}, {"ini_path": str(named)})

        (kept,) = sorted(named.parent.glob("VPinballX.ini.vpinfe-*"))
        self.assertEqual(kept.read_text(), "")

    def test_a_launcher_that_names_its_file_gets_it_there_and_no_other(self) -> None:
        named = self.root / "mine" / "VPinballX.ini"

        made = self.config.create("10.8", {"Player.BGSet": "1"}, {**self.bare,
                                                                  "ini_path": str(named)})

        self.assertEqual(made, named)
        self.assertTrue(named.is_file())
        self.assertFalse(self.data.exists())

    def test_the_launcher_is_not_told_where_the_file_went(self) -> None:
        settings = dict(self.bare)

        self.config.create("10.8", {"Player.BGSet": "1"}, settings)

        self.assertEqual(settings, self.bare)

    def test_a_version_that_names_no_folder_makes_nothing_and_says_so(self) -> None:
        with self.assertRaises(ValueError):
            self.config.create("soon", {"Player.BGSet": "1"}, self.bare)

        self.assertFalse(self.data.exists())

    def test_a_write_still_refuses_where_there_is_no_file(self) -> None:
        with self.assertRaises(ValueError):
            self.config.write(LAUNCHER, "", {"Player.BGSet": "1"}, self.bare)

        self.assertFalse(self.data.exists())


class TheKeysTheAnswerWritesTests(_Case):
    def offered(self, key: str) -> tuple[str, ...]:
        return self.config.scopes_for(key)

    def test_the_turn_is_offered_at_the_launcher_and_the_rest_of_its_section_is_not(self) -> None:
        self.assertIn(LAUNCHER, self.offered("TableOverride.ViewCabRotation"))
        self.assertNotIn(LAUNCHER, self.offered("TableOverride.ViewCabFOV"))

    def test_the_render_priorities_are_read_though_never_offered(self) -> None:
        self.app_ini.write_text(APP_INI + "\n[ScoreView]\nPriority.PUP = 4\n")

        values = self.config.read(LAUNCHER, "", self.settings)
        offered = {f.key for g in self.config.groups(self.settings) for f in g.settings}

        self.assertEqual(values["ScoreView.Priority.PUP"].value, "4")
        self.assertNotIn("ScoreView.Priority.PUP", offered)

    def test_every_key_the_answer_writes_can_be_written_at_the_launcher(self) -> None:
        keys = {*vs.DISPLAY.values(), *vs.OUTPUT.values(), vs.VIEW_CAB_ROTATION,
                *vs.PRIORITIES, *vs.FULL_DMD, "Plugin.DMDUtil.Enable",
                *(f"Plugin.DMDUtil.{kind}" for kind in vs.DMD_KINDS),
                *(key for region in vs.REGION.values() for key in region)}

        self.assertEqual({key for key in keys if LAUNCHER not in self.offered(key)}, set())

    def test_the_answer_reads_back_as_the_arrangement_it_was_made_from(self) -> None:
        wide = Monitor(1, "SAMSUNG", 2160, 3840, 0, 0)
        back = Monitor(2, "DELL P2419H", 1920, 1080, 2160, 0)
        score = Monitor(3, "CVT DVI", 1280, 390, 4080, 0)
        shown = [wide, back, score]
        arrangement = {"playfield": 1, "backglass": 2, "scoreview": 3}

        written = vs.answer(arrangement, arrangement, shown, [], {}, turn=("portrait", "0"))
        self.config.write(LAUNCHER, "", written.keys, self.settings)

        values = {key: one.value
                  for key, one in self.config.read(LAUNCHER, "", self.settings).items()}
        self.assertEqual(vs.placed(values, shown), {**arrangement, "topper": None})
        self.assertEqual(values["TableOverride.ViewCabRotation"], "0")
        self.assertEqual(values["ScoreView.Priority.PUP"], "3")
        self.assertEqual(values["Plugin.B2SLegacy.B2SHideGrill"], "1")
        self.assertIn("[Plugin.B2SLegacy]", self.app_ini.read_text())
        self.assertIn("[ScoreView]", self.app_ini.read_text())

    def test_a_part_in_the_playfield_reads_back_with_the_region_it_was_given(self) -> None:
        cab = Monitor(1, "SAMSUNG", 1080, 1920, 0, 0)
        arrangement: dict[str, vs.Placement] = {"playfield": 1, "scoreview": vs.INSIDE}

        written = vs.answer({"scoreview": vs.INSIDE}, arrangement, [cab], [], {})
        self.config.write(LAUNCHER, "", written.keys, self.settings)

        values = {key: one.value
                  for key, one in self.config.read(LAUNCHER, "", self.settings).items()}
        self.assertEqual(vs.placed(values, [cab])["scoreview"], vs.INSIDE)
        self.assertEqual([values[key] for key in vs.REGION["scoreview"]],
                         ["54", "1623", "972", "243"])

    def test_what_vpx_already_held_is_still_there_after_the_answer(self) -> None:
        self.app_ini.write_text(APP_INI + "\n[ScoreView]\nPriority.PUP = 9\n")
        shown = [Monitor(1, "SAMSUNG", 3840, 2160, 0, 0), Monitor(2, "CVT DVI", 1280, 390, 3840, 0)]
        arrangement = {"playfield": 1, "scoreview": 2}
        current = {key: one.value
                   for key, one in self.config.read(LAUNCHER, "", self.settings).items()}

        written = vs.answer(arrangement, arrangement, shown, [], current)
        self.config.write(LAUNCHER, "", written.keys, self.settings)

        self.assertEqual(self.config.read(LAUNCHER, "", self.settings)[
            "ScoreView.Priority.PUP"].value, "9")


class TheBackglassOrderTests(_Case):
    """What `backglass_order` says a table's own file should hold, and that writing it
    leaves the file the program reads the order from."""

    ORDER = ("\n[ScoreView]\nPriority.PUP = 3\nPriority.Nixie = 2\nPriority.B2S = 1\n"
             "\n[Plugin.B2S]\nEnable = 1\n")
    PROGRAM = Program(frozenset({"B2S", "Nixie", "PUP"}), {}, 6300, dmd_in_file=True)

    def setUp(self) -> None:
        super().setUp()
        self.app_ini.write_text(APP_INI + self.ORDER)
        patcher = mock.patch.object(type(self.config), "program", return_value=self.PROGRAM)
        patcher.start()
        self.addCleanup(patcher.stop)

    SWAPPED = {"ScoreView.Priority.B2S": "2", "ScoreView.Priority.Nixie": "1"}

    def order(self, in_file: bool, written: dict[str, str] | None = None) -> TableOrder:
        return self.config.backglass_order(str(self.table), self.settings, in_file, written)

    def put(self, order: TableOrder) -> None:
        self.config.write(SCOPE_ENTRY, str(self.table), order.write, self.settings)

    def test_a_file_that_places_a_dmd_swaps_b2s_and_nixie_in_the_table(self) -> None:
        got = self.order(True)

        self.assertEqual(got, TableOrder(write=self.SWAPPED, ours=self.SWAPPED))

    def test_written_to_the_table_it_is_read_back_and_the_launcher_is_untouched(self) -> None:
        first = self.order(True)
        self.put(first)

        mine = self.config.read(SCOPE_ENTRY, str(self.table), self.settings)
        self.assertEqual((mine["ScoreView.Priority.B2S"].value,
                          mine["ScoreView.Priority.Nixie"].value), ("2", "1"))
        self.assertEqual(self.app_ini.read_text(), APP_INI + self.ORDER)
        self.assertEqual(self.order(True, first.ours), TableOrder(ours=self.SWAPPED))

    def test_taking_the_dmd_out_clears_the_recorded_pair_and_the_file_goes_with_it(self) -> None:
        first = self.order(True)
        self.put(first)

        self.put(self.order(False, first.ours))

        self.assertFalse(self.table.with_suffix(".ini").exists())

    def test_a_pair_that_was_not_recorded_survives_the_dmd_going(self) -> None:
        self.put(self.order(True))

        self.assertEqual(self.order(False), TableOrder())
        self.assertTrue(self.table.with_suffix(".ini").exists())

    def test_a_recorded_pair_follows_the_launcher_when_its_ranks_change(self) -> None:
        first = self.order(True)
        self.put(first)
        self.app_ini.write_text(APP_INI + self.ORDER.replace("Nixie = 2", "Nixie = 5"))

        got = self.order(True, first.ours)

        self.assertEqual(got.write, {"ScoreView.Priority.B2S": "5",
                                     "ScoreView.Priority.Nixie": "1"})

    def test_a_table_that_follows_a_folder_file_is_left_alone(self) -> None:
        self.folder_file("[Backglass]\nBackglassOutput = 0\n")

        self.assertEqual(self.order(True), TableOrder())

    def test_a_table_that_turns_b2s_off_for_itself_is_left_alone(self) -> None:
        self.table.with_suffix(".ini").write_text("[Plugin.B2S]\nEnable = 0\n")

        self.assertEqual(self.order(True), TableOrder())

    def test_a_table_that_turns_b2s_on_for_itself_is_swapped_where_the_launcher_has_it_off(
            self) -> None:
        self.app_ini.write_text(APP_INI + self.ORDER.replace("Enable = 1", "Enable = 0"))
        self.assertEqual(self.order(True), TableOrder())
        self.table.with_suffix(".ini").write_text("[Plugin.B2S]\nEnable = 1\n")

        self.assertEqual(self.order(True).write, self.SWAPPED)

    def test_a_blank_switch_in_the_table_does_not_answer_over_the_launcher_s(self) -> None:
        self.table.with_suffix(".ini").write_text("[Plugin.B2S]\nEnable =\n")
        self.app_ini.write_text(APP_INI + self.ORDER.replace("Enable = 1", "Enable = 0"))
        self.assertEqual(self.order(True), TableOrder())

        self.app_ini.write_text(APP_INI + self.ORDER)

        self.assertEqual(self.order(True).write, self.SWAPPED)

    def test_the_switch_in_a_folder_file_is_not_read_for_a_table_with_a_file_of_its_own(
            self) -> None:
        """The program reads the one file, so the folder's switch does not reach this table."""
        self.table.with_suffix(".ini").write_text("[Player]\nFXAA = 1\n")
        self.folder_file("[Plugin.B2S]\nEnable = 0\n")

        self.assertEqual(self.order(True).write, self.SWAPPED)

    def test_settings_that_cannot_be_read_say_nothing_about_the_table(self) -> None:
        """Unknown is not wanted: the recorded pair stands (`ours` is None, not empty)."""
        mine = self.table.with_suffix(".ini")
        mine.write_text("[Plugin.B2S]\nEnable = 1\n")
        first = self.order(True)
        for unreadable in (mine, self.app_ini):
            with self.subTest(file=unreadable.name):
                unreadable.chmod(0)
                self.addCleanup(unreadable.chmod, 0o644)
                if self.readable(unreadable):
                    self.skipTest("the file is readable regardless of its mode")

                got = self.order(False, first.ours)

                self.assertEqual(got, TableOrder())
                self.assertIsNone(got.ours)
                unreadable.chmod(0o644)

    @staticmethod
    def readable(path: Any) -> bool:
        try:
            path.read_text()
        except OSError:
            return False
        return True

    def test_the_two_enable_switches_are_among_the_keys_a_table_write_settles_on(self) -> None:
        keys = self.config.order_keys()

        self.assertTrue({"Plugin.B2S.Enable", "Plugin.B2SLegacy.Enable",
                         "ScoreView.Priority.B2S", "ScoreView.Priority.Nixie"} <= set(keys))


if __name__ == "__main__":
    unittest.main()
