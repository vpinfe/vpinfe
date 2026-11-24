"""A game's text files, read and written back as they were found.

A script written on Windows is cp1252 with CRLF line ends; a file saved by a Mac editor is
UTF-8 with LF. A save that normalized either would show a one-line edit as the whole file
changed, so the file's own encoding and line ending are read once and written back, and the
text in between always uses "\\n".

The version is the hash of the bytes, so a save made from a stale read is refused whatever
the file's size or timestamp say.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path
from typing import Any

from common import atomic_write, failures, service_errors
from common.backups import folder as backup_folder
from common.backups import media as backup_media
from common.backups.copies import REASON_EDIT
from common.games import asset_lens, game_lens, media_placement
from common.i18n import t

UTF8 = "utf-8"
UTF8_BOM = "utf-8-sig"

CRLF = "crlf"
LF = "lf"

# Past this the file is shown and not edited: a request cannot be made to hold more.
MAX_BYTES = 4 * 1024 * 1024

# The files of a game that are text: what the Assets page lists as a script, a settings
# file, a point of view, a score view or Nixie layout, a patch or the author's notes, and a
# web page rule sheet.
SUFFIXES = frozenset({".vbs", ".ini", ".pov", ".scv", ".nix", ".dif", ".txt", ".md", ".nfo",
                      ".cfg", ".html"})

_BOM = b"\xef\xbb\xbf"


# Which of the save's two 409s it is, in `details["refused"]`: a caller asks a different
# question for each.
CHANGED = "changed"
NO_COPY = "no_copy"


class ChangedError(service_errors.BlockedError):
    """The file is not the one the text was read from."""


class NoCopyError(service_errors.BlockedError):
    """The copy the save keeps first has nowhere to go."""


def version_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _decode(data: bytes, target: Path) -> tuple[str, str, bool]:
    """The text, the encoding it was read as, and whether that was lossless. Bytes that are
    not UTF-8 are read in the file's legacy code page."""
    if data.startswith(_BOM):
        try:
            return data[len(_BOM):].decode("utf-8"), UTF8_BOM, True
        except UnicodeDecodeError:
            pass
    else:
        try:
            return data.decode("utf-8"), UTF8, True
        except UnicodeDecodeError:
            pass
    legacy = asset_lens.legacy_codepage(target)
    try:
        return data.decode(legacy), legacy, True
    except UnicodeDecodeError:
        return data.decode(legacy, errors="replace"), legacy, False


def _newline_of(data: bytes) -> str:
    """The line ending most lines use. CRLF only where it outnumbers a bare LF."""
    crlf = data.count(b"\r\n")
    return CRLF if crlf > data.count(b"\n") - crlf else LF


def _split_tail(data: bytes) -> int:
    """How many bytes at the end are the start of a UTF-8 character that is not finished."""
    for back in range(1, min(4, len(data)) + 1):
        byte = data[-back]
        if byte & 0xC0 != 0x80:
            need = 4 if byte >= 0xF0 else 3 if byte >= 0xE0 else 2 if byte >= 0xC0 else 1
            return back if need > back else 0
    return 0


def _target(game_dir: Path, path: str) -> Path:
    target = asset_lens.inside(game_dir, path)
    if target.suffix.lower() not in SUFFIXES:
        raise service_errors.RefusedError(t("error.assets.not_editable"),
                                          details={"path": path})
    if not target.is_file():
        raise service_errors.NotFoundError(t("error.assets.no_such_file", path=path),
                                           details={"path": path})
    return target


def _bytes(target: Path, limit: int) -> bytes:
    with target.open("rb") as handle:
        return handle.read(limit)


def read(game_id: str, path: str) -> dict[str, Any]:
    """The file's text and what a save needs to write it back, or why it cannot be edited.

    A file with a NUL byte is not text and is refused. A file too long or not fully
    readable comes back whole or in part with `editable` false and the reason in words.
    """
    game = game_lens.game_or_refuse(game_id)
    target = _target(Path(game.full_path_game or ""), path)
    data = _bytes(target, MAX_BYTES + 1)
    if b"\x00" in data:
        raise service_errors.RefusedError(t("error.assets.not_text", path=path),
                                          details={"path": path})
    too_large = len(data) > MAX_BYTES
    if too_large:
        data = data[:MAX_BYTES]
    if too_large:
        # The cut can fall inside a character; what it splits is dropped, not misread.
        data = data[:len(data) - _split_tail(data)]
    text, encoding, exact = _decode(data, target)
    why = ""
    if too_large:
        why = t("asset.text.too_large")
    elif not exact:
        why = t("asset.text.lossy")
    else:
        try:
            game_lens.game_for_writing(game_id)
        except service_errors.BlockedError as refusal:
            why = failures.why(refusal)
    return {"text": text.replace("\r\n", "\n"), "version": version_of(data),
            "encoding": encoding, "newline": _newline_of(data),
            "final_newline": data.endswith(b"\n"), "editable": not why, "why": why}


def _encoded(text: str, encoding: str, newline: str) -> bytes:
    """`text` as the file writes it, or a refusal naming the first line it cannot hold."""
    body = text.replace("\r\n", "\n")
    if newline == CRLF:
        body = body.replace("\n", "\r\n")
    try:
        return body.encode(encoding)
    except UnicodeEncodeError:
        pass
    for number, line in enumerate(text.replace("\r\n", "\n").split("\n"), start=1):
        try:
            line.encode(encoding)
        except UnicodeEncodeError:
            raise service_errors.RefusedError(
                t("error.assets.cannot_hold", line=number),
                details={"line": number, "encoding": encoding}) from None
    raise service_errors.RefusedError(t("error.assets.cannot_hold", line=1))


def save(game_id: str, path: str, text: str, version: str, *,
         replace_changed: bool = False, keep_copy: bool = True,
         media: bool = False) -> dict[str, Any]:
    """Write `text` over the file, keeping the file as it was first.

    `version` is what `read` returned. A file that no longer matches raises `ChangedError`
    unless `replace_changed` says to write over it, and the copy kept first is what makes
    that safe. A copy that cannot be kept raises `NoCopyError` unless `keep_copy` is false.
    `media` files the copy under Media, for a file that is one of the game's media, rather
    than under Assets. Returns the new version and where the copy went, or None for none.
    """
    game = game_lens.game_for_writing(game_id)
    game_dir = Path(game.full_path_game or "")
    target = _target(game_dir, path)
    current = _bytes(target, MAX_BYTES + 1)
    if len(current) > MAX_BYTES or b"\x00" in current:
        raise service_errors.RefusedError(t("error.assets.not_editable"),
                                          details={"path": path})
    held, encoding, exact = _decode(current, target)
    if not exact:
        raise service_errors.RefusedError(t("asset.text.lossy"), details={"path": path})
    if version_of(current) != version and not replace_changed:
        raise ChangedError(t("error.assets.changed"),
                           details={"refused": CHANGED, "path": path,
                                    "version": version_of(current)})
    # Text equal to what the file holds writes nothing, so a mixed-ending file is only
    # normalized by an edit.
    if text == held.replace("\r\n", "\n"):
        return {"version": version_of(current), "kept": None}
    data = _encoded(text, encoding, _newline_of(current))

    kept = None
    if keep_copy:
        try:
            folder = backup_folder.for_writing()
        except service_errors.BlockedError as refusal:
            raise NoCopyError(failures.why(refusal),
                              details={"refused": NO_COPY, "path": path}) from None
        kept = backup_media.keep(folder, game_id, target, target.relative_to(
            game_dir.resolve()).as_posix(), REASON_EDIT,
            area=backup_media.MEDIA if media else backup_media.ASSETS)

    try:
        with atomic_write.staged_for(target) as staged:
            with staged.open("wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            shutil.copymode(target, staged)
    except OSError:
        # A copy of a file that was not replaced is not a restore point.
        if kept is not None:
            kept.unlink(missing_ok=True)
            backup_media.prune(kept)
        raise
    media_placement.record_origin(game_dir, target)
    return {"version": version_of(data), "kept": str(kept) if kept else None}
