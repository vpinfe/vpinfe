"""A table's own `.info`, beside its table, once a game has two or more tables.

The game's file stays the one record everything reads. A game folder with one table has
only that file. With two or more, each table that is not named like the folder gets
`<table>.info` beside it, which VPX finds before the folder's file and writes its own section
into. This module is the storage edge for those files: `discover` and `fold` take them into the
game's record on read, and `write` splits the record back out on save.

A table's file stands on its own, so a program that reads it alone shows the right values:
the game's facts and the table's authors, the game's favorite, the table's own rating else the
game's, the game's tags plus its own, and the table's own play numbers in FileLayout.md's
units. What only VPinFE needs is in the file's `vpinfe.table`, with the table's own rating
and tags under `own`. After the split the game's manifest keeps just the id, name, `hidden`
and `absent_since` of a table that has a file.
"""

from __future__ import annotations

import copy
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from common import apps
from common.games import info_codec
from common.games.info_migration import write_json_atomic
from common.games.tables import (
    ABSENT_SINCE_KEY,
    FORM_CONTAINED,
    TABLE_FILENAME_KEY,
    TABLE_ID_KEY,
    TABLES_KEY,
    entry_filename,
    entry_form,
    parse_authors,
    table_entries,
)
from common.kept_copies import ADOPT, DELETE, SPLIT, copy_aside
from common.timestamps import epoch_to_iso

logger = logging.getLogger("vpinfe.common.games.table_files")

INFO_SUFFIX = ".info"
TABLE_SECTION = "table"
OWN_KEY = "own"
# Set on a file that arrived with an `Info` of its own, which is then left as found.
INFO_KEPT_KEY = "info_kept"
# What a table's file does not carry in its `vpinfe.table`: its name is the file's own, its
# play numbers are `User`, and the game's manifest keeps the other two.
_NOT_IN_FILE = frozenset({TABLE_FILENAME_KEY, "user", "hidden", ABSENT_SINCE_KEY})


@dataclass
class OwnFile:
    """One table's `.info` as found on disk. `stored` is None where it could not be read."""

    path: str
    stored: dict[str, Any] | None
    ours: bool


@dataclass
class Before:
    """What the game's file held before this write, as far as the split needs to know."""

    vpx: bool
    unowned: frozenset[str]
    known: frozenset[str]
    fat: frozenset[str]


def own_name(filename: str) -> str:
    return Path(filename).stem + INFO_SUFFIX


def shares_game_file(filename: str, folder_name: str) -> bool:
    """A table named like its folder reads and writes the folder's file."""
    return Path(filename).stem.lower() == folder_name.lower()


def _names(folder: str) -> dict[str, str]:
    try:
        return {name.lower(): name for name in os.listdir(folder)}
    except OSError:
        return {}


def _contained(data: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    return [(entry_filename(entry), entry) for entry in table_entries(data).values()
            if isinstance(entry, dict) and entry_form(entry) == FORM_CONTAINED
            and entry_filename(entry)]


def _is_slim(entry: dict[str, Any]) -> bool:
    return set(entry) <= set(info_codec.SLIM_KEYS)


def is_ours(stored: object) -> bool:
    """Whether a table's file carries VPinFE's record of the table."""
    section = stored.get("vpinfe") if isinstance(stored, dict) else None
    return isinstance(section, dict) and isinstance(section.get(TABLE_SECTION), dict)


def worth_looking(data: dict[str, Any]) -> bool:
    """Whether the folder could hold tables' own files: two or more tables, or an entry
    already down to its name, which stays true when the game is back to one."""
    found = _contained(data)
    return len(found) >= 2 or any(_is_slim(entry) for _name, entry in found)


def _read(path: str) -> OwnFile:
    from common.games.info_file import InvalidMetaConfigError, read_record

    try:
        _text, stored = read_record(path)
    except (InvalidMetaConfigError, OSError, ValueError):
        logger.warning("Could not read the table file %s; it is left as it is", path)
        return OwnFile(path, None, False)
    return OwnFile(path, stored, is_ours(stored))


def discover(info_path: str, data: dict[str, Any]) -> dict[str, OwnFile]:
    """The tables' own files in this game's folder, by lower-cased table file name."""
    if not worth_looking(data):
        return {}
    folder = os.path.dirname(info_path)
    names = _names(folder)
    folder_name = os.path.basename(folder)
    found: dict[str, OwnFile] = {}
    for filename, _entry in _contained(data):
        if shares_game_file(filename, folder_name):
            continue
        actual = names.get(own_name(filename).lower())
        if actual:
            found[filename.lower()] = _read(os.path.join(folder, actual))
    if len(_contained(data)) < 2:
        return {key: own for key, own in found.items() if own.ours}
    return found


def snapshot(data: dict[str, Any], info_path: str, registry: dict[str, OwnFile],
             stored_vpx: bool, slim: frozenset[str] = frozenset()) -> Before:
    """What the game's file holds: taken before `fold` fills the entries, or after a write
    with the names `write` left slim."""
    folder_name = os.path.basename(os.path.dirname(info_path))
    entries = _contained(data)
    known = frozenset(name.lower() for name, _ in entries)
    unowned = frozenset(name.lower() for name, _ in entries
                        if name.lower() not in registry
                        and not shares_game_file(name, folder_name))
    fat = frozenset(name.lower() for name, entry in entries
                    if not _is_slim(entry) and name.lower() not in slim)
    return Before(stored_vpx, unowned, known, fat)


def _plays(user: object) -> dict[str, int | None]:
    held = user if isinstance(user, dict) else {}
    counted = held.get("StartCount", held.get("RunCount"))
    last = held.get("LastRun")
    epoch = info_codec.epoch_of(last) if isinstance(last, str) else None
    if epoch is None and not isinstance(last, str | bool):
        found = info_codec.number(last)
        epoch = int(found) if found else None
    seconds = info_codec.number(held.get("RunTime"))
    count = info_codec.number(counted)
    return {"last_run": epoch or None,
            "start_count": max(0, int(count)) if count is not None else 0,
            "run_time_seconds": max(0, int(seconds)) if seconds is not None else 0}


def _later(one: int | None, other: int | None) -> int | None:
    return max((value for value in (one, other) if value), default=None)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _game_user(data: dict[str, Any]) -> dict[str, Any]:
    user = data.get("User")
    if not isinstance(user, dict):
        user = {}
        data["User"] = user
    return user


def _tags(value: object) -> list[str]:
    return [str(one) for one in value if str(one).strip()] if isinstance(value, list) else []


def _joined(first: list[str], second: list[str]) -> list[str]:
    return list(dict.fromkeys([*first, *second]))


def _fold_one(data: dict[str, Any], entry: dict[str, Any], own: OwnFile,
              favorite_was: bool | None = None) -> None:
    """`favorite_was` is the game's favorite before any table's file was read, so that a
    change in one file is not undone by the next file, which still says the old value."""
    stored = own.stored
    if stored is None:
        return
    held = entry.setdefault("user", {})
    plays = _plays(stored.get("User"))
    if not own.ours:
        # A file VPinFE did not write: its numbers join ours, by the larger count and time
        # and the later date, so reading it twice never counts a play twice.
        if plays["start_count"] or plays["run_time_seconds"] or plays["last_run"]:
            held["start_count"] = max(int(held.get("start_count") or 0), plays["start_count"] or 0)
            held["run_time_seconds"] = max(int(held.get("run_time_seconds") or 0),
                                           plays["run_time_seconds"] or 0)
            held["last_run"] = _later(held.get("last_run"), plays["last_run"])
        file_user = _dict(stored.get("User"))
        file_rating = info_codec.half_stars(file_user.get("Rating"))
        if file_rating and file_rating != (held.get("rating") or _game_user(data).get("Rating")):
            held["rating"] = file_rating
        if not held:
            entry.pop("user", None)
        return

    record = stored["vpinfe"][TABLE_SECTION]
    for key, value in record.items():
        if key not in (OWN_KEY, TABLE_ID_KEY) and key not in _NOT_IN_FILE:
            entry[key] = copy.deepcopy(value)
    held.update(plays)
    chosen = _dict(record.get(OWN_KEY))
    own_rating = info_codec.half_stars(chosen.get("rating"))
    own_tags = _tags(chosen.get("tags"))

    # What another program changed in the file is taken in: a rating that is not what we
    # would have written is the table's own, a changed favorite is the game's, and a tag
    # the file has that the game and the table do not is the table's.
    game = _game_user(data)
    file_user = _dict(stored.get("User"))
    file_rating = info_codec.half_stars(file_user.get("Rating"))
    if file_rating and file_rating != (own_rating or game.get("Rating") or 0):
        own_rating = file_rating
    if "Favorite" in file_user:
        wanted = str(file_user["Favorite"]).strip().lower() in ("1", "true")
        if wanted != (bool(game.get("Favorite")) if favorite_was is None else favorite_was):
            game["Favorite"] = wanted
    for tag in _tags(file_user.get("Tags")):
        if tag not in _tags(game.get("Tags")) and tag not in own_tags:
            own_tags.append(tag)
    if own_rating:
        held["rating"] = own_rating
    else:
        held.pop("rating", None)
    held["tags"] = own_tags
    _tidy(entry)


def _tidy(entry: dict[str, Any]) -> None:
    """A table with nothing recorded has no `user` at all, as before its file was read."""
    if not any(_dict(entry.get("user")).values()):
        entry.pop("user", None)


def fold(data: dict[str, Any], registry: dict[str, OwnFile]) -> None:
    """Take the tables' own files into the game's record, in place."""
    if not registry:
        return
    data[TABLES_KEY] = table_entries(data)
    was = bool(_game_user(data).get("Favorite"))
    for filename, entry in _contained(data):
        own = registry.get(filename.lower())
        if own is not None:
            _fold_one(data, entry, own, was)


def seed(info_path: str) -> dict[str, Any]:
    """A record for a folder that has table files and no file of its own for the game.

    The game's facts, rating, favorite and tags come from the first table's file that has
    them; each table that has a file of its own is listed, to be filled in by `fold`.
    """
    folder = os.path.dirname(info_path)
    names = _names(folder)
    folder_name = os.path.basename(folder)
    tables = sorted(name for name in names.values()
                    if apps.app_for(name) is not None
                    and own_name(name).lower() in names
                    and not shares_game_file(name, folder_name))
    if not tables:
        return {}
    data: dict[str, Any] = {"vpinfe": {}, TABLES_KEY: {}}
    for name in tables:
        entry: dict[str, Any] = {TABLE_FILENAME_KEY: name}
        found = _read(os.path.join(folder, names[own_name(name).lower()]))
        stored = found.stored or {}
        record = (stored.get("vpinfe") or {}).get(TABLE_SECTION) if found.ours else None
        if isinstance(record, dict) and record.get(TABLE_ID_KEY):
            entry[TABLE_ID_KEY] = record[TABLE_ID_KEY]
        data[TABLES_KEY][str(entry.get(TABLE_ID_KEY) or name)] = entry
        info = stored.get("Info")
        if isinstance(info, dict) and "Info" not in data:
            data["Info"] = {k: v for k, v in info.items() if k != "Authors"}
        user = stored.get("User")
        if isinstance(user, dict) and "User" not in data:
            data["User"] = {"Rating": info_codec.half_stars(user.get("Rating")),
                            "Favorite": str(user.get("Favorite", "")).strip().lower()
                            in ("1", "true"),
                            "Tags": _tags(user.get("Tags"))}
    return data


@dataclass
class Written:
    slim: frozenset[str]
    registry: dict[str, OwnFile]


def _vpx_target(data: dict[str, Any], before: Before, owners: list[str]) -> str:
    """The table a game's `VPX` section goes to, or "" where it cannot be told apart.

    The section describes the one table the game's file held when VPX wrote it. A game's file
    that listed two or more tables gives a mix, which only the backup keeps; a section that
    appears in a game already split belongs to the one table that has no file yet.
    """
    if len(before.unowned) == 1:
        only = next(iter(before.unowned))
        return only if only in owners else ""
    if not before.unowned:
        fresh = [name for name in owners if name not in before.known]
        return fresh[0] if len(fresh) == 1 else ""
    return ""


def write(info_path: str, data: dict[str, Any], registry: dict[str, OwnFile],
          before: Before, *, converting: bool) -> Written:
    """Write each table's own file, back up the game's file if it is about to lose records,
    and say which tables the game's file now holds only by name.

    Run before the game's file is written. Writing again changes nothing, so a stop between
    the two is finished by the next write.
    """
    folder = os.path.dirname(info_path)
    folder_name = os.path.basename(folder)
    names = _names(folder)
    data[TABLES_KEY] = table_entries(data)
    contained = _contained(data)
    splitting = len(contained) >= 2
    shared = any(shares_game_file(name, folder_name) for name, _ in contained)

    strays = _strays(registry, contained)
    followed: set[str] = set()
    owners: list[tuple[str, dict[str, Any], OwnFile | None]] = []
    for filename, entry in contained:
        if shares_game_file(filename, folder_name):
            continue
        key = filename.lower()
        actual = names.get(own_name(filename).lower())
        followed_from = strays.get(str(entry.get(TABLE_ID_KEY) or ""))
        if key in registry:
            owners.append((filename, entry, registry[key]))
        elif actual:
            late = _read(os.path.join(folder, actual))
            if splitting or late.ours:
                _fold_one(data, entry, late)
                owners.append((filename, entry, late))
        elif followed_from is not None:
            followed.add(followed_from.path)
            owners.append((filename, entry, OwnFile(os.path.join(folder, own_name(filename)),
                                                    followed_from.stored, followed_from.ours)))
        elif splitting and filename.lower() in names:
            owners.append((filename, entry, None))
    if not owners:
        _retire(strays, followed)
        return Written(frozenset(), {})

    if not splitting:
        _fold_back(data, [entry for _, entry, _ in owners])

    keys = [name.lower() for name, _, _ in owners]
    moved = None
    drops_vpx = "VPX" in data and not shared
    if drops_vpx:
        target = _vpx_target(data, before, keys)
        moved = (target, copy.deepcopy(data["VPX"])) if target else None

    written: dict[str, OwnFile] = {}
    for filename, entry, existing in owners:
        key = filename.lower()
        if existing is not None and existing.stored is None and os.path.exists(existing.path):
            written[key] = existing
            continue
        vpx = moved[1] if moved and moved[0] == key else None
        body = _build(entry, data, existing, vpx)
        path = existing.path if existing else os.path.join(folder, own_name(filename))
        if existing is None or existing.stored != body or not os.path.exists(path):
            if existing is not None and not existing.ours and os.path.exists(path):
                copy_aside(path, ADOPT)
            write_json_atomic(path, body)
        written[key] = OwnFile(path, body, True)

    _retire(strays, followed)
    losing = drops_vpx or bool(before.fat & set(keys))
    if losing and not converting and os.path.exists(info_path):
        copy_aside(info_path, SPLIT)
    if drops_vpx:
        data.pop("VPX", None)
    return Written(frozenset(keys), written)


def _strays(registry: dict[str, OwnFile],
            contained: list[tuple[str, dict[str, Any]]]) -> dict[str, OwnFile]:
    """The files found for a table that no longer has an entry under that file name, by the
    id its own file records: a renamed or swapped table's file, or a dropped table's."""
    current = {name.lower() for name, _ in contained}
    found: dict[str, OwnFile] = {}
    for key, own in registry.items():
        if key in current:
            continue
        section = _dict(_dict(own.stored).get("vpinfe")).get(TABLE_SECTION)
        table_id = str(_dict(section).get(TABLE_ID_KEY) or "")
        found[table_id or f"\0{key}"] = own
    return found


def _retire(strays: dict[str, OwnFile], followed: set[str]) -> None:
    """Remove the files of tables that are gone, after a copy. A file its table's new name
    has taken over is removed without one, unless it was not ours."""
    for own in strays.values():
        if not os.path.exists(own.path):
            continue
        if own.path not in followed or not own.ours:
            copy_aside(own.path, DELETE)
        os.unlink(own.path)


def _fold_back(data: dict[str, Any], entries: list[dict[str, Any]]) -> None:
    """A game back to one table: the table that remains keeps its file, and its own rating
    and tags become the game's."""
    game = _game_user(data)
    for entry in entries:
        held = entry.get("user")
        if not isinstance(held, dict):
            continue
        if held.get("rating"):
            game["Rating"] = held.pop("rating")
        if held.get("tags"):
            game["Tags"] = _joined(_tags(game.get("Tags")), _tags(held["tags"]))
            held["tags"] = []


def _build(entry: dict[str, Any], data: dict[str, Any], existing: OwnFile | None,
           vpx: dict[str, Any] | None) -> dict[str, Any]:
    stored = existing.stored if existing and existing.stored else {}
    out = {key: copy.deepcopy(value) for key, value in stored.items()
           if key not in ("Info", "User", "vpinfe")}
    if vpx is not None and "VPX" not in out:
        out["VPX"] = vpx
    section = _dict(stored.get("vpinfe"))

    found_info = stored.get("Info")
    kept = bool(section.get(INFO_KEPT_KEY)) or (
        existing is not None and not existing.ours and isinstance(found_info, dict)
        and bool(found_info))
    game_info = _dict(data.get("Info"))
    info = (copy.deepcopy(found_info) if kept and isinstance(found_info, dict)
            else {**game_info, "Authors": parse_authors(entry.get("authors"))})
    out = {"Info": info, **out}

    game = _dict(data.get("User"))
    held = _dict(entry.get("user"))
    own_rating = held.get("rating") or 0
    tags = _joined(_tags(game.get("Tags")), _tags(held.get("tags")))
    user = dict(_dict(stored.get("User")))
    user.update({
        "Rating": info_codec.half_stars(own_rating or game.get("Rating")),
        "Favorite": 1 if game.get("Favorite") in (True, 1, "1", "true", "True") else 0,
        "Tags": tags,
        "LastRun": (epoch_to_iso(held["last_run"]) or None) if held.get("last_run") else None,
        "StartCount": int(held.get("start_count") or 0),
        "RunTime": int(held.get("run_time_seconds") or 0),
    })
    out["User"] = user

    vpinfe = {key: copy.deepcopy(value) for key, value in section.items()
              if key not in (TABLE_SECTION, "schema")}
    vpinfe = {"schema": info_codec.SCHEMA, **vpinfe}
    if kept:
        vpinfe[INFO_KEPT_KEY] = True
    record = {key: copy.deepcopy(value) for key, value in entry.items()
              if key not in _NOT_IN_FILE}
    chosen: dict[str, Any] = {}
    if own_rating:
        chosen["rating"] = info_codec.half_stars(own_rating)
    if _tags(held.get("tags")):
        chosen["tags"] = _tags(held["tags"])
    if chosen:
        record[OWN_KEY] = chosen
    vpinfe[TABLE_SECTION] = record
    out["vpinfe"] = vpinfe
    return out


def due(config: dict[str, Any], folder_name: str, on_disk: list[str],
        info_names: frozenset[str] | None, has_game_file: bool) -> bool:
    """Whether a scan of this game should write its file so the split happens: a game with
    two or more tables where a table that is not named like the folder has no file yet, or a
    folder holding table files and none for the game."""
    if info_names is None:
        return False
    tables = [name for name, _ in _contained(config)]
    separate = [name for name in on_disk if not shares_game_file(name, folder_name)]
    if not has_game_file:
        return any(own_name(name).lower() in info_names for name in separate)
    if len(tables) < 2:
        return False
    return any(own_name(name).lower() not in info_names for name in separate)
