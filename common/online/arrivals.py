"""When each source's items were first seen, and the version they held before it changed.

Kept in STATE_DIR rather than the cache: a first-seen date cannot be rebuilt, and clearing
the cache would make every item new.

Two jobs only. A source that gives no dates is dated by when it was first seen, and a
release a source re-dated says what it was (`1.0.8, was 1.0.7`). Nothing is kept about when
anyone last looked.

A source's first refresh stamps everything it lists as already seen, so a new source's
backlog never reads as news.
"""

from __future__ import annotations

import functools
import json
import logging
import threading
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from common import timestamps
from common.atomic_write import write_atomic
from common.config.paths import STATE_DIR
from common.online.sources import Listed

logger = logging.getLogger("vpinfe.common.online.arrivals")

PATH = STATE_DIR / "arrivals.json"
SCHEMA = 1

_LOCK = threading.RLock()


def _locked[F: Callable[..., Any]](work: F) -> F:
    @functools.wraps(work)
    def held(*args: Any, **kwargs: Any) -> Any:
        with _LOCK:
            return work(*args, **kwargs)
    return held  # type: ignore[return-value]


# The file as last parsed, keyed by where it is, when it changed and how big it was. A pass
# over every release asks about each one, and parsing the file per question is quadratic.
_parsed: tuple[tuple[Path, int, int], dict[str, Any]] | None = None


def _load() -> dict[str, Any]:
    global _parsed
    try:
        stat = PATH.stat()
    except FileNotFoundError:
        return {}
    except OSError:
        logger.warning("Could not read %s; treating it as empty", PATH)
        return {}
    key = (PATH, stat.st_mtime_ns, stat.st_size)
    if _parsed is not None and _parsed[0] == key:
        return dict(_parsed[1])
    try:
        data = json.loads(PATH.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("Could not read %s; treating it as empty", PATH)
        return {}
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return {}
    _parsed = (key, data)
    return dict(data)


def _save(data: dict[str, Any]) -> None:
    global _parsed
    _parsed = None
    PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        write_atomic(PATH, lambda out: json.dump({**data, "schema": SCHEMA}, out))
    except Exception:
        logger.exception("Could not write %s", PATH)


def _items(data: dict[str, Any], source_id: str) -> dict[str, list[str]]:
    return ((data.get("sources") or {}).get(source_id) or {}).get("items") or {}


@_locked
def seeded(source_id: str) -> bool:
    """Whether the source's first refresh has been stamped."""
    return source_id in (_load().get("sources") or {})


@_locked
def stamp(source_id: str, listed: Iterable[Listed]) -> list[str]:
    """Take what the source lists now, and answer the ids that are new.

    Each item is held as `[first seen, version, was]`: the day it was first seen ("" for
    the backlog a first refresh found), the version it holds, and the version it held before
    the last time that changed. An item that stops being listed is kept, since it may come
    back and would otherwise read as new. A source that lists nothing yet is not seeded, so
    a catalog still downloading does not turn its whole backlog into news.
    """
    data = _load()
    sources = dict(data.get("sources") or {})
    first = source_id not in sources
    held = {key: list(value) for key, value in _items(data, source_id).items()}
    now = timestamps.utc_now_iso()
    arrived: list[str] = []
    changed = first
    for item in listed:
        known = held.get(item.id)
        if known is None:
            held[item.id] = ["" if first else now, item.version, ""]
            if not first:
                arrived.append(item.id)
                changed = True
        elif known[1] != item.version:
            held[item.id] = [known[0], item.version, known[1]]
            changed = True
    if first and not held:
        return []
    if changed:
        sources[source_id] = {"items": held}
        data["sources"] = sources
        _save(data)
    return arrived


@_locked
def first_seen(source_id: str, item_id: str) -> str:
    """When the item first arrived, or "" for one that was there before the source was
    watched."""
    found = _items(_load(), source_id).get(item_id)
    return str(found[0]) if found else ""


@_locked
def was(source_id: str, item_id: str) -> str:
    """The version the item held before it last changed, or "" if it never has."""
    found = _items(_load(), source_id).get(item_id)
    return str(found[2]) if found and len(found) > 2 else ""


def dated(source_id: str, item: Listed) -> str:
    """The date to put on an item: the one the source gives, else when it was first seen,
    else nothing."""
    return item.date or first_seen(source_id, item.id)
