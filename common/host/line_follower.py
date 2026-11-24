"""Following lines an app appends to log files while it runs."""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import IO

logger = logging.getLogger("vpinfe.common.host.line_follower")

POLL_SECONDS = 0.25
HEAD_BYTES = 64


@dataclass
class _Place:
    """Where reading a file has got to. `offset` is None for a file that was not there."""

    path: Path
    offset: int | None = None
    identity: tuple[int, int] | None = None
    head: bytes = b""
    partial: bytes = b""
    unreadable: bool = False


class Follower:
    """Reads only what is written after it is made: each file from its end at the time,
    or from the top for one that appears later.

    A file that shrinks, is replaced or is rewritten in place is read from the top again.
    Lines reach `on_line` on the follower's own thread, without their line ending.
    Nothing raises out of it: a file that cannot be read is tried again at the next poll.
    """

    def __init__(self, paths: Iterable[str | os.PathLike[str]],
                 on_line: Callable[[str], None], *,
                 interval: float = POLL_SECONDS) -> None:
        self._places = [_Place(Path(one)) for one in dict.fromkeys(str(p) for p in paths)]
        self._on_line = on_line
        self._interval = interval
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.lines = 0
        self._thread: threading.Thread | None = None
        for place in self._places:
            self._mark(place)

    @property
    def paths(self) -> list[Path]:
        return [place.path for place in self._places]

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="line-follower", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Reads what is left, so lines written just before the app exited are not lost,
        including a last line that has no line ending."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self.poll()
        with self._lock:
            for place in self._places:
                last, place.partial = place.partial, b""
                if last:
                    self._deliver(place, last)

    def poll(self) -> None:
        with self._lock:
            for place in self._places:
                try:
                    self._read(place)
                except OSError as error:
                    if not place.unreadable and place.offset is not None:
                        logger.warning("Cannot read %s (%s); trying again at each poll",
                                       place.path, error)
                    place.unreadable = True
                else:
                    place.unreadable = False

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            self.poll()

    def _mark(self, place: _Place) -> None:
        try:
            with place.path.open("rb") as handle:
                place.offset = handle.seek(0, os.SEEK_END)
                place.identity = _identity(handle)
                place.head = _head(handle, place.offset)
            logger.info("Following %s from byte %d", place.path, place.offset)
        except OSError:
            place.offset = None
            logger.info("%s is not there yet, following it from its top once it is",
                        place.path)

    def _read(self, place: _Place) -> None:
        with place.path.open("rb") as handle:
            identity = _identity(handle)
            size = handle.seek(0, os.SEEK_END)
            if (place.offset is None or size < place.offset or identity != place.identity
                    or _head(handle, place.offset) != place.head):
                if place.offset is not None:
                    logger.info("%s was replaced or rewritten, reading it from the top",
                                place.path)
                else:
                    logger.info("%s has appeared, reading it from the top", place.path)
                place.offset, place.partial = 0, b""
                place.identity = identity
            if size == place.offset:
                return
            handle.seek(place.offset)
            data = handle.read(size - place.offset)
            place.offset += len(data)
            place.head = _head(handle, place.offset)
        *whole, place.partial = (place.partial + data).split(b"\n")
        for raw in whole:
            self._deliver(place, raw)

    def _deliver(self, place: _Place, raw: bytes) -> None:
        self.lines += 1
        try:
            self._on_line(raw.decode("utf-8", errors="replace").rstrip("\r"))
        except Exception:  # noqa: BLE001 - one bad line must not end the following
            logger.exception("A line from %s was not handled", place.path)


def _identity(handle: IO[bytes]) -> tuple[int, int]:
    info = os.fstat(handle.fileno())
    return info.st_dev, info.st_ino


def _head(handle: IO[bytes], offset: int) -> bytes:
    """The start of the file as far as it has been read: a rewrite in place changes it."""
    handle.seek(0)
    return handle.read(min(HEAD_BYTES, offset))
