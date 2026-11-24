"""The pictures of a library held on another install, fetched by this device.

A window asks this device at `/library/<path>`; this device asks the library with the token
it was paired with and hands the file back. Only this device's own windows are served.
Only the library's collection-image, game and table media, manufacturer-logo and table-file
routes are forwarded, so a path outside them is a 404 and never reaches the library.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterator
from urllib.parse import unquote, urljoin

import requests
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, Response, StreamingResponse

from common.devices.device_registry import paired_header
from common.security import http_client
from httpapi import auth

logger = logging.getLogger("vpinfe.frontend.library_art")

PREFIX = "/library/"
TIMEOUT = 15
CHUNK = 64 * 1024

_SEGMENT = r"[^/]+"
ART_PATHS = tuple(re.compile(pattern) for pattern in (
    rf"api/v1/collections/{_SEGMENT}/image",
    rf"api/v1/games/{_SEGMENT}(?:/tables/{_SEGMENT})?/media/{_SEGMENT}",
    rf"media/{_SEGMENT}/{_SEGMENT}",
    rf"manufacturers/{_SEGMENT}/logo",
    rf"tables/{_SEGMENT}/.+",
))

_ASKED = ("if-none-match", "range")
_ANSWERED = ("content-type", "content-length", "content-range", "accept-ranges", "etag",
             "cache-control")


def is_art(path: str) -> bool:
    """Whether `path`, as it was sent (percent-encoded), is one of the library's art routes
    and climbs out of none of them."""
    if not any(route.fullmatch(path) for route in ART_PATHS):
        return False
    if any("/" in unquote(part) or "\\" in unquote(part) for part in path.split("/")):
        return False
    return not any(unquote(part) in (".", "..") for part in path.split("/"))


def register(app: FastAPI, library_url: Callable[[], str]) -> None:
    """Add `/library/<path>` to `app`. `library_url` is read per request, so a library
    chosen in Settings applies to the next picture."""

    @app.get(PREFIX + "{path:path}", include_in_schema=False)
    def library_file(request: Request, path: str) -> Response:
        if not auth.caller_is_local(request):
            return PlainTextResponse("", status_code=403)
        library = library_url()
        if not library:
            return PlainTextResponse("This device holds its own library", status_code=404)
        sent = _sent_path(request)
        if not is_art(sent):
            return PlainTextResponse("No such picture", status_code=404)
        return _fetched(request, urljoin(library.rstrip("/") + "/", sent))


def _sent_path(request: Request) -> str:
    raw = request.scope.get("raw_path")
    full = raw.decode("latin-1") if isinstance(raw, bytes) else request.url.path
    return full.split("?", 1)[0].removeprefix(PREFIX)


def _fetched(request: Request, url: str) -> Response:
    if request.url.query:
        url = f"{url}?{request.url.query}"
    headers = {**(paired_header(url) or {}),
               **{name: request.headers[name] for name in _ASKED if name in request.headers}}
    try:
        answer = http_client.get_response(url, timeout=TIMEOUT, headers=headers, stream=True)
    except (requests.RequestException, OSError) as exc:
        logger.debug("The library did not answer for %s: %s", url, exc)
        return PlainTextResponse("The library did not answer", status_code=502)
    if answer.status_code in (401, 403):
        answer.close()
        return PlainTextResponse("", status_code=403)
    if answer.status_code not in (200, 206, 304, 404, 416):
        answer.close()
        return PlainTextResponse("The library did not answer", status_code=502)
    kept = {name: answer.headers[name] for name in _ANSWERED if name in answer.headers}
    if answer.status_code in (304, 404, 416):
        answer.close()
        return Response(content=b"", status_code=answer.status_code, headers={
            name: value for name, value in kept.items()
            if name in ("etag", "cache-control", "content-range")})
    return StreamingResponse(_chunks(answer), status_code=answer.status_code, headers=kept)


def _chunks(answer: requests.Response) -> Iterator[bytes]:
    try:
        yield from answer.iter_content(CHUNK)
    finally:
        answer.close()
