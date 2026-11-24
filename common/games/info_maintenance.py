"""Upgrade a library of `.info` files, or put back what a newer build upgraded.

Neither walk stops on a bad file, and neither re-reads a `.vpx`.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import TypedDict

from common import service_errors
from common.config import path_state
from common.failures import why
from common.games import info_files, library_folders
from common.games.collection_store import restorable_collections_backup
from common.games.info_file import (
    GAME_ID_KEY,
    VPINFE_SECTION,
    InvalidMetaConfigError,
    MetaConfig,
)
from common.games.info_migration import (
    INFO_SCHEMA,
    copied_from,
    replace_atomic,
    restorable_backup,
    write_json_atomic,
)
from common.i18n import t
from common.kept_copies import RESTORE, copy_aside
from common.runtime.jobs import JobReporter, LogCallback, ProgressCallback

logger = logging.getLogger("vpinfe.common.games.info_maintenance")


class UpgradeResult(TypedDict):
    upgraded: int
    already_current: int
    failed: int
    # The folder and what went wrong with it, so a caller can name both.
    failures: list[tuple[str, str]]
    # What became of the 2.x VPS matches: bound to the table they name, or dropped
    # because the catalog holds no such id.
    matches_bound: int
    matches_dropped: int


class RestoreResult(TypedDict):
    restored: int
    nothing_to_restore: int
    failed: int
    failures: list[tuple[str, str]]
    collections_restored: bool


def game_dirs(game_root: str | Path | None = None,
              game_name: str | None = None) -> list[Path]:
    """Game folders under the root, or in every library folder when given none. Not
    load_games: that raises on the first bad `.info`. A folder that does not answer is left
    out rather than waited for: listing one on a dead mount never returns."""
    if game_root is None:
        found: list[Path] = []
        for location in library_folders.answering():
            if location.kind != library_folders.KIND_GAME:
                found += _children(Path(location.path), game_name)
                continue
            folder = Path(location.path)
            if folder.is_dir() and game_name in (None, "", folder.name):
                found.append(folder)
        return found
    root = Path(game_root)
    if not path_state.look(str(root)).reachable:
        return []
    return _children(root, game_name)


def _children(root: Path, game_name: str | None) -> list[Path]:
    """The game folders in a root that has answered."""
    if not root.is_dir():
        return []
    if game_name:
        one = root / game_name
        return [one] if one.is_dir() else []
    return sorted(
        (d for d in root.iterdir() if d.is_dir() and not d.name.startswith(".")),
        key=lambda d: d.name.lower(),
    )


def _info_path(game_dir: Path) -> Path:
    return info_files.game_info_path(game_dir)


def pending_upgrade(game_dir: str | Path) -> bool:
    """Whether this folder's `.info` still holds the 2.x shape. False if it cannot be read."""
    folder = Path(game_dir)
    if not _info_path(folder).exists():
        return False
    try:
        return MetaConfig(str(_info_path(folder))).pending_migration
    except (InvalidMetaConfigError, OSError):
        return False


def upgrade_library(
    game_root: str | Path | None = None,
    game_name: str | None = None,
    progress_cb: ProgressCallback | None = None,
    log_cb: LogCallback | None = None,
    route_match: Callable[[dict], str] | None = None,
) -> UpgradeResult:
    """Upgrade every table's `.info` in one pass.

    Startup already does the library, so this is the repair for what it did not reach.

    `route_match` is given each migrated record, and says
    what it did with the 2.x VPS match. Injected because deciding that needs the
    catalog, which is a layer above this one.
    """
    reporter = JobReporter(logger, progress_cb=progress_cb, log_cb=log_cb)
    log = reporter.log

    folders = game_dirs(game_root, game_name)
    total = len(folders)
    result: UpgradeResult = {"upgraded": 0, "already_current": 0, "failed": 0,
                             "failures": [], "matches_bound": 0, "matches_dropped": 0}

    log("Upgrading .info files. Each one is backed up first, so this can be undone.")
    reporter.progress(0, total, t("said.starting"))

    for index, game_dir in enumerate(folders, start=1):
        reporter.progress(index, total, game_dir.name)
        info_path = _info_path(game_dir)
        if not info_path.exists():
            result["already_current"] += 1
            continue
        try:
            meta = MetaConfig(str(info_path))
            if not meta.pending_migration:
                result["already_current"] += 1
                continue
            # Asked before the match is routed, so a folder that cannot take the file is
            # not counted as bound and then failed.
            library_folders.writable_game_folder(game_dir)
            if route_match is not None:
                routed = route_match(meta.data)
                if routed == "bound":
                    result["matches_bound"] += 1
                    log(f"Bound the VPS match to the table it names: {game_dir.name}")
                elif routed == "dropped":
                    result["matches_dropped"] += 1
                    log(f"Dropped a VPS match the catalog does not hold: {game_dir.name}")
            meta.write_config()
        except (InvalidMetaConfigError, OSError, service_errors.BlockedError) as exc:
            result["failed"] += 1
            result["failures"].append((game_dir.name, why(exc)))
            log(f"Could not upgrade {game_dir.name}: {why(exc)}")
            continue
        result["upgraded"] += 1
        log(f"Upgraded: {game_dir.name}")

    log(_upgrade_summary(result))
    return result


def restore_library(
    game_root: str | Path | None = None,
    game_name: str | None = None,
    max_schema: int = INFO_SCHEMA,
    config_dir: str | Path | None = None,
    progress_cb: ProgressCallback | None = None,
    log_cb: LogCallback | None = None,
    keep_ids: bool = True,
) -> RestoreResult:
    """Put back the newest readable backup in every folder that has one.

    All or nothing: nobody chose which games upgraded, so nobody can choose which return.
    A game keeps its id unless `keep_ids` is off, which only a reset to the 2.x bytes wants.
    """
    reporter = JobReporter(logger, progress_cb=progress_cb, log_cb=log_cb)
    log = reporter.log

    folders = game_dirs(game_root, game_name)
    total = len(folders)
    result: RestoreResult = {"restored": 0, "nothing_to_restore": 0, "failed": 0,
                             "failures": [], "collections_restored": False}

    log("Restoring backups. Your current .info files are backed up first, so this can be "
        "undone too.")
    reporter.progress(0, total, t("said.starting"))

    for index, game_dir in enumerate(folders, start=1):
        reporter.progress(index, total, game_dir.name)
        chosen = restorable_backup(game_dir, max_schema)
        if not chosen:
            result["nothing_to_restore"] += 1
            continue
        info_path = _info_path(game_dir)
        try:
            restore_file(info_path, chosen, keep_id=keep_ids)
        except OSError as exc:
            result["failed"] += 1
            result["failures"].append((game_dir.name, why(exc)))
            log(f"Left as it is, could not be restored: {game_dir.name}")
            continue
        result["restored"] += 1
        log(f"Restored: {game_dir.name}")

    # Collections live in the config directory rather than a game folder, and the id
    # migration rewrites them into something an older build cannot resolve.
    if config_dir:
        chosen = restorable_collections_backup(config_dir, max_schema)
        if chosen:
            target = copied_from(chosen)
            try:
                restore_file(target, chosen)
                result["collections_restored"] = True
                log("Restored your collections.")
            except OSError as exc:
                result["failed"] += 1
                result["failures"].append((target.name, why(exc)))
                log("Left as it is, could not be restored: your collections")

    log(_restore_summary(result))
    return result


def restore_file(path: str | Path, backup: str | Path, keep_id: bool = False) -> str | None:
    """Put `backup` in place at `path`, keeping what is there now. Returns the copy it
    kept, or None where there was nothing at `path`.

    With `keep_id`, a game's `.info` keeps the id it has: the copy was taken before the id
    existed or under another, and the id is what holds the game in its collections and its
    play history under each player.
    """
    kept_id = game_id_in(path) if keep_id and Path(path).suffix == ".info" else ""
    data = None
    if kept_id and game_id_in(backup) != kept_id:
        data = json.loads(Path(backup).read_text(encoding="utf-8"))
    kept = (copy_aside(str(path), RESTORE, restoring=backup)
            if Path(path).exists() else None)
    if isinstance(data, dict):
        section = data.get(VPINFE_SECTION)
        data[VPINFE_SECTION] = {**(section if isinstance(section, dict) else {}),
                                GAME_ID_KEY: kept_id}
        write_json_atomic(path, data)
        return kept
    replace_atomic(backup, path)
    return kept


def game_id_in(path: str | Path) -> str:
    """The game id a `.info` holds, or nothing when it has none or cannot be read."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    section = data.get(VPINFE_SECTION) if isinstance(data, dict) else None
    return str(section.get(GAME_ID_KEY) or "") if isinstance(section, dict) else ""


def _games(count: int) -> str:
    return f"{count} table" if count == 1 else f"{count} tables"


def _files(count: int) -> str:
    return "1 .info file" if count == 1 else f"{count} .info files"


def _upgrade_summary(result: UpgradeResult) -> str:
    if not result["upgraded"] and not result["failed"]:
        return "Nothing to upgrade - every .info file is already on the current format."
    said = []
    if result["upgraded"]:
        said.append(f"Upgraded {_files(result['upgraded'])}. Ratings, favorites, tags and "
                    "play counts are unchanged, and a backup of each was saved.")
    if result["failed"]:
        said.append(f"Could not upgrade {_files(result['failed'])}, and left each as it was.")
    return " ".join(said)


def _restore_summary(result: RestoreResult) -> str:
    if not result["restored"] and not result["collections_restored"]:
        return "Nothing to restore - there are no backups."
    if not result["restored"]:
        return "Restored your collections."
    summary = (
        f"Restored {_files(result['restored'])}. Their ratings, favorites, tags and play "
        "counts are back as this version recorded them."
    )
    if result["collections_restored"]:
        summary += " Your collections are back too."
    if result["nothing_to_restore"]:
        summary += (
            f" {_games(result['nothing_to_restore'])} were never upgraded, so there was "
            "nothing to put back."
        )
    if result["failed"]:
        summary += (
            f" {_games(result['failed'])} could not be restored and were left as they are."
        )
    return summary
