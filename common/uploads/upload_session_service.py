"""An upload in progress.

Files arrive in chunks and land in a session directory, never straight into the
library. Every path is checked against that directory: an upload naming `../` is
refused rather than resolved.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import IO

from common.i18n import t

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


def begin_session(source: Path | None = None) -> UploadSession:
    """Create a fresh upload session backed by a temp directory (sweeping expired ones first).

    With `source`, the session reads that file or folder where it is instead, and
    cleaning the session up leaves it alone.
    """
    _sweep()
    upload_id = uuid.uuid4().hex
    directory = tempfile.mkdtemp(prefix="vpinfe_upload_")
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


def store_file(upload_id: str, relpath: str, stream: IO[bytes]) -> int:
    """Stream a single uploaded file into the session directory at its relative path."""
    rec = _record(upload_id)
    if rec.get("source"):
        raise UnsafePathError(t("error.uploads.reads_folder_here"))
    dest = _safe_join(Path(rec["dir"]), relpath)
    dest.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with open(dest, "wb") as out:
        while True:
            chunk = stream.read(_CHUNK)
            if not chunk:
                break
            written += len(chunk)
            with _lock:
                rec["bytes"] += len(chunk)
                total = rec["bytes"]
            if total > MAX_TOTAL_BYTES:
                raise UploadTooLargeError(t("error.uploads.over_size_limit",
                                            limit=MAX_TOTAL_BYTES // 1024 ** 3))
            out.write(chunk)
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
            rec["pictures"] = tempfile.mkdtemp(prefix="vpinfe_upload_pictures_")
        return Path(rec["pictures"])
