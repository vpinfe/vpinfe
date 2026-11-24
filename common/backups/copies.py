"""One kept copy, and the id a client holds for it."""

from __future__ import annotations

import base64
import binascii
import json
import os
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path

from common.games.info_migration import needs_migration, written_by_beta
from common.i18n import t

# What a copy is of.
SUBJECT_GAME = "game"
SUBJECT_LAUNCHER = "launcher"
SUBJECT_THEME = "theme"
SUBJECT_VPINFE = "vpinfe"
SUBJECT_ARCHIVE = "archive"

# What the records in the config folder are of.
VPINFE_NAME = "VPinFE"

# What kind of file it is.
KIND_GAME = "game_metadata"
KIND_SETTINGS = "settings"
KIND_COLLECTIONS = "collections"
KIND_DEVICES = "devices"
KIND_PLAYERS = "players"
KIND_RECORDS = "records"
KIND_LAUNCHER = "launcher_settings"
KIND_THEME = "theme"
KIND_ARCHIVE = "backup"
KIND_MEDIA = "media"
KIND_ASSET = "asset"
KIND_TABLE = "table"

# Why it was taken.
REASON_UPGRADE = "upgrade"
REASON_RESTORE = "restore"
REASON_IMPORT = "import"
REASON_RESET = "reset"
REASON_FIRST_CHANGE = "first_change"
REASON_UPDATE = "update"
REASON_YOURS = "yours"
REASON_TURN = "turn"
REASON_CONVERT = "convert"
REASON_EDIT = "edit"
REASON_MOVE = "move"
REASON_REPLACE = "replace"
REASON_DELETE = "delete"

# Who wrote the file the copy holds. Empty where the file is not VPinFE's own.
BY_2X = "2.x"
BY_3X = "3.0"
BY_BETA = "beta"

def writer_of_info(data: object) -> str:
    """Which series wrote the `.info` held in `data`."""
    if written_by_beta(data):
        return BY_BETA
    return BY_2X if needs_migration(data) else BY_3X


# Where it is kept. Part of the id, so a path is only ever read in the place it came from.
PLACE_GAME = "game"
PLACE_RECORDS = "records"
PLACE_BESIDE = "beside"
PLACE_VPX_2X = "vpx_2x"
PLACE_THEME = "theme"
PLACE_ARCHIVE = "archive"
PLACE_MEDIA = "media"
PLACE_ASSET = "asset"
PLACE_TABLE = "table"
PLACE_MEMBER = "member"


@dataclass(frozen=True)
class Copy:
    id: str
    subject_kind: str
    subject_id: str
    subject_name: str
    kind: str
    file: str
    path: str
    taken: str
    written_by: str
    reason: str
    note: str
    size: int
    gone: bool
    # The id of the backup this is a part of. Empty for a copy that is a file of its own.
    within: str = ""
    # The VPinFE that wrote the backup, from its manifest. Empty for every other copy.
    version: str = ""
    # What the table was called when a table's file was copied, by version and authors.
    # Empty for every other copy.
    label: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def make_id(place: str, path: str | Path) -> str:
    """Opaque to a client: it holds the id the listing gave and hands it back."""
    raw = json.dumps([place, str(path)], separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _parts(copy_id: str) -> list[str] | None:
    try:
        padded = str(copy_id) + "=" * (-len(str(copy_id)) % 4)
        raw = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except (ValueError, binascii.Error, UnicodeError):
        return None
    if not isinstance(raw, list) or not all(isinstance(part, str) and part for part in raw):
        return None
    return raw


def _is_clean(path: str) -> bool:
    return "\0" not in path and os.path.isabs(path) and os.path.normpath(path) == path


def read_id(copy_id: str) -> tuple[str, Path] | None:
    """The place and the path an id holds, or None for anything that is not one."""
    parts = _parts(copy_id)
    if parts is None or len(parts) != 2 or not _is_clean(parts[1]):
        return None
    return parts[0], Path(parts[1])


def make_member_id(archive: str | Path, member: str) -> str:
    raw = json.dumps([PLACE_MEMBER, str(archive), member], separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def read_member_id(copy_id: str) -> tuple[Path, str] | None:
    """The backup and the part of it an id holds, or None for anything that is not one."""
    parts = _parts(copy_id)
    if parts is None or len(parts) != 3 or parts[0] != PLACE_MEMBER or not _is_clean(parts[1]):
        return None
    return Path(parts[1]), parts[2]


def iso_from_stamp(stamp: str) -> str:
    """`20261004T020630Z` as `2026-10-04T02:06:30Z`."""
    return (f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}T{stamp[9:11]}:"
            f"{stamp[11:13]}:{stamp[13:15]}Z")


def games_named(names: Iterable[str]) -> str:
    """What a backup of these games is called: the one, or the first by name and a count of
    the others."""
    named = sorted((name for name in names if name), key=str.casefold)
    if len(named) <= 1:
        return named[0] if named else ""
    return t("backups.subject.several", name=named[0], count=len(named) - 1)
