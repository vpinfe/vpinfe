"""Filter criteria, translated from the wire into what the store and matcher read.

One translation, shared by what writes a collection and what previews one, so a rule
cannot resolve differently before and after it is saved.
"""

from __future__ import annotations

from typing import Any

from common.games.collection_filters import UNCONSTRAINED, is_unconstrained


def many_in(value: Any) -> str:
    """A criterion as it is stored. A list joins; a string is already stored form."""
    if isinstance(value, list):
        joined = ",".join(str(part).strip() for part in value if str(part).strip())
        return joined or UNCONSTRAINED
    return str(value or UNCONSTRAINED)


def range_in(value: Any) -> dict[str, int]:
    """A range as it is stored: the ends that are set, and nothing for an open one."""
    if value is None:
        return {}
    ends = {"from": value.from_, "to": value.to}
    return {end: year for end, year in ends.items() if year is not None}


def criteria_for(f: Any) -> dict:
    """A criteria block in the shape the store and the matcher read."""
    if f is None:
        return {}
    said = {"letter": many_in(f.letter), "theme": many_in(f.theme),
            "game_type": many_in(f.game_type),
            "manufacturer": many_in(f.manufacturer), "year": many_in(f.year),
            "rating": f.rating,
            "rating_or_higher": "true" if f.rating_or_higher else None,
            "played": f.played, "favorite": f.favorite, "tags": many_in(f.tags),
            "year_range": range_in(f.year_range),
            "theme_none_of": many_in(f.theme_none_of),
            "game_type_none_of": many_in(f.game_type_none_of),
            "manufacturer_none_of": many_in(f.manufacturer_none_of),
            "year_none_of": many_in(f.year_none_of),
            "tags_none_of": many_in(f.tags_none_of)}
    return {name: value for name, value in said.items() if not is_unconstrained(value)}
