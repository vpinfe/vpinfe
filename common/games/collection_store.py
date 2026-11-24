"""Collections on disk: what is in each one, and what a collection is allowed to be."""

from __future__ import annotations

# collection_store.py
import configparser
import contextlib
import copy
import json
import logging
import os
import threading
from collections.abc import Container, Iterable, Iterator
from pathlib import Path

from common import events
from common.config.config_schema import PAGING_GROUP_ALIASES, PAGING_GROUPS
from common.games import collection_filters
from common.games.game import GameRecord
from common.games.game_identity import game_id
from common.games.game_metadata import (
    base_game_vps_id,
    vpinfe_section,
)
from common.games.info_file import VPINFE_SECTION
from common.games.info_migration import write_atomic
from common.i18n import t
from common.kept_copies import REASON_2X, backup_names, backup_stamp, copy_aside

logger = logging.getLogger("vpinfe.common.games.collection_store")


class DuplicateMemberError(ValueError):
    """A second ref for a pairing the collection already holds. Its own type so a caller
    can tell it from "no such member" and answer with a conflict rather than a not-found:
    the difference between asking for something impossible and asking about something
    absent."""

# Every writer holds a whole copy of the file and `save` writes all of it, so two that
# read before either saved lose one of the two edits. Callers each build their own store
# - the theme's collection menu and the API are different objects reading the same path -
# so the lock is the module's rather than any one store's.
_write_lock = threading.RLock()

# Generations of the collections file, counting from the first one that shipped.
#   1  ini, membership keyed by VPS id. Implied when no version is recorded.
#   2  JSON, membership keyed by the game's own id (common/games/game_identity.py).
#      Members and filters are nested and ordered, which an ini cannot encode.
#
# 3.0 development also numbered an ini keyed by game id, between the two. It never
# shipped, and anything below the current version migrates the same way, so it collapses
# into the JSON generation rather than leaving a gap.
COLLECTIONS_SCHEMA = 2

# Schema 0/1 only: in an ini the sections are collection names, so the version lived
# in a reserved section filtered out of the collection list. Both spellings are
# reserved - real files carry `[VPinFE]` and `[vpinfe]` side by side, because the
# section was renamed without the old one being removed. Reading only the new name
# turns the old one into a collection called "VPinFE".
SCHEMA_SECTION = VPINFE_SECTION
SCHEMA_SECTIONS = (VPINFE_SECTION, "VPinFE")
SCHEMA_KEY = "schema"

# The ini's membership key. Kept for reading a schema 0/1 file - see get_members().
MEMBERS_KEY = "vpsids"

COLLECTIONS_KEY = "collections"
COLLECTION_IMAGE_KEY = "image"

# One-time conversions this file has been through, by name. Not the schema number:
# whichever conversion stamped that first would tell the others they had already run.
MIGRATIONS_KEY = "migrations"
ORDER_DIRECTION_MIGRATION = "direction_applies_to_every_sort"
RATING_HALVES_MIGRATION = "rating_rule_is_in_half_stars"

# A member names a game, and optionally one of its tables:
#
#   {"game": "tuF3WogthK"}                        the game - its default table
#   {"game": "tuF3WogthK", "table": "9kRm2QvT8x"} exactly this table, frozen
#
# Two members may name the same game, which is how one game appears twice in a
# curated order with a different table each time. A bare id names the game.
MEMBER_GAME_KEY = "game"
MEMBER_TABLE_KEY = "table"

# Same shape, opposite sign. An excluded ref naming only a game removes the game; one
# naming a table removes that table and leaves the rest of the game alone.
#
# Excluding a table is not the inverse of naming one. A named table is frozen - a table
# added next month does not appear. An exclusion still tracks - it does. Both are
# wanted, and neither substitutes for the other.
EXCLUDED_KEY = "excluded"
# The criteria block. Its presence is what makes a collection a dynamic one.
FILTERS_KEY = "filters"
# False keeps the collection out of the frontend's menus; absent means shown.
IN_FRONTEND_KEY = "in_frontend"
# What the collection is for, in the owner's words. Free to add: a new field on a
# collection costs nothing, and a build that does not know it leaves it alone.
DESCRIPTION_KEY = "description"

# How a collection is ordered, as its own block rather than mixed in with the criteria.
#
#   {"by": "title", "direction": "asc"}
#
# `manual` means the member array is the order. It is never the default: a collection
# curated before curated order existed was displayed alphabetically, and honouring its
# insertion order would silently reshuffle a list the user is used to. The editor sets
# it when somebody actually arranges one.
ORDER_KEY = "order"
ORDER_BY_KEY = "by"
ORDER_DIRECTION_KEY = "direction"
# How a page press moves through this collection, or absent to follow the player's
# default. Absent has to be distinct from an explicit choice: stored as the resolved
# value instead, changing the default would stop reaching the collections that never
# expressed a preference, which is most of them.
ORDER_PAGING_GROUP_KEY = "paging_group"
DEFAULT_ORDER_BY = "title"
DEFAULT_DIRECTION = "asc"
MANUAL_ORDER = "manual"

# How many rows to keep, applied last so it caps an ordered list rather than choosing
# which rows are in it. Absent means all of them, which is every collection written
# before this key existed - so it is an addition to schema 2, not a new version.
# "Last 20 played" is an order plus this and nothing else.
LIMIT_KEY = "limit"

# The whole library, as a collection. Synthesized: the store answers for it and `save()`
# never writes it, so no user's file gains a row to say what absence already said. It
# constrains nothing, which is why the resolver needs no case for it - empty criteria
# match every game, so the library comes back through the ordinary path.
BUILTIN_ALL = "builtin:all"

# What `behavior.startup_collection` holds to open on whichever collection the frontend
# had open last. A setting value, not a collection: no collection can take the name.
LAST_COLLECTION = "Last Collection"


def is_reserved_name(name: str) -> bool:
    return name.strip().casefold() == LAST_COLLECTION.casefold()

BUILTIN_RECORDS = {
    BUILTIN_ALL: {"name": BUILTIN_ALL, "label": "All Games", "type": "filter",
                  "builtin": True, "image": "", "filters": {}},
}


def public_name(name: str | None) -> str:
    """What a collection is called outside core. The whole library is no collection at
    all out there - in the payload, in `get_current_collection`, in the URL - and
    the `builtin:` prefix reaches nobody."""
    return "" if not name or name == BUILTIN_ALL else str(name)

# The stored sort names, in the vocabulary the rest of 3.0 uses. Nothing writes the old
# spellings any more; a file written before this block existed still holds them, so the
# keys are what 2.x put on disk and the values are ours to change.
ORDER_ALIASES = {
    "Alpha": "title",
    "Newest": "added",
    "LastRun": "last_played",
    "Highest StartCount": "play_count",
    "RunTime": "play_time_seconds",
    # Not a 2.x spelling: the token this sort carried before durations named their unit.
    "play_time": "play_time_seconds",
}

# What a collection can be ordered by: the token stored, and the catalog key a person
# reads it by, in dropdown order. Keys, resolved where they are shown. `manual` is absent
# on purpose - it means the member array, which is not something to choose for a
# collection that filters.
SORT_LABELS = {
    "title": "order.by.title",
    "year": "order.by.year",
    "added": "order.by.added",
    "last_played": "order.by.last_played",
    "play_count": "order.by.play_count",
    "play_time_seconds": "order.by.play_time_seconds",
    "rating": "order.by.rating",
}

DIRECTION_LABELS = {"asc": "order.direction.asc", "desc": "order.direction.desc"}

DIRECTION_WORDS = {
    "title": {"asc": "order.direction.alpha_asc", "desc": "order.direction.alpha_desc"},
    "year": {"asc": "order.direction.oldest_first", "desc": "order.direction.newest_first"},
    "added": {"asc": "order.direction.oldest_first",
              "desc": "order.direction.newest_first"},
    "last_played": {"asc": "order.direction.oldest_first",
                    "desc": "order.direction.newest_first"},
    "play_count": {"asc": "order.direction.fewest_first",
                   "desc": "order.direction.most_first"},
    "play_time_seconds": {"asc": "order.direction.least_first",
                          "desc": "order.direction.most_first"},
    "rating": {"asc": "order.direction.lowest_first",
               "desc": "order.direction.highest_first"},
}
NATURAL_DIRECTION = {"title": "asc", "year": "asc", "added": "desc", "last_played": "desc",
                     "play_count": "desc", "play_time_seconds": "desc", "rating": "desc"}


def normalize_paging_group(value: object) -> str | None:
    """A paging choice as `sort`, `count`, or None for "follow the player".

    Anything unreadable becomes None rather than a guess: a collection that cannot say
    what it wants is a collection with no preference, which is the safe reading.
    """
    if value is None:
        return None
    raw = str(value).strip().lower()
    resolved = PAGING_GROUP_ALIASES.get(raw, raw)
    return resolved if resolved in PAGING_GROUPS else None


def normalize_direction(value: object) -> str:
    """A direction as `asc` or `desc`, whatever spelling it arrived in.

    The labels above are 2.x's stored spelling and still turn up in a criteria block, so
    anything that is not descending is ascending rather than an error.
    """
    return "desc" if str(value or "").strip().lower().startswith("desc") else "asc"


def _member_ref(value: object) -> dict | None:
    """One stored member as a ref, or None if there is nothing addressable in it."""
    if isinstance(value, str):
        value = value.strip()
        return {MEMBER_GAME_KEY: value} if value else None
    if not isinstance(value, dict):
        return None
    game = str(value.get(MEMBER_GAME_KEY, "") or "").strip()
    if not game:
        return None
    ref = {MEMBER_GAME_KEY: game}
    table = str(value.get(MEMBER_TABLE_KEY, "") or "").strip()
    if table:
        ref[MEMBER_TABLE_KEY] = table
    return ref


def _member_refs(values: Iterable[object] | None) -> list[dict]:
    return [ref for ref in (_member_ref(v) for v in (values or [])) if ref]

# What a filter collection stores, with the value meaning "unconstrained on this axis".
_FILTER_DEFAULTS = {
    "letter": "All", "theme": "All", "game_type": "All", "manufacturer": "All",
    "year": "All", "rating": "All", "rating_or_higher": "false",
    "sort_by": "Alpha", "order_by": "desc",
}
_NAMED_IN_A_2X_INI = {axis: old for old, axis in collection_filters.LEGACY_AXIS_NAMES.items()}


def _ini_schema(parser: configparser.ConfigParser) -> int:
    """The highest version either reserved section declares, or 0 if none does.

    0 means "declares nothing", which `collections_schema` turns into None for a caller
    choosing a backup to restore. The store reads an unstamped ini as generation 1.
    """
    found = []
    for name in SCHEMA_SECTIONS:
        if name in parser:
            try:
                found.append(int(parser[name].get(SCHEMA_KEY, 0) or 0))
            except (TypeError, ValueError):
                pass
    return max(found, default=0)

_warned_newer_schema: set[int] = set()


class CollectionStore:
    """The user's collections, stored as JSON.

    Records are dicts in a list, so their order is the order they are shown in and a
    filter collection's criteria nest instead of being flattened into ini keys. A
    schema 0/1 `collections.ini` beside the JSON is read once and converted on save.
    """

    def __init__(self, path: str) -> None:
        # Either name is accepted: the JSON file is what we read and write, and a
        # caller still holding the ini path - a script, an old config - gets the same
        # collections rather than an empty list.
        given = Path(path)
        if given.suffix == ".ini":
            self.ini_path, self.path = given, given.with_name(COLLECTIONS_NAME)
        else:
            self.path, self.ini_path = given, given.with_name(COLLECTIONS_NAME_INI)
        self._converted_from_ini = False
        # This store's own copies: `set_view_filters` writes into `builtin:all`, and one
        # caller's view must not become another's.
        self._builtins = {name: dict(record) for name, record in BUILTIN_RECORDS.items()}
        self.reload()

    def reload(self) -> None:
        """Load from disk, discarding unsaved changes."""
        self.records: list[dict] = []
        self._schema = COLLECTIONS_SCHEMA
        self._migrations: list[str] = []
        self._converted_from_ini = False

        if self.path.exists():
            self._load_json()
        elif self.ini_path.exists():
            self._load_ini()
            self._converted_from_ini = True

    def _load_json(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.exception("Could not read %s; starting from no collections", self.path)
            return
        if not isinstance(data, dict):
            return
        self._schema = int(data.get(SCHEMA_KEY, COLLECTIONS_SCHEMA) or COLLECTIONS_SCHEMA)
        self._migrations = [str(name) for name in data.get(MIGRATIONS_KEY) or []
                            if isinstance(name, str)]
        records = data.get(COLLECTIONS_KEY)
        if not isinstance(records, list):
            return
        for record in records:
            if not isinstance(record, dict) or not record.get("name"):
                continue
            for key in ("members", EXCLUDED_KEY):
                if key in record:
                    record[key] = _member_refs(record[key])
            self.records.append(record)

    def _load_ini(self) -> None:
        """Read a schema 0/1 file into the current shape. Nothing is written here -
        the conversion reaches disk on the next save, which keeps reading side-effect
        free for the callers that only ever look."""
        parser = configparser.ConfigParser()
        parser.read(self.ini_path, encoding="utf-8")
        # No stamp means the shape 2.x shipped, which is generation 1.
        self._schema = _ini_schema(parser) or 1
        # 2.x applied the direction to every sort, so what the ini stores is what it showed.
        self._migrations.append(ORDER_DIRECTION_MIGRATION)
        for name in parser.sections():
            if name in SCHEMA_SECTIONS:
                continue
            sec = parser[name]
            record = {"name": name, "image": sec.get("image", "")}
            if sec.get("type", "vpsid") == "filter":
                record["type"] = "filter"
                record["filters"] = {k: sec.get(_NAMED_IN_A_2X_INI.get(k, k), d)
                                     for k, d in _FILTER_DEFAULTS.items()}
            else:
                record["type"] = "manual"
                record["members"] = _member_refs(
                    sec.get(MEMBERS_KEY, "").split(","))
            self.records.append(record)

    def _record(self, name: str) -> dict | None:
        for record in self.records:
            if record.get("name") == name:
                return record
        # A fresh copy, so a reader that mutates what it is handed cannot make a builtin
        # differ between two calls. Writers go through _require_mutable, which refuses.
        builtin = self._builtins.get(name)
        return dict(builtin) if builtin else None

    def _require(self, name: str) -> dict:
        record = self._record(name)
        if record is None:
            raise KeyError(t("error.collections.not_found", name=name))
        return record

    def _require_mutable(self, name: str) -> dict:
        """The stored record to write to. A builtin has none - it is synthesized on read,
        so an edit would be accepted and then vanish at the next call."""
        record = self._require(name)
        if record.get("builtin") is True:
            raise ValueError(t("error.collections.locked", name=name))
        return record

    def get_collections_name(self) -> list[str]:
        return [r["name"] for r in self.records]

    def arrange(self, names: list[str]) -> None:
        """Put the records in the order `names` gives, which must name each exactly once."""
        if sorted(names) != sorted(self.get_collections_name()):
            raise ValueError(t("error.collections.order_collections"))
        place = {name: at for at, name in enumerate(names)}
        self.records.sort(key=lambda record: place[record["name"]])

    def schema_version(self) -> int:
        return self._schema

    def has_migrated(self, name: str) -> bool:
        """Whether this file has already been through the named one-time conversion."""
        return name in self._migrations

    def record_migration(self, name: str) -> None:
        """Say it has, so it is not done to the user's file a second time. Written by
        the next `save`, which is what makes it survive a restart."""
        if name not in self._migrations:
            self._migrations.append(name)

    def _stamp_schema(self, version: int = COLLECTIONS_SCHEMA) -> None:
        self._schema = version

    def has_filters(self, section: str) -> bool:
        """Whether this collection carries criteria.

        The block's presence, not the stored `type`: the kind is derived from it,
        and a record either has criteria or it does not.
        """
        record = self._record(section)
        return record is not None and FILTERS_KEY in record

    def is_filter_based(self, section: str) -> bool:
        """The name most callers use. Derived, so a collection changes kind by gaining
        or losing criteria rather than by being told."""
        return self.has_filters(section)

    def clear_filters(self, section: str) -> None:
        """Drop the criteria, leaving the membership. The second half of "keep the
        result rather than the rule"."""
        record = self._require_mutable(section)
        record.pop(FILTERS_KEY, None)
        record.pop("type", None)

    def get_filters(self, section: str) -> dict | None:
        if not self.has_filters(section):
            return None
        stored = self._require(section).get("filters") or {}
        # Defaults underneath, the file on top: the defaults are the nine criteria an ini
        # could hold, and an axis added since - `played` is the first - is only in the file.
        return {**_FILTER_DEFAULTS, **stored}

    def set_view_filters(self, criteria: dict | None) -> None:
        """Constrain `builtin:all` by criteria that are stored nowhere - the frontend's
        filter controls, which make a collection out of the library rather than narrowing
        the one on screen. Around `_require_mutable` on purpose: there is no record to
        write to, and this lasts as long as the store object."""
        self._builtins[BUILTIN_ALL]["filters"] = dict(criteria or {})

    def get_order(self, section: str) -> dict:
        """How to order this collection: {"by", "direction", "paging"}.

        `paging_group` is None when the collection follows the player's default.

        Falls back to the sort a filter collection stored beside its criteria, and then
        to title. Never falls back to `manual` - see ORDER_KEY.

        Both paths read through ORDER_ALIASES. An old spelling in the block resolves to
        no sort at all otherwise: it does not match a branch, so the list comes back in
        title order and nothing says why.
        """
        record = self._require(section)
        stored = record.get(ORDER_KEY)
        if isinstance(stored, dict) and str(stored.get(ORDER_BY_KEY, "") or "").strip():
            raw = str(stored[ORDER_BY_KEY]).strip()
            direction = stored.get(ORDER_DIRECTION_KEY) or DEFAULT_DIRECTION
        else:
            criteria = record.get("filters") or {}
            raw = str(criteria.get("sort_by", "") or "").strip()
            direction = criteria.get("order_by") or DEFAULT_DIRECTION
        by = ORDER_ALIASES.get(raw, raw) or DEFAULT_ORDER_BY
        paging_group = stored.get(ORDER_PAGING_GROUP_KEY) if isinstance(stored, dict) else None
        return {ORDER_BY_KEY: by,
                ORDER_DIRECTION_KEY: normalize_direction(direction),
                ORDER_PAGING_GROUP_KEY: normalize_paging_group(paging_group)}

    def set_order(self, section: str, by: str, direction: str = DEFAULT_DIRECTION,
                  paging_group: str | None = None) -> None:
        """Record how this collection is ordered. `manual` means the member array.

        `paging_group` of None is not a value - it is the collection saying nothing, so
        it follows whatever the player has set.
        """
        record = self._require_mutable(section)
        record[ORDER_KEY] = {ORDER_BY_KEY: by,
                             ORDER_DIRECTION_KEY: normalize_direction(direction)}
        paging_group = normalize_paging_group(paging_group)
        if paging_group is not None:
            record[ORDER_KEY][ORDER_PAGING_GROUP_KEY] = paging_group
        # The criteria said the same thing in the 2.x spellings, and `get_order` reads
        # this block first - so leaving them is leaving a second, stale answer behind.
        for key in collection_filters.ORDERING_KEYS:
            record.get("filters", {}).pop(key, None)

    def get_limit(self, section: str) -> int | None:
        """How many rows this collection keeps, or None for all of them."""
        raw = self._require(section).get(LIMIT_KEY, "")
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return None
        return value if value > 0 else None

    def set_limit(self, section: str, limit: int | None) -> None:
        """Cap this collection, or lift the cap with None.

        Refused on a builtin: a limit on the whole library hides most of it with nothing
        on screen to say why, and `builtin:all` is what everything else falls back to.
        """
        record = self._require_mutable(section)
        if limit is None:
            record.pop(LIMIT_KEY, None)
            return
        value = int(limit)
        if value <= 0:
            raise ValueError(t("error.collections.limit_too_low"))
        record[LIMIT_KEY] = value

    def get_excluded_refs(self, section: str) -> list[dict]:
        """What this collection removes, whatever put it there. Applied after members
        and filters, so it overrules both."""
        return [dict(ref) for ref in _member_refs(self._require(section).get(EXCLUDED_KEY))]

    def exclude(self, section: str, game_id: str, table_id: str = "") -> None:
        """Remove a game, or one of its tables, from this collection."""
        record = self._require_mutable(section)
        excluded = _member_refs(record.get(EXCLUDED_KEY))
        ref = _member_ref({MEMBER_GAME_KEY: game_id, MEMBER_TABLE_KEY: table_id})
        if ref and ref not in excluded:
            excluded.append(ref)
        record[EXCLUDED_KEY] = excluded

    def unexclude(self, section: str, game_id: str,
                  table_id: str | None = None) -> None:
        """Undo an exclusion: one ref, or every exclusion naming this game.

        `None` is every one of them - the game is wanted back whole. `""` is the *one*
        exclusion that names no table, and an id is the one naming it. Collapsing those
        two leaves an exclusion of a whole game impossible to undo without also undoing
        the per-table ones somebody meant to keep.
        """
        record = self._require_mutable(section)
        excluded = _member_refs(record.get(EXCLUDED_KEY))
        if table_id is None:
            keep = [e for e in excluded if e[MEMBER_GAME_KEY] != game_id]
        else:
            gone = _member_ref({MEMBER_GAME_KEY: game_id,
                                MEMBER_TABLE_KEY: table_id})
            keep = [e for e in excluded if e != gone]
        if keep:
            record[EXCLUDED_KEY] = keep
        else:
            # Nothing left to say; an empty list in every record is noise in the file.
            record.pop(EXCLUDED_KEY, None)

    def get_member_refs(self, section: str) -> list[dict]:
        """Membership as stored: an ordered list of refs, each naming a game and
        optionally one of its tables. This is the list the resolver walks."""
        return [dict(ref) for ref in _member_refs(self._require(section).get("members"))]

    def unknown_filter_axes(self, section: str) -> list[str]:
        """Criteria in this collection that this build cannot resolve.

        Non-empty means refuse the collection rather than resolve what is left: skipping
        a constraint answers a different question and does it silently. Every other
        collection in the file is unaffected, which is what lets an axis be added
        without a schema bump.
        """
        record = self._record(section)
        if not record:
            return []
        return collection_filters.unknown_axes(record.get("filters"))

    def get_members(self, section: str) -> list[str]:
        """The game ids a collection contains, in order and de-duplicated.

        The same game can appear more than once - two tables of it, at two positions -
        so this is not the same length as the stored list. Callers that need to know
        which table want get_member_refs().
        """
        seen = []
        for ref in _member_refs(self._require(section).get("members")):
            if ref[MEMBER_GAME_KEY] not in seen:
                seen.append(ref[MEMBER_GAME_KEY])
        return seen

    def get_all(self) -> dict[str, list[str]]:
        return {name: self.get_members(name) for name in self.get_collections_name()}

    def add_collection(self, section: str, members: Iterable[object] | None = None) -> None:
        """Add a collection whose membership is an explicit list of games."""
        if is_reserved_name(section):
            raise ValueError(t("error.collections.name_reserved", name=section))
        if self._record(section) is not None:
            raise ValueError(t("error.collections.name_taken",
                               name=section))
        self.records.append({"name": section, "type": "manual",
                             "image": "", "members": list(members or [])})

    def add_filter_collection(
        self,
        section: str,
        letter: str = "All",
        theme: str = "All",
        game_type: str = "All",
        manufacturer: str = "All",
        year: str = "All",
        rating: str = "All",
        rating_or_higher: str = "false",
        sort_by: str = "Alpha",
        order_by: str = "desc",
        played: bool | None = None,
    ) -> None:
        """Add a filter-based collection."""
        if is_reserved_name(section):
            raise ValueError(t("error.collections.name_reserved", name=section))
        if self._record(section) is not None:
            raise ValueError(t("error.collections.name_taken",
                               name=section))
        criteria: dict[str, str | bool] = {
            "letter": letter, "theme": theme, "game_type": game_type,
            "manufacturer": manufacturer, "year": year, "rating": rating,
            "rating_or_higher": rating_or_higher, "sort_by": sort_by,
            "order_by": normalize_direction(order_by),
        }
        # Only when asked for. Written as false it would read as "never played" rather
        # than as saying nothing about play at all.
        if played is not None:
            criteria["played"] = bool(played)
        self.records.append({"name": section, "type": "filter", "image": "",
                             "filters": criteria})

    def copy_collection(self, source: str, section: str) -> None:
        if is_reserved_name(section):
            raise ValueError(t("error.collections.name_reserved", name=section))
        if self._record(section) is not None:
            raise ValueError(t("error.collections.name_taken",
                               name=section))
        record = copy.deepcopy(self._require_mutable(source))
        record["name"] = section
        self.records.append(record)

    def make_filter_collection(self, section: str, filters: dict,
                               order: dict | None = None,
                               limit: int | None = None) -> None:
        """Give a collection criteria, keeping its name, icon, position and members.

        Membership stays: a rule and named members are combinable. Dropping it here
        made "add a rule" destructive.
        """
        record = self._require_mutable(section)
        current = self.get_order(section)
        # A sort written the 2.x way lives inside the criteria being replaced.
        held_in_criteria = (not isinstance(record.get(ORDER_KEY), dict)
                            and any(key in (record.get(FILTERS_KEY) or {})
                                    for key in collection_filters.ORDERING_KEYS))
        # Still written, and no longer read: `has_filters` derives the kind from the
        # block's presence. Kept in the file so a build that predates that still reads
        # the collection correctly.
        record["type"] = "filter"
        record[FILTERS_KEY] = dict(filters)
        if order or held_in_criteria:
            given = order or {}
            self.set_order(section,
                           given.get(ORDER_BY_KEY) or current[ORDER_BY_KEY],
                           given.get(ORDER_DIRECTION_KEY) or current[ORDER_DIRECTION_KEY],
                           current[ORDER_PAGING_GROUP_KEY])
        if self.get_order(section)[ORDER_BY_KEY] == MANUAL_ORDER:
            self.set_order(section, DEFAULT_ORDER_BY,
                           (order or {}).get(ORDER_DIRECTION_KEY) or DEFAULT_DIRECTION,
                           current[ORDER_PAGING_GROUP_KEY])
        if limit is not None:
            self.set_limit(section, limit)

    def delete_collection(self, section: str) -> None:
        self.records.remove(self._require_mutable(section))

    def rename_collection(self, old_name: str, new_name: str) -> None:
        record = self._require_mutable(old_name)
        if is_reserved_name(new_name):
            raise ValueError(t("error.collections.name_reserved", name=new_name))
        if self._record(new_name) is not None:
            raise ValueError(t("error.collections.name_taken",
                               name=new_name))
        if not new_name.strip():
            raise ValueError(t("error.collections.name_needed"))
        record["name"] = new_name

    def add_member(self, section: str, member_id: str, table_id: str = "",
                   after_table: str | None = None) -> None:
        """Add a game, or one specific table of it. Adding the same pairing twice is
        a no-op; adding a second table of a game already present is not.

        `after_table` puts the new ref directly after this game's ref naming that table
        - `""` for the one naming none - and appending is what happens without it. It
        exists because a second table added to the end of a long manual collection is
        indistinguishable from nothing having happened.
        """
        record = self._require_mutable(section)
        members = _member_refs(record.get("members"))
        ref = _member_ref({MEMBER_GAME_KEY: member_id, MEMBER_TABLE_KEY: table_id})
        if ref and ref not in members:
            at = None
            if after_table is not None:
                sibling = _member_ref({MEMBER_GAME_KEY: member_id,
                                       MEMBER_TABLE_KEY: after_table})
                at = next((i for i, m in enumerate(members) if m == sibling), None)
            if at is None:
                members.append(ref)
            else:
                members.insert(at + 1, ref)
        record["members"] = members

    def remove_member(self, section: str, member_id: str,
                      table_id: str | None = None) -> None:
        """Remove one ref, or every ref naming this game.

        `None` is every ref - what "remove this game" means. `""` is the *one* ref that
        names no table, and an id is the one naming it. Collapsing those two deletes every
        row for a game when only the one following its default was meant, and does it
        silently.
        """
        record = self._require_mutable(section)
        members = _member_refs(record.get("members"))
        if table_id is None:
            keep = [m for m in members if m[MEMBER_GAME_KEY] != member_id]
        else:
            gone = _member_ref({MEMBER_GAME_KEY: member_id,
                                MEMBER_TABLE_KEY: table_id})
            keep = [m for m in members if m != gone]
        if len(keep) == len(members):
            raise ValueError(t("error.collections.not", game=member_id, name=section))
        record["members"] = keep

    def set_member_table(self, section: str, member_id: str, table_id: str,
                         was: str | None = "") -> None:
        """Point an existing ref at a different table, keeping its position.

        Remove-then-add would do the same to the file and put the row at the end, which
        is the one thing a curated order cannot afford. `was` says which ref to move
        when a game has several; an empty `table_id` gives the game
        back its default.
        """
        record = self._require_mutable(section)
        members = _member_refs(record.get("members"))
        wanted = _member_ref({MEMBER_GAME_KEY: member_id, MEMBER_TABLE_KEY: was})
        ref = _member_ref({MEMBER_GAME_KEY: member_id, MEMBER_TABLE_KEY: table_id})
        at = next((i for i, m in enumerate(members) if m == wanted), None)
        # A blank member id addresses nothing, so neither ref is built and there is
        # nothing in the collection it could be pointing at.
        if at is None or ref is None:
            raise ValueError(t("error.collections.row_gone", name=section))
        # Refused, not repaired. A pairing appears once (2.10), so pointing this ref at
        # a table another already names cannot be stored - and the obvious repair, of
        # dropping one of them, takes a row away without saying so. A caller that is
        # told can offer a choice; a collection that quietly loses a row cannot.
        if any(m == ref for i, m in enumerate(members) if i != at):
            raise DuplicateMemberError(t("error.collections.table_already_in", name=section))
        members[at] = ref
        record["members"] = members

    def set_members(self, section: str, members: Iterable[object] | None) -> None:
        """Replace the membership outright, taking game ids or refs. For a caller where
        the order is the point, which is the Manager saving a whole edit.

        A caller passing bare ids is naming games rather than tables, so a table this
        collection had named is gone. That is the right meaning for an editor that can
        only show games, but it is not something to lose quietly.
        """
        record = self._require_mutable(section)
        replacement = _member_refs(members)
        named = {m[MEMBER_GAME_KEY] for m in _member_refs(record.get("members"))
                 if MEMBER_TABLE_KEY in m}
        lost = named - {m[MEMBER_GAME_KEY] for m in replacement if MEMBER_TABLE_KEY in m}
        if lost:
            logger.warning(
                "Collection %r: replacing membership dropped the table named for %s; "
                "those games contribute their default table now",
                section, ", ".join(sorted(lost)))
        if replacement:
            record["members"] = replacement
        else:
            # Dropped rather than written empty, the same way `unexclude` drops an
            # empty exclusion list: a key present in every record and meaning nothing
            # is noise in the file.
            record.pop("members", None)

    def __contains__(self, section: str) -> bool:
        return self._record(section) is not None

    def get_description(self, section: str) -> str:
        """What this collection is for, in the owner's words. Empty when unsaid."""
        record = self._record(section)
        return str((record or {}).get(DESCRIPTION_KEY, "") or "").strip()

    def set_description(self, section: str, text: str | None) -> None:
        """Say what it is for, or stop saying. Empty removes the key rather than
        storing a blank, the same way an empty membership does."""
        record = self._require_mutable(section)
        cleaned = str(text or "").strip()
        if cleaned:
            record[DESCRIPTION_KEY] = cleaned
        else:
            record.pop(DESCRIPTION_KEY, None)

    def get_in_frontend(self, section: str) -> bool:
        return (self._record(section) or {}).get(IN_FRONTEND_KEY) is not False

    def set_in_frontend(self, section: str, shown: bool) -> None:
        record = self._require_mutable(section)
        if shown:
            record.pop(IN_FRONTEND_KEY, None)
        else:
            record[IN_FRONTEND_KEY] = False

    def get_image(self, section: str) -> str:
        record = self._record(section)
        return str((record or {}).get(COLLECTION_IMAGE_KEY, "") or "").strip()

    def set_image(self, section: str, filename: str | None) -> None:
        """Set the collection's icon, or clear it when given nothing."""
        record = self._require_mutable(section)
        if filename:
            record[COLLECTION_IMAGE_KEY] = filename
        else:
            record.pop(COLLECTION_IMAGE_KEY, None)

    def set_filter(self, section: str, key: str, value: object) -> None:
        """One criterion on a filter collection."""
        self._require_mutable(section).setdefault("filters", {})[key] = value

    def migrate_membership_to_game_ids(self, games: Iterable[GameRecord]) -> int:
        """Move VPS-keyed membership onto game ids. Returns how many entries moved.

        Acts on whatever is still not a game id, whatever the file's stamp says, so it
        is safe to call at every startup and again after a library read: a VPS id can
        arrive from matching after the first call, and a save made before it stamps the
        file as done. A VPS id moves to every game that carries it. A file from a newer
        VPinFE is left alone.

        An entry that matches no game is kept, because the game may simply not be
        present right now and dropping it would lose that membership for good.
        """
        with _write_lock:
            self.reload()
            version = self.schema_version()
            if version > COLLECTIONS_SCHEMA:
                if version not in _warned_newer_schema:
                    _warned_newer_schema.add(version)
                    logger.warning(
                        "collections.ini uses schema %s, newer than this build's %s. "
                        "Leaving membership untouched.", version, COLLECTIONS_SCHEMA)
                return 0
            names = self.get_collections_name()
            if not names:
                return 0

            by_vps: dict[str, list[str]] = {}
            known_ids: set[str] = set()
            for game in games:
                tid = game_id(game)
                if not tid:
                    continue
                known_ids.add(tid)
                vpinfe = vpinfe_section(getattr(game, "meta_config", {}))
                for candidate in (base_game_vps_id(game),
                                  str(vpinfe.get("alt_vpsid", "") or "").strip()):
                    if candidate and tid not in by_vps.setdefault(candidate, []):
                        by_vps[candidate].append(tid)

            unresolved_by_collection: dict[str, list[str]] = {}
            moved = 0
            for name in names:
                refs = self.get_member_refs(name)
                named = {ref[MEMBER_GAME_KEY] for ref in refs}
                rewritten: list[dict] = []
                for ref in refs:
                    member = ref[MEMBER_GAME_KEY]
                    if member in known_ids:
                        rewritten.append(ref)
                    elif member in by_vps:
                        added = [tid for tid in by_vps[member] if tid not in named]
                        named.update(added)
                        rewritten.extend({MEMBER_GAME_KEY: tid} for tid in added)
                        moved += 1
                    else:
                        rewritten.append(ref)
                        unresolved_by_collection.setdefault(name, []).append(member)
                if rewritten != refs:
                    self._require(name)["members"] = rewritten

            converting = self._converted_from_ini
            if moved or converting:
                self._stamp_schema()
                self.save()
            # Loud only when the file changed; a game that is simply absent would
            # otherwise be reported at every startup.
            level = logging.INFO if moved or converting else logging.DEBUG
            logger.log(level, "Collection membership moved onto game ids: %s moved, %s "
                       "members match no game", moved,
                       sum(len(ids) for ids in unresolved_by_collection.values()))
            for name, ids in sorted(unresolved_by_collection.items()):
                logger.log(logging.WARNING if moved or converting else logging.DEBUG,
                           "Collection %r keeps %s member(s) no game matched: %s",
                           name, len(ids), ", ".join(sorted(ids)[:10])
                           + (", ..." if len(ids) > 10 else ""))
            return moved

    @contextlib.contextmanager
    def mutate(self) -> Iterator[CollectionStore]:
        """Read the file, change it, write it back, with no other writer in between.

        `write_atomic` already stops a reader seeing half a file. What it cannot stop is
        the edit made against a copy that went stale while it was being made: the theme's
        collection menu and the API write the same file through different stores, and
        without this the second `save` drops the first one's collection and reports
        success. Reloading inside the lock is what makes the copy current.

        Raising inside the block writes nothing, which is what lets a caller validate
        against the just-reloaded file and refuse.
        """
        with _write_lock:
            self.reload()
            yield self
            self.save()

    def save(self) -> None:
        """Write collections back to disk, atomically.

        Prefer `mutate` for a read-modify-write; calling this alone writes whatever this
        store last read, which may no longer be what is on disk.

        The first save after reading an ini keeps a copy of it and leaves the original
        in place: a user who goes back to 2.x needs the file 2.x reads.
        """
        with _write_lock:
            if self._converted_from_ini and self.ini_path.exists():
                logger.info("Kept the pre-JSON collections at %s",
                            copy_aside(str(self.ini_path), REASON_2X))
                self._converted_from_ini = False
            # Never stamp a newer file down to what this build writes. A newer VPinFE
            # owns that number, and claiming it would tell the next reader we understood
            # the file.
            payload = {SCHEMA_KEY: max(self._schema, COLLECTIONS_SCHEMA),
                       COLLECTIONS_KEY: self.records}
            if self._migrations:
                payload[MIGRATIONS_KEY] = self._migrations
            write_atomic(self.path,
                         lambda handle: json.dump(payload, handle, indent=2,
                                                  ensure_ascii=False))
        # Same reason game_repository announces a refreshed game: a wheel showing a
        # collection is showing this file, and nothing else would tell it.
        events.emit(events.COLLECTIONS_CHANGED, path=str(self.path))

    # ------------------------------------------------------------------
    # NEW JSON METADATA AWARE FILTERING
    # ------------------------------------------------------------------

    def is_member(self, game: GameRecord, member_ids: Container[str]) -> bool:
        """Whether a game belongs to a collection whose membership is `member_ids`.

        Membership is the game's own id. VPS ids are still accepted because a file
        written before the migration, or an entry for a game that was not present
        when it ran, still holds one - and because a game with no VPSdb match has
        no VPS id at all, which is one of the reasons membership moved off it.
        """
        if game_id(game) and game_id(game) in member_ids:
            return True

        vpinfe = vpinfe_section(getattr(game, "meta_config", {}))
        base_vpsid = base_game_vps_id(game)
        alt_vpsid = str(vpinfe.get("alt_vpsid", "") or "").strip()
        return bool(
            (base_vpsid and base_vpsid in member_ids)
            or (alt_vpsid and alt_vpsid in member_ids)
        )


COLLECTIONS_NAME = "collections.json"
COLLECTIONS_NAME_INI = "collections.ini"


def collections_schema(path: str | Path) -> int | None:
    """The schema a saved collections file declares, or None if it predates versioning.

    Reads either format, because backups of both exist: a restore has to be able to
    look at a `collections.ini` copy written before the move to JSON.
    """
    saved = Path(path)
    if COLLECTIONS_NAME_INI in saved.name:
        parser = configparser.ConfigParser()
        parser.read(saved, encoding="utf-8")
        return _ini_schema(parser) or None
    try:
        data = json.loads(saved.read_text(encoding="utf-8"))
    except ValueError:
        return None
    return int(data.get(SCHEMA_KEY, 0) or 0) or None if isinstance(data, dict) else None


def restorable_collections_backup(config_dir: str | Path,
                                  max_schema: int = COLLECTIONS_SCHEMA) -> str | None:
    """The saved collections file this build would put back, or None.

    Newest readable wins, same as a .info. A copy written by a newer build is stepped over.
    """
    folder = Path(config_dir)
    try:
        names = os.listdir(folder)
    except OSError:
        return None
    candidates = backup_names(names, COLLECTIONS_NAME) + backup_names(names, COLLECTIONS_NAME_INI)
    for name in sorted(candidates, key=backup_stamp, reverse=True):
        candidate = folder / name
        try:
            schema = collections_schema(candidate)
        except (OSError, ValueError):
            logger.warning("Unreadable collections backup, skipping: %s", candidate)
            continue
        if schema is None or schema <= max_schema:
            return str(candidate)
    return None
