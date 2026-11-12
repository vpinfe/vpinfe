"""The Home shelves on Settings > Library > Discovery: one row per shelf of Discovery's Home
in the order Home draws them, each with its name, what it holds and a switch.

A drag, or Move Up and Move Down, sets the order; a switch hides the shelf. Both write at
once. The rows are the registry's, so a shelf added to it is a row here with nothing else
to do.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from functools import partial
from typing import Any

from nicegui import ui

from common.failures import why
from common.i18n import t
from manager.kit import arrange, offload, panel, verbs
from manager.kit.api import updates_shown
from manager.kit.data import Library
from manager.kit.on_page import on_page
from manager.library import discovery_shelves

# One event for the one list, because a move arrives as {from, to} and nothing else.
MOVED = "hub_discovery_shelves_moved"
KEY = "discovery_shelves"


class Shelves:
    """The arrangement as the page drew it, and what the person can do to it.

    `redraw` draws the rows again and `refresh` the Reset slot. A switch that saved changes
    nothing else on the page, so it refreshes only the slot; the rows are drawn again after
    a move, and after a refused write to put the control back."""

    def __init__(self, library: Library, settings: Mapping[str, Any],
                 redraw: Callable[[], None] | None = None,
                 refresh: Callable[[], None] | None = None, updates: bool = True) -> None:
        self.library = library
        self.updates = updates
        self.redraw: Callable[[], None] = redraw or (lambda: None)
        self.refresh: Callable[[], None] = refresh or (lambda: None)
        self.order = discovery_shelves.order(settings.get(discovery_shelves.ORDER_KEY))
        self.off = self.known_off(set(discovery_shelves.listed(
            settings.get(discovery_shelves.HIDDEN_KEY))))
        self._turn = asyncio.Lock()

    @staticmethod
    def known_off(off: set[str]) -> list[str]:
        """`off` as it is stored: the shelves there are, in the registry's order."""
        return [one for one in discovery_shelves.IDS if one in off]

    @property
    def unchanged(self) -> bool:
        """Whether this is what Home starts with: the default order, every shelf on."""
        return self.order == list(discovery_shelves.IDS) and not self.off

    @on_page
    async def _write(self, changes: dict[str, list[str]]) -> bool:
        try:
            await offload.io(self.library.put_manager_policy, changes)
        except Exception as exc:  # noqa: BLE001 - the reason belongs on the page
            ui.notify(t("manager.settings.save_failed"), caption=why(exc), type="negative")
            return False
        return True

    async def moved(self, event: Any) -> None:
        """One row put where it was dropped, then the list drawn again."""
        try:
            source, target = int(event["from"]), int(event["to"])
        except (KeyError, TypeError, ValueError):
            return
        async with self._turn:
            shown = list(self.order)
            if source == target or not (0 <= source < len(shown) and 0 <= target < len(shown)):
                return
            shown.insert(target, shown.pop(source))
            if await self._write({discovery_shelves.ORDER_KEY: shown}):
                self.order = shown
        # Drawn again either way: a drop leaves the row where it landed, and a refusal
        # must not leave the list showing an order that was never saved.
        self.redraw()
        self.refresh()

    async def step(self, shelf_id: str, by: int) -> None:
        at = self.order.index(shelf_id)
        await self.moved({"from": at, "to": at + by})

    async def flip(self, shelf_id: str, wanted_on: bool) -> None:
        async with self._turn:
            off = set(self.off)
            if wanted_on:
                off.discard(shelf_id)
            else:
                off.add(shelf_id)
            stored = self.known_off(off)
            saved = await self._write({discovery_shelves.HIDDEN_KEY: stored})
            if saved:
                self.off = stored
        if not saved:
            self.redraw()
        self.refresh()

    @on_page
    async def reset(self) -> None:
        async with self._turn:
            if await self._write({discovery_shelves.ORDER_KEY: [],
                                  discovery_shelves.HIDDEN_KEY: []}):
                self.order = list(discovery_shelves.IDS)
                self.off = []
        self.redraw()
        self.refresh()


async def moved(state: dict[str, Any], event: Any) -> None:
    """A drop in the list, for the block the page drew last."""
    block = state.get(KEY)
    if block is not None:
        await block.moved(event.args)


def entries(library: Library, state: dict[str, Any],
            values: Mapping[str, Any]) -> list[tuple[Any, ...]]:
    """The group's rows for the page's list of facts, from the settings the page has already
    read."""
    block = state[KEY] = Shelves(library, values.get(discovery_shelves.SECTION) or {},
                                 updates=updates_shown(dict(values)))
    rows = panel.region(partial(draw, block))
    slot = panel.region(partial(draw_reset, block))
    block.redraw, block.refresh = rows.refill, slot.refill

    def both() -> None:
        rows()
        slot()

    return [(panel.HEADING, t("manager.settings.heading_shelves")),
            panel.lede(t("manager.settings.shelves_note")), (panel.FULL, both)]


def draw(block: Shelves) -> None:
    with ui.column().classes("gap-0 w-full manager-plain-rows").props(f'data-arrange="{MOVED}"'):
        for at, shelf_id in enumerate(block.order):
            _row(block, discovery_shelves.BY_ID[shelf_id], at, len(block.order))
    ui.run_javascript(arrange.SCRIPT)


def draw_reset(block: Shelves) -> None:
    """Reset, only while something differs from the arrangement Home starts with."""
    if block.unchanged:
        return
    with ui.row().classes("items-center pt-1"):
        panel.action(t("word.reset"), block.reset, icon=verbs.RESET,
                     hint=t("manager.settings.shelves_reset_hint"))()


def _row(block: Shelves, shelf: discovery_shelves.Shelf, at: int, of: int) -> None:
    held = shelf.id == discovery_shelves.UPDATES and not block.updates
    with ui.row().classes("items-center gap-2 w-full no-wrap manager-member-row"):
        ui.icon(verbs.DRAG).classes("manager-drag-handle") \
            .props("tabindex=0 role=button").tooltip(t("manager.workbench.drag_hint"))
        with ui.column().classes("gap-0 grow min-w-0"):
            ui.label(t(shelf.name)).classes("manager-fact-value truncate min-w-0")
            ui.label(t("manager.settings.shelf.updates.held") if held
                     else t(shelf.description)).classes("manager-member-table")
        _moves(block, shelf.id, at, of)
        panel.switch(shelf.id not in block.off and not held, _flipper(block, shelf.id),
                     disabled=held, hint=t(shelf.name))()


def _flipper(block: Shelves, shelf_id: str) -> Callable[[Any], Any]:
    return lambda event: block.flip(shelf_id, bool(event.value))


def _moves(block: Shelves, shelf_id: str, at: int, of: int) -> None:
    """Move Up and Move Down, because a drag needs a way to do the same without a pointer.
    An end the row is already at is shown and refused, so the entries keep their places."""
    _button, menu = panel.menu_button(verbs.MORE, t("manager.theme_sources.move"))
    with menu:
        panel.verb_entries([
            panel.Verb(t("manager.theme_sources.move_up"),
                       partial(block.step, shelf_id, -1) if at > 0 else None,
                       hint=t("manager.theme_sources.already_first")),
            panel.Verb(t("manager.theme_sources.move_down"),
                       partial(block.step, shelf_id, 1) if at < of - 1 else None,
                       hint=t("manager.theme_sources.already_last"))])
