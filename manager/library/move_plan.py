"""What choosing tables for a game's file will do, worked out from the file move state.

The dialog asks about one or several files of a game. Each is a *slot*, and each slot is
decided on its own: `outcome` gives, for the tables chosen for it, what every table of the
game ends up with. The core makes the same decision when the move is run; this only looks
ahead so the dialog can say it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from common import service_errors
from common.games import file_move

# What a table ends up with, for one slot.
USES = "uses"
GETS_OWN = "gets_own"
GETS_COPY = "gets_copy"
REPLACES = "replaces"
LOSES = "loses"
OWN = "own"
NONE = "none"

# Why a choice cannot be saved.
NOTHING = "nothing"
NO_TABLE = "no_table"


@dataclass(frozen=True)
class Slot:
    """One kind of file of the game, with the state `file_move.state` answered for it."""

    family: str
    kind: str
    label: str
    state: dict[str, Any]

    @property
    def folder(self) -> str:
        return str(self.state.get("folder") or "")

    @property
    def has_file(self) -> bool:
        return bool(self.state.get("game"))

    @property
    def rows(self) -> list[dict[str, Any]]:
        return list(self.state.get("tables") or [])

    def row(self, table_id: str) -> dict[str, Any]:
        return next(one for one in self.rows if one["id"] == table_id)


@dataclass
class Outcome:
    """What a choice does to one slot: `ends` is each table's result, by table id."""

    changes: bool
    problem: str = ""
    stays: bool = False
    ends: dict[str, str] = field(default_factory=dict)


def uses_now(slot: Slot, row: dict[str, Any]) -> bool:
    """Whether this table reads the game's file today."""
    if not slot.has_file:
        return False
    return bool(row["folder_named"]) or (bool(slot.state.get("fallback")) and not row["own"])


def readers(slot: Slot) -> set[str]:
    """The tables that use the game's file today, apart from the one named like the folder,
    which is shown but not chosen."""
    return {one["id"] for one in slot.rows if not one["folder_named"] and uses_now(slot, one)}


def target(slot: Slot, choices: dict[str, bool | None]) -> set[str]:
    """The tables to have the file: those ticked, with an unticked-but-undecided row (None)
    left as it is."""
    chosen: set[str] = set()
    for row in slot.rows:
        if row["folder_named"]:
            continue
        pick = choices.get(row["id"])
        if pick or (pick is None and uses_now(slot, row)):
            chosen.add(row["id"])
    return chosen


def outcome(slot: Slot, chosen: Iterable[str]) -> Outcome:
    picked = set(chosen)
    rows = slot.rows
    stems = [one["stem"] for one in rows]
    own = {one["stem"]: bool(one["own"]) for one in rows}
    selected = [one["stem"] for one in rows if one["id"] in picked]
    changes = picked != readers(slot)
    try:
        plan = file_move.plan_to_tables(
            slot.family, slot.kind, slot.folder, stems, selected,
            lambda stem: own.get(stem, False))
    except service_errors.RefusedError:
        # The rows still say what they are: unchanged when the file would stay as it is,
        # losing it when no table is left to have it.
        waiting = [one for one in rows if not one["folder_named"] and not one["own"]]
        stays = file_move.has_fallback(slot.family, slot.kind) and len(stems) > 1 \
            and all(one["id"] in picked for one in waiting)
        return Outcome(changes, problem=NOTHING if stays else NO_TABLE, stays=stays,
                       ends={one["id"]: _end(slot, one, one["id"] in picked, stays, False)
                             for one in rows})
    copies = not plan.stays and len(plan.selected) > 1
    ends: dict[str, str] = {}
    for row in rows:
        ends[row["id"]] = _end(slot, row, row["id"] in picked, plan.stays, copies)
    return Outcome(changes, stays=plan.stays, ends=ends)


def _end(slot: Slot, row: dict[str, Any], chosen: bool, stays: bool, copies: bool) -> str:
    if row["folder_named"]:
        if not slot.has_file:
            return NONE
        return USES if stays else LOSES
    if chosen:
        if row["own"]:
            return REPLACES
        if stays:
            return USES
        return GETS_COPY if copies else GETS_OWN
    if row["own"]:
        return OWN
    return LOSES if uses_now(slot, row) else NONE
