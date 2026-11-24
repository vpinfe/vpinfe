"""Writing a file so a reader never sees half of one."""

from __future__ import annotations

import contextlib
import os
import secrets
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import IO

# Underscores, not the BACKUP_MARKER hyphen: must not read as a restore point.
STAGED_PREFIX = ".vpinfe_write_"
STAGED_SUFFIX = ".tmp"


def _named(exc: OSError) -> str:
    if isinstance(exc.filename, (str, bytes, os.PathLike)):
        return os.path.abspath(os.fsdecode(exc.filename))
    return ""


@contextlib.contextmanager
def naming_folder(path: str | Path) -> Iterator[None]:
    """An OSError raised inside that names a file in `path`'s folder, other than `path`
    itself, names the folder instead.

    Wrap only what touches a file made beside `path`: a failure on another real file in
    the folder, one being unlinked say, would lose that file's name.
    """
    target = os.path.abspath(path)
    folder = os.path.dirname(target)
    try:
        yield
    except OSError as exc:
        named = _named(exc)
        if named and named != target and os.path.dirname(named) == folder:
            exc.filename = folder
        raise


@contextlib.contextmanager
def staged_for(path: str | Path) -> Iterator[Path]:
    """A path beside `path` to write, moved over `path` when the block ends cleanly and
    removed when it does not.

    Nothing is made here; whatever writes the staged path makes it. An OSError on it names
    the folder.
    """
    target = Path(os.path.abspath(path))
    staged = target.with_name(f"{STAGED_PREFIX}{secrets.token_hex(6)}{STAGED_SUFFIX}")
    try:
        yield staged
        os.replace(staged, target)
    except OSError as exc:
        if _named(exc) == str(staged):
            exc.filename = str(target.parent)
        raise
    finally:
        with contextlib.suppress(OSError):
            staged.unlink(missing_ok=True)


def write_atomic(path: str | Path, write: Callable[[IO[str]], object]) -> None:
    """Write a file so a reader sees the old one or the new one, never half of one.

    open(path, "w") truncates before writing, and the id backfill rewrites every .info in
    one burst at first launch - the worst moment to be interrupted. `write` is handed the
    open handle.
    """
    with staged_for(path) as staged, open(staged, "w", encoding="utf-8") as handle:
        write(handle)
        handle.flush()
        os.fsync(handle.fileno())
