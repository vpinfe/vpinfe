"""One collection resolved into the entries every window shows, cached.

Three windows onto one library were three copies that happened to agree, each deriving
the same answer. Only the controller takes input, so only one could ever change it. The
window keeps its name, its socket and its browser; this is everything else.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

from common.config.config_access import ConfigSource, NetworkConfig
from common.games import (
    collection_filters,
    collection_resolver,
    game_identity,
    rankings,
    remote_library,
)
from common.games.collection_store import (
    BUILTIN_ALL,
    DEFAULT_DIRECTION,
    DEFAULT_ORDER_BY,
    MANUAL_ORDER,
    ORDER_BY_KEY,
    ORDER_DIRECTION_KEY,
    ORDER_PAGING_GROUP_KEY,
    CollectionStore,
    normalize_direction,
    normalize_paging_group,
    public_name,
)
from common.games.collections_service import (
    WHEELS_SHOWN,
    get_collection_image_url,
    get_collections_manager,
    get_frontend_collections,
    offered,
    save_filter_collection,
)
from common.games.game_repository import all_games
from common.games.media_lookup import resolved_kinds
from common.i18n import t
from common.service_errors import BlockedError, NotFoundError, ServiceError
from common.values import is_truthy
from frontend import game_state

logger = logging.getLogger("vpinfe.frontend.library_resolver")


def library_url(ini_config: ConfigSource) -> str:
    """The install this one reads its library from, or "" when it holds its own."""
    try:
        return NetworkConfig.from_config(ini_config.config).library_url
    except Exception:
        logger.debug("Could not read the library URL; holding a local library",
                     exc_info=True)
        return ""


_MANY_AXES = ("letter", "theme", "game_type", "manufacturer", "year", "tags",
              "theme_none_of", "game_type_none_of", "manufacturer_none_of", "year_none_of",
              "tags_none_of")


def _chosen(value: Any) -> tuple[str, ...]:
    items = value if isinstance(value, (list, tuple)) else str(value or "").split(",")
    return tuple(sorted({str(item).strip() for item in items} - {"", "All"}))


def _flag(value: Any) -> bool | None:
    return None if value is None else is_truthy(value)


def rules(filters: dict[str, Any], order_by: str, direction: str) -> tuple:
    """A collection's rules in one shape, whether a store holds them or a library's API
    reported them."""
    rating = str(filters.get("rating") or "All")
    return (tuple(_chosen(filters.get(axis)) for axis in _MANY_AXES),
            rating, rating != "All" and is_truthy(filters.get("rating_or_higher")),
            _flag(filters.get("played")), _flag(filters.get("favorite")),
            bool(filters.get("year_range")),
            str(order_by or DEFAULT_ORDER_BY), normalize_direction(direction))


class LibraryResolver:
    """The library, the filter, the sort, and the list they produce.

    Mutation is serialized: the bridge runs each window's call on its own thread, so
    one shared view makes a sort and a read genuinely concurrent.
    """

    def __init__(self, ini_config: ConfigSource,
                 games: list[Any] | None = None) -> None:
        self._ini_config = ini_config
        self.lock = threading.RLock()

        # With a library set, the list this install holds is entries that install
        # resolved, not games off a disk this one may not have.
        self._library_url = library_url(ini_config)
        self._remote = bool(self._library_url)
        # A library's entries arrive one collection at a time: which one `all_games` is.
        self._held = BUILTIN_ALL
        self._whole: list[Any] | None = None
        self._orders: dict[str, dict[str, Any]] = {}
        # The collection the last reload was told that install no longer has.
        self._gone = ""

        # An unreadable library is empty, not fatal: a first run before the scan has
        # none, and wants a view it can fill in rather than an exception.
        if games is not None:
            self.all_games = games
        else:
            try:
                self.all_games = self._load()
            except Exception:
                logger.debug("No library to build a view from yet", exc_info=True)
                self.all_games = []
        self.filtered_games: list = []
        self.current_filters = game_state.default_filter_state()
        self.current_collection = BUILTIN_ALL
        self.current_sort = DEFAULT_ORDER_BY
        self.current_order = DEFAULT_DIRECTION

        # `_entries_source` alone says whether a view has ever been built: it is the
        # list `_entries` was derived from, and None until there is one.
        self._entries: list[collection_resolver.Entry] = []
        self._entries_source: list | None = None
        self._payload: str | None = None
        self._payload_key: tuple | None = None
        self._stale = True

        self.reset_to_default()

    def _load(self, collection: str = "") -> list[Any]:
        """The library: another install's entries, or the local games. Different kinds of
        thing, which `rebuild_entries` knows."""
        if self._remote:
            entries = self._ask(remote_library.fetch_entries, collection)
            if not collection:
                self._whole = entries
            return entries
        return all_games()

    def _ask(self, call: Callable[..., Any], *args: Any) -> Any:
        """`call` against the library. A refusal the library words raises as its
        ServiceError, and any other failure as BlockedError."""
        try:
            return call(self._library_url, *args)
        except ServiceError:
            raise
        except Exception as exc:
            logger.debug("The library at %s did not answer", self._library_url,
                         exc_info=True)
            raise BlockedError(t("error.frontend.library_unreachable",
                                 url=self._library_url)) from exc

    @property
    def remote(self) -> bool:
        return self._remote

    def reload(self) -> list[Any]:
        """The library again. A library that has gone quiet leaves the list alone: a
        stale wheel beats a screen emptying because one request failed."""
        self._gone = ""
        try:
            self.all_games = self._load(public_name(self.current_collection))
            self._held = self.current_collection
        except NotFoundError:
            self._gone = self.current_collection
        except Exception:
            logger.debug("Could not reload the library; keeping what is shown",
                         exc_info=True)
        return self.all_games

    def whole(self) -> list[Any]:
        if self._remote and self._whole is not None:
            return self._whole
        return self.all_games

    def collections(self) -> CollectionStore:
        """This install's collections. One place to ask, so the view and the resolver
        behind it cannot end up reading two different files."""
        return get_collections_manager()

    def offered(self, showing: str = "") -> list[dict[str, Any]]:
        """The collections the frontend offers, and `showing` whether offered or not.
        A library's rows arrive glanced."""
        if not self._remote:
            return get_frontend_collections(showing)
        try:
            resources = remote_library.fetch_collections(self._library_url)
        except Exception:
            logger.warning("Could not list the library's collections", exc_info=True)
            return []
        return offered([remote_library.metadata_row(resource)
                        for resource in resources], showing)

    def image_url(self, collection: str) -> str:
        if not self._remote:
            return get_collection_image_url(collection)
        try:
            resource = remote_library.fetch_collection(self._library_url, collection)
        except Exception:
            logger.debug("No image for %r from the library", collection, exc_info=True)
            return ""
        return str(remote_library.metadata_row(resource)["image_url"])

    def stored(self, name: str) -> tuple[dict | None, dict[str, Any]] | None:
        """A collection's criteria and its order block, or None when there is no
        collection by that name. A library's criteria stay over there, and its ranked
        orders arrive ranked. BlockedError when the library cannot be asked."""
        if not self._remote:
            store = self.collections()
            if name not in store:
                return None
            return store.get_filters(name), store.get_order(name)
        order: dict[str, Any] = {ORDER_BY_KEY: DEFAULT_ORDER_BY,
                                 ORDER_DIRECTION_KEY: DEFAULT_DIRECTION,
                                 ORDER_PAGING_GROUP_KEY: None}
        if name != BUILTIN_ALL:
            try:
                resource = self._ask(remote_library.fetch_collection, name)
            except NotFoundError:
                return None
            by = str(resource.get("order_by") or DEFAULT_ORDER_BY)
            order = {ORDER_BY_KEY: MANUAL_ORDER if rankings.is_token(by) else by,
                     ORDER_DIRECTION_KEY: normalize_direction(
                         resource.get("direction") or DEFAULT_DIRECTION),
                     ORDER_PAGING_GROUP_KEY: normalize_paging_group(
                         resource.get("paging_group") or None)}
        self._orders[name] = order
        return None, order

    def save_filter(self, name: str, criteria: dict[str, Any], order_by: str,
                    direction: str) -> None:
        """The filter menu's controls saved as a collection in the library this install
        reads. `criteria` is keyed as `save_filter_collection` takes it."""
        if not self._remote:
            # The stored criteria keys are still 2.x's `sort_by` and `order_by`, where
            # `order_by` is the direction. That is on disk and stays.
            save_filter_collection(name, **criteria, sort_by=order_by, order_by=direction)
            return
        self._ask(remote_library.create_collection,
                  {"name": name, "filters": {**criteria, "order_by": order_by,
                                             "direction": direction}})

    def keep_filter(self, name: str, criteria: dict[str, Any], order_by: str,
                    direction: str) -> tuple[str, bool]:
        """`save_filter` once per set of rules: the name a collection holding exactly
        these rules already has, and True; else the rules saved under `name` or the next
        free `name N`, and False."""
        wanted = rules(criteria, order_by, direction)
        held = self._rules_by_name()
        for existing, theirs in held.items():
            if theirs == wanted:
                return existing, True
        kept, number = name, 1
        while kept in held:
            number += 1
            kept = f"{name} {number}"
        self.save_filter(kept, criteria, order_by, direction)
        return kept, False

    def _rules_by_name(self) -> dict[str, tuple | None]:
        """Every collection's name, with its rules where it is made by rules alone."""
        if self._remote:
            return {str(row["name"]): rules(row["filters"], row.get("order_by", ""),
                                            row.get("direction", ""))
                    if row.get("filters") and not row.get("added")
                    and not row.get("excluded") and row.get("limit") is None else None
                    for row in self._ask(remote_library.fetch_collections)}
        store = self.collections()
        found: dict[str, tuple | None] = {}
        for name in store.get_collections_name():
            filters = store.get_filters(name)
            alone = (filters is not None and not store.get_member_refs(name)
                     and not store.get_excluded_refs(name) and store.get_limit(name) is None)
            order = store.get_order(name)
            found[name] = (rules(filters, order[ORDER_BY_KEY], order[ORDER_DIRECTION_KEY])
                           if filters is not None and alone else None)
        return found

    def paging_group(self, name: str) -> str | None:
        """How a page press moves through `name`, or None to follow the player. A
        library's is the one it gave when the collection was chosen."""
        if self._remote:
            return (self._orders.get(name) or {}).get(ORDER_PAGING_GROUP_KEY)
        try:
            return self.collections().get_order(name)[ORDER_PAGING_GROUP_KEY]
        except (KeyError, ValueError):
            return None

    def resolve_view(self, collection: str, criteria: dict | None = None) -> list:
        """The entries a collection holds, off this install's library.

        A remote library's are that install's answer, kept as it arrived: the resolver
        reads a game's table dicts out of its `.info` and those stayed over there, so
        re-resolving here would quietly produce an empty wheel. Criteria are matched
        against each entry instead.

        `criteria` is the filter menu's controls, which make a collection out of the
        library rather than narrowing one - so they only arrive with `builtin:all`.
        """
        if self._remote:
            if collection != self._held:
                self.all_games = self._load(public_name(collection))
                self._held = collection
            return [entry for entry in self.all_games
                    if collection_filters.matches(criteria, entry.game)]
        store = self.collections()
        if criteria:
            store.set_view_filters(criteria)
        return collection_resolver.resolve(collection, store, self.all_games)

    def glance(self, names: list[str]) -> dict[str, dict[str, Any]]:
        """How many entries each collection resolves to here, and the first few wheels."""
        unknown: dict[str, Any] = {"table_count": None, "game_wheel_urls": []}
        found: dict[str, dict[str, Any]] = {}
        if self._remote:
            found = {name: dict(unknown) for name in names}
            if BUILTIN_ALL in found and self._whole is not None:
                found[BUILTIN_ALL] = remote_library.glance(self._whole)
            return found
        store = self.collections()
        for name in names:
            try:
                entries = collection_resolver.resolve(name, store, self.all_games)
            except collection_resolver.UnresolvableCollectionError:
                found[name] = dict(unknown)
                continue
            wheels: list[str] = []
            for entry in entries:
                if len(wheels) == WHEELS_SHOWN:
                    break
                target = entry.table_id or game_identity.game_id(entry.game)
                if target and "wheel" in resolved_kinds(entry.game, entry.table_id):
                    wheels.append(f"/media/{quote(target, safe='')}/wheel")
            found[name] = {"table_count": len(entries), "game_wheel_urls": wheels}
        return found

    # -- staleness -----------------------------------------------------------

    def mark_stale(self) -> None:
        """Say the library may have moved. One broadcast, one refresh - not one per
        window answering it."""
        with self.lock:
            self._stale = True

    def refresh_if_stale(self, refresh: Callable[[], None]) -> None:
        """Run `refresh` only if nothing has since. `refresh` takes no arguments."""
        with self.lock:
            if not self._stale:
                return
            self._stale = False
            refresh()

    # -- derivation ----------------------------------------------------------

    def rebuild_entries(self) -> None:
        """Recompute the list the wheel steps through. Call after a sort: it mutates in
        place, so the change is otherwise undetectable."""
        games = self.filtered_games or []
        self._entries = list(games)
        self._entries_source = games
        self._payload = None

    @property
    def entries(self) -> list[collection_resolver.Entry]:
        """What an index from a theme addresses. Rebuilt when the source list is
        replaced, so swapping `filtered_games` cannot leave a stale view behind.

        Under the lock: a sort mutates `filtered_games` in place, so a rebuild racing one
        walks a list being reordered - and a reader can otherwise see `_entries` assigned
        before `_entries_source` catches up. The window is small enough to pass under a
        light load and fail under a heavy one.
        """
        with self.lock:
            games = self.filtered_games or []
            if self._entries_source is not games:
                self.rebuild_entries()
            return self._entries

    def reset_to_default(self) -> None:
        """The whole library, by the (article-reordered) title, ascending. Resetting
        drops whatever collection or filter was on screen - that is what it is for. The
        list is the resolver's own, so an in-place sort never disturbs the library behind
        it; the Game objects stay shared, so a rating update still reaches every reader."""
        with self.lock:
            games = self.resolve_view(BUILTIN_ALL)
            self.current_collection = BUILTIN_ALL
            self.current_filters = game_state.default_filter_state()
            self.current_sort = DEFAULT_ORDER_BY
            self.current_order = DEFAULT_DIRECTION
            self.filtered_games = games
            self.rebuild_entries()

    def show_all_if_gone(self) -> bool:
        """Fall back to all games when the collection on screen is gone. False when it is
        not, and when the library cannot be asked: the view stays as it is."""
        with self.lock:
            name = self.current_collection
            if name == BUILTIN_ALL:
                return False
            if self._remote:
                if self._gone != name:
                    return False
            elif name in self.collections():
                return False
            try:
                self.reset_to_default()
            except BlockedError:
                logger.debug("The library went quiet after dropping %r; keeping what is shown",
                             name, exc_info=True)
                return False
            return True

    # -- the payload ---------------------------------------------------------

    def payload(self, contract: int, *, collection: str = "") -> str:
        """The theme payload, built once however many windows ask. Cleared by
        `rebuild_entries`, which every change to the list goes through."""
        # The order is in the key because it decides the groups stamped on each entry.
        key = (contract, collection, self.current_sort)
        with self.lock:
            if self._payload is None or self._payload_key != key:
                self._payload = game_state.games_json(
                    self.entries, contract, collection=collection,
                    order_by=self.current_sort)
                self._payload_key = key
            return self._payload

    def invalidate_payload(self) -> None:
        """Drop the built payload without rebuilding the list behind it."""
        with self.lock:
            self._payload = None
