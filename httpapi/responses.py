"""Responses several routers need to send, shaped the same way each time."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import Query
from starlette.requests import Request
from starlette.responses import FileResponse, Response

from common.media import sized_media
from common.media.sized_media import Served

FOREVER = "public, max-age=31536000, immutable"
HELD_LIMIT = 16 * 1024 * 1024

# A library file is the app's own address but not the app's own content: a downloaded .html
# opened from here would run in the Manager's origin. `sandbox` gives the document an opaque
# origin and no scripts, and `nosniff` stops a file served as one type being read as another.
LIBRARY_FILE = {"Content-Security-Policy": "sandbox", "X-Content-Type-Options": "nosniff"}

MEDIA_SIZE = Query(None, description="The longest edge in pixels, for an image: "
                                     + " or ".join(str(one) for one in sized_media.SIZES))
MEDIA_VERSION = Query("", description="The `version` the listing gave; a match is kept "
                                      "for good")


def media_file(served: Served, request: Request, sent_version: str) -> Response:
    """Kept for good when the address names the file's current version, and asked
    about every time otherwise."""
    if sent_version and sent_version == served.version:
        return _whole_file(served.path, request,
                           {"Cache-Control": FOREVER, **LIBRARY_FILE}, revalidate=False)
    return revalidating_file(served.path, request)


def revalidating_file(path: Path, request: Request) -> Response:
    """A file that is always asked about and rarely re-sent.

    These URLs name a slot rather than a file, so a replacement changes the bytes
    behind an unchanged address; without `no-cache` a browser guesses freshness from
    Last-Modified and can serve stale art for days.

    The 304 is not optional with it. Starlette answers conditional requests only from
    `StaticFiles`, so a bare `FileResponse` re-sends the whole file every time - which
    on a media map of thirteen tiles turns "sometimes stale" into megabytes per draw.
    """
    return _whole_file(path, request, {"Cache-Control": "no-cache", **LIBRARY_FILE},
                       revalidate=True)


def _whole_file(path: Path, request: Request, headers: dict[str, str], *,
                revalidate: bool) -> Response:
    """The file behind `path` as one response, its headers and body from one open handle.

    A replacement renames a new file over `path`, so a `FileResponse`, which stats the path
    and opens it again to send, can declare the old file's length over the new file's bytes
    and the client reads a short body. A handle keeps the file it opened. A range, or a
    file over `HELD_LIMIT`, is still sent from the path.
    """
    with path.open("rb") as handle:
        info = os.fstat(handle.fileno())
        # FileResponse only fills in etag and last-modified when it is given a stat,
        # otherwise they appear while the response is being sent - too late to compare.
        response = FileResponse(path, stat_result=info, headers=headers)
        sent = request.headers.get("if-none-match") if request is not None else ""
        etag = response.headers.get("etag", "")
        if revalidate and sent and etag and etag in [tag.strip().removeprefix("W/")
                                                     for tag in sent.split(",")]:
            return Response(status_code=304, headers={**headers, "ETag": etag})
        if info.st_size > HELD_LIMIT or (request is not None and request.headers.get("range")):
            return response
        content = handle.read()
    sent_headers = {k: v for k, v in response.headers.items() if k != "content-length"}
    return Response(content, headers=sent_headers)
