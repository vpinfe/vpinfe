"""Getting games into a collection: the one add every surface offers, and its Undo."""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from functools import partial
from itertools import pairwise
from typing import Any

from nicegui import run, ui

from common.failures import why
from common.games.collection_store import MANUAL_ORDER
from common.i18n import t
from manager.kit import dialog as frame
from manager.kit import offload, panel, remembered, undo, verbs
from manager.kit.on_page import on_page

logger = logging.getLogger("vpinfe.manager.library.collection_adds")

# What a row being added is, which is what the words count.
GAMES = "games"
TABLES = "tables"

# Member origins in a collection's stored membership. `missing` names a game this
# library no longer has - still a stored row, and still a place in the order.
_IN = ("named", "filter")
_STORED = ("named", "missing")

_ADDED = {GAMES: "manager.adds.added_games", TABLES: "manager.adds.added_tables"}
_ADDED_HELD = {GAMES: "manager.adds.held_games",
               TABLES: "manager.adds.held_tables"}
_EXCEPTION = {GAMES: "manager.adds.exception_games",
              TABLES: "manager.adds.exception_tables"}
_EXCEPTION_HELD = {GAMES: "manager.adds.held_exception_games",
                   TABLES: "manager.adds.held_exception_tables"}


@dataclass(frozen=True)
class Row:
    """A game following its default table, or one table of it held to that table."""

    game: str
    table: str = ""


@dataclass
class Wrote:
    """What one add changed, which is what its Undo takes back."""

    smart: bool = False
    held: int = 0
    written: list[Row] = field(default_factory=list)
    lifted: list[Row] = field(default_factory=list)
    put_back: int = 0
    # The order it had, where dropping at a place made it Custom Order.
    order: dict[str, str] | None = None

    @property
    def added(self) -> int:
        return len(self.written) + self.put_back


def holds(members: Sequence[dict[str, Any]]) -> tuple[set[str], set[tuple[str, str]]]:
    """The games a collection holds and each (game, table) it plays, read off its stored
    membership. A game past the limit is held; one taken out is not."""
    out = {str(one.get("game") or "") for one in members
           if one.get("origin") == "excluded" and not one.get("ref_table")}
    games: set[str] = set()
    tables: set[tuple[str, str]] = set()
    for member in members:
        game = str(member.get("game") or "")
        if member.get("origin") not in _IN or game in out:
            continue
        table = (member.get("tables") or [{}])[0]
        if table.get("origin") == "excluded":
            continue
        games.add(game)
        if table.get("id"):
            tables.add((game, str(table["id"])))
    return games, tables


def held_rows(members: Sequence[dict[str, Any]], rows: Iterable[Row]) -> list[Row]:
    games, tables = holds(members)
    return [row for row in rows
            if ((row.game, row.table) in tables if row.table else row.game in games)]


def keeping_out(members: Sequence[dict[str, Any]], row: Row) -> list[Row]:
    """The exclusions that keep `row` out: every one naming its game for a game, and the
    whole game's and that table's for one table."""
    return [Row(str(one.get("game") or ""), str(one.get("ref_table") or ""))
            for one in members
            if one.get("origin") == "excluded" and one.get("game") == row.game
            and (not row.table or str(one.get("ref_table") or "") in ("", row.table))]


def listed_order(members: Sequence[dict[str, Any]]) -> list[str]:
    """One game id per stored row, as the list shows them: what setting an order takes."""
    return [str(one.get("game") or "") for one in members
            if one.get("origin") in _STORED]


def write(library: Any, name: str, rows: Sequence[Row], at: int | None = None) -> Wrote:
    """Put `rows` in `name` and answer what changed. Blocking: run it off the loop.

    A row it holds is left alone, and one it keeps out is let back in before anything is
    written. `at` places the new rows in a collection without rules, and makes its order
    Custom Order.
    """
    collection = next((one for one in library.load_collections()
                       if one.get("name") == name), None)
    if collection is None:
        raise LookupError(t("manager.page.gone"))
    wrote = Wrote(smart=(collection.get("type") or "") == "filter")
    members = library.collection_members(name).get("members") or []
    held = held_rows(members, rows)
    wrote.held = len(held)
    wanted = [row for row in rows if row not in held]
    for row in wanted:
        for ref in keeping_out(members, row):
            if ref not in wrote.lifted:
                library.unexclude_from_collection(name, ref.game, ref.table)
                wrote.lifted.append(ref)
    if wrote.lifted:
        members = library.collection_members(name).get("members") or []
        back = held_rows(members, wanted)
        wrote.put_back = len(back)
        wanted = [row for row in wanted if row not in back]
    before = listed_order(members)
    for row in wanted:
        library.add_to_collection(name, row.game, row.table)
        wrote.written.append(row)
    if at is not None and wanted and not wrote.smart:
        place = max(0, min(at, len(before)))
        if (collection.get("order_by") or "") != MANUAL_ORDER:
            wrote.order = {"order_by": str(collection.get("order_by") or ""),
                           "direction": str(collection.get("direction") or "")}
        library.set_collection_order(
            name, before[:place] + [row.game for row in wanted] + before[place:])
    return wrote


def unwrite(library: Any, name: str, wrote: Wrote) -> None:
    """Take back what `write` did. Blocking: run it off the loop.

    A row already gone is not an error: the collection has what Undo asked for.
    """
    members = library.collection_members(name).get("members") or []
    stored = {(str(one.get("game") or ""), str(one.get("ref_table") or ""))
              for one in members if one.get("origin") in _STORED}
    for row in wrote.written:
        if (row.game, row.table) in stored:
            library.remove_from_collection(name, row.game, row.table)
    for ref in wrote.lifted:
        library.exclude_from_collection(name, ref.game, ref.table)
    if wrote.order and wrote.order.get("order_by"):
        library.patch_collection(name, {key: value for key, value in wrote.order.items()
                                        if value})


def said(name: str, wrote: Wrote, what: str) -> str:
    """The message, counted in what the rows are."""
    if not wrote.added:
        return t("manager.adds.all_held", count=wrote.held, name=name)
    exception = is_exception(wrote)
    if wrote.held:
        keys = _EXCEPTION_HELD if exception else _ADDED_HELD
        return t(keys[what], count=wrote.added, name=name, held=wrote.held)
    return t((_EXCEPTION if exception else _ADDED)[what], count=wrote.added, name=name)


def is_exception(wrote: Wrote) -> bool:
    """A smart collection's rules did not match these, so they stay on their own."""
    return wrote.smart and bool(wrote.written)


@on_page
async def add(library: Any, name: str, rows: Iterable[Row], *, what: str = GAMES,
              at: int | None = None,
              then: Callable[[], Awaitable[Any]] | None = None) -> Wrote | None:
    """Add `rows` to `name`, say what happened, and offer the way back.

    `then` redraws whatever shows the collection, after the add and again after Undo.
    """
    asked = list(dict.fromkeys(rows))
    try:
        wrote = await offload.io(write, library, name, asked, at)
    except Exception as exc:  # noqa: BLE001 - the reason belongs on screen
        ui.notify(t("said.add_failed"), caption=why(exc), type="negative")
        return None
    used(name)
    if then is not None:
        await then()

    async def reverse() -> None:
        await run.io_bound(unwrite, library, name, wrote)
        if then is not None:
            await then()

    if not wrote.added:
        ui.notify(said(name, wrote, what), type="positive")
    else:
        undo.offer(said(name, wrote, what), reverse, warn=is_exception(wrote),
                   icon=verbs.SMART if is_exception(wrote) else None)
    return wrote


# --- taking games back out -------------------------------------------------------------


@dataclass
class Took:
    """What one removal changed, which is what its Undo puts back."""

    removed: list[Row] = field(default_factory=list)
    kept_out: list[Row] = field(default_factory=list)
    # None where an order sorts the collection: only a hand arrangement is put back.
    order: list[Row] | None = None
    # A row naming a game or a table the library no longer has.
    for_good: bool = False
    # The tables a table-level removal acted on.
    tables: set[Row] = field(default_factory=set)

    @property
    def taken(self) -> int:
        return len({row.game for row in self.removed + self.kept_out})


def row_of(member: dict[str, Any]) -> Row:
    """The stored ref a listed row is, by the table it names."""
    return Row(str(member.get("game") or ""), str(member.get("ref_table") or ""))


def _stored(members: Sequence[dict[str, Any]]) -> list[Row]:
    return [row_of(one) for one in members if one.get("origin") in _STORED]


def _refused(member: dict[str, Any]) -> bool:
    table = (member.get("tables") or [{}])[0]
    return member.get("origin") == "missing" \
        or bool(member.get("ref_table")) and table.get("origin") == "missing"


def _read(library: Any, name: str) -> tuple[dict[str, Any], list[dict[str, Any]], Took]:
    collection = next((one for one in library.load_collections()
                       if one.get("name") == name), None)
    if collection is None:
        raise LookupError(t("manager.page.gone"))
    members = library.collection_members(name).get("members") or []
    return collection, members, Took(order=_stored(members)
                                     if collection.get("order_by") == MANUAL_ORDER else None)


def take(library: Any, name: str, games: Sequence[str]) -> Took:
    """Take `games` out of `name`: every row naming one, and the rules' match kept out
    where its rules would bring it back. Blocking: run it off the loop."""
    collection, members, took = _read(library, name)
    wanted = set(games)
    stored = [one for one in members
              if one.get("origin") in _STORED and one.get("game") in wanted]
    took.removed = [row_of(one) for one in stored]
    took.for_good = any(_refused(one) for one in stored)
    for row in took.removed:
        library.remove_from_collection(name, row.game, row.table)
    if (collection.get("type") or "") == "filter":
        if took.removed:
            members = library.collection_members(name).get("members") or []
        games_now = holds(members)[0]
        took.kept_out = [Row(game) for game in games if game in games_now]
        for row in took.kept_out:
            library.exclude_from_collection(name, row.game, row.table)
            took.tables.add(row)
    return took


def take_row(library: Any, name: str, member: dict[str, Any]) -> Took:
    """Take one row of the list out of `name`: the ref it is and nothing else naming its
    game, with the game kept out where the rules would list it again. Blocking: run it
    off the loop."""
    collection, _members, took = _read(library, name)
    row = row_of(member)
    took.for_good = _refused(member)
    if member.get("origin") == "filter":
        library.exclude_from_collection(name, row.game, row.table)
        took.kept_out = [row]
        return took
    library.remove_from_collection(name, row.game, row.table)
    took.removed = [row]
    if (collection.get("type") or "") == "filter" and any(
            one.get("game") == row.game and one.get("origin") == "filter"
            for one in library.collection_members(name).get("members") or []):
        library.exclude_from_collection(name, row.game, "")
        took.kept_out = [Row(row.game)]
    return took


def take_tables(library: Any, name: str, rows: Sequence[Row]) -> Took:
    """Take the tables `rows` name out of `name`: each stored ref that plays one, and the
    table kept out where its rules would list it again. Blocking: run it off the loop."""
    collection, members, took = _read(library, name)
    for row in rows:
        for member in members:
            played = (member.get("tables") or [{}])[0]
            if member.get("origin") != "named" or member.get("game") != row.game \
                    or str(played.get("id") or "") != row.table:
                continue
            ref = row_of(member)
            library.remove_from_collection(name, ref.game, ref.table)
            took.removed.append(ref)
            took.tables.add(row)
            took.for_good = took.for_good or _refused(member)
    if (collection.get("type") or "") == "filter":
        if took.removed:
            members = library.collection_members(name).get("members") or []
        tables_now = holds(members)[1]
        took.kept_out = [row for row in rows if (row.game, row.table) in tables_now]
        for row in took.kept_out:
            library.exclude_from_collection(name, row.game, row.table)
    return took


def untake(library: Any, name: str, took: Took) -> None:
    """Put back what `take`, `take_tables` or `take_row` took. Blocking: run it off the loop."""
    for row in took.kept_out:
        library.unexclude_from_collection(name, row.game, row.table)
    for row in took.removed:
        library.add_to_collection(name, row.game, row.table)
    if took.order:
        _put_in_place(library, name, took.order)


def _put_in_place(library: Any, name: str, order: list[Row]) -> None:
    """`order` written back over the rows stored now, where they are the same rows."""
    members = library.collection_members(name).get("members") or []
    now = _stored(members)
    if Counter(now) != Counter(order):
        return
    refused = {row_of(one) for one in members if _refused(one)}
    for game in dict.fromkeys(row.game for row in order):
        wanted = [row for row in order if row.game == game]
        if [row for row in now if row.game == game] == wanted or refused & set(wanted):
            continue
        for row in wanted[1:]:
            library.remove_from_collection(name, game, row.table)
        for ahead, row in pairwise(wanted):
            library.add_to_collection(name, game, row.table, ahead.table)
    library.set_collection_order(name, [row.game for row in order])


async def remove(library: Any, name: str, games: Sequence[str], *,
                 then: Callable[[], Awaitable[Any]] | None = None) -> None:
    """Take `games` out of `name`, say so, and offer the way back."""
    await _taken(partial(take, library, name, list(games)), library, name, then)


async def remove_tables(library: Any, name: str, rows: Sequence[Row], *,
                        then: Callable[[], Awaitable[Any]] | None = None) -> None:
    """Take the tables `rows` name out of `name`, say so, and offer the way back."""
    await _taken(partial(take_tables, library, name, list(rows)), library, name, then,
                 what=TABLES)


async def remove_row(library: Any, name: str, member: dict[str, Any], *,
                     then: Callable[[], Awaitable[Any]] | None = None) -> None:
    """Take one row of the list out of `name`, say so, and offer the way back."""
    await _taken(partial(take_row, library, name, member), library, name, then)


@on_page
async def _taken(taking: Callable[[], Took], library: Any, name: str,
                 then: Callable[[], Awaitable[Any]] | None, what: str = GAMES) -> None:
    try:
        took = await offload.io(taking)
    except Exception as exc:  # noqa: BLE001 - the reason belongs on screen
        ui.notify(t("said.action_failed"), caption=why(exc), type="negative")
        return
    if then is not None:
        await then()

    async def reverse() -> None:
        await run.io_bound(untake, library, name, took)
        if then is not None:
            await then()

    kept_out = bool(took.kept_out) and not took.removed
    if what == TABLES:
        key = "manager.adds.excluded_tables" if kept_out else "manager.adds.removed_tables"
        said = t(key, count=len(took.tables), name=name)
    else:
        key = "manager.adds.excluded_games" if kept_out else "manager.adds.removed_games"
        said = t(key, count=took.taken, name=name)
    if took.for_good:
        ui.notify(said, type="positive")
    else:
        undo.offer(said, reverse)


# --- the collections added to last ---------------------------------------------

_RECENT = "recent_collections"
RECENT_HELD = 5


def recent(known: Iterable[str]) -> list[str]:
    """The collections added to most recently that still exist, newest first, for this
    Manager user."""
    here = set(known)
    return [name for name in remembered.get(_RECENT) or [] if name in here][:RECENT_HELD]


def used(name: str) -> None:
    kept = [one for one in remembered.get(_RECENT) or [] if one != name]
    remembered.put(_RECENT, [name, *kept][:RECENT_HELD])


def renamed(old: str, new: str) -> None:
    held = remembered.get(_RECENT) or []
    if old in held:
        remembered.put(_RECENT, [new if one == old else one for one in held])


# --- what a grid's menus offer ----------------------------------------------------

_PICK_TITLE = {GAMES: "manager.adds.pick_games", TABLES: "manager.adds.pick_tables"}


@dataclass
class Offer:
    """Rows a menu offers to add, and what redraws the grid behind them."""

    library: Any
    rows: list[Row]
    what: str
    # Names the one row, for a title; empty over several.
    subject: str
    then: Callable[[], Awaitable[Any]]
    # The collection the grid is narrowed to, whose Remove joins the menu.
    narrowed: str = ""


@dataclass
class Read:
    """What a menu needs to know before it is drawn."""

    kinds: dict[str, bool]
    recent: list[str]
    held: dict[str, list[dict[str, Any]]]


async def read(library: Any, narrowed: str = "") -> Read:
    """The collections, which were added to last, and what those hold. Off the loop.
    Never raises."""
    try:
        collections = await offload.io(library.load_collections)
    except Exception:  # noqa: BLE001 - the menu draws what it has
        logger.warning("manager: could not read the collections for a menu", exc_info=True)
        return Read(kinds={}, recent=[], held={})
    kinds = {str(one.get("name") or ""): (one.get("type") or "") == "filter"
             for one in collections}
    last = recent(kinds)
    wanted = list(dict.fromkeys(last + ([narrowed] if narrowed in kinds else [])))
    held = await offload.io(library.held_members, wanted) if wanted else {}
    return Read(kinds=kinds, recent=last, held=held)


def holds_all(held: dict[str, list[dict[str, Any]]], name: str, rows: list[Row]) -> bool:
    members = held.get(name)
    return members is not None and len(held_rows(members, rows)) == len(rows)


def draw(offer: Offer, known: Read, close: Callable[[], Any]) -> None:
    """The adds for a grid's row or selection, drawn into the menu being built. `known`
    must be read when the menu opens: what it holds decides the quick item and the checks."""
    def go(name: str) -> Callable[[], Any]:
        async def run_it() -> None:
            close()
            await add(offer.library, name, offer.rows, what=offer.what, then=offer.then)
        return run_it

    if known.recent and not holds_all(known.held, known.recent[0], offer.rows):
        top = known.recent[0]
        _item(t("manager.adds.add_to", name=top), go(top), slot=False)
    parent = _item(t("manager.adds.menu"), None, opens=True, slot=False)
    with parent, ui.menu().props('anchor="top end" self="top start"') as sub:
        if known.recent:
            ui.item_label(t("manager.adds.recent")).props("header") \
                .classes("manager-menu-header")
            for name in known.recent:
                _toggle(name, _toggler(offer, known.held, close, name),
                        smart=known.kinds.get(name, False),
                        have=len(held_rows(known.held.get(name) or [], offer.rows)),
                        of=len(offer.rows))
            ui.separator()

        async def more() -> None:
            sub.close()
            close()
            await pick(offer)

        async def new() -> None:
            sub.close()
            close()
            await new_with(offer)

        _item(t("manager.adds.more_collections"), more)
        _item(t("manager.adds.new_collection"), new)
    if offer.narrowed and offer.narrowed in known.kinds and offer.what == GAMES:
        async def take_out() -> None:
            close()
            await remove(offer.library, offer.narrowed, [row.game for row in offer.rows],
                         then=offer.then)

        _item(t("manager.adds.remove_from", name=offer.narrowed), take_out, slot=False)


def _toggler(offer: Offer, held: dict[str, list[dict[str, Any]]], close: Callable[[], Any],
             name: str) -> Callable[[], Any]:
    @on_page
    async def run_it() -> None:
        close()
        if not holds_all(held, name, offer.rows):
            await add(offer.library, name, offer.rows, what=offer.what, then=offer.then)
        elif offer.what == TABLES:
            await remove_tables(offer.library, name, offer.rows, then=offer.then)
        else:
            await remove(offer.library, name, [row.game for row in offer.rows],
                         then=offer.then)
    return run_it


def draw_every(offer: Offer, collections: list[dict[str, Any]],
               held: dict[str, list[dict[str, Any]]], close: Callable[[], Any]) -> None:
    for one in sorted(collections, key=lambda one: str(one.get("name") or "").casefold()):
        name = str(one.get("name") or "")
        _toggle(name, _toggler(offer, held, close, name),
                smart=(one.get("type") or "") == "filter",
                have=len(held_rows(held.get(name) or [], offer.rows)), of=len(offer.rows))
    if collections:
        ui.separator()

    async def new() -> None:
        close()
        await new_with(offer)

    _item(t("manager.adds.new_collection"), new)


def _item(label: str, act: Callable[[], Any] | None, *, smart: bool = False,
          opens: bool = False, slot: bool = True) -> Any:
    """One entry: the Smart mark in the leading slot, a drill arrow in the trailing one
    where it opens a submenu. `slot=False` is the entry in a menu of verbs, which has no
    leading slot."""
    def mark() -> None:
        ui.icon(verbs.SMART).classes("manager-menu-mark")

    def trail() -> None:
        ui.icon(verbs.DRILL).classes("manager-menu-trail")

    return panel.menu_entry(label, act, mark=mark if smart else None,
                            trail=trail if opens else None, auto_close=False, slot=slot)


def _toggle(label: str, act: Callable[[], Any], *, smart: bool, have: int, of: int) -> Any:
    """A click adds the missing rows, or removes all of them when `have == of`."""
    whole = have == of

    def mark() -> None:
        ui.icon(verbs.SMART).classes("manager-menu-mark")

    def trail() -> None:
        if whole:
            with ui.element("span").classes("manager-menu-trail manager-menu-toggle"):
                ui.icon(verbs.CHOOSE).classes("manager-menu-check")
                ui.label(t("word.remove")).classes("manager-menu-remove")
        else:
            ui.label(t("manager.adds.some_held", held=have, count=of)) \
                .classes("manager-menu-trail")

    item = panel.menu_entry(label, act, mark=mark if smart else None,
                            trail=trail if have else None,
                            classes="manager-menu-checked" if whole else "",
                            auto_close=False)
    state = "true" if whole else "mixed" if have else "false"
    item.props(f"role=menuitemcheckbox aria-checked={state}")
    return item


# --- More Collections... and New Collection... ------------------------------------

_NEW = "\x00new"


@on_page
async def pick(offer: Offer) -> None:
    """Every collection, typed into, with what each is and holds: the rest of the menu."""
    library = offer.library
    try:
        collections = await offload.io(library.load_collections)
        names = [str(one.get("name") or "") for one in collections]
        held = await offload.io(library.held_members, names) if names else {}
    except Exception as exc:  # noqa: BLE001 - the reason belongs on screen
        ui.notify(t("said.action_failed"), caption=why(exc), type="negative")
        return
    title = (t("manager.adds.pick_one", name=offer.subject) if offer.subject
             else t(_PICK_TITLE[offer.what], count=len(offer.rows)))
    chosen = await _pick_one(title, collections, held, offer.rows)
    if chosen == _NEW:
        await new_with(offer)
    elif chosen:
        await add(library, chosen, offer.rows, what=offer.what, then=offer.then)


async def _pick_one(title: str, collections: list[dict[str, Any]],
                    held: dict[str, list[dict[str, Any]]], rows: list[Row]) -> str | None:
    chosen: dict[str, str] = {"name": ""}
    ordered = sorted(collections, key=lambda one: str(one.get("name") or "").casefold())
    with frame.opened(title, ignore_outside_click=True) as box:
        with ui.element("div").classes("w-full"):
            find = frame.field(placeholder=t("manager.adds.find_collection"))
        with frame.well() as held_at:
            held_at.classes("manager-collection-pick")
            listing = ui.column().classes("w-full gap-0 manager-well-rows")
        with ui.element("div").classes("mt-1"):
            panel.action(t("manager.adds.new_collection"), lambda: box.submit(_NEW),
                         icon=verbs.CREATE,
                         hint=t("manager.adds.new_collection.help"))()
        with frame.footer():
            frame.cancel(lambda: box.submit(None))
            go = frame.answer(t("word.add"), lambda: box.submit(chosen["name"] or None),
                              icon=verbs.ADD)
        go.disable()

        def shown() -> list[dict[str, Any]]:
            typed = str(find.value or "").strip().casefold()
            return [one for one in ordered
                    if typed in str(one.get("name") or "").casefold()]

        def choose(name: str) -> None:
            chosen["name"] = name
            go.enable()
            draw_rows()

        def draw_rows() -> None:
            listing.clear()
            found = shown()
            with listing:
                if not found:
                    ui.label(t("manager.adds.none_match")).classes("manager-help")
                for one in found:
                    name = str(one.get("name") or "")
                    _collection_row(one, holds_all(held, name, rows),
                                    name == chosen["name"], partial(choose, name))

        def typed() -> None:
            pickable = [str(one.get("name") or "") for one in shown()
                        if not holds_all(held, str(one.get("name") or ""), rows)]
            if len(pickable) == 1:
                chosen["name"] = pickable[0]
                go.enable()
            elif chosen["name"] not in pickable:
                chosen["name"] = ""
                go.disable()
            draw_rows()

        find.on_value_change(typed)
        find.on("keydown.enter", lambda: box.submit(chosen["name"]) if chosen["name"]
                else None)
        draw_rows()
    frame.focus(box, find)
    # On the list's frame, which the server never redraws: a height set on the list
    # itself is wiped by the next redraw of its rows.
    box.on("show", lambda: ui.run_javascript(
        f"(() => {{ const el = document.getElementById('c{held_at.id}');"
        " if (el) el.style.height = el.offsetHeight + 'px'; })()"))
    box.open()
    return await box


def _collection_row(one: dict[str, Any], held: bool, chosen: bool,
                    take: Callable[[], None]) -> None:
    """One collection to pick: its name, what kind it is and how many games it has, and a
    check where it holds every row already."""
    smart = (one.get("type") or "") == "filter"
    count = int(one.get("count") or 0)
    classes = "items-center gap-3 w-full no-wrap manager-source-row manager-source-row--entry"
    classes += " manager-source-row--held" if held else " manager-source-row--pick"
    if chosen:
        classes += " manager-source-row--chosen"
    row = ui.row().classes(classes)
    with row:
        if smart:
            ui.icon(verbs.SMART).classes("manager-menu-mark")
        else:
            ui.element("span").classes("manager-menu-mark")
        with ui.column().classes("gap-0 grow min-w-0"):
            ui.label(str(one.get("name") or "")).classes("manager-source-name")
            ui.label(t("manager.workbench.smart_games" if smart
                       else "manager.workbench.hand_picked", count=count)) \
                .classes("manager-help")
        if held:
            ui.icon(verbs.CHOOSE).classes("manager-menu-check shrink-0")
    if not held:
        row.on("click", take)


async def new_with(offer: Offer) -> None:
    """A new collection holding `offer`'s rows, made in one step."""
    async def made(name: str) -> None:
        await add(offer.library, name, offer.rows, what=offer.what, then=offer.then)

    ask_new(offer.library, made, announce=False)


def ask_new(library: Any, made: Callable[[str], Awaitable[Any]], *,
            announce: bool = True) -> None:
    """A name. Nothing else.

    The kind is not a question at creation: it is decided by what the collection ends up
    holding, and changed in the panel where the games and the rule both are. Asking up
    front would make it a mode. `announce` says Created, for a caller that says nothing
    else about it.
    """
    held: dict[str, Any] = {}

    @on_page
    async def keep() -> None:
        name = held["name"]
        wanted = (name.value or "").strip()
        if not wanted:
            name.props["error"] = True
            name.props["error-message"] = t("said.needs_name")
            return
        dialog.close()
        try:
            created = await offload.io(library.create_collection, wanted, None)
        except Exception as exc:  # noqa: BLE001 - the reason belongs on screen
            ui.notify(t("said.action_failed"), caption=why(exc), type="negative")
            return
        if announce:
            ui.notify(t("manager.collections.created", name=wanted), type="positive")
        await made(str(created.get("name") or wanted))

    with frame.opened(t("manager.collections.new_collection"), ignore_outside_click=True) as dialog:
        panel.facts(ui, [(t("word.name"), lambda: held.update(name=frame.field()))])
        with frame.footer():
            frame.cancel(dialog.close)
            go = frame.answer(t("manager.collections.create"), keep, icon=verbs.CREATE)
    frame.focus(dialog, held["name"])
    frame.enter_presses(go)
    dialog.open()
