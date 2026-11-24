"""The pane beside an extension's list, for the row it has open.

The extension answers `detail(row_id)` with a title, a line under it and sections of facts,
fields and rows; core draws them as it draws every other panel, so the pane looks like the
Manager and keeps working wherever the extension runs. A field is written when it is set,
and the answer to that write is the pane drawn again.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import partial
from typing import Any
from urllib.parse import quote

from nicegui import run, ui

from common.failures import why
from common.i18n import t
from manager.extensions import ext_cells, ext_links, ext_lists, ext_page
from manager.kit import offload, panel, when
from manager.kit.api import ApiClient
from manager.kit.on_page import on_page

# Not a section of any other rail: a section key an extension chose cannot be one of core's.
SECTION_PREFIX = "ext:"
SUBJECT = "extension_row"


def route(extension: dict[str, Any], declared: dict[str, Any], row_id: str) -> str:
    return f"/ext/{extension.get('name')}{declared['detail']}/{quote(row_id, safe='')}"


def row_of(extension: dict[str, Any], declared: dict[str, Any], row_id: str) -> dict[str, Any]:
    """The list's own row for `row_id`, as the last read left it, else just its id."""
    held = ext_lists.kept(str(extension.get("name") or ""), str(declared.get("key") or ""))
    return next((one for one in held["rows"] or [] if str(one.get("id")) == row_id),
                {"id": row_id})


def verbs(context: dict[str, Any]) -> list[panel.Verb]:
    """The list's verbs for the one row, in the header's menu."""
    async def press(verb: dict[str, Any], chosen: list[dict[str, Any]]) -> None:
        await ext_lists.run_verb(context["extension"], context["declared"], verb, chosen,
                                 context["verbed"])

    return ext_lists.verb_entries(context["declared"], [context["row"]], press)


def sections(context: dict[str, Any], found: dict[str, Any]) -> tuple[Any, ...]:
    """The pane's sections as the rail declares them. Imported late: the rail is drawn by
    the workbench, which draws this."""
    from manager.workbench.core import Section

    out = []
    for one in found.get("sections") or []:
        out.append(Section(
            SECTION_PREFIX + str(one["key"]), partial(_label, str(one.get("label") or "")),
            partial(_draw_section, one), subjects=frozenset({SUBJECT})))
    return tuple(out)


def _label(label: str, _context: dict[str, Any]) -> str:
    """The section's own name; Details where the extension gave none."""
    return label or t("manager.workbench.details")


async def _draw_section(one: dict[str, Any], context: dict[str, Any]) -> None:
    entries = section_entries(one, await ext_cells.prepared(context, [one]))
    panel.facts(ui, panel.headed(entries, _label(str(one.get("label") or ""), context)))


def section_entries(one: dict[str, Any], context: dict[str, Any]) -> list[tuple[Any, ...]]:
    """A section's facts, fields and rows as the panel lists them. `context` names the
    route a field is written to, the page's `state`, and `redraw(answer)`, which draws the
    answer to a write (None where it is to be read again). A game or table fact is drawn
    from what `ext_cells.prepared` added to `context`."""
    client = ApiClient()
    entries: list[tuple[Any, ...]] = []
    for fact in one.get("facts") or []:
        entries.append((fact["label"], _fact_value(fact, context)))
    for field in one.get("fields") or []:
        entries.append((field["label"],
                        ext_page.control(client, field, _saver(context, str(field["key"])))))
        if field.get("help"):
            entries.append(panel.note(str(field["help"])))
    for each in one.get("rows") or []:
        entries.append((each["label"], each["under"]))
    return entries


def _fact_value(fact: dict[str, Any], context: dict[str, Any]) -> Any:
    kind, value, help_ = fact["kind"], fact["value"], str(fact.get("help") or "")
    if kind == "state":
        return panel.state(str(value), str(fact.get("tier") or "unknown"), hint=help_)
    if kind == "chips":
        return partial(_chips, list(value))
    if kind == "link":
        to = ext_links.filled(str(fact["to"]), str(context["state"].get("host") or "localhost"))
        return (panel.link_out(str(value), to=to, hint=help_, rel=ext_links.REL)
                if to else str(value))
    if kind == "picture":
        return partial(_picture, str(value))
    if kind in ext_cells.KINDS:
        return ext_cells.draw(fact, context)
    if kind == "date":
        return when.local(value) or str(value)
    return str(value)


def _chips(words: list[str]) -> None:
    with ui.element("div").classes("manager-chips"):
        for word in words:
            ui.label(word).classes("manager-member-chip")


def _picture(source: str) -> None:
    if source:
        ui.image(source).classes("manager-pane-picture")


def _saver(context: dict[str, Any], key: str) -> Callable[..., Awaitable[str]]:
    """Write one field, then draw the pane the extension answered with. A text field gets
    the refusal under itself and keeps what was typed; any other says so in a notice."""
    @on_page
    async def save(new_value: Any, *, under: bool = False) -> str:
        try:
            answer = await run.io_bound(
                ApiClient().ext_put, context["route"], {"values": {key: new_value}})
        except Exception as exc:  # noqa: BLE001 - the reason belongs on screen
            if under:
                return why(exc)
            ui.notify(t("said.save_failed"), caption=why(exc), type="negative")
            await context["redraw"](None)
            return ""
        if context.get("again") is not None:
            await context["again"]()
        await context["redraw"](answer)
        return ""

    return save


async def read(extension: dict[str, Any], declared: dict[str, Any], row_id: str) -> dict[str, Any]:
    """The pane for a row, or `ApiError` in the extension's words where there is none."""
    return await offload.io(ApiClient().ext_get, route(extension, declared, row_id))


def heading(declared: dict[str, Any]) -> str:
    return str(declared.get("title") or "")


def prompt() -> str:
    return t("manager.community.pane_select")
