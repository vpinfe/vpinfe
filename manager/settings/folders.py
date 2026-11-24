"""The Folders page in Settings > Library: where this device finds your games.

One row per folder, in the order that decides which of two copies of a game is used. What
the disk says is read on every draw and never stored, because a share that has dropped is
the case this page exists to report. Where new games go is said on the folder - a chip, a
menu entry and a switch, all drawn from the rows, so there is no list of options to keep in
step with them.

The row model and the menu are plain functions of the wire's rows, so what a row says and
offers can be read without a page.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

from nicegui import run, ui

from common.config import config_schema, path_state
from common.failures import why
from common.games import library_folders as model
from common.games import tables
from common.i18n import t
from manager.kit import arrange, confirm, offload, panel, undo, verbs
from manager.kit import dialog as frame
from manager.kit.data import Library
from manager.kit.on_page import on_page
from manager.library import import_report
from manager.settings import library_folders

logger = logging.getLogger("vpinfe.manager.settings.folders")

KEY = "library.folders"
# One event for the one list, because a move arrives as {from, to} and nothing else.
MOVED = "hub_folders_moved"

ASK_SECTION, ASK_KEY = "updates", "ask_where_new_games_go"


@dataclass(frozen=True)
class Folder:
    """One folder as the page reads it from the wire."""

    id: str
    path: str
    kind: str
    state: str
    reason: str
    reachable: bool
    writable: bool
    games: int
    duplicates: int
    server: str
    chosen: bool

    @property
    def takes(self) -> bool:
        """Whether a new game could be created here now."""
        return self.kind == model.KIND_ROOT and self.writable

    @classmethod
    def read(cls, row: dict[str, Any]) -> Folder:
        origin = row.get("origin") or {}
        return cls(id=str(row["location_id"]), path=str(row.get("path") or ""),
                   kind=str(row.get("kind") or model.KIND_ROOT),
                   state=str(row.get("state") or ""), reason=str(row.get("reason") or ""),
                   reachable=bool(row.get("reachable")), writable=bool(row.get("writable")),
                   games=int(row.get("games") or 0),
                   duplicates=int(row.get("shadowed") or 0),
                   server=str(origin.get("server") or ""),
                   chosen=bool(row.get("write_to")))


@dataclass(frozen=True)
class Plan:
    """What the page says about where new games go, worked out from the rows.

    `chip` is the folder carrying it, or "" while adding a game asks each time or nothing
    can take one. `switch` is whether the Ask switch is drawn: only with a choice to make.
    """

    folders: tuple[Folder, ...]
    asks: bool
    switch: bool
    chip: str
    nowhere: bool

    def offers(self, folder: Folder) -> bool:
        """Whether Put new games here changes something on this folder."""
        return folder.kind == model.KIND_ROOT and folder.id != self.chip


def plan(found: dict[str, Any]) -> Plan:
    folders = tuple(Folder.read(row) for row in found.get("locations") or [])
    takers = [one for one in folders if one.takes]
    switch = len(takers) > 1
    asks = switch and bool(found.get("ask_where_new_games_go"))
    chosen = next((one.id for one in folders if one.chosen), "")
    return Plan(folders=folders, asks=asks, switch=switch,
                chip="" if asks or not takers else chosen, nowhere=not takers)


def games_said(folder: Folder) -> str:
    """How many games it holds, or nothing where it cannot be read: its chip says why they
    are missing, and a count of zero would say they are not there."""
    if not folder.reachable:
        return ""
    if folder.kind == model.KIND_GAME:
        return t("manager.folders.one_game")
    return t("manager.folders.games", count=folder.games) if folder.games \
        else t("manager.folders.no_games")


def caption(folder: Folder) -> list[tuple[str, bool]]:
    """The line under the path, as (text, is the link to the duplicates)."""
    parts = [(games_said(folder), False),
             (t("manager.folders.on_server", server=folder.server) if folder.server else "",
              False),
             (t("manager.folders.duplicates", count=folder.duplicates)
              if folder.duplicates else "", True)]
    return [part for part in parts if part[0]]


def state_chip(folder: Folder) -> tuple[str, str, str] | None:
    """(word, level, tooltip) for a folder that is not Ready."""
    if folder.state == path_state.OK:
        return None
    word = library_folders.state_of({"state": folder.state})
    level = library_folders.STATE_TIERS.get(folder.state, "bad")
    tip = t("manager.folders.read_only_cost") if folder.state == path_state.READ_ONLY \
        else folder.reason
    return word, level, tip


def chip_tip(folder: Folder) -> str:
    """What the New games chip says on hover, with what happens while its folder is away."""
    where = t("manager.folders.new_games_help")
    if folder.state == path_state.READ_ONLY:
        return t("manager.folders.chip_then", where=where,
                 then=t("manager.folders.chip_read_only"))
    if not folder.reachable:
        return t("manager.folders.chip_then", where=where,
                 then=t("manager.folders.chip_away"))
    return where


def first_sentence(said: str) -> str:
    return said.split(". ", 1)[0].rstrip(".")


def refusal(folder: Folder) -> str:
    """Why a folder cannot take a new game now, in the words under its dimmed entry."""
    return first_sentence(folder.reason) or t("error.path.unwritable")


class Folders:
    """The list as the page drew it, and what the person can do to a row."""

    def __init__(self, library: Library, found: dict[str, Any],
                 redraw: Callable[[], None]) -> None:
        self.library = library
        self.redraw = redraw
        self.plan = plan(found)
        self.order = [one.id for one in self.plan.folders]
        self.asking = bool(found.get("ask_where_new_games_go"))
        self.stale = False
        self._turn = asyncio.Lock()

    @on_page
    async def _reorder(self, order: list[str]) -> bool:
        try:
            await offload.io(self.library.set_location_order, order)
        except Exception as exc:  # noqa: BLE001 - the reason belongs on the page
            ui.notify(t("manager.workbench.reorder_failed"), caption=why(exc),
                      type="negative")
            return False
        self.stale = True
        return True

    async def moved(self, event: Any) -> None:
        """One row put where it was dropped, then the list drawn again."""
        try:
            source, target = int(event["from"]), int(event["to"])
        except (KeyError, TypeError, ValueError):
            return
        async with self._turn:
            shown = list(self.order)
            if (self.stale or source == target
                    or not (0 <= source < len(shown) and 0 <= target < len(shown))):
                return
            shown.insert(target, shown.pop(source))
            await self._reorder(shown)
        # Drawn again either way: a drop leaves the row where it landed, and a refusal
        # must not leave the list showing an order that was never saved.
        self.redraw()

    async def step(self, location_id: str, by: int) -> None:
        at = self.order.index(location_id)
        await self.moved({"from": at, "to": at + by})

    @on_page
    async def put_here(self, location_id: str) -> None:
        """Choosing a folder for new games is choosing not to be asked."""
        try:
            await offload.io(self.library.set_location_write_to, location_id)
            if self.asking:
                await offload.io(self.library.put_config, library_folders.NOT_ASKING)
        except Exception as exc:  # noqa: BLE001
            ui.notify(t("said.save_failed"), caption=why(exc), type="negative")
            return
        self.redraw()

    @on_page
    async def ask_changed(self, event: Any) -> None:
        wanted = bool(getattr(event, "value", False))
        if wanted == self.asking:
            return
        try:
            await offload.io(self.library.put_config,
                             library_folders.ASKING if wanted else library_folders.NOT_ASKING)
        except Exception as exc:  # noqa: BLE001
            ui.notify(t("said.save_failed"), caption=why(exc), type="negative")
            self.redraw()
            return
        self.redraw()

    @on_page
    async def remove(self, folder: Folder) -> None:
        """Acts at once: nothing on disk is touched and adding it back restores its
        games, so Undo is the safety rather than a question."""
        before = list(self.order)
        async with self._turn:
            if self.stale:
                return
            try:
                await run.io_bound(self.library.delete_location, folder.id)
            except Exception as exc:  # noqa: BLE001
                ui.notify(t("said.remove_failed"), caption=why(exc), type="negative")
                return
            self.stale = True

        @on_page
        async def back() -> None:
            await offload.io(self.library.put_location, folder.id,
                             {"path": folder.path, "kind": folder.kind})
            held = [str(one.get("location_id"))
                    for one in (await offload.io(self.library.locations)).get("locations") or []]
            # Before the first folder that followed it and is still there: a position
            # counted in the list it left would be wrong after any other removal.
            held = [one for one in held if one != folder.id]
            after = next((one for one in before[before.index(folder.id) + 1:]
                          if one in held), None)
            held.insert(held.index(after) if after else len(held), folder.id)
            await offload.io(self.library.set_location_order, held)
            # Only a choice the person made is restored: with asking on, the chip is the
            # first folder that can take a game, which nobody chose.
            if folder.chosen and not self.asking:
                await offload.io(self.library.set_location_write_to, folder.id)
            self.redraw()

        undo.offer(removed_said(folder), back)
        self.redraw()

    @on_page
    async def add(self) -> None:
        folder_dialog(self.library, self._added, first=self.plan.nowhere)

    @on_page
    async def _added(self, kind: str, path: str) -> None:
        try:
            await offload.io(self.library.put_location, model.mint_location_id(),
                             {"path": path, "kind": kind})
        except Exception as exc:  # noqa: BLE001
            ui.notify(t("said.add_failed"), caption=why(exc), type="negative")
            return
        self.redraw()

    @on_page
    async def change(self, folder: Folder) -> None:
        @on_page
        async def saved(kind: str, path: str) -> None:
            if (path, kind) == (folder.path, folder.kind):
                return
            try:
                await offload.io(self.library.put_location, folder.id,
                                 {"path": path, "kind": kind})
            except Exception as exc:  # noqa: BLE001
                ui.notify(t("said.save_failed"), caption=why(exc), type="negative")
                return
            self.redraw()

        folder_dialog(self.library, saved, current=folder)


async def moved(state: dict[str, Any], event: Any) -> None:
    """A drop in the list, for the block the page drew last."""
    block = state.get("folders")
    if block is not None:
        await block.moved(event.args)


def removed_said(folder: Folder) -> str:
    """The act, then what it did and did not do. A folder that cannot be read has no
    count to give."""
    if folder.kind == model.KIND_GAME and folder.reachable:
        return t("manager.folders.removed", path=folder.path, count=1)
    if folder.reachable and folder.games:
        return t("manager.folders.removed", path=folder.path, count=folder.games)
    return t("manager.folders.removed_bare", path=folder.path)


def acts(block: Folders, folder: Folder, at: int, of: int) -> list[panel.Verb]:
    """What the row's menu holds, in order: where new games go, the moves, Change, then
    Remove last and in red."""
    found: list[panel.Verb] = []
    if block.plan.offers(folder):
        found.append(panel.Verb(t("manager.folders.put_here"),
                                partial(block.put_here, folder.id) if folder.takes else None,
                                hint="" if folder.takes else refusal(folder)))
    if at > 0:
        found.append(panel.Verb(t("manager.theme_sources.move_up"),
                                partial(block.step, folder.id, -1)))
    if at < of - 1:
        found.append(panel.Verb(t("manager.theme_sources.move_down"),
                                partial(block.step, folder.id, 1)))
    found.append(panel.Verb(t("manager.folders.change"), partial(block.change, folder)))
    found.append(panel.Verb(t("word.remove"), partial(block.remove, folder), danger=True))
    return found


def draw(block: Folders, importing: dict[str, Any] | None,
         below: list[tuple[Any, ...]] | None = None,
         newest_import: tuple[str, str] | None = None) -> None:
    folders = block.plan.folders
    if not folders:
        _empty(block, importing, below, newest_import)
        return
    entries: list[tuple[Any, ...]] = [(panel.FULL, partial(_list, block))]
    if len(folders) > 1:
        entries.append(panel.intro(t("manager.folders.priority")))
    if block.plan.nowhere:
        entries.append(panel.intro(t("manager.folders.nowhere")))
    entries.append((panel.FULL, partial(_buttons, block, importing, newest_import)))
    if block.plan.switch:
        ask = config_schema.option(ASK_SECTION, ASK_KEY)
        if ask is not None:
            entries += [(ask.label, panel.switch(block.asking, block.ask_changed)),
                        panel.note(ask.description)]
    panel.facts(ui, [*_heading(), *entries, *(below or [])])


def _heading() -> list[tuple[Any, ...]]:
    """The library's own group is named, whether or not other groups follow it."""
    return [(panel.HEADING, t("manager.folders.heading_library"))]


def _list(block: Folders) -> None:
    folders = block.plan.folders
    with ui.column().classes("gap-0 w-full").props(f'data-arrange="{MOVED}"'):
        for at, folder in enumerate(folders):
            _row(block, folder, at, len(folders))
    if len(folders) > 1:
        ui.run_javascript(arrange.SCRIPT)


def _row(block: Folders, folder: Folder, at: int, of: int) -> None:
    with ui.row().classes("items-center gap-2 w-full no-wrap manager-member-row"):
        if of > 1:
            ui.icon("drag_indicator").classes("manager-drag-handle") \
                .props("tabindex=0 role=button") \
                .tooltip(t("manager.workbench.drag_hint"))
        with ui.column().classes("gap-0 grow min-w-0"):
            panel.path_text(folder.path)
            _caption(block, folder)
        if chip := state_chip(folder):
            word, level, tip = chip
            ui.label(word).classes(f"manager-tier manager-tier--{level} shrink-0") \
                .tooltip(tip)
        _button, menu = panel.menu_button(verbs.MORE, t("manager.folders.more"))
        with menu:
            panel.verb_entries(acts(block, folder, at, of), menu.close)


def _caption(block: Folders, folder: Folder) -> None:
    parts = caption(folder)
    takes_new = block.plan.switch and folder.id == block.plan.chip
    if not parts and not takes_new:
        return
    with ui.row().classes("items-center gap-1 no-wrap"):
        for place, (said, is_link) in enumerate(parts):
            if place:
                ui.label("·").classes("manager-member-table")
            if is_link:
                panel.action(said, partial(_duplicates, block, folder),
                             icon=verbs.DUPLICATE, inline=True,
                             hint=t("manager.folders.duplicates_help"))()
            else:
                ui.label(said).classes("manager-member-table")
        if takes_new:
            ui.label(t("manager.folders.new_games")) \
                .classes("manager-member-chip manager-chip-quiet") \
                .tooltip(chip_tip(folder))



def _buttons(block: Folders, importing: dict[str, Any] | None,
             newest_import: tuple[str, str] | None) -> None:
    with ui.row().classes("items-center gap-2"):
        panel.action(t("manager.folders.add_folder"), block.add, icon=verbs.ADD,
                     hint=t("manager.folders.add_help"))()
        if importing is not None:
            panel.action(t("manager.folders.import"),
                         partial(library_folders.open_importer, importing, block.redraw),
                         icon=verbs.IMPORT,
                         hint=t("manager.folders.import_help"))()
            if newest_import is not None:
                import_report.latest_line(newest_import)()


def _empty(block: Folders, importing: dict[str, Any] | None,
           below: list[tuple[Any, ...]] | None = None,
           newest_import: tuple[str, str] | None = None) -> None:
    """A device with no folders finds no games, so this is a finding and leads with its
    fix."""
    def finding() -> None:
        with ui.element("div").classes("manager-attention w-full"):
            ui.icon("error_outline").classes("manager-attention-icon")
            ui.label(t("manager.folders.none_yet")).classes("manager-attention-line grow")
            panel.action(t("manager.folders.add_folder"), block.add, icon=verbs.ADD,
                         hint=t("manager.folders.add_help"))()
            if importing is not None:
                panel.action(t("manager.folders.import"),
                             partial(library_folders.open_importer, importing, block.redraw),
                             icon=verbs.IMPORT,
                             hint=t("manager.folders.import_help"))()
                if newest_import is not None:
                    import_report.latest_line(newest_import)()

    panel.facts(ui, [*_heading(), (panel.FULL, finding),
                     panel.intro(t("manager.folders.none_yet_help")), *(below or [])])


async def page(library: Library, redraw: Callable[[], None], state: dict[str, Any],
               below: Callable[[], Awaitable[list[tuple[Any, ...]]]] | None = None,
               ) -> Callable[[], None]:
    """Read what the disk says, and hand back the draw that puts it on the page. The read
    is here so the draw itself runs without waiting. `below` reads the settings that live
    on this page but belong to the schema, in the same list so they share its label
    column."""
    rows = await below() if below else []
    try:
        found = await offload.io(library.locations)
    except Exception as exc:  # noqa: BLE001 - this page says why, never 500s
        reason = why(exc)
        return lambda: panel.facts(ui, [*_heading(),
                                        panel.intro(t("manager.folders.read_failed"),
                                                    hint=reason), *rows])
    importing = await library_folders.import_action(library)
    newest_import = await import_report.latest(library) if importing is not None else None
    block = Folders(library, found, redraw)
    # Kept for the drag listener, which is registered once and reads it at the drop.
    state["folders"] = block
    return lambda: draw(block, importing, rows, newest_import)


def detect(state_now: str, said: str) -> str:
    """Which kind a typed folder is. Only for a folder that is there: the answer is what is
    inside it, and a path that does not resolve has nothing inside to read."""
    if state_now == "ok" and tables.is_game_folder(said):
        return model.KIND_GAME
    return model.KIND_ROOT


def sort_it(found: dict[str, Any], state_now: str, said: str, first: bool = False) -> str:
    """Decide the kind of the typed folder, and give the line that says so. `first` is for
    a library with no folder taking new games, where the line adds that this one will."""
    found["kind"] = detect(state_now, said)
    if state_now != "ok":
        return ""
    if found["kind"] == model.KIND_GAME:
        return t("manager.folders.one_game_folder")
    return t("manager.folders.first_home" if first else "manager.folders.many_games")


def folder_dialog(library: Library, saved: Callable[[str, str], Any], *,
                  current: Folder | None = None, first: bool = False) -> None:
    """The folder, asked before the row exists, or changed on the row that holds it.

    Not a placeholder row to edit afterwards: a location with no folder reads as
    unreachable, the same words a share that has dropped uses. Changing adds **Contains**,
    which Add settles by detection and nothing else corrects. `saved` is handed the kind
    and the path once the dialog has closed.
    """
    changing = current is not None
    found: dict[str, Any] = {"kind": current.kind if current else model.KIND_ROOT}
    held: dict[str, Any] = {}

    def checked(state_now: str, said: str) -> str:
        if current is None:
            return sort_it(found, state_now, said, first)
        # Detection sets the radio, and only for a path that is not the one already held:
        # that one's kind is what the person has said it is.
        if said != current.path and state_now == "ok":
            found["kind"] = detect(state_now, said)
            held["contains"].value = found["kind"]
        return ""

    def draw_folder() -> None:
        held["folder"] = panel.path_field(
            placeholder="/path/to/your/games", wants="dir", width="w-full",
            value=current.path if current else "", on_checked=checked,
            browse=library.folders)

    def kind_changed(event: Any) -> None:
        found["kind"] = str(getattr(event, "value", "") or model.KIND_ROOT)

    def remember(control: Any) -> None:
        held["contains"] = control

    async def keep() -> None:
        wanted = (held["folder"].value or "").strip()
        if not wanted:
            held["folder"].props["error"] = True
            held["folder"].props["error-message"] = t("manager.folders.needs_name")
            return
        box.close()
        await saved(str(found["kind"]), wanted)

    entries: list[tuple[Any, ...]] = [(t("word.folder"), draw_folder)]
    if changing:
        entries.append((t("word.contains"), panel.choice(
            [(model.KIND_ROOT, t("manager.folders.many_games"), ""),
             (model.KIND_GAME, t("manager.folders.one_game_folder"), "")],
            found["kind"], kind_changed, on_control=remember)))
    with frame.opened(t("manager.folders.change_title") if changing
                      else t("manager.folders.add_folder"), ignore_outside_click=True) as box:
        panel.facts(ui, entries)
        with frame.footer():
            frame.cancel(box.close)
            go = frame.answer(t("word.save" if changing else "word.add"), keep,
                              icon=verbs.SAVE if changing else verbs.ADD)
    frame.focus(box, held["folder"])
    frame.enter_presses(go)
    box.open()


@on_page
async def adopt_copy(library: Library, location_id: str, copy: dict[str, Any]) -> bool:
    """Settle one duplicate by giving that game folder an id of its own.

    Confirmed because it is the only thing in any of this that writes to somebody's game
    folder, and because it decides which copy is which - the thing the install
    deliberately refuses to decide on its own.
    """
    if not await confirm.ask(
            t("manager.workbench.own_question"),
            detail=t("manager.workbench.own_detail"),
            lines=[str(copy.get("path") or "")],
            confirm=t("manager.workbench.make_own"), icon=verbs.SPLIT):
        return False
    try:
        await run.io_bound(library.adopt_shadowed, location_id, str(copy.get("path") or ""))
    except Exception as exc:  # noqa: BLE001
        ui.notify(t("said.action_failed"), caption=why(exc), type="negative")
        return False
    ui.notify(t("manager.workbench.own_done"), type="positive")
    return True


def draw_copies(copies: list[dict[str, Any]],
                adopt: Callable[[dict[str, Any]], Any]) -> None:
    """The duplicates, one row each: the folder, what to do about it, and which copy is
    used instead."""
    for one in copies:
        # Marked, so the action stays visible rather than waiting for a hover: every row
        # here is one somebody came to act on.
        with ui.column().classes("gap-0 w-full manager-member-row") \
                .props('data-origin="shadowed"'):
            with ui.row().classes("items-center gap-2 w-full no-wrap"):
                ui.label(Path(str(one.get("path") or "")).name) \
                    .classes("manager-member-name grow min-w-0 truncate") \
                    .tooltip(str(one.get("path") or ""))
                with ui.element("div").classes("manager-row-action"):
                    ui.button(t("manager.workbench.own_action"), icon=verbs.SPLIT,
                              on_click=lambda _, o=one: adopt(o)) \
                        .props("flat dense no-caps size=sm")
            used = str(one.get("used_path") or "")
            ui.label(t("manager.workbench.answered", value=used)) \
                .classes("manager-member-table truncate").tooltip(used)


@on_page
async def _duplicates(block: Folders, folder: Folder) -> None:
    """What this folder holds that a higher one is already answering for, and the one
    thing to do about each. Close is the only way out otherwise."""
    try:
        copies = await offload.io(block.library.shadowed_here, folder.id)
    except Exception as exc:  # noqa: BLE001
        ui.notify(t("manager.folders.duplicates_failed"), caption=why(exc),
                  type="negative")
        return
    if not copies:
        block.redraw()
        return

    async def adopt(one: dict[str, Any]) -> None:
        if await adopt_copy(block.library, folder.id, one):
            box.close()
            block.redraw()

    with frame.opened(t("manager.folders.duplicates_title", path=folder.path)) as box:
        ui.label(t("manager.workbench.shadowed_intro")).classes("manager-help")
        draw_copies(copies, adopt)
        with frame.footer():
            frame.cancel(box.close, t("word.close"))
    box.open()
