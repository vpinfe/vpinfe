"""One-time conversions to a user's collections file.

Separate from `collection_store`, which says what a collection is allowed to be.
"""

from __future__ import annotations

import logging

from common.games.collection_store import (
    COLLECTIONS_SCHEMA,
    MANUAL_ORDER,
    ORDER_DIRECTION_MIGRATION,
    RATING_HALVES_MIGRATION,
    CollectionStore,
)

logger = logging.getLogger("vpinfe.common.games.collection_migration")

# The collection the launcher used to maintain by hand, and the name it maintained it
# under. Kept: it is what the user sees, and what `startup_collection` may point at.
LAST_PLAYED_NAME = "Last Played"
LAST_PLAYED_MIGRATION = "last_played_is_derived"

# What it becomes. The cap was the launcher's, where it destroyed the 31st play; here it
# only stops the list being shown, so raising it later surfaces plays that far back.
LAST_PLAYED_FILTERS = {"played": True}
LAST_PLAYED_ORDER = {"by": "last_played", "direction": "desc"}
LAST_PLAYED_LIMIT = 30


# Direction used to be a no-op for every sort but title and year, which read largest
# first whatever was stored. Honouring `asc` literally now would reorder those lists to
# express an intent nobody could have had, so the value they behaved as is written down.
DIRECTION_ALWAYS_APPLIED = ("title", "year", MANUAL_ORDER)


def ensure_order_direction(collections: CollectionStore) -> int:
    """Pin the direction the sorts that ignored it were actually showing. Returns how
    many collections moved."""
    if collections.has_migrated(ORDER_DIRECTION_MIGRATION):
        return 0
    if collections.schema_version() > COLLECTIONS_SCHEMA:
        logger.warning("Collections file is schema %s, newer than this build's %s; "
                       "leaving stored sort directions alone",
                       collections.schema_version(), COLLECTIONS_SCHEMA)
        return 0

    moved = []
    with collections.mutate() as store:
        for name in store.get_collections_name():
            order = store.get_order(name)
            if (order["by"] not in DIRECTION_ALWAYS_APPLIED
                    and order["direction"] == "asc"):
                store.set_order(name, order["by"], "desc")
                moved.append(name)
        store.record_migration(ORDER_DIRECTION_MIGRATION)

    if moved:
        logger.info("Recorded the descending order %s already had on screen",
                    ", ".join(repr(name) for name in moved))
    return len(moved)


def ensure_rating_halves(collections: CollectionStore) -> int:
    """Say a saved rating rule in half stars, the way a rating is kept. A rule used to name
    whole stars, 1 to 5, and now names a step of a half star, 1 to 10, so each one is doubled
    once and finds the games it always did. Returns how many collections moved."""
    if collections.has_migrated(RATING_HALVES_MIGRATION):
        return 0
    if collections.schema_version() > COLLECTIONS_SCHEMA:
        logger.warning("Collections file is schema %s, newer than this build's %s; "
                       "leaving saved rating rules alone",
                       collections.schema_version(), COLLECTIONS_SCHEMA)
        return 0

    moved = []
    with collections.mutate() as store:
        for name in store.get_collections_name():
            stars = str((store.get_filters(name) or {}).get("rating") or "").strip()
            if stars.isdigit() and 0 < int(stars):
                store.set_filter(name, "rating", str(min(5, int(stars)) * 2))
                moved.append(name)
        store.record_migration(RATING_HALVES_MIGRATION)

    if moved:
        logger.info("Rating rules now name half stars: %s",
                    ", ".join(repr(name) for name in moved))
    return len(moved)


def ensure_last_played(collections: CollectionStore) -> bool:
    """Give this file a Last Played that derives itself. Returns whether it wrote.

    Converts a hand-maintained Last Played row, in place, keeping its name, icon and
    position - or creates one when there is none. A fresh install gets it too: nothing
    creates it on first launch any more, so seeding is the only way a new user has one.

    Once. A user who deletes it, or turns it back into a hand-picked list, must not find
    it rebuilt under them at the next start.
    """
    if collections.has_migrated(LAST_PLAYED_MIGRATION):
        return False
    if collections.schema_version() > COLLECTIONS_SCHEMA:
        # Same rule the membership rekey follows: an older build does not rewrite a file
        # a newer one wrote, because it cannot know what else the newer one put in it.
        logger.warning("Collections file is schema %s, newer than this build's %s; "
                       "leaving Last Played as it is", collections.schema_version(),
                       COLLECTIONS_SCHEMA)
        return False

    with collections.mutate() as store:
        existing = LAST_PLAYED_NAME in store.get_collections_name()
        if not existing:
            store.add_collection(LAST_PLAYED_NAME)
        store.make_filter_collection(LAST_PLAYED_NAME, LAST_PLAYED_FILTERS,
                                     order=LAST_PLAYED_ORDER, limit=LAST_PLAYED_LIMIT)
        # This list was maintained by `track_game_play`, not curated, so it does not
        # survive the conversion the way a hand-picked one does.
        store.set_members(LAST_PLAYED_NAME, [])
        store.record_migration(LAST_PLAYED_MIGRATION)

    logger.info("%s %r: it now derives from the play dates in each .info",
                "Converted" if existing else "Added", LAST_PLAYED_NAME)
    return True
