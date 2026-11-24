"""What recordings learned of an app's windows from the desktop."""

from __future__ import annotations

import copy
import json
import logging
import threading
from collections.abc import Iterable
from typing import Any

from common.atomic_write import write_atomic
from common.config.paths import CAPTURE_DIR
from common.host import launch, tools
from common.timestamps import utc_now_iso

from . import placing

logger = logging.getLogger("vpinfe.common.capture.unshown")

# {game id: {table: {"placed_by", "windows", "learned_at"}}}
FILE = CAPTURE_DIR / "unshown.json"

_lock = threading.RLock()
_cached: tuple[tuple[str, int, int], dict[str, Any]] | None = None


def _read() -> dict[str, Any]:
    global _cached
    try:
        now = FILE.stat()
        stamp = (str(FILE), now.st_mtime_ns, now.st_size)
        if _cached is not None and _cached[0] == stamp:
            return _cached[1]
        found = json.loads(FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        logger.warning("Could not read %s; nothing is remembered", FILE, exc_info=True)
        return {}
    held = found if isinstance(found, dict) else {}
    _cached = (stamp, held)
    return held


def learn(game_id: str, placer: placing.Placer | None, seen: Iterable[str],
          unseen: Iterable[str]) -> None:
    """A recording of the table `placer` names read the desktop: `seen` are the app's
    windows it showed, `unseen` those the recording was to record that it showed nowhere."""
    if placer is None or not placer.placed_by:
        return
    with _lock:
        held = copy.deepcopy(_read())
        tables = held.setdefault(game_id, {})
        before = tables.get(placer.table) or {}
        kept = set(before.get("windows") or ()) \
            if before.get("placed_by") == placer.placed_by else set()
        windows = sorted((kept - set(seen)) | set(unseen))
        if windows == sorted(kept) and (windows or not before):
            return
        if windows:
            tables[placer.table] = {"placed_by": placer.placed_by, "windows": windows,
                                    "learned_at": utc_now_iso()}
        else:
            tables.pop(placer.table, None)
        if not tables:
            held.pop(game_id, None)
        FILE.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(FILE, lambda handle: json.dump(held, handle, indent=2))


def of(game_id: str, game: Any, table: str | None = None) -> dict[str, dict[str, Any]]:
    """Each window of this table's that a recording found the app did not show, with the
    reason a plan leaves its kinds for, while what placed it is unchanged."""
    with _lock:
        tables = _read().get(game_id)
    if not tables:
        return {}
    try:
        here = launch.this_devices_copy(game)
    except launch.LaunchUnavailableError:
        return {}
    placer = placing.placer(here, table)
    slot = tables.get(placer.table) if placer is not None else None
    if placer is None or not slot or slot.get("placed_by") != placer.placed_by:
        return {}
    return {str(window): {"key": placing.NOT_SHOWN_LAST,
                          "params": {"window": str(window), "app": placer.app},
                          "fix": tools.FIX_NONE, "remedy": None}
            for window in slot.get("windows") or ()}
