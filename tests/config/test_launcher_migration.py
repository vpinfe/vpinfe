"""The configuration an install already had, read out as launchers.

The case that matters is a 2.x cabinet with plugin profiles: it should come up with the
launcher it was running plus one per profile, each carrying the environment and overrides
it already had rather than a bare copy of Visual Pinball.
"""

import configparser
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast
from unittest.mock import patch

from common import apps
from common.config.config_store import ConfigStore
from common.games import launcher_migration, launchers
from common.games.game import Game


def _config(**values) -> configparser.ConfigParser:
    parser = configparser.ConfigParser()
    parser.add_section("Settings")
    for key, value in {"vpxbinpath": "/usr/bin/VPinballX_GL",
                       "vpxlaunchenv": "SDL_VIDEODRIVER=wayland",
                       **values}.items():
        parser.set("Settings", key, str(value))
    return parser


class SeedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.core = ConfigStore(os.path.join(self.tmp.name, "vpinfe.json"))
        self.profiles = os.path.join(self.tmp.name, "plugin_profiles")

    @property
    def store(self) -> launchers.LauncherStore:
        return launchers.LauncherStore(self.tmp.name)

    def _seed(self, config=None) -> None:
        for section in (config or _config()).sections():
            if not self.core.config.has_section(section):
                self.core.config.add_section(section)
            for key, value in (config or _config()).items(section):
                self.core.config.set(section, key, value)
        with patch.object(launcher_migration, "PLUGIN_PROFILES_DIR", Path(self.profiles)):
            launcher_migration.seed(self.core)
        self.core.save()

    def _profile(self, name: str) -> None:
        os.makedirs(self.profiles, exist_ok=True)
        with open(os.path.join(self.profiles, name), "w", encoding="utf-8") as handle:
            handle.write("[Plugin.DMD]\n")

    def test_an_install_gets_the_launcher_it_was_already_running(self) -> None:
        self._seed()

        held = self.store.launchers()
        self.assertEqual(len(held), 1)
        self.assertEqual(held[0].value("bin_path"), "/usr/bin/VPinballX_GL")
        self.assertEqual(held[0].value("launch_env"), "SDL_VIDEODRIVER=wayland")
        self.assertEqual(held[0].display_name, launcher_migration.SHIPPED_NAME)

    def test_a_2x_file_still_seeds_its_launcher(self) -> None:
        """The seven keys left the schema, so nothing resolves their old spellings any
        more. This pass has to know them itself, or an upgrading cabinet comes up with a
        launcher that has no program and no way to say what happened."""
        parser = configparser.ConfigParser()
        parser.add_section("Settings")
        parser.set("Settings", "vpxbinpath", "/opt/vpinball/VPinballX_GL")
        parser.set("Settings", "vpxinipath", "/home/cab/VPinballX.ini")
        parser.set("Settings", "vpxlogdeleteonstart", "true")
        parser.set("Settings", "globaltableinioverridemask", "windows")

        self._seed(parser)

        one = self.store.launchers()[0]
        self.assertEqual(one.value("bin_path"), "/opt/vpinball/VPinballX_GL")
        self.assertEqual(one.value("ini_path"), "/home/cab/VPinballX.ini")
        self.assertIs(one.value("log_delete_on_start"), True)
        self.assertNotIn("table_ini_override_mask", one.settings)
        self.assertTrue(self.core.config.has_option(
            "Settings", "globaltableinioverridemask"),
            "left for the pass that renames the per-table files")

    def test_a_3x_file_seeds_from_the_current_spellings(self) -> None:
        parser = configparser.ConfigParser()
        parser.add_section("general")
        parser.set("general", "vpx_bin_path", "/usr/bin/VPinballX_BGFX")
        parser.set("general", "vpx_ini_path", "/cfg/VPinballX.ini")
        parser.set("general", "global_ini_override", "/cfg/other.ini")

        self._seed(parser)

        one = self.store.launchers()[0]
        self.assertEqual(one.value("bin_path"), "/usr/bin/VPinballX_BGFX")
        self.assertEqual(one.value("ini_path"), "/cfg/other.ini",
                         "the override was the file Visual Pinball started with")
        self.assertNotIn("ini_override", one.settings)
        self.assertFalse(self.core.config.has_option("general", "vpx_bin_path"))

    def test_an_install_with_nothing_configured_still_gets_one(self) -> None:
        """A launcher with no binary is something a person can fix from the Manager. No
        launcher at all is a machine with nothing to point at."""
        self._seed(configparser.ConfigParser())

        self.assertEqual([one.app for one in self.store.launchers()], ["vpx"])

    def test_each_plugin_profile_becomes_a_launcher(self) -> None:
        self._profile("no-dmd.ini")
        self._profile("Loud.ini")

        self._seed()

        held = self.store.launchers()
        self.assertEqual([one.display_name for one in held],
                         [launcher_migration.SHIPPED_NAME, "Loud", "no-dmd"])

    def test_a_profile_named_like_the_shipped_launcher_gets_the_next_name(self) -> None:
        self._profile(f"{launcher_migration.SHIPPED_NAME.upper()}.ini")

        self._seed()

        self.assertEqual(self.store.launchers()[1].display_name,
                         f"{launcher_migration.SHIPPED_NAME.upper()} 2")

    def test_a_profile_keeps_what_the_install_was_already_set_to(self) -> None:
        """A profile was a copy of the ini, not a different Visual Pinball. Dropping the
        environment would leave it launching differently in ways nobody asked for."""
        self._profile("no-dmd.ini")

        self._seed()

        profile = self.store.launchers()[1]
        self.assertEqual(profile.value("bin_path"), "/usr/bin/VPinballX_GL")
        self.assertEqual(profile.value("launch_env"), "SDL_VIDEODRIVER=wayland")
        self.assertTrue(profile.value("ini_path").endswith("no-dmd.ini"))

    def test_only_the_profiles_are_claimed_as_ours_to_delete(self) -> None:
        """Removing a launcher offers to delete the ini it owns. The shipped one points
        at Visual Pinball's own file and must never be offered."""
        self._profile("no-dmd.ini")

        self._seed()

        shipped, profile = self.store.launchers()
        self.assertFalse(shipped.owns_ini)
        self.assertTrue(profile.owns_ini)

    def test_the_shipped_launcher_leads_so_it_is_the_default(self) -> None:
        self._profile("aaa-sorts-first.ini")

        self._seed()

        self.assertEqual(self.store.launchers()[0].display_name,
                         launcher_migration.SHIPPED_NAME)

    def test_a_file_that_is_not_an_ini_is_not_a_profile(self) -> None:
        self._profile("real.ini")
        os.makedirs(self.profiles, exist_ok=True)
        with open(os.path.join(self.profiles, "notes.txt"), "w",
                  encoding="utf-8") as handle:
            handle.write("hello")

        self._seed()

        self.assertEqual(len(self.store.launchers()), 2)

    def test_it_carries_exactly_what_the_app_declares(self) -> None:
        """A field left unseeded reads as its default, which for a path means the install
        quietly stopped using what it was configured with."""
        self._seed()
        declared = apps.launcher_fields("vpx")
        assert declared is not None

        self.assertEqual(sorted(self.store.launchers()[0].settings),
                         sorted(f.key for f in declared))

    def test_the_keys_it_read_are_consumed(self) -> None:
        """Seeding runs once because there is nothing left to read, and a launcher
        somebody deletes does not come back."""
        self._seed()

        self.assertFalse(self.core.config.has_option("Settings", "vpxbinpath"))
        self.assertFalse(self.core.config.has_option("Settings", "vpxlaunchenv"))


class _Game:
    """Enough of a game for the assignment pass: its folder name and its metadata."""

    def __init__(self, name: str, vpinfe: dict, tables: dict | None = None):
        self.game_dir_name = name
        self.meta_config = {"vpinfe": dict(vpinfe),
                            "tables": dict(tables or {"t-" + name: {"filename": "a.vpx"}})}


class AssignmentTests(unittest.TestCase):
    """A table's own override becomes a launcher and an assignment."""

    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = launchers.LauncherStore(self.tmp.name)
        self.store.save([launchers.Launcher(
            launcher_id="shipped", app="vpx",
            display_name=launcher_migration.SHIPPED_NAME,
            settings={"bin_path": "/usr/bin/vpx", "launch_env": "X=1"})], {})

    def _run(self, games):
        return launcher_migration.migrate_assignments(self.store, games)

    def test_a_table_with_nothing_set_is_left_alone(self) -> None:
        self._run([_Game("plain", {})])

        self.assertEqual(self.store.mappings(), {})
        self.assertEqual(len(self.store.launchers()), 1)

    def test_an_alt_launcher_becomes_a_launcher_and_an_assignment(self) -> None:
        self._run([_Game("vr", {"alt_launcher": "/opt/vpx-vr/VPinballX"})])

        held = self.store.launchers()
        self.assertEqual(len(held), 2)
        self.assertEqual(held[1].display_name, "VPinballX")
        self.assertEqual(self.store.mappings(), {"t-vr": held[1].launcher_id})

    def test_the_same_binary_twice_is_one_launcher(self) -> None:
        """Forty tables naming one alternative build is one launcher, not forty."""
        self._run([_Game("a", {"alt_launcher": "/opt/x/VPinballX"}),
                   _Game("b", {"alt_launcher": "/opt/x/VPinballX"})])

        self.assertEqual(len(self.store.launchers()), 2)
        self.assertEqual(len(set(self.store.mappings().values())), 1)

    def test_a_later_pass_reuses_the_launcher_an_earlier_one_made(self) -> None:
        """A table added after the first pass names the same program: it joins that
        launcher instead of making `VPinballX 2`."""
        self._run([_Game("a", {"alt_launcher": "/opt/x/VPinballX"})])
        self._run([_Game("b", {"alt_launcher": "/opt/x/VPinballX"})])

        held = self.store.launchers()
        self.assertEqual([one.display_name for one in held],
                         [launcher_migration.SHIPPED_NAME, "VPinballX"])
        self.assertEqual(self.store.mappings(),
                         {"t-a": held[1].launcher_id, "t-b": held[1].launcher_id})

    def test_a_table_naming_the_shipped_program_joins_the_shipped_launcher(self) -> None:
        self._run([_Game("a", {"alt_launcher": "/usr/bin/vpx"})])

        self.assertEqual(len(self.store.launchers()), 1)
        self.assertEqual(self.store.mappings(), {"t-a": "shipped"})

    def test_an_alt_launcher_keeps_the_rest_of_the_install_setup(self) -> None:
        """It only ever replaced the program. A bare launcher would drop the environment
        and the overrides the install was already launching with."""
        self._run([_Game("vr", {"alt_launcher": "/opt/x/VPinballX"})])

        made = self.store.launchers()[1]
        self.assertEqual(made.value("bin_path"), "/opt/x/VPinballX")
        self.assertEqual(made.value("launch_env"), "X=1")

    def _profile_launcher(self, name: str, file: str) -> None:
        self.store.put(launchers.Launcher(
            launcher_id="p1", app="vpx", display_name=name, owns_ini=True,
            settings={launcher_migration.PROFILE_FILE:
                      str(launcher_migration.PLUGIN_PROFILES_DIR / file)}))

    def test_a_plugin_profile_maps_to_the_launcher_seeding_made(self) -> None:
        self._profile_launcher("no-dmd", "no-dmd.ini")

        self._run([_Game("quiet", {"plugin_profile": "no-dmd"})])

        self.assertEqual(self.store.mappings(), {"t-quiet": "p1"})
        self.assertEqual(len(self.store.launchers()), 2, "no second launcher for it")

    def test_it_is_found_by_its_file_when_seeding_named_it_otherwise(self) -> None:
        self._profile_launcher("Visual Pinball X 2", "Visual Pinball X.ini")

        self._run([_Game("quiet", {"plugin_profile": "visual pinball x"})])

        self.assertEqual(self.store.mappings(), {"t-quiet": "p1"})

    def test_two_builds_with_one_file_name_get_two_names(self) -> None:
        self._run([_Game("a", {"alt_launcher": "/opt/one/VPinballX_GL"}),
                   _Game("b", {"alt_launcher": "/opt/two/VPinballX_GL"})])

        self.assertEqual([one.display_name for one in self.store.launchers()[1:]],
                         ["VPinballX_GL", "VPinballX_GL 2"])

    def test_a_build_named_like_a_launcher_already_there_gets_the_next_name(self) -> None:
        self._run([_Game("a", {"alt_launcher": "/opt/x/Visual Pinball X"})])

        self.assertEqual(self.store.launchers()[1].display_name, "Visual Pinball X 2")

    def test_a_profile_with_no_launcher_is_reported_and_left_on_the_default(self) -> None:
        """The ini was deleted, or never existed. Falling back is right; doing it
        silently is not."""
        with self.assertLogs("vpinfe.common.games.launcher_migration",
                             level="WARNING"):
            self._run([_Game("quiet", {"plugin_profile": "gone"})])

        self.assertEqual(self.store.mappings(), {})

    def test_every_table_in_the_game_is_assigned(self) -> None:
        """The override was written per game, and nothing records which of its tables it
        was for. Guessing one would be inventing a choice nobody made."""
        game = _Game("multi", {"alt_launcher": "/opt/x/VPinballX"},
                     tables={"t1": {"filename": "a.vpx"}, "t2": {"filename": "b.vpx"}})

        self._run([game])

        self.assertEqual(sorted(self.store.mappings()), ["t1", "t2"])

    def test_the_keys_are_taken_out_of_the_info(self) -> None:
        """Consumed rather than left inert: both are published to contract 1 themes, and
        a dead key in the theme API is a lie to third-party code. Contract 1 computes
        them from the resolver now."""
        game = _Game("vr", {"alt_launcher": "/opt/x/VPinballX",
                            "plugin_profile": "no-dmd"})
        written = []

        with patch("common.games.game_metadata.persist_game_meta",
                   side_effect=lambda g, c: written.append(c)):
            self._run([game])

        self.assertNotIn("alt_launcher", game.meta_config["vpinfe"])
        self.assertNotIn("plugin_profile", game.meta_config["vpinfe"])
        self.assertEqual(len(written), 1, "the file is rewritten without them")
        self.assertNotIn("alt_launcher", written[0]["vpinfe"])

    def test_a_game_that_had_none_is_not_rewritten(self) -> None:
        """Only the games that carried an override are written. A library-wide rewrite
        for nothing is a round trip per folder on a share."""
        written = []
        game = _Game("plain", {})

        with patch("common.games.game_metadata.persist_game_meta",
                   side_effect=lambda g, c: written.append(g)):
            self._run([game])

        self.assertEqual(written, [])

    def test_what_it_read_is_gone_so_a_second_pass_finds_nothing(self) -> None:
        game = _Game("vr", {"alt_launcher": "/opt/x/VPinballX"})
        self._run([game])
        self.store.assign("t-vr", "")

        self._run([game])

        self.assertEqual(self.store.mappings(), {})


class TablePatternTests(unittest.TestCase):
    """A 2.x per-table `{stem}.{mask}.ini` becomes the table's own `{stem}.ini`."""

    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.core = ConfigStore(os.path.join(self.tmp.name, "vpinfe.json"))
        self.core.config.add_section("Settings")
        self.game_dir = Path(self.tmp.name) / "Example (Maker 1990)"
        self.game_dir.mkdir()
        (self.game_dir / "Example.vpx").write_text("", encoding="utf-8")

    def _game(self) -> Game:
        game = _Game("Example (Maker 1990)", {},
                     {"t-example": {"filename": "Example.vpx"}})
        game.full_path_game = str(self.game_dir)  # type: ignore[attr-defined]
        return cast(Game, game)

    def _run(self, enabled: str = "true", mask: str = "windows") -> int:
        for key, value in (("globaltableinioverrideenabled", enabled),
                           ("globaltableinioverridemask", mask)):
            self.core.config.set("Settings", key, value)
        return launcher_migration.retire_table_pattern(self.core, [self._game()])

    def _file(self, name: str, text: str) -> Path:
        path = self.game_dir / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_the_patterned_file_becomes_the_tables_own(self) -> None:
        self._file("Example.windows.ini", "[Player]\nBallSize = 52\n")

        self.assertEqual(self._run(), 1)

        self.assertFalse((self.game_dir / "Example.windows.ini").exists())
        self.assertIn("BallSize", (self.game_dir / "Example.ini").read_text(encoding="utf-8"))

    def test_a_table_with_its_own_keeps_both_and_says_so(self) -> None:
        self._file("Example.windows.ini", "[Player]\nmasked = 1\n")
        self._file("Example.ini", "[Player]\nown = 1\n")

        with self.assertLogs("vpinfe.common.games.launcher_migration", "WARNING"):
            self.assertEqual(self._run(), 0)

        self.assertTrue((self.game_dir / "Example.windows.ini").exists())
        self.assertIn("own", (self.game_dir / "Example.ini").read_text(encoding="utf-8"))

    def test_nothing_moves_where_the_pattern_was_off(self) -> None:
        self._file("Example.windows.ini", "[Player]\n")

        self.assertEqual(self._run(enabled="false"), 0)

        self.assertFalse((self.game_dir / "Example.ini").exists())

    def test_the_keys_go_so_it_runs_once(self) -> None:
        self._run()
        self._file("Example.windows.ini", "[Player]\n")

        again = launcher_migration.retire_table_pattern(self.core, [self._game()])

        self.assertEqual(again, 0)
        self.assertFalse((self.game_dir / "Example.ini").exists())



class ConvertedIniTests(unittest.TestCase):
    """A 2.x file read by the config store: the seed runs inside the conversion."""

    def _converted(self, ini: str) -> tuple[ConfigStore, launchers.LauncherStore]:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        folder = Path(tmp.name)
        (folder / "vpinfe.ini").write_text(ini, encoding="utf-8")
        with patch.object(launcher_migration, "PLUGIN_PROFILES_DIR", folder / "none"):
            core = ConfigStore(str(folder / "vpinfe.json"))
        return core, launchers.LauncherStore(folder)

    def test_the_seven_keys_become_the_first_launcher_and_leave_the_file(self) -> None:
        core, store = self._converted(
            "[Settings]\nvpxbinpath = /opt/vpinball/VPinballX_GL\n"
            "vpxlogdeleteonstart = true\n")

        [one] = store.launchers()
        self.assertEqual(one.value("bin_path"), "/opt/vpinball/VPinballX_GL")
        self.assertIs(one.value("log_delete_on_start"), True)
        self.assertEqual(one.display_name, launcher_migration.SHIPPED_NAME)
        for section in core.config.sections():
            self.assertFalse(
                {"vpxbinpath", "vpx_bin_path", "vpxlogdeleteonstart"}
                & set(core.config.options(section)), section)

    def test_the_pattern_keys_are_still_found_to_rename_the_files(self) -> None:
        core, _ = self._converted(
            "[Settings]\nglobaltableinioverrideenabled = true\n"
            "globaltableinioverridemask = windows\n")

        self.assertEqual(launcher_migration.retire_table_pattern(core, []), 0)
        for section in core.config.sections():
            self.assertFalse(
                {"globaltableinioverridemask", "global_game_ini_override_mask"}
                & set(core.config.options(section)), section)

    def test_a_file_with_none_of_them_keeps_the_shipped_launcher(self) -> None:
        _, store = self._converted("[Settings]\ntablerootdir = /games\n")

        [one] = store.launchers()
        self.assertEqual(one.value("bin_path"), "")


if __name__ == "__main__":
    unittest.main()
