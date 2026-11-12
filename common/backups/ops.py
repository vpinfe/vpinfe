"""What a client does with a kept copy: list them, put one back, delete one.

A copy is named by the id the listing gave. Nothing here believes a path it was not given
by `places.find`, so an id built by hand reaches nothing.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from common import atomic_write, service_errors
from common.config import path_state
from common.failures import why
from common.games import (
    asset_ops,
    config_backups,
    game_lens,
    game_repository,
    info_files,
    info_maintenance,
    launcher_ops,
    launchers,
    library_folders,
    media_ops,
)
from common.games.info_file import MetaConfig
from common.games.info_migration import copied_from
from common.host import launch_state
from common.i18n import t
from common.runtime.jobs import KIND_LIBRARY_SCAN, track

from . import archive, pending, places, table_entry
from . import folder as backup_folder
from . import media as media_files
from .copies import (
    KIND_GAME,
    KIND_LAUNCHER,
    PLACE_ARCHIVE,
    PLACE_ASSET,
    PLACE_BESIDE,
    PLACE_GAME,
    PLACE_MEDIA,
    PLACE_RECORDS,
    PLACE_TABLE,
    PLACE_THEME,
    PLACE_VPX_2X,
    REASON_RESTORE,
    Copy,
    make_id,
    make_member_id,
    read_id,
    read_member_id,
)


@dataclass(frozen=True)
class Restored:
    """`kept` is the copies of what the restore replaced, so putting each back is another
    restore of that id. `pending` is a restore that waits for VPinFE to close, and
    `skipped` the parts of a backup that had nowhere to go, each with its reason."""

    copy: Copy
    kept: list[Copy] = field(default_factory=list)
    pending: bool = False
    skipped: list[dict[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"copy": self.copy.as_dict(), "kept": [one.as_dict() for one in self.kept],
                "pending": self.pending, "skipped": self.skipped}


def listing(subject_kind: str = "", subject_id: str = "") -> dict[str, Any]:
    return {"copies": [one.as_dict() for one in places.scan(subject_kind, subject_id)],
            "pending": pending.waiting(), "folder": backup_folder.status()}


def _found(copy_id: str) -> Copy:
    one = places.find(copy_id)
    if one is None:
        raise service_errors.NotFoundError(t("error.backups.gone"))
    return one


def _kept_copy(path: str | None, place: str) -> Copy | None:
    return places.find(make_id(place, path)) if path else None


def _kept(found: Copy | None) -> list[Copy]:
    return [found] if found else []


def _while_nothing_plays() -> None:
    if launch_state.current().launching:
        raise service_errors.BlockedError(t("error.backups.playing"))


def take(scope: str, *, game_ids: list[str], launcher_id: str, note: str, name: str = "") -> Copy:
    path = archive.take(scope, game_ids=game_ids, launcher_id=launcher_id, note=note, name=name)
    return _found(make_id(PLACE_ARCHIVE, path))


def restore(copy_id: str) -> Restored:
    one = _found(copy_id)
    if (member := read_member_id(copy_id)) is not None:
        return _restore_member(one, *member)
    place, path = read_id(copy_id) or ("", Path())
    if place == PLACE_ARCHIVE:
        return _restore_archive(one, path)
    if place == PLACE_GAME:
        return _restore_game(one, path)
    if place == PLACE_RECORDS:
        return _restore_record(one, path)
    if place == PLACE_THEME:
        return _restore_theme(one, path)
    if place in (PLACE_MEDIA, PLACE_ASSET, PLACE_TABLE):
        return _restore_file(one, place, path)
    return _restore_launcher(one, place, path)


def _restore_game(one: Copy, path: Path) -> Restored:
    folder = path.parent
    target = info_files.game_info_path(folder)
    try:
        with track(KIND_LIBRARY_SCAN, title=t("said.title_backup_restore")):
            kept = info_maintenance.restore_file(target, path, keep_id=True)
    except ValueError as exc:
        raise service_errors.RefusedError(why(exc)) from exc
    game_repository.refresh_games()
    return Restored(one, _kept(_kept_copy(kept, PLACE_GAME)))


def _restore_file(one: Copy, place: str, path: Path) -> Restored:
    """Put a game's file back where it was, keeping the file it replaces as a copy of its own."""
    held = media_files.where(path)
    if held is None:
        raise service_errors.NotFoundError(t("error.backups.gone"))
    game = game_lens.game_for_writing(held.game_id)
    if place == PLACE_TABLE and launch_state.running(held.game_id, game.game_dir_name):
        raise service_errors.BlockedError(t("error.games.table_running"))
    game_dir = Path(str(game.full_path_game))
    target = game_dir.joinpath(*held.relative.split("/"))
    try:
        replaced = (media_files.keep(held.folder, held.game_id, target, held.relative,
                                     REASON_RESTORE, area=held.area)
                    if target.is_file() else None)
        target.parent.mkdir(parents=True, exist_ok=True)
        with atomic_write.staged_for(target) as staged:
            shutil.copy2(path, staged)
    except OSError as exc:
        raise service_errors.RefusedError(why(exc)) from exc
    if place == PLACE_TABLE and (record := table_entry.beside(path)) is not None:
        MetaConfig(str(info_files.info_path_of(game))).restore_table(record)
    media_ops.reread(game_dir)
    asset_ops.refresh_if_backglass(held.game_id, target)
    return Restored(one, _kept(_kept_copy(str(replaced) if replaced else None, place)))


def _restore_record(one: Copy, path: Path) -> Restored:
    target = copied_from(path)
    pending.add(target, path, copy_id=one.id)
    return Restored(one, pending=True)


def _launcher_of(one: Copy) -> launchers.Launcher:
    found = launchers.get_launcher_store().get(one.subject_id)
    if found is None:
        raise service_errors.NotFoundError(t("error.backups.no_owner", name=one.subject_name))
    return found


def _restore_launcher(one: Copy, place: str, path: Path) -> Restored:
    _while_nothing_plays()
    owner = _launcher_of(one)
    files = launcher_ops.config_files(owner)
    target = _target_for_aside(place, path, files)
    if target is None:
        raise service_errors.RefusedError(t("error.launchers.copy_unmatched"))
    kept = info_maintenance.restore_file(target, path)
    return Restored(one, _kept(_kept_copy(kept, PLACE_BESIDE)))


def _target_for_aside(place: str, path: Path, files: dict[str, str]) -> Path | None:
    if place == PLACE_VPX_2X:
        found = places.INI_2X_NAME.match(path.stem)
        stem = found.group("stem") if found else path.stem
        return next((Path(held).expanduser() for held in files.values()
                     if Path(held).expanduser().stem == stem), None)
    return config_backups.target_for(path, files)


def _restore_theme(one: Copy, path: Path) -> Restored:
    """Swap the theme with its `.previous` folder, so each is the other's undo."""
    live = path.parent / one.subject_id
    if not live.exists():
        os.rename(path, live)
        return Restored(one)
    parked = Path(tempfile.mkdtemp(prefix=".swapping-", dir=path.parent))
    parked.rmdir()
    os.rename(live, parked)
    os.rename(path, live)
    os.rename(parked, path)
    with contextlib.suppress(OSError):
        os.utime(path)
    return Restored(one, _kept(_kept_copy(str(path), PLACE_THEME)))


def _put_game(path: Path, member: archive.Member) -> Copy | None:
    folder = archive.game_folder(member)
    if folder is None:
        raise service_errors.NotFoundError(t("error.backups.no_game", id=member.subject_name))
    library_folders.writable_game_folder(folder)
    with tempfile.TemporaryDirectory() as scratch:
        staged = archive.extract(path, member, Path(scratch))
        kept = info_maintenance.restore_file(info_files.game_info_path(folder), staged,
                                             keep_id=True)
    return _kept_copy(kept, PLACE_GAME)


def _put_launcher(path: Path, member: archive.Member) -> Copy | None:
    _while_nothing_plays()
    owner = launchers.get_launcher_store().get(member.subject_id)
    if owner is None:
        raise service_errors.NotFoundError(t("error.backups.no_owner", name=member.subject_name))
    held = [Path(file).expanduser() for file in launcher_ops.config_files(owner).values()]
    wanted = Path(member.path).name
    target = next((one for one in held if one.name == wanted), None)
    if target is None and owner.owns_ini and len(held) == 1:
        target = held[0]
    if target is None:
        raise service_errors.RefusedError(t("error.launchers.copy_unmatched"))
    path_state.require(str(target.parent), path_state.DIR, writable=True,
                       name=target.parent.name)
    with tempfile.TemporaryDirectory() as scratch:
        kept = info_maintenance.restore_file(target, archive.extract(path, member,
                                                                      Path(scratch)))
    return _kept_copy(kept, PLACE_BESIDE)


def _put_record(path: Path, member: archive.Member) -> None:
    if member.path not in archive.RECORD_KINDS:
        raise service_errors.RefusedError(t("error.backups.unreadable"))
    pending.stage(archive.record_folder(member.path) / member.path, path, member,
                  make_member_id(path, member.name))


def _put_member(path: Path, member: archive.Member) -> tuple[Copy | None, bool]:
    """One file of a backup back in place: what it replaced, and whether it waits."""
    if member.kind == KIND_GAME:
        return _put_game(path, member), False
    if member.kind == KIND_LAUNCHER:
        return _put_launcher(path, member), False
    _put_record(path, member)
    return None, True


def _putting_back(members: list[archive.Member]) -> contextlib.AbstractContextManager[Any]:
    return (track(KIND_LIBRARY_SCAN, title=t("said.title_put_back"))
            if any(one.kind == KIND_GAME for one in members)
            else contextlib.nullcontext())


def _manifest_of(path: Path) -> archive.Manifest:
    manifest = archive.read(path)
    if manifest is None:
        raise service_errors.RefusedError(t("error.backups.unreadable"))
    return manifest


def _restore_member(one: Copy, path: Path, name: str) -> Restored:
    member = next((each for each in _manifest_of(path).members if each.name == name), None)
    if member is None:
        raise service_errors.NotFoundError(t("error.backups.gone"))
    try:
        with _putting_back([member]):
            kept, waits = _put_member(path, member)
    except (OSError, ValueError) as exc:
        raise service_errors.RefusedError(why(exc)) from exc
    if member.kind == KIND_GAME:
        game_repository.refresh_games()
    return Restored(one, _kept(kept), waits)


def _restore_archive(one: Copy, path: Path) -> Restored:
    members = list(_manifest_of(path).members)
    kept: list[Copy] = []
    skipped: list[dict[str, str]] = []
    waits = False
    try:
        with _putting_back(members):
            for member in members:
                try:
                    found, waiting = _put_member(path, member)
                except (service_errors.ServiceError, OSError, ValueError) as exc:
                    skipped.append({"name": member.subject_name or member.path,
                                    "file": Path(member.path).name, "reason": why(exc)})
                    continue
                kept += _kept(found)
                waits = waits or waiting
    finally:
        if any(each.kind == KIND_GAME for each in members):
            game_repository.refresh_games()
    return Restored(one, kept, waits, skipped)


def set_note(copy_id: str, note: str) -> Copy:
    """The note on a backup the user took. A file in one and a copy VPinFE took have none."""
    _found(copy_id)
    held = read_id(copy_id)
    if held is None or held[0] != PLACE_ARCHIVE:
        raise service_errors.RefusedError(t("error.backups.no_note"))
    archive.set_note(held[1], note)
    return _found(copy_id)


def delete(copy_id: str) -> Copy:
    one = _found(copy_id)
    if read_member_id(copy_id) is not None:
        raise service_errors.RefusedError(t("error.backups.member_kept"))
    place, path = read_id(copy_id) or ("", Path())
    members = places.archive_copies(path)[1:] if place == PLACE_ARCHIVE else []
    if place == PLACE_THEME:
        shutil.rmtree(path)
    else:
        record = table_entry.beside_path(path) if place == PLACE_TABLE else None
        archive.remove(path) if place == PLACE_ARCHIVE else path.unlink()
        if record is not None:
            record.unlink(missing_ok=True)
        if place in (PLACE_MEDIA, PLACE_ASSET, PLACE_TABLE):
            media_files.prune(path)
        for each in members:
            pending.drop(each.id)
    pending.drop(path)
    pending.drop(copy_id)
    return one
