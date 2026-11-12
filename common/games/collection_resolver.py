"""One collection, resolved to the ordered list a frontend navigates.

Five steps, and the same answer whoever asks - the wheel and the API both arrive here:

    1. membership   filters, then members overriding them, then exclusions
    2. visibility   drop hidden and absent - library-wide, and it beats a named table
    3. selection    the table a member named, or the game's default
    4. order        the member array, or a computed sort
    5. limit        keep the first N rows, if the collection caps itself

Both callers are named above because for ten days only one of them was true. The wheel
went through a second engine that read game ids and nothing else, so it could not see a
member naming a table, an exclusion, a limit or a stored order - and it matched "Last
Played" by string literal. A collection meant one thing over REST and another on the
surface a player actually uses.

An entry is a table with its game attached. Media is resolved by whoever serializes it,
because the play lens and the management lens want different amounts of it.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from common.games import collation, collection_filters, rankings
from common.games.collection_store import MANUAL_ORDER, CollectionStore
from common.games.game import GameRecord, ScannedGame
from common.games.game_identity import game_id
from common.games.game_metadata import (
    game_rating,
    game_title,
    game_year,
    run_time_seconds,
    section,
    vpinfe_section,
)
from common.games.play_numbers import PlayNumbers
from common.games.tables import (
    TABLE_FILENAME_KEY,
    TABLE_ID_KEY,
    entry_filename,
    offerable,
    offered_tables,
    recorded_default,
    table_entries,
)
from common.timestamps import iso_to_epoch

MEMBER_GAME_KEY = "game"
MEMBER_TABLE_KEY = "table"

DEFAULT_ORDER = "title"


class UnresolvableCollectionError(Exception):
    """A collection this build cannot answer for, naming why.

    Raised rather than resolved-around: dropping a criterion we do not understand
    answers a different question, and does it silently.
    """

    def __init__(self, name: str, axes: Iterable[str]) -> None:
        self.name = name
        self.axes = list(axes)
        super().__init__(
            f"Collection {name!r} filters on {', '.join(self.axes)}, which this build "
            f"does not know. Update VPinFE to use it.")


@dataclass(frozen=True)
class Entry:
    """One row of the resolved list: a table, with the game it belongs to."""

    game: Any
    table: dict
    siblings: int

    @property
    def table_id(self) -> str:
        return str(self.table.get(TABLE_ID_KEY, "") or "")

    @property
    def filename(self) -> str:
        return entry_filename(self.table)

    # A device's list is entries rather than games, and the frontend's sorts read these
    # off whatever they are handed. Forwarding beats teaching every sort both shapes.
    @property
    def meta_config(self) -> dict[str, Any] | None:
        return getattr(self.game, "meta_config", {})

    @property
    def creation_time(self) -> float | None:
        return getattr(self.game, "creation_time", None)

    # The folder name is the last fallback for a title, so a game whose .info names no
    # Title sorted as "" when the wheel re-sorted entries rather than games.
    @property
    def game_dir_name(self) -> str | None:
        return getattr(self.game, "game_dir_name", None)


def _user_value(game: GameRecord, key: str, fallback: int = 0) -> int:
    try:
        return int(section(getattr(game, "meta_config", {}), "User").get(key, fallback) or 0)
    except (TypeError, ValueError):
        return fallback


def visible_entries(game: ScannedGame) -> list[dict]:
    """A game's offerable tables, the default first and the rest by name.

    Default first is what makes a member naming only a game deterministic: it takes the
    head of this list, and that is the game's own choice rather than directory order.
    """
    entries = table_entries(getattr(game, "meta_config", {}))
    # No entries at all is a folder no build has parsed, and the scan's .vpx stands in
    # for it. Entries that are all unofferable is a different thing - the tables are
    # known and withheld - so falling back there would hand back the very table that
    # was hidden, or one whose file is gone.
    if not entries:
        return _unparsed_entry(game)
    recorded = recorded_default(vpinfe_section(getattr(game, "meta_config", {})))
    return [entry for _id, entry in offered_tables(entries, recorded)]


def _unparsed_entry(game: ScannedGame) -> list[dict]:
    """The .vpx the scan found, for a game nothing has parsed yet.

    Its .info has no tables section - a folder that has never been through a metadata
    build, which is the normal state of a freshly added game. It is still launchable
    and the frontend has always offered it, so it gets an entry with no id rather than
    disappearing until somebody runs a scan. The id arrives with the parse.
    """
    path = str(game.full_path_vpx_file or "").strip()
    if not path:
        return []
    return [{TABLE_ID_KEY: "", TABLE_FILENAME_KEY: os.path.basename(path)}]


def _named_table(game: GameRecord, table_id: str) -> dict | None:
    """The table a member named, unless it is hidden or its file is gone. Both are
    library-wide and beat a member: `hidden` exists so a patch base can stay on disk
    without being playable, and an absent file has nothing to launch."""
    entry = table_entries(getattr(game, "meta_config", {})).get(table_id)
    if not isinstance(entry, dict) or not offerable(entry):
        return None
    return entry


def _excluded(refs: Iterable[dict]) -> tuple[set[str], set[str]]:
    """(whole games, individual tables) named by a collection's exclusions."""
    games = {r[MEMBER_GAME_KEY] for r in refs if MEMBER_TABLE_KEY not in r}
    tables = {r[MEMBER_TABLE_KEY] for r in refs if MEMBER_TABLE_KEY in r}
    return games, tables


# Any because the key's type is the field's: a title sorts as a tuple, a rating as an
# int, a timestamp as a float, and there is no useful supertype a sort would accept.
def _primary_key(order_by: str) -> Callable[[GameRecord], Any]:
    """The value a sort orders on, ascending in every case. Negating one here would put
    the field back to largest-first whatever `order.direction` says, which is the bug
    this shape exists to prevent - direction is applied once, by `_ordered`.

    Takes a game, or an entry, which forwards what these read. One key, both callers."""
    if order_by == "year":
        return lambda game: str(game_year(game))
    if order_by == "rating":
        return lambda game: game_rating(game)
    if order_by == "added":
        return lambda game: getattr(game, "creation_time", 0) or 0
    if order_by in PLAY_ORDERS:
        return _play_key(order_by)
    return lambda game: collation.sort_key(game_title(game))


PLAY_ORDERS = ("play_time_seconds", "last_played", "play_count")


def _play_key(order_by: str) -> Callable[[Any], Any]:
    """The key for an order on play numbers, whose they are being the setting's answer.

    The whole game's read the game, as they always did. The others read the table of
    the entry handed over, or the game's default where it is handed a game, so a game
    offering two tables can sit in two places in a list of tables.
    """
    plays = PlayNumbers()
    if plays.whole_game:
        if order_by == "play_time_seconds":
            # The seconds, not User.RunTime: ordering on the minutes ties every game with
            # under a minute on it at zero, which is most of a library that gets browsed.
            return lambda game: run_time_seconds(getattr(game, "meta_config", {}) or {})
        stored = {"last_played": "LastRun", "play_count": "StartCount"}[order_by]
        return lambda game: _user_value(game, stored)

    def shown(item: Any) -> Any:
        game = getattr(item, "game", item)
        numbers = plays.counters(getattr(item, "meta_config", {}) or {},
                                 str(getattr(game, "full_path_game", "") or ""),
                                 getattr(item, "table", None)) or {}
        if order_by == "last_played":
            return iso_to_epoch(numbers.get("last_played")) or 0
        return int(numbers.get(order_by, 0) or 0)

    return shown


def _tiebreak(entry: Entry) -> tuple:
    """Title then table id, so a limited collection cuts the same rows every time."""
    return (game_title(entry.game).lower(), entry.table_id)


def _ordered(entries: list, order_by: str, descending: bool = False) -> list:
    """`entries` sorted in place, tiebreak first and then the field, both stable.

    The tiebreak does not turn around with the field. Two games level on rating stay in
    title order in a descending list, which is the sort `frontend.game_state.apply_sort`
    has always applied - reversing the finished list would order them Z to A instead.

    `descending` is set aside for a ranked view.
    """
    entries.sort(key=_tiebreak)
    if rankings.is_token(order_by):
        entries.sort(key=rankings.rank_key(order_by))
        return entries
    key = _primary_key(order_by)
    if order_by in PLAY_ORDERS and not PlayNumbers().whole_game:
        entries.sort(key=key, reverse=descending)
    else:
        entries.sort(key=lambda entry: key(entry.game), reverse=descending)
    return entries


def order_games(games: list, order_by: str, descending: bool = False) -> list:
    """`games` in the order a collection would put them, sorted in place.

    The wheel re-sorts its entries through this one implementation: one set of keys and
    one direction rule. A ranked order reads an entry's own table, and a game's default.

    `manual` means the array is already the order, so it is left alone.
    """
    if order_by == MANUAL_ORDER:
        return games
    games.sort(key=lambda game: collation.sort_key(game_title(game)))
    if rankings.is_token(order_by):
        games.sort(key=rankings.rank_key(order_by))
        return games
    games.sort(key=_primary_key(order_by), reverse=descending)
    return games


@dataclass(frozen=True)
class Holding:
    """What a collection holds, and how: the games it resolves to, capped by its limit,
    and each bucket they came from before the cap."""

    games: list[Any]
    added: list[Any]
    matched: list[Any]
    excluded: int
    # Game id to the tables the rule matched by their own values. A game that matched
    # itself is not in it.
    tables: dict[str, tuple[str, ...]] = field(default_factory=dict)


def _ruled(stored_filters: dict, game: Any, entries: list[dict],
           per_table: bool) -> list[dict] | None:
    """What a rule takes of one game: None for nothing, [] for the game itself, or the
    tables among `entries` whose own values it matched."""
    if collection_filters.matches(stored_filters, game):
        return []
    if not per_table:
        return None
    return [entry for entry in entries
            if collection_filters.matches(stored_filters, game, entry)] or None


def resolve_games(name: str, collections: CollectionStore, games: list[Any]) -> list[Any]:
    """The games a collection contains, for the management lens. See `holding`."""
    return holding(name, collections, games).games


def holding(name: str, collections: CollectionStore, games: list[Any]) -> Holding:
    """The games a collection contains, for the management lens.

    Not the play lens: a game whose only .vpx is hidden, or which has none at all,
    still belongs to the collection. It produces no entry - there is nothing to
    launch - but hiding it from the list you would go to in order to fix it is how a
    library grows a game nobody can find.

    Same membership, ordering and limit as `resolve`, so the two lenses cannot disagree
    about what a collection holds or what order it is in. A game the limit cuts is not
    in the collection - the cap is part of the definition, not a display setting.
    """
    unknown = collections.unknown_filter_axes(name)
    if unknown:
        raise UnresolvableCollectionError(name, unknown)

    by_id: dict[str, Any] = {}
    for game in games:
        found = game_id(game)
        if found:
            by_id.setdefault(found, game)

    member_refs = collections.get_member_refs(name)
    named = {ref[MEMBER_GAME_KEY] for ref in member_refs}
    stored_filters = collections.get_filters(name) or {}
    dropped_games, dropped_tables = _excluded(collections.get_excluded_refs(name))

    picked, seen = [], set()

    def _add(game: Any) -> None:
        # By object, for the reason `resolve._take` gives: an unscanned game has no id.
        if id(game) in seen or game_id(game) in dropped_games:
            return
        seen.add(id(game))
        picked.append(game)

    for ref in member_refs:
        game = by_id.get(ref[MEMBER_GAME_KEY])
        if game is not None:
            _add(game)

    from_filters = []
    ruled_tables: dict[str, tuple[str, ...]] = {}
    if stored_filters:
        per_table = collection_filters.table_sensitive(stored_filters)
        for game in games:
            if game_id(game) in named or game_id(game) in dropped_games:
                continue
            entries = [entry for key, entry in
                       table_entries(getattr(game, "meta_config", {})).items()
                       if key not in dropped_tables and isinstance(entry, dict)]
            taken = _ruled(stored_filters, game, entries, per_table)
            if taken is None:
                continue
            before = len(picked)
            _add(game)
            from_filters.extend(picked[before:])
            del picked[before:]
            if taken:
                ruled_tables[game_id(game)] = tuple(
                    str(entry.get(TABLE_ID_KEY, "")) for entry in taken)

    order = collections.get_order(name)
    order_by = order["by"] or DEFAULT_ORDER
    as_entries = {id(g): Entry(game=g, table={}, siblings=0) for g in picked + from_filters}

    def _sorted(games: list[Any], by: str, descending: bool = False) -> list[Any]:
        return [e.game for e in
                _ordered([as_entries[id(g)] for g in games], by, descending)]

    if order_by == MANUAL_ORDER:
        result = picked + _sorted(from_filters, DEFAULT_ORDER)
    else:
        result = _sorted(picked + from_filters, order_by, order["direction"] == "desc")
    limit = collections.get_limit(name)
    return Holding(games=result[:limit] if limit else result, added=picked,
                   matched=from_filters, excluded=len(dropped_games), tables=ruled_tables)


def resolve(name: str, collections: CollectionStore, games: list[Any], *,
            capped: bool = True) -> list[Entry]:
    """The ordered entries a collection contains, one per game.

    `games` is the library. A game offering several tables contributes the one step 3
    selected; `Entry.siblings` says how many it has, and `/games/{id}/tables` lists them.
    `capped=False` sets the collection's limit aside.
    """
    unknown = collections.unknown_filter_axes(name)
    if unknown:
        raise UnresolvableCollectionError(name, unknown)

    by_id: dict[str, Any] = {}
    for game in games:
        found = game_id(game)
        if found:
            by_id.setdefault(found, game)

    member_refs = collections.get_member_refs(name)
    named = {ref[MEMBER_GAME_KEY] for ref in member_refs}
    stored_filters = collections.get_filters(name) or {}
    dropped_games, dropped_tables = _excluded(collections.get_excluded_refs(name))

    # 1-3. Members first, in their order. A game named here states exactly what this
    # collection holds for it, so the filter is not consulted for that game at all.
    ordered: list[Entry] = []
    seen: set[tuple[int, str]] = set()

    def _take(game: Any, entry: dict) -> None:
        table_id = str(entry.get(TABLE_ID_KEY, ""))
        if table_id in dropped_tables or game_id(game) in dropped_games:
            return
        # Keyed on the object, not on the game's id: a game that has never been through
        # a metadata build has no id yet, and keying on "" would collapse every one of
        # them into a single row. Members resolve through `by_id` to the object the
        # library holds, so the same pairing twice is still one row.
        key = (id(game), table_id)
        if key in seen:
            return
        seen.add(key)
        ordered.append(Entry(game=game, table=entry, siblings=len(visible_entries(game))))

    for ref in member_refs:
        game = by_id.get(ref[MEMBER_GAME_KEY])
        if game is None:
            continue    # not in the library right now; the membership stays on disk
        if MEMBER_TABLE_KEY in ref:
            named_table = _named_table(game, ref[MEMBER_TABLE_KEY])
            if named_table is not None:
                _take(game, named_table)
        else:
            # Naming a game means the game, so it contributes its default and nothing
            # else. Naming two of a game's tables is how a collection holds both, and
            # that is why nothing de-duplicates by game after this.
            offered = visible_entries(game)
            if offered:
                _take(game, offered[0])

    from_filters: list[Entry] = []
    if stored_filters and not collection_filters.unknown_axes(stored_filters):
        per_table = collection_filters.table_sensitive(stored_filters)
        for game in games:
            if game_id(game) in named:
                continue
            offered = visible_entries(game)
            if not offered:
                continue
            taken = _ruled(stored_filters, game, offered, per_table)
            if taken is None:
                continue
            before = len(ordered)
            for entry in taken or offered[:1]:
                _take(game, entry)
            from_filters.extend(ordered[before:])
            del ordered[before:]

    # 4. Order. `manual` is the member array, and only when the collection says so:
    # a list curated before curated order existed was shown alphabetically, and
    # honouring its insertion order would reshuffle it under the user.
    order = collections.get_order(name)
    order_by = order["by"] or DEFAULT_ORDER
    descending = order["direction"] == "desc"

    if order_by == "manual":
        # Whatever a filter contributed has no position of its own, so it follows.
        result = ordered + _ordered(from_filters, DEFAULT_ORDER)
    else:
        result = _ordered(ordered + from_filters, order_by, descending)

    # 5. Limit. Last, so it caps an ordered list rather than deciding what is in it.
    # `_tiebreak` is what stops "top 20 rated" returning a different twenty each time
    # two tables are level.
    limit = collections.get_limit(name) if capped else None
    return result[:limit] if limit else result
