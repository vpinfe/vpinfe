"""The sections an extension declared for its own panel, after Details and Settings.

The extension answers what each section holds (facts, fields, rows) when it is opened, and
the actions its author placed there; core draws both as it draws every other panel. A field
is written when it is set, and the answer is the panel drawn again.
"""

from __future__ import annotations

from functools import partial
from typing import Any

from nicegui import ui

from common.extensions import host
from common.failures import why
from common.i18n import t
from manager.extensions import ext_cells, ext_page, ext_row
from manager.kit import deeplink, offload, panel
from manager.kit.api import ApiClient

# Not a section of any other rail, and not a row's `ext:` either.
SECTION_PREFIX = deeplink.SECTION_PREFIX
SUBJECT = "extension"


def sections(context: dict[str, Any]) -> tuple[Any, ...]:
    """The rail's entries for the running extension's sections, in the order declared.
    Imported late: the rail is drawn by the workbench, which draws this."""
    from manager.workbench.core import Section

    record = context["extension"]["record"] or {}
    if record.get("state") != host.LOADED:
        return ()
    return tuple(
        Section(SECTION_PREFIX + str(one["key"]), partial(_label, str(one["title"])),
                partial(_draw, one), subjects=frozenset({SUBJECT}),
                icon=str(one.get("icon") or ""), titled=True)
        for one in record.get("sections") or [])


def keys(extensions: list[dict[str, Any]]) -> list[str]:
    """Every rail key an address can name for a running extension's own sections."""
    return [SECTION_PREFIX + str(one["key"]) for extension in extensions
            if extension.get("state") == host.LOADED
            for one in extension.get("sections") or []]


def _label(title: str, _context: dict[str, Any]) -> str:
    return title


async def _draw(one: dict[str, Any], context: dict[str, Any]) -> None:
    record = context["extension"]["record"] or {}
    name = str(record.get("name") or "")
    route = f"/ext/{name}{one['base']}"
    offered = ext_page.placed(record, str(one["key"]))
    try:
        answer = await offload.io(ApiClient().ext_get, route)
    except Exception as exc:  # noqa: BLE001 - the section says why, never 500s
        with ui.column().classes("gap-0 manager-form w-full"):
            if offered:
                ext_page.action_lines(offered, name, context["refill"])
            panel.facts(ui, [panel.note(t("manager.community.read_failed", name=one["title"]),
                                        hint=why(exc))])
        return
    shown = answer.get("section") or {}
    redraw = context["rebuild"]

    async def written(_answer: Any) -> None:
        await redraw()

    held = {**await ext_cells.prepared(context, [shown]), "route": route,
            "redraw": written, "again": None}
    with ui.column().classes("gap-0 manager-form w-full"):
        if offered:
            ext_page.action_lines(offered, name, context["refill"])
        entries = ext_row.section_entries(shown, held)
        if entries:
            panel.facts(ui, entries)
        elif not offered:
            panel.facts(ui, [panel.note(t("manager.ext_sections.empty"))])
