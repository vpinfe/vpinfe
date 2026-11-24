"""The folder Back up now writes to: one setting, the only folder that is listed.

A folder nobody can write to refuses before anything is written, and changing the setting
carries the backups across only through `relocate`.
"""

from __future__ import annotations

import pathlib
import shutil
import threading
import time
import unittest
from unittest.mock import patch

from common import service_errors
from common.backups import archive, media, places, relocate
from common.backups import folder as backup_folder
from common.config import paths
from common.config.config_access import cfg_set
from common.config.config_store import ConfigStore
from common.runtime import jobs
from tests.support.library import TempTree


class BackupFolderCase(TempTree):
    def setUp(self) -> None:
        super().setUp()
        self.config = self.root / "config"
        self.config.mkdir()
        (self.config / "tags.json").write_text("{}", encoding="utf-8")
        self.default = self.config / "backups"
        self.store = ConfigStore(str(self.root / "vpinfe.ini"))
        for patcher in (patch.object(paths, "DEFAULT_BACKUPS_DIR", self.default),
                        patch.object(backup_folder, "get_ini_config",
                                     side_effect=lambda: ConfigStore(self.store.ini_path)),
                        patch.object(archive, "CONFIG_DIR", self.config)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def choose(self, folder: pathlib.Path | str) -> None:
        cfg_set(self.store, backup_folder.SECTION, backup_folder.FOLDER, str(folder))
        self.store.save()

    def take(self) -> pathlib.Path:
        return archive.take(archive.SCOPE_RECORDS)

    def take_media_copy(self, folder: pathlib.Path) -> pathlib.Path:
        source = self.root / "picture.png"
        source.write_bytes(b"png")
        return media.keep(folder, "game-1", source, "media/picture.png")


class WhereABackupGoesTests(BackupFolderCase):
    def test_a_backup_goes_to_the_default_folder_until_one_is_chosen(self) -> None:
        path = self.take()

        self.assertEqual(path.parent, self.default)
        self.assertEqual(backup_folder.known(), [self.default])

    def test_a_backup_goes_to_the_chosen_folder(self) -> None:
        bigger = self.root / "bigger"
        bigger.mkdir()
        self.choose(bigger)

        self.assertEqual(self.take().parent, bigger)

    def test_a_chosen_folder_that_is_not_there_is_refused_and_nothing_is_made(self) -> None:
        """A missing chosen folder is refused, never created."""
        gone = self.root / "gone"
        self.choose(gone)

        with self.assertRaises(service_errors.BlockedError) as raised:
            self.take()

        self.assertIn("backups folder", str(raised.exception))
        self.assertFalse(gone.exists())
        self.assertFalse(self.default.exists())


    def test_a_folder_typed_with_dots_is_written_to_and_its_backup_found_again(self) -> None:
        bigger = self.root / "bigger"
        bigger.mkdir()
        self.choose(f"{self.root}/elsewhere/../bigger")

        path = self.take()

        self.assertEqual(path.parent, bigger)
        self.assertIsNotNone(places.archive_copy(path))

    def test_a_relative_folder_is_made_absolute_before_it_is_used(self) -> None:
        self.assertTrue(backup_folder.spelled("rel/../there").is_absolute())


class ChangingTheFolderTests(BackupFolderCase):
    def test_only_the_folder_the_setting_names_is_listed(self) -> None:
        self.take()
        bigger = self.root / "bigger"
        bigger.mkdir()
        self.choose(bigger)
        second = archive.take(archive.SCOPE_RECORDS)

        self.assertEqual(archive.listed(), [second])
        self.assertEqual(backup_folder.known(), [bigger])

    def test_a_folder_with_a_comma_in_its_name_is_written_to_and_listed(self) -> None:
        odd = self.root / "one, two"
        odd.mkdir()
        self.choose(odd)
        made = self.take()

        self.assertEqual(archive.listed(), [made])

    def test_a_folder_that_no_longer_answers_is_left_out_of_the_listing(self) -> None:
        bigger = self.root / "bigger"
        bigger.mkdir()
        self.choose(bigger)
        self.take()
        shutil.rmtree(bigger)

        self.assertEqual(backup_folder.listable(), [])


class WhatMovingWouldDoTests(BackupFolderCase):
    def test_nothing_moves_when_the_old_folder_holds_no_backup(self) -> None:
        self.take()
        bigger = self.root / "bigger"
        bigger.mkdir()
        self.choose(bigger)

        found = relocate.plan("")

        self.assertEqual((found.moving, found.already), (0, 0))

    def test_the_default_folder_counts_as_the_old_one(self) -> None:
        self.take()
        self.take_media_copy(self.default)
        bigger = self.root / "bigger"
        bigger.mkdir()

        found = relocate.plan(str(bigger))

        self.assertEqual((found.old, found.new, found.moving), (self.default, bigger, 2))

    def test_backups_already_in_the_new_folder_are_counted(self) -> None:
        self.take()
        bigger = self.root / "bigger"
        bigger.mkdir()
        self.choose(bigger)
        self.take()
        self.take_media_copy(bigger)
        self.choose("")

        found = relocate.plan(str(bigger))

        self.assertEqual((found.moving, found.already), (1, 2))

    def test_a_file_that_is_not_a_backup_is_not_counted(self) -> None:
        self.take()
        (self.default / "notes.txt").write_text("mine", encoding="utf-8")
        (self.default / "media").mkdir()
        (self.default / "media" / "readme.txt").write_text("mine", encoding="utf-8")

        self.assertEqual(relocate.plan(str(self.root)).moving, 1)

    def test_the_same_folder_moves_nothing(self) -> None:
        self.take()

        self.assertEqual(relocate.plan("").moving, 0)
        self.assertEqual(relocate.plan(str(self.default)).moving, 0)

    def test_a_new_folder_that_is_not_there_is_a_problem(self) -> None:
        self.take()

        found = relocate.plan(str(self.root / "gone"))

        self.assertEqual(found.moving, 1)
        self.assertIn("Nothing is at that path", found.problem)

    def test_an_old_folder_that_does_not_answer_is_nothing_to_move(self) -> None:
        bigger = self.root / "bigger"
        bigger.mkdir()
        self.choose(bigger)
        self.take()
        shutil.rmtree(bigger)

        self.assertEqual(relocate.plan("").moving, 0)


class MovingTests(BackupFolderCase):
    def run_begin(self, setting: str) -> dict:
        job = relocate.begin(setting, lambda: self.choose(setting))
        for _ in range(100):
            found = jobs.get(job.id)
            if found is not None and found.state != jobs.RUNNING:
                break
            time.sleep(0.05)
        assert found is not None
        self.assertEqual(found.state, jobs.DONE, found.error)
        return found.result  # type: ignore[return-value]

    def test_the_backups_go_across_and_the_old_folder_is_left_empty(self) -> None:
        first = self.take()
        media = self.take_media_copy(self.default)
        bigger = self.root / "bigger"
        bigger.mkdir()

        result = self.run_begin(str(bigger))

        self.assertEqual(result["moved"], 2)
        self.assertTrue((bigger / first.name).is_file())
        self.assertTrue((bigger / media.relative_to(self.default)).is_file())
        self.assertFalse(first.exists())
        self.assertEqual(list(self.default.iterdir()), [])
        self.assertEqual(backup_folder.current(), bigger)

    def test_a_backup_with_the_same_name_is_left_and_the_old_one_stays(self) -> None:
        first = self.take()
        bigger = self.root / "bigger"
        bigger.mkdir()
        (bigger / first.name).write_bytes(b"the one already there")

        result = self.run_begin(str(bigger))

        self.assertEqual((result["moved"], result["left"]), (0, [first.name]))
        self.assertEqual((bigger / first.name).read_bytes(), b"the one already there")
        self.assertTrue(first.exists())

    def test_a_file_that_cannot_be_moved_stays_and_the_rest_go(self) -> None:
        first = self.take()
        bigger = self.root / "bigger"
        bigger.mkdir()
        real = relocate._move_one

        def fail_on_the_first(source: pathlib.Path, target: pathlib.Path) -> bool:
            if source == first:
                raise OSError("the disk said no")
            return real(source, target)

        self.take_media_copy(self.default)
        with patch.object(relocate, "_move_one", fail_on_the_first):
            result = self.run_begin(str(bigger))

        self.assertEqual(result["moved"], 1)
        self.assertEqual([one["name"] for one in result["failed"]], [first.name])
        self.assertEqual(result["from"], str(self.default))
        self.assertTrue(first.exists())

    def test_a_copy_that_is_the_wrong_size_keeps_the_original(self) -> None:
        first = self.take()
        bigger = self.root / "bigger"
        bigger.mkdir()

        def short(source: pathlib.Path, target: pathlib.Path) -> None:
            target.write_bytes(b"x")

        with patch.object(relocate.shutil, "copy2", short):
            result = self.run_begin(str(bigger))

        self.assertEqual(result["moved"], 0)
        self.assertEqual(len(result["failed"]), 1)
        self.assertTrue(first.exists())
        self.assertFalse((bigger / first.name).exists())

    def test_a_new_folder_that_cannot_be_written_to_refuses_and_changes_nothing(self) -> None:
        self.take()
        saved: list[str] = []

        with self.assertRaises(service_errors.BlockedError):
            relocate.begin(str(self.root / "gone"), lambda: saved.append("saved"))

        self.assertEqual(saved, [])

    def test_a_second_move_while_one_runs_is_refused_and_changes_nothing(self) -> None:
        self.take()
        bigger = self.root / "bigger"
        bigger.mkdir()
        release = threading.Event()
        blocker = jobs.submit(jobs.KIND_BACKUPS_MOVE, lambda job: release.wait(5))
        self.addCleanup(release.set)
        saved: list[str] = []

        with self.assertRaises(jobs.JobBusyError):
            relocate.begin(str(bigger), lambda: saved.append("saved"))

        release.set()
        self.assertEqual(saved, [])
        self.assertEqual(blocker.kind, jobs.KIND_BACKUPS_MOVE)

    def test_a_new_folder_that_cannot_be_written_to_refuses_even_with_nothing_to_move(
            self) -> None:
        saved: list[str] = []

        with self.assertRaises(service_errors.BlockedError):
            relocate.begin(str(self.root / "gone"), lambda: saved.append("saved"))

        self.assertEqual(saved, [])

    def test_a_name_taken_after_the_check_is_not_replaced(self) -> None:
        source = self.take()
        bigger = self.root / "bigger"
        bigger.mkdir()
        (bigger / source.name).write_bytes(b"arrived meanwhile")

        self.assertFalse(relocate._claim(source, bigger / source.name))

        self.assertEqual((bigger / source.name).read_bytes(), b"arrived meanwhile")

    def test_a_second_name_for_the_same_folder_moves_nothing(self) -> None:
        self.take()
        alias = self.root / "alias"
        alias.symlink_to(self.default, target_is_directory=True)

        self.assertEqual(relocate.plan(str(alias)).moving, 0)


class WhichBackupsAreBelievedTests(BackupFolderCase):
    def test_a_backup_in_a_folder_nobody_chose_is_not_found(self) -> None:
        """An id carries a path, so a zip in some other folder must not be read as ours."""
        stranger = self.root / "stranger"
        stranger.mkdir()
        alien = stranger / "vpinfe-backup-20261001T010203Z.zip"
        alien.write_bytes(b"not ours")

        self.assertIsNone(places.archive_copy(alien))

    def test_a_backup_in_the_chosen_folder_is_found(self) -> None:
        bigger = self.root / "bigger"
        bigger.mkdir()
        self.choose(bigger)
        path = self.take()

        found = places.archive_copy(path)

        self.assertIsNotNone(found)
        self.assertEqual(found.path, str(path))  # type: ignore[union-attr]


class StatusTests(BackupFolderCase):
    def test_the_default_is_fine_before_it_exists(self) -> None:
        self.assertEqual(backup_folder.status(),
                         {"path": str(self.default), "problem": ""})

    def test_a_chosen_folder_that_is_not_there_says_so(self) -> None:
        self.choose(self.root / "gone")

        found = backup_folder.status()

        self.assertEqual(found["path"], str(self.root / "gone"))
        self.assertIn("Nothing is at that path", found["problem"])

    def test_a_chosen_folder_that_works_has_no_problem(self) -> None:
        bigger = self.root / "bigger"
        bigger.mkdir()
        self.choose(bigger)

        self.assertEqual(backup_folder.status()["problem"], "")


if __name__ == "__main__":
    unittest.main()
