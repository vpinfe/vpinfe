"""Pictures an extension's rows name, kept and served by core.

A row holds a web address or a file in the extension's folder. The page is never given
either: `address` turns it into one of core's own, and `served` answers it, so a third
party never learns who is looking at the Manager or when. Only a source that came in
this extension's own rows has an address, so the route fetches nothing else.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import requests

from common.atomic_write import staged_for
from common.config.paths import CACHE_DIR
from common.i18n import t
from common.media import sized_media
from common.security import http_client
from common.service_errors import NotFoundError

if TYPE_CHECKING:
    from .host import Record

logger = logging.getLogger("vpinfe.common.extensions.pictures")

ROOT = CACHE_DIR / "extension_pictures"
OUTBOUND = "net:outbound"
LARGEST_BYTES = 8_000_000
_MAX_PIXELS = 40_000_000
_FORMATS = ("PNG", "JPEG", "WEBP", "BMP", "GIF")
FETCH_SECONDS = 10
RETRY_AFTER = 600
_MOST_REMEMBERED = 50_000

_lock = threading.Lock()
_sources: dict[str, dict[str, str]] = {}
_failed: dict[tuple[str, str], float] = {}
_fetching: dict[tuple[str, str], threading.Lock] = {}


class OutboundNotDeclaredError(Exception):
    """A row names a web address its extension has not declared it may reach."""


HOST_SLOT = "{host}"


def is_web_address(source: str) -> bool:
    return source.lower().startswith(("http://", "https://"))


def is_openable(url: str, host: str = "localhost") -> bool:
    """A web address a browser can be sent to: `{host}` filled in, a host named, a port that
    is a number. One that does not parse is not one."""
    done = url.replace(HOST_SLOT, host)
    if not is_web_address(done):
        return False
    try:
        parts = urlsplit(done)
        has_port = parts.port is None or parts.port >= 0
    except ValueError:
        return False
    return has_port and bool(parts.hostname)


def _id(source: str) -> str:
    return hashlib.blake2b(source.encode("utf-8"), digest_size=10).hexdigest()


def address(record: Record, source: object) -> str:
    """Where the page asks for `source`, or "" for a row with no picture. Remembers it:
    only a remembered source is ever fetched."""
    wanted = str(source or "").strip()
    if not wanted:
        return ""
    if is_web_address(wanted) and (record.manifest is None
                                   or OUTBOUND not in record.manifest.capabilities):
        raise OutboundNotDeclaredError(wanted)
    picture = _id(wanted)
    with _lock:
        known = _sources.setdefault(record.name, {})
        if len(known) >= _MOST_REMEMBERED:
            known.clear()
        known[picture] = wanted
    return f"/api/v1/ext/{record.name}/pictures/{picture}"


def served(record: Record, picture: str) -> sized_media.Served:
    """The picture at list size, never the original: a file that cannot be shrunk is not
    served. Fetched the first time it is asked for, once at a time per picture."""
    with _lock:
        source = _sources.get(record.name, {}).get(picture)
        failed = _failed.get((record.name, picture), 0.0)
        fetching = _fetching.setdefault((record.name, picture), threading.Lock())
    if source is None or time.time() - failed < RETRY_AFTER:
        raise NotFoundError(t("error.extensions.no_picture"))
    with fetching:
        kept = _file(record, source, picture)
        copy = sized_media.served(kept, sized_media.THUMBNAIL) if kept else None
    if copy is None or copy.path == kept:
        if kept is not None and is_web_address(source):
            kept.unlink(missing_ok=True)
        if is_web_address(source):
            with _lock:
                _failed[(record.name, picture)] = time.time()
        raise NotFoundError(t("error.extensions.no_picture"))
    return copy


def identity(record: Record) -> sized_media.Served:
    """The picture its manifest names, at list size. Only for one that is shipped or
    accepted, and never a file that cannot be decoded and shrunk."""
    if not record.picture_shown or record.manifest is None:
        raise NotFoundError(t("error.extensions.no_picture"))
    here = _file(record, record.manifest.picture, "")
    copy = sized_media.served(here, sized_media.THUMBNAIL) if here else None
    if copy is None or copy.path == here:
        raise NotFoundError(t("error.extensions.no_picture"))
    return copy


def _file(record: Record, source: str, picture: str) -> Path | None:
    if is_web_address(source):
        return _fetched(record, source, picture)
    root = record.directory.resolve()
    try:
        here = (root / source).resolve()
    except (OSError, ValueError):
        return None
    if not here.is_relative_to(root) or not here.is_file() or not is_image(here):
        logger.warning("%s names the picture %.200s, which is not an image in its folder",
                       record.name, source)
        return None
    return here


class TooLargeError(ValueError):
    pass


class NotAnImageError(ValueError):
    pass


def _fetched(record: Record, source: str, picture: str) -> Path | None:
    if record.manifest is None or OUTBOUND not in record.manifest.capabilities:
        return None
    kept = ROOT / record.name / picture
    if kept.is_file():
        return kept

    def counted(written: int) -> None:
        if written > LARGEST_BYTES:
            raise TooLargeError

    try:
        kept.parent.mkdir(parents=True, exist_ok=True)
        with staged_for(kept) as partial:
            http_client.download_file(source, partial, timeout=FETCH_SECONDS,
                                      progress=counted)
            if not is_image(partial):
                raise NotAnImageError
    except (OSError, ValueError, requests.RequestException) as exc:
        logger.warning("%s: no picture from %.200s: %s", record.name, source,
                       str(exc) or type(exc).__name__)
        return None
    return kept


def is_image(path: Path) -> bool:
    from PIL import Image

    try:
        with Image.open(path, formats=_FORMATS) as opened:
            return opened.size[0] * opened.size[1] <= _MAX_PIXELS
    except Exception:  # noqa: BLE001 - whatever Pillow says, the file is not a picture
        return False
