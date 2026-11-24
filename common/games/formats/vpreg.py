"""Reading a `VPReg.stg` and writing the sets in it as a `VPReg.ini`.

Visual Pinball keeps what a table saves with `SaveValue` in one registry file shared by
every table. The `.stg` is an OLE file: a storage per set (the name the table gave), a
stream per value, each stream holding the value as UTF-16. Visual Pinball reads the
`.ini` beside it first, so a game folder holding only its own sets starts a table with
what it saved and nothing of anyone else's.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

import olefile


def sets_in(path: str | Path) -> dict[str, dict[str, str]]:
    """Every set in the file, as name -> {value name: value}. Empty where it is unreadable."""
    try:
        with olefile.OleFileIO(str(path)) as ole:
            found: dict[str, dict[str, str]] = {}
            for entry in ole.listdir(streams=True, storages=False):
                if len(entry) != 2:
                    continue
                with ole.openstream(entry) as stream:
                    raw = stream.read()
                found.setdefault(entry[0], {})[entry[1]] = (
                    raw[:len(raw) // 2 * 2].decode("utf-16-le", errors="replace"))
            return found
    except (OSError, ValueError):
        return {}


def _writable(name: str, value: str) -> bool:
    """A line the `.ini` can hold: a break in a value or a stray bracket in a name would
    be read back as something else."""
    return not (any(c in value for c in "\r\n\0")
                or any(c in name for c in "=[]\r\n\0") or name.startswith(";"))


def sections_in(text: str) -> set[str]:
    """The set names an `.ini` already holds, lowercased: Visual Pinball matches them in
    any case."""
    return {one.strip().casefold() for one in re.findall(r"^\s*\[([^\]\r\n]*)\]", text, re.M)}


def ini_text(sets: dict[str, dict[str, str]], names: Iterable[str],
             skip: Iterable[str] = ()) -> str:
    """The `.ini` for the named sets, in the order asked for, leaving out the `skip`
    sets by any case. Empty where none is held."""
    chunks = []
    left_out = {one.strip().casefold() for one in skip}
    for name in dict.fromkeys(names):
        if name.strip().casefold() in left_out:
            continue
        values = {key: value for key, value in sets.get(name, {}).items()
                  if _writable(key, value)}
        if values and not any(c in name for c in "]\r\n\0"):
            chunks.append(f"[{name}]\n" + "".join(f"{k}={v}\n" for k, v in values.items()))
    return "\n".join(chunks)
