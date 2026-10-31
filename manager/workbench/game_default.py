"""A game's default table: the radio list that chooses it and the words around it."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import Any

from nicegui import run, ui

from common.failures import why
from common.i18n import t
from manager.kit import game_tables
from manager.kit.on_page import on_page
from manager.workbench.core import _said_file, _said_name


def _radio(on: bool, hint: str, act: Callable[[], Any] | None = None) -> None:
    """One choice of the group. With no `act` it is a state: filled where it holds, dim
    where `hint` says why it cannot be chosen."""
    mark = ui.icon("radio_button_checked" if on else "radio_button_unchecked") \
        .classes("manager-default-mark").props(f'role=radio aria-checked={str(on).lower()}')
    if on:
        mark.classes(add="manager-default-mark--on")
    elif act is None:
        mark.classes(add="opacity-30")
    else:
        mark.classes(add="cursor-pointer")
        mark.on("click", act)
    mark.tooltip(hint)


def table_choice(context: dict[str, Any], table: dict[str, Any]) -> None:
    """The radio of a table's row: chosen where the game is locked to it. A table that
    cannot be offered says why instead."""
    on = bool(table.get("default")) \
        and (table.get("default_kind") or "") == game_tables.CHOSEN
    blocked = game_tables.why_not_default(table)
    if on:
        _radio(True, t("manager.workbench.locked_on"))
    elif blocked:
        _radio(False, blocked)
    else:
        _radio(False, t("manager.workbench.make_default"), partial(_lock_to, context, table))


def automatic_choice(context: dict[str, Any], tables: list[dict[str, Any]]) -> None:
    """Automatic as the first choice, and the table it plays, so the way back says where
    it lands before it is taken."""
    target = game_tables.automatic_table(tables)
    on = not game_tables.is_locked(tables)
    game = str(context["game"].get("name") or "")
    with ui.row().classes("items-start gap-2 w-full no-wrap manager-member-row"):
        if on:
            _radio(True, t("manager.workbench.automatic_on"))
        else:
            _radio(False, t("manager.workbench.automatic_off"),
                   lambda: _use_automatic(context, game))
        with ui.column().classes("gap-0 grow min-w-0"):
            ui.label(t("manager.game_tables.automatic")).classes("manager-member-name")
            ui.label(t("manager.workbench.default_recent")).classes("manager-member-table")
            if target is not None:
                with ui.column().classes("gap-0 w-full min-w-0 manager-default-target"):
                    _said_name(target, game_tables.table_name(target)) \
                        .classes("manager-member-name min-w-0")
                    if line := game_tables.file_line(target, game):
                        _said_file(line, str(target.get("filename") or ""))


def fell_note(tables: list[dict[str, Any]]) -> None:
    """Says so where the table somebody locked cannot be played and Automatic is."""
    locked = game_tables.fell_from(tables)
    plays = next((row for row in tables if row.get("default")), None)
    if locked is None or plays is None:
        return
    ui.label(t("manager.workbench.default_fell",
               locked=game_tables.table_name(locked), plays=game_tables.table_name(plays))) \
        .classes("manager-help manager-state--warn")


@on_page
async def _lock_to(context: dict[str, Any], table: dict[str, Any]) -> None:
    try:
        await run.io_bound(context["library"].set_default_table, context["game_id"],
                           str(table.get("id") or ""))
    except Exception as exc:
        ui.notify(t("manager.workbench.change_failed"), caption=why(exc), type="negative")
        return
    ui.notify(game_tables.locked_said(str(context["game"].get("name") or ""), table),
              type="positive")
    await context["rebuild"]()


@on_page
async def _use_automatic(context: dict[str, Any], game: str) -> None:
    try:
        after = await run.io_bound(context["library"].set_default_table,
                                   context["game_id"], "")
    except Exception as exc:
        ui.notify(t("manager.workbench.change_failed"), caption=why(exc), type="negative")
        return
    ui.notify(game_tables.automatic_said(game, (after or {}).get("tables") or []),
              type="positive")
    await context["rebuild"]()
