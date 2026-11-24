"""Which asset kind a file is, decided by the registry alone.

Everything downstream - what a plan proposes, where an import writes - follows from the
kind, so these are the rules the rest of the upload path is built on.
"""

from __future__ import annotations

import unittest

from common.games.asset_registry import (
    classify_bare_extension,
    match_media_kind,
    spec_for,
)


class AssetRegistryTests(unittest.TestCase):
    def test_classify_bare_extension(self):
        cases = [
            ("Medieval Madness.vpx", "table"),
            ("Medieval Madness.directb2s", "backglass"),
            ("mm_105b.crz", "altcolor_serum"),
            ("mm_105b.CRZ", "altcolor_serum"),
            ("mm_105b.cROMc", "altcolor_serum"),   # Serum's other format
            ("mm_105b.vni", "altcolor_vni"),
            ("mm_105b.PAL", "altcolor_vni"),
            ("mm_105b.pac", "altcolor_vni"),
            ("Medieval Madness.ini", "ini"),
            ("wheel.png", "media"),
            ("wheel.apng", "media"),
            ("bg.mp4", "media"),
            ("bg.webm", "media"),
            ("audio.mp3", "media"),
            ("roms.zip", None),        # archives are inspected, not bare-classified
            ("pack.7z", None),
            ("readme.txt", None),
            ("noext", None),
        ]
        for filename, expected in cases:
            with self.subTest(filename=filename):
                spec = classify_bare_extension(filename)
                self.assertEqual(spec.kind if spec else None, expected)

    def test_match_media_kind_canonical_names(self):
        cases = [
            ("bg.png", "backglass"),
            ("dmd.png", "scoreview"),
            ("dmd.mp4", "scoreview_video"),
            ("table.png", "playfield"),
            ("table.mp4", "playfield_video"),
            ("wheel.png", "wheel"),
            ("audio.mp3", "audio"),
            ("realdmd-color.png", "real_dmd_color"),
        ]
        for filename, expected in cases:
            with self.subTest(filename=filename):
                self.assertEqual(match_media_kind(filename), expected)

    def test_a_webm_is_a_video_of_the_kind_its_name_gives(self):
        cases = [
            ("table.webm", "playfield_video"),
            ("backglass.webm", "backglass_video"),
            ("dmd.webm", "scoreview_video"),
            ("(Playfield) Doom.webm", "playfield_video"),
            ("(Playfield) Doom.png", "playfield"),
        ]
        for filename, expected in cases:
            with self.subTest(filename=filename):
                self.assertEqual(match_media_kind(filename), expected)

    def test_match_media_kind_keyword_fallback(self):
        cases = [
            ("MyTable_wheel.png", "wheel"),
            ("Table_backglass.png", "backglass"),
            ("Game_dmd.mp4", "scoreview_video"),
            ("realdmd.png", "real_dmd"),
            ("song.mp3", "audio"),        # any recognized audio file -> audio slot
            ("photo.png", None),          # no keyword -> unrecognized
            ("notes.txt", None),          # non-media extension
        ]
        for filename, expected in cases:
            with self.subTest(filename=filename):
                self.assertEqual(match_media_kind(filename), expected)

    def test_realdmd_not_claimed_by_dmd_rule(self):
        # "real_dmd" contains "scoreview"; the realdmd rule must win for the video too,
        # or realdmd.mp4 would fall through to dmd_video.
        self.assertEqual(match_media_kind("realdmd.png"), "real_dmd")
        self.assertEqual(match_media_kind("realdmd.mp4"), "real_dmd_video")

    def test_a_real_dmd_video_uploads_to_its_own_kind(self):
        cases = [
            ("realdmd.mp4", "real_dmd_video"),
            ("realdmd-color.mp4", "real_dmd_color_video"),
            ("(RealDMD) Attack from Mars.mp4", "real_dmd_video"),
            ("(RealColorDMD) Attack from Mars.mp4", "real_dmd_color_video"),
            ("Real DMD Color Attack from Mars.mp4", "real_dmd_color_video"),
            ("(RealDMD) Attack from Mars.png", "real_dmd"),
        ]
        for filename, expected in cases:
            with self.subTest(filename=filename):
                self.assertEqual(match_media_kind(filename), expected)

    def test_a_full_dmd_file_uploads_to_its_own_kind_and_not_the_dmd_slot(self):
        cases = [
            ("fulldmd.png", "full_dmd"),
            ("fulldmd.mp4", "full_dmd_video"),
            ("(FullDMD) Attack from Mars.png", "full_dmd"),
            ("(FullDMD) Attack from Mars.mp4", "full_dmd_video"),
            ("Full DMD Attack from Mars.mp4", "full_dmd_video"),
            ("Attack from Mars full-dmd.png", "full_dmd"),
            ("dmd.mp4", "scoreview_video"),
        ]
        for filename, expected in cases:
            with self.subTest(filename=filename):
                self.assertEqual(match_media_kind(filename), expected)

    def test_spec_for_flags(self):
        self.assertFalse(spec_for("table").requires_game)
        self.assertTrue(spec_for("backglass").requires_game)
        self.assertTrue(spec_for("altcolor_serum").requires_rom)
        self.assertFalse(spec_for("pup_pack").requires_rom)
        self.assertTrue(spec_for("media").allow_multiple)
        with self.assertRaises(KeyError):
            spec_for("nonexistent")

    def test_the_kinds_vpx_resolves_are_declared_here(self):
        """They were declared only in `asset_resolver`, so an uploaded .vbs was a file
        the registry had never heard of. None is required to launch: a table whose
        script lives inside the .vpx runs, and a sidecar is an override."""
        for kind in ("script", "pov", "scv", "nix"):
            with self.subTest(kind=kind):
                self.assertTrue(spec_for(kind).requires_game)
                self.assertFalse(spec_for(kind).required_to_launch)
        self.assertEqual(classify_bare_extension("Table.vbs").kind, "script")

    def test_the_resolver_reads_its_extensions_from_here(self):
        """One declaration per kind: adding an extension to a spec must not have to be
        remembered in a second list that decides what a table launches with."""
        from common.games.asset_resolver import VPX_ASSET_KINDS

        for kind in VPX_ASSET_KINDS:
            with self.subTest(kind=kind.key):
                self.assertEqual(kind.extension, spec_for(kind.key).extensions[0])


if __name__ == "__main__":
    unittest.main()
