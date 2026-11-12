"""An upload in progress.

Files arrive in chunks and land in a session directory, never straight into the
library. Every path is checked against that directory: an upload naming `../` is
refused rather than resolved. The directory is in the working folder beside the library,
never the system temp folder (see `working_folder`).
"""

from __future__ import annotations

import contextlib
import logging
import shutil
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import IO

from common.i18n import t
from common.uploads import working_folder

logger = logging.getLogger("vpinfe.common.uploads.upload_session_service")

MAX_TOTAL_BYTES = 20 * 1024 ** 3      # 20 GiB per session (PUP packs are large)
SESSION_TTL_SECONDS = 3600
_CHUNK = 1024 * 1024


class UnknownSessionError(ValueError):
    pass


class UnsafePathError(ValueError):
    pass


class UploadTooLargeError(ValueError):
    pass


@dataclass(frozen=True)
class UploadSession:
    upload_id: str
    directory: str
    created: float


_sessions: dict[str, dict] = {}     # upload_id -> {"dir": str, "created": float, "bytes": int}
_lock = threading.Lock()


def _safe_join(base: Path, relative: str) -> Path:
    rel = PurePosixPath(relative.replace("\\", "/"))
    drive_letter = len(relative) >= 2 and relative[1] == ":"
    if not relative or rel.is_absolute() or ".." in rel.parts or drive_letter:
        raise UnsafePathError(t("error.uploads.unsafe_upload_path", path=relative))
    dest = (base / Path(*rel.parts)).resolve()
    try:
        dest.relative_to(base.resolve())
    except ValueError as exc:
        raise UnsafePathError(t("error.uploads.unsafe_upload_path", path=relative)) from exc
    return dest


_held: set[str] = set()


def hold(upload_id: str) -> None:
    """Keep a session from being swept or aborted while an import reads from it."""
    with _lock:
        _held.add(upload_id)


def release(upload_id: str) -> None:
    with _lock:
        _held.discard(upload_id)


def is_held(upload_id: str) -> bool:
    with _lock:
        return upload_id in _held


def _sweep() -> None:
    now = time.time()
    with _lock:
        stale = [uid for uid, rec in _sessions.items()
                 if now - rec["created"] > SESSION_TTL_SECONDS and uid not in _held]
        records = [_sessions.pop(uid) for uid in stale]
    for rec in records:
        _remove(rec)


def _in_use() -> set[str]:
    with _lock:
        return {path for rec in _sessions.values()
                for path in (rec["dir"], rec.get("pictures")) if path}


def begin_session(source: Path | None = None, expected_bytes: int = 0) -> UploadSession:
    """Create a fresh upload session in the working folder (sweeping expired ones first).

    `expected_bytes` is what is about to be stored in it: the session is refused, with the
    reason, where the drive has no room for that.

    With `source`, the session reads that file or folder where it is instead, and
    cleaning the session up leaves it alone.
    """
    _sweep()
    working_folder.clear_leftovers(_in_use())
    if source is None:
        working_folder.ensure_room(expected_bytes)
    upload_id = uuid.uuid4().hex
    directory = "" if source else str(working_folder.make("upload-"))
    created = time.time()
    with _lock:
        _sessions[upload_id] = {"dir": directory, "created": created, "bytes": 0,
                                "source": str(source) if source else ""}
    return UploadSession(upload_id, directory, created)


def get_session_source(upload_id: str) -> Path | None:
    found = _record(upload_id).get("source") or ""
    return Path(found) if found else None


def _record(upload_id: str) -> dict:
    with _lock:
        rec = _sessions.get(upload_id)
    if rec is None:
        raise UnknownSessionError(t("error.uploads.upload_gone"))
    return rec


@contextlib.contextmanager
def writer(upload_id: str, relpath: str) -> Iterator[Callable[[bytes], None]]:
    """Open one file of the session for writing, and hand back what takes its chunks.

    A drive that fills up is refused with the reason, in plain words, instead of as the
    error it raised.
    """
    rec = _record(upload_id)
    if rec.get("source"):
        raise UnsafePathError(t("error.uploads.reads_folder_here"))
    dest = _safe_join(Path(rec["dir"]), relpath)
    written = 0

    def put(chunk: bytes) -> None:
        nonlocal written
        with _lock:
            rec["bytes"] += len(chunk)
            total = rec["bytes"]
        written += len(chunk)
        if total > MAX_TOTAL_BYTES:
            raise UploadTooLargeError(t("error.uploads.over_size_limit",
                                        limit=MAX_TOTAL_BYTES // 1024 ** 3))
        out.write(chunk)

    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as out:
            yield put
    except BaseException as exc:
        # A file that did not arrive whole is not kept: it would hold the drive, and count
        # against the session's limit, until the session expires.
        dest.unlink(missing_ok=True)
        with _lock:
            rec["bytes"] -= written
        if isinstance(exc, OSError):
            raise working_folder.out_of_room(exc) or exc from exc
        raise


def store_file(upload_id: str, relpath: str, stream: IO[bytes]) -> int:
    """Stream a single uploaded file into the session directory at its relative path."""
    written = 0
    with writer(upload_id, relpath) as put:
        while True:
            chunk = stream.read(_CHUNK)
            if not chunk:
                break
            written += len(chunk)
            put(chunk)
    return written


def get_session_dir(upload_id: str) -> Path:
    return Path(_record(upload_id)["dir"])


def finish_session(upload_id: str) -> dict:
    directory = get_session_source(upload_id) or get_session_dir(upload_id)
    files = ([directory] if directory.is_file()
             else [p for p in directory.rglob("*") if p.is_file()])
    return {"file_count": len(files), "total_bytes": sum(p.stat().st_size for p in files)}


def cleanup_session(upload_id: str) -> None:
    with _lock:
        rec = _sessions.pop(upload_id, None)
    if rec is not None:
        _remove(rec)


def _remove(rec: dict) -> None:
    if rec["dir"]:
        shutil.rmtree(rec["dir"], ignore_errors=True)
    if rec.get("pictures"):
        shutil.rmtree(rec["pictures"], ignore_errors=True)


def picture_folder(upload_id: str) -> Path:
    """Where pictures taken out of an archive are kept for the sheet to draw. It goes with
    the session and is never inside it, so it is not read as part of the drop."""
    with _lock:
        rec = _sessions.get(upload_id)
        if rec is None:
            raise UnknownSessionError(t("error.uploads.upload_gone"))
        if not rec.get("pictures"):
            rec["pictures"] = str(working_folder.make("pictures-"))
        return Path(rec["pictures"])
