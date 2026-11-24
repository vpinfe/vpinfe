"""The displays Visual Pinball says it used, read from its own log."""

from __future__ import annotations

import re
from pathlib import Path

LOG_FILE = "vpinball.log"

# The display it picked for itself, where none was set or the one set was not there.
_USED = re.compile(r'Using display "(.+)"(?: instead)?\.\s*$')
_OPENED = re.compile(r"was created on display (.+) \[\d+x\d+ [^\[\]]*\]\s*$")
_MISSING = re.compile(r'The selected display "(.+?)" is not available\.')

_SEEN: dict[Path, tuple[tuple[int, int], tuple[str, ...]]] = {}


def reported(log: str) -> tuple[str, ...]:
    """The display names a log says VPX used, the most recent first, leaving out any it
    last said was not available."""
    last: dict[str, bool] = {}
    for line in log.splitlines():
        if "display" not in line:
            continue
        if missing := _MISSING.search(line):
            last.pop(missing[1], None)
            last[missing[1]] = False
        if used := _USED.search(line) or _OPENED.search(line):
            last.pop(used[1], None)
            last[used[1]] = True
    return tuple(name for name, there in reversed(last.items()) if there)


def reported_in(path: Path | None) -> tuple[str, ...]:
    """`reported` for the log at `path`, read again only once the file has changed."""
    if path is None:
        return ()
    try:
        found = path.stat()
    except OSError:
        return ()
    stamp = (found.st_mtime_ns, found.st_size)
    held = _SEEN.get(path)
    if held is not None and held[0] == stamp:
        return held[1]
    try:
        names = reported(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return ()
    _SEEN[path] = (stamp, names)
    return names
