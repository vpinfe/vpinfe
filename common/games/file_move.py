"""Moving a file between a game and its tables.

A file belongs to a tier by its name: the game folder's name serves every table, a table's
stem serves that table alone. Changing who uses a file is therefore a rename inside the
folder the file already sits in, and nothing is copied across a device. The one exception
is a file several tables are to have, which is copied beside it.

Both kinds of file go through one set of rules. A *slot* is the files a kind has at one
stem: a media kind's pages and spellings, or an asset's single file. What a move does is
decided from which tables have a file of their own at that slot, and the whole of it is
run as steps that can all be put back - by a failure part way, or by `undo`.
"""

from __future__ import annotations

import copy
import logging
import os
import secrets
import shutil
import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from common import service_errors
from common.backups import folder as backup_folder
from common.backups import media as backups_media
from common.backups.copies import REASON_DELETE, REASON_MOVE, REASON_REPLACE
from common.failures import why
from common.games import game_lens, info_files, media_ops, media_placement, table_lens
from common.games.asset_registry import BACKGLASS_FILES, spec_for
from common.games.asset_resolver import VPX_ASSET_KINDS
from common.games.game import Game
from common.games.game_repository import game_to_row
from common.host import launch_state
from common.i18n import t
from common.media.media_specs import MEDIA_SPECS

logger = logging.getLogger("vpinfe.common.games.file_move")

MEDIA = "media"
ASSET = "asset"

# What VPX reads only when it is named for the table. A file of these kinds cannot go to
# the game, because no table would read it from there.
TABLE_ONLY = frozenset({"script", "pov"})

_ASSET_KINDS = {kind.key: kind for kind in VPX_ASSET_KINDS}
_MEDIA_KINDS = {spec.kind for spec in MEDIA_SPECS if spec.token}
MEDIA_KINDS = tuple(sorted(_MEDIA_KINDS))
_KEPT = 32

# Journals of moves that can still be undone, newest last.
_done: OrderedDict[str, _Done] = OrderedDict()
_lock = threading.Lock()


@dataclass(frozen=True)
class Table:
    id: str
    stem: str
    filename: str


@dataclass
class Moved:
    """What a move did, for the surface to say."""

    moved: list[dict[str, str]] = field(default_factory=list)
    # Tables that lost their own file to this move, and the name each kept copy was given.
    replaced: list[dict[str, str]] = field(default_factory=list)
    lost: list[str] = field(default_factory=list)
    uses: list[str] = field(default_factory=list)
    changed_for: list[str] = field(default_factory=list)
    stays: bool = False
    undo: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"moved": self.moved, "replaced": self.replaced, "lost": self.lost,
                "uses": self.uses, "changed_for": self.changed_for, "stays": self.stays,
                "undo": self.undo}


# --- slots ------------------------------------------------------------------------


def _slot_kind_or_refuse(family: str, kind: str) -> str:
    known = _MEDIA_KINDS if family == MEDIA else set(_ASSET_KINDS) if family == ASSET else set()
    if kind not in known:
        raise service_errors.RefusedError(t("error.file_move.unknown_kind"),
                                          details={"family": family, "kind": kind})
    return kind


def movable(family: str, kind: str) -> bool:
    """Whether a file of this kind can be moved between a game and its tables."""
    return kind in (_MEDIA_KINDS if family == MEDIA else _ASSET_KINDS if family == ASSET
                    else ())


def label_of(family: str, kind: str) -> str:
    if family == MEDIA:
        return next(spec.label for spec in MEDIA_SPECS if spec.kind == kind)
    return spec_for(kind).label


def has_fallback(family: str, kind: str) -> bool:
    """Whether a file at the folder's name is read by tables named otherwise. A point of
    view is not: it is the folder-named table's alone."""
    return family == MEDIA or _ASSET_KINDS[kind].folder_fallback


def pieces(game_dir: Path, family: str, kind: str, stem: str) -> list[media_placement.Piece]:
    """The files this kind has at this stem, each with where it would be at another."""
    if family == MEDIA:
        return media_placement.pieces(game_dir, kind, stem)
    extension = _ASSET_KINDS[kind].extension
    wanted = (stem + extension).lower()
    try:
        names = sorted(entry.name for entry in game_dir.iterdir()
                       if entry.is_file() and entry.name.lower() == wanted)
    except OSError:
        return []
    return [media_placement.Piece(game_dir / name, 1,
                                  lambda to_stem: game_dir / (to_stem + extension))
            for name in names[:1]]


def _same(left: str, right: str) -> bool:
    return left.lower() == right.lower()


# --- the plan ---------------------------------------------------------------------


@dataclass(frozen=True)
class Plan:
    """What a move to tables will do, worked out before any file is touched."""

    stays: bool
    selected: list[str]
    readers: list[str]
    lost: list[str]


def plan_to_tables(family: str, kind: str, folder: str, tables: list[str],
                   selected: list[str], own: Callable[[str], bool]) -> Plan:
    """Where a game's file ends up when `selected` tables are to have it.

    The file stays on the game when every table without a file of its own is chosen, since
    nothing then reads it from there but the table named like the folder, which reads it
    under its own name and would lose it otherwise. A kind no table reads from the game
    never stays: moving it is giving it away. A game with one table has nobody to share
    the file with, so choosing that table gives it the file under its own name.
    """
    others = [stem for stem in tables if not _same(stem, folder)]
    chosen: list[str] = []
    for stem in selected:
        match = next((one for one in others if _same(one, stem)), None)
        if match is None:
            raise service_errors.RefusedError(t("error.file_move.folder_named", name=stem))
        if match not in chosen:
            chosen.append(match)
    fallback = has_fallback(family, kind)
    readers = [stem for stem in others if not own(stem)] if fallback else []
    stays = fallback and len(tables) > 1 \
        and all(any(_same(r, c) for c in chosen) for r in readers)
    if stays and not any(own(stem) for stem in chosen):
        raise service_errors.RefusedError(t("error.file_move.nothing"))
    if not stays and not chosen:
        raise service_errors.RefusedError(t("error.file_move.no_table"))
    lost = [] if stays else [
        stem for stem in tables
        if not any(_same(stem, c) for c in chosen) and (_same(stem, folder) or stem in readers)]
    return Plan(stays, chosen, readers, lost)


# --- steps that can be put back ---------------------------------------------------


@dataclass
class _Op:
    kind: str          # "rename", "copy", "delete" or "rmdir"
    path: Path         # where it is now (rename, copy) or was (delete)
    source: Path | None = None   # where it came from (rename) or the kept copy (delete)


@dataclass
class _Done:
    game_id: str
    game_dir: Path
    family: str
    kind: str
    ops: list[_Op]
    ledger: dict[str, dict | None]


def _rename(source: Path, target: Path) -> None:
    if str(source) == str(target):
        return
    if _same(str(source), str(target)):
        # Where the filesystem ignores case, the target is the source: through a name
        # nothing resolves. Where it does not, a file that differs only in case is another.
        parked = source.with_name(source.name + ".moving")
        if os.path.lexists(parked) or (
                os.path.lexists(target) and not os.path.samefile(source, target)):
            raise FileExistsError(str(target))
        source.rename(parked)
        try:
            parked.rename(target)
        except OSError:
            parked.rename(source)
            raise
        return
    if os.path.lexists(target):
        raise FileExistsError(str(target))
    source.rename(target)


def _copy(source: Path, target: Path) -> None:
    if os.path.lexists(target):
        raise FileExistsError(str(target))
    shutil.copy2(source, target)


class _Run:
    """Applies steps in order, remembering each so they can be reversed."""

    def __init__(self, game_id: str, game_dir: Path, family: str) -> None:
        self.game_id = game_id
        self.game_dir = game_dir
        self.family = family
        self.ops: list[_Op] = []
        # Where each file's ledger entry goes: (from, to, whether `from` keeps its own).
        self.ledger_moves: list[tuple[str, str | None, bool]] = []

    def _relative(self, path: Path) -> str:
        return path.relative_to(self.game_dir).as_posix()

    def delete(self, path: Path, reason: str = REASON_MOVE) -> Path:
        """Take a file out, kept in Backups first. Answers the kept copy."""
        kept = backups_media.keep(
            backup_folder.current(), self.game_id, path, self._relative(path), reason,
            area=backups_media.MEDIA if self.family == MEDIA else backups_media.ASSETS)
        path.unlink()
        self.ops.append(_Op("delete", path, kept))
        self.ledger_moves.append((self._relative(path), None, False))
        return kept

    def rename(self, source: Path, target: Path) -> None:
        _rename(source, target)
        self.ops.append(_Op("rename", target, source))
        self.ledger_moves.append((self._relative(source), self._relative(target), False))

    def rename_tree(self, source: Path, target: Path) -> None:
        """Rename a file or a folder. Each file under a folder takes its ledger entry along."""
        under = [one.relative_to(source).as_posix()
                 for one in source.rglob("*") if one.is_file()] if source.is_dir() else []
        self.rename(source, target)
        for name in under:
            self.ledger_moves.append((f"{self._relative(source)}/{name}",
                                      f"{self._relative(target)}/{name}", False))

    def remove_empty(self, folder: Path) -> None:
        folder.rmdir()
        self.ops.append(_Op("rmdir", folder))

    def copy(self, source: Path, target: Path) -> None:
        _copy(source, target)
        self.ops.append(_Op("copy", target, source))
        self.ledger_moves.append((self._relative(source), self._relative(target), True))

    def rollback(self) -> None:
        """Reverse every step, last first. One that cannot be reversed does not stop the
        rest; the first failure is raised after the others have been tried."""
        failure: Exception | None = None
        while self.ops:
            op = self.ops.pop()
            try:
                _reverse(op)
            except Exception as exc:  # noqa: BLE001 - the others still go back
                logger.exception("Could not reverse %s of %s", op.kind, op.path)
                failure = failure or exc
        if failure is not None:
            raise failure


def _reverse(op: _Op) -> None:
    if op.kind == "rename" and op.source is not None:
        _rename(op.path, op.source)
    elif op.kind == "copy":
        op.path.unlink(missing_ok=True)
    elif op.kind == "rmdir":
        op.path.mkdir()
    elif op.kind == "delete" and op.source is not None:
        shutil.copy2(op.source, op.path)
        op.source.unlink(missing_ok=True)
        backups_media.prune(op.source)


def _info(game_dir: Path) -> Path | None:
    info = info_files.game_info_path(game_dir)
    return info if info.is_file() else None


def _ledger(game_dir: Path, run: _Run) -> dict[str, dict | None]:
    """Make the ledger follow the files the run moved, and answer what it held before so
    an undo can write it back. Origin and the VPS match belong to the file, not its name."""
    info = _info(game_dir)
    if info is None or not run.ledger_moves:
        return {}
    from common.games.info_file import MetaConfig

    meta = MetaConfig(str(info))
    keys = {key for source, target, _keeps in run.ledger_moves for key in (source, target)
            if key}
    before = meta.asset_entries(sorted(keys))
    after = dict(before)
    for source, target, keeps in run.ledger_moves:
        entry = copy.deepcopy(after.get(source))
        if not keeps:
            after[source] = None
        if target is not None and entry is not None:
            after[target] = entry
    changed = {key: value for key, value in after.items() if value != before.get(key)}
    if changed:
        meta.rewrite_assets(changed)
    return before


def _restore_ledger(game_dir: Path, before: dict[str, dict | None]) -> None:
    info = _info(game_dir)
    if info is None or not before:
        return
    from common.games.info_file import MetaConfig

    MetaConfig(str(info)).rewrite_assets(before)


# --- the game and its tables ------------------------------------------------------


def tables_of(game: Game) -> list[Table]:
    found = []
    for row in table_lens.table_rows(game, game_to_row(game)):
        filename = str(row.get("filename") or "")
        stem = Path(filename).stem
        if row.get("id") and stem:
            found.append(Table(str(row["id"]), stem, filename))
    return found


def _by_id(tables: list[Table], table_id: str) -> Table:
    found = next((one for one in tables if one.id == table_id), None)
    if found is None:
        raise service_errors.NotFoundError(t("error.games.table_not_found"),
                                           details={"table": table_id})
    return found


def playing(game: Game, game_id: str) -> bool:
    running = launch_state.current()
    return bool(running.launching and (
        running.game_id == game_id
        or (not running.game_id and running.game_name == game.game_dir_name)))


def _writable(game_id: str) -> tuple[Game, Path]:
    game = game_lens.game_for_writing(game_id)
    if playing(game, game_id):
        raise service_errors.BlockedError(t("error.file_move.playing"))
    return game, Path(game.full_path_game or "")


def _finish(game_id: str, game_dir: Path, family: str, kind: str, run: _Run,
            result: Moved) -> dict[str, Any]:
    try:
        before = _ledger(game_dir, run)
    except Exception as exc:  # noqa: BLE001 - said, and put back
        raise _blocked(run, exc) from exc
    token = secrets.token_urlsafe(12)
    with _lock:
        _done[token] = _Done(game_id, game_dir, family, kind, list(run.ops), before)
        while len(_done) > _KEPT:
            _done.popitem(last=False)
    result.undo = token
    _reread(game_id, game_dir, family, kind)
    return result.as_dict()


def _reread(game_id: str, game_dir: Path, family: str, kind: str) -> None:
    media_ops.reread(game_dir)
    if family == ASSET and kind in BACKGLASS_FILES:
        from common.games import asset_ops

        asset_ops.refresh_backglass(game_id, kind)


def _blocked(run: _Run, exc: Exception) -> service_errors.ServiceError:
    try:
        run.rollback()
    except Exception:  # noqa: BLE001 - the first failure is the one to report
        logger.exception("Could not put every file back after a failed move")
    logger.warning("A move failed and was put back", exc_info=exc)
    return service_errors.BlockedError(why(exc))


def _forget_copy(kept: Path) -> None:
    kept.unlink(missing_ok=True)
    backups_media.prune(kept)


def change_in_place(game_id: str, game_dir: Path, source: Path, target: Path,
                    replacing: Path | None = None, *, keep_copy: bool = True) -> None:
    """Rename or move a file or a folder, and carry the ledger entries with it. `replacing`
    is a file at `target` that goes to Backups first, and stays there unless `keep_copy` is
    off. A failure puts everything back. Keeps no undo: the caller says what the entry was
    called."""
    run = _Run(game_id, game_dir, ASSET)
    kept = None
    try:
        if replacing is not None:
            kept = run.delete(replacing, REASON_REPLACE)
        run.rename_tree(source, target)
        _ledger(game_dir, run)
    except Exception as exc:  # noqa: BLE001 - said, and put back
        raise _blocked(run, exc) from exc
    if kept is not None and not keep_copy:
        _forget_copy(kept)
    media_ops.reread(game_dir)


def rename_table_in_place(game_id: str, game_dir: Path, source: Path, target: Path,
                          followers: list[tuple[Path, Path]], entry: Callable[[], bool],
                          *, keep_copy: bool = True) -> None:
    """Rename a table's file, and each of `followers` (files named for it, as (from, to)), then
    call `entry` to record the new name. A file already at a follower's target goes to Backups
    first. Any failure, `entry` answering False included, puts everything back."""
    run = _Run(game_id, game_dir, ASSET)
    before: dict[str, dict | None] = {}
    kept: list[Path] = []
    try:
        run.rename(source, target)
        for file, to in followers:
            if os.path.lexists(to) and not _same(str(file), str(to)):
                kept.append(run.delete(to, REASON_REPLACE))
            run.rename(file, to)
        before = _ledger(game_dir, run)
        if not entry():
            raise service_errors.RefusedError(t("error.files.protected", path=source.name))
    except Exception as exc:  # noqa: BLE001 - said, and put back
        _restore_ledger(game_dir, before)
        raise _blocked(run, exc) from exc
    if not keep_copy:
        for copy_of in kept:
            _forget_copy(copy_of)
    media_ops.reread(game_dir)


def remove_in_place(game_id: str, game_dir: Path, path: Path, *, keep_copy: bool = True) -> str:
    """Take a file out after a copy goes to Backups, and drop its ledger entry. Without
    `keep_copy` the copy is dropped once the file is out. Answers the kept copy's path, empty
    where there is none."""
    run = _Run(game_id, game_dir, ASSET)
    try:
        kept = run.delete(path, REASON_DELETE)
        _ledger(game_dir, run)
    except Exception as exc:  # noqa: BLE001 - said, and put back
        raise _blocked(run, exc) from exc
    media_ops.reread(game_dir)
    if keep_copy:
        return str(kept)
    _forget_copy(kept)
    return ""


def remove_folder_in_place(game_id: str, game_dir: Path, folder: Path, *, keep: bool) -> None:
    """Remove a folder. With `keep` its contents move up to the folder it was in first; a
    name they would take there puts everything back. Without it everything in it goes, with
    no copy and no way back."""
    run = _Run(game_id, game_dir, ASSET)
    before: dict[str, dict | None] = {}
    try:
        if keep:
            for child in sorted(folder.iterdir()):
                run.rename_tree(child, folder.parent / child.name)
            run.remove_empty(folder)
            before = _ledger(game_dir, run)
        else:
            run.ledger_moves.extend(
                (run._relative(one), None, False) for one in folder.rglob("*") if one.is_file())
            before = _ledger(game_dir, run)
            shutil.rmtree(folder)
    except Exception as exc:  # noqa: BLE001 - said, and put back where it can be
        error = _blocked(run, exc)
        # Without `keep` the files that are already gone stay out of the ledger.
        _restore_ledger(game_dir, before if keep else {
            path: entry for path, entry in before.items() if (game_dir / path).exists()})
        raise error from exc
    media_ops.reread(game_dir)


def to_tables(game_id: str, family: str, kind: str, table_ids: list[str]) -> dict[str, Any]:
    """Give a game's file to these tables, as their own or by leaving it on the game.

    Tables with a file of their own have it replaced, kept in Backups.
    """
    kind = _slot_kind_or_refuse(family, kind)
    game, game_dir = _writable(game_id)
    tables = tables_of(game)
    folder = game_dir.name
    chosen = [_by_id(tables, one).stem for one in table_ids]
    source = pieces(game_dir, family, kind, folder)
    if not source:
        raise service_errors.RefusedError(
            t("error.games.slot_empty", slot=label_of(family, kind), name=folder))
    plan = plan_to_tables(family, kind, folder, [one.stem for one in tables], chosen,
                          lambda stem: bool(pieces(game_dir, family, kind, stem)))
    result = Moved(stays=plan.stays, lost=plan.lost)
    run = _Run(game_id, game_dir, family)
    try:
        for stem in plan.selected:
            for old in pieces(game_dir, family, kind, stem):
                kept = run.delete(old.path)
                result.replaced.append({"table": stem, "file": old.path.name,
                                        "kept": kept.name})
        if plan.stays:
            result.uses = list(dict.fromkeys([*plan.readers, *plan.selected]))
        else:
            *copied, last = plan.selected
            for stem in copied:
                for piece in source:
                    run.copy(piece.path, piece.at(stem))
                    result.moved.append({"table": stem, "file": piece.at(stem).name})
            for piece in source:
                run.rename(piece.path, piece.at(last))
                result.moved.append({"table": last, "file": piece.at(last).name})
            result.uses = list(plan.selected)
    except Exception as exc:  # noqa: BLE001 - said, and every step put back
        raise _blocked(run, exc) from exc
    return _finish(game_id, game_dir, family, kind, run, result)


def to_game(game_id: str, family: str, kind: str, table_id: str) -> dict[str, Any]:
    """Make a table's own file the game's. What the game had is kept in Backups, and the
    tables without a file of their own read the new one."""
    kind = _slot_kind_or_refuse(family, kind)
    label = label_of(family, kind)
    if family == ASSET and kind in TABLE_ONLY:
        raise service_errors.RefusedError(t("error.file_move.table_only", label=label),
                                          details={"kind": kind})
    game, game_dir = _writable(game_id)
    tables = tables_of(game)
    folder = game_dir.name
    table = _by_id(tables, table_id)
    if _same(table.stem, folder):
        raise service_errors.RefusedError(t("error.file_move.folder_named", name=table.stem))
    source = pieces(game_dir, family, kind, table.stem)
    if not source:
        raise service_errors.RefusedError(
            t("error.games.slot_empty", slot=label, name=table.stem))
    readers = [one.stem for one in tables
               if not _same(one.stem, folder) and not _same(one.stem, table.stem)
               and not pieces(game_dir, family, kind, one.stem)]
    result = Moved(stays=True)
    run = _Run(game_id, game_dir, family)
    try:
        for old in pieces(game_dir, family, kind, folder):
            kept = run.delete(old.path)
            result.replaced.append({"table": "", "file": old.path.name, "kept": kept.name})
        for piece in source:
            run.rename(piece.path, piece.at(folder))
            result.moved.append({"table": "", "file": piece.at(folder).name})
    except Exception as exc:  # noqa: BLE001 - said, and every step put back
        raise _blocked(run, exc) from exc
    result.uses = list(dict.fromkeys([*readers, table.stem]))
    named = [one.stem for one in tables if _same(one.stem, folder)]
    result.changed_for = [*named, *readers] if result.replaced else []
    return _finish(game_id, game_dir, family, kind, run, result)


def undo(game_id: str, token: str) -> dict[str, Any]:
    """Put back what a move of this game changed. A move can be undone once, and not after
    the server restarts."""
    with _lock:
        done = _done.pop(token, None)
        if done is not None and done.game_id != game_id:
            _done[token] = done
            done = None
    if done is None:
        raise service_errors.NotFoundError(t("error.file_move.expired"))
    game = game_lens.game_for_writing(done.game_id)
    if playing(game, done.game_id):
        with _lock:
            _done[token] = done
        raise service_errors.BlockedError(t("error.file_move.playing"))
    try:
        while done.ops:
            _reverse(done.ops[-1])
            done.ops.pop()
        _restore_ledger(done.game_dir, done.ledger)
    except (OSError, ValueError) as exc:
        with _lock:
            _done[token] = done
        raise service_errors.BlockedError(why(exc)) from exc
    _reread(done.game_id, done.game_dir, done.family, done.kind)
    return {"undone": True}


# --- what the picker needs --------------------------------------------------------


def _facts(files: list[media_placement.Piece]) -> dict[str, Any] | None:
    if not files:
        return None
    size = 0
    newest = 0.0
    for piece in files:
        try:
            stat = piece.path.stat()
        except OSError:
            continue
        size += stat.st_size
        newest = max(newest, stat.st_mtime)
    return {"files": [piece.path.name for piece in files], "size": size,
            "modified": newest}


def state(game_id: str, family: str, kind: str) -> dict[str, Any]:
    """The game's file for this kind, and what each of its tables has, for the surface
    that asks which tables are to have it."""
    kind = _slot_kind_or_refuse(family, kind)
    game = game_lens.game_or_refuse(game_id)
    game_dir = Path(game.full_path_game or "")
    folder = game_dir.name
    names = {entry.name.lower() for entry in _listing(game_dir)}
    rows = []
    for table in tables_of(game):
        named = _same(table.stem, folder)
        rows.append({
            "id": table.id, "stem": table.stem, "filename": table.filename,
            "folder_named": named,
            "own": None if named else _facts(pieces(game_dir, family, kind, table.stem)),
            "ini": f"{table.stem}.ini".lower() in names})
    return {
        "folder": folder,
        "game": _facts(pieces(game_dir, family, kind, folder)),
        "tables": rows,
        "fallback": has_fallback(family, kind),
        "to_game": not (family == ASSET and kind in TABLE_ONLY),
        "playing": playing(game, game_id),
    }


def _listing(game_dir: Path) -> list[os.DirEntry]:
    try:
        return list(os.scandir(game_dir))
    except OSError:
        return []


__all__ = ["ASSET", "MEDIA", "TABLE_ONLY", "Moved", "Plan", "label_of", "movable",
           "plan_to_tables", "state", "to_game", "to_tables", "undo"]
