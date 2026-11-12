"""Where each kind of kept copy is, and how to read one off the disk.

Every walk here is a listing of a folder or two. The game folders are the long one, and are
read on a thread pool: on a share the listing is the expensive part, and there is one per
game.
"""

from __future__ import annotations

import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from common.config import path_state
from common.config.paths import ASIDE_SUFFIX, CONFIG_DIR, STATE_DIR, get_themes_dir
from common.games import (
    game_repository,
    info_files,
    info_maintenance,
    launcher_ops,
    launchers,
    library_folders,
)
from common.games.collection_store import COLLECTIONS_NAME, COLLECTIONS_NAME_INI
from common.games.game_identity import game_id as game_id_of
from common.games.info_migration import copied_from, is_importer_bak
from common.i18n import t
from common.kept_copies import (
    CONVERT,
    DELETE,
    EDIT,
    IMPORT,
    MOVE,
    REASON_2X,
    REPLACE,
    RESET,
    RESTORE,
    TURN,
    backup_names,
    backup_reason,
    backup_stamp,
    is_copy,
)

from . import archive, table_entry
from . import folder as backup_folder
from . import media as media_files
from .copies import (
    BY_2X,
    BY_3X,
    KIND_ARCHIVE,
    KIND_ASSET,
    KIND_COLLECTIONS,
    KIND_DEVICES,
    KIND_GAME,
    KIND_LAUNCHER,
    KIND_MEDIA,
    KIND_PLAYERS,
    KIND_RECORDS,
    KIND_SETTINGS,
    KIND_TABLE,
    KIND_THEME,
    PLACE_ARCHIVE,
    PLACE_ASSET,
    PLACE_BESIDE,
    PLACE_GAME,
    PLACE_MEDIA,
    PLACE_RECORDS,
    PLACE_TABLE,
    PLACE_THEME,
    PLACE_VPX_2X,
    REASON_CONVERT,
    REASON_DELETE,
    REASON_EDIT,
    REASON_FIRST_CHANGE,
    REASON_IMPORT,
    REASON_MOVE,
    REASON_REPLACE,
    REASON_RESET,
    REASON_RESTORE,
    REASON_TURN,
    REASON_UPDATE,
    REASON_UPGRADE,
    REASON_YOURS,
    SUBJECT_ARCHIVE,
    SUBJECT_GAME,
    SUBJECT_LAUNCHER,
    SUBJECT_THEME,
    SUBJECT_VPINFE,
    VPINFE_NAME,
    Copy,
    games_named,
    iso_from_stamp,
    make_id,
    make_member_id,
    read_id,
    read_member_id,
    writer_of_info,
)

logger = logging.getLogger("vpinfe.common.backups.places")

# 2.x's Manager UI kept VPX's settings copies here, named by local time.
INI_2X_DIR = CONFIG_DIR / "backups" / "vpx_ini"
INI_2X_NAME = re.compile(r"^(?P<stem>.+?)-(?P<reason>manual|pre-restore)"
                          r"(?:-(?P<label>.+?))?-(?P<when>\d{8}-\d{6})$")

_WALK_THREADS = 16

# What a copy in the config folder is of, by the file it was taken of.
_RECORD_KINDS = {
    "vpinfe.ini": KIND_SETTINGS, "vpinfe.json": KIND_SETTINGS,
    COLLECTIONS_NAME_INI: KIND_COLLECTIONS, COLLECTIONS_NAME: KIND_COLLECTIONS,
    "devices.json": KIND_DEVICES, "players.json": KIND_PLAYERS,
}

_REASON_BY_WORD = {RESTORE: REASON_RESTORE, IMPORT: REASON_IMPORT, RESET: REASON_RESET,
                   TURN: REASON_TURN, CONVERT: REASON_CONVERT, EDIT: REASON_EDIT,
                   MOVE: REASON_MOVE, REPLACE: REASON_REPLACE,
                   DELETE: REASON_DELETE}


def _reason_of_name(name: str) -> str:
    return _REASON_BY_WORD.get(backup_reason(name), REASON_UPGRADE)


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _mtime_iso(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    except OSError:
        return ""


def _written_by_info(path: Path) -> str:
    """Who wrote a `.info` copy. A copy of a 2.x file keeps the bare name 2.x looks for, so
    that one is known by its name; every other is read."""
    if is_copy(path) and backup_reason(path) == REASON_2X:
        return BY_2X
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return writer_of_info(data)


# --- Game folders ----------------------------------------------------------------------

def _is_game_folder(folder: Path) -> bool:
    """Whether a folder is one the library reads, so a copy is only ever taken to be a
    game's when it sits in one. A path is resolved only after it has answered: resolving
    one on a dead mount never returns."""
    if not path_state.look(str(folder)).reachable:
        return False
    wanted = path_state.canonical(str(folder))
    parent = path_state.canonical(str(folder.parent))
    for location in library_folders.answering():
        held = path_state.canonical(location.path)
        if location.kind == library_folders.KIND_GAME:
            if held == wanted:
                return True
        elif held == parent and not folder.name.startswith("."):
            return True
    return False


def game_copies(folder: Path) -> list[Copy]:
    """Every copy kept beside this game's `.info`."""
    info_name = info_files.info_name(folder.name)
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    held = [name for name in backup_names(names, info_name) if is_copy(name)]
    held += [name for name in names if is_importer_bak(name, info_name)]
    if not held:
        return []
    game_id = info_maintenance.game_id_in(folder / info_name)
    gone = info_name not in names
    found = []
    for name in held:
        path = folder / name
        if is_importer_bak(name, info_name):
            taken, reason, written_by = _mtime_iso(path), REASON_IMPORT, _written_by_info(path)
        else:
            taken, reason = iso_from_stamp(backup_stamp(name)), _reason_of_name(name)
            written_by = _written_by_info(path)
        found.append(Copy(
            id=make_id(PLACE_GAME, path), subject_kind=SUBJECT_GAME, subject_id=game_id,
            subject_name=folder.name, kind=KIND_GAME, file=name, path=str(path), taken=taken,
            written_by=written_by, reason=reason, note="", size=_size(path), gone=gone))
    return found


def game_copy(path: Path) -> Copy | None:
    folder = path.parent
    if not _is_game_folder(folder):
        return None
    return next((one for one in game_copies(folder) if Path(one.path) == path), None)


def _all_game_copies() -> list[Copy]:
    folders = info_maintenance.game_dirs()
    if not folders:
        return []
    with ThreadPoolExecutor(max_workers=min(_WALK_THREADS, len(folders))) as pool:
        return [one for found in pool.map(game_copies, folders) for one in found]


# --- The config folder -----------------------------------------------------------------

def _record_copy(path: Path) -> Copy | None:
    if not is_copy(path) or not path.is_file():
        return None
    base = copied_from(path).name
    kind = _RECORD_KINDS.get(base, KIND_RECORDS)
    written_by = BY_2X if base.endswith(".ini") else BY_3X if base.endswith(".json") else ""
    return Copy(
        id=make_id(PLACE_RECORDS, path), subject_kind=SUBJECT_VPINFE, subject_id="",
        subject_name=VPINFE_NAME,
        kind=kind, file=path.name, path=str(path),
        taken=iso_from_stamp(backup_stamp(path)), written_by=written_by,
        reason=_reason_of_name(path.name), note="", size=_size(path), gone=False)


def _record_copies() -> list[Copy]:
    found = []
    for folder in (CONFIG_DIR, STATE_DIR):
        try:
            names = os.listdir(folder)
        except OSError:
            continue
        found += [one for name in names if (one := _record_copy(folder / name))]
    return found


def record_copy(path: Path) -> Copy | None:
    return _record_copy(path) if path.parent in (CONFIG_DIR, STATE_DIR) else None


# --- Launchers -------------------------------------------------------------------------

def _launcher_list() -> list[launchers.Launcher]:
    return launchers.get_launcher_store().launchers()


def _settings_files(launcher: launchers.Launcher) -> dict[str, str]:
    try:
        return launcher_ops.config_files(launcher)
    except Exception:  # noqa: BLE001 - an app that cannot say where its file is has no copies to list
        logger.warning("Could not read where %s keeps its settings", launcher.display_name,
                       exc_info=True)
        return {}


def _beside_copies(launcher: launchers.Launcher) -> list[Copy]:
    """The copies kept beside a settings file: VPX's own first-change one, and what a
    restore replaced."""
    found, seen = [], set()
    for named in dict.fromkeys(_settings_files(launcher).values()):
        source = Path(named).expanduser()
        try:
            names = os.listdir(source.parent)
        except OSError:
            continue
        for name in backup_names(names, source.name):
            path = source.parent / name
            if path in seen or not is_copy(name):
                continue
            seen.add(path)
            found.append(Copy(
                id=make_id(PLACE_BESIDE, path), subject_kind=SUBJECT_LAUNCHER,
                subject_id=launcher.launcher_id, subject_name=launcher.display_name,
                kind=KIND_LAUNCHER, file=name, path=str(path),
                taken=iso_from_stamp(backup_stamp(name)), written_by="",
                reason=(REASON_FIRST_CHANGE if backup_reason(name) == REASON_2X
                        else _reason_of_name(name)),
                note="", size=_size(path), gone=False))
    return found


def _x2_copy(path: Path, held: list[launchers.Launcher]) -> Copy | None:
    found = INI_2X_NAME.match(path.stem)
    if not found or path.suffix.lower() != ".ini" or not path.is_file():
        return None
    try:
        local = datetime.strptime(found.group("when"), "%Y%m%d-%H%M%S")
    except ValueError:
        return None
    taken = local.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    owner = launchers.default_for("vpx", held)
    return Copy(
        id=make_id(PLACE_VPX_2X, path), subject_kind=SUBJECT_LAUNCHER,
        subject_id=owner.launcher_id if owner else "",
        subject_name=owner.display_name if owner else "", kind=KIND_LAUNCHER, file=path.name,
        path=str(path), taken=taken, written_by="",
        reason=REASON_RESTORE if found.group("reason") == "pre-restore" else REASON_YOURS,
        note=found.group("label") or "", size=_size(path), gone=owner is None)


def _launcher_copies() -> list[Copy]:
    held = _launcher_list()
    found: list[Copy] = []
    for each in held:
        found += _beside_copies(each)
    if INI_2X_DIR.is_dir():
        found += [one for path in sorted(INI_2X_DIR.iterdir()) if (one := _x2_copy(path, held))]
    return found


def launcher_copy(place: str, path: Path) -> Copy | None:
    held = _launcher_list()
    if place == PLACE_VPX_2X:
        return _x2_copy(path, held) if path.parent == INI_2X_DIR else None
    for each in held:
        for copy in _beside_copies(each):
            if Path(copy.path) == path:
                return copy
    return None


# --- Themes ----------------------------------------------------------------------------

def _folder_size(folder: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(folder):
        for name in files:
            total += _size(Path(root) / name)
    return total


def theme_copy(path: Path) -> Copy | None:
    if (path.parent != get_themes_dir() or not path.name.endswith(ASIDE_SUFFIX)
            or path.is_symlink() or not path.is_dir()):
        return None
    theme = path.name[:-len(ASIDE_SUFFIX)]
    if not theme or theme.startswith("."):
        return None
    return Copy(
        id=make_id(PLACE_THEME, path), subject_kind=SUBJECT_THEME, subject_id=theme,
        subject_name=theme,
        kind=KIND_THEME, file=path.name, path=str(path), taken=_mtime_iso(path),
        written_by="", reason=REASON_UPDATE, note="", size=_folder_size(path),
        gone=not (path.parent / theme).is_dir())


def _theme_copies() -> list[Copy]:
    try:
        names = sorted(os.listdir(get_themes_dir()))
    except OSError:
        return []
    return [one for name in names if (one := theme_copy(get_themes_dir() / name))]


# --- Files of a game ---------------------------------------------------------------------

# What a copy kept in each area is, and where its id says it is kept.
_AREA_COPIES = {media_files.MEDIA: (KIND_MEDIA, PLACE_MEDIA),
                media_files.ASSETS: (KIND_ASSET, PLACE_ASSET),
                media_files.TABLES: (KIND_TABLE, PLACE_TABLE)}

def _game_names() -> dict[str, str]:
    return {game_id_of(game): str(game.game_dir_name) for game in game_repository.all_games()}


def _file_copy(path: Path, names: dict[str, str], area: str) -> Copy | None:
    held = media_files.where(path)
    if held is None or held.area != area or path.is_symlink() or not path.is_file():
        return None
    if area == media_files.TABLES and table_entry.is_entry(held.relative):
        return None
    kind, place = _AREA_COPIES[area]
    return Copy(
        id=make_id(place, path), subject_kind=SUBJECT_GAME, subject_id=held.game_id,
        subject_name=names.get(held.game_id, held.game_id), kind=kind, file=held.relative,
        path=str(path), taken=iso_from_stamp(backup_stamp(path)), written_by="",
        reason=_reason_of_name(path.name), note="", size=_size(path),
        gone=held.game_id not in names,
        label=table_entry.label_beside(path) if area == media_files.TABLES else "")


def _file_copies(game_id: str = "") -> list[Copy]:
    """The media, asset and table copies of every game, or of one: a game's own are under its
    id alone."""
    if game_id and Path(game_id).name != game_id:
        return []
    found: list[Copy] = []
    names = _game_names()
    for place in backup_folder.listable():
        for area in _AREA_COPIES:
            for root, _dirs, files in os.walk(place / area / game_id):
                found += [one for name in sorted(files)
                          if (one := _file_copy(Path(root) / name, names, area))]
    return found


def media_copy(path: Path) -> Copy | None:
    return _file_copy(path, _game_names(), media_files.MEDIA)


def asset_copy(path: Path) -> Copy | None:
    return _file_copy(path, _game_names(), media_files.ASSETS)


def table_copy(path: Path) -> Copy | None:
    return _file_copy(path, _game_names(), media_files.TABLES)


# --- Back up now -----------------------------------------------------------------------

@dataclass(frozen=True)
class _Present:
    """What exists now, to say which parts of a backup have nothing to go back to."""

    game_ids: frozenset[str]
    game_names: frozenset[str]
    launcher_ids: frozenset[str]


def _present() -> _Present:
    games = game_repository.all_games()
    return _Present(
        frozenset(game_id_of(game) for game in games),
        frozenset(str(game.game_dir_name) for game in games),
        frozenset(one.launcher_id for one in _launcher_list()))


def _member_gone(member: archive.Member, present: _Present) -> bool:
    if member.kind == KIND_GAME:
        if member.subject_id:
            return member.subject_id not in present.game_ids
        return member.subject_name not in present.game_names
    if member.kind == KIND_LAUNCHER:
        return member.subject_id not in present.launcher_ids
    return False


def _subject_label(manifest: archive.Manifest) -> str:
    members = manifest.members
    if manifest.name:
        return manifest.name
    if manifest.scope == archive.SCOPE_EVERYTHING:
        return t("backups.subject.everything")
    if manifest.scope == archive.SCOPE_ALL_GAMES:
        return t("backups.subject.all_games")
    if members and all(one.kind == KIND_GAME for one in members):
        return games_named(one.subject_name for one in members)
    if manifest.scope == archive.SCOPE_LAUNCHER and members:
        return members[0].subject_name
    return VPINFE_NAME if members else ""


_SUBJECT_BY_KIND = {KIND_GAME: SUBJECT_GAME, KIND_LAUNCHER: SUBJECT_LAUNCHER}


def _member_copy(path: Path, manifest: archive.Manifest, member: archive.Member,
                 present: _Present) -> Copy:
    return Copy(
        id=make_member_id(path, member.name),
        subject_kind=_SUBJECT_BY_KIND.get(member.kind, SUBJECT_VPINFE),
        subject_id=member.subject_id, subject_name=member.subject_name, kind=member.kind,
        file=Path(member.path).name,
        path=str(path), taken=manifest.taken, written_by=member.written_by, reason=REASON_YOURS,
        note=manifest.note, size=member.size, gone=_member_gone(member, present),
        within=make_id(PLACE_ARCHIVE, path), version=manifest.version)


def archive_copies(path: Path, present: _Present | None = None) -> list[Copy]:
    """The backup, then each file in it."""
    manifest = archive.read(path)
    held = present or _present()
    members = ([_member_copy(path, manifest, one, held) for one in manifest.members]
               if manifest else [])
    taken = (manifest.taken if manifest else "") or archive.taken_from_name(path.name)
    whole = Copy(
        id=make_id(PLACE_ARCHIVE, path), subject_kind=SUBJECT_ARCHIVE, subject_id="",
        subject_name=_subject_label(manifest) if manifest else "", kind=KIND_ARCHIVE,
        file=path.name,
        path=str(path), taken=taken, written_by=BY_3X, reason=REASON_YOURS,
        note=manifest.note if manifest else "", size=_size(path),
        gone=bool(members) and all(one.gone for one in members),
        version=manifest.version if manifest else "")
    return [whole, *members]


def _all_archive_copies() -> list[Copy]:
    held = archive.listed()
    present = _present() if held else None
    return [one for path in held for one in archive_copies(path, present)]


def _is_ours(path: Path) -> bool:
    return (backup_folder.holds(path.parent) and archive.is_archive_name(path.name)
            and not path.is_symlink() and path.is_file())


def archive_copy(path: Path) -> Copy | None:
    return archive_copies(path)[0] if _is_ours(path) else None


def member_copy(copy_id: str) -> Copy | None:
    held = read_member_id(copy_id)
    if held is None or not _is_ours(held[0]):
        return None
    return next((one for one in archive_copies(held[0]) if one.id == copy_id), None)


# --- All of it -------------------------------------------------------------------------

def _ordered(found: list[Copy]) -> list[Copy]:
    unique = {one.id: one for one in found}
    return sorted(unique.values(), key=lambda one: (one.taken, one.path), reverse=True)


def _for_subject(subject_kind: str, subject_id: str) -> list[Copy]:
    """What one game, launcher or theme has: its own copies and its file in each backup, read
    without walking every other game's folder."""
    members = [one for one in _all_archive_copies() if one.within] \
        if subject_kind in (SUBJECT_GAME, SUBJECT_LAUNCHER) else []
    if subject_kind == SUBJECT_GAME:
        folder = game_repository.game_folder(subject_id)
        own = [*(game_copies(folder) if folder is not None else []),
               *_file_copies(subject_id)]
    elif subject_kind == SUBJECT_LAUNCHER:
        own = _launcher_copies()
    elif subject_kind == SUBJECT_THEME:
        own = _theme_copies()
    else:
        return []
    return [one for one in [*own, *members]
            if one.subject_kind == subject_kind and one.subject_id == subject_id]


def scan(subject_kind: str = "", subject_id: str = "") -> list[Copy]:
    """Every copy there is, newest first, or those of one game, launcher or theme."""
    if subject_kind:
        return _ordered(_for_subject(subject_kind, subject_id))
    return _ordered([*_all_game_copies(), *_record_copies(), *_launcher_copies(),
                     *_theme_copies(), *_file_copies(), *_all_archive_copies()])


def find(copy_id: str) -> Copy | None:
    """The copy an id names, or None. The path in an id is only believed in the place the
    id says, and only when that place holds a copy by that name."""
    if (member := member_copy(copy_id)) is not None:
        return member
    held = read_id(copy_id)
    if held is None:
        return None
    place, path = held
    if place == PLACE_ARCHIVE:
        return archive_copy(path)
    if place == PLACE_GAME:
        return game_copy(path)
    if place == PLACE_RECORDS:
        return record_copy(path)
    if place in (PLACE_VPX_2X, PLACE_BESIDE):
        return launcher_copy(place, path)
    if place == PLACE_THEME:
        return theme_copy(path)
    if place == PLACE_MEDIA:
        return media_copy(path)
    if place == PLACE_ASSET:
        return asset_copy(path)
    if place == PLACE_TABLE:
        return table_copy(path)
    return None
