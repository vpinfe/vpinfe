"""A machine's high score table, as VPinFE last read it off the machine.

The `.info` keeps one record per ROM under `User.HighScores`. The ROM is the machine: two
tables on one ROM share its table, and a mod on a ROM of its own keeps its own.

A record is `{read_at, score_kind, entries | value, new}`: the reading as
`score_parser.result_to_jsonable` gives it, without its ROM (the key says it), when it was
read, and `new` - the positions of the entries the last game put there. A reading from a
text file also has `file`, its name, and `tables`, the ids of the tables with no ROM of
their own that it was read for, since nothing on such a table says which file is theirs.

Imports nothing beyond the standard library: the score parser, the metadata module and the
migration all read it.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

KEY = "HighScores"
# The 2.x key, one reading per game, which the migration moves into `KEY`.
LEGACY_KEY = "Score"

# What a surface can say about a ROM's high scores.
READ = "read"
NONE_YET = "none"
UNSUPPORTED = "unsupported"
UNREADABLE = "unreadable"


def rom_of(entry: Any) -> str:
    """The ROM a table declares, or ""."""
    return str((entry or {}).get("rom", "") or "").strip() if isinstance(entry, dict) else ""


def kept(meta: Any, rom: str) -> dict[str, Any] | None:
    """What is kept for `rom`, or None."""
    user = meta.get("User") if isinstance(meta, dict) else None
    held = user.get(KEY) if isinstance(user, dict) else None
    record = held.get(rom) if isinstance(held, dict) and rom else None
    return record if isinstance(record, dict) else None


def as_reading(record: dict[str, Any] | None) -> dict[str, Any] | None:
    """A kept record as a reading to compare the next one against. None where nobody
    knows when it was read, since nothing can be new against that."""
    if not record or not record.get("read_at"):
        return None
    if "value" in record:
        return {"value": record["value"]}
    return {"entries": list(record.get("entries") or [])}


def new_positions(before: dict | None, after: dict | None) -> list[int]:
    """Where on `after` the entries are that `before` did not hold.

    Compared on everything but rank and section, as many times as each is held, so a
    score that moved down a place is not new and a second identical one is. No reading
    before, or two readings of different kinds, is nothing new: a first game's factory
    table credits nobody.
    """
    if not before or not after or ("value" in before) != ("value" in after):
        return []
    if "value" in after:
        return [] if after["value"] == before["value"] else [0]
    held = Counter(_entry_key(entry) for entry in before.get("entries") or [])
    found = []
    for position, entry in enumerate(after.get("entries") or []):
        key = _entry_key(entry)
        if held[key]:
            held[key] -= 1
        else:
            found.append(position)
    return found


def _entry_key(entry: dict) -> str:
    return json.dumps({key: value for key, value in entry.items()
                       if key not in ("rank", "section")}, sort_keys=True)


def record(reading: dict[str, Any], before: dict | None, read_at: str | None) -> dict[str, Any]:
    """What is kept of `reading`. `before` is the reading the new entries are counted
    against."""
    held: dict[str, Any] = {"read_at": read_at,
                            "score_kind": str(reading.get("score_kind") or "")}
    if reading.get("file"):
        held["file"] = str(reading["file"])
    if "value" in reading:
        held["value"] = reading["value"]
        after: dict[str, Any] = {"value": reading["value"]}
    else:
        held["entries"] = [dict(entry) for entry in reading.get("entries") or []
                           if isinstance(entry, dict)]
        after = {"entries": held["entries"]}
    held["new"] = new_positions(before, after)
    return held


def keep(config: dict[str, Any], rom: str, held: dict[str, Any], *, table_id: str = "") -> None:
    """Keep `held` under `rom`. A table with no ROM names itself in `table_id`, and joins
    the tables the record was already kept for."""
    user = config.get("User")
    if not isinstance(user, dict):
        user = config["User"] = {}
    records = user.get(KEY)
    if not isinstance(records, dict):
        records = user[KEY] = {}
    tables = tables_of(records.get(rom))
    if table_id and table_id not in tables:
        tables.append(table_id)
    if tables:
        held = {**held, "tables": tables}
    records[rom] = held


def tables_of(record: Any) -> list[str]:
    """The ids of the ROM-less tables `record` was kept for."""
    named = record.get("tables") if isinstance(record, dict) else None
    return [str(one) for one in named] if isinstance(named, list) else []


def from_2x(user: dict[str, Any]) -> None:
    """Move 2.x's `User.Score` into the ROM it names. A reading that names no ROM, or
    holds nothing this can show, is left where it is rather than lost."""
    score = user.get(LEGACY_KEY)
    if not isinstance(score, dict):
        return
    rom = str(score.get("rom") or "").strip()
    entries = score.get("entries")
    if not rom or not (isinstance(entries, list) or _is_number(score.get("value"))):
        return
    reading = {"score_kind": score.get("score_kind") or score.get("score_type") or ""}
    reading.update({"value": score["value"]} if "value" in score else {"entries": entries})
    keep({"User": user}, rom, record(reading, None, None))
    del user[LEGACY_KEY]


def to_2x(user: dict[str, Any], rom: str) -> dict[str, Any]:
    """`User` as 2.x wrote it: `rom`'s record as `Score`, and no `HighScores`."""
    held = kept({"User": user}, rom)
    legacy = {key: value for key, value in user.items() if key != KEY}
    if held is not None:
        legacy[LEGACY_KEY] = {"rom": rom, "score_type": held.get("score_kind") or "",
                              **({"value": held["value"]} if "value" in held
                                 else {"entries": list(held.get("entries") or [])})}
    return legacy


def _is_number(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def view(meta: Any, entry: Any, *, table_id: str) -> dict[str, Any] | None:
    """The high scores of the table `entry`, as the wire carries them, or None where
    nothing has been read for its ROM."""
    rom = rom_of(entry)
    if not rom:
        rom = kept_for_table(meta, table_id)
    held = kept(meta, rom)
    return shown(rom, table_id, held) if held is not None else None


def kept_for_table(meta: Any, table_id: str) -> str:
    """The name of the score file kept for a table with no ROM, or ""."""
    user = meta.get("User") if isinstance(meta, dict) else None
    held = user.get(KEY) if isinstance(user, dict) else None
    if not table_id or not isinstance(held, dict):
        return ""
    return next((name for name, record in held.items() if table_id in tables_of(record)), "")


def shown(rom: str, table_id: str, held: dict[str, Any] | None, *, state: str = READ,
          reason: str = "") -> dict[str, Any]:
    """The wire's shape. `held` is None for a state with nothing read to show."""
    return {"rom": rom, "table_id": table_id,
            "read_at": (held or {}).get("read_at") or None, "state": state,
            "reason": reason, "file": str((held or {}).get("file") or ""),
            "sections": sections(held) if held else []}


def sections(held: dict[str, Any]) -> list[dict[str, Any]]:
    """The entries grouped by the machine's own sections, in the machine's order.

    A one-number machine is one section named by the map's word for it.
    """
    new = set(held.get("new") or [])
    if "value" in held:
        return [{"name": str(held.get("score_kind") or ""),
                 "entries": [shown_entry({"score": held["value"]}, 0 in new)]}]
    grouped: list[dict[str, Any]] = []
    for position, entry in enumerate(held.get("entries") or []):
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("section") or "")
        if not grouped or grouped[-1]["name"] != name:
            grouped.append({"name": name, "entries": []})
        grouped[-1]["entries"].append(shown_entry(entry, position in new))
    return grouped


def shown_entry(entry: dict[str, Any], new: bool = False) -> dict[str, Any]:
    """One entry as the wire carries it. `prefix` and `suffix` carry their own spacing, so
    `text` is exactly the three joined; `text` alone is the entry's lines where it holds
    no number."""
    rank = entry.get("rank")
    score = entry.get("score") if _is_number(entry.get("score")) else None
    prefix = str(entry.get("value_prefix") or "")
    suffix = str(entry.get("value_suffix") or "")
    if suffix and not suffix.startswith("-"):
        suffix = f" {suffix}"
    if score is None:
        text = "\n".join(str(line) for line in entry.get("extra_lines") or [])
    else:
        digits = format(score, "X") if entry.get("value_format") == "hex" else f"{score:,}"
        text = f"{prefix}{digits}{suffix}"
    return {"rank": rank if _is_number(rank) else None,
            "initials": str(entry.get("initials") or ""), "score": score,
            "prefix": prefix, "suffix": suffix, "text": text, "new": new}
