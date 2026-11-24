"""Files VPinFE could not read, remembered until it restarts.

Kept by path with the size and modified time the file had, so a file is read again once it
changes and not before.
"""

from __future__ import annotations

import os
from typing import TypedDict


class UnreadableGame(TypedDict):
    """A game folder left out of the library because its .info could not be read. `error`
    is the reason, in words."""

    folder: str
    path: str
    error: str


# path -> ((size, mtime_ns) when it could not be read, why).
_FAILED: dict[str, tuple[tuple[int, int], str]] = {}


def _key(path: str | os.PathLike[str]) -> str:
    return os.path.normpath(os.path.abspath(os.fspath(path)))


def _signature(key: str) -> tuple[int, int] | None:
    try:
        stat = os.stat(key)
    except OSError:
        return None
    return stat.st_size, stat.st_mtime_ns


def failed(path: str | os.PathLike[str]) -> str | None:
    """Why `path` could not be read, where it has not changed since; otherwise None."""
    key = _key(path)
    known = _FAILED.get(key)
    if known is None:
        return None
    if _signature(key) != known[0]:
        _FAILED.pop(key, None)
        return None
    return known[1]


def note(path: str | os.PathLike[str], why: str = "") -> None:
    """`path`, as it is now, could not be read. A file that is not there is not kept."""
    key = _key(path)
    signature = _signature(key)
    if signature is not None:
        _FAILED[key] = (signature, why)
