"""Every release VPS lists, by game and kind, with who made it and what it is.

Per release: its authors, whether it is a mod, its features, its table format, its version
and the date VPS added it. An author is a person, not a spelling, so people are listed once
in `people` and a release names them by position in that list, in the order VPS credits them.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Collection, Iterable
from typing import Any

from common import timestamps
from common.games import mods
from common.games.game_service import load_vpsdb
from common.online import vps_kinds

TABLES = "tableFiles"
LISTINGS = (TABLES, *vps_kinds.BY_LISTING)

# The names a person is also found under, beyond spelling: the first name here is the one
# shown, and the others are credits VPS files separately for the same work. Searching any
# of them finds the person.
SAME_PERSON: tuple[tuple[str, ...], ...] = (
    ("VPW", "VPinWorkshop", "VPW Team", "VPW Testers"),
)

_SPACES = re.compile(r"\s+")

_built: tuple[list[dict], dict[str, Any]] | None = None


def person_key(name: str) -> str:
    """The key two spellings of one name share: spacing and case do not tell people apart."""
    return _SPACES.sub(" ", str(name or "")).strip().casefold()


_FOLDED = {person_key(alias): person_key(group[0])
           for group in SAME_PERSON for alias in group}


def credit_keys(names: Iterable[object] | None) -> frozenset[str]:
    """The people a list of credits names, as keys: two spellings of one person, and the
    credits VPS files separately for one team, come out as the same key."""
    return frozenset(_FOLDED.get(person_key(str(name)), person_key(str(name)))
                     for name in names or () if person_key(str(name)))


def _credited(release: dict) -> list[str]:
    names = (_SPACES.sub(" ", str(one)).strip() for one in (release.get("authors") or []))
    return [one for one in names if one]


def _people(entries: Iterable[dict]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Each person once, ordered by name, and the position of every key in that order.

    The name shown is the spelling most releases use, or the first of `SAME_PERSON`'s
    group. Every other spelling and credit is an alias. `releases` counts a release once
    per person, whichever of their spellings it credits."""
    spellings: dict[str, Counter[str]] = {}
    releases: Counter[str] = Counter()
    for entry in entries:
        for listing in LISTINGS:
            for release in entry.get(listing) or []:
                credited = set()
                for name in _credited(release):
                    key = _FOLDED.get(person_key(name), person_key(name))
                    spellings.setdefault(key, Counter())[name] += 1
                    credited.add(key)
                releases.update(credited)
    named = {key: _shown(key, found) for key, found in spellings.items()}
    order = sorted(spellings, key=lambda key: (named[key].casefold(), key))
    people = []
    for key in order:
        found = spellings[key]
        also = {alias for group in SAME_PERSON if person_key(group[0]) == key
                for alias in group[1:]}
        also |= {name for name in found if name != named[key]}
        people.append({"name": named[key],
                       "aliases": sorted(also - {named[key]}, key=str.casefold),
                       "releases": releases[key]})
    return people, {key: position for position, key in enumerate(order)}


def _shown(key: str, found: Counter[str]) -> str:
    for group in SAME_PERSON:
        if person_key(group[0]) == key:
            return group[0]
    return max(found, key=lambda name: (found[name], name))


def _release(release: dict, positions: dict[str, int]) -> dict[str, Any]:
    by = dict.fromkeys(positions[_FOLDED.get(person_key(name), person_key(name))]
                       for name in _credited(release))
    return {
        "id": str(release.get("id") or ""),
        "by": list(by),
        "mod": mods.is_mod(release),
        "features": [str(word) for word in (release.get("features") or [])],
        "format": str(release.get("tableFormat") or ""),
        "version": str(release.get("version") or "").strip(),
        "added": timestamps.epoch_to_iso(int(release["createdAt"] // 1000))
        if isinstance(release.get("createdAt"), (int, float)) else "",
    }


def index() -> dict[str, Any]:
    """`{"people": [...], "entries": {vps_id: {listing: [release, ...]}}}` for the catalog
    as loaded, built once per load. An entry listing no release is left out, and so is a
    listing with none."""
    global _built
    catalog = load_vpsdb()
    if _built is not None and _built[0] is catalog:
        return _built[1]
    people, positions = _people(catalog)
    entries: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for entry in catalog:
        vps_id = str(entry.get("id") or "")
        listed = {listing: [_release(one, positions) for one in entry[listing]]
                  for listing in LISTINGS if entry.get(listing)}
        if vps_id and listed:
            entries[vps_id] = listed
    built = {"people": people, "entries": entries}
    _built = (catalog, built)
    return built


def of_entry(built: dict[str, Any], vps_id: str,
             held: Collection[str]) -> dict[str, list[dict[str, Any]]]:
    """One entry's releases by listing out of `built` (what `index` answered), each saying
    whether `held` names it."""
    return {listing: [{**one, "held": one["id"] in held} for one in found]
            for listing, found in (built["entries"].get(vps_id) or {}).items()}
