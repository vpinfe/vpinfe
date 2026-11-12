"""Back up now: the zip one press writes, and how it is read back.

A zip holds `manifest.json` and one member per file. What is in a member is a copy of the
file; where it goes back is worked out when it is restored, from the manifest's id and name,
never from a path in the zip.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import re
import shutil
import threading
import zipfile
import zlib
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from common import service_errors
from common.atomic_write import staged_for
from common.config.paths import CONFIG_DIR, STATE_DIR
from common.games import game_repository, info_files, info_maintenance, launcher_ops, launchers
from common.games.collection_store import COLLECTIONS_NAME, COLLECTIONS_NAME_INI
from common.i18n import t
from common.vpinfe_version import get_version

from . import folder as backup_folder
from .copies import (
    BY_2X,
    BY_3X,
    KIND_COLLECTIONS,
    KIND_DEVICES,
    KIND_GAME,
    KIND_LAUNCHER,
    KIND_PLAYERS,
    KIND_RECORDS,
    KIND_SETTINGS,
    VPINFE_NAME,
    writer_of_info,
)

logger = logging.getLogger("vpinfe.common.backups.archive")

NAME_PREFIX = "vpinfe-backup-"
NAME_SUFFIX = ".zip"
MANIFEST = "manifest.json"
FORMAT = 1
MAX_MEMBER_BYTES = 64 * 1024 * 1024
STAMP = "%Y%m%dT%H%M%SZ"

SCOPE_GAMES = "games"
SCOPE_ALL_GAMES = "all_games"
SCOPE_RECORDS = "records"
SCOPE_LAUNCHER = "launcher"
SCOPE_EVERYTHING = "everything"
SCOPES = (SCOPE_GAMES, SCOPE_ALL_GAMES, SCOPE_RECORDS, SCOPE_LAUNCHER, SCOPE_EVERYTHING)

# The records VPinFE keeps in its config folder, by name.
RECORD_KINDS = {
    "vpinfe.json": KIND_SETTINGS, "vpinfe.ini": KIND_SETTINGS,
    COLLECTIONS_NAME: KIND_COLLECTIONS, COLLECTIONS_NAME_INI: KIND_COLLECTIONS,
    "devices.json": KIND_DEVICES, "players.json": KIND_PLAYERS,
    "tags.json": KIND_RECORDS, "ui-preferences.json": KIND_RECORDS,
}

# What reading a damaged or unsupported zip can raise besides OSError.
BROKEN = (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError, RuntimeError)
_TAKING = threading.Lock()

# The records that are state, not config. A restore of one keeps this install's own row.
STATE_RECORDS = ("devices.json",)

_KINDS = {KIND_GAME, KIND_LAUNCHER, *RECORD_KINDS.values()}
_NAME = re.compile(rf"^{re.escape(NAME_PREFIX)}\d{{8}}T\d{{6}}Z(?:-\d+)?{re.escape(NAME_SUFFIX)}$")


@dataclass(frozen=True)
class Member:
    """One file in a backup. `path` is where it was, relative to its location: the game's
    folder and file, a record's name, or an app's settings file's name."""

    name: str
    kind: str
    subject_id: str
    subject_name: str
    path: str
    size: int = 0
    written_by: str = ""


@dataclass(frozen=True)
class Manifest:
    note: str
    taken: str
    version: str
    scope: str
    members: tuple[Member, ...] = field(default_factory=tuple)
    name: str = ""


def is_archive_name(name: str) -> bool:
    return _NAME.match(name) is not None


def _stamp_of(name: str) -> str:
    return name[len(NAME_PREFIX):len(NAME_PREFIX) + 16]


def taken_from_name(name: str) -> str:
    stamp = _stamp_of(name)
    return f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}T{stamp[9:11]}:{stamp[11:13]}:{stamp[13:15]}Z"


def _member_from(item: Any, held: set[str]) -> Member | None:
    if not isinstance(item, dict):
        return None
    name, kind = item.get("name"), item.get("kind")
    if not isinstance(name, str) or name not in held or kind not in _KINDS:
        return None
    size = item.get("size")
    return Member(
        name=name, kind=kind, subject_id=str(item.get("subject_id") or ""),
        subject_name=str(item.get("subject_name") or ""), path=str(item.get("path") or ""),
        size=size if isinstance(size, int) and size >= 0 else 0,
        written_by=str(item.get("written_by") or ""))


def read(path: Path) -> Manifest | None:
    """The manifest of a backup, or None for a file that is not a readable one."""
    try:
        with zipfile.ZipFile(path) as bundle:
            info = bundle.getinfo(MANIFEST)
            if info.file_size > MAX_MEMBER_BYTES:
                return None
            raw = json.loads(bundle.read(info))
            held = {item.filename for item in bundle.infolist()}
    except (OSError, ValueError, KeyError, *BROKEN):
        return None
    if not isinstance(raw, dict) or raw.get("format") != FORMAT:
        return None
    listed = raw.get("members")
    found = (_member_from(item, held) for item in listed) if isinstance(listed, list) else ()
    return Manifest(note=str(raw.get("note") or ""), taken=str(raw.get("taken") or ""),
                    version=str(raw.get("version") or ""), scope=str(raw.get("scope") or ""),
                    members=tuple(one for one in found if one is not None),
                    name=str(raw.get("name") or ""))


def game_folder(member: Member) -> Path | None:
    """The folder a game member goes back to. A member with an id goes to the game that has
    it; only a member without one is matched by its folder's name. Whether the folder can be
    reached is the caller's question, asked of the disk once, not answered here."""
    if member.subject_id:
        folder = game_repository.game_folder(member.subject_id)
    else:
        game = game_repository.by_folder_name(member.subject_name)
        folder = Path(str(game.full_path_game)) if game is not None else None
    return folder


def extract(path: Path, member: Member, into: Path) -> Path:
    """The member written as `into/<its file's name>`, or nothing written at all. Refused
    past MAX_MEMBER_BYTES and for a member that cannot be read."""
    target = into / (Path(member.path).name or "member")
    try:
        with zipfile.ZipFile(path) as bundle:
            info = bundle.getinfo(member.name)
            if info.file_size > MAX_MEMBER_BYTES:
                raise service_errors.RefusedError(t("error.backups.too_big"))
            with staged_for(target) as staged, bundle.open(info) as source, \
                    open(staged, "wb") as sink:
                shutil.copyfileobj(source, sink)
    except (KeyError, *BROKEN) as exc:
        raise service_errors.RefusedError(t("error.backups.unreadable")) from exc
    return target


# --- Taking one ------------------------------------------------------------------------

@dataclass(frozen=True)
class _Gathered:
    member: Member
    data: bytes


def _info_writer(data: bytes) -> str:
    try:
        return writer_of_info(json.loads(data))
    except ValueError:
        return ""


def _game_member(index: int, folder: Path) -> _Gathered | None:
    info = info_files.game_info_path(folder)
    try:
        data = info.read_bytes()
    except OSError:
        return None
    return _Gathered(Member(
        name=f"games/{index:04d}/{info.name}", kind=KIND_GAME,
        subject_id=info_maintenance.game_id_in(info), subject_name=folder.name,
        path=f"{folder.name}/{info.name}", size=len(data),
        written_by=_info_writer(data)), data)


def record_folder(name: str) -> Path:
    """Where a record file lives: the state folder for the registry, else config."""
    return STATE_DIR if name in STATE_RECORDS else CONFIG_DIR


def _record_members() -> list[_Gathered]:
    found = []
    for name, kind in RECORD_KINDS.items():
        try:
            data = (record_folder(name) / name).read_bytes()
        except OSError:
            continue
        found.append(_Gathered(Member(
            name=f"records/{name}", kind=kind, subject_id="", subject_name=VPINFE_NAME, path=name,
            size=len(data), written_by=BY_2X if name.endswith(".ini") else BY_3X), data))
    return found


def _launcher_members(launcher: launchers.Launcher) -> list[_Gathered]:
    found = []
    for named in dict.fromkeys(launcher_ops.config_files(launcher).values()):
        source = Path(named).expanduser()
        try:
            data = source.read_bytes()
        except OSError:
            continue
        found.append(_Gathered(Member(
            name=f"launchers/{launcher.launcher_id}/{source.name}", kind=KIND_LAUNCHER,
            subject_id=launcher.launcher_id, subject_name=launcher.display_name, path=source.name,
            size=len(data)), data))
    return found


def _launcher_named(launcher_id: str) -> launchers.Launcher:
    found = launchers.get_launcher_store().get(launcher_id)
    if found is None:
        raise service_errors.NotFoundError(t("error.backups.no_owner", name=launcher_id))
    return found


def _games(folders: Iterable[Path]) -> list[_Gathered]:
    gathered = (_game_member(index, folder) for index, folder in enumerate(folders, 1))
    return [one for one in gathered if one is not None]


def _gather(scope: str, game_ids: list[str], launcher_id: str) -> list[_Gathered]:
    if scope == SCOPE_GAMES:
        folders = []
        for game_id in dict.fromkeys(game_ids):
            folder = game_repository.game_folder(game_id)
            if folder is None:
                raise service_errors.NotFoundError(t("error.backups.no_game", id=game_id))
            folders.append(folder)
        return _games(folders)
    if scope == SCOPE_ALL_GAMES:
        return _games(info_maintenance.game_dirs())
    if scope == SCOPE_RECORDS:
        return _record_members()
    if scope == SCOPE_LAUNCHER:
        return _launcher_members(_launcher_named(launcher_id))
    if scope == SCOPE_EVERYTHING:
        found = _games(info_maintenance.game_dirs()) + _record_members()
        for each in launchers.get_launcher_store().launchers():
            found += _launcher_members(each)
        return found
    raise service_errors.RefusedError(t("error.backups.scope", scope=scope))


def _free_path(folder: Path, now: datetime) -> Path:
    while True:
        stamp = now.strftime(STAMP)
        path = folder / f"{NAME_PREFIX}{stamp}{NAME_SUFFIX}"
        if not path.exists():
            return path
        now += timedelta(seconds=1)


def take(scope: str, *, game_ids: list[str] | None = None, launcher_id: str = "",
         note: str = "", name: str = "") -> Path:
    """Write one backup and return its path. Refused when there is nothing to put in it. A
    blank name leaves the listing to derive one from what the backup holds."""
    gathered = _gather(scope, game_ids or [], launcher_id)
    if not gathered:
        raise service_errors.RefusedError(t("error.backups.nothing"))
    now = datetime.now(UTC)
    manifest: dict[str, Any] = {
        "format": FORMAT, "name": name.strip(), "note": note.strip(),
        "taken": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "version": get_version(), "scope": scope,
        "members": [asdict(one.member) for one in gathered]}
    folder = backup_folder.for_writing()
    with _TAKING:
        path = _free_path(folder, now)
        with staged_for(path) as staged, zipfile.ZipFile(
                staged, "w", zipfile.ZIP_DEFLATED) as bundle:
            bundle.writestr(MANIFEST, json.dumps(manifest, indent=2))
            for one in gathered:
                bundle.writestr(one.member.name, one.data)
    return path


def _copied(old: zipfile.ZipFile, item: zipfile.ZipInfo, new: zipfile.ZipFile) -> None:
    """One member carried into `new` as it was: its name, its stamp and how it is packed."""
    with old.open(item) as source, new.open(copy.copy(item), "w") as sink:
        shutil.copyfileobj(source, sink)


def set_note(path: Path, note: str) -> None:
    """The backup's note replaced, and nothing else about it. The zip is written again beside
    the old one and moved over it, so its name, and with it its id and Taken, stay."""
    try:
        with _TAKING, staged_for(path) as staged:
            with zipfile.ZipFile(path) as old, zipfile.ZipFile(
                    staged, "w", zipfile.ZIP_DEFLATED) as new:
                info = old.getinfo(MANIFEST)
                if info.file_size > MAX_MEMBER_BYTES:
                    raise service_errors.RefusedError(t("error.backups.unreadable"))
                raw = json.loads(old.read(info))
                if not isinstance(raw, dict) or raw.get("format") != FORMAT:
                    raise service_errors.RefusedError(t("error.backups.unreadable"))
                raw["note"] = note.strip()
                for item in old.infolist():
                    if item.filename == MANIFEST:
                        new.writestr(item.filename, json.dumps(raw, indent=2))
                    else:
                        _copied(old, item, new)
    except FileNotFoundError as exc:
        raise service_errors.NotFoundError(t("error.backups.gone")) from exc
    except (KeyError, ValueError, *BROKEN) as exc:
        raise service_errors.RefusedError(t("error.backups.unreadable")) from exc


def remove(path: Path) -> None:
    """A backup deleted. Under the lock a note edit holds, so its move over the old zip
    cannot bring back one that was deleted while it wrote."""
    with _TAKING:
        path.unlink()


def listed() -> list[Path]:
    found: list[Path] = []
    for folder in backup_folder.listable():
        try:
            names = os.listdir(folder)
        except OSError:
            continue
        found += [folder / name for name in sorted(names) if is_archive_name(name)]
    return found
