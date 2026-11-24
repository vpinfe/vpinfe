"""Which Overview rows are set aside on this install, and over what.

A dismissal is what only a person can say, so it is kept in STATE_DIR and never in the
`.info`: an exported table folder must not carry somebody's dismissals with it. A row
stays away while every id it counts is in its list.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Iterable
from typing import Any

from common.atomic_write import write_atomic
from common.config.paths import STATE_DIR

logger = logging.getLogger("vpinfe.common.games.dismissals")

PATH = STATE_DIR / "dismissed.json"
SCHEMA = 1

_LOCK = threading.Lock()


def _load() -> dict[str, Any]:
    try:
        data = json.loads(PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception:
        logger.warning("Could not read %s; treating it as empty", PATH)
        return {}
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return {}
    return data


def dismissed() -> dict[str, list[str]]:
    """What each Overview row was dismissed over, by row: the ids it counted at the time.
    A row nobody dismissed is absent."""
    found = _load().get("rows") or {}
    return {str(row): [str(item) for item in (ids or [])]
            for row, ids in found.items() if ids}


def dismiss(row: str, ids: Iterable[str]) -> None:
    """Replace what a row was dismissed over. No ids takes the row out of the record,
    which is how a dismissal is undone. The file is held against another writer for the
    whole read-modify-write: the API runs its handlers on a thread pool."""
    with _LOCK:
        held = dict(_load().get("rows") or {})
        keep = list(dict.fromkeys(str(item) for item in ids))
        if keep:
            held[str(row)] = keep
        else:
            held.pop(str(row), None)
        PATH.parent.mkdir(parents=True, exist_ok=True)
        try:
            write_atomic(PATH, lambda out: json.dump({"schema": SCHEMA, "rows": held}, out,
                                                     indent=2))
        except Exception:
            logger.exception("Could not write %s", PATH)
