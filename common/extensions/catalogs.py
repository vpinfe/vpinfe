"""Outside places an extension says a game, a table or a file can be reached."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("vpinfe.common.extensions.catalogs")

SUBJECTS = frozenset({"game", "table", "file"})


@dataclass(frozen=True)
class Catalog:
    extension: str
    key: str
    name: str
    subject: str
    link: Callable[[dict], str]


_lock = threading.RLock()
_catalogs: dict[str, Catalog] = {}


def register(extension: str, key: str, name: str, subject: str,
             link: Callable[[dict], str]) -> None:
    if subject not in SUBJECTS:
        raise ValueError(f"{key!r} names subject {subject!r}; core has "
                         f"{', '.join(sorted(SUBJECTS))}")
    with _lock:
        held = _catalogs.get(key)
        if held is not None and held.extension != extension:
            raise ValueError(f"{key!r} is already contributed by {held.extension}")
        _catalogs[key] = Catalog(extension, key, name, subject, link)
    logger.info("%s links %s to %r", extension, subject, name)


def forget(extension: str) -> None:
    with _lock:
        for key in [key for key, one in _catalogs.items() if one.extension == extension]:
            _catalogs.pop(key, None)


def links(subject: str, described: dict[str, Any]) -> list[dict[str, str]]:
    """Every place contributed for this subject, in the order they were contributed."""
    with _lock:
        held = [one for one in _catalogs.values() if one.subject == subject]
    found = []
    for one in held:
        try:
            url = str(one.link(dict(described)) or "").strip()
        except Exception:
            logger.exception("%s could not link %s %r", one.extension, subject,
                             described.get("game_id"))
            continue
        if url.lower().startswith(("http://", "https://")):
            found.append({"key": one.key, "name": one.name, "url": url})
    return found


def clear() -> None:
    """For tests."""
    with _lock:
        _catalogs.clear()
