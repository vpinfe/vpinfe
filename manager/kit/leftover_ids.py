"""Which of the settings an upgrade left behind a person can act on, and the ids a
dismissal of them is recorded against. The page that offers the move and Overview's row
count the same ones for the row's record. The notice keeps a record of its own over every
setting it lists, the ones only named included, because Overview forgets any id its row does
not count."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from common.apps.contract import MOVE, PLACE, TURN_ON

# The Overview row's record, and the notice's.
ROW = "leftovers"
NOTICE = "leftovers_notice"


def acts(item: Mapping[str, Any]) -> bool:
    """Whether the move or Turn On does something for this row. A box with no file to go
    into, and a setting that is only named, do not."""
    action = item.get("action")
    return action in (MOVE, TURN_ON) or (action == PLACE and int(item.get("files") or 0) > 0)


def of(launcher_id: str, items: Iterable[Mapping[str, Any]]) -> tuple[str, ...]:
    """`<launcher>:<setting>` for each row to act on."""
    return tuple(f"{launcher_id}:{one['key']}" for one in items if acts(one))


def every(launcher_id: str, items: Iterable[Mapping[str, Any]]) -> tuple[str, ...]:
    """`<launcher>:<setting>` for each row the notice lists, the ones only named included."""
    return tuple(f"{launcher_id}:{one['key']}" for one in items)


def scope(launcher_id: str) -> str:
    """What every id of one launcher starts with."""
    return f"{launcher_id}:"
