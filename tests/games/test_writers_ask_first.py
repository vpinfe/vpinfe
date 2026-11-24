"""A write into a game folder asks the disk first, and a folder that cannot take it is
refused in the shared words before anything is touched.

Without the question a dropped share ends in an errno halfway through a copy, or hangs in
a call that cannot be cancelled. Every writer goes through `game_lens.game_for_writing`.
"""

from __future__ import annotations

import base64
import os
import unittest
import zipfile
from collections.abc import Callable
from pathlib import Path
from unittest import mock

from common import service_errors
from common.config import path_state
from common.games import (
    asset_ops,
    game_ops,
    game_repository,
    library_folders,
    media_ops,
    picture_ops,
    table_ops,
)
from common.i18n import t
from common.uploads.asset_analyzer_service import analyze_path
from common.uploads.asset_import_service import build_import_plan, execute_import_plan
from tests.support.hung_folder import never_answering
from tests.support.library import TempTree, fake_game, write_game
from tests.support.skips import needs_posix_permissions

GAME_ID = "Ask0000001"
FOLDER = "Harbor Lights (Original 2024)"
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")


class _Library(TempTree):
    def setUp(self) -> None:
        super().setUp()
        info = {"Info": {"Name": "Harbor Lights"}, "vpinfe": {"game_id": GAME_ID}}
        self.library = self.root / "games"
        self.library.mkdir()
        self.folder = write_game(self.library, FOLDER, info=info)
        self.info = self.folder / f"{FOLDER}.info"
        self.before = self.info.read_bytes()
        self.listing = sorted(p.name for p in self.folder.iterdir())
        game = fake_game(self.folder, FOLDER, meta=info)
        self.store = library_folders.LocationStore(self.root / "config")
        self.store.save([library_folders.Location(location_id="l1", path=str(self.library))], "")
        for patcher in (
                mock.patch.object(library_folders, "get_location_store",
                                  return_value=self.store),
                mock.patch.object(game_repository, "game_by_id", return_value=game)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.picture = self.root / "art.png"
        self.picture.write_bytes(b"png")
        self.backglass = self.root / "new.directb2s"
        self.backglass.write_bytes(b"backglass")

    def drop(self) -> None:
        archive = self.root / "drop.zip"
        with zipfile.ZipFile(archive, "w") as made:
            made.writestr("New Table.directb2s", b"b2s")
        plan = build_import_plan(analyze_path(archive), game_dir=self.folder)
        execute_import_plan(plan, archive)

    def writers(self) -> dict[str, Callable[[], object]]:
        return {
            "game override": lambda: game_ops.set_favorite(GAME_ID, True),
            "game details": lambda: game_ops.set_details(GAME_ID, {"year": 1999}),
            "table setting": lambda: table_ops.set_hidden(GAME_ID, "tbl0000001", True),
            "media placed": lambda: media_ops.place_file(GAME_ID, "wheel", "", self.picture),
            "media removed": lambda: media_ops.remove(GAME_ID, "wheel"),
            "asset removed": lambda: asset_ops.remove(GAME_ID, "x.directb2s"),
            "asset placed": lambda: asset_ops.place_file(GAME_ID, "backglass", "",
                                                         self.backglass),
            "picture put": lambda: picture_ops.put(GAME_ID, "shot.png", PNG),
            "picture removed": lambda: picture_ops.remove(GAME_ID, "shot.png"),
            "import": self.drop,
        }

    def assert_untouched(self) -> None:
        self.assertEqual(self.info.read_bytes(), self.before)
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), self.listing)


@needs_posix_permissions
class ReadOnlyFolderTests(_Library):
    def test_every_writer_names_the_folder_and_writes_nothing(self) -> None:
        self.folder.chmod(0o555)
        self.addCleanup(self.folder.chmod, 0o755)
        wanted = t("error.path.read_only", name=FOLDER)

        for label, write in self.writers().items():
            with self.subTest(label), self.assertRaises(service_errors.BlockedError) as caught:
                write()
            self.assertEqual(str(caught.exception), wanted)
        self.assert_untouched()


class DeadMountTests(_Library):
    def test_a_folder_that_does_not_answer_is_refused_without_waiting_on_it(self) -> None:
        with mock.patch.object(path_state, "PROBE_SECONDS", 0.2), \
                never_answering(str(self.folder)):
            for label, write in self.writers().items():
                with self.subTest(label), self.assertRaises(service_errors.BlockedError):
                    write()
        self.assert_untouched()

    def test_a_folder_that_does_not_answer_is_named_by_its_library_folder(self) -> None:
        named = library_folders.Location("l1", str(self.library)).name
        with mock.patch.object(path_state, "PROBE_SECONDS", 0.2), \
                never_answering(str(self.folder)), \
                self.assertRaises(service_errors.BlockedError) as caught:
            game_ops.set_favorite(GAME_ID, True)
        self.assertIn(named, str(caught.exception))

    def test_a_folder_outside_every_library_folder_is_named_by_itself(self) -> None:
        self.store.save([], "")
        game = fake_game(self.folder, FOLDER, meta={"vpinfe": {"game_id": GAME_ID}})
        with mock.patch.object(game_repository, "game_by_id", return_value=game), \
                mock.patch.object(path_state, "PROBE_SECONDS", 0.2), \
                never_answering(str(self.folder)), \
                self.assertRaises(service_errors.BlockedError) as caught:
            game_ops.set_favorite(GAME_ID, True)
        self.assertIn(self.folder.name, str(caught.exception))

    def test_a_game_with_no_folder_is_refused(self) -> None:
        game = fake_game("", FOLDER)
        with mock.patch.object(game_repository, "game_by_id", return_value=game), \
                self.assertRaises(service_errors.BlockedError):
            game_ops.set_favorite(GAME_ID, True)


class WritableFolderTests(_Library):
    def test_a_folder_that_answers_is_handed_back(self) -> None:
        self.assertEqual(library_folders.writable_game_folder(self.folder), self.folder)
        self.assertEqual(Path(library_folders.writable_game_folder(str(self.folder))),
                         self.folder)

    def test_a_missing_folder_is_refused(self) -> None:
        with self.assertRaises(service_errors.BlockedError):
            library_folders.writable_game_folder(self.root / "gone")
        self.assertFalse(os.path.exists(self.root / "gone"))


if __name__ == "__main__":
    unittest.main()
