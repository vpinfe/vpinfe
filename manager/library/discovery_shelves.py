"""The shelves of Discovery's Home, and the arrangement a person has made of them.

A shelf is a stable id, the catalog key of its heading and the key of the line that says
what it holds. Two settings arrange them: the order, and the shelves switched off. Both
are read the way the library's other lists are, so a shelf added in a later version arrives
switched on, beside the shelf it follows by default.

`SHELVES` is in the order Home draws them until a person changes it. Drawing a shelf is
`discovery_home.fill`'s `draws`, which has to name the same ids.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

SECTION = "manager"
ORDER_KEY = "discovery_shelf_order"
HIDDEN_KEY = "hidden_discovery_shelves"

# The Settings page that arranges them.
PAGE = "library.discovery"

UPDATES = "updates"
NEW = "new"
WANTED = "wanted"
MISSING = "missing"
DONT_HAVE = "dont_have"
OTHER_VERSIONS = "other_versions"
MATCH = "match"
HEALTH = "health"
OPENED = "opened"
AUTHORS = "authors"
VIEWS = "views"


@dataclass(frozen=True)
class Shelf:
    """`id` is written into the settings file, so it is never renamed. `name` is the
    catalog key of the heading Home draws, `description` of the line Settings shows."""

    id: str
    name: str
    description: str


SHELVES: tuple[Shelf, ...] = (
    Shelf(UPDATES, "manager.discovery.updates", "manager.settings.shelf.updates.description"),
    Shelf(NEW, "manager.discovery.new_shelf", "manager.settings.shelf.new.description"),
    Shelf(WANTED, "manager.discovery.mark.wanted", "manager.settings.shelf.wanted.description"),
    Shelf(MISSING, "manager.discovery.missing_from",
          "manager.settings.shelf.missing.description"),
    Shelf(DONT_HAVE, "manager.discovery.dont_have",
          "manager.settings.shelf.dont_have.description"),
    Shelf(OTHER_VERSIONS, "manager.discovery.family",
          "manager.settings.shelf.other_versions.description"),
    Shelf(MATCH, "manager.discovery.unmatched", "manager.settings.shelf.match.description"),
    Shelf(HEALTH, "manager.discovery.health", "manager.settings.shelf.health.description"),
    Shelf(OPENED, "manager.discovery.opened_games",
          "manager.settings.shelf.opened.description"),
    Shelf(AUTHORS, "manager.discovery.authors", "manager.settings.shelf.authors.description"),
    Shelf(VIEWS, "manager.discovery.views", "manager.settings.shelf.views.description"),
)

IDS = tuple(shelf.id for shelf in SHELVES)
BY_ID = {shelf.id: shelf for shelf in SHELVES}


def listed(value: Any) -> list[str]:
    """A stored list however the settings hand it over: a list, or the comma string an ini
    holds. Anything else is no list."""
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, (list, tuple)):
        return []
    return [one for one in (str(each).strip() for each in value) if one]


def order(stored: Any) -> list[str]:
    """Every shelf, in the stored order. A name that is no shelf is dropped, and a shelf the
    stored order lacks goes straight after the shelf it follows by default, first of all
    where it has none."""
    kept: list[str] = []
    for one in listed(stored):
        if one in IDS and one not in kept:
            kept.append(one)
    for at, one in enumerate(IDS):
        if one not in kept:
            kept.insert(kept.index(IDS[at - 1]) + 1 if at else 0, one)
    return kept


def shown(settings: Mapping[str, Any] | None) -> list[str]:
    """The shelves Home draws, top to bottom, from the `manager` section of the settings."""
    held = settings if isinstance(settings, Mapping) else {}
    off = set(listed(held.get(HIDDEN_KEY)))
    return [one for one in order(held.get(ORDER_KEY)) if one not in off]
