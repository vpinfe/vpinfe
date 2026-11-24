"""Tables: the launchable artifacts inside a game folder.

A folder can hold several of them, so everything asks here which one is the default.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path

from common import apps
from common.games import collation
from common.timestamps import epoch_to_iso, iso_from_asctime, iso_from_authored_date

# One entry per .vpx, keyed by the table's id so a rename rewrites one field:
#
#   "tables": {
#     "9kRm2QvT8x": {"filename": "Table (VR Room).vpx", "version": "1.2", ...}
#   }
#
# A missing entry means a table nothing has parsed, and a missing `hidden` means visible.
TABLES_KEY = "tables"

# When the file behind an entry was first not found. Absent while the file is there.
# A timestamp rather than a bare flag because "gone for thirty seconds" and "gone for a
# week" are the same fact to a flag and different answers to a person - the first is
# almost always a share that has not mounted yet. Written by discovery, read by anyone
# deciding whether a table can be offered or acted on.
ABSENT_SINCE_KEY = "absent_since"

# When the library first had this table: the moment a person or an extension added it, or
# the file's own dates for one found on disk.
ADDED_KEY = "added"

# Minted on the first rebuild that sees the file, and outlives its name - see
# `adopted_entry`, which is what carries it across a rename.
TABLE_ID_KEY = "id"
TABLE_FILENAME_KEY = "filename"

# Which table is the default, kept in the vpinfe section because it is a game-level
# choice rather than something a table says about itself.
DEFAULT_TABLE_KEY = "default_table"

# What an entry with no file of its own is: the app that plays it, and the name that app
# knows it by. Pinball FX's table id, MAME's rom name. We never look the key up - the
# whole point of a key rather than a path is that the app already does that better, and
# pointing at the file would break the moment somebody reorganized their rompath.
TABLE_APP_KEY = "app"
TABLE_KEY_KEY = "key"

# A game file that is not in this folder. Held with forward slashes whatever wrote it,
# because `os.path.relpath` answers in the host's separator and the same library would
# otherwise read one way on Linux and another on Windows.
TABLE_PATH_KEY = "path"

# How an entry is shaped. Read off what the record holds, never stored: a mode field is
# one more thing that can disagree with reality, and this one cannot.
FORM_CONTAINED = "contained"
FORM_REFERENCED = "referenced"
FORM_KEYED = "keyed"

# What a tables entry takes from a parse, in the parser's own names. The .vpx's
# manufacturer/year/type can disagree with what VPS says in Info; both are kept.
PARSED_KEYS = (
    "file_hash", "vbs_hash", "version", "release_date", "save_date", "save_rev",
    "rom", "manufacturer", "year", "type",
)

DETECT_KEYS = (
    "detect_nfozzy", "detect_fleep", "detect_ssf", "detect_lut",
    "detect_scorbit", "detect_fastflips", "detect_flex", "detect_pinmame",
)


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "on")
    return value == 1


def parse_authors(value: object) -> list[str]:
    """Authors as the .vpx records them. Per table, never rolled up to the game."""
    if not value:
        return []
    if isinstance(value, list):
        return [str(a).strip() for a in value if str(a).strip()]
    return [a.strip() for a in str(value).split(",") if a.strip()]


def entry_from_parsed(parsed: dict | None) -> dict:
    """A tables entry from one VPXParser result.

    A failed parse resolves to empty values rather than borrowing from elsewhere in
    the .info: a half-filled entry reads as fact and isn't.
    """
    parsed = parsed if isinstance(parsed, dict) else {}
    entry = {key: parsed.get(key, "") or "" for key in PARSED_KEYS}
    # Dates are normalized on the way in, so the stored value sorts and filters. The
    # author's raw string is recoverable by re-parsing the .vpx beside the .info.
    entry["release_date"] = iso_from_authored_date(entry["release_date"])
    entry["save_date"] = iso_from_asctime(entry["save_date"])
    entry["authors"] = parse_authors(parsed.get("author_name", ""))
    for key in DETECT_KEYS:
        entry[key] = _as_bool(parsed.get(key, False))
    return entry


def table_id(entry: dict | None) -> str:
    """An entry's id, or "" if it predates ids. Never mints."""
    if not isinstance(entry, dict):
        return ""
    return str(entry.get(TABLE_ID_KEY, "") or "").strip()


def adopted_entry(entries: dict, filename: str, file_hash: str,
                  seen: Iterable[str]) -> dict | None:
    """The prior entry this file should keep, matched by content when it was renamed.

    The hash says it is the same file; its recorded name being absent from this scan says
    it moved rather than that a copy was made beside it.
    """
    _, direct = entry_for_filename(entries, filename)
    if direct:
        return direct
    if not file_hash:
        return None

    seen = set(seen)
    for entry in entries.values():
        if not isinstance(entry, dict) or entry_filename(entry) in seen:
            continue
        if entry.get("file_hash") == file_hash:
            return entry
    return None


def is_parsed(entry: dict | None) -> bool:
    """Whether an entry describes a table we have read, rather than one we only
    recorded something about - hidden, or where it came from. Reading those as parsed
    answers "no rom declared" for a file nothing has opened.
    """
    if not isinstance(entry, dict):
        return False
    return any(key in entry for key in PARSED_KEYS)


def table_names(names: Iterable[str]) -> list[str]:
    """The tables in a folder listing, sorted case-insensitively."""
    known = apps.table_suffixes()
    return sorted((n for n in names if n.lower().endswith(known)), key=str.lower)


def is_game_folder(path: str | Path) -> bool:
    """Whether this folder is itself a game, rather than a folder of them.

    The same rule the scan applies to every child folder it walks: a table file, or the
    record named after the folder. Either alone is enough - a record outlives the file
    it described, and discovery writes one for a folder that arrived holding only a
    table.
    """
    from common.games import info_files

    here = Path(path).expanduser()
    try:
        with os.scandir(here) as found:
            names = [one.name for one in found if not one.is_dir()]
    except OSError:
        return False
    return bool(table_names(names)) or info_files.info_name(here.name) in names


def survey(path: str | Path) -> dict[str, list[str]]:
    """What a folder holds: `game_folders`, the child folders `is_game_folder` calls
    games, and `loose`, the table files lying in it. Both sorted case-insensitively.

    Anything whose name starts with a dot is skipped: a folder, as the scan skips it, and
    a file, because macOS writes an AppleDouble `._` file beside each file on a volume
    that cannot hold its metadata, and that is not a table. An unreadable folder holds
    nothing.
    """
    here = Path(path).expanduser()
    folders: list[str] = []
    files: list[str] = []
    try:
        with os.scandir(here) as found:
            for one in found:
                if one.name.startswith("."):
                    continue
                if one.is_dir():
                    if is_game_folder(one.path):
                        folders.append(one.name)
                else:
                    files.append(one.name)
    except OSError:
        return {"game_folders": [], "loose": []}
    return {"game_folders": sorted(folders, key=str.lower), "loose": table_names(files)}


def entry_filename(entry: dict | None) -> str:
    """The file an entry describes, or ""."""
    if not isinstance(entry, dict):
        return ""
    return str(entry.get(TABLE_FILENAME_KEY, "") or "").strip()


def entry_key(entry: dict | None) -> str:
    """The name this entry's app knows it by, or "" for one that names a file."""
    if not isinstance(entry, dict):
        return ""
    return str(entry.get(TABLE_KEY_KEY, "") or "").strip()


def entry_app(entry: dict | None) -> str:
    """The app an entry declares, or "". Only a keyed entry declares one - a file says
    which app plays it by its own suffix, and a second answer could contradict it."""
    if not isinstance(entry, dict):
        return ""
    return str(entry.get(TABLE_APP_KEY, "") or "").strip()


def app_of(entry: dict) -> str:
    """Which app plays an entry, or "".

    A file says so by its own suffix, wherever that file is - a reference names one, so
    it answers the same way a table in the folder does. Something with no file at all
    has to declare it, because nothing about a key says whose it is.
    """
    declared = entry_app(entry)
    if declared:
        return declared
    # The reference first where there is one: it is what the entry actually plays, and
    # a stray `filename` beside it would answer for a file this folder does not have.
    reference = entry_reference(entry)
    named = os.path.basename(reference) if reference else entry_filename(entry)
    found = apps.app_for(named)
    return found.id if found is not None else ""


def entry_reference(entry: dict | None) -> str:
    """Where an entry's game file is, when it is not in this folder. As stored: relative
    to the game folder, or absolute, and forward-slashed either way."""
    if not isinstance(entry, dict):
        return ""
    return str(entry.get(TABLE_PATH_KEY, "") or "").strip()


def entry_form(entry: dict | None) -> str:
    """Contained, referenced or keyed. Derived, so it can never disagree with the
    record - which is also why they are checked in the order of what is most specific:
    a key means there is no file at all, and a path means the file is elsewhere."""
    if entry_key(entry):
        return FORM_KEYED
    return FORM_REFERENCED if entry_reference(entry) else FORM_CONTAINED


def entry_native_key(entry: dict | None) -> str:
    """What this entry is reconciled and looked up by within its game.

    A filename for something in the folder, the stored path for something elsewhere, and
    `app:key` for something no file describes. One string whichever it is, because every
    caller wants "which entry is this" and none of them wants to ask twice.
    """
    key = entry_key(entry)
    if key:
        return f"{entry_app(entry)}:{key}"
    return entry_reference(entry) or entry_filename(entry)


def stored_reference(path: str) -> str:
    """A path as the record holds it: forward slashes, whatever wrote it.

    `os.path.relpath` answers in the host's separator, and a library written on Windows
    and read on Linux would otherwise disagree with itself about where a file is.
    """
    return str(path or "").strip().replace("\\", "/")


def resolved_reference(game_dir: str, stored: str) -> str:
    """The absolute path a reference points at, or "".

    Relative is anchored on the game folder, because that is what lets a library be
    moved or shared as one piece and still resolve. An absolute reference is returned
    as it stands and survives the game folder moving, but not the library going
    anywhere else.
    """
    said = stored_reference(stored)
    if not said:
        return ""
    found = Path(said)
    if found.is_absolute():
        return os.path.normpath(str(found))
    # Lexically, not `resolve()`: a share that is not mounted is exactly the case this
    # has to answer for, and resolving would go to the filesystem to do it.
    return os.path.normpath(str(Path(str(game_dir or "")).joinpath(found)))


def keyed_entry(app: str, key: str) -> dict:
    """A new entry for something with no file. The minting pass gives it an id."""
    return {TABLE_APP_KEY: str(app or "").strip(),
            TABLE_KEY_KEY: str(key or "").strip()}


def referenced_entry(path: str) -> dict:
    """A new entry for a game file that lives somewhere else."""
    return {TABLE_PATH_KEY: stored_reference(path)}


def contained_entry(filename: str) -> dict:
    """A new entry for a game file in this folder."""
    return {TABLE_FILENAME_KEY: str(filename or "").strip()}


def entry_for_filename(entries: dict | None, filename: str) -> tuple[str, dict]:
    """(id, entry) for the table with this filename, or ("", {}). Callers arrive holding
    a name off a directory listing; the storage is keyed by id."""
    for found_id, entry in (entries or {}).items():
        if entry_filename(entry) == filename:
            return found_id, entry
    return "", {}


def table_filenames(entries: dict | None) -> list[str]:
    """Every .vpx the entries describe. What `default_table` and the disk both speak."""
    return [n for n in (entry_filename(e) for e in (entries or {}).values()) if n]


def rekey_by_id(entries: dict | None) -> dict:
    """The tables map keyed by id, converting the filename-keyed shape on the way.

    An entry that says nothing about what it is predates the re-key, and its map key is
    the filename it was stored under. One with no id yet keeps that name as its key
    until the minting pass assigns one - dropping it would destroy the `hidden` and play
    stats the id exists to protect.

    **An entry that already names itself is left alone**, whichever way it does it: a
    filename, a path to a file elsewhere, or a key its app knows it by. Asking about one
    of those three instead of all of them reads the map key as a filename, which gives
    the entry a file that is not there and makes it the one its folder can never find.
    """
    if not isinstance(entries, dict):
        return {}
    # Callers test identity to mean "nothing to convert", so an empty map belongs here
    # too: a game with no .vpx would otherwise be rewritten on every startup.
    if all(isinstance(e, dict) and entry_native_key(e) for e in entries.values()):
        return entries

    rekeyed = {}
    for key, entry in entries.items():
        if not isinstance(entry, dict):
            continue    # not a record; there is nothing to address or carry
        entry = dict(entry)
        if not entry_native_key(entry):
            entry[TABLE_FILENAME_KEY] = key
        rekeyed[table_id(entry) or key] = entry
    return rekeyed


def hidden_tables(entries: dict | None) -> set[str]:
    """Filenames the user has hidden - never ids, because the caller is comparing against
    a folder listing. Hiding never deletes; a patch base has to stay on disk."""
    return {
        name for name in (entry_filename(entry)
                          for entry in rekey_by_id(entries).values()
                          if isinstance(entry, dict) and entry.get("hidden") is True)
        if name
    }


def offerable(entry: dict) -> bool:
    """Whether this table can be handed to something that plays it: not hidden, and its
    file not gone."""
    return entry.get("hidden") is not True and not entry.get(ABSENT_SINCE_KEY)


def all_hidden(entries: dict | None) -> bool:
    """Whether a game offers none of its tables because it has hidden ones. A game whose
    tables are all gone is missing them, which is not this."""
    held = [entry for entry in rekey_by_id(entries).values() if isinstance(entry, dict)]
    return (any(entry.get("hidden") is True for entry in held)
            and not any(offerable(entry) for entry in held))


def is_recorded(recorded: str, found_id: str, entry: dict) -> bool:
    """Whether this is the table recorded as the game's default. The record holds an id,
    or the filename a 2.x library named."""
    wanted = (recorded or "").strip()
    return bool(wanted) and wanted in (found_id, entry_native_key(entry), entry_filename(entry))


def _by_name(pair: tuple[str, dict]) -> tuple[str, str]:
    return collation.sort_key(entry_filename(pair[1]) or entry_native_key(pair[1]))


def offered_tables(entries: dict | None, recorded: str = "",
                   listing: Iterable[str] | None = None) -> list[tuple[str, dict]]:
    """(id, entry) for each table a game offers, the default first and the rest by name.

    The default is the recorded choice, else the most recently added, ties by name. With
    `listing`, a table in the folder has to be in it, and a table file nothing describes
    yet is offered with no id.
    """
    held = rekey_by_id(entries)
    listed = table_names(listing) if listing is not None else None
    found = [(table_id(entry), entry) for entry in held.values()
             if isinstance(entry, dict) and offerable(entry)
             and (listed is None or entry_form(entry) != FORM_CONTAINED
                  or entry_filename(entry) in listed)]
    if listed is not None:
        described = {entry_filename(e).lower() for e in held.values() if isinstance(e, dict)}
        found += [("", contained_entry(name)) for name in listed
                  if name.lower() not in described]
    found.sort(key=_by_name)

    head = next((pair for pair in found if is_recorded(recorded, *pair)), None)
    if head is None and found:
        head = max(found, key=lambda pair: str(pair[1].get(ADDED_KEY) or ""))
    return [head, *(pair for pair in found if pair is not head)] if head else []


def default_entry(entries: dict | None, recorded: str = "",
                  listing: Iterable[str] | None = None) -> tuple[str, dict]:
    """(id, entry) for the table a game plays when nothing names one, or ("", {})."""
    offered = offered_tables(entries, recorded, listing)
    return offered[0] if offered else ("", {})


def entry_file(game_dir: str, entry: dict) -> str:
    """Where an entry's game file is, or "" for one that has none."""
    if entry_key(entry):
        return ""
    reference = entry_reference(entry)
    if reference:
        return resolved_reference(game_dir, reference)
    name = entry_filename(entry)
    return os.path.join(game_dir, name) if name and game_dir else ""


def stamp_added(game_dir: str, entry: dict) -> bool:
    """Give a table found on disk its `added`, from the file's birth time, or its ctime
    where the filesystem keeps none. False when it has one, or its file cannot be read."""
    if entry.get(ADDED_KEY):
        return False
    try:
        stat = os.stat(entry_file(game_dir, entry))
    except (OSError, ValueError):
        return False
    entry[ADDED_KEY] = epoch_to_iso(getattr(stat, "st_birthtime", stat.st_ctime))
    return True


def table_entries(meta: dict | None) -> dict:
    """The tables section keyed by id, or {}. Normalizes the pre-re-key shape on the way
    out, so a reader sees the current shape whether or not the file has been rewritten -
    only `table_identity.ensure_unique_table_ids` persists the conversion."""
    if not isinstance(meta, dict):
        return {}
    entries = meta.get(TABLES_KEY)
    return rekey_by_id(entries) if isinstance(entries, dict) else {}


def recorded_default(vpinfe: dict | None) -> str:
    """The default someone chose for this game, as stored, or "".

    A table id, so the choice survives a rename, or a filename where the 2.x migration
    seeded one or a hand-edited .info holds one. `is_recorded` reads either.

    Absent is the normal case and means "resolve from what is in the folder" - it is
    never written on a rebuild, which would freeze an arbitrary pick as a choice.
    """
    if not isinstance(vpinfe, dict):
        return ""
    return str(vpinfe.get(DEFAULT_TABLE_KEY, "") or "").strip()
