"""Whether the disks a run writes to have room for what is still to come."""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from . import session

MB = 1024 * 1024
# What a run leaves free, at the least.
RESERVE = 1024 * MB
# Bytes a second: the recorder's file per screen, and a stored file.
RAW_PER_SECOND = 12 * MB
RAW_SOUND_PER_SECOND = MB // 4
STORED_VIDEO_PER_SECOND = MB // 2
STORED_PICTURE = 8 * MB
STORED_SOUND_PER_SECOND = MB // 32

Free = Callable[[Path], int]


def _free(path: Path) -> int:
    return shutil.disk_usage(path).free


def _volume(path: Path) -> Path:
    """`path`, or the nearest folder above it that exists."""
    for folder in (path, *path.parents):
        if folder.exists():
            return folder
    return path


def stored(kinds: Iterable[str], length: int) -> int:
    """Bytes the files one game keeps come to."""
    total = 0
    for kind in kinds:
        if kind == session.AUDIO:
            total += STORED_SOUND_PER_SECOND * length
        elif kind.endswith("_video"):
            total += STORED_VIDEO_PER_SECOND * length
        else:
            total += STORED_PICTURE
    return total


def raw(kinds: Iterable[str], length: int) -> int:
    """Bytes one game's recordings take while it is encoded."""
    kinds = set(kinds)
    screens = sum(1 for _, video in session.KINDS.values() if video in kinds)
    return (RAW_PER_SECOND * screens
            + (RAW_SOUND_PER_SECOND if session.AUDIO in kinds else 0)) * length


@dataclass(frozen=True)
class Need:
    """What one game still to come writes: its folder and the bytes that land there, and
    the bytes kept beside the recordings (proposals)."""

    folder: Path
    placed: int
    kept: int


def short(needs: Iterable[Need], work: Path, raw_bytes: int, free: Free | None = None
          ) -> bool:
    """Whether any disk would be left with less than RESERVE."""
    free = free or _free
    wanted: dict[int, int] = {}
    free_on: dict[int, int] = {}

    def add(path: Path, count: int) -> None:
        where = _volume(path)
        device = os.stat(where).st_dev
        if device not in free_on:
            free_on[device] = free(where)
        wanted[device] = wanted.get(device, 0) + count

    add(work, raw_bytes)
    for need in needs:
        add(need.folder, need.placed)
        add(work, need.kept)
    return any(free_on[device] - count < RESERVE for device, count in wanted.items())

