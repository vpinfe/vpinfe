"""Take 3.0's own state off an install so the next start migrates from scratch.

A test instrument rather than a recovery aid: a one-time migration whose marker is
already set does nothing and says nothing, so a 2->3 test that has run once stops
testing.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path

from common.atomic_write import STAGED_PREFIX, STAGED_SUFFIX
from common.devices import serving
from common.devices.device_registry import DeviceRegistry
from common.failures import why
from common.games import info_files, table_files
from common.games.collection_store import COLLECTIONS_NAME, COLLECTIONS_NAME_INI
from common.games.info_file import ASSETS_KEY, VPINFE_SECTION
from common.games.info_maintenance import game_dirs, restore_library
from common.games.info_migration import (
    needs_migration,
    restorable_backup,
    written_by_beta,
)
from common.games.tables import TABLES_KEY
from common.kept_copies import ADOPT, BACKUP_MARKER, RESET, backup_names, backup_reason, copy_aside
from common.runtime.jobs import JobReporter, LogCallback, ProgressCallback
from common.security import http_client, pinned_certificates

logger = logging.getLogger("vpinfe.common.games.revert_3x")

# 3.0 wrote these and 2.x reads none of them. Named one at a time on purpose: a sweep
# for anything backup-shaped also eats the hand-made .bak-repro files kept beside them.
CONFIG_FILES = ("vpinfe.json", COLLECTIONS_NAME, "players.json",
                "manager-ui-state.json")
# The same, in the state folder. `dismissed.json`, `arrivals.json`, `marks.json` and
# `draw_order.json` are 3.0 data with no 2.x original: a reset library that kept them would
# measure against an install that no longer exists.
STATE_FILES = ("devices.json", "dismissed.json", "arrivals.json", "marks.json",
               "draw_order.json")

# Whose `.vpinfe-*` copies are ours to remove. The ini pair is what the conversion to
# JSON set aside; the JSON pair is what a restore keeps before it replaces one.
BACKED_UP_NAMES = ("vpinfe.ini", "vpinfe.json", COLLECTIONS_NAME_INI, COLLECTIONS_NAME)

# Copied aside before removal, and the sweep above skips copies made for a reset so these
# outlive the next one. Everything else here is regenerable or has a 2.x original to fall
# back on; these have neither. `vpinfe.json` only when it lists a mobile device, which
# is entered by hand and which no 2.x ini can rebuild. The roster is the same: every player
# but the owner is entered by hand, and anything held for a player is filed under their id.
# So are the marks: Wanted and Ignored are what only a person can say.
KEPT_BEFORE_REMOVAL = ("vpinfe.json", "players.json", "marks.json")

# Which of the two baselines the reset produced, reported rather than left to infer.
RESTORED_FROM_2X = "restored_from_2x"
FRESH_INSTALL = "fresh_install"

# Only the unversioned backups are 2.x originals. The default would happily pick a
# schema 2 copy that an earlier --restore-info left in the folder.
MAX_2X_SCHEMA = 0

# The `.info` sections introduced in 3.0. 2.x wrote `VPinFE`, `VPXFile` and `Medias`, all
# three of which the migration renames or supersedes, so any of these in a file 2.x has
# not since written over is proof 3.0 made it.
OUR_SECTIONS = (VPINFE_SECTION, TABLES_KEY, ASSETS_KEY)


class InstanceRunningError(RuntimeError):
    """VPinFE is up, so the reset would be undone before anyone saw it."""


def running_instance(http_port: int, timeout: float = 10.0) -> bool:
    """Whether a VPinFE is serving on this install's HTTP port.

    Reads the discovery document rather than only opening the socket, so an unrelated
    program on that port cannot block a reset. A listening socket dies with the process
    that holds it, so there is no stale port to be fooled by.

    The timeout stays long: a busy machine answers late, and a late answer judged as
    none lets the reset through under a live instance.
    """
    for scheme in ("http", "https"):
        try:
            document = http_client.get_json(f"{scheme}://127.0.0.1:{int(http_port)}/api/v1/",
                                            timeout=timeout, trust=pinned_certificates.UNVERIFIED)
        except (OSError, ValueError):
            continue
        if isinstance(document, dict) and document.get("name") == "VPinFE":
            return True
    return False


def reset(game_root: str | Path | None, config_dir: str | Path, *, http_port: int,
          state_dir: str | Path,
          config_only: bool = False, dry_run: bool = False,
          progress_cb: ProgressCallback | None = None,
          log_cb: LogCallback | None = None) -> dict:
    """Remove 3.0's state. Raises InstanceRunningError when VPinFE is running.

    A live instance holds the settings and the collections in memory and writes them
    back complete with their markers, so the refusal is not caution. A dry run is
    allowed either way: it writes nothing.
    """
    config_home = Path(config_dir)
    state_home = Path(state_dir)
    reporter = JobReporter(logger, progress_cb=progress_cb, log_cb=log_cb)

    # The setting is where the next start would listen. A run that was started on another
    # port says where it is, so a port changed since it started does not hide it.
    registry = DeviceRegistry(state_home / "devices.json")
    port = next((candidate for candidate in (http_port, serving.published(registry))
                 if candidate is not None and running_instance(candidate)), None)
    live = port is not None
    if live and not dry_run:
        raise InstanceRunningError(
            f"VPinFE is answering on port {port}. Stop it and run this again - a "
            "reset under a running instance is written straight back over.")

    result = {
        "dry_run": dry_run, "config_only": config_only, "instance_running": live,
        "config_removed": [], "config_kept": [], "restored": 0, "deleted_info": [],
        "removed_backups": 0,
        "failed": 0, "failures": [],
        # Never deleted, so this answer is the same before and after.
        "end_state": (RESTORED_FROM_2X if (config_home / "vpinfe.ini").exists()
                      else FRESH_INSTALL),
    }
    if live:
        reporter.log(f"VPinFE is answering on port {port}, so a real run would refuse.")

    if not config_only:
        _reset_library(game_root, result, reporter, dry_run)
    _reset_config(config_home, state_home, result, dry_run)

    for line in _summary(result):
        reporter.log(line)
    return result


def _reset_library(game_root: str | Path | None, result: dict, reporter: JobReporter,
                   dry_run: bool) -> None:
    restorable, ours = _library_plan(game_root)
    result["deleted_info"] = sorted(game_dir.name for game_dir in ours)

    if dry_run:
        result["restored"] = len(restorable)
        return

    outcome = restore_library(game_root, max_schema=MAX_2X_SCHEMA,
                              progress_cb=reporter.progress_cb, keep_ids=False)
    result["restored"] = outcome["restored"]
    result["failed"] += outcome["failed"]
    result["failures"] += outcome["failures"]

    for game_dir in ours:
        _remove(info_files.game_info_path(game_dir), result)
    # After the restore, not before: the unversioned copy is what it reads, and
    # replace_atomic copies rather than moves, so its source is still here afterwards.
    for game_dir in game_dirs(game_root):
        _remove_table_files(game_dir, result)
        result["removed_backups"] += _sweep(game_dir, info_files.info_name(game_dir.name), result)


def _remove_table_files(folder: Path, result: dict) -> None:
    """Take the tables' own files out of a folder, with every copy taken beside them. A file
    that was another program's before VPinFE wrote to it goes back to what it was."""
    try:
        names = os.listdir(folder)
    except OSError:
        return
    game_file = info_files.info_name(folder.name).lower()
    own = [name for name in names
           if name.lower().endswith(".info") and name.lower() != game_file]
    for name in own:
        path = folder / name
        try:
            ours = table_files.is_ours(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
        if not ours:
            continue
        original = next((one for one in backup_names(names, name)
                         if backup_reason(one) == ADOPT), None)
        try:
            if original:
                shutil.copy2(folder / original, path)
            else:
                path.unlink()
        except OSError as exc:
            result["failed"] += 1
            result["failures"].append((name, why(exc)))
    for name in names:
        base = name.split(BACKUP_MARKER, 1)[0]
        if BACKUP_MARKER in name and base in own:
            result["removed_backups"] += _remove(folder / name, result)


def _reset_config(config_dir: Path, state_dir: Path, result: dict, dry_run: bool) -> None:
    targets = _config_targets(config_dir, state_dir)
    if dry_run:
        result["config_removed"] = sorted(path.name for path in targets)
        result["config_kept"] = sorted(path.name for path in _kept_sources(config_dir, state_dir)
                                       if path.exists())
        return
    result["config_kept"] = _keep_aside(config_dir, state_dir, result)
    result["config_removed"] = sorted(path.name for path in targets
                                      if _remove(path, result))


def _kept_sources(config_dir: Path, state_dir: Path) -> list[Path]:
    sources = [(state_dir if name in STATE_FILES else config_dir) / name
               for name in KEPT_BEFORE_REMOVAL]
    return [path for path in sources if path.name != "vpinfe.json" or _lists_a_device(path)]


def _lists_a_device(settings: Path) -> bool:
    try:
        return bool(json.loads(settings.read_text(encoding="utf-8")).get("mobile_devices"))
    except (OSError, ValueError, AttributeError):
        return False


def _keep_aside(config_dir: Path, state_dir: Path, result: dict) -> list[str]:
    """Copy what cannot be rebuilt, before the removal that follows. Returns the copies.

    A failure here is reported and does not stop the reset: the copy is a courtesy, and
    refusing to reset because one could not be made would strand the user in the state
    they are trying to leave.
    """
    kept = []
    for source in _kept_sources(config_dir, state_dir):
        if not source.exists():
            continue
        try:
            kept.append(Path(copy_aside(source, RESET)).name)
        except OSError as exc:
            result["failed"] += 1
            result["failures"].append((source.name, str(exc)))
    return sorted(kept)


def _library_plan(game_root: str | Path | None) -> tuple[list[Path], list[Path]]:
    """Which folders get their 2.x `.info` back, and which lose the one 3.0 made.

    A folder with no unversioned backup and a 2.x-shaped `.info` is left alone: nothing
    of 3.0's has been written into it, so there is nothing here to take out.
    """
    restorable, ours = [], []
    for game_dir in game_dirs(game_root):
        info_path = info_files.game_info_path(game_dir)
        if not info_path.exists():
            continue
        if restorable_backup(game_dir, MAX_2X_SCHEMA):
            restorable.append(game_dir)
        elif _written_by_3x(info_path):
            ours.append(game_dir)
    return restorable, ours


def _written_by_3x(info_path: Path) -> bool:
    """Whether 3.0 wrote this `.info`, by a section only 3.0 writes.

    Not the schema stamp: the id backfill writes `{"vpinfe": {"game_id": ...}}` into a
    folder that had no `.info` at all, and that file carries no stamp. Unreadable
    answers no - this removes what it can identify, and a file too broken to parse is
    the one thing here nobody can re-enter.
    """
    try:
        with info_path.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        logger.warning("Could not read %s; leaving it alone", info_path)
        return False
    if not isinstance(data, dict):
        return False
    if needs_migration(data) and not written_by_beta(data):
        return False        # 2.x has written this since, so its own shape is in there
    return any(name in data for name in OUR_SECTIONS)


def _config_targets(config_dir: Path, state_dir: Path) -> list[Path]:
    """Everything under the config and state directories this removes, by name."""
    targets = [state_dir / name for name in STATE_FILES if (state_dir / name).exists()]
    try:
        names = os.listdir(config_dir)
    except OSError:
        return targets
    targets += [config_dir / name for name in CONFIG_FILES
                if (config_dir / name).exists()]
    for owned in BACKED_UP_NAMES:
        targets += [config_dir / name for name in backup_names(names, owned)
                    if backup_reason(name) != RESET]
    return targets + [config_dir / name for name in names if _is_write_temp(name)]


def _is_write_temp(name: str) -> bool:
    return name.startswith(STAGED_PREFIX) and name.endswith(STAGED_SUFFIX)


def _sweep(folder: Path, owned: str, result: dict) -> int:
    """Every copy of `owned` this build made, plus any half-written temp file."""
    try:
        names = os.listdir(folder)
    except OSError:
        return 0
    doomed = backup_names(names, owned) + [name for name in names if _is_write_temp(name)]
    return sum(_remove(folder / name, result) for name in doomed)


def _remove(path: Path, result: dict) -> int:
    """Delete a file or a whole directory. Returns 1 when it went, 0 when it could not."""
    try:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    except OSError as exc:
        result["failed"] += 1
        result["failures"].append((path.name, str(exc)))
        return 0
    return 1


def _summary(result: dict) -> list[str]:
    """What went, or would go. The same shape either way, so a dry run reads as the plan
    for the run that follows it."""
    dry = result["dry_run"]
    lines = [f"{'Would remove' if dry else 'Removed'} "
             f"{len(result['config_removed'])} item(s) from the config directory: "
             f"{', '.join(result['config_removed']) or 'nothing'}"]
    if result["config_kept"]:
        # Named, because a copy nobody is told about is one nobody restores from.
        lines.append(
            f"{'Would keep' if dry else 'Kept'} a copy of what cannot be rebuilt: "
            f"{', '.join(result['config_kept'])}")
    if not result["config_only"]:
        lines.append(
            f"{'Would restore' if dry else 'Restored'} {result['restored']} .info "
            f"file(s) from their 2.x backups, leaving no VPinFE backup copy anywhere "
            "in the library")
        if result["deleted_info"]:
            # The one thing here nobody can re-enter: a rating, a favorite and a play
            # count accumulated under 3.0. Named rather than counted, before and after.
            lines.append(
                f"{'Would delete' if dry else 'Deleted'} the .info 3.0 made for "
                f"{len(result['deleted_info'])} game(s) with no 2.x backup: "
                f"{', '.join(result['deleted_info'])}")
    lines.append(
        "This install keeps its vpinfe.ini, so the next start reads its 2.x settings."
        if result["end_state"] == RESTORED_FROM_2X else
        "This install has no vpinfe.ini, so the next start is a first run.")
    if result["failed"]:
        lines.append(f"{result['failed']} item(s) could not be removed and were left "
                     "as they are.")
    return lines
