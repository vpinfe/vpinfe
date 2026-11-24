"""Where an import keeps the files it is working on.

Beside the library, in a hidden folder of the place new games are created, so the copy of
a drop and the scratch an archive is read through are on the drive the import ends up on
and not in the system temp folder, which is often small and sometimes memory.

**Never falls back to the system temp folder.** Where that place cannot be written to, or
has no room, the import is refused with the reason.
"""

from __future__ import annotations

import contextlib
import errno
import logging
import os
import shutil
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path

from common import i18n, service_errors
from common.games import library_folders
from common.i18n import t

logger = logging.getLogger("vpinfe.common.uploads.working_folder")

NAME = ".import-working"
MB = 1024 * 1024
# What is left free beyond the files themselves, for the members read out of an archive.
MARGIN = 256 * MB
COPIES = 2
# A folder untouched this long belongs to an import that is over or was cut off.
LEFTOVER_SECONDS = 24 * 3600


def _where() -> tuple[Path, str]:
    """The folder and the name the person knows its library folder by."""
    found = library_folders.destination()
    if found.location is None:
        raise service_errors.BlockedError(found.reason)
    return Path(found.location.path) / NAME, found.location.name


def root() -> Path:
    """The working folder, made if it is not there yet."""
    folder, name = _where()
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise service_errors.BlockedError(t("error.uploads.working_folder", name=name)) from exc
    return folder


def ensure_room(stored: int) -> None:
    """Refuses unless the drive holds `stored` bytes twice, and the margin: the copy kept
    here, and the one the import then writes into the library on the same drive."""
    folder, name = _where()
    root()
    free = shutil.disk_usage(folder).free
    needed = stored * COPIES + MARGIN
    if needed > free:
        raise service_errors.BlockedError(
            t("error.uploads.no_room", name=name, needed=i18n.size(needed),
              free=i18n.size(free)),
            details={"needed": needed, "free": free})


def out_of_room(exc: OSError) -> service_errors.BlockedError | None:
    """The refusal for an error that is the drive filling up, or None for any other."""
    if exc.errno not in (errno.ENOSPC, errno.EDQUOT):
        return None
    try:
        name = _where()[1]
    except service_errors.ServiceError:
        return service_errors.BlockedError(t("error.uploads.filled_up"))
    return service_errors.BlockedError(t("error.uploads.filled_up_at", name=name))


def make(prefix: str) -> Path:
    """A new folder in the working folder, for the caller to remove."""
    return Path(tempfile.mkdtemp(prefix=prefix, dir=root()))


@contextlib.contextmanager
def scratch() -> Iterator[str]:
    """A folder that is removed when the block ends, for `tempfile.TemporaryDirectory()`'s
    place. Yields the path as text, as that does."""
    folder = make("scratch-")
    try:
        yield str(folder)
    finally:
        shutil.rmtree(folder, ignore_errors=True)


@contextlib.contextmanager
def scratch_file(suffix: str = "") -> Iterator[Path]:
    """A name to extract one file to, removed when the block ends."""
    with scratch() as folder:
        yield Path(folder) / f"file{suffix}"


def clear_leftovers(keep: set[str]) -> None:
    """Remove what a cut-off import left behind: folders not in `keep` and not touched for
    a day. Quiet where there is nothing to do or the folder cannot be read."""
    try:
        folder = _where()[0]
    except service_errors.ServiceError:
        return
    now = time.time()
    with contextlib.suppress(OSError):
        for one in os.scandir(folder):
            if one.path in keep or not one.is_dir(follow_symlinks=False):
                continue
            if now - one.stat().st_mtime > LEFTOVER_SECONDS:
                shutil.rmtree(one.path, ignore_errors=True)
