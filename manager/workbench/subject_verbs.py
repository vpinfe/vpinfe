"""The verbs each kind of subject offers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import partial
from typing import Any

from manager.backups import backups as backups_page
from manager.devices import devices as devices_page
from manager.extensions import ext_row
from manager.frontend import launchers as launchers_page
from manager.frontend import players as players_page
from manager.frontend import themes as themes_page
from manager.kit import deeplink, game_tables, panel
from manager.kit.on_page import on_page
from manager.library import collection_adds, discovery_pane, file_acts, game_acts, tageditor
from manager.library import discovery as discovery_page
from manager.library.match_group import pick_a_match
from manager.workbench.collection import _collection, _written
from manager.workbench.game_table import _game_redrawn, _table_gone
from manager.workbench.tag import _tag_changed


def _tag_verbs(context: dict[str, Any]) -> list[panel.Verb]:
    return tageditor.acts(context["library"], [{**context["tag"], "tag": context["name"]}],
                          partial(_tag_changed, context))


def _collection_verbs(context: dict[str, Any]) -> list[panel.Verb]:
    # Imported here: library.collections imports library.games, which imports this module.
    from manager.library.collections import acts

    state = context["state"]

    @on_page
    async def after(then: str | None) -> None:
        if then:
            state["collection"] = then
            await _written(context, then)
            return
        reread = state.get("refresh_collections")
        if callable(reread):
            await reread()
        deeplink.sync(state)
        await context["rebuild"]()

    row = _collection(context)
    return acts(context["library"], state, [row], after)


def _page_redraw(context: dict[str, Any]) -> Callable[[], None]:
    def redraw() -> None:
        reload_page = context["state"].get("rerender")
        if callable(reload_page):
            reload_page()
    return redraw


def _launcher_verbs(context: dict[str, Any]) -> list[panel.Verb]:
    return launchers_page.acts(context["library"], context["state"], [context["launcher"]],
                               len(context.get("launchers") or []), _page_redraw(context),
                               after=context["rebuild"],
                               playing=bool(context.get("playing")))


def _theme_verbs(context: dict[str, Any]) -> list[panel.Verb]:
    return themes_page.acts(context["library"], [context["theme"]],
                            partial(themes_page.changed, context))


def _backup_verbs(context: dict[str, Any]) -> list[panel.Verb]:
    async def refresh() -> None:
        again = context["state"].get("refresh_backups")
        if callable(again):
            await again()

    return backups_page.acts(context["library"], [context["backup"]], refresh)


def _discovery_verbs(context: dict[str, Any]) -> list[panel.Verb]:
    async def after() -> None:
        again = context["state"].get("redraw_discovery")
        if callable(again):
            await again()

    return [*discovery_page.pane_acts(context["library"], context["discovery"],
                                      context.get("game_id") or "", after),
            discovery_pane.download_missing(context)]


def _player_verbs(context: dict[str, Any]) -> list[panel.Verb]:
    player = context["player"]
    return players_page.acts(context["library"], context["state"], [player],
                             _page_redraw(context),
                             accounts={str(player["id"]): context.get("accounts") or ()},
                             after=context["rebuild"])


def _device_verbs(context: dict[str, Any]) -> list[panel.Verb]:
    if context.get("library") is None:
        return []
    target = devices_page.Target(context["device"], devices_page.client_for(context),
                                 context.get("actions"), not devices_page.is_local(context))
    return devices_page.acts(context["library"], [target], _page_redraw(context))


def _game_verbs(context: dict[str, Any]) -> list[panel.Verb]:
    library, game_id, state = context["library"], context["game_id"], context["state"]
    name = str(context["game"].get("name") or "")
    offer = collection_adds.Offer(library, [collection_adds.Row(game_id)],
                                  collection_adds.GAMES, name,
                                  then=partial(_game_redrawn, context),
                                  narrowed=str(state.get("collection") or ""))

    async def refresh(ids: list[str]) -> None:
        again = state.get("refresh_games")
        if callable(again):
            await again(ids)
        else:
            await context["rebuild"]()

    async def rate(ids: list[str], value: int) -> bool:
        grid_rate = state.get("rate_games")
        if not callable(grid_rate):
            return False
        landed = await grid_rate(ids, value)
        if landed:
            await context["rebuild"]()
        return bool(landed)

    async def match(_games: list[dict[str, Any]]) -> None:
        await pick_a_match(context)

    return game_acts.game_acts(
        library, [context["game"]], state, refresh,
        rate=rate if callable(state.get("rate_games")) else None, match=match,
        collections=game_acts.collections_of(offer, context["collections"]))


def _table_verbs(context: dict[str, Any]) -> list[panel.Verb]:
    library, game_id, table_id = context["library"], context["game_id"], context["lens"]
    table = next((one for one in context["tables"] if str(one.get("id")) == table_id), None)
    if table is None:
        return []

    async def after(game_ids: list[str], gone: bool) -> None:
        if gone:
            await _table_gone(context, table)
            return
        await context["rebuild"]()
        refresh = context["state"].get("refresh_game")
        if callable(refresh):
            for one in game_ids:
                await refresh(one)

    offer = collection_adds.Offer(
        library, [collection_adds.Row(game_id, table_id)], collection_adds.TABLES,
        game_tables.name_among(table, context["tables"]),
        then=partial(_game_redrawn, context))
    target = game_acts.Target(game_id, str(context["game"].get("name") or ""), table,
                              context["tables"])
    return game_acts.table_acts(library, [target], after, context["state"],
                                game_acts.collections_of(offer, context["collections"]))


def _file_after(context: dict[str, Any]) -> Callable[[list[str]], Awaitable[None]]:
    async def after(_game_ids: list[str]) -> None:
        await context["rebuild"]()
    return after


def _media_file_verbs(context: dict[str, Any]) -> list[panel.Verb]:
    one = file_acts.File(context["row"], context["game"])
    return file_acts.media_acts(context["library"], [one], context["state"],
                                _file_after(context))


def _asset_file_verbs(context: dict[str, Any]) -> list[panel.Verb]:
    one = file_acts.File(context["row"], context["game"])
    return file_acts.asset_acts(context["library"], [one], context["state"],
                                _file_after(context))


# What the panel header's menu offers, per subject: the same lists the grids' row menus
# draw. A subject with none has no menu.
def _selection_verbs(context: dict[str, Any]) -> list[panel.Verb]:
    """The grid's own bulk menu: the grid that holds the ticks names it."""
    verbs_of = context["state"].get("selection_verbs")
    return verbs_of() if callable(verbs_of) else []


SUBJECT_VERBS: dict[str, Callable[[dict[str, Any]], list[panel.Verb]]] = {
    "selection": _selection_verbs,
    "game_selection": _selection_verbs,
    "game": _game_verbs,
    "table": _table_verbs,
    "tag": _tag_verbs,
    "collection": _collection_verbs,
    "launcher": _launcher_verbs,
    "theme": _theme_verbs,
    "device": _device_verbs,
    "player": _player_verbs,
    "backup": _backup_verbs,
    ext_row.SUBJECT: ext_row.verbs,
    "discovery": _discovery_verbs,
    "media_file": _media_file_verbs,
    "asset_file": _asset_file_verbs,
}
