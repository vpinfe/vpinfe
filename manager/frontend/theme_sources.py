"""The theme sources on Settings > Themes, and the dialog that adds one.

A source is a list of themes (`themes.registries`) or a single theme
(`themes.repositories`). The person pastes an address and the server's check says which
it is, so nobody has to know the kind first. Every change is written to the key its kind
names and then read again, so the Themes page lists what the sources now offer.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from functools import partial
from typing import Any

from nicegui import ui

from common.config import config_schema
from common.failures import why
from common.i18n import t
from common.online import theme_source_checks as checks
from common.online import theme_sources as online
from manager.kit import arrange, offload, panel, undo, verbs
from manager.kit import dialog as frame
from manager.kit.data import Library
from manager.kit.on_page import on_page

logger = logging.getLogger("vpinfe.manager.frontend.theme_sources")

SECTION = "themes"
LISTS, THEMES = "registries", "repositories"

# One event per group, because a move arrives as {from, to} and nothing else.
MOVED = {LISTS: "hub_theme_lists_moved", THEMES: "hub_theme_themes_moved"}

_DEBOUNCE_MS = 400

_CHIP_KEY = {checks.NOT_A_THEME: "not_theme"}


def default_list() -> str:
    found = config_schema.option(SECTION, LISTS)
    return str(found.default) if found is not None else ""


def _held(sources: dict[str, Any], group: str) -> list[str]:
    return [str(one["address"]) for one in sources.get(group) or []]


@on_page
async def _save(library: Library, group: str, addresses: list[str]) -> bool:
    try:
        await offload.io(library.put_config, {SECTION: {group: addresses}})
    except Exception as exc:  # noqa: BLE001 - the reason belongs on the page
        ui.notify(t("manager.settings.save_failed"), caption=why(exc), type="negative")
        return False
    return True


@on_page
async def _read_again(library: Library) -> None:
    """The themes, read from the sources as they now are. Without it the Themes page keeps
    listing what the last read found until the next one."""
    checking = ui.notification(t("manager.settings.checking_themes"), spinner=True,
                               timeout=None)
    try:
        await offload.io(library.themes, True)
    except Exception as exc:  # noqa: BLE001 - the rows say which source failed
        ui.notify(t("manager.settings.check_failed"), caption=why(exc), type="negative")
    finally:
        checking.dismiss()


@on_page
async def read_unread(block: Block, rows: ui.element) -> None:
    """The read *Check now* runs, for a page that found a source with no result. It says
    nothing: the row said Checking, and the redraw says what the read found. A page that
    has moved on while it ran is left alone."""
    block.asked |= block.checking
    try:
        await offload.io(block.library.themes, True)
    except Exception as exc:  # noqa: BLE001 - the rows say which source failed
        logger.warning("Could not read the theme sources: %s", why(exc))
    if not rows.is_deleted:
        block.rerender()


@on_page
async def _change(library: Library, group: str, addresses: list[str]) -> bool:
    if not await _save(library, group, addresses):
        return False
    await _read_again(library)
    return True


class Block:
    """The sources as the page drew them, and what the person can do to one."""

    def __init__(self, library: Library, sources: dict[str, Any],
                 rerender: Callable[[], None], checking: frozenset[str] = frozenset(),
                 asked: set[str] | None = None) -> None:
        self.library = library
        self.rerender = rerender
        self.checking = checking
        self.asked = asked if asked is not None else set()
        self.addresses = {LISTS: _held(sources, LISTS), THEMES: _held(sources, THEMES)}
        self.stale = False
        self._turn = asyncio.Lock()

    async def _commit(self, group: str, addresses: list[str]) -> bool:
        """Write one group, holding the turn. Once one has gone through, the rows on screen
        are the old ones until the page is drawn again, so a drop from them means nothing."""
        if not await _change(self.library, group, addresses):
            return False
        self.addresses[group] = addresses
        self.stale = True
        return True

    async def moved(self, group: str, event: Any) -> None:
        """One row put where it was dropped."""
        try:
            source, target = int(event["from"]), int(event["to"])
        except (KeyError, TypeError, ValueError):
            return
        async with self._turn:
            shown = list(self.addresses[group])
            if (self.stale or source == target
                    or not (0 <= source < len(shown) and 0 <= target < len(shown))):
                return
            shown.insert(target, shown.pop(source))
            await self._commit(group, shown)
        self.rerender()

    async def step(self, group: str, address: str, by: int) -> None:
        at = self.addresses[group].index(address)
        await self.moved(group, {"from": at, "to": at + by})

    @on_page
    async def remove(self, group: str, source: dict[str, Any]) -> None:
        address = str(source["address"])
        async with self._turn:
            held = list(self.addresses[group])
            if address not in held:
                return
            at = held.index(address)
            if not await self._commit(group, [one for one in held if one != address]):
                return

        @on_page
        async def back() -> None:
            async with self._turn:
                now = _held(await offload.io(self.library.theme_sources), group)
                if address not in now:
                    now.insert(min(at, len(now)), address)
                    await offload.io(self.library.put_config, {SECTION: {group: now}})
                    await _read_again(self.library)
            self.rerender()

        undo.offer(t("manager.theme_sources.removed_list" if group == LISTS
                     else "manager.theme_sources.removed_theme", name=_title(group, source)),
                   back)
        self.rerender()


async def moved(state: dict[str, Any], group: str, event: Any) -> None:
    """A drop in one of the groups, for the block the page drew last."""
    block = state.get("theme_sources")
    if block is not None:
        await block.moved(group, event.args)


def rows(block: Block, sources: dict[str, Any]) -> list[tuple[Any, Any]]:
    """The Sources row and the strip under it. A source in `block.checking` has never been
    read: its row says so, and the read that gives it a result runs as the rows are drawn."""
    themes, lists = sources.get(THEMES) or [], sources.get(LISTS) or []
    stock_missing = not any(online.is_stock(str(one["address"])) for one in lists)

    def strip() -> None:
        with ui.row().classes("items-center gap-1 no-wrap"):
            panel.action(t("manager.theme_sources.add"),
                         partial(add_source, block.library, block.rerender),
                         icon=verbs.ADD, hint=t("manager.theme_sources.add.help"))()
            if stock_missing:
                panel.action(t("manager.theme_sources.add_default"),
                             partial(add_default, block.library, block.rerender),
                             icon=verbs.ADD, hint=t("manager.theme_sources.add_default.help"))()

    def draw() -> None:
        shown = _sources(block, themes, lists)
        if block.checking and shown is not None:
            ui.timer(0, partial(read_unread, block, shown), once=True)

    return [(t("manager.theme_sources.label"), draw), (panel.ASIDE, strip)]


def _sources(block: Block, themes: list[dict[str, Any]],
             lists: list[dict[str, Any]]) -> ui.element | None:
    """Single themes first, because one outranks every list, then the lists in their order.
    A drag stays within its kind: each kind is its own arranged run."""
    if not themes and not lists:
        ui.label(t("manager.theme_sources.none")).classes("manager-fact-value")
        return None
    gutter = len(themes) > 1 or len(lists) > 1
    with ui.column().classes("manager-theme-kinds w-full") as kinds:
        for group, found in ((THEMES, themes), (LISTS, lists)):
            if not found:
                continue
            with ui.column().classes("gap-0 w-full manager-plain-rows") \
                    .props(f'data-arrange="{MOVED[group]}"'):
                for at, one in enumerate(found):
                    _row(block, group, one, at, len(found), gutter)
            if len(found) > 1:
                ui.run_javascript(arrange.SCRIPT)
    return kinds


def _row(block: Block, group: str, source: dict[str, Any], at: int, of: int,
         gutter: bool) -> None:
    """`gutter` keeps a row that cannot be dragged in line with the ones that can."""
    address = str(source["address"])
    unread = address in block.checking
    with ui.row().classes("items-center gap-2 w-full no-wrap manager-member-row"):
        if of > 1:
            ui.icon("drag_indicator").classes("manager-drag-handle") \
                .props("tabindex=0 role=button") \
                .tooltip(t("manager.workbench.drag_hint"))
        elif gutter:
            ui.element("div").classes("manager-drag-gutter")
        with ui.column().classes("gap-0 grow min-w-0"):
            with ui.row().classes("items-center gap-2 no-wrap min-w-0"):
                _name(group, source)
                if source.get("level") and not unread:
                    chip = ui.label(_word(source)).classes(
                        f"manager-tier manager-tier--{source['level']} shrink-0")
                    if source.get("reason"):
                        chip.tooltip(str(source["reason"]))
            if said := t("manager.theme_sources.reading") if unread else _detail(group, source):
                line = ui.label(said).classes("manager-member-table")
                if took := source.get("took") or []:
                    line.tooltip(t("manager.theme_sources.took", count=_count(
                        int(source["count"])), taken=len(took), names=", ".join(took)))
        _button, menu = panel.menu_button(verbs.MORE, t("manager.folders.more"))
        with menu:
            panel.verb_entries(acts(block, group, source, at, of), menu.close)


def _name(group: str, source: dict[str, Any]) -> None:
    name = _title(group, source)
    link = str(source.get("link") or "")
    if link:
        panel.link_out(name, to=link)()
    else:
        ui.label(name).classes("manager-fact-value truncate min-w-0")


def _own_title(group: str, source: dict[str, Any]) -> str:
    """What a source is called until somebody names it. The VPinFE Theme List is called
    that, wherever its address says it lives."""
    if group == LISTS and online.is_stock(str(source["address"])):
        return t("manager.theme_sources.stock")
    return str(source["name"])


def _title(group: str, source: dict[str, Any]) -> str:
    return str(source.get("label") or "") or _own_title(group, source)


def acts(block: Block, group: str, source: dict[str, Any], at: int,
         of: int) -> list[panel.Verb]:
    """What the row's menu holds, in order: the moves, Rename, then Remove last and in red.
    An end the row is already at is shown and refused rather than left out, so the two
    moves keep their places from row to row."""
    address = str(source["address"])
    found: list[panel.Verb] = []
    if of > 1:
        found.append(panel.Verb(t("manager.theme_sources.move_up"),
                                partial(block.step, group, address, -1) if at > 0 else None,
                                hint=t("manager.theme_sources.already_first")))
        found.append(panel.Verb(t("manager.theme_sources.move_down"),
                                partial(block.step, group, address, 1) if at < of - 1 else None,
                                hint=t("manager.theme_sources.already_last")))
    found.append(panel.Verb(t("manager.theme_sources.rename"),
                            partial(rename_source, block, group, source)))
    found.append(panel.Verb(t("word.remove"), partial(block.remove, group, source),
                            danger=True))
    return found


def _count(count: int) -> str:
    return t("manager.theme_sources.count", count=count)


def _detail(group: str, source: dict[str, Any]) -> str:
    """The line under a source's name: how many themes a list has, or which version of its
    one theme, and where. The stock list also says where it lives, because its name does
    not."""
    if group == LISTS:
        count = int(source.get("count") or 0)
        named = online.is_stock(str(source["address"])) or bool(source.get("label"))
        where = str(source.get("display") or "") if named else ""
        return " · ".join(part for part in (_count(count) if count else "", where) if part)
    where = str(source.get("display") or "")
    parts = [where if where != _title(group, source) else "",
             str(source.get("version") or ""),
             t("manager.theme_sources.pinned", ref=source["pinned"])
             if source.get("pinned") else ""]
    return " · ".join(part for part in parts if part)


def _word(source: dict[str, Any]) -> str:
    """What the chip says. Only a source with something wrong has one."""
    state = str(source["state"])
    if state == checks.NEEDS_VPINFE:
        return t("manager.theme_sources.state.needs_version", needs=str(source["needs"]))
    return t(f"manager.theme_sources.state.{_CHIP_KEY.get(state, state)}")


def _kept(text: str, suggested: str) -> str:
    """The name to keep for a source: none where it is blank or what it would be called
    anyway, so that a name nobody chose goes on following the source."""
    name = online.clean_name(text)
    return "" if name == online.clean_name(suggested) else name


@on_page
async def _save_name(library: Library, address: str, name: str) -> bool:
    try:
        await offload.io(library.name_theme_source, address, name)
    except Exception as exc:  # noqa: BLE001 - the reason belongs on the page
        ui.notify(t("manager.theme_sources.name_failed"), caption=why(exc), type="negative")
        return False
    return True


async def rename_source(block: Block, group: str, source: dict[str, Any]) -> None:
    """A name for one source. Clearing it, or typing what it would be called anyway, takes
    the name back."""
    own = _own_title(group, source)
    shown: dict[str, Any] = {}

    @on_page
    async def save() -> None:
        box.close()
        if await _save_name(block.library, str(source["address"]),
                       _kept(str(shown["name"].value or ""), own)):
            block.rerender()

    with frame.opened(t("manager.theme_sources.rename_title"), ignore_outside_click=True) as box:
        def draw_name() -> None:
            shown["name"] = frame.field(_title(group, source), placeholder=own)
            shown["name"].props(f"maxlength={online.NAME_LIMIT}")

        panel.facts(ui, [(t("word.name"), draw_name)])
        with frame.footer():
            frame.cancel(box.close)
            go = frame.answer(t("manager.theme_sources.rename"), save, icon=verbs.RENAME)
    frame.focus(box, shown["name"], select=True)
    frame.enter_presses(go)
    box.open()


@on_page
async def add_default(library: Library, done: Callable[[], None]) -> None:
    """The VPinFE Theme List put back, in one press."""
    held = _held(await offload.io(library.theme_sources), LISTS)
    stock = default_list()
    if stock not in held and await _change(library, LISTS, [*held, stock]):
        done()


async def add_source(library: Library, done: Callable[[], None]) -> None:
    """The dialog: one address, checked as it settles, and Add once the check allows it."""
    found: dict[str, Any] = {}
    asked = {"count": 0}
    shown: dict[str, Any] = {}
    # What the name field was last filled with, and whether anything else has been typed.
    named: dict[str, Any] = {"suggested": "", "typed": False}

    def draw_address() -> None:
        control = frame.field(placeholder="https://")
        control.on_value_change(lambda: check(str(control.value or "").strip()))
        shown["address"] = control

    def draw_result() -> None:
        shown["result"] = ui.column().classes("gap-1 w-full")

    def draw_name() -> None:
        control = frame.field()
        control.props(f"maxlength={online.NAME_LIMIT}")
        control.on_value_change(lambda: named.update(
            typed=str(control.value or "") != named["suggested"]))
        shown["name"] = control

    def suggest(name: str) -> None:
        """The name field, filled with what the source is called unless somebody has typed
        their own."""
        control = shown["name"]
        if named["typed"] and control.value:
            return
        named["suggested"] = name
        control.value = name
        named["typed"] = False

    async def check(address: str) -> None:
        asked["count"] += 1
        mine = asked["count"]
        found.clear()
        shown["add"].disable()
        result = shown["result"]
        result.clear()
        suggest("")
        if not address:
            return
        await asyncio.sleep(_DEBOUNCE_MS / 1000)
        if mine != asked["count"] or result.is_deleted:
            return
        with result:
            ui.label(t("manager.theme_sources.checking")).classes("manager-help")
        try:
            said = await offload.io(library.check_theme_source, address)
        except Exception as exc:  # noqa: BLE001 - the dialog says why
            said = {}
            reason = why(exc)
        else:
            reason = ""
        if mine != asked["count"] or result.is_deleted:
            return
        result.clear()
        with result:
            _verdict(said, reason)
        if said.get("addable"):
            found.update(said)
            suggest(_suggested(said))
            shown["add"].enable()

    @on_page
    async def add() -> None:
        if not found:
            return
        box.close()
        group, address = str(found["group"]), str(found["address"])
        name = _kept(str(shown["name"].value or ""), named["suggested"])
        held = _held(await offload.io(library.theme_sources), group)
        if address not in held and await _change(library, group, [*held, address]):
            await _save_name(library, address, name)
            done()

    with frame.opened(t("manager.theme_sources.add_title"), ignore_outside_click=True) as box:
        panel.facts(ui, [
            (t("manager.theme_sources.address"), draw_address),
            (panel.ASIDE, draw_result),
            (t("word.name"), draw_name),
            panel.note(t("manager.theme_sources.trust"))])
        with frame.footer():
            frame.cancel(box.close)
            shown["add"] = frame.answer(t("word.add"), add, icon=verbs.ADD)
    shown["add"].disable()
    frame.focus(box, shown["address"])
    frame.enter_presses(shown["add"])
    box.open()


def _suggested(said: dict[str, Any]) -> str:
    """What a source about to be added would be called: the theme's own name, or the
    owner and repository of a list."""
    if said.get("kind") == checks.LIST and online.is_stock(str(said.get("address") or "")):
        return t("manager.theme_sources.stock")
    return str(said.get("name") or "")


def _verdict(said: dict[str, Any], failure: str) -> None:
    """What the address turned out to be, then anything the person should know before
    adding it."""
    if failure:
        ui.label(failure).classes("manager-help text-negative")
        return
    state = str(said.get("state") or "")
    if state not in checks.REFUSED and state != checks.NOT_ANSWERING:
        _what_it_is(said)
    if state != checks.READY:
        with ui.row().classes("items-center gap-2 no-wrap"):
            if said.get("level"):
                ui.label(_word(said)).classes(
                    f"manager-tier manager-tier--{said['level']} shrink-0")
            ui.label(str(said.get("reason") or "")).classes("manager-help")
    if state in checks.REFUSED:
        return
    if names := said.get("replaces") or []:
        ui.label(t("manager.theme_sources.replaces", names=", ".join(names))) \
            .classes("manager-help")
    if took := said.get("took") or []:
        ui.label(t("manager.theme_sources.took_note", count=len(took))) \
            .classes("manager-help")
    if installs := said.get("installs") or []:
        ui.label(t("manager.theme_sources.installs", names=", ".join(installs))) \
            .classes("manager-help")
    elif not online.is_stock(str(said.get("address") or "")):
        ui.label(t("manager.theme_sources.nothing_installs")).classes("manager-help")


def _what_it_is(said: dict[str, Any]) -> None:
    if said.get("kind") == checks.LIST:
        count = int(said.get("count") or 0)
        lead = t("manager.theme_sources.is_list", count=count)
        name = ""
    else:
        lead = t("manager.theme_sources.is_theme")
        name = " ".join(part for part in (str(said.get("name") or ""),
                                          str(said.get("version") or "")) if part)
    with ui.row().classes("items-center gap-2 no-wrap"):
        ui.label(" · ".join(part for part in (lead, name) if part)) \
            .classes("manager-fact-value")
        if said.get("link"):
            panel.link_out(str(said.get("display") or ""), to=str(said["link"]))()
