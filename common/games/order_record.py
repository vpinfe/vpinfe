"""The draw order this install wrote into tables' own settings, by table id.

A rank in a table's settings looks the same whoever put it there, so what was written is
kept here, in STATE_DIR: it belongs to this install's tables and never goes into the table's
folder. Only a pair still equal to its record is taken back.

A record that cannot be taken whole is never read as empty and never written over.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Iterable, Mapping
from typing import Any

from common import service_errors
from common.atomic_write import write_atomic
from common.config.paths import STATE_DIR
from common.i18n import t

logger = logging.getLogger("vpinfe.common.games.order_record")

PATH = STATE_DIR / "draw_order.json"
SCHEMA = 1

_LOCK = threading.Lock()


class Unreadable(service_errors.BlockedError):
    """The record is there and cannot be read whole: not JSON, not the shape this writes,
    or a read the system refused."""


def _unreadable() -> Unreadable:
    return Unreadable(t("error.order_record.unreadable", path=str(PATH)))


def _pair(pair: Any) -> dict[str, str]:
    if not isinstance(pair, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in pair.items()):
        raise _unreadable()
    return dict(pair)


def _load() -> dict[str, dict[str, str]]:
    try:
        data = json.loads(PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        logger.warning("Could not read %s: %s", PATH, exc)
        raise _unreadable() from exc
    held = data.get("tables") if isinstance(data, dict) and data.get("schema") == SCHEMA else None
    if not isinstance(held, dict):
        logger.warning("%s is not a record this version writes", PATH)
        raise _unreadable()
    return {str(table): found for table, pair in held.items() if (found := _pair(pair))}


def held() -> dict[str, dict[str, str]]:
    """What was written, by table id. A table nothing was written for is absent. Raises
    `Unreadable` where the file is there and cannot be read whole."""
    with _LOCK:
        return _load()


def keep(changes: Mapping[str, Mapping[str, str]]) -> None:
    """Replace the record of each table named; an empty pair takes the table out. The file
    is held against another writer for the whole read-modify-write, and is written only
    where something changes. Raises `Unreadable` rather than write over a record it could not
    read, and lets a failed write through as the OSError it was."""
    if not changes:
        return
    with _LOCK:
        record: dict[str, Any] = dict(_load())
        before = dict(record)
        for table, pair in changes.items():
            if pair:
                record[str(table)] = dict(pair)
            else:
                record.pop(str(table), None)
        if record == before:
            return
        PATH.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(PATH, lambda out: json.dump({"schema": SCHEMA, "tables": record}, out,
                                                 indent=2))


def drop(tables: Iterable[str]) -> None:
    """Take these tables out of the record. Logged and not raised."""
    try:
        keep({str(table): {} for table in tables})
    except (service_errors.ServiceError, OSError):
        logger.exception("Could not take %s out of the record of draw orders", list(tables))
