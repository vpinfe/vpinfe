"""A game's Pictures, at the end of its Play section: every screen as a player took them
during play."""

from __future__ import annotations

import html
import logging
from functools import partial
from typing import Any

from nicegui import ui

from common.failures import why
from common.i18n import t
from manager.kit import art, game_tables, offload, panel, undo, verbs, when
from manager.kit.on_page import on_page
from manager.library import mediaview

logger = logging.getLogger("vpinfe.manager.library.pictures")


async def read(context: dict[str, Any]) -> dict[str, Any]:
    """`pictures` for the panel's subject, or the `reason` they could not be read."""
    try:
        return {"pictures": await offload.io(context["library"].pictures,
                                             context["game_id"], context["lens"])}
    except Exception as exc:  # noqa: BLE001 - the reason belongs on the page
        logger.warning("manager: could not list the pictures of %s: %s",
                       context["game_id"], exc)
        return {"reason": why(exc)}


def rows(context: dict[str, Any], found: dict[str, Any]) -> list[tuple[Any, Any]]:
    """The Pictures group, or nothing where there are none."""
    heading = (panel.HEADING, t("manager.workbench.pictures"))
    if "reason" in found:
        return [heading, (panel.LEDE, partial(panel.line,
                                              t("manager.workbench.pictures_unreadable"),
                                              hint=str(found["reason"])))]
    shots = list(found.get("pictures") or [])
    if not shots:
        return []
    return [heading, (panel.FULL, partial(_grid, context, shots, table_names(context, shots)))]


def table_names(context: dict[str, Any], shots: list[dict[str, Any]]) -> dict[str, str]:
    """Each table's name by its id, where the pictures come from more than one."""
    ids = {str(shot.get("table_id") or "") for shot in shots}
    if len(ids) < 2:
        return {}
    tables = context["tables"]
    return {str(table.get("id")): game_tables.name_among(table, tables)
            for table in tables if str(table.get("id") or "") in ids}


def _grid(context: dict[str, Any], shots: list[dict[str, Any]],
          names: dict[str, str]) -> None:
    with ui.element("div").classes("manager-mediatile-grid"):
        for shot in shots:
            _tile(context, shot, names.get(str(shot.get("table_id") or ""), ""))


def _tile(context: dict[str, Any], shot: dict[str, Any], table: str) -> None:
    taken = str(shot.get("taken") or "")
    width, height = int(shot.get("width") or 0), int(shot.get("height") or 0)
    src = art.picture(context["game_id"], str(shot["name"]),
                      version=str(shot.get("version") or ""), size=art.CELL)
    tile = ui.element("div").classes(
        "manager-mediatile manager-mediatile--present cursor-pointer")
    tile.on("click", lambda: _look(context, shot))
    with tile:
        art_box = ui.element("div").classes("manager-mediatile-art")
        if width and height:
            art_box.style(f"aspect-ratio:{width}/{height}")
        with art_box:
            ui.html(f'<img src="{html.escape(src)}" loading="lazy"'
                    f' alt="{html.escape(when.local(taken))}">') \
                .classes("manager-mediatile-media")
        ui.label(when.ago(taken)).classes("manager-mediatile-cap")
        if table:
            ui.label(table).classes("manager-mediatile-cap")
    tile.tooltip(game_tables.JOIN.join(part for part in (when.local(taken), table) if part))


def _look(context: dict[str, Any], shot: dict[str, Any]) -> None:
    mediaview.open_image(
        art.picture(context["game_id"], str(shot["name"]),
                    version=str(shot.get("version") or "")),
        when.local(str(shot.get("taken") or "")),
        [(verbs.DELETE, t("word.delete"), lambda: _delete(context, shot))])


@on_page
async def _delete(context: dict[str, Any], shot: dict[str, Any]) -> None:
    library, game_id, name = context["library"], context["game_id"], str(shot["name"])
    try:
        held = await offload.io(library.remove_picture, game_id, name)
    except Exception as exc:  # noqa: BLE001 - the reason belongs on screen
        ui.notify(t("said.delete_failed"), caption=why(exc), type="negative")
        return
    await context["rebuild"]()
    state = context["state"]
    view = state.get("view")

    async def put_back() -> None:
        await offload.io(library.put_picture, game_id, name, held)
        redraw = state.get("panel_rebuild")
        if state.get("game") == game_id and state.get("view") == view and callable(redraw):
            await redraw()

    undo.offer(t("manager.workbench.picture_deleted"), put_back)
