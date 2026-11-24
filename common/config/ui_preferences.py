"""How a user has arranged a UI, kept with the library so it follows them between
machines.

Losing this costs more than a notice shown twice: a column layout someone tuned is
worth keeping, so it is its own file with its own durability expectation, and it is not in
`revert_3x.CONFIG_FILES` - a state reset is about 3.0 data, not about throwing away
how somebody likes their tables.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from common.atomic_write import write_atomic
from common.config.paths import CONFIG_DIR

logger = logging.getLogger("vpinfe.common.config.ui_preferences")

PREFERENCES_PATH = CONFIG_DIR / "ui-preferences.json"
SCHEMA = 1


def _load() -> dict[str, Any]:
    try:
        data = json.loads(PREFERENCES_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception:
        logger.warning("Could not read %s; treating it as empty", PREFERENCES_PATH)
        return {}
    return data.get("scopes", {}) if isinstance(data, dict) else {}


def get(scope: str) -> dict[str, Any]:
    value = _load().get(scope)
    return value if isinstance(value, dict) else {}


def put(scope: str, value: dict[str, Any]) -> dict[str, Any]:
    scopes = _load()
    scopes[scope] = value
    payload = json.dumps({"schema": SCHEMA, "scopes": scopes}, indent=2)
    # Atomic: a half-written layout would be read as an empty one on next start, and
    # silently reset every table the user had arranged.
    PREFERENCES_PATH.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(PREFERENCES_PATH, lambda stream: stream.write(payload))
    return value
