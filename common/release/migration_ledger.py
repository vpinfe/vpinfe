"""What an update did to a person's files, kept so the review of it can say so.

Kept on this install's row, never inside the stores a migration changes, and written by
the caller that knows the count. A migration's own marker says it ran; this says what it did.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any

from common.devices import device_registry
from common.release.release_notes import is_release, newer

logger = logging.getLogger("vpinfe.common.release.migration_ledger")

KEY = "migrations"

_lock = threading.Lock()


@dataclass(frozen=True)
class Entry:
    name: str
    version: str
    count: int = 0


def _load() -> list[dict[str, Any]]:
    found = device_registry.get_device_registry().self_get(KEY)
    return [one for one in found if isinstance(one, dict) and one.get("name")] \
        if isinstance(found, list) else []


def record(name: str, version: str, count: int = 0) -> None:
    """Note that `name` ran under `version` and changed `count` things. A second call for
    the same name and version adds its count, so a job that runs in pieces is one line.
    Never raises: a note that cannot be kept must not fail what it describes."""
    with _lock:
        try:
            kept = _load()
            for one in kept:
                if one["name"] == name and one.get("version") == version:
                    one["count"] = int(one.get("count") or 0) + int(count)
                    break
            else:
                kept.append({"name": name, "version": version, "count": int(count)})
            device_registry.get_device_registry().set_self(**{KEY: kept})
        except OSError:
            logger.exception("Could not record that %s ran", name)


def entries(since: str) -> list[Entry]:
    """What ran under a tagged release after `since`, in the order it ran. With nothing
    for `since`, every such entry. A build that is not a release is not one a person ran
    an update to, so what it did is left out."""
    return [Entry(one["name"], str(one.get("version") or ""), int(one.get("count") or 0))
            for one in _load()
            if is_release(str(one.get("version") or ""))
            and (not since or newer(str(one["version"]), since))]
