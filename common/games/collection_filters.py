"""Every axis a filter collection can constrain, declared once.

One definition per axis drives four things at once: what
a stored filter may contain, how it matches, what control the Manager renders, and
what the API's schema says. Adding an axis is an entry here.

Because the registry is the only place that knows the axes, a stored filter naming one
this build does not have is *detectable*: the collection is refused by name instead of
silently resolving to a different membership. That is what makes adding an axis free -
an older build degrades loudly rather than quietly answering the wrong question.

The rule that keeps it free: **axes are append-only and their meaning never changes.**
Different semantics get a new axis. `tests/curation/test_collection_filters.py` holds a
snapshot that fails if an existing definition moves.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from common.games import collation, derived_tags
from common.games.game import GameRecord
from common.games.game_metadata import (
    game_last_run,
    game_manufacturer,
    game_rating,
    game_tags,
    game_themes,
    game_title,
    game_type,
    game_year,
    get_meta_value,
    normalize_rating,
    play_record,
    table_tags,
    whole_stars,
)
from common.games.tables import table_entries
from common.i18n import t
from common.values import is_truthy

# What a criterion says when it constrains nothing. The vocabulary the filter engine
# and the Manager already share, kept rather than translated.
UNCONSTRAINED = "All"

GAME_SCOPE = "game"
TABLE_SCOPE = "table"


def _values(criterion: object) -> set[str]:
    """A criterion as the set of values it accepts. Comma-separated throughout."""
    return {part.strip() for part in str(criterion).split(",") if part.strip()}


def letter_of(game: GameRecord) -> str:
    """The letter group a title sorts into. Digits and symbols share one bucket.

    The one definition, for paging and filtering both: a second is how `300` came to
    page under `#`, match no letter filter, and be offered as `3` by the picker. The
    rule sits with the ordering in `common.games.collation`, because a group the sort does not
    keep contiguous is a page jump that lands outside the letter it named.
    """
    return collation.letter_of(game_title(game))


def _match_letter(criterion: object, game: GameRecord, table: dict) -> bool:
    return letter_of(game) in {str(v).upper() for v in _values(criterion)}


def _match_theme(criterion: object, game: GameRecord, table: dict) -> bool:
    return bool(_values(criterion) & set(game_themes(game)))


def _match_tag(criterion: object, game: GameRecord, table: dict) -> bool:
    """Any of the tags asked for, on the game or on the table in hand. Case-sensitive,
    because the tags are: two spellings are two tags until somebody merges them, and
    matching across them would hide the duplicate the tag editor exists to find."""
    return bool(_values(criterion) & (set(game_tags(game)) | set(table_tags(table))
                                      | set(derived_tags.game_tags(game))
                                      | set(derived_tags.table_tags(table))))


def _carried_tags(game: GameRecord) -> list[str]:
    found = list(game_tags(game)) + derived_tags.game_tags(game)
    for entry in table_entries(getattr(game, "meta_config", {})).values():
        if isinstance(entry, dict):
            found += table_tags(entry) + derived_tags.table_tags(entry)
    return found


def _match_favorite(criterion: object, game: GameRecord, table: dict) -> bool:
    return is_truthy(criterion) == bool(play_record(
        getattr(game, "meta_config", {})).get("favorite"))


def _match_game_type(criterion: object, game: GameRecord, table: dict) -> bool:
    return game_type(game) in _values(criterion)


def _match_manufacturer(criterion: object, game: GameRecord, table: dict) -> bool:
    return game_manufacturer(game) in _values(criterion)


def _match_year(criterion: object, game: GameRecord, table: dict) -> bool:
    return game_year(game) in _values(criterion)


_FOUR_DIGITS = re.compile(r"\s*(\d{4})")


def _year_number(value: object) -> int | None:
    found = _FOUR_DIGITS.match(str(value if value is not None else ""))
    return int(found.group(1)) if found else None


def year_bounds(criterion: object) -> tuple[int | None, int | None]:
    """A `year_range` criterion as (from, to), inclusive, None where a side is open."""
    if not isinstance(criterion, dict):
        return None, None
    return _year_number(criterion.get("from")), _year_number(criterion.get("to"))


def _match_year_range(criterion: object, game: GameRecord, table: dict) -> bool:
    start, end = year_bounds(criterion)
    year = _year_number(game_year(game))
    if year is None:
        return False
    return (start is None or year >= start) and (end is None or year <= end)


def _steps_wanted(criterion: object) -> set[int]:
    """The half-star steps a criterion names, 1 to 10, the scale a game is rated on. A rule
    saved when a rule took whole stars is doubled once at startup, so it finds what it did."""
    return {normalize_rating(v) for v in _values(criterion)}


def _match_rating(criterion: object, game: GameRecord, table: dict) -> bool:
    """Exactly the one step the rule names: 4 finds 2 stars, 9 finds 4 and a half."""
    return game_rating(game) in _steps_wanted(criterion)


def _match_played(criterion: object, game: GameRecord, table: dict) -> bool:
    """`true` selects the games with a play on record, `false` the ones without.

    Ordering the library by `last_played` cannot stand in for this: a game that has
    never been played sorts as a value rather than being left out, so "the last 30
    played" comes back padded with games nobody has touched.
    """
    return (game_last_run(game) > 0) == is_truthy(criterion)


def _none_of(matcher: Callable) -> Callable:
    """The opposite of a choice matcher: a game holding none of the values it names."""
    def match(criterion: object, game: GameRecord, table: dict) -> bool:
        return not matcher(criterion, game, table)
    return match


def _match_no_tag(criterion: object, game: GameRecord, table: dict) -> bool:
    """With no table in hand this is the game with every one of its tables, so a game
    is taken whole only when nothing it plays carries the tag."""
    if table:
        return not _match_tag(criterion, game, table)
    return not (_values(criterion) & set(_carried_tags(game)))


def _match_rating_or_higher(criterion: object, game: GameRecord, table: dict) -> bool:
    """Reads `rating` as a floor rather than a set. Declared as its own axis because
    that is how it is stored and how the UI presents it - a checkbox beside rating."""
    wanted = _steps_wanted(criterion)
    return bool(wanted) and game_rating(game) >= min(wanted)


@dataclass(frozen=True)
class FilterAxis:
    """One thing a collection can filter on.

    `scope` says which object the criterion is about. It is what removes the ambiguity
    in `manufacturer`, `year` and `type`, which exist on a game *and* on each of its
    tables, where the bare name resolves to whichever is reached first.

    `name` is stored and the label is shown, so a label can be reworded freely and a name
    never can - and the label is what a *reader* calls it, not a short form of the key.
    Both rating axes label as "Rating": they are one control, and the catalog carries the
    word twice so a translator is not asked to guess that.
    """

    name: str
    scope: str
    kind: str
    matches: Callable
    # Which group this game is in. `matches` answers the other question - is it in group
    # A? - and paging to the next boundary can only ask this one. None where the axis
    # has no groups to page between.
    groups: Callable | None = None
    # Where this axis's values come from, so `available_options` and the API derive them
    # rather than each keeping a list beside a registry whose point is one declaration.
    # `values_key` is the name they are reported under - `game_type` answers as `types`,
    # so pluralising the axis name would be a rule with an exception - and it reaches
    # themes through `frontend/api.py`, which is why the spellings do not move.
    values_of: Callable | None = None
    values_key: str = ""
    # Whether a criterion may hold more than one value, which is an OR across them.
    # Declared rather than inferred: the matcher has always split on commas, so every
    # axis *looked* multi-valued while only some of them mean anything that way, and a
    # control could not tell which. Rating is the case in point - two ratings at once
    # says nothing the floor does not say better.
    many: bool = False
    # Offered on every install whatever the library holds, for an axis whose values are
    # a scale rather than data.
    choices: tuple[str, ...] = ()
    # The axis whose field this one is asked under, or empty where it is its own.
    field: str = ""

    @property
    def label(self) -> str:
        """What a reader calls this axis. The key follows `name`, which never moves."""
        return t(f"filter.{self.name}.label")

    @property
    def summary(self) -> str:
        """The sentence explaining the axis, for a tooltip. Prose: may be untranslated."""
        return t(f"filter.{self.name}.summary")

    @property
    def is_table_scoped(self) -> bool:
        return self.scope == TABLE_SCOPE


AXES: tuple[FilterAxis, ...] = (
    FilterAxis("letter", GAME_SCOPE, "letter",
               _match_letter, groups=letter_of, many=True,
               values_of=lambda game: [letter_of(game)], values_key="letters"),
    # The label a reader sees; `name` is stored and never moves. "Theme" alone reads as
    # the frontend's in a list of rules that has no game in front of it.
    FilterAxis("theme", GAME_SCOPE, "choice",
               _match_theme, many=True,
               values_of=game_themes, values_key="themes"),
    FilterAxis("game_type", GAME_SCOPE, "choice",
               _match_game_type, many=True,
               values_of=lambda game: [game_type(game)], values_key="types"),
    FilterAxis("manufacturer", GAME_SCOPE, "choice",
               _match_manufacturer, many=True,
               values_of=lambda game: [game_manufacturer(game)],
               values_key="manufacturers"),
    FilterAxis("year", GAME_SCOPE, "choice",
               _match_year, groups=lambda game: str(game_year(game)), many=True,
               values_of=lambda game: [str(game_year(game) or "")],
               values_key="years"),
    FilterAxis("rating", GAME_SCOPE, "rating",
               _match_rating, groups=lambda game: str(whole_stars(game_rating(game))),
               choices=tuple(str(step) for step in range(1, 11))),
    FilterAxis("rating_or_higher", GAME_SCOPE, "rating",
               _match_rating_or_higher, field="rating"),
    FilterAxis("played", GAME_SCOPE, "flag",
               _match_played),
    FilterAxis("favorite", GAME_SCOPE, "flag",
               _match_favorite),
    # The user's own words, so the values are whatever this library holds - the same
    # shape as `theme`, which is where they come from for everybody else.
    FilterAxis("tags", TABLE_SCOPE, "choice",
               _match_tag, many=True,
               values_of=_carried_tags, values_key="tags"),
    FilterAxis("year_range", GAME_SCOPE, "range",
               _match_year_range, field="year"),
    FilterAxis("theme_none_of", GAME_SCOPE, "none_of",
               _none_of(_match_theme), many=True, field="theme"),
    FilterAxis("game_type_none_of", GAME_SCOPE, "none_of",
               _none_of(_match_game_type), many=True, field="game_type"),
    FilterAxis("manufacturer_none_of", GAME_SCOPE, "none_of",
               _none_of(_match_manufacturer), many=True, field="manufacturer"),
    FilterAxis("year_none_of", GAME_SCOPE, "none_of",
               _none_of(_match_year), many=True, field="year"),
    FilterAxis("tags_none_of", TABLE_SCOPE, "none_of",
               _match_no_tag, many=True, field="tags"),
)

AXES_BY_NAME = {axis.name: axis for axis in AXES}

# Stored beside the criteria but not criteria: they say how to order what matched, not
# what matches. Reserved so an older build does not read them as an axis it lacks and
# refuse a collection it can resolve perfectly well.
ORDERING_KEYS = frozenset({"sort_by", "order_by"})

# 2.x's name for an axis, which a 2.x collections.ini still carries. Read only where
# that file is imported: 3.0 writes and reads each axis under its own name.
LEGACY_AXIS_NAMES = {"table_type": "game_type"}


def criterion(stored: dict | None, name: str, default: object = None) -> object:
    """One criterion out of a stored filter, or `default` where it sets none."""
    return (stored or {}).get(name, default)


def is_unconstrained(criterion: object) -> bool:
    """Whether a criterion asks for nothing. Absent, empty and "All" all mean this, and
    so does a range with neither end set."""
    if isinstance(criterion, dict):
        return year_bounds(criterion) == (None, None)
    return criterion in (None, "", UNCONSTRAINED) or not _values(criterion)


def unknown_axes(stored: dict | None) -> list[str]:
    """Axes in a stored filter that this build cannot resolve.

    A caller finding any must refuse the collection rather than resolve what is left:
    ignoring a constraint answers a different question, and does it silently.
    """
    return sorted(name for name in (stored or {})
                  if name not in ORDERING_KEYS and name not in AXES_BY_NAME)


def matches(stored: dict | None, game: GameRecord, table: dict | None = None) -> bool:
    """Whether one game, optionally via one of its tables, satisfies every criterion.

    An axis that constrains nothing is skipped; `rating_or_higher` reads the `rating`
    criterion, so it is skipped when rating itself is unconstrained.
    """
    stored = stored or {}
    rating = stored.get("rating")
    for name, criterion in stored.items():
        axis = AXES_BY_NAME.get(name)
        if axis is None or is_unconstrained(criterion):
            continue
        if axis.name == "rating_or_higher":
            if str(criterion).strip().lower() not in ("1", "true", "yes", "on"):
                continue
            criterion = rating
            if is_unconstrained(criterion):
                continue
        elif axis.name == "rating" and _reads_rating_as_a_floor(stored):
            continue
        if not axis.matches(criterion, game, table or {}):
            return False
    return True


def table_sensitive(stored: dict | None) -> bool:
    """Whether a table of a game can match where the game itself does not."""
    return any(not is_unconstrained(criterion)
               and name in AXES_BY_NAME and AXES_BY_NAME[name].is_table_scoped
               for name, criterion in (stored or {}).items())


def _reads_rating_as_a_floor(stored: dict) -> bool:
    value = str(stored.get("rating_or_higher", "") or "").strip().lower()
    return value in ("1", "true", "yes", "on")

# ---------------------------------------------------------------------------
# What there is to filter *on*, as opposed to whether a game matches. The Manager
# UI asks for the letters, themes, types, manufacturers and years its controls
# should offer, and the answer comes from the same axis definitions the matching uses.
# ---------------------------------------------------------------------------

class GameListFilters:
    """Filter games by various criteria: starting letter, theme, type, and rating."""

    def __init__(self, games: Iterable[GameRecord] | None = None) -> None:
        self.games = list(games or [])

    @staticmethod
    def _get_meta_value(game: GameRecord, section: str, key: str,
                        fallback: Any = "") -> Any:
        """Helper to safely extract metadata values."""
        return get_meta_value(getattr(game, "meta_config", {}), section, key, fallback)

    def get_available_letters(self) -> list[str]:
        """The groups present, through `letter_of` so the list and matcher agree."""
        return sorted({letter_of(game) for game in self.games})

    def get_available_themes(self) -> list[str]:
        """Return sorted list of unique themes from all games."""
        themes = set()
        for game in self.games:
            themes.update(game_themes(game))
        return sorted(themes)

    def get_available_types(self) -> list[str]:
        """Return sorted list of unique game types."""
        types = set()
        for game in self.games:
            current_type = game_type(game)
            if current_type:
                types.add(current_type)
        return sorted(types)

    def get_available_manufacturers(self) -> list[str]:
        """Return sorted list of unique manufacturers."""
        manufacturers = set()
        for game in self.games:
            manufacturer = game_manufacturer(game)
            if manufacturer:
                manufacturers.add(manufacturer)
        return sorted(manufacturers)

    def get_available_years(self) -> list[str]:
        """Return sorted list of unique years."""
        years = set()
        for game in self.games:
            year = game_year(game)
            if year:
                years.add(str(year))
        return sorted(years)

    def available_options(self) -> dict[str, list[str]]:
        """Every choice axis and the values this library actually holds.

        One answer for the frontend and the API both, so a filter offered on one surface
        is a filter the other can resolve - and derived from `AXES`, so an axis that
        declares where its values come from needs no second entry anywhere.
        """
        found: dict[str, set[str]] = {}
        for axis in AXES:
            if not (axis.values_of and axis.values_key):
                continue
            seen = found.setdefault(axis.values_key, set())
            for game in self.games:
                seen.update(str(value) for value in axis.values_of(game) if value)
        return {key: sorted(values) for key, values in found.items()}

    def value_counts(self) -> dict[str, dict[str, int]]:
        """How many games hold each of those values, keyed the way `available_options`
        keys them."""
        counted: dict[str, dict[str, int]] = {}
        for axis in AXES:
            if not (axis.values_of and axis.values_key):
                continue
            held = counted.setdefault(axis.values_key, {})
            for game in self.games:
                for value in {str(value) for value in axis.values_of(game) if value}:
                    held[value] = held.get(value, 0) + 1
        return counted

    def _get_game_name(self, game: GameRecord) -> str:
        """Get game name from either JSON or legacy format."""
        return game_title(game)

    def _get_game_theme(self, game: GameRecord) -> list[str]:
        """Get game theme(s) from either JSON or legacy format."""
        return game_themes(game)

    def _get_game_type(self, game: GameRecord) -> str:
        """Get game type from either JSON or legacy format."""
        return game_type(game)

    def _get_game_manufacturer(self, game: GameRecord) -> str:
        """Get game manufacturer from either JSON or legacy format."""
        return game_manufacturer(game)

    def _get_game_year(self, game: GameRecord) -> str:
        """Get game year from either JSON or legacy format."""
        return game_year(game)

    @staticmethod
    def _normalize_rating(value: Any) -> int:
        """Normalize rating values to an integer in the range 0..10."""
        return normalize_rating(value)

    def _get_game_rating(self, game: GameRecord) -> int:
        """Get game rating from User.Rating metadata."""
        return game_rating(game)

    # The predicates live in collection_filters, so a filter collection and this class
    # cannot disagree about what "manufacturer = Williams" selects. These stay because
    # the Manager and the frontend both call them one axis at a time.

    def _by_axis(self, games: list[GameRecord], axis_name: str,
                 criterion: object) -> list[GameRecord]:
        if is_unconstrained(criterion):
            return games
        axis = AXES_BY_NAME[axis_name]
        return [game for game in games if axis.matches(criterion, game, {})]

    def filter_by_letter(self, games: list[GameRecord], letter: object) -> list[GameRecord]:
        """Filter games by starting letter of name. Supports comma-separated values."""
        return self._by_axis(games, "letter", letter)

    def filter_by_theme(self, games: list[GameRecord], theme: object) -> list[GameRecord]:
        """Filter games by theme. Supports comma-separated values."""
        return self._by_axis(games, "theme", theme)

    def filter_by_type(self, games: list[GameRecord], game_type: object) -> list[GameRecord]:
        """Filter games by type (EM, SS, etc.). Supports comma-separated values."""
        return self._by_axis(games, "game_type", game_type)

    def filter_by_manufacturer(self, games: list[GameRecord],
                               manufacturer: object) -> list[GameRecord]:
        """Filter games by manufacturer. Supports comma-separated values."""
        return self._by_axis(games, "manufacturer", manufacturer)

    def filter_by_year(self, games: list[GameRecord], year: object) -> list[GameRecord]:
        """Filter games by year. Supports comma-separated values."""
        return self._by_axis(games, "year", year)

    def filter_by_rating(self, games: list[GameRecord], rating: object,
                         rating_or_higher: Any = False) -> list[GameRecord]:
        """Filter games by rating, optionally reading it as a floor."""
        if is_unconstrained(rating):
            return games
        axis = AXES_BY_NAME[
            "rating_or_higher" if is_truthy(rating_or_higher) else "rating"]
        return [game for game in games if axis.matches(rating, game, {})]

    def apply_filters(
            self, letter: str | None = None, theme: str | None = None,
            game_type: str | None = None, manufacturer: str | None = None,
            year: str | None = None, rating: str | None = None,
            rating_or_higher: Any = False) -> list[GameRecord]:
        """
        Apply multiple filters in combination.
        Returns filtered and sorted list of games.
        """
        result = list(self.games)  # Make a copy to avoid modifying original

        # Apply each filter sequentially
        if letter and letter != "All":
            result = self.filter_by_letter(result, letter)

        if theme and theme != "All":
            result = self.filter_by_theme(result, theme)

        if game_type and game_type != "All":
            result = self.filter_by_type(result, game_type)

        if manufacturer and manufacturer != "All":
            result = self.filter_by_manufacturer(result, manufacturer)

        if year and year != "All":
            result = self.filter_by_year(result, year)

        if rating and rating != "All":
            result = self.filter_by_rating(result, rating, rating_or_higher)

        # Sort alphabetically by name
        result.sort(
            key=lambda t: collation.sort_key(self._get_game_name(t))
        )

        return result


# What kind of group each order falls into. The two vocabularies name the same thing
# differently - an order says `title`, the groups it makes are `letter`. An order absent
# here has no groups: every timestamp is its own, and a curated array has no boundaries.
GROUP_KIND_FOR_ORDER = {"title": "letter", "year": "year", "rating": "rating"}


def group_kind(order_by: str) -> str:
    """What kind of group this order has - `letter`, `year`, `rating` - or "" for none."""
    # Deferred: collection_store imports this module, so a top-level import would loop.
    from common.games.collection_store import ORDER_ALIASES
    return GROUP_KIND_FOR_ORDER.get(ORDER_ALIASES.get(order_by, order_by), "")


def group_key(order_by: str) -> Callable | None:
    """What group a game falls in under this order, or None if the order has none."""
    axis = AXES_BY_NAME.get(group_kind(order_by))
    return axis.groups if axis else None
