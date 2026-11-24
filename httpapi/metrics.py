"""What this machine is doing, over the wire.

Live readings only. The static facts about a machine - its host, its OS, the browser it
runs - are a device's to report, and answering them here as well would be two homes for
one thing.

Reading takes a sample, so a caller polling this is what fills the session's history.
Nothing samples on a timer of its own: a machine nobody is looking at does not need a
record of having been idle.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Query

from common.host import metrics

from . import scopes
from .auth import requires

logger = logging.getLogger("vpinfe.httpapi.metrics")

router = APIRouter(prefix="/metrics", tags=["metrics"])


def _watched() -> list[tuple[str, dict[str, str]]]:
    """The paths worth reporting free space for, each with what it is: where the library
    lives (`library`, named by its folder), where VPinFE keeps its own files (`config`)
    and where it keeps its caches (`cache`). Those rather than every mount, because they
    are what fills up and stops something working."""
    from common.config.paths import CACHE_DIR, CONFIG_DIR
    from common.games import library_folders

    found: list[tuple[str, dict[str, str]]] = []
    try:
        found += [(one.path, {"role": "library", "name": one.name})
                  for one in library_folders.configured()]
    except Exception:  # noqa: BLE001 - an unreadable list is not a metrics failure
        logger.debug("Could not read the library folders for metrics", exc_info=True)
    found.append((str(CONFIG_DIR), {"role": "config"}))
    # A cache folder is made by the first write to it, and a folder not made yet is not a
    # volume that cannot be read.
    if CACHE_DIR.exists():
        found.append((str(CACHE_DIR), {"role": "cache"}))
    return found


def _with_holds(disks: list[dict[str, Any]],
                watched: list[tuple[str, dict[str, str]]]) -> list[dict[str, Any]]:
    """Each volume with what is watched on it, so the page can title it by role rather
    than by a path. Copies: the sample is cached and shared between callers."""
    held: dict[str, list[dict[str, str]]] = {}
    for path, hold in watched:
        held.setdefault(path, []).append(hold)
    found = []
    for disk in disks:
        holds: list[dict[str, str]] = []
        for path in [disk.get("path"), *(disk.get("also") or [])]:
            holds += [one for one in held.get(str(path), []) if one not in holds]
        found.append({**disk, "holds": holds})
    return found


@router.get("", summary="What this device is doing now",
            dependencies=[requires(scopes.SYSTEM_READ)])
def read(history_seconds: float = Query(0, ge=0)) -> dict[str, Any]:
    """The current reading, and as much of this session as was asked for.

    `history_seconds` of 0 means none rather than all: a caller showing a number wants
    one sample, and sending an hour of them to draw a single figure is a page load spent
    on data nobody drew.
    """
    watched = _watched()
    now = metrics.sample(list(dict.fromkeys(path for path, _ in watched)))
    now = {**now, "disks": _with_holds(now.get("disks") or [], watched)}
    return {"now": now,
            "history": metrics.history(history_seconds) if history_seconds else [],
            "marks": metrics.marks(history_seconds) if history_seconds else [],
            "kept_seconds": metrics.HISTORY}
