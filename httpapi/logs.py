"""This install's own log, as records rather than lines.

A traceback is one thing that happened. Split into fourteen rows it buries the message
that caused it, and a level filter drops the half carrying the reason - so a record keeps
its continuation lines and a filter applies to the whole of it.

Answered by the install holding the file. A fleet surface reads another machine's log
through that machine, which is the only party that has it.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from common.host import log_sources

from . import models, scopes
from .auth import requires

logger = logging.getLogger("vpinfe.httpapi.logs")

router = APIRouter(prefix="/logs", tags=["logs"])

# Enough to see what led to something without handing over the whole file. A reader that
# wants more of it has the path.
MAX_RECORDS = 1000


@router.get("", summary="Recent log records from this install",
            dependencies=[requires(scopes.SYSTEM_READ)])
def get_log(limit: int = Query(200, ge=1, le=MAX_RECORDS),
            level: str = "", contains: str = "",
            source: str = "") -> models.LogRecords:
    """The tail, oldest first, optionally filtered by level or by text.

    `source` is a name resolved against the files this install reads, never a path.
    """
    return models.LogRecords.model_validate(
        log_sources.read(source, limit, level, contains))


@router.get("/file", summary="One of this install's log files, whole",
            dependencies=[requires(scopes.SYSTEM_READ)],
            response_class=FileResponse)
def download_log(source: str = "") -> FileResponse:
    """The file itself, for somebody who wants to attach it to a report.

    Resolved against the same list `source` is above, so a name that is not one of those
    files is a 404 rather than a path read.
    """
    chosen = log_sources.resolve(source)
    if chosen is None or not chosen.path.is_file():
        raise HTTPException(status_code=404, detail="No such log")
    if chosen.path.stat().st_size > log_sources.MAX_DOWNLOAD_BYTES:
        raise HTTPException(status_code=413, detail="That log is too large to download")
    return FileResponse(chosen.path, media_type="text/plain", filename=chosen.name)
