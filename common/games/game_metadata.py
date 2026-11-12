"""Reading a game's `.info`, whichever schema wrote it.

Callers ask for a value, not for a schema version: a file written by 2.x and one
written today answer the same questions here.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from typing import Any

from common import events
from common.games import high_scores, info_files, ties, unwritten
from common.games.game import Game, GameRecord
from common.games.info_codec import RATING_MAX
from common.games.info_file import GAME_ID_KEY, VPINFE_SECTION, MetaConfig
from common.games.info_migration import MATCHED_BY_USER, MATCHED_ON_IMPORT, VPS_MATCHED_BY_KEY
from common.games.tables import (
    DETECT_KEYS,
    TABLE_FILENAME_KEY,
    TABLE_ID_KEY,
    TABLES_KEY,
    default_entry,
    entry_filename,
    entry_for_filename,
    entry_native_key,
    recorded_default,
    rekey_by_id,
    table_entries,
)
from common.i18n import t
from common.ids import new_id
from common.online import sites
from common.players import players
from common.values import is_truthy

# Re-exported so the theme payload and the Manager agree with storage. Sourced
# from the tables module rather than restated, since a second list drifts silently -
# it already did: these held the old spellings and were writing dead `detectssf`
# keys over the top of the real `detect_ssf` ones.
DETECTION_KEYS = DETECT_KEYS


def normalize_meta(meta: Any) -> dict[str, Any]:
    if isinstance(meta, dict):
        return meta
    if hasattr(meta, "get_config"):
        data = meta.get_config()
        return data if isinstance(data, dict) else {}
    if hasattr(meta, "config") and isinstance(meta.config, dict):
        return meta.config
    return {}


def section(meta: Any, name: str) -> dict[str, Any]:
    normalized = normalize_meta(meta)
    value = normalized.get(name, {})
    return value if isinstance(value, dict) else {}


def vpinfe_section(meta: Any) -> dict[str, Any]:
    """The section VPinFE owns. Read through here rather than by name, so the name
    lives in one place."""
    return section(meta, VPINFE_SECTION)


def get_meta_value(meta: Any, section_name: str, key: str, fallback: Any = "") -> Any:
    sec = section(meta, section_name)
    value = sec.get(key, fallback)
    return fallback if value is None else value


def first_meta_value(meta: Any, *paths: tuple[str, str], default: Any = "") -> Any:
    for section_name, key in paths:
        value = get_meta_value(meta, section_name, key, None)
        if value not in ("", None):
            return value
    return default


def default_table(meta: Any, names: Any = None) -> tuple[str, dict[str, Any]]:
    """(filename, entry) for the table this game defaults to; ("", {}) when it has none.

    Returns both because the callers that need one usually need the other - a row on the
    games list shows the filename and the version off the same table - and resolving
    twice would be doing the same work to answer half the question each time.

    Callers holding a folder listing should pass it, so a recorded default that is no
    longer on disk falls through to one that is.
    """
    normalized = normalize_meta(meta)
    _found, entry = default_entry(table_entries(normalized),
                                  recorded_default(vpinfe_section(normalized)), names)
    return entry_filename(entry), entry


def default_table_entry(meta: Any, names: Any = None) -> dict[str, Any]:
    """What the game's default table says about itself, or {}."""
    return default_table(meta, names)[1]


def normalize_rating(value: Any) -> int:
    """A rating on the scale a game is rated on: 0 (unrated) to 10, a half star a step."""
    try:
        normalized = int(float(value))
    except (TypeError, ValueError):
        normalized = 0
    return max(0, min(RATING_MAX, normalized))


def star_words(halves: Any) -> str:
    """A rating as the number of stars a person reads: `3`, `3½`, `½`. The count a rule or
    a menu names, so it is never the stored 0 to 10."""
    steps = normalize_rating(halves)
    whole = f"{steps // 2}" if steps // 2 else ""
    return whole + ("½" if steps % 2 else "") or "0"


def whole_stars(rating: Any) -> int:
    """A rating as whole stars, for a reader that draws no half star. The half rounds down,
    except the lowest: a half star is a rating, so it reads as one star and never as none."""
    halves = normalize_rating(rating)
    return max(1, halves // 2) if halves else 0


def as_string_list(value: Any) -> list[str]:
    """A list-valued metadata field, whatever the .info actually held.

    These come back as lists from a normal metadata build, but a hand-edited or
    badly-written .info can hold a scalar - including a stringified list. One such
    game is enough to make every consumer's type assumption wrong, so it is
    contained here.

    A scalar becomes a one-item list rather than being parsed. Reading
    "['a', 'b']" back as two items would mean inventing a syntax for a file format
    that does not have one, so the odd value stays visible instead.
    """
    if value in ("", None):
        return []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return [str(value)]


def reorder_leading_article(title: Any) -> str:
    """Move a leading "The " article to the end so titles sort by their
    second word, e.g. "The Addams Family" -> "Addams Family, The".

    Idempotent: a title that has already been reordered (or never started
    with "The") is returned unchanged. Only the whole word "The" followed by
    more text is reordered ("Theatre of Magic" is left alone).
    """
    text = str(title or "").strip()
    if not text:
        return text
    parts = text.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "the":
        return f"{parts[1]}, {parts[0]}"
    return text


# field -> (the key our section stores the user's answer under, the Info key the
# catalog's own answer sits under).
GAME_OVERRIDES = {
    "title": ("alt_title", "Title"),
    "manufacturer": ("alt_manufacturer", "Manufacturer"),
    "year": ("alt_year", "Year"),
    "type": ("alt_type", "Type"),
    "themes": ("alt_themes", "Themes"),
    "ipdb_id": ("alt_ipdb_id", "IPDBId"),
}


def game_override(meta: Any, field: str) -> Any:
    """What the user answered for one field, or "" where they have not.

    Empty is not an answer. The control clears an override by writing the discovered
    value back, so a blank says nobody has spoken rather than asking for the field to
    read blank.
    """
    value = vpinfe_section(meta).get(GAME_OVERRIDES[field][0])
    if isinstance(value, list):
        return value
    return str(value or "").strip()


def game_discovered(meta: Any, field: str) -> Any:
    """What the entry said for one field, which is what a revert returns to."""
    return section(meta, "Info").get(GAME_OVERRIDES[field][1], "")


def game_title(game: GameRecord) -> str:
    meta = normalize_meta(getattr(game, "meta_config", {}))
    answered = game_override(meta, "title")
    if answered:
        # A user-set title wins on its own - it does not require a match - and is left
        # exactly as entered, never reordered.
        return answered
    raw = str(game_discovered(meta, "title")
              or get_meta_value(meta, "VPSdb", "name", "")
              or game.game_dir_name or "").strip()
    return reorder_leading_article(raw)


def game_tags(game: GameRecord) -> list[str]:
    """The user's own words for a game. A set on the way out: `Tags` is a JSON list and
    nothing has stopped one holding the same word twice."""
    meta = normalize_meta(getattr(game, "meta_config", {}))
    said = section(meta, "User").get("Tags") or []
    if not isinstance(said, list):
        said = [said]
    seen, out = set(), []
    for tag in (str(item).strip() for item in said):
        if tag and tag not in seen:
            seen.add(tag)
            out.append(tag)
    return out


def normalize_tag(text: str) -> str:
    """One tag, as it is stored. Trimmed, and internal runs collapsed - `Wide  Body `
    must not become a second tag. Case is left alone on purpose: the picker surfaces
    close matches and the user decides."""
    return " ".join(str(text or "").split())


def _tag_set(tags: Iterable[Any] | None) -> list[str]:
    stored: list[str] = []
    for tag in tags or []:
        said = normalize_tag(tag)
        if said and said not in stored:
            stored.append(said)
    return stored


def set_game_tags(game: Game, tags: Iterable[str] | None) -> list[str]:
    """Write the whole set, returning what was stored.

    A whole-value write, like the rating: the set is the resource. Normalized and
    de-duplicated here rather than at a call site, so every writer agrees.
    """
    stored = _tag_set(tags)
    config = load_game_meta(game)
    get_or_create_user_meta(config)["Tags"] = stored
    persist_game_meta(game, config)
    game.meta_config = config
    return stored


def table_tags(entry: dict[str, Any]) -> list[str]:
    said = (entry.get("user") or {}).get("tags") or []
    return _tag_set(said if isinstance(said, list) else [said])


def set_table_tags(game: Game, native: str, tags: Iterable[str] | None) -> list[str]:
    """One table's own set, written the way a game's is."""
    stored = _tag_set(tags)
    config = load_game_meta(game)
    get_or_create_table_user(config, native)["tags"] = stored
    persist_game_meta(game, config)
    game.meta_config = config
    return stored


def carrying(games: Iterable[Game], sources: Iterable[str]) -> list[Game]:
    """The games a sweep over `sources` would write to - those with one of them on the
    game or on any of its tables."""
    wanted = {normalize_tag(name) for name in sources if normalize_tag(name)}
    return [game for game in games
            if wanted & set(game_tags(game))
            or any(isinstance(entry, dict) and wanted & set(table_tags(entry))
                   for entry in table_entries(
                       normalize_meta(getattr(game, "meta_config", {}))).values())]


def retag_library(games: Iterable[Game], sources: Iterable[str], into: str = "") -> int:
    """Rename, merge and delete are one sweep, and this is it.

    Renaming is merging one tag into a name nothing uses; deleting is merging into
    nothing. Writing them as three would be three chances to disagree about ordering
    and about what happens when a game already carries the survivor.

    Returns how many games changed. Order is kept, and the survivor lands where the
    first of the sources was rather than at the end - a merge should not reshuffle a
    list somebody arranged.
    """
    wanted = {normalize_tag(name) for name in sources if normalize_tag(name)}
    survivor = normalize_tag(into)
    if not wanted or survivor in wanted and len(wanted) == 1:
        return 0

    def swept(held: list[str]) -> list[str]:
        out: list[str] = []
        for tag in held:
            said = survivor if tag in wanted else tag
            if said and said not in out:
                out.append(said)
        return out

    touched = 0
    for game in games:
        held = game_tags(game)
        out = swept(held)
        tables = {key: swept(table_tags(entry)) for key, entry in
                  table_entries(normalize_meta(getattr(game, "meta_config", {}))).items()
                  if isinstance(entry, dict) and wanted & set(table_tags(entry))}
        if out != held:
            set_game_tags(game, out)
        if tables:
            _set_tables_tags(game, tables)
        if out != held or tables:
            touched += 1
    return touched


def _set_tables_tags(game: Game, changes: dict[str, list[str]]) -> None:
    config = load_game_meta(game)
    entries = rekey_by_id(config.setdefault(TABLES_KEY, {}))
    config[TABLES_KEY] = entries
    for key, tags in changes.items():
        if isinstance(entries.get(key), dict):
            entries[key].setdefault("user", {})["tags"] = tags
    persist_game_meta(game, config)
    game.meta_config = config


def game_themes(game: GameRecord) -> list[str]:
    """The game's themes. `Info.Themes` is ours and is a JSON list, so a scalar there is
    a bad value and stays visible as one - `as_string_list` has the reasoning, and this
    reads the same way it does. `VPSdb.theme` is scraped 2.x data where the repr form
    genuinely occurred, so that one is parsed."""
    meta = normalize_meta(getattr(game, "meta_config", {}))
    answered = game_override(meta, "themes")
    if answered:
        return as_string_list(answered)

    value = game_discovered(meta, "themes")
    if value:
        return as_string_list(value)

    legacy = get_meta_value(meta, "VPSdb", "theme", "")
    if not legacy:
        return []
    if isinstance(legacy, list):
        return legacy
    try:
        parsed = ast.literal_eval(str(legacy))
        if isinstance(parsed, list):
            return parsed
    except (ValueError, SyntaxError):
        pass
    return [legacy]


def game_type(game: GameRecord) -> str:
    meta = normalize_meta(getattr(game, "meta_config", {}))
    return str(game_override(meta, "type")
               or first_meta_value(meta, ("Info", "Type"), ("VPSdb", "type"),
                                   default="") or "")


def game_manufacturer(game: GameRecord) -> str:
    meta = normalize_meta(getattr(game, "meta_config", {}))
    return str(game_override(meta, "manufacturer")
               or first_meta_value(meta, ("Info", "Manufacturer"),
                                   ("VPSdb", "manufacturer"), default="") or "")


def game_year(game: GameRecord) -> str:
    meta = normalize_meta(getattr(game, "meta_config", {}))
    value = (game_override(meta, "year")
             or first_meta_value(meta, ("Info", "Year"), ("VPSdb", "year"), default=""))
    return str(value) if value else ""


def game_ipdb_id(game: GameRecord) -> str:
    """The IPDB number, the user's own answer first.

    Parsed out of the entry's `ipdbUrl` at match time, so it is the catalog's answer
    wherever the user has not given one.
    """
    meta = normalize_meta(getattr(game, "meta_config", {}))
    return str(game_override(meta, "ipdb_id")
               or game_discovered(meta, "ipdb_id") or "")


def game_rating(game: GameRecord) -> int:
    meta = normalize_meta(getattr(game, "meta_config", {}))
    return normalize_rating(get_meta_value(meta, "User", "Rating", 0))


def game_last_run(game: GameRecord) -> int:
    """When the game was last played, as the epoch integer `User.LastRun` is specced as.

    0 for a game with no play on record. Both the `played` axis and the `last_played`
    order read this one accessor, so a game the filter admits is always one the sort
    has a date to place.
    """
    meta = normalize_meta(getattr(game, "meta_config", {}))
    try:
        return int(get_meta_value(meta, "User", "LastRun", 0) or 0)
    except (TypeError, ValueError):
        return 0


def set_game_rating(game: Game, rating: Any) -> int:
    """Write `User.Rating`, returning what was stored.

    Re-reads from disk first, so a rating set from one surface does not overwrite
    whatever another wrote to the same `.info` while this copy was held.
    """
    config = load_game_meta(game)
    stored = normalize_rating(rating)
    get_or_create_user_meta(config)["Rating"] = stored
    persist_game_meta(game, config)
    game.meta_config = config
    owner = players.get_roster().owner()
    events.emit(events.GAME_RATED,
                game_id=str(vpinfe_section(config).get(GAME_ID_KEY, "") or "").strip(),
                player=owner.as_payload() if owner else None, rating=stored)
    return stored



def play_record(meta: Any) -> dict[str, Any]:
    """A game's play record, in the shapes a consumer wants rather than the file's.

    `User` is VPX's own section and keeps its own: LastRun an epoch integer, RunTime in
    minutes. The names match the collection sort axes, which are already the outward
    vocabulary for these.
    """
    from common.timestamps import epoch_to_iso

    user = section(meta, "User")
    return {
        "rating": int(user.get("Rating", 0) or 0),
        "favorite": bool(user.get("Favorite", 0)),
        "tags": user.get("Tags") or [],
        "last_played": epoch_to_iso(user.get("LastRun")) or None,
        "high_scores": _default_high_scores(meta),
        "play_count": int(user.get("StartCount", 0) or 0),
        # The seconds we keep, not the minutes multiplied back up - that only ever
        # returned whole minutes, and inflated ones at that.
        "play_time_seconds": run_time_seconds(meta),
    }


def _default_high_scores(meta: Any) -> dict[str, Any] | None:
    """The machine's high score table for the table that plays when the game is
    launched - its default, whose ROM a game launch reads."""
    _name, entry = default_table(meta)
    return high_scores.view(normalize_meta(meta), entry,
                            table_id=str(entry.get(TABLE_ID_KEY, "") or ""))


def table_rating(table: dict) -> int:
    """One table's own rating, and 0 for the many that have none.

    The game's is the headline - both levels carry one, and the game's is primary - so
    an unrated table is the normal case rather than a gap - it means "the game's rating
    stands", never "nobody liked it".
    """
    return normalize_rating((table.get("user") or {}).get("rating", 0))


def set_game_favorite(game: Game, favorite: Any) -> bool:
    """Write `User.Favorite`, returning what was stored.

    A real boolean. Every `.info` written before this holds the zero-fill `0`, and the
    reader coerces, so an old one still reads false.

    Re-read from disk first, for the reason `set_game_rating` gives.
    """
    stored = bool(favorite)
    config = load_game_meta(game)
    get_or_create_user_meta(config)["Favorite"] = stored
    persist_game_meta(game, config)
    game.meta_config = config
    return stored


PRIVATE_KEY = "private"


def game_private(meta: Any) -> bool:
    """Whether the game is Private: never sent to a community service, whoever plays it."""
    return is_truthy(vpinfe_section(meta).get(PRIVATE_KEY))


def set_game_private(game: Game, private: Any) -> bool:
    """Write `vpinfe.private`, returning what was stored. Re-read from disk first, for the
    reason `set_game_rating` gives."""
    stored = bool(private)
    config = load_game_meta(game)
    get_or_create_vpinfe_meta(config)[PRIVATE_KEY] = stored
    persist_game_meta(game, config)
    game.meta_config = config
    return stored


def set_table_rating(game: Game, filename: str, rating: Any) -> int:
    """Write one table's rating, returning what was stored.

    Re-read from disk first, for the reason `set_game_rating` gives: two surfaces write
    the same `.info`, and the one holding the older copy would otherwise win.
    """
    config = load_game_meta(game)
    get_or_create_table_user(config, filename)["rating"] = normalize_rating(rating)
    persist_game_meta(game, config)
    game.meta_config = config
    return normalize_rating(rating)


def reset_game_play_record(game: Game) -> dict[str, Any]:
    """Put a game's counters back to nothing, leaving what was entered alone.

    Rating, favorite and tags are opinions somebody set; the counters are a record of
    what happened. Resetting is the common correction - a table launched twenty times
    while it was being tested reads as a favorite forever otherwise - and it is the
    one that needs no arithmetic from the user.
    """
    config = load_game_meta(game)
    user = get_or_create_user_meta(config)
    user["LastRun"] = None
    user["StartCount"] = 0
    user["RunTime"] = 0
    # The seconds are the record and `User.RunTime` is the minutes VPX keeps, so
    # zeroing the minutes alone leaves `run_time_seconds` answering for a reset game.
    vpinfe_section(config).pop("run_time_seconds", None)
    persist_game_meta(game, config)
    game.meta_config = config
    return play_record(config)


def set_game_play_record(game: Game, *, play_count: int | None = None,
                         run_time_seconds: int | None = None,
                         last_played: int | None = None) -> dict[str, Any]:
    """Write a game's counters to given values. The migration case, and only that.

    Resetting is the correction somebody makes about their own library; this is what a
    library arriving from somewhere else needs, and the reset route's own note says so:
    setting a count to a number is the migration case and is not that.

    A field left out is left alone, so a source that knows a play count but not how long
    it ran does not silently zero the time. `RunTime` is the minutes VPX keeps and the
    seconds are the record, so both are written from the one number rather than one
    being derived later from the other.
    """
    config = load_game_meta(game)
    user = get_or_create_user_meta(config)
    if play_count is not None:
        user["StartCount"] = max(0, int(play_count))
    if last_played is not None:
        user["LastRun"] = int(last_played) or None
    if run_time_seconds is not None:
        seconds = max(0, int(run_time_seconds))
        user["RunTime"] = seconds // 60
        vpinfe_section(config)["run_time_seconds"] = seconds
    persist_game_meta(game, config)
    game.meta_config = config
    return play_record(config)


def reset_table_play_record(game: Game, filename: str) -> dict[str, Any]:
    """The same, for one table's own counters."""
    config = load_game_meta(game)
    entry = get_or_create_table_user(config, filename)
    entry["last_run"] = None
    entry["start_count"] = 0
    entry["run_time_seconds"] = 0
    persist_game_meta(game, config)
    game.meta_config = config
    return {"last_played": None, "play_count": 0, "play_time_seconds": 0,
            "tags": _tag_set(entry.get("tags") or [])}


def table_play_record(table: dict) -> dict[str, Any]:
    """One table's own play record. Counters only - a rating is entered rather than
    accumulated, so it sits beside this record and not in it. The last run is kept as an
    epoch and shown as the ISO stamp a game's is (`play_record`)."""
    from common.timestamps import epoch_to_iso

    user = table.get("user") or {}
    return {
        "last_played": epoch_to_iso(user.get("last_run")) or None,
        "play_count": int(user.get("start_count", 0) or 0),
        "play_time_seconds": int(user.get("run_time_seconds", 0) or 0),
    }

def _derived_table_tags(table: dict) -> list[str]:
    from common.games import derived_tags

    return derived_tags.table_tags(table)


def table_descriptor(table: dict, *, default_id: str = "") -> dict[str, Any]:
    """One table as both play lenses report it.

    A game folder holds several tables and each answers for itself, so this is the whole
    of what a table is on the wire rather than a few fields borrowed from its game. Built
    once here because the REST lens and the theme payload have to agree.

    The .vpx's `companyname`, `companyyear` and `playfieldvariant` are deliberately
    absent. Authors leave the company fields alone and VPSdb populates the game's, and
    `playfieldvariant` is a rendering mode rather than SS/EM - publishing it as `type`
    beside the game's `type` would put two unrelated meanings behind one word.
    """
    def parsed(key: str) -> str | None:
        """Null rather than "" so "not parsed" is distinct from "parsed as blank"."""
        return str(table.get(key, "") or "").strip() or None

    return {
        "id": str(table.get(TABLE_ID_KEY, "") or ""),
        "filename": entry_filename(table),
        "version": str(table.get("version", "") or ""),
        "rom": str(table.get("rom", "") or ""),
        # The sha256 of the .vpx. What lets two installs sharing a filesystem agree they
        # hold the same file without comparing paths, which differ by mount point.
        "file_hash": str(table.get("file_hash", "") or ""),
        "default": bool(default_id) and table.get(TABLE_ID_KEY) == default_id,
        "hidden": table.get("hidden") is True,
        # Top level, where the game's rating is too, so the two lenses read alike.
        "rating": table_rating(table),
        "release_date": parsed("release_date"),
        # The rest of what a parse takes. Declared in PARSED_KEYS and stored since
        # schema 2, projected nowhere until now - the sha256 of the script sidecar, and
        # and what the authoring tool recorded about the last save. Part of a table's
        # identity in the same way `file_hash` and `version` are.
        "vbs_hash": str(table.get("vbs_hash", "") or ""),
        "save_date": parsed("save_date"),
        "save_rev": parsed("save_rev"),
        "authors": table.get("authors") or [],
        "detects": {key.removeprefix("detect_"): bool(table.get(key, False))
                    for key in DETECTION_KEYS},
        "user": {**table_play_record(table), "tags": table_tags(table)},
        "derived_tags": _derived_table_tags(table),
        # What the user said about this table, against what was discovered. The game
        # carries its own three; these are the ones that govern a single file.
        #
        # The third one a table can hold is deliberately not here. It names a thing one
        # program does with one kind of hardware state, and a descriptor every app
        # answers is the wrong place to teach that. It stays settable where it is.
        "overrides": {
            "alt_launcher": str(table.get("alt_launcher", "") or ""),
            "plugin_profile": str(table.get("plugin_profile", "") or ""),
        },
    }


def game_frontend_dof_event(game: GameRecord) -> str:
    """The DOF effect a game asks for when selected, or "" to use the default."""
    meta = normalize_meta(getattr(game, "meta_config", {}))
    return str(vpinfe_section(meta).get("frontend_dof_event", "") or "").strip()


def declared_no_match(meta: dict[str, Any]) -> bool:
    """Whether somebody has said this machine is in no catalog.

    `alt_vpsid` null says so. Absent or empty says nothing, and the scan's answer stands.
    """
    vpinfe = vpinfe_section(meta)
    return "alt_vpsid" in vpinfe and vpinfe["alt_vpsid"] is None


def game_vps_id(game: GameRecord) -> str:
    return effective_vps_id(normalize_meta(getattr(game, "meta_config", {})))


def effective_vps_id(meta: dict[str, Any]) -> str:
    """The entry in force: a person's override, else `Info`'s, else ""."""
    if declared_no_match(meta):
        return ""
    alt_vpsid = str(vpinfe_section(meta).get("alt_vpsid", "") or "").strip()
    if alt_vpsid:
        return alt_vpsid
    return str(section(meta, "Info").get("VPSId", "") or "").strip()


def vps_matched_by(meta: dict[str, Any]) -> str:
    """`"user"` or `"import"` where a person made the match in force, else `""`.

    An override and a declared no-match are a person's whatever the record says.
    """
    vpinfe = vpinfe_section(meta)
    if declared_no_match(meta) or str(vpinfe.get("alt_vpsid", "") or "").strip():
        return MATCHED_BY_USER
    return info_matched_by(meta)


def info_matched_by(meta: dict[str, Any]) -> str:
    """How the entry in `Info` was made, whatever overrides it: `"user"`, `"import"`,
    or `""` for a guess and for no entry."""
    if not str(section(meta, "Info").get("VPSId", "") or "").strip():
        return ""
    said = vpinfe_section(meta).get(VPS_MATCHED_BY_KEY)
    return said if said in (MATCHED_ON_IMPORT, MATCHED_BY_USER) else ""


def record_vps_match(meta: dict[str, Any], matched_by: str) -> None:
    """Say how the entry about to be written to `Info` was chosen, `""` for a guess.

    Clears the override, which the new entry settles. Mutates `meta`.
    """
    vpinfe = meta.get(VPINFE_SECTION)
    if not isinstance(vpinfe, dict):
        vpinfe = meta[VPINFE_SECTION] = {}
    vpinfe["alt_vpsid"] = ""
    if matched_by:
        vpinfe[VPS_MATCHED_BY_KEY] = matched_by
    else:
        vpinfe.pop(VPS_MATCHED_BY_KEY, None)


def base_game_vps_id(game: GameRecord) -> str:
    return str(section(getattr(game, "meta_config", {}), "Info").get("VPSId", "") or "").strip()


def get_or_create_user_meta(config: dict[str, Any]) -> dict[str, Any]:
    user = config.setdefault("User", {})
    user.setdefault("Rating", 0)
    user.setdefault("Favorite", False)
    user.setdefault("LastRun", None)
    user.setdefault("StartCount", 0)
    user.setdefault("RunTime", 0)
    user.setdefault("Tags", [])
    return user


def run_time_seconds(meta: Any) -> int:
    """The play time recorded against a game, in seconds.

    Seeded from `User.RunTime` for a game last played before the seconds were kept, so
    an existing library carries its history forward instead of restarting at nothing.
    """
    vpinfe = vpinfe_section(meta)
    if "run_time_seconds" in vpinfe:
        return _as_int(vpinfe.get("run_time_seconds"))
    return _as_int(section(meta, "User").get("RunTime")) * 60


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def get_or_create_vpinfe_meta(config: dict[str, Any]) -> dict[str, Any]:
    """The section VPinFE owns, to write into. `vpinfe_section` normalizes and may copy,
    so it reads but does not write."""
    return config.setdefault(VPINFE_SECTION, {})


# What put an identity on a file. A closed set with no value meaning "I guessed": a
# caller that inferred one sends nothing and the file joins the manual queue.
CONFIRMED_BY_USER = "user"
SOURCE_KEY = "source"


def _listed(vps_file_id: str) -> dict[str, Any]:
    """The file the catalog lists under this id, or {} while it holds none."""
    from common.games.game_service import find_vps_record

    return find_vps_record(vps_file_id)


def table_source(entry: dict[str, Any] | None) -> dict[str, Any]:
    """Which upstream release a table is, as far as anything has established.

    Absent is the ordinary answer and means nobody has looked, not that the lookup
    failed. Those are different states and only a matcher can produce the second.
    """
    found = (entry or {}).get(SOURCE_KEY) or {}
    return dict(found) if isinstance(found, dict) else {}


def holds_base(base: dict[str, Any], filename: str, file_hash: str) -> bool:
    """Whether the table called `filename`, hashed `file_hash`, is the file a patched
    table's `base` says its patch was applied to.

    By hash where both sides have one, and by name only where either was never read: a
    name alone takes a newer file of the same name for the base.
    """
    digest = str(base.get("hash") or "")
    if digest and file_hash:
        return file_hash == digest
    return bool(filename) and filename == str(base.get("file") or "")


def made_from(entries: dict | None, filename: str) -> list[str]:
    """The tables in `entries` a patch made from the one called `filename`, by filename."""
    _found, own = entry_for_filename(entries, filename)
    held = str(own.get("file_hash") or "")
    return sorted(entry_filename(one) for one in (entries or {}).values()
                  if isinstance(one, dict) and one is not own
                  and isinstance(base := table_source(one).get("base"), dict)
                  and holds_base(base, filename, held))


def set_table_source(game: Game, filename: str, vps_file_id: str) -> dict[str, Any]:
    """Record that somebody says this table is that release, or that it is none.

    Merged into whatever `source` already holds rather than replacing it: a patched
    file's `base` and `patch` say how the bytes were made, and which upstream record
    they are is a different question about the same file.

    `confirmed_by` becomes `user` because that is what happened - a person picked from
    a list nothing ordered. An explicit re-match overrides a stronger basis on purpose;
    the ranking governs automatic writes, not somebody correcting one.
    """
    config = load_game_meta(game)
    entries = rekey_by_id(config.setdefault(TABLES_KEY, {}))
    config[TABLES_KEY] = entries
    found_id, entry = entry_for_filename(entries, filename)
    if not entry:
        found_id = new_id()
        entry = {TABLE_ID_KEY: found_id, TABLE_FILENAME_KEY: filename}
        entries[found_id] = entry

    source = dict(entry.get(SOURCE_KEY) or {})
    wanted = str(vps_file_id or "").strip()
    if wanted:
        ties.tie(source, wanted, CONFIRMED_BY_USER, _listed(wanted))
    else:
        # Unbinding takes back the claim and nothing else. A file we built still has a
        # base and a patch, and forgetting those would make it unrebuildable. What stays
        # is the person's answer, so a sweep does not tie the table again.
        ties.decline(source)
    entry[SOURCE_KEY] = source

    persist_game_meta(game, config)
    game.meta_config = config
    return dict(source)


def set_asset_source(game: Game, path: str, vps_file_id: str) -> dict[str, Any]:
    """Record that somebody says the file at this path is that VPS record, or take it
    back. The assets ledger's twin of `set_table_source`.

    No `host_item_id`: that rule governs what a caller may assert over HTTP, and a
    person pointing at a record is not claiming to know which of its files they hold.
    """
    from common.games.asset_origin import ASSETS_KEY

    config = load_game_meta(game)
    key = str(path or "").strip().replace("\\", "/").strip("/")
    if not key:
        raise ValueError(t("error.games.location_needed"))
    entries = config.setdefault(ASSETS_KEY, {})
    entry = entries.setdefault(key, {})

    source = dict(entry.get(SOURCE_KEY) or {})
    wanted = str(vps_file_id or "").strip()
    if wanted:
        ties.tie(source, wanted, CONFIRMED_BY_USER, _listed(wanted))
    else:
        # Unbinding takes back the claim and nothing else - the host that placed the
        # file and the hash it published are facts about the file, not about the match.
        ties.untie(source)
    if source:
        entry[SOURCE_KEY] = source
    else:
        # A line with no source is the person's own and the next scan lists it again,
        # so dropping it here and keeping an empty one come to the same file.
        entries.pop(key, None)

    persist_game_meta(game, config)
    game.meta_config = config
    return dict(source)


# What the entry describes, as opposed to which entry it is. Adopting rewrites the
# first and leaves the second alone: `Info.VPSId` is what VPS supplied, and the field a
# surface offers to revert a corrected match to. Overwriting it would destroy the
# baseline the undo needs.
DESCRIBED_BY_VPS = ("Title", "Manufacturer", "Year", "Type", "Themes", "IPDBId")

# Described by the entry like the rest, but stored outside `Info` - so it is diffed and
# adopted alongside them and written to its own block.
GUIDES_FIELD = "guides"


def contract_1_tutorial(config: Any) -> str:
    """The Pinball Primer guide's address, which is what `entry.tutorial` has meant.

    Primer only. Guarded by `tests/invariants/test_nothing_has_shipped.py`; contract 2
    serves the whole `guides` list.
    """
    from common.games.info_file import (
        GUIDE_TUTORIAL,
        GUIDES_KEY,
        PINBALL_PRIMER_PREFIX,
    )

    held = normalize_meta(config).get(GUIDES_KEY)
    for one in held if isinstance(held, list) else []:
        if not isinstance(one, dict) or one.get("kind") != GUIDE_TUTORIAL \
                or one.get("hidden"):
            continue
        said = str(one.get("url") or "").strip()
        if said.startswith(PINBALL_PRIMER_PREFIX):
            return said
    return ""


GUIDE_SOURCES = {
    "pinballprimer.github.io": "Pinball Primer",
    "tiltforums.com": "Tilt Forums",
}


YOUTUBE_WATCH = "https://www.youtube.com/watch?v={id}"


def guide_address(guide: dict[str, Any]) -> str:
    """Where `guide` opens: its url, or the video it names where it has none."""
    said = str(guide.get("url") or "")
    video = str(guide.get("youtube_id") or "")
    return said or (YOUTUBE_WATCH.format(id=video) if video else "")


def guide_source(guide: dict[str, Any]) -> str:
    """A display name for `guide`'s host, or the host itself."""
    address = guide_address(guide)
    return GUIDE_SOURCES.get(sites.host(address)) or sites.site_name(address)


def is_catalog_guide(guide: dict[str, Any]) -> bool:
    from common.games.info_migration import FROM_CATALOG, GUIDE_ORIGIN

    return guide.get(GUIDE_ORIGIN) == FROM_CATALOG


def guides_on_wire(config: Any, *, hidden: bool = False) -> list[dict[str, Any]]:
    """The stored guides, each with an address to open and where it lives. The hidden
    ones only where `hidden` asks for them."""
    from common.games.info_file import GUIDES_KEY
    from common.games.info_migration import FROM_CATALOG, GUIDE_ORIGIN

    held = normalize_meta(config).get(GUIDES_KEY)
    return [{**{k: v for k, v in one.items() if k != "vps_file_id"},
             "url": guide_address(one), "source": guide_source(one),
             GUIDE_ORIGIN: FROM_CATALOG if is_catalog_guide(one) else "user",
             "hidden": bool(one.get("hidden"))}
            for one in (held if isinstance(held, list) else [])
            if isinstance(one, dict) and (hidden or not one.get("hidden"))]


def merge_guides(held: list[Any], offered: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The stored list with the catalog's part of it replaced by `offered`."""
    fresh = {guide_address(one): one for one in offered}
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for one in held:
        if not isinstance(one, dict):
            continue
        address = guide_address(one)
        if address in seen:
            continue
        if not is_catalog_guide(one):
            out.append(one)
        elif address in fresh:
            out.append({**fresh[address], **({"hidden": True} if one.get("hidden") else {})})
        else:
            continue
        seen.add(address)
    for one in offered:
        address = guide_address(one)
        if address not in seen:
            out.append(one)
            seen.add(address)
    return out


def vps_details_differ(config: dict[str, Any],
                       vps_entry: dict[str, Any]) -> dict[str, tuple[Any, Any, bool]]:
    """Which described fields disagree with the entry, as {field: (ours, theirs, new)}.

    `new` is true where the record says nothing and the entry does, which is a gap being
    filled rather than a value being replaced.

    Empty for a game whose details came from the entry it is still matched to, which is
    every game that has never been re-matched.
    """
    from common.games.info_file import GUIDES_KEY, guides_from_vps, info_from_vps

    theirs = info_from_vps(vps_entry)
    ours = section(config, "Info")
    found = {}
    for field in DESCRIBED_BY_VPS:
        mine, yours = ours.get(field), theirs.get(field)
        if _as_said(mine) != _as_said(yours):
            found[field] = (mine, yours, not _as_said(mine))
    held = config.get(GUIDES_KEY)
    held = [one for one in (held if isinstance(held, list) else [])
            if isinstance(one, dict) and is_catalog_guide(one)]
    offered = guides_from_vps(vps_entry)
    if _guides_said(held) != _guides_said(offered):
        found[GUIDES_FIELD] = (held, offered, not held)
    return found


def _guides_said(guides: list[dict[str, Any]]) -> list[tuple[str, ...]]:
    """Compared by what each one points at, in no order. A title is not part of it: the
    migration stores a 2.x link untitled, and the catalog titles the same link."""
    return sorted((str(one.get("url") or ""), str(one.get("youtube_id") or ""),
                   str(one.get("kind") or ""))
                  for one in guides)


def _as_said(value: Any) -> Any:
    """Compared as a person reads them: 1992 and "1992" are the same year, and themes
    differ by what is in them rather than by the order VPS happens to list them."""
    if isinstance(value, list):
        return sorted(str(item).strip().lower() for item in value)
    return str(value if value is not None else "").strip().lower()


def adopt_vps_details(game: Game, vps_entry: dict[str, Any],
                      fields: Iterable[str] | None = None) -> dict[str, Any]:
    """Make the named details describe the entry the game is matched to.

    `fields` None takes everything that differs. A name that does not differ is
    ignored rather than refused, so a caller may send what it was shown.
    """
    config = load_game_meta(game)
    fresh = vps_details_differ(config, vps_entry)
    wanted = set(fresh) if fields is None else (set(fields) & set(fresh))
    from common.games.info_file import GUIDES_KEY

    info = config.setdefault("Info", {})
    for field in wanted:
        theirs = fresh[field][1]
        if field == GUIDES_FIELD:
            config[GUIDES_KEY] = merge_guides(config.get(GUIDES_KEY) or [], theirs)
        elif theirs in ("", [], None) and field == "IPDBId":
            info.pop(field, None)
        else:
            info[field] = theirs
    dissolve_agreed_overrides(config)
    persist_game_meta(game, config)
    game.meta_config = config
    return {field: fresh[field][1] for field in wanted}


def dissolve_agreed_overrides(config: dict[str, Any]) -> list[str]:
    """Drop every override that now says what `Info` says, and name the ones dropped.

    Mutates `config`.
    """
    vpinfe = vpinfe_section(config)
    info = section(config, "Info")
    gone = []
    for field, (ours, theirs) in GAME_OVERRIDES.items():
        if ours not in vpinfe:
            continue
        if _as_said(vpinfe.get(ours)) == _as_said(info.get(theirs)):
            vpinfe.pop(ours)
            gone.append(field)
    return gone


def get_or_create_table_user(config: dict[str, Any], native: str) -> dict[str, Any]:
    """One entry's play record, created on its first launch.

    `native` is what names the entry within its game - a filename for something in the
    folder, `app:key` for something with no file. Matched on that rather than on the
    filename alone: an entry that has no file matches no filename, and inventing one for
    it adds a second, phantom entry on every launch.

    The create is for files only, and it is safe that it is: an entry with no file
    cannot be launched before it has been added, so it is always already here.

    The counters are written here; `rating` is written by `set_table_rating` and is the
    only entered value in the block. Favorite is still in the design with no producer,
    and storing a field nothing fills invites a reader to trust it.
    """
    entries = rekey_by_id(config.setdefault(TABLES_KEY, {}))
    config[TABLES_KEY] = entries
    found_id, entry = next(
        ((i, e) for i, e in entries.items()
         if isinstance(e, dict) and entry_native_key(e) == native), ("", {}))
    if not entry:
        found_id = new_id()
        entry = {TABLE_ID_KEY: found_id, TABLE_FILENAME_KEY: native}
        entries[found_id] = entry
    user = entry.setdefault("user", {})
    user.setdefault("last_run", None)
    user.setdefault("start_count", 0)
    user.setdefault("run_time_seconds", 0)
    return user


def load_game_meta(game: Game) -> dict[str, Any]:
    meta_path = info_files.info_path_of(game)
    if meta_path.exists():
        return normalize_meta(MetaConfig(str(meta_path)).data)
    return normalize_meta(getattr(game, "meta_config", {}))


def persist_game_meta(game: Game, config: dict[str, Any]) -> None:
    meta_file = MetaConfig(str(info_files.info_path_of(game)))
    upgraded = meta_file.pending_migration
    meta_file.data = config
    meta_file.write_config()
    game.meta_config = config
    # Both flags were read during the scan, and this write is what makes them wrong.
    # Nothing is pending once the file is on disk, and an upgrade has just left a
    # restore point behind it. Without this the id backfill upgrades the whole library
    # at startup and every loaded game still claims it needs upgrading - which is what
    # the Manager then reports, for as long as the process lives.
    game.info_pending_upgrade = False
    if upgraded:
        game.info_restorable = True


def keep_game_meta(game: Game, config: dict[str, Any]) -> None:
    """`persist_game_meta` for what VPinFE works out on its own. Where the folder refuses
    the write, the game holds `config` until VPinFE restarts; a person's edit uses
    `persist_game_meta`, which raises."""
    try:
        persist_game_meta(game, config)
    except Exception as exc:
        unwritten.hold(info_files.info_path_of(game), config, exc)
        game.meta_config = config


def write_held_meta() -> None:
    """Write each .info VPinFE could not, where its folder now allows it."""
    for info in unwritten.infos():
        config = unwritten.held(info)
        if config is None:
            continue
        try:
            MetaConfig(info).write_config()
        except Exception as exc:
            unwritten.hold(info, config, exc)
