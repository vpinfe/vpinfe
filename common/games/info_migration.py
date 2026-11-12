"""Bring a 2.x or schema 2 `.info` up to schema 3.

Generations of the file, counting from the first one that shipped:

  1  what 2.x wrote. Implied - 2.x recorded no version, so a file either carries a
     stamp or is this one.
  2  the 3.0 betas: our keys under `vpinfe`, tables, assets and guides at the top.
  3  the current shape (`info_codec`): tables, media, assets and guides in one manifest
     under `vpinfe`, and `User` in the units FileLayout.md names.

`schema_of` still answers None rather than 1 for an unstamped file, because callers
need to tell "declares nothing" from "declares 1"; the migration treats them alike.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from common.atomic_write import staged_for, write_atomic
from common.games import high_scores
from common.games.tables import TABLE_FILENAME_KEY, TABLE_ID_KEY, TABLES_KEY, parse_authors
from common.kept_copies import BACKUP_MARKER, backup_names
from common.timestamps import iso_from_asctime, iso_from_authored_date

logger = logging.getLogger("vpinfe.common.games.info_migration")

SCHEMA_KEY = "schema"
INFO_SCHEMA = 3
BETA_SCHEMA = 2

# Declared here rather than in `info_file` because that module imports this one.
GUIDES_KEY = "guides"
# Who put a guide on the game. Only the catalog's records say so; any other is a person's.
GUIDE_ORIGIN = "origin"
FROM_CATALOG = "vps"
# The importer's one copy of a merged `.info`, `<name>.info.bak`. Nothing writes it now.
IMPORTER_BAK = ".bak"

# How the match in `Info` was made, kept in the `vpinfe` section. Absent is VPinFE's
# guess from the folder name.
VPS_MATCHED_BY_KEY = "vps_matched_by"
MATCHED_ON_IMPORT = "import"
MATCHED_BY_USER = "user"

# What 2.x called each VPXFile field, against what it is called now. Matched
# case-insensitively: real files carry both `detectnfozzy` and `detectNfozzy`, and a
# case-sensitive lookup silently drops the flag.
_TABLE_KEYS = {
    "filehash": "file_hash",
    "vbshash": "vbs_hash",
    "version": "version",
    "releasedate": "release_date",
    "savedate": "save_date",
    "saverev": "save_rev",
    "manufacturer": "manufacturer",
    "year": "year",
    "type": "type",
    "rom": "rom",
    "detectnfozzy": "detect_nfozzy",
    "detectfleep": "detect_fleep",
    "detectssf": "detect_ssf",
    "detectlut": "detect_lut",
    "detectscorebit": "detect_scorbit",     # the old name was a typo for Scorbit
    "detectfastflips": "detect_fastflips",
    "detectflex": "detect_flex",
    "detectpinmame": "detect_pinmame",
}

_VPINFE_KEYS = {
    "deletednvramonclose": "delete_nvram_on_close",
    "altlauncher": "alt_launcher",
    "pluginprofile": "plugin_profile",
    "alttitle": "alt_title",
    "altvpsid": "alt_vpsid",
}

_DROPPED_SECTIONS = ("VPXFile", "Medias")
BETA_LISTS = ("tables", "assets", "guides")
_DROPPED_INFO_KEYS = ("Rom",)

# `alt_launcher` and `plugin_profile` are retired, and this is deliberately not where
# they go. They name a launcher for a *table*, and a table has no id until
# `ensure_unique_table_ids` mints one over the migrated library - so dropping them here
# would destroy the values before anything could record which table they belonged to.
# `launcher_migration` consumes them instead, after the ids exist, and writes the file
# without them.



def schema_of(data: object) -> int | None:
    """The schema a loaded .info declares, or None if it predates versioning.

    Only the section we write today counts - the old PascalCase `schema` numbered that
    section's shape, not the file's (5db3f3d).
    """
    if not isinstance(data, dict):
        return None
    section = data.get("vpinfe")
    if not isinstance(section, dict):
        return None
    try:
        return int(section.get(SCHEMA_KEY) or 0) or None
    except (TypeError, ValueError):
        return None


def is_versioned(data: object) -> bool:
    """Whether this file has been through the migration."""
    return schema_of(data) is not None


def _written_by_2x(data: dict) -> bool:
    """Anything 2.x wrote.

    We never write VPXFile or Medias, so either in a stamped file is proof 2.x has been
    back since - checked ahead of the stamp, which is the thing not to trust here.
    """
    if any(name in data for name in _DROPPED_SECTIONS):
        return True
    if is_versioned(data):
        return False
    user = data.get("User")
    if isinstance(user, dict) and "FrontendDOFEvent" in user:
        return True
    legacy = data.get("VPinFE")
    return isinstance(legacy, dict) and any(k.lower() in _VPINFE_KEYS for k in legacy)


def needs_migration(data: object) -> bool:
    """A file that is not schema 3, or a schema 3 one an older build wrote to again.

    A file with no stamp and nothing 2.x wrote has nothing to convert: it reads as it is
    and is stamped on its first write. A beta never writes the manifest, so a table,
    asset or guide list at the top of a schema 3 file is proof a beta has been back.
    """
    if not isinstance(data, dict):
        return False
    if _written_by_2x(data):
        return True
    schema = schema_of(data)
    if schema is None:
        return False
    return schema < INFO_SCHEMA or any(name in data for name in BETA_LISTS)


def written_by_beta(data: object) -> bool:
    """A file the 3.0 beta wrote: schema 2, or a schema 3 one the beta has written to again."""
    return (needs_migration(data) and isinstance(data, dict) and not _written_by_2x(data)
            and (schema_of(data) or 0) >= BETA_SCHEMA)


def _rename(source: dict, mapping: dict) -> dict:
    renamed = {}
    for key, value in source.items():
        new_key = mapping.get(key.lower())
        if new_key:
            renamed[new_key] = value
    return renamed


def _table_entry(vpx_file: dict, authors: object) -> dict:
    entry = _rename(vpx_file, _TABLE_KEYS)
    entry["release_date"] = iso_from_authored_date(entry.get("release_date", ""))
    entry["save_date"] = iso_from_asctime(entry.get("save_date", ""))
    entry["authors"] = parse_authors(authors)
    return entry


def migrate(data: dict) -> dict:
    """The file as the in-memory record, stamped schema 3. Pure: takes a loaded .info,
    returns a new one.

    The in-memory record is what `info_codec.decode` gives and what schema 2 already is,
    so the step from 2 to 3 changes nothing but the stamp; the new shape and units are
    written by `info_codec.encode`. Its output is the in-memory shape, so it is only ever
    run on a file as read from disk: a stamped record with a top-level `tables` reads as a
    beta's.
    """
    from common.games import info_codec

    beta_lists: dict[str, Any] = {}
    if info_codec.is_schema_3(data):
        beta_lists = {name: data[name] for name in BETA_LISTS if name in data}
        data = info_codec.decode(data)
    from_beta = bool(beta_lists)
    upgrading = (schema_of(data) or 0) < INFO_SCHEMA and not from_beta
    if _written_by_2x(data):
        data = migrate_2x(data)
    record = dict(data)
    vpinfe = dict(record.get("vpinfe") or {})
    vpinfe[SCHEMA_KEY] = INFO_SCHEMA
    record["vpinfe"] = vpinfe
    if from_beta:
        _fold_beta_plays(record, beta_lists.get(TABLES_KEY))
    if upgrading:
        _fold_single_table_marks(record)
    return record


def _later_stamp(a: object, b: object) -> object:
    """The later of two ISO stamps; whichever exists where only one does."""
    stamps = [str(x) for x in (a, b) if x]
    return max(stamps) if stamps else a


def _fold_beta_plays(record: dict, beta_tables: object) -> None:
    """Take in what a beta counted after we last wrote: the larger count and time and the
    later date, so folding twice never adds twice."""
    mine = record.get(TABLES_KEY)
    if not isinstance(mine, dict) or not isinstance(beta_tables, dict):
        return
    by_name = {str(e.get(TABLE_FILENAME_KEY, "")).lower(): e for e in mine.values()
               if isinstance(e, dict)}
    for key, theirs in beta_tables.items():
        if not isinstance(theirs, dict) or not isinstance(theirs.get("user"), dict):
            continue
        entry = (mine.get(str(theirs.get(TABLE_ID_KEY) or key))
                 or by_name.get(str(theirs.get(TABLE_FILENAME_KEY) or key).lower()))
        if entry is None:
            continue
        ours = entry.setdefault("user", {})
        for field in ("start_count", "run_time_seconds"):
            if field in theirs["user"] or field in ours:
                ours[field] = max(int(ours.get(field) or 0), int(theirs["user"].get(field) or 0))
        stamp = _later_stamp(ours.get("last_run"), theirs["user"].get("last_run"))
        if stamp:
            ours["last_run"] = stamp


def _fold_single_table_marks(record: dict) -> None:
    """A game with one table has one rating and one set of tags, the game's. What the
    table alone held goes up: its rating where the game has none, and its tags added."""
    tables = record.get(TABLES_KEY)
    if not isinstance(tables, dict) or len(tables) != 1:
        return
    entry = next(iter(tables.values()))
    held = entry.get("user") if isinstance(entry, dict) else None
    if not isinstance(held, dict):
        return
    user = record.setdefault("User", {})
    rating = held.pop("rating", 0)
    if rating and not user.get("Rating"):
        user["Rating"] = rating
    held_tags = held.pop("tags", None)
    tags = [str(one) for one in held_tags] if isinstance(held_tags, list) else []
    if tags:
        user["Tags"] = [*(user.get("Tags") or []),
                        *(one for one in tags if one not in (user.get("Tags") or []))]
    if not held:
        entry.pop("user", None)


def migrate_2x(data: dict) -> dict:
    """The 2.x file as schema 2. Pure: takes a loaded .info, returns a new one."""
    info = dict(data.get("Info") or {})
    user = dict(data.get("User") or {})
    legacy = data.get("VPinFE")
    legacy = dict(legacy) if isinstance(legacy, dict) else {}
    vpx_file = data.get("VPXFile")
    vpx_file = dict(vpx_file) if isinstance(vpx_file, dict) else {}

    # Authors were game-level, which only worked while a folder held one table.
    # With exactly one, they are that file's - a real recorded fact, so it carries.
    authors = info.pop("Authors", None)
    for key in _DROPPED_INFO_KEYS:
        info.pop(key, None)

    vpinfe = _rename(legacy, _VPINFE_KEYS)
    # Anything else in there came from somewhere we don't know about; it stays as it is.
    vpinfe.update({k: v for k, v in legacy.items() if k.lower() not in _VPINFE_KEYS})
    # A file already holding the new section keeps it, and it wins: running this over
    # its own output has to be a no-op, or the second call eats the first.
    already = data.get("vpinfe")
    if isinstance(already, dict):
        vpinfe.update(already)
    vpinfe[SCHEMA_KEY] = BETA_SCHEMA
    if not is_versioned(data) and str(info.get("VPSId", "") or "").strip():
        vpinfe.setdefault(VPS_MATCHED_BY_KEY, MATCHED_BY_USER)

    # The DOF override is configuration, and User is the interop contract for play
    # history. It moves, and the old key goes with it.
    dof_event = user.pop("FrontendDOFEvent", None)
    if dof_event is not None:
        vpinfe["frontend_dof_event"] = dof_event

    high_scores.from_2x(user)

    # 2.x kept one tutorial link in Info. It becomes a guide, and a file already holding
    # guides keeps them - running this over its own output has to be a no-op.
    primer = str(info.pop("PinballPrimerTut", "") or "").strip()
    guides = data.get(GUIDES_KEY)
    guides = list(guides) if isinstance(guides, list) else []
    if primer and not any(isinstance(one, dict) and one.get("url") == primer
                          for one in guides):
        guides.append({"kind": "tutorial", GUIDE_ORIGIN: FROM_CATALOG, "title": "", "authors": [],
                       "url": primer, "youtube_id": ""})

    tables = dict(data.get(TABLES_KEY) or {})
    filename = str(vpx_file.get("filename", "") or "").strip()
    if filename:
        # Refresh what VPXFile covers and leave the rest - hidden, source, play stats.
        # Nothing to keep on a first migration; everything to lose when 2.x wrote the
        # file again and this runs over an entry we already built.
        prior = tables.get(filename)
        prior = prior if isinstance(prior, dict) else {}
        tables[filename] = {**prior, **_table_entry(vpx_file, authors)}
        # No default is recorded. 2.x described one table, so the resolver picks that
        # one anyway - and writing it down would freeze an arbitrary pick as a choice,
        # which is exactly what `recorded_default` says a rebuild must never do. It
        # also made every migrated game report a user-chosen default it never had.

    migrated: dict[str, Any] = {"Info": info, "User": user, "vpinfe": vpinfe,
                                TABLES_KEY: tables}
    if guides:
        migrated[GUIDES_KEY] = guides
    known = {*_DROPPED_SECTIONS, "Info", "User", "VPinFE", "vpinfe", TABLES_KEY,
             GUIDES_KEY}
    migrated.update({k: v for k, v in data.items() if k not in known})
    return migrated


def is_importer_bak(name: str | Path, info_name: str) -> bool:
    """Whether a name is the importer's old single copy of this `.info`."""
    return Path(name).name == info_name + IMPORTER_BAK


def copied_from(backup: str | Path) -> Path:
    """The file this copy was taken of."""
    path = Path(backup)
    if path.name.endswith(IMPORTER_BAK) and BACKUP_MARKER not in path.name:
        return path.with_name(path.name[:-len(IMPORTER_BAK)])
    return path.with_name(path.name.rsplit(BACKUP_MARKER, 1)[0])


def restorable_backup(game_dir: str | Path, max_schema: int = INFO_SCHEMA,
                      names: Iterable[str] | None = None) -> str | None:
    """The backup this build would restore here, or None.

    Newest readable wins; a newer one is stepped over rather than ending the search.
    Pass `names` when the folder is already listed - only folders with a backup pay.
    """
    folder = Path(game_dir)
    if names is None:
        try:
            names = os.listdir(folder)
        except OSError:
            return None
    info_name = f"{folder.name}.info"
    for name in backup_names(names, info_name):
        candidate = folder / name
        try:
            schema = backup_schema(candidate)
        except (OSError, ValueError):
            logger.warning("Unreadable backup, skipping: %s", candidate)
            continue
        if schema is None or schema <= max_schema:
            return str(candidate)
    return None


def backup_schema(path: str | Path) -> int | None:
    """The schema a backup holds, or None when it predates versioning.

    Raises if the file cannot be read at all - the caller decides whether to skip it.
    """
    with open(path, encoding="utf-8") as handle:
        return schema_of(json.load(handle))


def replace_atomic(source: str | Path, path: str | Path) -> None:
    """Put `source` at `path` with no window where `path` is half written.

    A plain copy truncates first, and restore does that once per game across the library.
    """
    with staged_for(path) as staged:
        shutil.copy2(source, staged)


def write_json_atomic(path: str | Path, data: object) -> None:
    """Write a .info atomically."""
    write_atomic(path, lambda handle: json.dump(data, handle, indent=4))
