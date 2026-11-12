"""The export allowlist: a bundle is one game, not one folder.

Both transports - the VPXZ download and the mobile Web Send - ask
export_bundle the same question, so what ships cannot depend on which button
was pressed.
"""

from __future__ import annotations

import json
import unittest
import zipfile
from configparser import ConfigParser
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from common.games.archive_service import archive_for, cleanup_archive, create_vpxz_archive
from common.games.export_bundle import bundle_paths, is_readme, prune_info
from common.service_errors import NotFoundError
from tests.support.library import fake_game

FOLDER = "Cactus Canyon (Bally 1998)"
CHOSEN = "Cactus Canyon (Bally 1998) - VPW 1.2.vpx"
OTHER = "Cactus Canyon (Bally 1998) - other build.vpx"


def _library(tmp) -> Path:
    root = Path(tmp) / FOLDER
    (root / "medias").mkdir(parents=True)
    (root / "pinmame" / "roms").mkdir(parents=True)
    (root / "music").mkdir()
    for name in (CHOSEN, OTHER):
        (root / name).write_bytes(b"vpx")
    stem = Path(CHOSEN).stem
    (root / f"{stem}.directb2s").write_bytes(b"b2s")
    (root / f"{stem}.ini").write_text("[Player]", encoding="utf-8")
    (root / f"{Path(OTHER).stem}.directb2s").write_bytes(b"other b2s")
    (root / f"{FOLDER}.directb2s").write_bytes(b"shared b2s")
    (root / "pinmame" / "roms" / "cc_13.zip").write_bytes(b"rom")
    (root / "music" / "theme.ogg").write_bytes(b"music")
    (root / "medias" / "wheel.png").write_bytes(b"wheel")
    (root / "README.txt").write_text("by the author", encoding="utf-8")
    (root / "table.nfo").write_text("notes", encoding="utf-8")
    (root / f"{FOLDER}.info").write_text(json.dumps({
        "Info": {"Title": "Cactus Canyon"},
        "vpinfe": {"default_table": CHOSEN},
        "assets": {"medias/wheel.png": {"source": {"host": "user"}},
                   "medias/bg.png": {"source": {"host": "vpinmediadb", "hash": "abc"}}},
    }), encoding="utf-8")
    return root


class BundleTests(unittest.TestCase):
    def _names(self, root, **kwargs):
        return {arcname for _, arcname in bundle_paths(root, **kwargs)}

    def test_the_bundle_is_one_game_not_one_folder(self) -> None:
        with TemporaryDirectory() as tmp:
            names = self._names(_library(tmp))

        self.assertIn(CHOSEN, names)
        self.assertNotIn(OTHER, names, "alternate builds stay home")
        self.assertNotIn(f"{Path(OTHER).stem}.directb2s", names,
                         "and so do their companions")

    def test_the_bundle_ships_what_vpx_would_open_and_not_what_it_would_skip(self) -> None:
        """The same rule the frontend applies to media: the most specific file that
        exists, not everything that might match.

        A table with its own `.directb2s` never opens the folder-named one - the
        resolver takes the dedicated file and stops - so shipping both sent bytes the
        far side could not load. On one real game that was 54MB of a 245MB bundle.
        """
        with TemporaryDirectory() as tmp:
            names = self._names(_library(tmp))

        stem = Path(CHOSEN).stem
        self.assertIn(f"{stem}.directb2s", names, "its own is what VPX opens")
        self.assertIn(f"{stem}.ini", names)
        self.assertNotIn(f"{FOLDER}.directb2s", names,
                         "the folder-named one is shadowed, so it is dead weight")

    def test_the_shared_fallback_ships_when_it_is_the_one_that_resolves(self) -> None:
        """Narrowing must not mean dropping the fallback - it is what a table without
        its own companion actually loads."""
        with TemporaryDirectory() as tmp:
            root = _library(tmp)
            (root / f"{Path(CHOSEN).stem}.directb2s").unlink()
            names = self._names(root)

        self.assertIn(f"{FOLDER}.directb2s", names,
                      "with nothing more specific, the shared one is what VPX opens")

    def test_the_game_dirs_ship_and_media_does_not(self) -> None:
        with TemporaryDirectory() as tmp:
            names = self._names(_library(tmp))

        self.assertIn("pinmame/roms/cc_13.zip", {n.replace("\\", "/") for n in names})
        self.assertIn("music/theme.ogg", {n.replace("\\", "/") for n in names})
        self.assertFalse(any(n.startswith("medias") for n in names),
                         "artwork is for browsing, not playing")

    def test_the_authors_notes_ride_along(self) -> None:
        with TemporaryDirectory() as tmp:
            names = self._names(_library(tmp))

        self.assertIn("README.txt", names)
        self.assertIn("table.nfo", names)

    def test_readme_detection_is_narrow(self) -> None:
        """alias.txt and friends must never look like a readme."""
        self.assertTrue(is_readme("README.md"))
        self.assertTrue(is_readme("readme"))
        self.assertTrue(is_readme("table.nfo"))
        self.assertFalse(is_readme("alias.txt"))
        self.assertFalse(is_readme("notes.txt"))

    def test_everything_means_everything(self) -> None:
        with TemporaryDirectory() as tmp:
            names = self._names(_library(tmp), everything=True)

        self.assertIn(OTHER, names)
        # Forward-slashed, not `Path`: a zip entry name is forward-slashed by the
        # format's own spec, so building the expectation with the host separator asked
        # for "medias\\wheel.png" on Windows and passed everywhere else.
        self.assertIn("medias/wheel.png", names)

    def test_a_caller_may_pick_the_table(self) -> None:
        with TemporaryDirectory() as tmp:
            names = self._names(_library(tmp), table=OTHER)

        self.assertIn(OTHER, names)
        self.assertNotIn(CHOSEN, names)


class PrunedInfoTests(unittest.TestCase):
    def test_asset_entries_shrink_to_what_ships(self) -> None:
        info = json.dumps({"Info": {"Title": "X"},
                           "assets": {"medias/wheel.png": {"source": {"host": "user"}},
                                      "medias/bg.png": {"source": {"host": "user"}}}})

        pruned = json.loads(prune_info(info, {"medias/wheel.png"}))

        self.assertEqual(list(pruned["assets"]), ["medias/wheel.png"])
        self.assertEqual(pruned["Info"], {"Title": "X"},
                         "everything else passes through untouched")

    def test_a_folder_root_file_is_not_confused_with_one_under_medias(self) -> None:
        """What the old basename match could not do: both entries end in wheel.png,
        and only one of them is in the bundle."""
        info = json.dumps({"assets": {"medias/wheel.png": {"source": {"host": "user"}},
                                      "wheel.png": {"source": {"host": "user"}}}})

        pruned = json.loads(prune_info(info, {"wheel.png"}))

        self.assertEqual(list(pruned["assets"]), ["wheel.png"])

    def test_an_older_file_ships_with_its_whole_stars_for_the_receiver_to_double(self) -> None:
        info = json.dumps({"User": {"Rating": 4}, "assets": {}})

        pruned = json.loads(prune_info(info, set()))

        self.assertEqual(pruned["User"]["Rating"], 4)
        self.assertNotIn("schema", pruned.get("vpinfe", {}))

    def test_a_broken_info_passes_through_rather_than_vanishing(self) -> None:
        self.assertEqual(prune_info("not json", set()), "not json")


class FullExportScopeTests(unittest.TestCase):
    def test_the_full_export_scope_is_reserved_and_granted_locally(self) -> None:
        """full=true carries its own permission, so a future token holding only
        games:read cannot pull whole folders. Local trust grants everything, so
        nothing changes for anyone today."""
        from httpapi import scopes
        from httpapi.auth import SignInPolicy

        self.assertIn(scopes.GAMES_EXPORT_FULL, scopes.CORE)
        identity = SignInPolicy().identify(None)  # type: ignore[arg-type]
        self.assertTrue(identity.can(scopes.GAMES_EXPORT_FULL))


class ArchiveTests(unittest.TestCase):
    def test_the_vpxz_holds_the_bundle_and_a_true_manifest(self) -> None:
        with TemporaryDirectory() as tmp:
            archive = create_vpxz_archive(_library(tmp))
            self.addCleanup(cleanup_archive, archive)
            with zipfile.ZipFile(archive.path) as z:
                names = set(z.namelist())
                info = json.loads(z.read(f"{FOLDER}/{FOLDER}.info"))

        self.assertIn(f"{FOLDER}/{CHOSEN}", names)
        self.assertNotIn(f"{FOLDER}/{OTHER}", names)
        self.assertFalse(any("/medias/" in n for n in names))
        self.assertEqual(info["assets"], {},
                         "the manifest describes the archive, and no media shipped")

    def test_everything_mode_archives_the_folder(self) -> None:
        with TemporaryDirectory() as tmp:
            archive = create_vpxz_archive(_library(tmp), everything=True)
            self.addCleanup(cleanup_archive, archive)
            with zipfile.ZipFile(archive.path) as z:
                names = set(z.namelist())

        self.assertIn(f"{FOLDER}/{OTHER}", names)
        self.assertIn(f"{FOLDER}/medias/wheel.png", names)


def _game_under(root: Path, game_id: str, table: bytes):
    folder = root / FOLDER
    folder.mkdir(parents=True)
    (folder / CHOSEN).write_bytes(table)
    meta = {"vpinfe": {"game_id": game_id}}
    (folder / f"{FOLDER}.info").write_text(json.dumps(meta), encoding="utf-8")
    return fake_game(folder, FOLDER, meta=meta)


class ArchiveByIdTests(unittest.TestCase):
    def _archive(self, games, legacy_root: Path, game_id: str):
        legacy = ConfigParser()
        legacy["general"] = {"game_root_dir": str(legacy_root)}
        with patch("common.games.game_repository.all_games", return_value=games), \
                patch("common.config.paths.get_ini_config", return_value=legacy):
            return archive_for(game_id)

    def test_a_game_in_the_second_location_ships_its_own_table(self) -> None:
        with TemporaryDirectory() as tmp:
            first, second = Path(tmp) / "first", Path(tmp) / "second"
            games = [_game_under(first, "gid0000001", b"first table"),
                     _game_under(second, "gid0000002", b"second table")]
            archive = self._archive(games, first, "gid0000002")
            self.addCleanup(cleanup_archive, archive)
            with zipfile.ZipFile(archive.path) as z:
                shipped = z.read(f"{FOLDER}/{CHOSEN}")

        self.assertEqual(shipped, b"second table")

    def test_a_folder_that_is_not_there_is_refused_not_shipped_empty(self) -> None:
        with TemporaryDirectory() as tmp:
            gone = fake_game(Path(tmp) / "second" / FOLDER, FOLDER,
                             meta={"vpinfe": {"game_id": "gid0000002"}})

            with self.assertRaises(NotFoundError):
                self._archive([gone], Path(tmp) / "first", "gid0000002")


class SettingsFileTests(unittest.TestCase):
    """The table's settings file: kept by a download, left home by a send unless asked,
    and replaced by a mobile copy where the game has one."""

    def _names(self, root, **kwargs):
        return {arcname for _, arcname in bundle_paths(root, **kwargs)}

    def _with_mobile(self, tmp, word: str = "mobile") -> Path:
        root = _library(tmp)
        (root / f"{Path(CHOSEN).stem}.{word}.ini").write_text("[Player]\nmobile=1",
                                                               encoding="utf-8")
        return root

    def test_the_settings_file_goes_by_default(self) -> None:
        with TemporaryDirectory() as tmp:
            names = self._names(_library(tmp))

        self.assertIn(f"{Path(CHOSEN).stem}.ini", names)

    def test_a_send_leaves_it_home_unless_asked(self) -> None:
        with TemporaryDirectory() as tmp:
            root = _library(tmp)
            left = self._names(root, settings=False)
            asked = self._names(root, settings=True)

        self.assertNotIn(f"{Path(CHOSEN).stem}.ini", left)
        self.assertIn(f"{Path(CHOSEN).stem}.ini", asked)

    def test_a_mobile_copy_goes_in_its_place_under_the_name_the_table_expects(self) -> None:
        stem = Path(CHOSEN).stem
        with TemporaryDirectory() as tmp:
            root = self._with_mobile(tmp)
            sent = {arcname: path for path, arcname in
                    bundle_paths(root, settings=False, mobile_word="mobile")}

            self.assertEqual(sent[f"{stem}.ini"].read_text(encoding="utf-8"),
                             "[Player]\nmobile=1")
        self.assertNotIn(f"{stem}.mobile.ini", sent, "the word is not part of the sent name")

    def test_a_mobile_copy_wins_over_the_settings_file_even_when_that_is_asked_for(self) -> None:
        stem = Path(CHOSEN).stem
        with TemporaryDirectory() as tmp:
            root = self._with_mobile(tmp)
            sent = [(path, arcname) for path, arcname in
                    bundle_paths(root, settings=True, mobile_word="mobile")]

            self.assertEqual([arcname for _p, arcname in sent].count(f"{stem}.ini"), 1)
            self.assertEqual(
                dict((arcname, path) for path, arcname in sent)[f"{stem}.ini"]
                .read_text(encoding="utf-8"), "[Player]\nmobile=1")

    def test_the_word_is_matched_without_regard_to_case(self) -> None:
        with TemporaryDirectory() as tmp:
            root = self._with_mobile(tmp, "Mobile")
            sent = {arcname for _path, arcname in
                    bundle_paths(root, settings=False, mobile_word="mobile")}

        self.assertIn(f"{Path(CHOSEN).stem}.ini", sent)

    def test_without_the_word_a_mobile_copy_is_not_looked_for(self) -> None:
        with TemporaryDirectory() as tmp:
            root = self._with_mobile(tmp)
            sent = self._names(root, settings=False, mobile_word="")

        self.assertNotIn(f"{Path(CHOSEN).stem}.ini", sent)
        self.assertNotIn(f"{Path(CHOSEN).stem}.mobile.ini", sent)

    def test_the_whole_folder_is_the_folder_as_it_is(self) -> None:
        stem = Path(CHOSEN).stem
        with TemporaryDirectory() as tmp:
            root = self._with_mobile(tmp)
            names = self._names(root, everything=True, settings=False,
                                mobile_word="mobile")

        self.assertLessEqual({f"{stem}.ini", f"{stem}.mobile.ini"}, names)


if __name__ == "__main__":
    unittest.main()
