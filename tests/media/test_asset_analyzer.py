"""What the analyzer recognizes in an upload, before anything is planned or written."""

from __future__ import annotations

import os
import unittest
from unittest import mock

from common.uploads import asset_analyzer_service
from common.uploads.asset_analyzer_service import analyze_path, analyze_upload_session
from tests.support.uploads import kinds, make_zip


class RomArchiveTests(unittest.TestCase):
    def test_pinmame_rom_zip_is_recognized(self):
        """Chip names run to three characters. A two-character cap accepted .u7 and
        rejected .u21/.106/.112, and since every member must match, one rejection sank
        the whole archive."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "lah_112.zip")
            make_zip(path, ["lahsnd.u7", "lahsnd.u21", "lahsnd.u17",
                             "lahdispa.106", "lahcpua.112"])
            self.assertEqual(kinds(analyze_path(path)), ["rom"])

    def test_flat_audio_archive_is_not_a_rom(self):
        """The three-character rule must not swallow .mp3: an altsound pack is a flat
        archive of audio, and would otherwise be claimed as a ROM."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sounds.zip")
            make_zip(path, ["1.mp3", "2.mp3", "3.mp3"])
            self.assertNotIn("rom", kinds(analyze_path(path)))


class AssetAnalyzerTests(unittest.TestCase):
    def test_game_bundle(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "bundle.zip"
            make_zip(zip_path, ["Foo.vpx", "Foo.directb2s", "Foo.ini"])
            result = analyze_path(zip_path)
            self.assertTrue(result.has_game)
            self.assertEqual(kinds(result), ["backglass", "ini", "table"])
            self.assertEqual(result.error, "")

    def test_pup_marker_and_root(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "pup.zip"
            make_zip(zip_path, ["MyPup/screens.pup", "MyPup/S1/a.mp4", "MyPup/S2/b.mp4"])
            result = analyze_path(zip_path)
            self.assertEqual(kinds(result), ["pup_pack"])
            self.assertEqual(result.assets[0].root, "MyPup")
            self.assertEqual(len(result.assets[0].entries), 3)

    def test_pup_fallback_by_shape(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        names = [f"Pack/screen{i}/clip.mp4" for i in range(12)]
        with TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "shape.zip"
            make_zip(zip_path, names)
            result = analyze_path(zip_path)
            self.assertEqual(kinds(result), ["pup_pack"])
            self.assertEqual(result.assets[0].root, "Pack")

    def test_altsound_nested_marker(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "as.zip"
            make_zip(zip_path, ["altsound/altsound.csv", "altsound/1/x.ogg", "altsound/2/y.ogg"])
            result = analyze_path(zip_path)
            self.assertEqual(kinds(result), ["altsound"])
            self.assertEqual(result.assets[0].root, "altsound")

    def test_flat_rom_archive(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "rom.zip"
            make_zip(zip_path, ["mm_105.bin", "mm_snd.u7", "mm.cpu"])
            result = analyze_path(zip_path)
            self.assertEqual(kinds(result), ["rom"])
            self.assertEqual(len(result.assets[0].entries), 3)

    def test_nested_zip_is_rom_blob(self):
        import io
        import zipfile
        from pathlib import Path
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "nested.zip"
            inner = io.BytesIO()
            with zipfile.ZipFile(inner, "w") as iz:
                iz.writestr("a.bin", b"x")
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.writestr("Foo.vpx", b"x")
                archive.writestr("roms/mm.zip", inner.getvalue())
            result = analyze_path(zip_path)
            self.assertEqual(kinds(result), ["rom", "table"])

    def test_music_folder(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "music.zip"
            make_zip(zip_path, ["music/song1.mp3", "music/song2.ogg"])
            result = analyze_path(zip_path)
            self.assertEqual(kinds(result), ["music"])
            self.assertEqual(result.assets[0].root, "music")

    def test_music_with_video_is_not_music(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "mv.zip"
            make_zip(zip_path, ["stuff/song1.mp3", "stuff/clip.mp4"])
            result = analyze_path(zip_path)
            self.assertNotIn("music", kinds(result))

    def test_junk_entries_skipped(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "junk.zip"
            make_zip(zip_path, ["Foo.vpx", "__MACOSX/._Foo.vpx", ".DS_Store"])
            result = analyze_path(zip_path)
            self.assertEqual(kinds(result), ["table"])

    def test_unrecognized_only_is_error(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "empty.zip"
            # Genuinely unclaimable names - readme.txt is recognized now.
            make_zip(zip_path, ["notes.md", "random.xyz"])
            result = analyze_path(zip_path)
            self.assertEqual(result.assets, ())
            self.assertTrue(result.error)

    def test_readme_files_are_claimed_with_a_preview(self):
        """The author's notes reach the person installing the game."""
        from pathlib import Path
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "bundle.zip"
            make_zip(zip_path, ["Table X.vpx", "README.txt", "extra.nfo", "alias.txt"])
            result = analyze_path(zip_path)

        kinds = sorted(a.kind for a in result.assets)
        self.assertEqual(kinds.count("readme"), 2, "README.txt and extra.nfo")
        self.assertIn("alias.txt", result.unrecognized,
                      "a bare .txt never claims as anything")
        readme = next(a for a in result.assets if a.detail == "README.txt")
        self.assertEqual(readme.preview, "x" * 16, "preview text extracted best-effort")

    def test_dir_source_parity_with_zip(self):
        import zipfile
        from pathlib import Path
        from tempfile import TemporaryDirectory
        names = ["Foo.vpx", "MyPup/screens.pup", "MyPup/s1/a.mp4"]
        with TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "parity.zip"
            make_zip(zip_path, names)
            from_zip = sorted((a.kind, a.root) for a in analyze_path(zip_path).assets)
            tree = Path(tmp) / "tree"
            with zipfile.ZipFile(zip_path) as archive:
                archive.extractall(tree)
            from_dir = sorted((a.kind, a.root) for a in analyze_path(tree).assets)
            self.assertEqual(from_zip, from_dir)

    def test_single_bare_files(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            wheel = Path(tmp) / "wheel.png"
            wheel.write_bytes(b"x")
            result = analyze_path(wheel)
            self.assertEqual(kinds(result), ["media"])
            self.assertEqual(result.assets[0].media_kind, "wheel")

    def test_rar_tool_hint_is_the_registrys(self):
        from common.host import tools, vpinos
        from common.uploads.asset_analyzer_service import rar_tool_hint
        with mock.patch.object(vpinos, "detected", return_value=False):
            for here, says in ((tools.WINDOWS, "7-Zip"), (tools.DARWIN, "brew install unar"),
                               (tools.LINUX, "package manager")):
                with mock.patch.object(tools, "here", return_value=here):
                    self.assertIn(says, rar_tool_hint())
                    self.assertIn("RAR Tool Path", rar_tool_hint())

    def test_rarfile_runs_the_program_the_registry_found(self):
        from pathlib import Path

        from common.host import tools
        fake = mock.Mock()
        found = tools.Found(tools.RAR, tools.State.FOUND, Path("/opt/bin/7zz"))
        with (mock.patch.object(asset_analyzer_service, "rarfile", fake),
              mock.patch.object(tools, "resolve", return_value=found),
              mock.patch.object(asset_analyzer_service, "_pointed_at", None)):
            self.assertTrue(asset_analyzer_service.rar_tool_available())
        self.assertEqual(fake.SEVENZIP2_TOOL, str(Path("/opt/bin/7zz")))
        fake.tool_setup.assert_called_once_with(
            force=True, unrar=False, unar=False, sevenzip=False, sevenzip2=True,
            bsdtar=False)

    def test_nothing_found_is_not_available(self):
        from common.host import tools
        fake = mock.Mock()
        with (mock.patch.object(asset_analyzer_service, "rarfile", fake),
              mock.patch.object(tools, "resolve",
                                return_value=tools.Found(tools.RAR, tools.State.MISSING))):
            self.assertFalse(asset_analyzer_service.rar_tool_available())
        fake.tool_setup.assert_not_called()

    def test_missing_rar_tool_reported_before_dialog(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from common.uploads.asset_analyzer_service import rar_tool_hint
        with TemporaryDirectory() as tmp:
            fake_rar = Path(tmp) / "x.rar"
            fake_rar.write_bytes(b"x")
            with (
                mock.patch.object(asset_analyzer_service, "open_source") as fake_open,
                mock.patch.object(asset_analyzer_service, "rar_tool_available",
                                  return_value=False),
            ):
                fake_open.return_value = mock.Mock(kind="rar", name="x.rar")
                result = analyze_path(fake_rar)
            self.assertEqual(result.assets, ())
            self.assertIn(rar_tool_hint(), result.error)

    def test_rar_backend_missing_is_graceful(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            fake_rar = Path(tmp) / "x.rar"
            fake_rar.write_bytes(b"not really a rar")
            with mock.patch.object(asset_analyzer_service, "rarfile", None):
                result = analyze_path(fake_rar)
            self.assertEqual(result.assets, ())
            self.assertIn("rarfile", result.error)

    def test_upload_session_single_archive(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            session.mkdir()
            make_zip(session / "bundle.zip", ["Foo.vpx"])
            result, source_path = analyze_upload_session(session)
            self.assertEqual(kinds(result), ["table"])
            self.assertEqual(source_path.name, "bundle.zip")

    def test_upload_session_folder(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            (session / "MyPup").mkdir(parents=True)
            (session / "MyPup" / "screens.pup").write_bytes(b"x")
            (session / "Foo.vpx").write_bytes(b"x")
            result, source_path = analyze_upload_session(session)
            self.assertEqual(kinds(result), ["pup_pack", "table"])
            self.assertEqual(source_path, session)


class TableCompanionTests(unittest.TestCase):
    """A table's script, point of view and score view, arriving beside it."""

    def _analyzed(self, names):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "bundle.zip"
            make_zip(zip_path, names)
            return analyze_path(zip_path)

    def test_the_table_s_own_are_claimed_whatever_their_case(self):
        result = self._analyzed(["Foo.vpx", "Foo.vbs", "foo.POV", "Foo.scv", "Foo.nix"])

        self.assertEqual(kinds(result), ["nix", "pov", "script", "scv", "table"])

    def test_one_named_for_something_else_is_not_the_table_s(self):
        result = self._analyzed(["Foo.vpx", "core.vbs"])

        self.assertEqual(kinds(result), ["table"])
        self.assertEqual(result.unrecognized, ("core.vbs",))

    def test_one_in_another_folder_is_not_the_table_s(self):
        result = self._analyzed(["Foo.vpx", "extras/Foo.vbs"])

        self.assertEqual(kinds(result), ["table"])


class GameInfoDetectionTests(unittest.TestCase):
    def test_info_beside_vpx_is_claimed_and_parsed(self):
        import json
        import zipfile
        from pathlib import Path
        from tempfile import TemporaryDirectory
        info = {"Info": {"VPSId": "abc123"}, "User": {"Rating": 4}}
        with TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "bundle.zip"
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.writestr("Foo (Bar 1999)/Foo.vpx", b"x")
                archive.writestr("Foo (Bar 1999)/Foo (Bar 1999).info", json.dumps(info))
            result = analyze_path(zip_path)
            self.assertIn("game_info", kinds(result))
            self.assertEqual(result.bundle_info["Info"]["VPSId"], "abc123")

    def test_lone_info_stays_unrecognized(self):
        import json
        from pathlib import Path
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            info_file = Path(tmp) / "Foo.info"
            info_file.write_text(json.dumps({"Info": {}}))
            result = analyze_path(info_file)
            self.assertNotIn("game_info", kinds(result))

    def test_invalid_info_is_dropped_with_note(self):
        import zipfile
        from pathlib import Path
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "bundle.zip"
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.writestr("Foo.vpx", b"x")
                archive.writestr("Foo.info", b"this is not json {{{")
            result = analyze_path(zip_path)
            self.assertNotIn("game_info", kinds(result))
            self.assertIsNone(result.bundle_info)
            self.assertIn("Foo.info", result.unrecognized)


if __name__ == "__main__":
    unittest.main()
