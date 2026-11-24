"""What a `.vpx` table file says of itself."""

from __future__ import annotations

from pathlib import Path

import olefile

# `GameStg/GameData` is a run of records: a 4-byte length counting the tag and the data,
# a 4-byte tag, then the data. It ends at `ENDB`; a table that never wrote EFSS has it off.
_STREAM = ("GameStg", "GameData")
_FSS = b"EFSS"
_END = b"ENDB"


def fss_enabled(path: str | Path) -> bool | None:
    """Whether the table enables Full Single Screen, the one view Visual Pinball starts a
    table in only where the table asks for it. None where the file cannot say."""
    try:
        with olefile.OleFileIO(str(path)) as ole:
            data = ole.openstream(_STREAM).read()
    except (OSError, ValueError):
        return None
    at = 0
    while at + 8 <= len(data):
        size = int.from_bytes(data[at:at + 4], "little")
        tag = data[at + 4:at + 8]
        if tag == _FSS:
            return None if size != 8 else bool(int.from_bytes(data[at + 8:at + 12], "little"))
        if tag == _END:
            return False
        if size < 4:
            return None
        at += 4 + size
    return None
