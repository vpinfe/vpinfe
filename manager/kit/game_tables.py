"""How a game and a table are named and shown, in one vocabulary.

Do not reuse `media_ownership.py`'s nouns here: "This table" and "This game" are about
which media file wins, and borrowing them would overload a vocabulary that is correct.
"""

from __future__ import annotations

import os
import re
from typing import Any

from common.i18n import t

JOIN = " · "
_WORD = re.compile(r"[^\W_]+")

# The groups a fact belongs to, spelled once: the panel draws them as headings and the
# grid's built-in views are named for them, so crossing between the two is not a
# translation. Only the groups that have rows.
# "Table File", not "File": a panel that also shows a table ini, a script and a rom
# left a reader asking which file. Named alongside `Default Table` below.
FILE = t("manager.game_tables.table_file")
FEATURES = t("manager.game_tables.features")
# What a keyed entry's group is called where the file group would be. There is no file,
# so "Table File" would be a heading over a blank - and the thing it does have is the
# name its program knows it by.
KNOWN_AS = t("manager.game_tables.known")
# A referenced entry's group. Not "Table File", which reads as a file of this game's -
# the whole point of one of these is that the file belongs somewhere else.
ELSEWHERE = t("manager.game_tables.where")

# What a reference is doing right now. Notable first, like every pair here. **Not
# Missing** - nothing is lost when a share has not mounted, and the word that tells
# somebody a file was deleted is the wrong word for a location being away.
REACH_WORDS = (t("word.unreachable"), t("manager.game_tables.reachable"))
LAUNCH = t("manager.game_tables.launch")
PLAY = t("manager.game_tables.play")
FRONTEND = t("manager.game_tables.frontend")

# A collection's row held to one table, and what that costs, said on the word.
LOCKED_WORDS = (t("manager.game_tables.locked"), t("manager.game_tables.locked.help"))
# A row naming a game or a table the library does not hold.
GONE_WORDS = (t("manager.game_tables.missing"), t("manager.game_tables.not_library"))


def native_key(table: dict[str, Any] | None) -> str:
    """What names this entry to the install: its filename, or `app:key` where it has no
    file. The one string the launch path, the API and this surface all speak."""
    held = table or {}
    key = str(held.get("key") or "").strip()
    if key:
        return f"{str(held.get('app') or '').strip()}:{key}"
    stored = held.get("reference") or {}
    # A list row carries the stored path as a string; one game's table, the record.
    reference = str(stored if isinstance(stored, str) else stored.get("path") or "").strip()
    return reference or str(held.get("filename") or "")


def is_keyed(table: dict[str, Any] | None) -> bool:
    """Whether this entry has no file. Read off `form`, which the install derives."""
    return str((table or {}).get("form") or "") == "keyed"


def is_referenced(table: dict[str, Any] | None) -> bool:
    """Whether this entry's file is somewhere other than the game folder."""
    return str((table or {}).get("form") or "") == "referenced"


def can_swap(table: dict[str, Any] | None) -> bool:
    """Whether this entry's file can be replaced or deleted: it is in the game folder
    and there."""
    return (str((table or {}).get("form") or "") == "contained"
            and bool((table or {}).get("available")) and not (table or {}).get("absent_since"))


def can_forget(table: dict[str, Any] | None) -> bool:
    """Whether this entry's record can be dropped: anything the game folder does not hold.
    A table that is there only comes back at the next scan."""
    return bool((table or {}).get("absent_since")) or is_keyed(table) or is_referenced(table)


# How a game's default was decided, said as what happens next rather than who acted.
# Locked is the word a collection row held to one table wears, and means the same.
CHOSEN = "user"
DERIVED = "auto"

DEFAULT_WORDS = {
    CHOSEN: (t("manager.game_tables.locked"),
             t("manager.game_tables.locked_default.help")),
    DERIVED: (t("manager.game_tables.automatic"),
              t("manager.game_tables.automatic.help")),
}

# One name and one direction per fact, read by the column, the panel, the funnel and the
# row menu. Notable is first; docs/conventions.md has why.
HIDDEN_WORDS = (t("word.hidden"), t("manager.game_tables.offered"))
# What a grid row holds where a game or table is hidden or private, and "" where it is not:
# the grid draws the token as its word.
HIDDEN_CELL, PRIVATE_CELL = "hidden", "private"
FILE_WORDS = (t("manager.game_tables.missing"), t("word.present"))
# Which script runs, not how the file got there: VPX loads a `<table>.vbs` sidecar in
# place of the one inside the .vpx, so the sidecar is an override and "Extracted" named
# only its provenance. External is the notable half - it is the table running something
# other than what its author shipped.
SCRIPT_WORDS = (t("manager.game_tables.external"), t("manager.game_tables.internal"))
# Whether every required asset resolves. Notable first, like the pairs above, because
# the ordinary table runs - a word on every row that says so tells a reader nothing.
# Three-valued, so the unknown has its own word: a table nothing has parsed cannot be
# called ready and has not been found wanting either.
LAUNCH_WORDS = (t("manager.game_tables.blocked"), t("word.ready"))
# What a game's match to VPS is: made, deliberately declined, or not made.
VPS_MATCHED, VPS_CLEARED, VPS_UNMATCHED = "matched", "cleared", "unmatched"
VPS_SHARED = "shared"
ASSET_PRESENT, ASSET_MISSING = "present", "missing"


def vps_match(game: dict[str, Any]) -> str:
    """The token for a game's match to VPS. A cleared match is the user saying there is
    none, which `alt_vps_id` holds as null."""
    if game.get("vps_id"):
        return VPS_MATCHED
    if (game.get("overrides") or {}).get("alt_vps_id", "") is None:
        return VPS_CLEARED
    return VPS_UNMATCHED


# Whether a game is kept from every community service. Private is the notable half.
COMMUNITY = t("manager.game_tables.community")
PRIVATE_WORDS = (t("manager.game_tables.private"), t("manager.game_tables.not_private"))
PRIVATE_HELP = t("manager.game_tables.private.help")
# How the match in force was made, by the token `how_matched` gives a row.
HOW_MATCHED = {"auto": t("manager.game_tables.auto_matched"),
               "import": t("manager.game_tables.match_import"),
               "user": t("manager.game_tables.match_user")}


def how_matched(game: dict[str, Any]) -> str:
    """`auto`, `import` or `user` for the match in force, `""` where there is none."""
    if not game.get("vps_id"):
        return ""
    return str(game.get("vps_matched_by") or "") or "auto"


def mod_of_said(mod: dict[str, Any] | None) -> str:
    """What a mod is based on, `""` for a release that is not one.

    The release VPS links it to, with its game only where that is another; VPS's note
    where there is no link; Unknown where there is neither.
    """
    if not mod:
        return ""
    if mod.get("vps_file_id"):
        made_by = ", ".join(str(name) for name in (mod.get("authors") or [])[:3])
        told = JOIN.join(part for part in (str(mod.get("game") or ""),
                                           str(mod.get("version") or ""), made_by) if part)
        return told or t("manager.workbench.no_version")
    return str(mod.get("note") or "") or t("word.unknown")


def mod_line(mod: dict[str, Any] | None) -> str:
    """`mod_of_said` as a line of its own. VPS's note follows Mod rather than Mod of,
    because it describes the mod far more often than it names what the mod is of."""
    if not mod:
        return ""
    if not mod.get("vps_file_id") and mod.get("note"):
        return t("manager.game_tables.mod_noted", note=mod_of_said(mod))
    return t("manager.game_tables.mod_of", told=mod_of_said(mod))


def release_facts(release: dict[str, Any], *, features: bool = True) -> list[str]:
    """That a release is a mod and of what, then the rest of its features.

    `features` false stops at the mod, for a list where the features would only crowd it.
    """
    listed = [str(one) for one in (release.get("features") or [])]
    facts: list[str] = []
    mod = release.get("mod_of")
    if mod:
        facts.append(mod_line(mod))
    elif "MOD" in listed:
        facts.append(t("manager.games.mod"))
    if features:
        facts.extend(one for one in listed if one != "MOD")
    return facts


def made_from_it(table: dict[str, Any], tables: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The tables in `tables` a patch made from `table`."""
    own = str(table.get("id") or "")
    return [one for one in tables if own and one is not table
            and str(((one.get("source") or {}).get("base") or {}).get("table_id") or "") == own]


def made_from_it_line(names: list[str]) -> str:
    """Those tables named, as the line said before their base goes, or `""` for none."""
    return t("manager.game_tables.derived", names=", ".join(names)) if names else ""


# The label, not the state - "Default" alone left a reader asking "default what?" on a
# panel that also has a default launcher and a default view. Named here so the grid and
# the workbench cannot answer it differently.
DEFAULT_LABEL = t("manager.game_tables.default_table")
LAUNCH_UNKNOWN = t("word.unknown")


def word_for(pair: tuple[str, str], notable: bool) -> str:
    """A fact's own word for the state it is in, notable first. A helper for a one-line
    lookup because the one line is where it goes wrong: `pair[not present]` at a call
    site put "Missing" on a file that was on disk."""
    return pair[0] if notable else pair[1]


def year_of(value: Any) -> int | None:
    """A year as the number a grid sorts and filters on, or None where there is none.

    Catalog and `.info` years arrive as a string, a number or nothing, and the odd one as
    a range ("1995,1997"), which is read as the year it starts at. Anything without four
    digits in it is no year.
    """
    found = re.search(r"(?<!\d)\d{4}(?!\d)", str(value or ""))
    return int(found.group()) or None if found else None


def made(row: dict[str, Any]) -> str:
    """Who made the game and when: manufacturer then year, blank parts left out. The
    first half of the line under a table's name; `table_name` is the second."""
    return " ".join(part for part in (str(row.get(key) or "").strip()
                                      for key in ("manufacturer", "year")) if part)


def table_name(table: dict[str, Any]) -> str:
    """Which table this is: the release it is matched to, else the file's own version and
    authors, else the file name. Presentation only; nothing is written over the file's data."""
    return _release_name(table) or _version_and_author(table) or _file_of(table)


def release_version_and_authors(table: dict[str, Any]) -> tuple[str, str] | None:
    """The version and authors of the release this table is matched to, or None where it
    has no match or the release lists neither.

    The version is the one VPS listed when the table was matched: the file is that
    version, and an update is measured from it. A release the catalog no longer holds
    names nothing, so such a table is named by its file.
    """
    source = table.get("source") or {}
    if not source.get("vps_file_id"):
        return None
    authors = ", ".join(str(a) for a in (source.get("authors") or []) if str(a).strip())
    listed = str(source.get("version") or "").strip()
    version = str(source.get("vps_version") or "").strip() or listed
    if not (listed or authors):
        return None
    return version, authors


def _release_name(table: dict[str, Any]) -> str:
    named = release_version_and_authors(table)
    return JOIN.join(part for part in (named or ()) if part)


def named_version_and_authors(table: dict[str, Any]) -> tuple[str, str]:
    """The version and authors a table is named by: the release's, else the file's. Both
    from one place, never one of each."""
    named = release_version_and_authors(table)
    if named is not None:
        return named
    return _file_version(table), _file_authors(table)


def file_says(table: dict[str, Any]) -> str:
    """What the file itself reads as, where the table is named by something else and
    that differs; "" where they agree or the file reads as nothing."""
    own = _version_and_author(table)
    return own if own and own != table_name(table) else ""


def named_in(row: dict[str, Any], tables: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """The line under a file's game that says which table it is for, as the Tables grid
    draws it: `table_name` of the table the row names, among `tables` by id.

    A file named for a table that is not here has no table to read a version from, so the
    stem it is named for stands as a file name. A file of the game's own names none.
    """
    table = tables.get(str(row.get("table") or ""))
    if table is not None:
        return {"said_built": table_name(table), "said_built_file": names_a_file(table)}
    stem = str(row.get("table_file") or "")
    return {"said_built": stem, "said_built_file": bool(stem)}


def scope_of(row: dict[str, Any]) -> str:
    """`table` for a file named for one table, `game` for a file of the game's own."""
    return "table" if row.get("table") or row.get("table_file") else "game"


def told_apart(tables: list[dict[str, Any]]) -> dict[str, str]:
    """By id, what to add after the name of each of `tables` that reads the same as
    another: the run of its filename holding the words the rest do not all share."""
    same: dict[str, list[dict[str, Any]]] = {}
    for one in tables:
        same.setdefault(table_name(one), []).append(one)
    added: dict[str, str] = {}
    for group in (group for group in same.values() if len(group) > 1):
        stems = [os.path.splitext(_file_of(one))[0] for one in group]
        shared = set.intersection(*({word.casefold() for word in _WORD.findall(stem)}
                                    for stem in stems))
        for one, stem in zip(group, stems, strict=True):
            differ = [word for word in _WORD.finditer(stem)
                      if word.group().casefold() not in shared]
            if differ:
                added[str(one.get("id") or "")] = stem[differ[0].start():differ[-1].end()]
    return added


def name_among(table: dict[str, Any], tables: list[dict[str, Any]]) -> str:
    """`table_name`, and what `told_apart` adds to it among its game's `tables`."""
    differs = told_apart(tables).get(str(table.get("id") or ""))
    return JOIN.join((table_name(table), differs)) if differs else table_name(table)


def _file_of(table: dict[str, Any]) -> str:
    # `reference` is a path string in a grid row and an object in the game's own read.
    reference = table.get("reference")
    path = str((reference.get("path") if isinstance(reference, dict) else reference) or "")
    return (str(table.get("filename") or "") or str(table.get("key") or "")
            or re.split(r"[\\/]", path)[-1])


def _stem(name: str) -> str:
    return os.path.splitext(re.split(r"[\\/]", name)[-1])[0].lower()


def named_as_folder(table: dict[str, Any] | None, game: dict[str, Any]) -> bool:
    """Whether the table's file carries its folder's name, in any case."""
    folder = re.split(r"[\\/]", str(game.get("folder") or "").rstrip("\\/"))[-1]
    return table is not None and bool(folder) and _stem(_file_of(table)) == folder.lower()


def _script(table: dict[str, Any]) -> dict[str, Any]:
    return (table.get("assets") or {}).get("script") or {}


def runs_its_own_script(table: dict[str, Any]) -> bool:
    """Whether the .vbs the table runs carries the table's own name."""
    script = str(_script(table).get("file") or "")
    return bool(script) and _stem(script) == _stem(_file_of(table))


def shares_its_script(table: dict[str, Any]) -> bool:
    """Whether the .vbs the table runs is the folder's, run by every table without one."""
    return _script(table).get("resolution") == "shared"


def usual_launcher(tables: list[dict[str, Any]]) -> str:
    """The launcher a game plays with: its default table's, or "" with no default."""
    default = next((one for one in tables if one.get("default")), None)
    return str((default or {}).get("launcher") or "")


def names_a_file(table: dict[str, Any]) -> bool:
    """Whether `table_name` fell back to a file, whose end is the part worth keeping
    when there is no room for all of it."""
    return not (_release_name(table) or _version_and_author(table))


def _file_authors(table: dict[str, Any]) -> str:
    authors = table.get("authors")
    if isinstance(authors, list):
        return ", ".join(str(a) for a in authors if str(a).strip())
    return str(table.get("author") or "").strip()


def _file_version(table: dict[str, Any]) -> str:
    return str(table.get("version") or "").strip()


def _version_and_author(table: dict[str, Any]) -> str:
    return JOIN.join(part for part in (_file_version(table), _file_authors(table)) if part)


def offered(table: dict[str, Any]) -> bool:
    """Whether the frontend can offer this table: not hidden, and its file not gone."""
    return not table.get("hidden") and not table.get("absent_since")


def why_not_default(table: dict[str, Any]) -> str:
    """Why this table cannot be made its game's default, or "" where it can."""
    if table.get("absent_since"):
        return t("manager.workbench.default_absent")
    return t("manager.game_tables.hidden_blocked") if table.get("hidden") else ""


def hidden_hint(table: dict[str, Any], tables: list[dict[str, Any]]) -> str:
    """What hiding this table does, `tables` being the whole game's."""
    others = (row for row in tables if row.get("id") != table.get("id"))
    if offered(table) and not any(offered(row) for row in others):
        return t("manager.game_tables.hidden_last.help")
    return t("manager.game_tables.hidden.help")


def hidden_said(game: str, table: dict[str, Any], answer: dict[str, Any] | None, *,
                hidden: bool) -> str:
    """The notification once `table` is hidden or offered again. `table` is the row as it
    was, and `answer` what the write returned: the table and the game's default after."""
    now = (answer or {}).get("default")
    if not hidden:
        if now and now.get("id") == table.get("id"):
            return t("manager.game_tables.result_offered", game=game, table=table_name(now))
        return HIDDEN_WORDS[1]
    if not now:
        return t("manager.game_tables.hidden_none_left", game=game)
    if not table.get("default"):
        return HIDDEN_WORDS[0]
    key = ("manager.game_tables.result_both"
           if (table.get("default_kind") or "") == CHOSEN
           else "manager.game_tables.result_hidden")
    return t(key, game=game, table=table_name(now))


def now_plays(game: str, table: dict[str, Any]) -> str:
    """The notification once `table` is made its game's default."""
    return t("manager.game_tables.now_plays", game=game, table=table_name(table))


def lock_act(table: dict[str, Any],
             tables: list[dict[str, Any]]) -> tuple[bool, str] | None:
    """What the default row offers - True to lock it, False to unlock it - and the words
    for that, or None where it offers neither. `tables` is the whole game's."""
    if not table.get("default"):
        return None
    if (table.get("default_kind") or "") == CHOSEN:
        goes = next((row for row in tables if row.get("automatic")), None)
        if goes and goes.get("id") != table.get("id"):
            return False, t("manager.game_tables.unlock_to", table=table_name(goes))
        return False, t("manager.game_tables.unlock")
    return (True, t("manager.game_tables.lock")) if len(tables) > 1 else None


def is_locked(tables: list[dict[str, Any]]) -> bool:
    """Whether the game plays a table somebody chose, rather than the newest one."""
    return any(row.get("default") and (row.get("default_kind") or "") == CHOSEN
               for row in tables)


def automatic_table(tables: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The table the game plays with no choice recorded."""
    return next((row for row in tables if row.get("automatic")), None)


def fell_from(tables: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The table somebody locked that can no longer be played, so another is the default."""
    return next((row for row in tables if row.get("locked") and not row.get("default")), None)


def file_line(table: dict[str, Any], game: str) -> str:
    """The file's name under the table's, without the game's own name where it begins
    with it, and "" where the table is named by its file already."""
    if names_a_file(table):
        return ""
    name = _file_of(table)
    game = game.strip()
    if game and name[:len(game)].casefold() == game.casefold():
        rest = name[len(game):].lstrip(" -–_·")
        if os.path.splitext(rest)[0]:
            return rest
    return name


def locked_said(game: str, table: dict[str, Any]) -> str:
    """The notification once `table` is chosen as its game's default."""
    return t("manager.game_tables.result_locked", game=game, table=table_name(table))


def automatic_said(game: str, after: list[dict[str, Any]]) -> str:
    """The notification once the game is back on Automatic, `after` being its tables as
    the write left them."""
    now = next((row for row in after if row.get("default")), None)
    if now is None:
        return t("manager.game_tables.automatic")
    return t("manager.game_tables.result_automatic", game=game, table=table_name(now))


def lock_said(game: str, table: dict[str, Any], after: list[dict[str, Any]], *,
              lock: bool) -> str:
    """The notification once `table` is locked or unlocked, `after` being the game's
    tables as the write left them."""
    if lock:
        return t("manager.game_tables.locked_to", table=table_name(table))
    now = next((row for row in after if row.get("default")), None)
    if now and now.get("id") != table.get("id"):
        return t("manager.game_tables.result_unlocked", game=game, table=table_name(now))
    return t("manager.game_tables.unlocked")
