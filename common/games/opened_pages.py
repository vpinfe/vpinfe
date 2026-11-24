"""The release pages somebody opened, so a file that arrives soon after can be tied to one.

Kept in the install's state, never the `.info`: a page opened is a fact about this
install's afternoon, and an exported table folder must not carry it.
"""

from __future__ import annotations

import functools
import json
import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from common import timestamps
from common.atomic_write import write_atomic
from common.config.paths import STATE_DIR

logger = logging.getLogger("vpinfe.common.games.opened_pages")

OPENED_PAGES_PATH = STATE_DIR / "opened_pages.json"
SCHEMA = 1
KEEP = 50
EXPIRES_AFTER = 24 * 3600

_LOCK = threading.Lock()


def _changes[F: Callable[..., Any]](work: F) -> F:
    @functools.wraps(work)
    def locked(*args: Any, **kwargs: Any) -> Any:
        with _LOCK:
            return work(*args, **kwargs)
    return locked  # type: ignore[return-value]


def _load() -> list[dict[str, Any]]:
    try:
        data = json.loads(OPENED_PAGES_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except Exception:
        logger.warning("Could not read %s; treating it as empty", OPENED_PAGES_PATH)
        return []
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return []
    return [one for one in data.get("opened") or [] if isinstance(one, dict)]


def _save(opened: list[dict[str, Any]]) -> None:
    OPENED_PAGES_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        write_atomic(OPENED_PAGES_PATH,
                     lambda out: json.dump({"schema": SCHEMA, "opened": opened}, out, indent=2))
    except Exception:
        logger.exception("Could not write %s", OPENED_PAGES_PATH)


def _age(one: dict[str, Any], now: float) -> float | None:
    then = timestamps.iso_to_epoch(one.get("at"))
    return None if then is None else now - then


@_changes
def note(game_id: str, kind: str, vps_file_id: str, page: str) -> None:
    """Record that this release's page was opened now. Opening it again moves it to the
    front rather than listing it twice."""
    now = time.time()
    fresh = {"game_id": str(game_id), "kind": str(kind), "vps_file_id": str(vps_file_id),
             "page": str(page), "at": timestamps.utc_now_iso()}
    held = [one for one in _load()
            if not (one.get("game_id") == fresh["game_id"]
                    and one.get("vps_file_id") == fresh["vps_file_id"])
            and (age := _age(one, now)) is not None and age < EXPIRES_AFTER]
    _save([fresh, *held][:KEEP])


def all_recent() -> list[dict[str, Any]]:
    """What was opened for any game inside the last day, newest first. Each carries `at`
    as an ISO time."""
    now = time.time()
    return [one for one in _load()
            if (age := _age(one, now)) is not None and age < EXPIRES_AFTER]


def recent(game_id: str, kind: str = "") -> list[dict[str, Any]]:
    """What was opened for this game inside the last day, newest first, optionally of one
    kind. Each carries `at` as an ISO time."""
    return [one for one in all_recent()
            if one.get("game_id") == str(game_id)
            and (not kind or one.get("kind") == str(kind))]


@_changes
def forget(game_id: str, vps_file_id: str = "") -> None:
    """Drop what was opened for a game, or one release of it once the file has arrived."""
    _save([one for one in _load()
           if not (one.get("game_id") == str(game_id)
                   and (not vps_file_id or one.get("vps_file_id") == str(vps_file_id)))])
