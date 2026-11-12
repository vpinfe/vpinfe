"""The assets section."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from functools import partial
from pathlib import PurePosixPath
from typing import Any

from nicegui import ui

from common import i18n
from common.games import asset_resolver, collation
from common.games.asset_registry import ALWAYS_KEPT as _ALWAYS_KEPT
from common.i18n import t
from manager.kit import game_tables, offload, panel, scroll, verbs
from manager.library import file_states, uploads
from manager.library.assets import _reason
from manager.workbench.core import _asset_name
from manager.workbench.file import (
    _asset_detail_of,
    _asset_file_block,
    _asset_icon,
    _same_file,
    _served,
)
from manager.workbench.game_media import _kept_kinds, _pick_slot
from manager.workbench.game_table import _add_action, _extract_script, _rom_state, _state

_CHOSEN = "manager-source-row--chosen"


def _assets_label(context: dict[str, Any]) -> str:
    """Counts what is here, not what could be: a denominator over sixteen registry
    kinds would call every ordinary game mostly empty."""
    game = context["game"]
    chosen = next((item for item in context["tables"]
                   if item.get("id") == context["lens"]), None)
    kept = _kept_kinds(context, "asset")
    resolved = _only_kept((chosen or {}).get("assets") or {}, kept)
    have = sum(1 for state in resolved.values()
               if state.get("resolution") != "none")
    # Skipping what the table already answered, exactly as the body does. The folder
    # reports a backglass this table resolves too, so counting both said four where
    # two rows were drawn - a header disagreeing with the list under it.
    have += sum(1 for kind, state in _only_kept(game.get("assets") or {}, kept).items()
                if kind not in resolved and state.get("present"))
    have += len(context.get("rom_files") or [])
    return t("manager.workbench.assets_2", have=(have)) if have else t("manager.workbench.assets")


async def _assets_block(context: dict[str, Any]) -> None:
    """Every type of asset this library collects, a section each, and the files the game
    holds of it under its name."""
    holder = ui.column().classes("gap-3 w-full")

    turn = [0]
    one_at_a_time = asyncio.Lock()
    rows: dict[str, ui.element] = {}
    drawn: list[tuple[Any, Any, Any]] = []

    async def draw(*, whole: bool = True) -> None:
        # Each pick redraws on a task of its own, and a draw awaits between clearing the
        # dock and filling it. Only the newest pick draws, so two cards never stack.
        # A pick leaves the list standing and moves the mark while the files are the ones
        # the rows were drawn from; any other draw rebuilds them.
        turn[0] += 1
        mine = turn[0]
        async with one_at_a_time:
            if mine != turn[0]:
                return
            library, game_id = context["library"], context["game_id"]
            files = await offload.io(library.files_of, "asset", game_id)
            found = _picked(files, str(context["slot"]["kind"] or ""), context["lens"])
            shown = [(one["id"], one.get("file"), one.get("present")) for one in files]
            if whole or found is None or shown != drawn:
                drawn[:] = shown
                rows.clear()
                holder.clear()
                with holder:
                    for _kind, section in _asset_sections(
                            context, files, found,
                            lambda key: partial(_pick_slot, context, key, pick), rows):
                        section()
            else:
                for file_id, row in rows.items():
                    if found is not None and file_id == found["id"]:
                        row.classes(add=_CHOSEN)
                    else:
                        row.classes(remove=_CHOSEN)
            await _asset_dock(context, found, files)

    async def pick() -> None:
        await scroll.held(lambda: draw(whole=False))

    await draw()


async def _asset_dock(context: dict[str, Any], found: dict[str, Any] | None,
                      files: list[dict[str, Any]]) -> None:
    """The picked asset's card, the file panel's own, without the file's settings."""
    dock = context.get("dock")
    if dock is None:
        return
    library, game_id = context["library"], context["game_id"]
    dock.clear()
    with dock:
        if found is None:
            with ui.column().classes("manager-dock-empty items-center gap-1"):
                ui.label(t("manager.workbench.file_panel_empty")).classes("manager-empty-title")
                ui.label(t("manager.workbench.file_hint")).classes("manager-help")
            return
        detail = await _asset_detail_of(library, game_id, found)
        await _asset_file_block({**context, "row": found, "kind": found["kind"], "detail": detail,
                                 "lens": str(found.get("table") or context["lens"]),
                                 "served": _served(found, files, context["tables"]),
                                 "link_view": "assets"})


def _asset_sections(context: dict[str, Any], files: list[dict[str, Any]],
                    found: dict[str, Any] | None = None,
                    pick: Callable[[str], Callable[[], Any]] | None = None,
                    rows: dict[str, ui.element] | None = None
                    ) -> list[tuple[str, Callable[[], None]]]:
    """One section for every type the library collects, in the order of the word each
    shows, whether or not the game holds a file of it. A type the library does not
    collect is left out entirely: a section saying an EM table has no ROM is not a fact
    about that table, it is the list answering a question this library never asked."""
    game = context["game"]
    kept = _kept_kinds(context, "asset")
    kinds = {*asset_resolver.PER_TABLE_KINDS, *(game.get("assets") or {}), "rom"}
    chosen = next((item for item in context["tables"]
                   if item.get("id") == context["lens"]), None)
    return [(kind, _section(context, kind, files, chosen, found, pick, rows))
            for kind in sorted((one for one in kinds if _collects(one, kept)),
                               key=lambda one: collation.sort_key(_asset_name(one)))]


def _section(context: dict[str, Any], kind: str, files: list[dict[str, Any]],
             chosen: dict[str, Any] | None, found: dict[str, Any] | None,
             pick: Callable[[str], Callable[[], Any]] | None,
             rows: dict[str, ui.element] | None = None) -> Callable[[], None]:
    here = [one for one in files if one.get("kind") == kind and one.get("present")]
    table = chosen if chosen is not None else {}
    scripted = kind == "script" and chosen is not None \
        and ((table.get("assets") or {}).get("script") or {}).get("resolution") == "none"
    table_id = str(table.get("id") or "") if kind in asset_resolver.PER_TABLE_KINDS else ""

    def draw() -> None:
        with ui.column().classes("w-full gap-1") as section:
            uploads.declare(section, game=context["game_id"], table=table_id, asset=kind)
            with ui.row().classes("items-center gap-2 w-full no-wrap manager-heading-row"):
                ui.label(_asset_name(kind)) \
                    .classes("manager-card-title manager-fact-heading grow")
                if scripted:
                    _extract_action(context, table)
                elif kind != "script":
                    _add_action(context, kind)
            for one in here:
                key = f"{kind}:{str(one.get('file') or '').lower()}"
                element = _file_row(kind, one, found is not None and one["id"] == found["id"],
                                    pick(key) if pick else None)
                if rows is not None:
                    rows[one["id"]] = element
            told = kind == "rom" and _table_rom(chosen, here)
            if not here and not told:
                _nothing_here(context, kind, scripted)

    return draw


def _extract_action(context: dict[str, Any], table: dict[str, Any]) -> None:
    """Take the script out of the .vpx, so it can be edited."""
    ui.button(t("manager.workbench.extract"), icon=verbs.EXTRACT,
              on_click=lambda: _extract_script(context, table)) \
        .props("flat dense no-caps size=sm").classes("manager-action manager-action--inline")


def _table_rom(chosen: dict[str, Any] | None, here: list[dict[str, Any]]) -> bool:
    """The ROM the viewed table names and whether it is installed, drawn when no file
    held under that name says so already."""
    pinmame = (chosen or {}).get("dependencies", {}).get("pinmame") or {}
    rom = str(pinmame.get("effective") or pinmame.get("declared") or "")
    if not rom or any(PurePosixPath(str(one.get("file") or "")).stem.lower() == rom.lower()
                      for one in here):
        return False
    with ui.row().classes("items-center gap-2 w-full no-wrap"):
        said = _rom_state(pinmame, rom)
        if callable(said):
            said()
        else:
            ui.label(rom).classes("manager-slot-file truncate").tooltip(rom)
    return True


def _nothing_here(context: dict[str, Any], kind: str, scripted: bool) -> None:
    if scripted:
        panel.line(game_tables.word_for(game_tables.SCRIPT_WORDS, False),
                   hint=t("manager.workbench.script_embedded"))
        return
    if kind == "readme" and ((context["game"].get("assets") or {}).get(kind) or {}).get("present"):
        _state(t("word.present"), "on")()
        return


def _file_row(kind: str, row: dict[str, Any], chosen: bool,
              pick: Callable[[], Any] | None) -> ui.element:
    """A file: its name, the one chip that is an exception, its size, and the way into
    its card. Who owns it and which VPS record it is are on the card."""
    name, size = str(row.get("file") or ""), row.get("size_bytes")
    classes = ("items-center gap-3 w-full no-wrap manager-source-row "
               "manager-source-row--pick manager-source-row--entry")
    if chosen:
        classes += " " + _CHOSEN
    element = ui.row().classes(classes).props("tabindex=0 role=button")
    if pick is not None:
        element.on("click", lambda: pick())
        element.on("keydown.enter", lambda: pick())
    with element:
        ui.icon(_asset_icon(kind)).classes("opacity-70 shrink-0")
        with ui.column().classes("gap-0 min-w-0 grow"):
            with ui.row().classes("items-center gap-2 no-wrap min-w-0 w-full"):
                ui.label(name).classes("manager-source-name").tooltip(name)
                _chip_of(kind, row)
            if size:
                ui.label(i18n.size(size)).classes("manager-help w-full")
        ui.icon(verbs.DRILL).classes("manager-summary-go")
    return element


def _chip_of(kind: str, row: dict[str, Any]) -> None:
    """External for a script beside the table; Unused and Orphaned for a file nothing
    reads. In Use draws nothing: it is the ordinary case."""
    reason = _reason(row)
    if reason not in (file_states.UNUSED, file_states.ORPHAN):
        if kind == "script":
            _state(game_tables.word_for(game_tables.SCRIPT_WORDS, True), "off",
                   hint=t("manager.workbench.script_sidecar"))()
        return
    found = file_states.table("manager.assets.unused_meaning").state(reason)
    if found is not None:
        _state(found.word, "off" if reason == file_states.UNUSED else "warn",
               hint=found.line())()


def _picked(files: list[dict[str, Any]], key: str, lens: str) -> dict[str, Any] | None:
    """The file a pick names: `kind:file`, with the file in lowercase as an address
    carries it, or a bare kind, which is the kind's file on the table shown."""
    kind, colon, name = key.partition(":")
    if not kind:
        return None
    if not colon:
        return _same_file(files, {"kind": kind, "table": lens})
    return next((one for one in files if one.get("kind") == kind and one.get("present")
                 and str(one.get("file") or "").lower() == name), None)


def _only_kept(states: dict[str, Any], kept: set[str] | None) -> dict[str, Any]:
    """Drop what the library does not collect. `table` is never in the toggles and so
    is never dropped - the .vpx is the library rather than an accessory to it."""
    return {kind: state for kind, state in states.items() if _collects(kind, kept)}


def _collects(kind: str, kept: set[str] | None) -> bool:
    return kept is None or kind in kept or kind in _ALWAYS_KEPT
