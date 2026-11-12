"""Putting another file in a table's place, and deleting one table, without losing what the
table is.

A swap keeps the entry: its id, plays, rating, tags, Hidden, the default and the
collections that name it all stay, and the file behind it changes. The old file, and every
file named for it that is not carried to the new one, goes to Backups first, so nothing is
thrown away that cannot be put back. Only a table kept in the game's folder can be swapped
or deleted.

A file named for the folder is shared by every table in it and is never carried, kept or
deleted here. A script is carried only when asked: VPX runs a sidecar script in place of
the table's own.
"""

from __future__ import annotations

import copy
import logging
import shutil
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from common import apps, atomic_write, service_errors
from common.backups import folder as backup_folder
from common.backups import media as backup_media
from common.backups import places, table_entry
from common.backups.copies import PLACE_TABLE, REASON_DELETE, REASON_REPLACE, make_id, read_id
from common.failures import why
from common.games import (
    asset_resolver,
    backglass_order,
    game_lens,
    game_repository,
    info_files,
    info_maintenance,
    order_record,
    table_ops,
    tables,
)
from common.games.asset_resolver import VPX_ASSET_KINDS as _PER_TABLE_KINDS
from common.games.game import Game
from common.games.game_metadata import (
    load_game_meta,
    set_table_source,
    table_source,
    vpinfe_section,
)
from common.games.game_repository import reread_game
from common.games.game_service import companions_beside, find_vps_release
from common.games.info_file import MetaConfig
from common.games.library_enrichment import read_one
from common.games.tables import table_entries
from common.host import launch_state
from common.i18n import t

logger = logging.getLogger("vpinfe.common.games.table_swap")

CARRY_UPDATE = ("ini", "backglass", "pov", "scv", "nix")
CARRY_INSTEAD = ("backglass", "scv", "nix")

_NAMED_FOR_TABLE = (asset_resolver.RESOLUTION_DEDICATED, asset_resolver.RESOLUTION_SUPERSEDED)
_FOLDER_FALLBACK = frozenset(kind.key for kind in _PER_TABLE_KINDS
                             if kind.folder_fallback)


@dataclass(frozen=True)
class _Held:
    """A table about to change: its game, folder and the file its entry names."""

    game: Game
    game_id: str
    folder: Path
    filename: str
    entry: dict


def _held(game_id: str, table_id: str, *, writing: bool = True) -> _Held:
    """The table as the folder holds it. Only a write is refused while the game runs: what a
    swap would do can be read at any time."""
    game = game_lens.game_for_writing(game_id)
    if writing and launch_state.running(game_id, game.game_dir_name):
        raise service_errors.BlockedError(t("error.games.table_running"))
    entry = table_entries(load_game_meta(game)).get(table_id)
    if not isinstance(entry, dict) or tables.entry_form(entry) != tables.FORM_CONTAINED:
        raise service_errors.RefusedError(t("error.games.swap_not_contained"),
                                          details={"table": table_id})
    folder = Path(str(game.full_path_game))
    filename = table_ops.filename_or_refuse(game, table_id)
    if not (folder / filename).is_file():
        raise service_errors.NotFoundError(t("error.games.table_not_found"),
                                           details={"table": table_id})
    own_id = info_maintenance.game_id_in(info_files.info_path_of(game))
    if not own_id:
        raise service_errors.RefusedError(t("error.games.swap_no_id"))
    return _Held(game, own_id, folder, filename, entry)


def named_files(folder: Path, filename: str) -> dict[str, Path]:
    """The files in `folder` named for this table, by kind. A file named for the folder is
    shared and is not among them."""
    files, _ = asset_resolver.folder_listing(folder)
    resolved = asset_resolver.resolve_for_table(filename, folder.name, files)
    return {kind: folder / found["file"] for kind, found in resolved.items()
            if found["resolution"] in _NAMED_FOR_TABLE}


def _kinds_brought(source: Path) -> set[str]:
    """The kinds of file that sit beside `source` under its own stem."""
    beside = {one.name for one in companions_beside(source)}
    return {kind for kind, path in named_files(source.parent, source.name).items()
            if path.name in beside}


def brought(source: Path | None) -> set[str]:
    return _kinds_brought(source) if source is not None and source.is_file() else set()


def default_carry(brings: set[str], instead: bool) -> set[str]:
    """The kinds ticked to carry before anyone chooses: not one the new file brings."""
    return set(CARRY_INSTEAD if instead else CARRY_UPDATE) - brings


def shared_by_name(folder: Path, source_name: str) -> set[str]:
    """The kinds that cannot be renamed to `source_name`: its stem is the folder's, so a
    file under it is the one every table in the folder falls back to."""
    if Path(source_name).stem.lower() != folder.name.lower():
        return set()
    return set(_FOLDER_FALLBACK)


def plan(game_id: str, table_id: str, source: str = "", instead: bool = False,
         brings: Iterable[str] | None = None) -> dict[str, Any]:
    """What a swap would do, for the confirmation to ask about.

    Each file the table has under its own name is listed with whether it is ticked to
    carry. A file the new one brings of its own starts unticked; `brings` names those
    kinds where the new file is not yet on disk beside its companions. `patched` names the
    tables built from this one, which cannot be rebuilt once it goes.
    """
    held = _held(game_id, table_id, writing=False)
    brings = set(brings) if brings is not None else brought(Path(source) if source else None)
    chosen = default_carry(brings, instead)
    shared = shared_by_name(held.folder, Path(source).name) if source else set()
    files = [{"kind": kind, "file": path.name,
              "carry": kind in chosen and kind not in shared,
              "brought": kind in brings}
             for kind, path in named_files(held.folder, held.filename).items()]
    patched = [tables.entry_filename(each)
               for each in table_entries(load_game_meta(held.game)).values()
               if isinstance(each, dict)
               and ((each.get("source") or {}).get("base") or {}).get("file") == held.filename]
    return {"table": table_id, "filename": held.filename,
            "label": describe(held.entry, held.filename), "files": files, "patched": patched}


def describe(entry: dict, filename: str) -> str:
    """What a person calls this table: its version and authors from the VPS release it is
    tied to, else from the file itself, else the file name."""
    release = find_vps_release(str((table_source(entry) or {}).get("vps_file_id") or ""))
    version = str(release.get("version") or "")
    authors = [str(one) for one in release.get("authors") or []]
    if not version and not authors:
        version = str(entry.get("version") or "")
        authors = tables.parse_authors(entry.get("authors"))
    parts = [version, ", ".join(authors[:2])]
    return " \u00b7 ".join(one for one in parts if one) or filename


def game_id_of(folder: Path) -> str:
    """The id of the game kept in `folder`, or "" where the library does not hold one."""
    wanted = folder.resolve()
    return next((found for found, game in game_repository.catalog().items()
                 if Path(str(game.full_path_game)).resolve() == wanted), "")


def choices(game_id: str) -> list[dict[str, Any]]:
    """The tables of a game that can be swapped or deleted, the one it plays first.

    Each is its id, file name, label and whether it is the game's default. A table kept
    anywhere but in the game's folder is not among them.
    """
    game = game_lens.game_or_refuse(game_id)
    meta = load_game_meta(game)
    entries = table_entries(meta)
    folder = Path(str(game.full_path_game))
    files, _ = asset_resolver.folder_listing(folder)
    default, _entry = tables.default_entry(entries, tables.recorded_default(
        vpinfe_section(meta)), files)
    return [{"id": found, "filename": tables.entry_filename(entry),
             "label": describe(entry, tables.entry_filename(entry)), "default": found == default}
            for found, entry in entries.items()
            if isinstance(entry, dict) and tables.entry_form(entry) == tables.FORM_CONTAINED
            and (folder / tables.entry_filename(entry)).is_file()]


def _kept(copies: Iterable[Path]) -> list[dict]:
    found = (places.find(make_id(PLACE_TABLE, str(path))) for path in copies)
    return [one.as_dict() for one in found if one is not None]


def _keep(held: _Held, files: Iterable[Path], reason: str = REASON_REPLACE) -> list[Path]:
    """Copy each file into Backups, refusing before the first when nothing can be written."""
    folder = backup_folder.for_writing()
    return [backup_media.keep(folder, held.game_id, each,
                              each.relative_to(held.folder).as_posix(), reason,
                              area=backup_media.TABLES)
            for each in files if each.is_file()]


def _one_file(one: Path, other: Path) -> bool:
    """Two names the folder holds as one file, as a case-insensitive folder does for names
    that differ only in case: removing the target would remove the file being renamed to it."""
    return one == other or (one.exists() and other.exists() and one.samefile(other))


def _check_source(held: _Held, source: Path) -> None:
    if not source.is_file():
        raise service_errors.RefusedError(t("error.games.not_file"), details={"path": str(source)})
    player = apps.app_for(source.name)
    if player is None:
        raise service_errors.RefusedError(t("error.games.no_player", name=source.name))
    if player.id != (tables.app_of(held.entry) or apps.default_app().id):
        raise service_errors.RefusedError(t("error.games.swap_other_app", name=source.name))
    here = held.folder / held.filename
    if source.samefile(here):
        raise service_errors.RefusedError(t("error.games.swap_same_file"))
    target = held.folder / source.name
    if target.exists() and not (target.samefile(here) if here.exists() else False):
        raise service_errors.BlockedError(t("error.games.file_exists"),
                                          details={"filename": source.name})


def _undo_staging(old: Path, new: Path, kept_old: Path) -> None:
    """The entry did not take the new file: put the folder back as it was."""
    if not _one_file(new, old):
        new.unlink(missing_ok=True)
        return
    try:
        with atomic_write.staged_for(old) as staged:
            shutil.copy2(kept_old, staged)
    except OSError as exc:
        logger.warning("Could not put %s back from Backups: %s", old.name, exc)


def replace(game_id: str, table_id: str, path: str, *, carry: Iterable[str] | None = None,
            instead: bool = False, vps_file_id: str = "") -> dict[str, Any]:
    """Make the file at `path` this table, keeping the table.

    `carry` is the kinds of file named for the old table that are renamed to the new one;
    none given takes the defaults for an update, or for another release with `instead`.
    The old file and every file named for it that is not carried go to Backups, and the
    answer lists those copies. `vps_file_id` ties the table to that release afterwards.

    A new file's own companions are placed only where nothing already is, so a carried
    file wins over one the new file brings. Refused before anything changes when the file
    cannot be played by this table's app, its name is taken, or Backups cannot be written.
    """
    held = _held(game_id, table_id)
    source = Path(path)
    tied = copy.deepcopy(held.entry.get("source"))
    _check_source(held, source)
    wanted = str(vps_file_id or "").strip()
    if wanted:
        table_ops.refuse_unplayable_release(find_vps_release(wanted), held.entry, table_id, wanted)

    chosen = default_carry(brought(source), instead) if carry is None else set(carry)
    chosen -= shared_by_name(held.folder, source.name)
    old = held.folder / held.filename
    new = held.folder / source.name
    new_stem = Path(source.name).stem
    named = named_files(held.folder, held.filename)

    moves: dict[Path, Path] = {}
    for kind, file in named.items():
        target = file.with_name(new_stem + file.suffix)
        if kind in chosen and target != file:
            moves[file] = target
    leaving = [file for kind, file in named.items() if kind not in chosen]
    displaced = [target for file, target in moves.items()
                 if target.is_file() and not _one_file(file, target)]
    copies = _keep(held, [old, *leaving, *displaced])
    table_entry.keep(held.game_id, held.filename, held.entry,
                     describe(held.entry, held.filename), REASON_REPLACE)

    try:
        with atomic_write.staged_for(new) as staged:
            shutil.copy2(source, staged)
        if not MetaConfig(str(info_files.info_path_of(held.game))).swap_table(
                table_id, new.name, read_one(new)):
            raise service_errors.RefusedError(t("error.games.swap_not_contained"),
                                              details={"table": table_id})
    except Exception as exc:
        _undo_staging(old, new, copies[0])
        if isinstance(exc, OSError):
            raise service_errors.BlockedError(why(exc)) from exc
        raise

    try:
        for file in leaving:
            file.unlink(missing_ok=True)
        for file, target in moves.items():
            if not _one_file(file, target):
                target.unlink(missing_ok=True)
            file.rename(target)
        for each in companions_beside(source):
            beside = held.folder / each.name
            if not beside.exists():
                shutil.copy2(each, beside)
        if not _one_file(new, old):
            old.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("Replacing %s left files behind: %s", held.filename, exc)
        raise service_errors.BlockedError(why(exc)) from exc
    finally:
        game = reread_game(held.game)
        backglass_order.settle(held.game_id)

    if wanted:
        set_table_source(game, new.name, wanted)
        game = reread_game(game)
    return {"table": table_ops.row_or_refuse(game, table_id), "kept": _kept(copies),
            "source": tied}


def delete(game_id: str, table_id: str) -> dict[str, Any]:
    """Delete one table's file and the files named for it, after copying each to Backups.

    The entry goes with them, and a copy of it is kept so restoring the file brings it back.
    The game plays its next table, or none when this was its last, keeping its media and
    settings. A collection that named the table plays the game's default instead.
    """
    held = _held(game_id, table_id)
    files = [held.folder / held.filename, *named_files(held.folder, held.filename).values()]
    copies = _keep(held, files, REASON_DELETE)
    table_entry.keep(held.game_id, held.filename, held.entry,
                     describe(held.entry, held.filename), REASON_DELETE)
    try:
        for each in files:
            each.unlink(missing_ok=True)
        MetaConfig(str(info_files.info_path_of(held.game))).drop_table(table_id)
        order_record.drop([table_id])
    except OSError as exc:
        raise service_errors.BlockedError(why(exc)) from exc
    finally:
        reread_game(held.game)
    return {"deleted": True, "table": table_id, "filename": held.filename,
            "kept": _kept(copies)}


def undo(game_id: str, table_id: str, copy_ids: Iterable[str],
         source: dict[str, Any] | None = None) -> dict[str, Any]:
    """Swap back to the table file `copy_ids` kept, with its files.

    What is named for the table now goes to the old file's name, and the files that went
    to Backups with it come back where nothing is. The file swapped out goes to Backups in
    turn, so the undo can be undone. `source` is the release tie the swap answered with,
    given back to the table.
    """
    held = _held(game_id, table_id)
    kept = [one for one in (_copy_in_backups(each, held.game_id) for each in copy_ids)
            if one is not None]
    back = next((one for one in kept if apps.app_for(Path(one[1]).name) is not None), None)
    if back is None:
        raise service_errors.NotFoundError(t("error.backups.gone"))
    with tempfile.TemporaryDirectory() as scratch:
        staged = Path(scratch) / Path(back[1]).name
        shutil.copy2(back[0], staged)
        done = replace(game_id, table_id, str(staged),
                       carry=set(named_files(held.folder, held.filename)))
    for path, relative in kept:
        target = held.folder.joinpath(*relative.split("/"))
        if path != back[0] and not target.exists():
            shutil.copy2(path, target)
    if source:
        MetaConfig(str(info_files.info_path_of(held.game))).restore_table_source(table_id, source)
    game = reread_game(held.game)
    return {**done, "table": table_ops.row_or_refuse(game, table_id)}


def _copy_in_backups(copy_id: str, game_id: str) -> tuple[Path, str] | None:
    """A kept copy of this game's table files as its path and the name it had, or None."""
    found = read_id(copy_id)
    held = None if found is None else backup_media.where(found[1])
    if (found is None or held is None or held.area != backup_media.TABLES
            or held.game_id != game_id or not found[1].is_file()):
        return None
    return found[1], held.relative
