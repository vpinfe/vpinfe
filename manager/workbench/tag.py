"""The tag subject."""

from __future__ import annotations

from functools import partial
from typing import Any

from nicegui import run, ui

from common import icons
from common.failures import why
from common.games import tag_registry
from common.i18n import t
from manager.extensions import ext_lists
from manager.kit import confirm, deeplink, game_tables, offload, panel, tag_chips, thumbnails, verbs
from manager.kit.data import read_state, tag_source
from manager.kit.on_page import on_page
from manager.workbench.core import _rows, _said_line


async def _tags_refreshed(context: dict[str, Any]) -> None:
    """The tags grid, put right after a write that left the panel as it is."""
    refresh = context["state"].get("refresh_tags")
    if callable(refresh):
        await refresh()


async def _tag_changed(context: dict[str, Any], name: str | None) -> None:
    """The grid and the panel, after a write - the panel following the tag to `name`."""
    context["state"]["tag"] = name
    refresh = context["state"].get("refresh_tags")
    if callable(refresh):
        await refresh()
    await context["rebuild"]()


async def _tag_details(context: dict[str, Any]) -> None:
    library, name, look = context["library"], context["name"], context["tag"]

    typed: list[Any] = []

    @on_page
    async def rename(value: str) -> None:
        said = " ".join(str(value or "").split())
        if not said or said == name:
            return
        if said in library.tag_looks() and not await confirm.ask(
                t("manager.tageditor.merge", into=said),
                detail=t("manager.tageditor.merge_detail"),
                confirm=t("word.merge"), icon=verbs.MERGE):
            for one in typed:
                one.value = name
            return
        try:
            await run.io_bound(library.merge_tags, [name], said)
        except Exception as exc:  # noqa: BLE001
            ui.notify(t("said.action_failed"), caption=why(exc), type="negative")
            return
        await _tag_changed(context, said)

    @on_page
    async def describe(value: str) -> None:
        try:
            await run.io_bound(library.put_tag, name, {"description": value})
        except Exception as exc:  # noqa: BLE001
            ui.notify(t("said.action_failed"), caption=why(exc), type="negative")
            return
        look["description"] = value
        await _tags_refreshed(context)

    sources = list(look.get("sources") or [])
    _rows(ui, [
        (t("manager.tageditor.tag"),
         name if sources else panel.field(name, rename, on_control=typed.append)),
        *[(t("manager.tags.from"), partial(_tag_source, one)) for one in sources],
        (t("manager.workbench.description"), panel.field(str(look.get("description") or ""),
                                            describe, lines=2)),
        (t("manager.tags.color"), partial(_swatches, context)),
    ])


def _tag_source(said: dict[str, Any]) -> None:
    with ui.column().classes("gap-0 min-w-0"):
        panel.link(tag_source(said), to=ext_lists.address(str(said.get("extension") or ""),
                                                          str(said.get("list") or "")))()
        panel.line(read_state(said), hint=str(said.get("error") or ""))


def _swatches(context: dict[str, Any]) -> None:
    library, name, look = context["library"], context["name"], context["tag"]
    chosen = str(look.get("color") or "") if look.get("chosen") else ""

    @on_page
    async def pick(color: str) -> None:
        try:
            await run.io_bound(library.put_tag, name, {"color": color})
        except Exception as exc:  # noqa: BLE001
            ui.notify(t("said.action_failed"), caption=why(exc), type="negative")
            return
        look["chosen"] = bool(color)
        if color:
            look["color"] = color
        ring(color)
        await _tags_refreshed(context)

    ring = tag_chips.swatches(chosen, tag_registry.derived_color(name), pick)


def _carried(row: dict[str, Any]) -> list[str]:
    return [*((row.get("user") or {}).get("tags") or []), *(row.get("derived_tags") or [])]


async def _tag_games(context: dict[str, Any]) -> None:
    name = context["name"]
    library = context["library"]
    carrying = [one for one in library.games if name in _carried(one)]
    wants_tables = context["tag"].get("tables")
    listing = (await offload.io(library.load_tables)
               if wants_tables or library.thumbnail() else [])
    tables = [one for one in listing if name in _carried(one)] if wants_tables else []
    shown = library.game_art()
    look = library.thumbnail_look()
    with ui.column().classes("gap-0 manager-form w-full min-w-0"):
        if not carrying and not tables:
            ui.label(t("manager.tags.on_no_games")).classes("manager-help px-3")
        for game in sorted(carrying, key=lambda one: str(one.get("name") or "").lower()):
            to = "/manager?" + deeplink.query({"view": "games", "game": game["id"]})
            with ui.row().classes("items-center gap-2 w-full no-wrap manager-member-row"), \
                    thumbnails.beside(None if shown is None else shown.get(game["id"], ""),
                                      icons.GAMES, to=to, look=look), \
                    ui.column().classes("gap-0 grow min-w-0"):
                panel.link(str(game.get("name") or ""), to=to)()
                _said_line(game_tables.made(game))
        if tables:
            ui.label(t("manager.tageditor.tables")).classes("manager-group px-3 mt-2")
        for table in tables:
            to = "/manager?" + deeplink.query({"view": "tables",
                                               "game": str(table.get("game_id") or ""),
                                               "table": str(table.get("id") or "")})
            with ui.row().classes("items-center gap-2 w-full no-wrap manager-member-row"), \
                    thumbnails.beside(None if shown is None else thumbnails.address(table),
                                      icons.TABLES, to=to, look=look), \
                    ui.column().classes("gap-0 grow min-w-0"):
                panel.link(str(table.get("game") or table.get("name") or ""), to=to)()
                _said_line(game_tables.made(table), table)
