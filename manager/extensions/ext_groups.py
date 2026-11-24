"""The groups extensions put on a game's or a table's panel: a section of their own after
core's, or the end of one of core's.

The extension answers with one section of facts, fields and rows and core draws it as it
draws the rest of the panel. The panel never waits for it: each group is a region drawn
once its answer arrives. A group with nothing to say leaves no trace in the panel; one
that does not answer says so in one line, so a missing group is never a silent one.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from functools import partial
from typing import Any
from urllib.parse import quote

from nicegui import ui

from common.failures import why
from common.i18n import t
from manager.extensions import ext_cells, ext_links, ext_lists, ext_mark, ext_row
from manager.kit import busy, offload, panel, verbs
from manager.kit.api import ApiClient
from manager.kit.on_page import on_page

logger = logging.getLogger("vpinfe.manager.extensions.ext_groups")


def route(extension: dict[str, Any], group: dict[str, Any], game_id: str,
          table_id: str = "") -> str:
    base = f"/ext/{extension.get('name')}{group['base']}/{quote(game_id, safe='')}"
    return f"{base}/{quote(table_id, safe='')}" if group["subject"] == "table" else base


def declared(context: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """(extension, group) for each group that belongs on this panel. A game's group is on
    a game's panel and on a table's; a table's group is only on a table's. A Private game
    has only the groups declared to work on one: the others are not told the library holds
    it."""
    private = bool(context["game"].get("private"))
    subjects = ("game", "table") if context["lens"] else ("game",)
    return [(extension, group)
            for extension in context["state"].get("extensions") or []
            for group in extension.get("groups") or []
            if group["subject"] in subjects and (group.get("private_ok") or not private)]


def own(context: dict[str, Any]) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    """(rail key, extension, group) for each group that is a section of its own."""
    return [(f"ext_group.{extension.get('name')}.{group['subject']}", extension, group)
            for extension, group in declared(context) if not group.get("section")]


def title(extension: dict[str, Any], group: dict[str, Any]) -> str:
    return str(group.get("title") or extension.get("display_name") or extension.get("name")
               or "")


def fill(context: dict[str, Any], section: str, subject: str = "") -> None:
    """A region for each group that ends `section` (of `subject`'s, where one is named),
    in the container now open, each drawn when it is read."""
    for extension, group in declared(context):
        if group.get("section") == section and (not subject or group["subject"] == subject):
            show_in_place(context, extension, group)


def show_in_place(context: dict[str, Any], extension: dict[str, Any],
                  group: dict[str, Any]) -> None:
    """One group's region in the container now open, drawn when it is read."""
    region = ui.column().classes("gap-0")
    busy.fill(region, partial(_show, region, extension, group, context, None))


async def _show(region: ui.element, extension: dict[str, Any], group: dict[str, Any],
                context: dict[str, Any], answer: dict[str, Any] | None) -> None:
    """The group, from `answer` where the write that came just before it gave one, else
    read again."""
    where = route(extension, group, str(context["game_id"]), str(context["lens"] or ""))
    if answer is None:
        try:
            answer = dict(await offload.io(ApiClient().ext_get, where))
        except Exception as exc:  # noqa: BLE001 - a group that does not answer draws a line saying so
            logger.warning("%s's %s group did not answer: %s", extension.get("name"),
                           group["subject"], why(exc))
            if not region.is_deleted:
                region.clear()
                with region:
                    panel.facts(ui, panel.headed([panel.intro(
                        t("manager.community.read_failed",
                          name=str(extension.get("display_name") or extension.get("name") or "")),
                        hint=why(exc))], title(extension, group)))
            return
    if region.is_deleted:
        return
    section = answer.get("section")
    if not section:
        region.clear()
        return

    async def redraw(new: dict[str, Any] | None) -> None:
        await _show(region, extension, group, context, new)

    held = await ext_cells.prepared(
        {"state": context["state"], "route": where, "redraw": redraw, "again": None,
         "library": context.get("library")}, [section])
    if region.is_deleted:
        return
    region.clear()
    with region:
        panel.facts(ui, [*_heading(extension, str(answer.get("title") or title(extension, group))),
                         *ext_row.section_entries(section, held),
                         *_strip(answer.get("acts") or [], where, redraw,
                                 str(extension.get("display_name") or extension.get("name")
                                     or ""))])


def _heading(extension: dict[str, Any], title: str) -> list[tuple[Any, ...]]:
    mark = ext_mark.drawer(extension)
    return [(panel.HEADING, title) if mark is None else (panel.OWNED, (title, mark))]


def _strip(acts: list[dict[str, Any]], where: str,
           redraw: Callable[[dict[str, Any] | None], Awaitable[None]], name: str
           ) -> list[tuple[Any, ...]]:
    """The acts closing the group, a verb each, or none where the extension offers none."""
    if not acts:
        return []

    @on_page
    async def pressed(act: dict[str, Any]) -> None:
        try:
            said = await offload.io(ApiClient().ext_post,
                                    f"{where}/acts/{quote(str(act['key']), safe='')}", {})
        except Exception as exc:  # noqa: BLE001 - the reason belongs on screen
            ui.notify(t("said.action_failed"), caption=why(exc), type="negative")
            return
        await ext_lists.told(said, lambda: redraw(None))

    def draw() -> None:
        with ui.row().classes("items-center gap-1 no-wrap"):
            for act in acts:
                label = str(act.get("label") or act["key"])
                if "url" in act:
                    if ext_lists.web(act["url"]):
                        panel.link_out(label, to=ext_lists.web(act["url"]),
                                       rel=ext_links.REL)()
                else:
                    panel.action(label, partial(pressed, act), icon=verbs.GO,
                                 hint=t("manager.community.act_hint", name=name))()

    return [(panel.ASIDE, draw)]
