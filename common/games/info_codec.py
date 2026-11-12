"""A game's `.info` as schema 3 holds it on disk, and as the rest of VPinFE reads it.

On disk the game's files, media, assets and guides are four lists under `vpinfe.manifest`
and `User` is in the units FileLayout.md names. In memory the record keeps the shape and
units every reader was written against: `tables` and `assets` are maps, `guides` a list
with its origin, `Rating` runs 0 to 10 as the file has it, `LastRun` is an epoch and
`RunTime` is minutes with the exact seconds in `vpinfe.run_time_seconds`. Everything above
storage - themes, the API, VPinPlay, the manager - reads the in-memory form and so does not
change with the schema.

`decode` leaves a file that is not schema 3 in the shape it found it: schema 2 is the in-memory
shape. `encode` always writes schema 3.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from math import isfinite
from typing import Any
from urllib.parse import parse_qs, urlparse

from common.games.info_migration import needs_migration
from common.games.tables import (
    ABSENT_SINCE_KEY,
    TABLE_APP_KEY,
    TABLE_FILENAME_KEY,
    TABLE_ID_KEY,
    TABLE_KEY_KEY,
    TABLE_PATH_KEY,
    TABLES_KEY,
    rekey_by_id,
)
from common.timestamps import epoch_to_iso

# FileLayout.md's `Rating`: 0 is unrated, and VPinFE draws it as five stars, half a star a step.
RATING_MAX = 10

SCHEMA = 3
MANIFEST_KEY = "manifest"
MEDIA_KEY = "media"
ASSETS_KEY = "assets"
GUIDES_KEY = "guides"

# What the manifest calls where a table's file is. In memory it is still `filename`.
FILE_KEY = "file"
URL_KEY = "url"

# Keys a 2.x or beta build writes at the top of a file and schema 3 keeps in the manifest.
TOP_LEVEL_LISTS = (TABLES_KEY, ASSETS_KEY, GUIDES_KEY)

MEDIA_FOLDER = "medias"
YOUTUBE_WATCH = "https://www.youtube.com/watch?v={id}"
CATALOG_ORIGIN = "vps"
ORIGIN_KEY = "origin"
VPS_FILE_ID_KEY = "vps_file_id"
SOURCE_KEY = "source"

# What the game's manifest keeps of a table that has a file of its own.
SLIM_KEYS = (TABLE_ID_KEY, TABLE_FILENAME_KEY, "hidden", ABSENT_SINCE_KEY)


def stamped_schema(data: object) -> int:
    """The schema a loaded file declares, or 0 where it declares none."""
    section = data.get("vpinfe") if isinstance(data, dict) else None
    try:
        return int(section.get("schema") or 0) if isinstance(section, dict) else 0
    except (TypeError, ValueError):
        return 0


def is_schema_3(data: object) -> bool:
    return stamped_schema(data) >= SCHEMA


def decode(data: dict[str, Any]) -> dict[str, Any]:
    """The in-memory record for what is on disk.

    A file a 2.x or beta build wrote is already in the in-memory shape, but its ratings are
    whole stars, 0 to 5, and come back doubled. A file with no stamp and nothing a 2.x build
    wrote is read as it is. A schema 3 file that a 2.x or beta build has written to again
    carries that build's units in `User`, and is converted as that build's file.
    """
    if not is_schema_3(data):
        return _ratings_doubled(data) if needs_migration(data) else dict(data)
    returning = needs_migration(data)
    record = {k: v for k, v in data.items() if k not in TOP_LEVEL_LISTS}
    vpinfe = dict(record.get("vpinfe") or {})
    manifest = vpinfe.pop(MANIFEST_KEY, None)
    manifest = manifest if isinstance(manifest, dict) else {}
    record["vpinfe"] = vpinfe

    record[TABLES_KEY] = _tables_in(manifest.get(TABLES_KEY))
    files = [*_listed(manifest.get(MEDIA_KEY)), *_listed(manifest.get(ASSETS_KEY))]
    guides = _listed(manifest.get(GUIDES_KEY))
    files += [one for one in guides if FILE_KEY in one]
    files = [one for one in files if one.get(FILE_KEY)]
    record[ASSETS_KEY] = {str(one[FILE_KEY]): _without(one, FILE_KEY) for one in files}
    record[GUIDES_KEY] = [_guide_in(one) for one in guides if FILE_KEY not in one]

    user = record.get("User")
    if isinstance(user, dict):
        record["User"] = _user_in(user, vpinfe, older=returning)
    for entry in record[TABLES_KEY].values():
        _table_user_in(entry)
    return record


def encode(record: dict[str, Any], slim: Iterable[str] = ()) -> dict[str, Any]:
    """What goes on disk for the in-memory record.

    A table whose file name is in `slim`, lower-cased, has a `.info` of its own that holds its
    record, so the game's manifest keeps only what must outlive that file: the id, the name,
    `hidden` and `absent_since`.
    """
    out = {k: v for k, v in record.items() if k not in TOP_LEVEL_LISTS}
    vpinfe = dict(out.get("vpinfe") or {})
    vpinfe["schema"] = SCHEMA

    user = out.get("User")
    if isinstance(user, dict):
        out["User"] = _user_out(user, vpinfe)
    vpinfe.pop("run_time_seconds", None)

    files = _files_out(record.get(ASSETS_KEY))
    guides = [_guide_out(one) for one in _listed(record.get(GUIDES_KEY))]
    guides += [one for one in files if _group_of(one[FILE_KEY]) == GUIDES_KEY]
    vpinfe[MANIFEST_KEY] = {
        TABLES_KEY: _tables_out(record.get(TABLES_KEY), frozenset(slim)),
        MEDIA_KEY: [one for one in files if _group_of(one[FILE_KEY]) == MEDIA_KEY],
        ASSETS_KEY: [one for one in files if _group_of(one[FILE_KEY]) == ASSETS_KEY],
        GUIDES_KEY: guides,
    }
    out["vpinfe"] = vpinfe
    return out


def _ratings_doubled(data: dict[str, Any]) -> dict[str, Any]:
    """A copy of an older file with its ratings on today's scale, a table's own included."""
    out = dict(data)
    user = out.get("User")
    if isinstance(user, dict) and "Rating" in user:
        out["User"] = {**user, "Rating": half_stars(_stars(user["Rating"]) * 2)}
    tables = out.get(TABLES_KEY)
    if isinstance(tables, dict):
        out[TABLES_KEY] = {key: _table_stars_doubled(entry) for key, entry in tables.items()}
    return out


def _table_stars_doubled(entry: Any) -> Any:
    user = entry.get("user") if isinstance(entry, dict) else None
    if isinstance(user, dict) and "rating" in user:
        return {**entry, "user": {**user, "rating": half_stars(_stars(user["rating"]) * 2)}}
    return entry


def _stars(value: object) -> float:
    amount = number(value)
    return 0 if amount is None or amount <= 0 else min(amount, RATING_MAX // 2)


def _listed(value: object) -> list[dict[str, Any]]:
    return [one for one in value if isinstance(one, dict)] if isinstance(value, list) else []


def _without(one: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {k: v for k, v in one.items() if k not in keys}


def _location(entry: dict[str, Any]) -> tuple[str, ...]:
    for key in (FILE_KEY, TABLE_FILENAME_KEY, TABLE_PATH_KEY, URL_KEY):
        if entry.get(key):
            return (str(entry[key]).lower(),)
    return (str(entry.get(TABLE_APP_KEY, "")).lower(), str(entry.get(TABLE_KEY_KEY, "")).lower())


def _tables_in(listed: object) -> dict[str, dict[str, Any]]:
    tables: dict[str, dict[str, Any]] = {}
    for one in _listed(listed):
        entry = dict(one)
        if FILE_KEY in entry:
            entry[TABLE_FILENAME_KEY] = entry.pop(FILE_KEY)
        key = str(entry.get(TABLE_ID_KEY) or entry.get(TABLE_FILENAME_KEY) or "")
        if key:
            tables[key] = entry
    return tables


def _tables_out(tables: object, slim: frozenset[str] = frozenset()) -> list[dict[str, Any]]:
    listed = []
    for entry in rekey_by_id(tables if isinstance(tables, dict) else None).values():
        if not isinstance(entry, dict):
            continue
        if str(entry.get(TABLE_FILENAME_KEY) or "").lower() in slim:
            entry = {k: v for k, v in entry.items() if k in SLIM_KEYS}
        out = dict(_table_user_out(entry))
        if TABLE_FILENAME_KEY in out:
            out = {FILE_KEY: out.pop(TABLE_FILENAME_KEY), **out}
        listed.append(out)
    return sorted(listed, key=_location)


def _group_of(path: str) -> str:
    """Which list a file belongs in, by its name: the kind is read from the name, as it is
    everywhere else, so a line moved by hand goes back on the next write."""
    from common.games.asset_registry import match_media_kind

    parts = str(path).replace("\\", "/").split("/")
    kind = match_media_kind(parts[-1])
    if kind == "rule_sheet":
        return GUIDES_KEY
    in_media = len(parts) > 1 and parts[0].lower() == MEDIA_FOLDER
    return MEDIA_KEY if kind is not None or in_media else ASSETS_KEY


def _files_out(assets: object) -> list[dict[str, Any]]:
    listed = [{FILE_KEY: str(path), **entry}
              for path, entry in (assets.items() if isinstance(assets, dict) else ())
              if isinstance(entry, dict)]
    return sorted(listed, key=_location)


def watch_id(address: str) -> str:
    """The video a YouTube address names, or ""."""
    parsed = urlparse(address)
    host = parsed.netloc.lower().removeprefix("www.")
    if host == "youtu.be":
        return parsed.path.strip("/")
    if host in ("youtube.com", "m.youtube.com") and parsed.path == "/watch":
        return parse_qs(parsed.query).get("v", [""])[0]
    return ""


def _guide_in(one: dict[str, Any]) -> dict[str, Any]:
    guide = dict(one)
    address = str(guide.pop(URL_KEY, "") or "")
    source = guide.pop(SOURCE_KEY, None)
    video = str(guide.pop("youtube_id", "") or "")
    if not video and address and address == YOUTUBE_WATCH.format(id=watch_id(address)):
        video, address = watch_id(address), ""
    guide.update({URL_KEY: address, "youtube_id": video,
                  ORIGIN_KEY: CATALOG_ORIGIN if isinstance(source, dict) else "user"})
    if isinstance(source, dict) and source.get(VPS_FILE_ID_KEY):
        guide[VPS_FILE_ID_KEY] = source[VPS_FILE_ID_KEY]
    return guide


def _guide_out(one: dict[str, Any]) -> dict[str, Any]:
    guide = dict(one)
    url = str(guide.get(URL_KEY) or "")
    video = str(guide.pop("youtube_id", "") or "")
    origin = guide.pop(ORIGIN_KEY, None)
    file_id = guide.pop(VPS_FILE_ID_KEY, "")
    address = url or (YOUTUBE_WATCH.format(id=video) if video else "")
    out = {URL_KEY: address, **_without(guide, URL_KEY)}
    if video and url and url != YOUTUBE_WATCH.format(id=video):
        out["youtube_id"] = video
    if origin == CATALOG_ORIGIN:
        out[SOURCE_KEY] = {VPS_FILE_ID_KEY: file_id}
    return out


def number(value: object) -> float | None:
    """A finite number, or None for anything a person's edit could have left there."""
    try:
        found = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return found if isfinite(found) else None


def half_stars(value: object) -> int:
    """A 0 to 10 rating, a half star a step; anything else a person's edit left is unrated."""
    amount = number(value)
    return 0 if amount is None or amount <= 0 else int(min(amount, RATING_MAX))


def epoch_of(stamp: str) -> int | None:
    """A UTC ISO stamp as epoch seconds; a stamp with no zone is UTC, as `epoch_to_iso` has it."""
    try:
        parsed = datetime.fromisoformat(stamp.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return int((parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).timestamp())


def _user_in(user: dict[str, Any], vpinfe: dict[str, Any], older: bool = False) -> dict[str, Any]:
    """`User` in memory. `older` is a file a 2.x or beta build last wrote: whole stars and
    minutes, with any `vpinfe.run_time_seconds` the beta kept left as found."""
    out = dict(user)
    out["Rating"] = half_stars(_stars(user.get("Rating")) * 2 if older else user.get("Rating"))
    if "Favorite" in user:
        out["Favorite"] = str(user["Favorite"]).strip().lower() in ("1", "true")
    if isinstance(user.get("LastRun"), str):
        out["LastRun"] = epoch_of(user["LastRun"])
    amount = number(user.get("RunTime"))
    if amount is not None and older:
        out["RunTime"] = max(0, int(amount))
    elif amount is not None:
        total = max(0, int(amount))
        out["RunTime"] = total // 60
        if total:
            vpinfe["run_time_seconds"] = total
    return out


def _user_out(user: dict[str, Any], vpinfe: dict[str, Any]) -> dict[str, Any]:
    out = dict(user)
    out["Rating"] = half_stars(user.get("Rating"))
    if "Favorite" in user:
        out["Favorite"] = 1 if user["Favorite"] in (True, 1, "1", "true", "True") else 0
    if "LastRun" in user:
        out["LastRun"] = (epoch_to_iso(user["LastRun"]) or None) if user["LastRun"] else None
    seconds = vpinfe.get("run_time_seconds")
    if seconds is None:
        seconds = (number(user.get("RunTime")) or 0) * 60
    out["RunTime"] = max(0, int(seconds))
    return out


def _table_user_in(entry: dict[str, Any]) -> None:
    user = entry.get("user")
    if isinstance(user, dict) and "rating" in user:
        entry["user"] = {**user, "rating": half_stars(user["rating"])}


def _table_user_out(entry: dict[str, Any]) -> dict[str, Any]:
    user = entry.get("user")
    if isinstance(user, dict) and "rating" in user:
        return {**entry, "user": {**user, "rating": half_stars(user["rating"])}}
    return entry
