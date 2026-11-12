"""What the wheel is currently showing - the filter, the sort, and the games left."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any

from common.extensions import contributions
from common.games import collection_filters, collection_resolver, derived_tags, game_identity
from common.games.collection_filters import (
    GameListFilters,
    group_key,
    group_kind,
)
from common.games.collection_resolver import Entry, visible_entries
from common.games.collection_store import (
    BUILTIN_ALL,
    DEFAULT_DIRECTION,
    DEFAULT_ORDER_BY,
    MANUAL_ORDER,
    ORDER_ALIASES,
    normalize_direction,
)
from common.games.game import Game, GameRecord, ScannedGame
from common.games.game_metadata import (
    GAME_OVERRIDES,
    contract_1_tutorial,
    game_ipdb_id,
    game_manufacturer,
    game_override,
    game_themes,
    game_title,
    game_type,
    game_year,
    guides_on_wire,
    normalize_meta,
    normalize_rating,
    play_record,
    reorder_leading_article,
    section,
    table_descriptor,
    vpinfe_section,
)
from common.games.media_lookup import page_counts, resolved_kinds
from common.games.tables import entry_file
from common.i18n import t
from common.media.manufacturer_logos import manufacturer_logo_url
from common.media.media_specs import game_media_payload
from common.timestamps import epoch_to_iso
from common.values import is_truthy
from frontend.input_api import PAGING_GROUP_ALIASES, PAGING_GROUP_DEFAULT
from frontend.theme_contract import CURRENT_CONTRACT, project

if TYPE_CHECKING:
    from frontend.api import API

logger = logging.getLogger("vpinfe.frontend.game_state")




# The theme's sort names, from the order a collection stores. Choosing a collection
# applies its order once, and these are what the sort UI and the letter jump read back.
# are read on the way in and must never be answered with.



def default_filter_state() -> dict[str, Any]:
    return {
        "letter": None,
        "theme": None,
        "type": None,
        "manufacturer": None,
        "year": None,
        "rating": None,
        "rating_or_higher": False,
    }


def _legacy_row(game: Game, logo_cache: dict[str, str | None]) -> dict:
    """One row in the shape every published theme reads.

    Built from the game exactly as it always was. Contract 2 is assembled separately
    rather than projected from this: twelve published themes depend on this shape and
    the parity gate holds it against master, so it does not go behind a transformation.
    """
    # A copy: what follows adds fields the theme contract defines, and writing those
    # into the shared meta_config would put a dropped section back on disk at the next
    # rebuild.
    meta = dict(normalize_meta(game.meta_config))
    info = section(meta, "Info")
    held_user = meta.get("User")
    if isinstance(held_user, dict) and isinstance(held_user.get("Rating"), float):
        meta["User"] = {**held_user, "Rating": normalize_rating(held_user["Rating"])}

    # This shape has one slot per field, so a user's answer is resolved into it here.
    # A theme on this contract would otherwise read the catalog's value while every
    # other surface shows the user's.
    used_alttitle = False
    for field, (_, said) in GAME_OVERRIDES.items():
        answered = game_override(meta, field)
        if not answered:
            continue
        info[said] = answered
        meta["Info"] = info
        used_alttitle = used_alttitle or field == "title"

    # Reorder a leading "The " on the canonical Info.Title so the theme displays and
    # sorts by the second word. A user-set alttitle is left exactly as entered.
    if not used_alttitle and info.get("Title"):
        info["Title"] = reorder_leading_article(info["Title"])
        meta["Info"] = info

    row = {
        "gameDirName": game.game_dir_name,
        "fullPathGame": game.full_path_game,
        "fullPathVPXfile": game.full_path_vpx_file,
        "pupPackExists": game.pup_pack_exists,
        "altColorExists": game.alt_color_exists,
        "altSoundExists": game.alt_sound_exists,
        "meta": meta,
    }
    row.update(game_media_payload(game))
    maker = game_manufacturer(game)
    if maker not in logo_cache:
        logo_cache[maker] = manufacturer_logo_url(maker)
    row["ManufacturerLogoPath"] = logo_cache[maker]
    return row


def _default_id(game: ScannedGame) -> str:
    """Which of a game's tables is its default. Resolved the same way the REST lens
    resolves it, so a theme showing siblings agrees with an API client about which."""
    offered = visible_entries(game)
    return str(offered[0].get("id", "") or "") if offered else ""


def _entry_row(entry: Entry, logo_cache: dict[str, str | None],
               group: Callable[[GameRecord], Any] | None = None) -> dict:
    """One entry in contract 2: the game, the table it is, and what resolved for it."""
    game = entry.game
    meta = normalize_meta(game.meta_config)
    info = section(meta, "Info")
    vpinfe = vpinfe_section(meta)
    maker = game_manufacturer(game)
    if maker not in logo_cache:
        logo_cache[maker] = manufacturer_logo_url(maker)
    return {
        "game": {
            "id": game_identity.game_id(game),
            "vps_id": str(info.get("VPSId", "") or ""),
            "name": game_title(game),
            "manufacturer": maker,
            "year": game_year(game),
            "type": game_type(game),
            "themes": game_themes(game),
            "dir_name": game.game_dir_name,
            "path": game.full_path_game,
            "manufacturer_logo": logo_cache[maker],
            "created_at": epoch_to_iso(getattr(game, "creation_time", None)) or None,
            "user": play_record(meta),
            "derived_tags": derived_tags.game_tags(game),
            # Everything the record declares about the machine. These were absent for
            # no reason anybody chose - a projection listed some fields and stopped -
            # and an extension, a remote frontend and the Manager all read this.
            "ipdb_id": game_ipdb_id(game),
            "tutorial": contract_1_tutorial(meta),
            "guides": guides_on_wire(meta),
            "overrides": {
                "alt_title": str(vpinfe.get("alt_title", "") or ""),
                "alt_vps_id": str(vpinfe.get("alt_vpsid", "") or ""),
                "frontend_dof_event": str(vpinfe.get("frontend_dof_event", "") or ""),
            },
        },
        # `path` is the theme's alone: a local frontend opens the file, and REST cannot
        # carry a path that means anything on another machine.
        "table": table_descriptor(entry.table, default_id=_default_id(game)) | {
            "path": entry_file(str(game.full_path_game or ""), entry.table)},
        "assets": {
            "pup_pack": bool(game.pup_pack_exists),
            "alt_color": bool(game.alt_color_exists),
            "alt_sound": bool(game.alt_sound_exists),
        },
        "siblings": entry.siblings,
        # Which art exists, not where it lives. The URL is /media/<table id>/<kind> and the
        # bytes are fetched when something is shown - naming the files here would put a
        # filesystem path in a web page and several hundred kilobytes on the wire.
        "media": resolved_kinds(game, entry.table_id),
        "pages": page_counts(game, entry.table_id),
        # None when the order has no groups; `group_by` on the payload says which.
        "group": group,
        # What extensions have contributed about this game. Always here and empty at
        # load, because a list of four hundred cannot wait on four hundred calls to
        # somebody else's server - a theme written as `if (entry.ext.rating)` is right
        # throughout and never has to know there is a waiting state.
        "ext": contributions.held(game_identity.game_id(game)),
    }


def games_json(entries: Sequence[Entry], contract: int = CURRENT_CONTRACT, *,
               collection: str = "", order_by: str = DEFAULT_ORDER_BY) -> str:
    """The theme payload, at the contract the theme asked for.

    Contract 1 is a bare array of rows, one per game - what every published theme
    reads, and what the parity gate compares against master.
    """
    logo_cache: dict[str, str | None] = {}
    if contract < CURRENT_CONTRACT:
        return json.dumps([project(_legacy_row(e.game, logo_cache), contract)
                           for e in entries])
    # The group comes from the order the list is built with, so it is correct for as long
    # as the list is and a wheel step costs no round trip to know where it sits.
    key = group_key(order_by)
    return json.dumps({
        "collection": collection,
        "count": len(entries),
        # Empty when the order has no groups, so a theme can tell "no grouping here"
        # from "the group happens to be empty".
        "group_by": group_kind(order_by) if key is not None else "",
        "entries": [_entry_row(e, logo_cache, key(e.game) if key else None)
                    for e in entries],
    })


def sort_state(order: dict) -> tuple[str, str]:
    """The order a collection resolves by, and its direction.

    One vocabulary, so there is nothing to translate and nothing to lose. The five 2.x
    sort names cannot express year, rating or play time, and collapsing those to `Alpha`
    reports a title sort for a collection ordered by something else.

    `manual` passes through: apply_sort leaves the curator's array alone, deliberately
    rather than by not recognizing the name.
    """
    return order["by"], order["direction"]


def _filter_state(criteria: dict | None) -> dict:
    """The menu's filter controls for a collection: what it selects on, or nothing."""
    if not criteria:
        return default_filter_state()
    return {
        "letter": criteria["letter"],
        "theme": criteria["theme"],
        "type": collection_filters.criterion(criteria, "game_type", "All"),
        "manufacturer": criteria["manufacturer"],
        "year": criteria["year"],
        "rating": criteria.get("rating", "All"),
        "rating_or_higher": is_truthy(criteria.get("rating_or_higher", "false")),
    }


def _criteria(filters: dict) -> dict:
    """The filter controls as criteria, in the shape `save_filter_collection` stores."""
    return {
        "letter": filters["letter"],
        "theme": filters["theme"],
        "game_type": filters["type"],
        "manufacturer": filters["manufacturer"],
        "year": filters["year"],
        "rating": filters["rating"],
        "rating_or_higher": "true" if filters["rating_or_higher"] else "false",
    }


def apply_collection(api: API, collection: str) -> None:
    """Show a collection: what it holds, in the order it says, applied once.

    A collection that filters also fills the menu's controls, so what it selects is
    visible and can be adjusted from there. Adjusting leaves the collection rather than
    narrowing it - see `apply_filters`.
    """
    name = collection or BUILTIN_ALL
    games = api.library.resolve_view(name)
    stored = api.library.stored(name)
    api.current_collection = name
    if stored:
        criteria, order = stored
        api.current_filters = _filter_state(criteria)
        api.current_sort, api.current_order = sort_state(order)
    else:
        api.current_sort, api.current_order = MANUAL_ORDER, DEFAULT_DIRECTION
    api.filtered_games = games
    api._rebuild_entries()


def _current_membership(api: API) -> list[Any]:
    """The entries the current view holds, off the library as it now stands."""
    if api.current_collection == BUILTIN_ALL:
        # The controls make a collection out of the *library* rather than narrowing the
        # one on screen, so they are criteria only here. A stored collection carries its
        # own, and `current_filters` is then just what the menu should show.
        return api.library.resolve_view(BUILTIN_ALL, _criteria(api.current_filters))
    return api.library.resolve_view(api.current_collection)


def rebuild_view(api: API) -> None:
    """Re-derive the list from the library the view already holds.

    A collection's own stored sort is not reapplied. Choosing a collection applies it
    once; a player who sorted differently afterwards keeps that.
    """
    if api.library.show_all_if_gone():
        return
    api.filtered_games = _current_membership(api)
    apply_sort(api.filtered_games, api.current_sort, api.current_order)
    api._rebuild_entries()


def refresh_view(api: API) -> None:
    """Re-derive the current view from the library, without changing what it is.

    Membership, order and the game objects themselves all go stale: a finished session
    moves a game up a LastRun wheel, and a Manager edit replaces the object outright
    rather than mutating the one this view is holding.

    The view says where its library comes from. Reading the local one here would hand a
    install its own empty disk on the first refresh, throwing away what arrived.
    """
    api.all_games = api.library.reload()
    rebuild_view(api)


def save_current_filter_collection(api: API, name: str, letter: str, theme: str,
                                   game_type: str, manufacturer: str, year: str,
                                   order_by: str, rating: str,
                                   rating_or_higher: object,
                                   direction: str = DEFAULT_DIRECTION) -> dict[str, Any]:
    api.library.save_filter(
        name, {"letter": letter, "theme": theme, "game_type": game_type,
               "manufacturer": manufacturer, "year": year, "rating": rating,
               "rating_or_higher": is_truthy(rating_or_higher)},
        order_by, direction)
    return {"success": True, "message": t("frontend.collection.saved", name=name)}


def keep_filter_collection(api: API, name: str, letter: str, theme: str, game_type: str,
                           manufacturer: str, year: str, order_by: str, rating: str,
                           rating_or_higher: object, direction: str) -> dict[str, Any]:
    kept, already = api.library.keep_filter(
        name, {"letter": letter, "theme": theme, "game_type": game_type,
               "manufacturer": manufacturer, "year": year, "rating": rating,
               "rating_or_higher": is_truthy(rating_or_higher)},
        order_by, direction)
    return {"success": True, "name": kept, "already": already}


def filter_options(games: Sequence[GameRecord]) -> dict[str, list[str]]:
    return GameListFilters(games).available_options()


def apply_filters(api: API, letter: str | None = None, theme: str | None = None,
                  game_type: str | None = None, manufacturer: str | None = None,
                  year: str | None = None, rating: str | None = None,
                  rating_or_higher: object = None) -> int:
    """Filter the library. Setting a control is choosing a different collection - one
    made from the library - so it leaves whatever collection was on screen."""
    api.current_collection = BUILTIN_ALL
    updates = {
        "letter": letter,
        "theme": theme,
        "type": game_type,
        "manufacturer": manufacturer,
        "year": year,
        "rating": rating,
    }
    for key, value in updates.items():
        if value is not None:
            api.current_filters[key] = value
    if rating_or_higher is not None:
        api.current_filters["rating_or_higher"] = is_truthy(rating_or_higher)

    api.current_sort, api.current_order = DEFAULT_ORDER_BY, DEFAULT_DIRECTION
    api.filtered_games = _current_membership(api)
    api._rebuild_entries()
    return len(api.filtered_games)


def apply_sort(games: list[Any], order_by: str, direction: str | None = None) -> int:
    """Re-order the wheel, in place, by one of the orders a collection can carry.

    One implementation, shared with `collection_resolver`. A second sorter here would
    cover five of the eight orders and drift from the first by construction: a collection
    ordered by year or rating has no name in the 2.x vocabulary, arrives as `Alpha`, and
    the wheel sorts by title while claiming to sort by year.

    `order_by` is the collection's own token, and the 2.x spellings still arrive from the
    collection menu, so they are normalized first.
    """
    order_by = ORDER_ALIASES.get(order_by, order_by)
    # A caller that names no direction gets descending, as it always has.
    descending = normalize_direction(direction or "desc") == "desc"
    collection_resolver.order_games(games, order_by, descending)
    return len(games)


def page_jump_index(games: Sequence[Any], index: int, direction: str,
                    order_by: str = "title",
                    paging_group: str = PAGING_GROUP_DEFAULT,
                    page_size: int = 10) -> int:
    """The wheel index a page press lands on.

    Grouping by `sort` moves to the next boundary in whatever the list is ordered by -
    the next letter when ordered by title, the next year when ordered by year. Where an
    order has no groups, or the whole list is one group, there is no boundary to find and
    the press falls back to a count.

    Grouping by `count` moves `page_size`, capped at half the list so a press never wraps
    past where it started. It is the one grouping that works whatever the order is,
    because it reads no values at all. All paging is circular.
    """
    count = len(games)
    if count <= 1:
        return 0 if count else index
    index = index % count
    forward = direction != "prev"

    if PAGING_GROUP_ALIASES.get(paging_group, paging_group) == "sort":
        key = group_key(order_by)
        if key is not None:
            keys = [key(game) for game in games]
            if len(set(keys)) > 1:
                step = 1 if forward else -1
                pos = (index + step) % count
                while keys[pos] == keys[index]:
                    pos = (pos + step) % count
                if not forward:
                    # Walked back onto the previous group's last entry; rewind to its first.
                    while keys[(pos - 1) % count] == keys[pos] and (pos - 1) % count != index:
                        pos = (pos - 1) % count
                return pos

    step = min(page_size, max(1, count // 2))
    return (index + step) % count if forward else (index - step) % count



def _sort_by_numeric_meta(games: list[Any], field: str, reverse: bool) -> None:
    games.sort(key=lambda game: game_title(game).lower())
    games.sort(key=lambda game: _numeric_meta_value(game, field), reverse=reverse)


def _numeric_meta_value(game: GameRecord, field: str) -> int:
    meta = normalize_meta(getattr(game, "meta_config", {}))
    user = section(meta, "User")
    info = section(meta, "Info")
    try:
        value = int(user.get(field, info.get(field, -1 if field == "LastRun" else 0)))
    except (TypeError, ValueError):
        value = -1 if field == "LastRun" else 0
    return value


# Rating lives in common/games/game_metadata.py, addressed by game rather than by a
# position in one window's filtered list. `API.set_game_rating` converts the index and
# calls it, so a rating set from a theme and one set over HTTP are the same write.
