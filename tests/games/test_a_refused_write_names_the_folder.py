"""A write a game folder refuses names the folder, not a file VPinFE made beside its target,
and leaves no such file behind.

Each write goes into a real folder made read-only, and the failure is read the way the
Manager shows it, through `why()`.
"""

from __future__ import annotations

import errno
import json
import stat
import zipfile
from collections.abc import Callable
from pathlib import Path
from unittest import mock

from common.atomic_write import naming_folder, staged_for
from common.failures import why
from common.games import asset_ops
from common.games.game_service import replace_table
from common.games.info_migration import replace_atomic, write_json_atomic
from common.i18n import t
from common.kept_copies import RESTORE, copy_aside, write_backup
from common.online import vpsdb_media
from common.uploads.asset_analyzer_service import analyze_path
from common.uploads.asset_import_service import (
    build_import_plan,
    execute_import_plan,
    select_plan_items,
)
from tests.support.library import TempTree, fake_game, game_info, write_game
from tests.support.saved import save_record
from tests.support.skips import needs_posix_permissions
from tests.support.swaps import library_of

GAME_ID = "Refused001"
FOLDER = "Locked Game (Original 2024)"


@needs_posix_permissions
class RefusedWriteTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        info = {"Info": {"Name": "Locked Game"}, "vpinfe": {"game_id": GAME_ID}}
        self.folder = write_game(self.root, FOLDER, info=info)
        self.info = self.folder / f"{FOLDER}.info"
        self.game = fake_game(self.folder, FOLDER, meta=info)
        self.folder.chmod(0o555)
        self.addCleanup(self.folder.chmod, 0o755)
        # The question asked before a write refuses this folder in its own words; these
        # tests are about the write itself, for a folder that turns read-only after it.
        patcher = mock.patch("common.games.library_folders.writable_game_folder")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.source = self.root / "source"
        self.source.write_text("{}", encoding="utf-8")

    def _assert_names_the_folder(self, write: Callable[[], object],
                                 folder: Path | None = None) -> None:
        with self.assertRaises(OSError) as caught:
            write()
        self.assertEqual(why(caught.exception),
                         t("said.why.no_permission_at", path=str(folder or self.folder)))

    def _import(self, members: dict[str, bytes], action: str = "") -> None:
        drop = self.root / "drop.zip"
        with zipfile.ZipFile(drop, "w") as archive:
            for name, data in members.items():
                archive.writestr(name, data)
        plan = build_import_plan(analyze_path(drop), game_dir=self.folder)
        if action:
            plan = select_plan_items(plan, [index for index, item in enumerate(plan.items)
                                            if item.action == action])
        with mock.patch("common.uploads.asset_import_service.refresh_game"):
            execute_import_plan(plan, drop)

    def test_an_atomic_write(self) -> None:
        self._assert_names_the_folder(lambda: write_json_atomic(self.info, {}))

    def test_a_copy_put_in_its_place(self) -> None:
        self._assert_names_the_folder(lambda: replace_atomic(self.source, self.info))

    def test_the_restore_point_an_upgrade_keeps(self) -> None:
        self._assert_names_the_folder(lambda: write_backup(self.info, "{}"))

    def test_the_copy_a_restore_sets_aside(self) -> None:
        self._assert_names_the_folder(lambda: copy_aside(self.info, RESTORE))

    def test_a_placed_file(self) -> None:
        source = self.root / "new.directb2s"
        source.write_bytes(b"backglass")

        with mock.patch("common.games.game_repository.catalog",
                        return_value={GAME_ID: self.game}):
            self._assert_names_the_folder(
                lambda: asset_ops.place_file(GAME_ID, "backglass", "", source))

    def test_a_table_replaced_under_its_own_name(self) -> None:
        with mock.patch("common.games.game_service.refresh_game"):
            self._assert_names_the_folder(lambda: replace_table(
                self.folder, f"{FOLDER}.vpx", b"vpx", "vpx", f"{FOLDER}.vpx"))

    def test_a_table_replaced_under_a_new_name(self) -> None:
        with mock.patch("common.games.game_service.refresh_game"):
            self._assert_names_the_folder(lambda: replace_table(
                self.folder, "New Table.vpx", b"vpx", "vpx", f"{FOLDER}.vpx"))

    def test_an_imported_table(self) -> None:
        self._assert_names_the_folder(lambda: self._import({"New Table.vpx": b"vpx"}))

    def test_an_imported_backglass(self) -> None:
        self._assert_names_the_folder(lambda: self._import({"New Table.directb2s": b"b2s"}))

    def test_an_imported_info(self) -> None:
        info = json.dumps({"Info": {"Name": "Locked Game"}}).encode()
        self._assert_names_the_folder(lambda: self._import(
            {f"{FOLDER}.vpx": b"vpx", f"{FOLDER}.info": info}, action="write_info"))

    def test_an_imported_rom(self) -> None:
        roms = self.folder / "pinmame" / "roms"
        self.folder.chmod(0o755)
        roms.mkdir(parents=True)
        self.folder.chmod(0o555)
        roms.chmod(0o555)
        self.addCleanup(roms.chmod, 0o755)

        self._assert_names_the_folder(lambda: self._import(
            {"mm_105.bin": b"rom", "mm_snd.u7": b"rom", "mm.cpu": b"rom"}), roms)

    def test_art_updated_from_vpinmediadb(self) -> None:
        with mock.patch.object(vpsdb_media, "download_file",
                               side_effect=lambda url, dest: dest.write_bytes(b"art")):
            self._assert_names_the_folder(lambda: vpsdb_media.replace_file(
                "https://example.invalid/wheel.png", self.folder / "wheel.png"))


class NothingStaysBehindTests(TempTree):
    """The move into place is refused, as Windows refuses one over a table VPX has open."""

    def setUp(self) -> None:
        super().setUp()
        self.folder = write_game(self.root, FOLDER, info={"vpinfe": {"game_id": GAME_ID}})
        for patched in (mock.patch("common.games.game_service.refresh_game"),
                        mock.patch("os.replace", side_effect=PermissionError(
                            errno.EACCES, "Permission denied"))):
            patched.start()
            self.addCleanup(patched.stop)

    def _hidden(self) -> list[str]:
        return sorted(one.name for one in self.folder.iterdir() if one.name.startswith("."))

    def test_a_table_replaced_under_its_own_name(self) -> None:
        with self.assertRaises(OSError):
            replace_table(self.folder, f"{FOLDER}.vpx", b"vpx", "vpx", f"{FOLDER}.vpx")

        self.assertEqual(self._hidden(), [])

    def test_a_table_replaced_under_a_new_name(self) -> None:
        with self.assertRaises(OSError):
            replace_table(self.folder, "New Table.vpx", b"vpx", "vpx", f"{FOLDER}.vpx")

        self.assertEqual(self._hidden(), [])


class RenamedOnlyInCaseTests(TempTree):
    """The new table's name differs from the old one's only in case."""

    def setUp(self) -> None:
        super().setUp()
        self.folder = self.root / "Example"
        self.folder.mkdir()
        (self.folder / "example.vpx").write_bytes(b"old")

    def _tables(self) -> list[bytes]:
        return [one.read_bytes() for one in self.folder.glob("*.vpx")]

    def test_a_replaced_table_is_kept(self) -> None:
        with mock.patch("common.games.game_service.refresh_game"):
            replace_table(self.folder, "Example.vpx", b"new", "vpx", "example.vpx")

        self.assertEqual(self._tables(), [b"new"])

    def test_an_imported_table_is_kept(self) -> None:
        drop = self.root / "drop.zip"
        with zipfile.ZipFile(drop, "w") as archive:
            archive.writestr("Example.vpx", b"new")
        info = game_info(self.folder.name, game_id="Game00000001",
                         tables={"t1": {"id": "t1", "filename": "example.vpx"}})
        save_record(self.folder / f"{self.folder.name}.info", info)
        library_of(self, {"Game00000001": fake_game(self.folder, self.folder.name, meta=info)})
        plan = build_import_plan(analyze_path(drop), game_dir=self.folder)

        with mock.patch("common.uploads.asset_import_service.refresh_game"):
            execute_import_plan(plan, drop)

        self.assertEqual(self._tables(), [b"new"])


class StagedForTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        self.target = self.root / "Example.info"

    def _left(self) -> list[str]:
        return sorted(one.name for one in self.root.iterdir())

    def test_a_block_that_ends_cleanly_puts_the_file_in_place(self) -> None:
        with staged_for(self.target) as staged:
            staged.write_text("new", encoding="utf-8")

        self.assertEqual(self.target.read_text(encoding="utf-8"), "new")
        self.assertEqual(self._left(), ["Example.info"])

    def test_a_block_that_raises_leaves_the_target_as_it_was(self) -> None:
        self.target.write_text("old", encoding="utf-8")

        with self.assertRaises(ValueError), staged_for(self.target) as staged:
            staged.write_text("half", encoding="utf-8")
            raise ValueError

        self.assertEqual(self.target.read_text(encoding="utf-8"), "old")
        self.assertEqual(self._left(), ["Example.info"])

    def test_a_failure_on_another_file_in_the_folder_keeps_its_name(self) -> None:
        other = self.root / "Old Table.vpx"

        with self.assertRaises(OSError) as caught, staged_for(self.target):
            raise PermissionError(errno.EACCES, "Permission denied", str(other))

        self.assertEqual(caught.exception.filename, str(other))

    def test_the_file_is_made_as_a_plain_write_makes_one(self) -> None:
        plain = self.root / "plain"
        plain.write_text("x", encoding="utf-8")

        with staged_for(self.target) as staged:
            staged.write_text("x", encoding="utf-8")

        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode),
                         stat.S_IMODE(plain.stat().st_mode))


class NamingFolderTests(TempTree):
    def _named(self, filename: Path) -> object:
        with self.assertRaises(OSError) as caught, naming_folder(self.root / "Example.info"):
            raise PermissionError(13, "Permission denied", str(filename))
        return caught.exception.filename

    def test_a_file_made_beside_the_target_names_the_folder(self) -> None:
        self.assertEqual(self._named(self.root / ".vpinfe_write_abc.tmp"), str(self.root))

    def test_the_target_itself_keeps_its_name(self) -> None:
        target = self.root / "Example.info"

        self.assertEqual(self._named(target), str(target))

    def test_a_file_in_another_folder_keeps_its_name(self) -> None:
        elsewhere = self.root / "other" / "source.info"

        self.assertEqual(self._named(elsewhere), str(elsewhere))
