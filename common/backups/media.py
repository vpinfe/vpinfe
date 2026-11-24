"""A game's file, kept before something replaces it.

A copy lives at `<backups folder>/<area>/<game id>/<where the file sat in the game folder>/
<name>.vpinfe-<stamp>-<reason>`. The area is `media` for a picture or video, `assets` for a
script, settings file or note, and `tables` for a table and the files named for it. The game
id is the first part, so a renamed game folder does not strand its copies, and the rest is
enough to put the file back with no record beside it.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from common import atomic_write, kept_copies, service_errors
from common.config.config_access import ConfigSource
from common.games import info_migration
from common.i18n import t
from common.media import launch_media

from . import folder as backup_folder
from .copies import REASON_DELETE, REASON_TURN

MEDIA = "media"
ASSETS = "assets"
TABLES = "tables"
AREAS = (MEDIA, ASSETS, TABLES)
# A file taken out of the launch media folder is kept under this id in the media area, where
# a game's id would be.
LAUNCH_ID = "launch-media"


@dataclass(frozen=True)
class Where:
    """Which game and file a copy is of, read off where it is kept."""

    folder: Path
    area: str
    game_id: str
    relative: str


def _stamped(base: Path, reason: str, when: datetime) -> Path:
    return Path(kept_copies.backup_path(base, when, reason))


def keep(folder: Path, game_id: str, source: Path, relative: str,
         reason: str = REASON_TURN, *, area: str = MEDIA) -> Path:
    """Copy `source`, the file at `relative` in the game's folder, into `folder`'s `area`.
    Never replaces a copy: a name taken is a second later."""
    if Path(game_id).name != game_id:
        raise service_errors.RefusedError(t("error.media_rotate.not_in_game"))
    if area not in AREAS:
        raise ValueError(area)
    base = folder / area / game_id / relative
    when = datetime.now(UTC)
    kept = _stamped(base, reason, when)
    while kept.exists():
        when += timedelta(seconds=1)
        kept = _stamped(base, reason, when)
    kept.parent.mkdir(parents=True, exist_ok=True)
    with atomic_write.staged_for(kept) as staged:
        shutil.copy2(source, staged)
    return kept


def keep_launch(source: Path, reason: str) -> Path:
    """Copy a file of the launch media folder into the backups folder."""
    return keep(backup_folder.for_writing(), LAUNCH_ID, source, source.name, reason)


def remove_launch(name: str, config: ConfigSource | None = None) -> Path:
    """Take a file out of the launch media folder, keeping a copy, and return the copy. The
    file a setting plays is refused."""
    path = launch_media.path_of(name)
    if path is None:
        raise service_errors.NotFoundError(t("error.launch_media.gone", name=name))
    if launch_media.in_use(name, config):
        raise service_errors.RefusedError(t("error.launch_media.in_use", name=name))
    kept = keep_launch(path, REASON_DELETE)
    try:
        path.unlink()
    except OSError as exc:
        raise service_errors.RefusedError(t("error.launch_media.remove", name=name)) from exc
    return kept


def where(path: Path) -> Where | None:
    """Where a path is kept, or None when it is not a copy in a known folder. `path` is
    believed only under a folder backups are kept in."""
    if not kept_copies.is_copy(path):
        return None
    for folder in backup_folder.known():
        for area in AREAS:
            try:
                parts = path.relative_to(folder / area).parts
            except ValueError:
                continue
            if len(parts) < 2:
                return None
            named = info_migration.copied_from(path).name
            return Where(folder, area, parts[0], "/".join((*parts[1:-1], named)))
    return None


def prune(path: Path) -> None:
    """Remove the folders a deleted copy leaves empty, up to its area."""
    for folder in backup_folder.known():
        for area in AREAS:
            stop = folder / area
            if stop not in path.parents:
                continue
            parent = path.parent
            while parent != stop:
                try:
                    parent.rmdir()
                except OSError:
                    return
                parent = parent.parent
            return
