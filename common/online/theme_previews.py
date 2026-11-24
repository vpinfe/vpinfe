"""A theme's preview picture, kept on this machine so it can be sized like other art.

One copy per theme, named by its address and release: a new release, or an address that
moved, is fetched again and the old copy, with the sized copies made from it, is deleted.
The host is asked about the same release again after `RECHECK_AFTER`, in case the picture
changed under it, and after `RETRY_AFTER` when it did not answer.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import requests

from common.atomic_write import staged_for, write_atomic
from common.config.paths import CACHE_DIR
from common.media import sized_media
from common.security import http_client

logger = logging.getLogger("vpinfe.common.online.theme_previews")

ROOT = CACHE_DIR / "theme_previews"

RECHECK_AFTER = 24 * 60 * 60
RETRY_AFTER = 5 * 60

_PREFIX = "source-"
_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}

_locks: dict[Path, threading.Lock] = {}
_locks_guard = threading.Lock()
_failed: dict[Path, float] = {}


def _lock_for(copy: Path) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(copy, threading.Lock())


def _folder(key: str) -> Path:
    return ROOT / hashlib.blake2b(key.encode(), digest_size=8).hexdigest()


def _release(url: str, release: str) -> str:
    return hashlib.blake2b(f"{url}\0{release}".encode(), digest_size=5).hexdigest()


def _copy(key: str, url: str, release: str) -> Path:
    """Named with the address's own suffix, so a file served as it is keeps its type."""
    suffix = Path(urlsplit(url).path).suffix.lower()
    kept = suffix if suffix in _SUFFIXES else ""
    return _folder(key) / f"{_PREFIX}{_release(url, release)}{kept}"


def _fetchable(url: str) -> bool:
    """A picture's address is the theme author's to name, so this machine does not go to a
    host that is plainly its own or its network's."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host or host == "localhost" or host.endswith(".localhost"):
        return False
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        return True


def _note(copy: Path) -> Path:
    return copy.with_name(copy.name + ".json")


def _held(copy: Path) -> dict:
    try:
        held = json.loads(_note(copy).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return held if isinstance(held, dict) else {}


def version(key: str, url: str, release: str) -> str:
    """What an address for this picture carries as `v`: the kept copy's own token, which
    changes when the copy is replaced, or the release's before one is kept."""
    copy = _copy(key, url, release)
    return sized_media.version(copy) or _release(url, release)


def path(key: str, url: str, release: str) -> Path | None:
    """The picture as a file on this machine: the kept copy of this release, fetched when
    there is none and asked about again when it is old. None when it cannot be had."""
    if not _fetchable(url):
        return None
    copy = _copy(key, url, release)
    with _lock_for(copy):
        held = _held(copy) if copy.is_file() else {}
        now = time.time()
        if copy.is_file() and now - float(held.get("checked") or 0) < RECHECK_AFTER:
            return copy
        if now - _failed.get(copy, 0) < RETRY_AFTER:
            return copy if copy.is_file() else None
        try:
            fetched = http_client.get_if_changed(url, str(held.get("etag") or ""), timeout=15)
            if fetched is not None:
                _keep(copy, fetched[0])
        except requests.RequestException as exc:
            _failed[copy] = now
            http_client.unreachable(logger, f"theme picture {url}", exc)
            return copy if copy.is_file() else None
        except OSError:
            _failed[copy] = now
            logger.warning("Could not keep the picture at %s", url, exc_info=True)
            return copy if copy.is_file() else None
        _failed.pop(copy, None)
        _stamp(copy, fetched[1] if fetched is not None else str(held.get("etag") or ""))
        return copy if copy.is_file() else None


def _keep(copy: Path, content: bytes) -> None:
    copy.parent.mkdir(parents=True, exist_ok=True)
    with staged_for(copy) as staged:
        staged.write_bytes(content)
    for older in copy.parent.glob(f"{_PREFIX}*"):
        if older.name != copy.name and not older.name.endswith(".json"):
            sized_media.forget(older)
            older.unlink(missing_ok=True)
            _note(older).unlink(missing_ok=True)


def _stamp(copy: Path, etag: str) -> None:
    if not copy.is_file():
        return
    try:
        write_atomic(_note(copy), lambda out: json.dump({"etag": etag,
                                                         "checked": time.time()}, out))
    except OSError:
        logger.warning("Could not note when %s was checked", copy, exc_info=True)
