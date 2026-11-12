"""Changing the files and folders of a game by their path in its folder.

`asset_ops` and `media_ops` act on files VPX or a theme finds by name. This acts on whichever
file or folder the game's folder holds, so what a name means to either of them is not its
business - but what a change would stop them finding is told to the caller first.

A change that has effects is refused with every effect listed, each naming the `resolve`
values that settle it. Nothing is touched until the caller has answered all of them.
"""

from __future__ import annotations

import os
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from common import apps, service_errors
from common.failures import why
from common.games import (
    asset_lens,
    asset_ops,
    asset_resolver,
    backglass_order,
    file_move,
    game_lens,
    game_repository,
    info_files,
    table_swap,
    tables,
)
from common.games.game import Game
from common.games.game_metadata import load_game_meta
from common.games.info_file import MetaConfig
from common.i18n import t

RESOLVE_BREAK = "break"
RESOLVE_REPLACE = "replace"
RESOLVE_KEEP = "keep"
RESOLVE_FIX = "fix"
RESOLUTIONS = (RESOLVE_BREAK, RESOLVE_REPLACE, RESOLVE_KEEP, RESOLVE_FIX)

_RESERVED = '<>:"/\\|?*'
_LONGEST = 255
_USED = (asset_resolver.RESOLUTION_DEDICATED, asset_resolver.RESOLUTION_SHARED)


@dataclass
class _Plan:
    game_id: str
    game_dir: Path
    source: Path
    target: Path | None
    folder: bool
    replacing: Path | None = None
    impacts: list[dict[str, Any]] = field(default_factory=list)
    game: Game | None = None
    table: file_move.Table | None = None
    # The files named for a table being renamed, with the impact that asks about each.
    followers: list[tuple[dict[str, Any], Path, Path]] = field(default_factory=list)

    def relative(self, path: Path) -> str:
        return path.relative_to(self.game_dir).as_posix()


def _record(game_dir: Path, file: Path) -> bool:
    """The game's own `.info`, which is not a file to change."""
    return file.parent == game_dir and info_files.is_game_info(file)


def _name_or_refuse(name: str) -> None:
    """A name that is one segment, that a Windows share would keep as given."""
    clean = "".join(c for c in unicodedata.normalize("NFC", name)
                    if c not in _RESERVED and unicodedata.category(c) not in ("Cc", "Cs"))
    if (not name or clean != name or name != name.strip() or name.endswith(".")
            or name in {".", ".."} or len(name.encode("utf-8")) > _LONGEST):
        raise service_errors.RefusedError(t("error.files.bad_name"), details={"name": name})


def _as_on_disk(root: Path, found: Path) -> Path:
    """`found` spelled as the folders hold it: on a filesystem that ignores case a path typed
    in another case resolves as typed, and the record keys files by how they are spelled."""
    here = root
    for part in found.relative_to(root).parts:
        try:
            names = [one.name for one in here.iterdir()]
        except OSError:
            return found
        here = here / (part if part in names else next(
            (one for one in names if one.lower() == part.lower()), part))
    return here


def _entry_or_refuse(game_dir: Path, path: str, *, folder: bool) -> Path:
    """The file, or with `folder` the folder, `path` names, as it sits under the folder as
    the game knows it."""
    root = game_dir.resolve()
    found = asset_lens.inside(root, path)
    if found != root:
        found = _as_on_disk(root, found)
    if found == root:
        raise service_errors.RefusedError(t("error.files.root"), details={"path": path})
    if not found.exists() or found.is_dir() != folder:
        raise service_errors.NotFoundError(
            t("error.files.no_folder" if folder else "error.assets.no_such_file", path=path),
            details={"path": path})
    if (root / PurePosixPath(path)).is_symlink():
        raise service_errors.RefusedError(t("error.files.link", path=path), details={"path": path})
    return game_dir / found.relative_to(root)


def _destination(game_dir: Path, source: Path, name: str | None, to: str | None) -> Path:
    """Where `source` ends up: `name` in the folder `to`, either of which may stay as it is."""
    if name is None and to is None:
        raise service_errors.RefusedError(t("error.files.no_change"))
    if name is not None:
        _name_or_refuse(name)
    parent = source.parent
    if to is not None:
        root = game_dir.resolve()
        found = root if not to.strip("/") else asset_lens.inside(root, to)
        if not found.is_dir() or (found != root and (root / PurePosixPath(to)).is_symlink()):
            raise service_errors.NotFoundError(t("error.files.no_folder", path=to),
                                               details={"path": to})
        parent = game_dir / _as_on_disk(root, found).relative_to(root)
    return parent / (name if name is not None else source.name)


def _clash(target: Path, changing: Path | None = None) -> Path | None:
    """What already has the target's name, matched without case as VPX matches it. The
    entry being changed is not a clash, so a change of case can go through."""
    wanted = target.name.lower()
    me = str(changing).lower() if changing is not None else None
    try:
        return next((one for one in target.parent.iterdir()
                     if one.name.lower() == wanted and str(one).lower() != me), None)
    except OSError:
        return None


def _inside(path: Path, folder: Path) -> bool:
    """Whether `path` is `folder` or under it, matched without case."""
    here, there = str(path).lower(), str(folder).lower()
    return here == there or here.startswith(there + os.sep)


def _under(path: str, folder: str) -> bool:
    return path == folder or path.startswith(f"{folder}/")


def _fixed_places(game_dir: Path, source: str) -> list[dict[str, Any]]:
    """The folders VPX or VPinFE reads by their place in the game that `source` is in, is,
    or holds."""
    places = {name.lower() for name in asset_lens.whole_folders(game_dir.resolve())}
    places |= {"medias", *asset_resolver.FIXED_PLACES}
    here = source.lower()
    return [{"code": "fixed_place", "folder": folder, "answers": [RESOLVE_BREAK]}
            for folder in sorted(places) if _under(here, folder) or _under(folder, here)]


def _companions(game: Any, plan: _Plan, arriving: list[str] | None = None) -> list[dict[str, Any]]:
    """What each table would stop using, or use another file for, among the files beside
    the table. A file in a folder is no companion, so only a change at the top matters.
    `arriving` is the files a folder's removal puts at the top."""
    root = plan.game_dir

    def at_top(path: Path | None) -> bool:
        return path is not None and path.parent == root

    if arriving is None and (plan.folder or not (at_top(plan.source) or at_top(plan.target))):
        return []
    files, _ = asset_resolver.folder_listing(root)
    if arriving is not None:
        after = [*files, *arriving]
    else:
        after = [name for name in files
                 if not (at_top(plan.source) and name == plan.source.name)]
        if plan.replacing is not None:
            after = [name for name in after if name.lower() != plan.replacing.name.lower()]
        if plan.target is not None and at_top(plan.target):
            after.append(plan.target.name)
    found: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, bool]] = set()
    for table in file_move.tables_of(game):
        before = asset_resolver.resolve_for_table(table.filename, root.name, files)
        later = asset_resolver.resolve_for_table(table.filename, root.name, after)
        for kind, was in before.items():
            if was["resolution"] not in _USED:
                continue
            now = later[kind]
            becomes = now["file"] if now["resolution"] in _USED else ""
            shared = was["resolution"] == asset_resolver.RESOLUTION_SHARED
            key = (kind, was["file"], becomes, shared)
            if becomes.lower() == was["file"].lower() or key in seen:
                continue
            seen.add(key)
            found.append({"code": "companion", "kind": kind,
                          "table": "" if shared else table.stem, "was": was["file"],
                          "becomes": becomes, "answers": [RESOLVE_BREAK]})
    return found


def _table_of(game: Game, game_dir: Path, source: Path) -> file_move.Table | None:
    """The table a file at the top of the game's folder is, where the game's record holds it
    as its own. A file a program plays that no table is, is not one to rename."""
    if source.parent != game_dir:
        return None
    entries = tables.table_entries(load_game_meta(game))
    return next((one for one in file_move.tables_of(game)
                 if one.filename.lower() == source.name.lower()
                 and tables.entry_form(entries.get(one.id)) == tables.FORM_CONTAINED), None)


def _table_target_or_refuse(plan: _Plan) -> None:
    """A table stays in the game's folder and in the kind of file it is."""
    assert plan.target is not None
    if plan.target.parent != plan.game_dir:
        raise service_errors.RefusedError(t("error.files.table_stays"))
    if apps.app_for(plan.target.name) is not apps.app_for(plan.source.name):
        raise service_errors.RefusedError(t("error.files.table_kind", name=plan.target.name),
                                          details={"name": plan.target.name})


def _followers(plan: _Plan) -> None:
    """The files named for the table, which follow it to its new name or stay behind and
    stop being its own. A name the game's own folder has is every table's, so nothing is
    carried to it."""
    assert plan.target is not None
    old, new = plan.source.stem, plan.target.stem
    root = plan.game_dir
    found = [(kind, path, path.with_name(new + path.suffix))
             for kind, path in table_swap.named_files(root, plan.source.name).items()]
    if old.lower() != root.name.lower():
        for kind in file_move.MEDIA_KINDS:
            found += [(kind, one.path, one.at(new))
                      for one in file_move.pieces(root, file_move.MEDIA, kind, old)]
    shared = new.lower() == root.name.lower()
    for kind, path, to in found:
        if str(to).lower() == str(path).lower():
            continue
        taken = _clash(to, path) is not None
        carry = [RESOLVE_BREAK] if shared else [RESOLVE_REPLACE if taken else RESOLVE_FIX,
                                                RESOLVE_BREAK]
        impact = {"code": "named_file", "kind": kind, "path": plan.relative(path),
                  "becomes": plan.relative(to), "answers": carry}
        plan.impacts.append(impact)
        plan.followers.append((impact, path, to))


def _check_what_moves_up(plan: _Plan) -> None:
    """Moving a folder's files up into the folder it was in: they take its names there. What
    would be taken, a table or the game's record they would become, and what at the top of
    the game's folder would change for a table, are said before any of them moves."""
    up = plan.source.parent
    for child in sorted(plan.source.iterdir()):
        if (clash := _clash(up / child.name, child)) is not None:
            raise service_errors.BlockedError(
                t("error.files.name_taken", name=child.name),
                details={"name": child.name, "path": plan.relative(clash)})
        if up == plan.game_dir and child.is_file() and (
                apps.app_for(child.name) is not None or _record(plan.game_dir, child)):
            raise service_errors.RefusedError(t("error.files.becomes_table", name=child.name),
                                              details={"name": child.name})
    if up == plan.game_dir:
        places = {one["folder"]: one for child in sorted(plan.source.iterdir())
                  if child.is_dir() for one in _fixed_places(plan.game_dir, child.name)}
        plan.impacts += list(places.values())
        plan.impacts += _companions(plan.game, plan, arriving=[
            one.name for one in sorted(plan.source.iterdir()) if one.is_file()])


def _files_under(folder: Path) -> int:
    return sum(1 for one in folder.rglob("*") if one.is_file())


def _plan(game_id: str, path: str, *, folder: bool, name: str | None = None,
          to: str | None = None, remove: bool = False, keeping: bool = False) -> _Plan:
    game = game_lens.game_for_writing(game_id)
    game_dir = Path(game.full_path_game or "")
    source = _entry_or_refuse(game_dir, path, folder=folder)
    table = None if folder or remove else _table_of(game, game_dir, source)
    if not folder and table is None and (apps.app_for(source.name) is not None
                                         or _record(game_dir, source)):
        raise service_errors.RefusedError(t("error.files.protected", path=path),
                                          details={"path": path})
    plan = _Plan(game_id, game_dir, source, None, folder, game=game, table=table)
    if remove:
        if folder and (holds := _files_under(source)):
            plan.impacts.append({"code": "holds", "path": plan.relative(source), "files": holds,
                                 "answers": [RESOLVE_KEEP, RESOLVE_BREAK]})
            if keeping:
                _check_what_moves_up(plan)
    else:
        plan.target = _destination(game_dir, source, name, to)
        if str(plan.target) == str(source):
            raise service_errors.RefusedError(t("error.file_move.nothing"))
        if folder and _inside(plan.target.parent, source):
            raise service_errors.RefusedError(t("error.files.into_itself"))
        if table is not None:
            _table_target_or_refuse(plan)
        elif not folder and (apps.app_for(plan.target.name) is not None
                             or _record(game_dir, plan.target)):
            raise service_errors.RefusedError(
                t("error.files.becomes_table", name=plan.target.name),
                details={"name": plan.target.name})
        if (taken := _clash(plan.target, source)) is not None:
            if folder or table is not None or taken.is_dir() or taken.is_symlink():
                raise service_errors.BlockedError(
                    t("error.files.name_taken", name=plan.target.name),
                    details={"name": plan.target.name})
            plan.replacing = taken
            plan.impacts.append({"code": "name_taken", "path": plan.relative(taken),
                                 "answers": [RESOLVE_REPLACE]})
    plan.impacts += _fixed_places(game_dir, plan.relative(source))
    if table is not None:
        _followers(plan)
    else:
        plan.impacts += _companions(game, plan)
    return plan


def _answered_or_block(plan: _Plan, resolve: list[str]) -> None:
    for value in resolve:
        if value not in RESOLUTIONS:
            raise service_errors.RefusedError(t("error.files.bad_resolve", value=value),
                                              details={"value": value, "known": list(RESOLUTIONS)})
    if any(not set(resolve) & set(one["answers"]) for one in plan.impacts):
        raise service_errors.BlockedError(t("error.files.impacts"),
                                          details={"impacts": plan.impacts})


def _playing_or_block(game_id: str) -> None:
    game = game_lens.game_for_writing(game_id)
    if file_move.playing(game, game_id):
        raise service_errors.BlockedError(t("error.files.playing"))


def impacts(game_id: str, path: str, *, folder: bool, name: str | None = None,
            to: str | None = None, remove: bool = False) -> dict[str, Any]:
    """What the change would do, for the caller to answer before making it."""
    plan = _plan(game_id, path, folder=folder, name=name, to=to, remove=remove)
    return {"path": path, "becomes": plan.relative(plan.target) if plan.target else "",
            "impacts": plan.impacts}


def change(game_id: str, path: str, *, folder: bool, name: str | None = None,
           to: str | None = None, resolve: list[str], keep_copy: bool = True) -> dict[str, Any]:
    """Rename a file or folder, move it into another folder, or both. Where it came from, and
    which VPS release it is, stay with a file. Refused for a table, for the game's `.info`
    and for a name that would make a file a table. A file it replaces is kept in Backups
    unless `keep_copy` is off."""
    _playing_or_block(game_id)
    plan = _plan(game_id, path, folder=folder, name=name, to=to)
    _answered_or_block(plan, resolve)
    assert plan.target is not None
    if plan.table is not None:
        _rename_table(plan, resolve, keep_copy)
        return {"path": plan.relative(plan.target), "was": plan.relative(plan.source),
                "name": plan.target.name}
    file_move.change_in_place(game_id, plan.game_dir, plan.source, plan.target, plan.replacing,
                              keep_copy=keep_copy)
    if not folder:
        asset_ops.refresh_if_backglass(game_id, plan.source, plan.target)
    return {"path": plan.relative(plan.target), "was": plan.relative(plan.source),
            "name": plan.target.name}


def _rename_table(plan: _Plan, resolve: list[str], keep_copy: bool) -> None:
    """The table's file, and the files named for it that `resolve` carries, go to the new
    name, and the game's record takes the new name for the table. The table keeps its id,
    plays, rating, tags and release."""
    assert plan.target is not None and plan.table is not None and plan.game is not None
    table_id, new_name = plan.table.id, plan.target.name
    answered = set(resolve)
    carried = [(path, to) for impact, path, to in plan.followers
               if impact["answers"][0] != RESOLVE_BREAK and impact["answers"][0] in answered]
    record = str(info_files.info_path_of(plan.game))
    try:
        file_move.rename_table_in_place(
            plan.game_id, plan.game_dir, plan.source, plan.target, carried,
            lambda: MetaConfig(record).rename_table(table_id, new_name), keep_copy=keep_copy)
    finally:
        game_repository.reread_game(plan.game)
        backglass_order.settle(plan.game_id)


def remove(game_id: str, path: str, *, folder: bool, resolve: list[str],
           keep_copy: bool = True) -> dict[str, Any]:
    """Take a file out after a copy goes to Backups, unless `keep_copy` is off. A folder goes
    with what is in it and no copy, or with `keep` the files in it move up to the folder it
    was in."""
    _playing_or_block(game_id)
    keeping = RESOLVE_KEEP in resolve
    plan = _plan(game_id, path, folder=folder, remove=True, keeping=keeping)
    _answered_or_block(plan, resolve)
    if folder:
        moving = list(plan.source.iterdir()) if keeping else []
        file_move.remove_folder_in_place(game_id, plan.game_dir, plan.source, keep=keeping)
        asset_ops.refresh_if_backglass(game_id, *moving)
    else:
        file_move.remove_in_place(game_id, plan.game_dir, plan.source, keep_copy=keep_copy)
        asset_ops.refresh_if_backglass(game_id, plan.source)
    return {"removed": [path]}


def make_folder(game_id: str, path: str) -> dict[str, Any]:
    """A new folder at `path`, and the folders above it that are not there yet."""
    game = game_lens.game_for_writing(game_id)
    game_dir = Path(game.full_path_game or "")
    root = game_dir.resolve()
    for segment in PurePosixPath(path).parts:
        _name_or_refuse(segment)
    found = asset_lens.inside(root, path)
    target = game_dir / found.relative_to(root)
    if found == root or os.path.lexists(found) or _clash(target) is not None:
        raise service_errors.BlockedError(t("error.files.name_taken", name=target.name),
                                          details={"name": target.name})
    try:
        found.mkdir(parents=True)
    except OSError as exc:
        raise service_errors.BlockedError(why(exc)) from exc
    return {"path": target.relative_to(game_dir).as_posix()}


__all__ = ["RESOLVE_BREAK", "RESOLUTIONS", "change", "impacts", "make_folder", "remove"]
