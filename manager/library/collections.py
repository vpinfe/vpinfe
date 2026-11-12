"""The collections grid: one row per collection, with the workbench beside it.

A collection is a subject like any other here, so this is a grid and not a pair of
cards. What one *is* - a name, how it is ordered, how many games - is what the columns
carry; what it *holds* is the workbench's answer, because membership is a list and a
list does not fit in a cell.
"""

from __future__ import annotations

import inspect
import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from functools import partial
from typing import Any

from nicegui import run, ui

from common.failures import why
from common.games.collection_store import (
    BUILTIN_ALL,
    DIRECTION_WORDS,
    MANUAL_ORDER,
    SORT_LABELS,
)
from common.i18n import t
from manager.extensions import ext_lists
from manager.kit import (
    confirm,
    grid,
    offload,
    panel,
    rename,
    states,
    thumbnails,
    verbs,
    views,
    walks,
)
from manager.kit.on_page import on_page
from manager.library import collection_adds
from manager.library.games import view_control

logger = logging.getLogger("vpinfe.manager.library.collections")

SCOPE = "manager.collections"
GLYPH = "collections_bookmark"

# The wire's kind, and what a person reads for it.
KIND_LABELS = {"manual": "manager.collections.hand_picked",
               "filter": "manager.collections.smart"}
_KIND_WORDS = {kind: t(key) for kind, key in KIND_LABELS.items()}

# A number filters as a number: greater-than, less-than, between. AG Grid's default
# filter is the text one, which offers "contains" over a count - and `agNumberColumnFilter`
# is community, unlike the set filter this project cannot use.
_NUMERIC: dict[str, Any] = {"type": "numericColumn",
                            "filter": "agNumberColumnFilter"}

_ESCAPE = ("const esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;')"
           ".replace(/\"/g, '&quot;');")

_NAME = (
    "params => {" + _ESCAPE +
    f" const tip = esc({json.dumps(t('manager.collections.opens_on.help'))});"
    f" const off = esc({json.dumps(t('manager.collections.out_menu.help'))});"
    f" const unsaved = esc({json.dumps(t('word.not_saved'))});"
    f" const why = esc({json.dumps(t('manager.collections.not_saved.help'))});"
    " const d = params.data || {};"
    " let said = esc(params.value == null ? '' : params.value);"
    " if (d.opens_on) said += ' <i class=\"material-icons manager-cell-mark"
    " manager-cell-mark--chosen\" title=\"' + tip + '\">" + verbs.OPENS_ON + "</i>';"
    " if (d.in_frontend === false) said += ' <i class=\"material-icons manager-cell-mark\""
    " title=\"' + off + '\">" + verbs.HIDE + "</i>';"
    " if (d.unsaved) said += ' <span class=\"manager-member-chip manager-cell-chip manager-tier"
    " manager-tier--warn\" title=\"' + why + '\">' + unsaved + '</span>';"
    f" const art = d['{thumbnails.FIELD}'] ?? null;"
    " if (art == null) return said;"
    " const lines = '<span class=\"manager-cell-named\">' + said + '</span>';"
    f" return {thumbnails.frame_js(GLYPH)}; }}"
)

_KIND = (
    "params => {" + _ESCAPE +
    " const said = esc(params.valueFormatted ?? params.value ?? '');"
    " return params.value === 'filter' ? '<i class=\"material-icons manager-cell-mark\">"
    + verbs.SMART + "</i> ' + said : said; }"
)

# The cell's value stays the count, which is what the column sorts and filters on.
_GAMES = (
    "params => {" + _ESCAPE +
    " const d = params.data || {};"
    " const said = esc(d.games_said || (params.value ?? ''));"
    " const chip = text => text ? ' <span class=\"manager-member-chip manager-cell-chip"
    " manager-tier manager-tier--warn\">' + esc(text) + '</span>' : '';"
    " return said + chip(d.missing_said) + chip(d.hidden_said); }"
)

MENU_IN, ATTENTION_NEEDED = "in", "needed"

IN_MENU = states.Table(
    covers=t("manager.collections.in_menu.help"),
    states=(states.State(MENU_IN, t("manager.collections.in_menu"),
                         t("manager.collections.in_menu.meaning"), glyph=states.TICK),),
    blank=states.State("", t("manager.collections.out_menu"),
                       t("manager.collections.out_menu.meaning")))

ATTENTION = states.Table(
    covers=t("manager.collections.needs_attention.help"),
    states=(states.State(ATTENTION_NEEDED, t("manager.collections.needs_attention"),
                         t("manager.collections.attention.meaning"), tone=states.WARN),),
    blank=states.State("", t("manager.collections.in_order"),
                       t("manager.collections.in_order.meaning")))

COLUMNS = [
    grid.identifier("name", t("word.name"), 240, pinned="left",
                    help=t("manager.collections.name.help"),
                    **{":rowDrag": "params => !params.data.pinned", ":cellRenderer": _NAME}),
    grid.column("kind", t("word.kind"), 120, help=t("manager.collections.kind.help"),
                **{**grid.choice_filter([{"value": kind, "label": word}
                                         for kind, word in _KIND_WORDS.items()]),
                   ":cellRenderer": _KIND,
                   ":comparator": f"(a, b) => {{ const said = {json.dumps(_KIND_WORDS)};"
                                  " return String(said[a] ?? a)"
                                  ".localeCompare(String(said[b] ?? b)); }"}),
    # Right-aligned with the other number rather than left with the words: a count is
    # read against the counts above and below it.
    grid.column("count", t("manager.collections.games"), 170,
                help=t("manager.collections.games.help"),
                **{**_NUMERIC, ":cellRenderer": _GAMES}),
    grid.column("added", t("manager.collections.manually_added"), **_NUMERIC,
                help=grid.NO_HELP),
    grid.column("matched", t("manager.collections.rule_matched"), **_NUMERIC,
                help=grid.NO_HELP),
    grid.column("excluded", t("manager.collections.manually_removed"), **_NUMERIC,
                help=grid.NO_HELP),
    grid.column("order", t("manager.collections.order"), 200,
                help=grid.NO_HELP),
    grid.column("limit", t("manager.collections.limit"),
                help=grid.NO_HELP,
                **{**_NUMERIC, ":valueFormatter": "params => params.value || (params.data && "
                   f"params.data.pinned ? '' : {json.dumps(t('word.all'))})"}),
    grid.column("missing", t("manager.collections.missing"), **_NUMERIC,
                help=t("manager.collections.missing.help")),
    grid.column("hidden", t("word.hidden"), **_NUMERIC,
                help=t("manager.collections.hidden.help")),
    IN_MENU.column("in_menu", t("manager.collections.in_menu"), 110),
    ATTENTION.column("attention", t("manager.collections.needs_attention"), 150),
]

# Keeps the All Games row first however the grid is sorted.
_KEEP_FIRST = """params => {
  const at = params.nodes.findIndex(node => node.data && node.data.pinned);
  if (at > 0) params.nodes.unshift(...params.nodes.splice(at, 1));
}"""

_ARRANGEMENT = """(() => {
  const api = getElement(%d).api;
  const shown = [];
  api.forEachNodeAfterFilterAndSort(node => shown.push(node.data.id));
  const by = api.getColumnState().filter(c => c.sort)
    .sort((a, b) => (a.sortIndex ?? 0) - (b.sortIndex ?? 0))[0];
  return {shown: shown, sorted: by ? by.colId : ''};
})()"""

# Focusing the row is what opens its panel. Waits for the row, because the grid takes
# new rowData after this runs; false where the row is not displayed.
_FOCUS_ROW = """new Promise((done) => {
  const api = getElement(%d).api;
  let tries = 0;
  const look = () => {
    const node = api && api.getRowNode(%s);
    if (node && node.rowIndex !== null) {
      api.ensureNodeVisible(node);
      api.setFocusedCell(node.rowIndex, 'name');
      return done(true);
    }
    if (node || ++tries >= 40) return done(false);
    setTimeout(look, 25);
  };
  look();
})"""

COLLECTION_VIEWS: dict[str, list[str] | views.Preset] = {
    "manager.view.everything": views.Preset(
        columns=("name", "kind", "count", "order"),
        help=t("manager.view.collections_everything.help")),
    "manager.collections.needs_attention": views.Preset(
        columns=("name", "kind", "count", "added", "matched", "excluded", "missing", "hidden"),
        filters={"attention": {"values": [ATTENTION_NEEDED]}},
        help=t("manager.view.collections_needs_attention.help")),
}


def rows(collections: list[dict[str, Any]], opens_on: str = "",
         unsaved: set[str] | frozenset[str] = frozenset(),
         art: bool = False) -> list[dict[str, Any]]:
    """One row per collection, in the words the grid shows.

    `count` is what the collection resolves to, which is its size. The stored
    membership is a different number and lives in the panel. `opens_on` names the
    collection the cabinet opens on, and `unsaved` the ones with rules not yet saved.
    `art` carries each one's picture, while the lists draw art.
    """
    built = []
    for row in collections:
        count = int(row.get("count") or 0)
        whole = int(row.get("before_limit") or 0)
        missing = int(row.get("missing") or 0)
        hidden = int(row.get("hidden") or 0)
        excluded = int(row.get("excluded") or 0)
        built.append({
            "id": row.get("name") or "",
            "name": row.get("name") or "",
            "kind": str(row.get("type") or ""),
            **({thumbnails.FIELD: thumbnails.collection(row)} if art else {}),
            # Zero is an answer here, not an absence: this is what the collection
            # resolves to, and an empty collection resolves to none.
            "count": count,
            "games_said": (t("manager.collections.count_of", count=count, whole=whole)
                           if whole > count else str(count)),
            "missing_said": (t("manager.collections.count_missing", count=missing)
                             if missing else ""),
            "hidden_said": (t("manager.collections.count_hidden", count=hidden)
                            if hidden else ""),
            "added": int(row.get("added") or 0),
            "matched": int(row.get("matched") or 0),
            "excluded": excluded,
            "missing": missing,
            "hidden": hidden,
            "attention": ATTENTION_NEEDED if missing or hidden or excluded else "",
            "opens_on": bool(opens_on) and row.get("name") == opens_on,
            "in_frontend": row.get("in_frontend") is not False,
            "in_menu": MENU_IN if row.get("in_frontend") is not False else "",
            "unsaved": row.get("name") in unsaved,
            "order": _order_line(row),
            "limit": row.get("limit") or None,
            # Kept whole on the row so the workbench does not refetch what the grid
            # already read.
            "_raw": row,
        })
    return built


def all_games_row(count: int, opens_on: str = "") -> dict[str, Any]:
    """The whole library as the first row: not a collection anyone stores, so it has no
    members, no rules and no order to edit. Its panel is the one place to say whether the
    frontend opens on it."""
    return {"id": BUILTIN_ALL, "name": t("manager.collections.all_games"), "kind": "",
            "count": count, "games_said": str(count), "missing_said": "", "hidden_said": "",
            "added": 0, "matched": 0, "excluded": 0, "missing": 0, "hidden": 0,
            "attention": "", "opens_on": not opens_on, "in_frontend": True, "in_menu": MENU_IN,
            "unsaved": False, "order": "", "limit": None, "pinned": True, "_raw": {},
            grid.NO_DROP: True}


def _order_line(row: dict[str, Any]) -> str:
    """How this collection is ordered, in one phrase.

    `manual` is the stored member array and says so - it is not one of the fields a
    collection can be sorted by, which is why SORT_LABELS does not carry it.
    """
    by = row.get("order_by") or ""
    if by == MANUAL_ORDER:
        return t("order.by.manual")
    if not by:
        return ""
    if row.get("ranking"):
        return ext_lists.ranked_label(row["ranking"])
    field = t(SORT_LABELS[by]) if by in SORT_LABELS else by
    way = DIRECTION_WORDS.get(by, {}).get(row.get("direction") or "")
    return t("manager.collections.ordered", by=field, direction=t(way)) if way else field


def build(collections: list[dict[str, Any]], library: Any,
          on_select: Callable[[dict | None], Any],
          state: dict[str, Any] | None = None,
          rerender: Callable[[], None] | None = None) -> None:
    state = state if state is not None else {}
    look = library.thumbnail_look()
    def with_all_games(real: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [all_games_row(len(library.games), library.opens_on()), *real]

    built = with_all_games(rows(collections, library.opens_on(),
                                state.get("unsaved_rules") or set(), look is not None))

    def collections_only() -> list[dict[str, Any]]:
        """`built` without the All Games row: what counts, moves and is selected."""
        return [row for row in built if not row.get("pinned")]

    columns = grid.with_art(COLUMNS, look)
    fields = [definition["field"] for definition in columns]

    arrangement: dict[str, Any] = {"shown": [row["id"] for row in collections_only()],
                                   "sorted": ""}
    hint: dict[str, Any] = {}

    def annotate() -> None:
        hint["label"] = ui.label().classes("text-xs manager-label shrink-0 whitespace-nowrap")
        hint["label"].set_visibility(False)

    with ui.row().classes("w-full items-center gap-2 px-3 py-2 mb-2 shrink-0 "
                                  "manager-panel manager-grid-bar"):
        bar = panel.grid_bar()
        wire_views, _picker, showing, describe = view_control(
            library, SCOPE, COLLECTION_VIEWS, fields, columns, bar=bar,
            annotate=annotate, art_in_lists=True)
        describe()
        with bar.top, panel.bar_end():
            search = panel.search(t("manager.collections.search_collections"))
        with bar.bottom, panel.bar_end():
            count = ui.label(t("manager.collections.collections",
                               count=len(collections_only()))).classes("text-xs manager-label")
            panel.add_action([(t("manager.collections.new_collection"),
                               lambda: collection_adds.ask_new(library, created))],
                              empty=not collections_only())
            show_selection = panel.selection_menu(
                t("manager.collections.collections_menu"), lambda: grid.selection(table),
                lambda chosen: acts(library, state, [one["_raw"] for one in chosen], redrawn,
                                    moves=moves(chosen[0]["name"]) if chosen else ()),
                lambda: table.run_grid_method("deselectAll"))

    by_id = {row["id"]: row for row in built}
    grid.on_row_focus(SCOPE,
                      lambda event: on_select(by_id.get(grid.focused_row(event))))
    picked: list[dict[str, Any]] = []

    shown: dict[str, int] = {"rows": len(collections_only())}

    def said() -> str:
        if picked:
            return grid.selection_said(table, len(picked), t(
                "manager.collections.selected", picked=len(picked), shown=shown["rows"]))
        total = len(collections_only())
        if shown["rows"] != total:
            return t("manager.collections.collections_of", shown=shown["rows"],
                     count=total)
        return t("manager.collections.collections", count=total)

    def on_selected(rows_selected: list[dict[str, Any]]) -> None:
        picked[:] = rows_selected
        show_selection(bool(rows_selected))
        count.text = said()

    def on_context(row: dict | None) -> None:
        if not row or row["id"] == BUILTIN_ALL:
            menu.clear()
            return
        on = grid.menu_rows(table, row)
        panel.verb_menu(
            menu, row["name"] if len(on) == 1
            else t("manager.collections.collections", count=len(on)),
            panel.for_selection(
                acts(library, state, [one["_raw"] for one in on], redrawn,
                     moves=moves(row["name"])), len(on)))

    async def on_header_context(col_id: str | None) -> None:
        await grid.header_menu(menu, table, columns, col_id)

    with ui.element("div").classes("w-full grow min-h-0 flex flex-col"):
        table: ui.aggrid = grid.build(columns, built, SCOPE, on_selected,
                                      on_context, on_header_context, view_of=showing,
                                      rows_without_a_menu=[BUILTIN_ALL],
                                      rows_not_selectable=[BUILTIN_ALL])
        menu = ui.context_menu()
    table.options["rowDragManaged"] = True
    table.options[":postSortRows"] = _KEEP_FIRST
    table.options[":rowDragText"] = "params => params.rowNode.data.name"

    async def redrawn(then: str | None) -> None:
        if then:
            await reread(then)
        elif rerender is not None:
            rerender()

    def moves(name: str) -> list[panel.Verb]:
        out = []
        for label, where in ((t("manager.collections.move_up"), "up"),
                             (t("manager.collections.move_down"), "down"),
                             (t("manager.collections.move_first"), "top")):
            order = moved(name, where)
            out.append(panel.Verb(
                label, partial(arrange, order, name) if order else None,
                hint=sorted_said() if not order and arrangement["sorted"] else "",
                in_panel=False, one=True))
        return out

    def sorted_said() -> str:
        by = arrangement["sorted"]
        header = next((d.get("headerName") for d in columns if d.get("field") == by), by)
        return t("manager.collections.sort_lock", column=header)

    def moved(name: str, where: str) -> list[str] | None:
        """The whole order with `name` moved, or None where it cannot go that way."""
        shown = arrangement["shown"]
        if arrangement["sorted"] or name not in shown:
            return None
        order = [row["id"] for row in collections_only()]
        at = shown.index(name)
        if where == "top":
            if order[0] == name:
                return None
            order.remove(name)
            order.insert(0, name)
        elif where == "up":
            if at == 0:
                return None
            order.remove(name)
            order.insert(order.index(shown[at - 1]), name)
        else:
            if at == len(shown) - 1:
                return None
            order.remove(name)
            order.insert(order.index(shown[at + 1]) + 1, name)
        return order

    @on_page
    async def arrange(order: list[str], focus: str = "") -> None:
        try:
            await run.io_bound(library.arrange_collections, order)
        except Exception as exc:
            ui.notify(t("said.action_failed"), caption=why(exc), type="negative")
        await reread(focus)

    @on_page
    async def dragged() -> None:
        drawn = await ui.run_javascript(_ARRANGEMENT % table.id)
        await arrange([one for one in (drawn or {}).get("shown") or [] if one != BUILTIN_ALL])

    async def counted() -> None:
        drawn = await ui.run_javascript(_ARRANGEMENT % table.id) or {}
        arrangement["shown"] = [one for one in drawn.get("shown") or [] if one != BUILTIN_ALL]
        arrangement["sorted"] = str(drawn.get("sorted") or "")
        shown["rows"] = len(arrangement["shown"])
        count.text = said()
        hint["label"].text = sorted_said() if arrangement["sorted"] else ""
        hint["label"].set_visibility(bool(arrangement["sorted"]))

    table.on("modelUpdated", counted)
    table.on("rowDragEnd", dragged, args=["type"])
    wire_views(table)
    search.on_value_change(
        lambda: table.run_grid_method("setGridOption", "quickFilterText",
                                      search.value or ""))

    @on_page
    async def reread(focus: str = "") -> None:
        built[:] = with_all_games(rows(
            await offload.io(library.load_collections), library.opens_on(),
            state.get("unsaved_rules") or set(), look is not None))
        by_id.clear()
        by_id.update({row["id"]: row for row in built})
        table.run_grid_method("setGridOption", "rowData", built)
        grid.redraw(table)
        count.text = said()
        if not focus:
            return
        try:
            shown = await ui.run_javascript(_FOCUS_ROW % (table.id, json.dumps(focus)),
                                            timeout=2.0)
        except Exception:  # noqa: BLE001 - the panel still opens below
            shown = False
        if not shown:
            opened = on_select(by_id.get(focus))
            if inspect.isawaitable(opened):
                await opened

    @on_page
    async def settled() -> None:
        """The grid put right after a write that left the panel as it is. A changed row is
        updated where it stands, so the selection, the scroll and the open panel stay; a
        collection added or gone is the whole grid's to read again."""
        fresh = with_all_games(rows(
            await offload.io(library.load_collections), library.opens_on(),
            state.get("unsaved_rules") or set(), look is not None))
        if {row["id"] for row in fresh} != set(by_id):
            await reread()
            return
        changed = [row for row in fresh if by_id.get(row["id"]) != row]
        if changed:
            grid.transact(table, built, {"update": changed}, by_id)
        count.text = said()

    async def created(name: str) -> None:
        await reread(name)
        if callable(open_pane := state.get("open_pane")):
            open_pane()

    state["refresh_collections"] = reread
    state["settle_collections"] = settled

    def mark_unsaved(names: set[str]) -> None:
        changed = [{**row, "unsaved": row["id"] in names} for row in built
                   if row["unsaved"] != (row["id"] in names)]
        if changed:
            grid.transact(table, built, {"update": changed}, by_id)

    state["mark_unsaved"] = mark_unsaved


async def renamed_to(library: Any, state: dict[str, Any], old: str, wanted: str) -> str:
    """Rename `old`, carrying everything the manager keeps under its name, and answer the
    name it was given."""
    made = await offload.io(library.patch_collection, old, {"name": wanted})
    new = str(made.get("name") or wanted.strip())
    collection_adds.renamed(old, new)
    if state.get("collection") == old:
        state["collection"] = new
    drafts = state.setdefault("collection_drafts", {})
    if old in drafts:
        drafts[new] = drafts.pop(old)
    return new


def acts(library: Any, state: dict[str, Any], rows_on: list[dict[str, Any]],
         after: Callable[[str | None], Awaitable[None]], *,
         moves: Sequence[panel.Verb] = ()) -> list[panel.Verb]:
    """What can be done to the collections `rows_on`, as the library holds them.

    `after` is given the collection to show next, or None when there is no one to follow:
    the one has gone, or several were acted on. `moves` are the grid's own, which need its
    order.
    """
    names = [str(one.get("name") or "") for one in rows_on]
    several = len(names) > 1
    drafts = state.get("collection_drafts", {})
    smart = [one for one in rows_on if one.get("type") == "filter"
             and "rules" not in drafts.get(str(one.get("name")), {})]

    @on_page
    async def rename_them() -> None:
        renamed: list[str] = []

        async def ask(row: dict[str, Any], place: str) -> Any:
            return await rename.ask(
                str(row["name"]), title=t("manager.collections.rename_title"),
                label=t("word.name"), place=place if several else "", walking=several)

        async def write(row: dict[str, Any], wanted: str) -> None:
            renamed.append(await renamed_to(library, state, str(row["name"]), wanted))

        if await walks.each(rows_on, ask, write, t("said.action_failed")):
            ui.notify(t("manager.collections.renamed", name=renamed[0]) if not several
                      else t("manager.collections.renamed_several", count=len(renamed)),
                      type="positive")
            await after(renamed[0] if not several else None)

    @on_page
    async def duplicate() -> None:
        copies: list[str] = []
        try:
            taken = {str(one.get("name") or "")
                     for one in await offload.io(library.load_collections)}
            for name in names:
                made = await offload.io(library.copy_collection, _copy_name(name, taken), name)
                copies.append(str(made.get("name") or ""))
                taken.add(copies[-1])
        except Exception as exc:  # noqa: BLE001
            ui.notify(t("said.action_failed"), caption=why(exc), type="negative")
        if not copies:
            return
        ui.notify(t("manager.collections.created", name=copies[0]) if not several
                  else t("manager.collections.duplicated", count=len(copies)),
                  type="positive")
        await after(copies[0] if not several else None)

    @on_page
    async def delete() -> None:
        # Asked, because a manual collection is somebody's hand-picked list and there
        # is no undo behind this.
        if several:
            # Eight, then a count: the list is here to say which ones, and a hundred names
            # is a dialog nobody reads to the end of.
            shown = names[:8] + ([t("said.and_more", count=len(names) - 8)]
                                 if len(names) > 8 else [])
            asked = await confirm.ask(
                t("manager.collections.delete_collections", count=len(names)),
                detail=await what_deleting_leaves(library, names), lines=shown)
        else:
            asked = await confirm.ask(t("manager.collections.delete", name=names[0]),
                                      detail=await what_deleting_leaves(library, names))
        if not asked:
            return
        gone: list[str] = []
        try:
            for name in names:
                await run.io_bound(library.delete_collection, name)
                gone.append(name)
        except Exception as exc:  # noqa: BLE001
            ui.notify(t("said.action_failed"), caption=why(exc), type="negative")
        if not gone:
            return
        ui.notify(t("manager.collections.deleted", name=gone[0]) if not several
                  else t("manager.collections.deleted_several", count=len(gone)),
                  type="positive")
        for name in gone:
            state.setdefault("collection_drafts", {}).pop(name, None)
        if state.get("collection") in gone:
            state["collection"] = None
        await after(None)

    @on_page
    async def convert() -> None:
        kept = [str(one.get("name") or "") for one in smart]
        if len(kept) == 1:
            asked = await confirm.ask(
                t("manager.workbench.convert_question", name=kept[0]),
                detail=t("manager.workbench.convert_detail", count=int(smart[0].get("count") or 0)),
                confirm=t("manager.workbench.convert"), icon=verbs.KEEP, danger=False)
        else:
            asked = await confirm.ask(
                t("manager.workbench.convert_several", count=len(kept)),
                detail=t("manager.workbench.convert_several_detail"),
                lines=kept[:8] + ([t("said.and_more", count=len(kept) - 8)]
                                  if len(kept) > 8 else []),
                confirm=t("manager.workbench.convert"), icon=verbs.KEEP, danger=False)
        if asked:
            await keep_result(library, state, kept, after)

    offered = [panel.Verb(t("manager.tageditor.rename"), rename_them, in_panel=False),
               panel.Verb(t("manager.workbench.duplicate"), duplicate)]
    if smart:
        offered.append(panel.Verb(t("manager.workbench.convert_static"), convert))
    return [*offered, *moves, panel.Verb(t("word.delete"), delete, danger=True)]


@on_page
async def keep_result(library: Any, state: dict[str, Any], names: list[str],
                      after: Callable[[str | None], Awaitable[None]]) -> None:
    """Fix smart collections' members to the games each shows now, and stop updating them."""
    kept: list[str] = []
    try:
        for name in names:
            await run.io_bound(library.keep_collection_result, name)
            kept.append(name)
            state.setdefault("collection_drafts", {}).get(name, {}).pop("rules", None)
    except Exception as exc:  # noqa: BLE001
        ui.notify(t("said.action_failed"), caption=why(exc), type="negative")
    if not kept:
        return
    ui.notify(t("manager.workbench.converted", name=kept[0]) if len(kept) == 1
              else t("manager.workbench.converted_several", count=len(kept)), type="positive")
    await after(kept[0] if len(kept) == 1 else None)


def _copy_name(name: str, taken: set[str]) -> str:
    wanted = t("manager.collections.copy_of", name=name)
    number = 2
    while wanted in taken:
        wanted = t("manager.collections.copy_of_n", name=name, n=number)
        number += 1
    return wanted


async def what_deleting_leaves(library: Any, names: list[str]) -> str:
    try:
        settings = await offload.io(library.config_values)
    except Exception:  # noqa: BLE001 - asked all the same, without the second sentence
        settings = {}
    opens_on = str((settings.get("behavior") or {}).get("startup_collection") or "")
    if opens_on.strip() in names:
        return t("manager.collections.delete_default")
    return t("manager.collections.games_stay")


def stored_views(library: Any) -> tuple[list[views.View], str]:
    """Exposed for tests: which views this grid has saved."""
    return views.stored(library, SCOPE)
