"""The asset model: what a table would use, and what the folder holds for whom.

The rules under test mirror VPX's own lookup code - GetSettingsFileName in
pintable.h, the B2SServer constructor, pinmame's Alias.cpp. A regression here means
we disagree with the engine about what a launch would load.
"""

from __future__ import annotations

import unittest

from common.games import asset_resolver as res

FOLDER = "Attack from Mars (Bally 1995)"
BIGUS = "Attack from Mars (Bally 1995) - bigus1 (1) - VPF_14317.vpx"
CYBER = "Attack from Mars (Bally 1995) - cyberpez (1) - VPF_14030.vpx"


class LaunchLensTests(unittest.TestCase):
    def test_a_point_of_view_beside_a_table_with_settings_is_not_in_use(self) -> None:
        """`pintable.cpp` imports a `.pov` only where the table has no settings file -
        point of view moved into the settings system and the file is legacy. Calling it
        in use is the worse of the two mistakes: somebody edits it and watches nothing
        happen, with the screen agreeing with them throughout."""
        files = [BIGUS, BIGUS.replace(".vpx", ".pov"), BIGUS.replace(".vpx", ".ini")]

        resolved = res.resolve_for_table(BIGUS, FOLDER, files)

        self.assertEqual(resolved["pov"]["resolution"], res.RESOLUTION_SUPERSEDED)
        self.assertEqual(resolved["pov"]["superseded_by"], "ini")
        self.assertEqual(resolved["pov"]["file"], BIGUS.replace(".vpx", ".pov"))

    def test_and_it_is_in_use_where_the_table_has_no_settings(self) -> None:
        files = [BIGUS, BIGUS.replace(".vpx", ".pov")]

        resolved = res.resolve_for_table(BIGUS, FOLDER, files)

        self.assertEqual(resolved["pov"]["resolution"], res.RESOLUTION_DEDICATED)

    def test_a_folder_ini_supersedes_it_too(self) -> None:
        """The ini that answers is whichever of the two spellings resolved."""
        files = [BIGUS, BIGUS.replace(".vpx", ".pov"), f"{FOLDER}.ini"]

        resolved = res.resolve_for_table(BIGUS, FOLDER, files)

        self.assertEqual(resolved["pov"]["resolution"], res.RESOLUTION_SUPERSEDED)

    def test_nothing_is_invented_where_there_is_no_point_of_view(self) -> None:
        files = [BIGUS, BIGUS.replace(".vpx", ".ini")]

        resolved = res.resolve_for_table(BIGUS, FOLDER, files)

        self.assertEqual(resolved["pov"]["resolution"], res.RESOLUTION_NONE)

    def test_a_folder_named_script_stands_in_for_a_table_that_has_none(self) -> None:
        """`pintable.cpp` looks for `<table>.vbs` and, failing that, builds
        `<folder>.vbs` from the parent directory's name and loads that. This read as
        absent while VPX was running it - and a script override is the one asset whose
        absence changes what the table actually does."""
        files = [BIGUS, f"{FOLDER}.vbs"]

        resolved = res.resolve_for_table(BIGUS, FOLDER, files)

        self.assertEqual(resolved["script"]["resolution"], res.RESOLUTION_SHARED)
        self.assertEqual(resolved["script"]["file"], f"{FOLDER}.vbs")

    def test_and_the_table_s_own_script_still_wins(self) -> None:
        stem = BIGUS[: -len(".vpx")]
        files = [BIGUS, f"{stem}.vbs", f"{FOLDER}.vbs"]

        resolved = res.resolve_for_table(BIGUS, FOLDER, files)

        self.assertEqual(resolved["script"]["resolution"], res.RESOLUTION_DEDICATED)
        self.assertEqual(resolved["script"]["file"], f"{stem}.vbs")


    def test_a_stem_named_asset_wins_over_the_folder_named_one(self) -> None:
        files = [BIGUS, "Attack from Mars (Bally 1995) - bigus1 (1) - VPF_14317.directb2s",
                 f"{FOLDER}.directb2s"]

        resolved = res.resolve_for_table(BIGUS, FOLDER, files)

        self.assertEqual(resolved["backglass"]["resolution"], "dedicated")
        self.assertIn("bigus1", resolved["backglass"]["file"])

    def test_a_table_without_its_own_asset_inherits_the_folder_named_one(self) -> None:
        """VPX's fallback: the folder-named file is shared between all builds."""
        files = [BIGUS, CYBER, f"{FOLDER}.directb2s"]

        resolved = res.resolve_for_table(CYBER, FOLDER, files)

        self.assertEqual(resolved["backglass"],
                         {"resolution": "shared", "file": f"{FOLDER}.directb2s"})

    def test_nothing_resolves_to_none(self) -> None:
        resolved = res.resolve_for_table(BIGUS, FOLDER, [BIGUS])

        for kind in ("backglass", "b2spro", "ini", "script", "pov", "scv", "nix"):
            self.assertEqual(resolved[kind], {"resolution": "none"})

    def test_matching_is_case_insensitive_like_vpx(self) -> None:
        files = [BIGUS, BIGUS.replace(".vpx", ".DirectB2S")]

        resolved = res.resolve_for_table(BIGUS, FOLDER, files)

        self.assertEqual(resolved["backglass"]["resolution"], "dedicated")

    def test_a_pov_never_falls_back_to_the_folder_name(self) -> None:
        """pintable.cpp auto-imports a stem-named .pov only - no folder fallback."""
        files = [BIGUS, f"{FOLDER}.pov"]

        resolved = res.resolve_for_table(BIGUS, FOLDER, files)

        self.assertEqual(resolved["pov"], {"resolution": "none"})

    def test_the_table_ini_falls_back_to_the_folder_name(self) -> None:
        """GetSettingsFileName step 3: <folder-name>.ini, case-insensitively."""
        files = [BIGUS, f"{FOLDER}.INI"]

        resolved = res.resolve_for_table(BIGUS, FOLDER, files)

        self.assertEqual(resolved["ini"]["resolution"], "shared")

    def test_a_table_named_as_its_folder_is_served_the_game_s_file(self) -> None:
        """Its sibling is served the same file, so the two can only be told the same."""
        files = [f"{FOLDER}.vpx", CYBER, *(f"{FOLDER}{ext}" for ext in
                                            (".directb2s", ".ini", ".vbs", ".scv"))]
        for table in (f"{FOLDER}.vpx", f"{FOLDER.lower()}.vpx", CYBER):
            with self.subTest(table=table):
                resolved = res.resolve_for_table(table, FOLDER, files)

                self.assertEqual({kind: resolved[kind]["resolution"]
                                  for kind in ("backglass", "ini", "script", "scv")},
                                 dict.fromkeys(("backglass", "ini", "script", "scv"),
                                               "shared"))

    def test_but_a_folder_named_pov_is_that_table_s_alone(self) -> None:
        files = [f"{FOLDER}.vpx", CYBER, f"{FOLDER}.pov"]

        self.assertEqual(
            [res.resolve_for_table(table, FOLDER, files)["pov"]["resolution"]
             for table in (f"{FOLDER}.vpx", CYBER)],
            [res.RESOLUTION_DEDICATED, res.RESOLUTION_NONE])


    def test_a_folder_named_nix_is_that_table_s_alone_like_a_point_of_view(self) -> None:
        files = [f"{FOLDER}.vpx", CYBER, f"{FOLDER}.nix"]

        self.assertEqual(
            [res.resolve_for_table(table, FOLDER, files)["nix"]["resolution"]
             for table in (f"{FOLDER}.vpx", CYBER)],
            [res.RESOLUTION_DEDICATED, res.RESOLUTION_NONE])


class InventoryLensTests(unittest.TestCase):
    def test_every_file_is_attributed(self) -> None:
        files = [
            BIGUS, CYBER,
            BIGUS.replace(".vpx", ".directb2s"),       # dedicated to bigus
            f"{FOLDER}.directb2s",                     # shared
            "AFM - long gone build.directb2s",         # orphaned
        ]

        inv = res.inventory(FOLDER, files, [BIGUS, CYBER])

        bindings = {e["binding"] for e in inv["backglass"]["files"]}
        self.assertEqual(bindings, {"dedicated", "shared", "orphaned"})
        dedicated = [e for e in inv["backglass"]["files"] if e["binding"] == "dedicated"]
        self.assertEqual(dedicated[0]["table"], BIGUS)

    def test_a_file_named_as_the_folder_is_shared_beside_a_table_of_that_name(
            self) -> None:
        tables = [f"{FOLDER.lower()}.vpx", CYBER]

        inv = res.inventory(FOLDER, [*tables, f"{FOLDER}.directb2s", f"{FOLDER}.pov"],
                            tables)

        self.assertEqual(inv["backglass"]["files"],
                         [{"file": f"{FOLDER}.directb2s", "binding": "shared"}])
        self.assertEqual(inv["pov"]["files"],
                         [{"file": f"{FOLDER}.pov", "binding": "dedicated",
                           "table": f"{FOLDER.lower()}.vpx"}])

    def test_an_orphan_is_the_residue_of_a_deleted_build(self) -> None:
        inv = res.inventory(FOLDER, ["Old Build.directb2s"], [BIGUS])

        self.assertEqual(inv["backglass"]["files"][0]["binding"], "orphaned")

    def test_an_empty_folder_reports_empty_lists_for_every_kind(self) -> None:
        inv = res.inventory(FOLDER, [], [])

        for kind in ("backglass", "b2spro", "ini", "script", "pov", "scv", "nix"):
            self.assertEqual(inv[kind], {"files": []})


class RomChainTests(unittest.TestCase):
    def test_a_declared_rom_with_its_zip_is_installed(self) -> None:
        chain = res.resolve_rom_chain("afm_113b", {}, ["afm_113b.zip"])

        self.assertEqual(chain["effective"], "afm_113b")
        self.assertTrue(chain["installed"])
        self.assertIsNone(chain["alias_of"])

    def test_an_alias_rewrites_the_declared_name_before_anything_is_looked_up(self) -> None:
        """pinmame swaps alias for real before loading - Alias.cpp."""
        chain = res.resolve_rom_chain("afm_ultra", {"afm_ultra": "afm_113b"},
                                      ["afm_113b.zip"])

        self.assertEqual(chain["alias_of"], "afm_113b")
        self.assertEqual(chain["effective"], "afm_113b")
        self.assertTrue(chain["installed"])

    def test_not_found_is_reported_as_unknown_not_missing(self) -> None:
        """A DOF-only name on an EM game, or a rom in a global folder: neither is
        a defect, so the answer is null, never False."""
        chain = res.resolve_rom_chain("GTB2001_1971", {}, [])

        self.assertIsNone(chain["installed"])

    def test_no_declared_rom_is_a_complete_non_answer(self) -> None:
        chain = res.resolve_rom_chain("", {}, ["whatever.zip"])

        self.assertIsNone(chain["declared"])
        self.assertIsNone(chain["installed"])

    def test_alias_matching_is_case_insensitive_like_pinmame(self) -> None:
        chain = res.resolve_rom_chain("AFM_Ultra", {"afm_ultra": "afm_113b"}, [])

        self.assertEqual(chain["effective"], "afm_113b")

    def test_required_threads_through_from_the_script_detector(self) -> None:
        """required=True with installed=None is the case a doctor should surface;
        required=False says the declared name is a DOF key."""
        missing = res.resolve_rom_chain("nfl_pat", {}, [], required=True)
        self.assertTrue(missing["required"])
        self.assertIsNone(missing["installed"])

        dof_key = res.resolve_rom_chain("GTB2001_1971", {}, [], required=False)
        self.assertFalse(dof_key["required"])

        undetected = res.resolve_rom_chain("afm_113b", {}, ["afm_113b.zip"])
        self.assertIsNone(undetected["required"], "metadata predating the detector")

    def test_alias_file_parsing_skips_comments_and_junk(self) -> None:
        aliases = res.parse_alias_file(
            "# my aliases\n\nafm_ultra, afm_113b\nbad_line\nmm_x mm_109c\n")

        self.assertEqual(aliases, {"afm_ultra": "afm_113b", "mm_x": "mm_109c"})


class FlexDmdTests(unittest.TestCase):
    def test_content_folders_are_found(self) -> None:
        state = res.flexdmd_state(["AFM.UltraDMD", "pinmame"], detected=True)

        self.assertTrue(state["installed"])
        self.assertEqual(state["content"], ["AFM.UltraDMD"])

    def test_declared_stays_none_until_the_script_extraction_exists(self) -> None:
        """The honest degradation, same as the rom detector."""
        state = res.flexdmd_state(["AFM.UltraDMD"], detected=None)

        self.assertIsNone(state["declared"])


class NvramTests(unittest.TestCase):
    def test_no_effective_rom_means_no_nvram_question(self) -> None:
        self.assertEqual(res.nvram_state("/nowhere", None), {"present": False})

    def test_present_nvram_reports_live_modification_time(self) -> None:
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            nvdir = Path(tmp) / "pinmame" / "nvram"
            nvdir.mkdir(parents=True)
            (nvdir / "afm_113b.nv").write_bytes(b"scores")

            state = res.nvram_state(tmp, "afm_113b")

        self.assertTrue(state["present"])
        self.assertEqual(state["file"], "afm_113b.nv")
        self.assertIsInstance(state["modified_at"], int)


if __name__ == "__main__":
    unittest.main()
