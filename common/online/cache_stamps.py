"""What each cache in CACHE_DIR was last told: the version it holds, when it was last asked.

One file beside the caches it describes, so emptying the folder empties the stamps and the
next start fetches again rather than trusting a file that is gone.
"""

from __future__ import annotations

import json
import logging
import threading

from common.atomic_write import write_atomic
from common.config.paths import STAMPS_PATH

logger = logging.getLogger("vpinfe.common.online.cache_stamps")

_LOCK = threading.RLock()


def _read() -> dict:
    try:
        found = json.loads(STAMPS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


def get(group: str, key: str) -> str:
    """One stamp, or "" when it was never written or the file cannot be read."""
    held = _read().get(group)
    value = held.get(key) if isinstance(held, dict) else None
    return value.strip() if isinstance(value, str) else ""


def put(group: str, **values: str) -> None:
    """Write stamps under `group`, leaving every other one as it is. A file that cannot be
    written is logged and the stamp is lost, which only asks the question again."""
    with _LOCK:
        everything = _read()
        held = everything.get(group)
        everything[group] = {**(held if isinstance(held, dict) else {}), **values}
        try:
            STAMPS_PATH.parent.mkdir(parents=True, exist_ok=True)
            write_atomic(STAMPS_PATH, lambda fh: json.dump(everything, fh, indent=2))
        except OSError:
            logger.warning("Could not write %s", STAMPS_PATH, exc_info=True)
