"""The assets section."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from functools import partial
from typing import Any

from nicegui import ui

from common import i18n
from common.games.asset_registry import ALWAYS_KEPT as _ALWAYS_KEPT
from common.i18n import t
from manager.kit import game_tables, media_ownership, offload, panel, states, verbs
from manager.library import file_acts, file_states
from manager.workbench.core import HEADING, _asset_name, _rows
from manager.workbench.file import _asset_detail_of, _asset_file_block, _same_file, _served
from manager.workbench.game_media import _kept_kinds, _pick_slot
from manager.workbench.game_table import (
    _add_action,
    _drop_script,
    _extract_script,
    _rom_row,
    _state,
    _with_add,
)


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
    """Every asset this game holds, and whose each one is.

    Two levels in one list, because somebody asking what a table has does not care
    which of them the filesystem answers at. The five VPX resolves by naming rule are
    the table's and carry a tier; the rest belong to the folder and carry presence.
    The tiers are `media_ownership`'s - the question is the same one, which file wins.
    """
    holder = ui.column().classes("gap-0 manager-form")

    turn = [0]
    one_at_a_time = asyncio.Lock()

    async def draw() -> None:
        # Each pick redraws on a task of its own, and a draw awaits between clearing the
        # dock and filling it. Only the newest pick draws, so two cards never stack.
        turn[0] += 1
        mine = turn[0]
        async with one_at_a_time:
            if mine != turn[0]:
                return
            picked = context["slot"]["kind"]
            holder.clear()
            with holder:
                _rows(ui, _asset_entries(
                    context, lambda kind: panel.Pick(partial(_pick_slot, context, kind, draw),
                                                     kind == picked)))
            await _asset_dock(context, picked)

    await draw()


async def _asset_dock(context: dict[str, Any], picked: str) -> None:
    """The picked asset's card, the file panel's own, without the file's settings."""
    dock = context.get("dock")
    if dock is None:
        return
    kind = str(picked or "")
    library, game_id = context["library"], context["game_id"]
    files = await offload.io(library.files_of, "asset", game_id)
    if kind.startswith(ROM_PICK):
        found = _rom_picked(files, kind)
    else:
        found = _same_file(files, {"kind": kind, "table": context["lens"]}) if kind else None
    dock.clear()
    with dock:
        if found is None:
            with ui.column().classes("manager-dock-empty items-center gap-1"):
                ui.label(t("manager.workbench.file_panel_empty")).classes("manager-empty-title")
                ui.label(t("manager.workbench.file_hint")).classes("manager-help")
            return
        detail = await _asset_detail_of(library, game_id, found)
        await _asset_file_block({**context, "row": found, "kind": found["kind"], "detail": detail,
                                 "served": _served(found, files, context["tables"]),
                                 "link_view": "assets"})


def _asset_entries(context: dict[str, Any],
                   pick: Callable[[str], panel.Pick] | None = None) -> list[tuple[Any, ...]]:
    """The Assets block's rows, as `_assets_block` draws them.

    Kinds the library does not collect are left out entirely. A row saying an EM table
    has no ROM is not a fact about that table, it is the list answering a question this
    library never asked. `pick` makes a kind's row pickable.
    """
    game = context["game"]
    chosen = next((item for item in context["tables"]
                   if item.get("id") == context["lens"]), None)
    kept = _kept_kinds(context, "asset")
    resolved = _only_kept((chosen or {}).get("assets") or {}, kept)
    folder = _only_kept(game.get("assets") or {}, kept)

    entries: list[tuple[Any, ...]] = []

    def row(kind: str, value: Any) -> None:
        entries.append((_asset_name(kind), value, *((pick(kind),) if pick else ())))

    if chosen is not None:
        entries += [(HEADING, t("manager.workbench.table_4"))]
        for kind, state in sorted(resolved.items()):
            row(kind, _resolved_row(context, chosen, kind, state))
            if kind == "script" and state.get("resolution") != "none":
                digest = str(chosen.get("vbs_hash") or "")
                # The hash travels with the asset rather than sitting in a group of
                # identifiers. Only where there is a sidecar to have one.
                if digest:
                    entries.append((t("manager.workbench.script_hash"), digest))
        pinmame = (chosen.get("dependencies") or {}).get("pinmame") or {}
        if _collects("rom", kept) and (pinmame.get("effective") or pinmame.get("declared")):
            # A declared dependency, not a file: no file panel to open, so no pick.
            entries.append((_asset_name("rom"), _rom_row(context, pinmame)))

    entries += [(HEADING, t("manager.workbench.game_folder"))]
    for kind, state in sorted(folder.items()):
        # Already answered above, and more precisely: the table's own lookup says
        # which file wins, where the folder can only say one is somewhere in it.
        if kind in resolved:
            continue
        present = bool(state.get("present"))
        chip = _present_row(present)
        row(kind, chip if present else _with_add(context, kind, chip))
    if _collects("rom", kept):
        for one in context.get("rom_files") or []:
            entries.append((_asset_name("rom"), _rom_file_row(context, one),
                            *((pick(ROM_PICK + str(one["file"]).lower()),) if pick else ())))
    return entries


ROM_PICK = "rom:"


def _rom_picked(files: list[dict[str, Any]], key: str) -> dict[str, Any] | None:
    """The ROM file a pick names, by the lowercase file name a deep link carries."""
    name = key.removeprefix(ROM_PICK)
    return next((one for one in files if one.get("kind") in file_acts.ROM_FILES
                 and str(one.get("file") or "").lower() == name), None)


def _rom_file_row(context: dict[str, Any], one: dict[str, Any]) -> Any:
    """A ROM file in the game's folder: its name and size, and whether a table reads it.
    A table asked for, which the folder lacks, is the This Table row's."""
    name, size = str(one.get("file") or ""), one.get("size_bytes")
    users = [str(user) for user in one.get("users") or []]
    if users:
        mine = context["lens"] and context["lens"] in users
        meaning = t("manager.workbench.rom_used_this") if mine \
            else t("manager.workbench.rom_used_n", count=len(users))
        chip = _state(t("word.in_use"), "on", glyph="check",
                      hint=states.line(t("word.in_use"), meaning))
    else:
        unused = file_states.table("manager.assets.unused_meaning").state(file_states.UNUSED)
        chip = _state(t("manager.media_ownership.unused"), "off",
                      hint=unused.line() if unused else "")

    def draw() -> None:
        with ui.element("div").classes("manager-fact-edit"):
            ui.label(name).classes("manager-slot-file truncate").tooltip(name)
            if size:
                ui.label(i18n.size(size)).classes("manager-help")
            chip()

    return draw


def _only_kept(states: dict[str, Any], kept: set[str] | None) -> dict[str, Any]:
    """Drop what the library does not collect. `table` is never in the toggles and so
    is never dropped - the .vpx is the library rather than an accessory to it."""
    return {kind: state for kind, state in states.items() if _collects(kind, kept)}


def _collects(kind: str, kept: set[str] | None) -> bool:
    return kept is None or kind in kept or kind in _ALWAYS_KEPT


def _resolved_row(context: dict[str, Any], table: dict[str, Any], kind: str,
                  state: dict[str, Any]) -> Any:
    """Whose file this is, the file itself, and the one act that changes it."""
    external = state.get("resolution") != "none"
    name = str(state.get("file") or "")

    def draw() -> None:
        with ui.element("div").classes("manager-fact-edit"):
            # The script takes no tier. Its absence is not a gap - a table running the
            # script inside its own .vpx is the ordinary table - and "Missing" here
            # would call every one of them broken. `SCRIPT_WORDS` asks the question
            # that has no wrong answer: which script runs.
            if kind == "script":
                word = game_tables.word_for(game_tables.SCRIPT_WORDS, external)
                why = (t("manager.workbench.script_sidecar") if external
                       else t("manager.workbench.script_embedded"))
                ui.label(word).classes("manager-tier manager-tier--off").tooltip(why)
            else:
                tier = media_ownership.for_resolution(state.get("resolution"))
                ui.label(t(tier.noun)).classes(f"manager-tier {tier.css}") \
                    .tooltip(t(tier.why))
            if name:
                ui.label(name).classes("manager-slot-file truncate").tooltip(name)
            if kind == "script":
                _script_actions(context, table, external)
            elif not external:
                _add_action(context, kind)

    return draw


def _present_row(present: bool) -> Any:
    """A folder-level kind, which is either there or not. No tier: nothing resolves it
    per table, and a tier would imply it could."""
    return _state(t("word.present") if present else t("manager.workbench.missing"),
            "on" if present else "off")


def _script_actions(context: dict[str, Any], table: dict[str, Any],
                    external: bool) -> None:
    """Extract a sidecar, or drop one. The script is managed here and Launch only
    reports it."""
    if external and game_tables.runs_its_own_script(table):
        ui.button(t("word.delete"),
            icon=verbs.DELETE, on_click=lambda: _drop_script(context, table)) \
            .props("flat dense no-caps size=sm") \
            .classes("manager-action manager-action--inline manager-action--danger")
    else:
        ui.button(t("manager.workbench.extract"), icon=verbs.EXTRACT,
                on_click=lambda: _extract_script(context, table)) \
            .props("flat dense no-caps size=sm") \
            .classes("manager-action manager-action--inline")
