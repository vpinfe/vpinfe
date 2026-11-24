
"""The tag editor: one row per tag, and the way to fix two spellings of one word.

Entry does not fold case - the picker surfaces close matches and the user decides -
so `sci-fi` and `Sci-Fi` can both exist. This is where that gets cleaned up,
which is why merge is the headline here and rename is the same operation with one
source.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import partial
from typing import Any

from nicegui import ui

from common.failures import why
from common.games.tag_registry import derived_color
from common.i18n import t
from manager.kit import (
    confirm,
    grid,
    offload,
    panel,
    rename,
    renderers,
    states,
    tag_chips,
    verbs,
    views,
    walks,
)
from manager.kit import dialog as frame
from manager.kit.data import TAG_IN_USE, TAG_ONE, TAG_TWO, TAG_UNUSED, Library
from manager.kit.on_page import on_page

SUBJECT = "tag"
LABEL = t("manager.tageditor.tags")

SCOPE = "manager.tags"

USE = states.Table(
    covers="",
    states=(states.State(TAG_IN_USE, t("manager.tageditor.in_use"),
                         t("manager.tageditor.in_use.meaning"), glyph=states.TICK),
            states.State(TAG_UNUSED, t("manager.tageditor.unused"),
                         t("manager.tageditor.unused.meaning"), tone=states.QUIET)))

SPELLING = states.Table(
    covers=t("manager.tageditor.two_spellings.help"),
    states=(states.State(TAG_ONE, t("manager.tageditor.one_spelling"),
                         t("manager.tageditor.one_spelling.meaning"), glyph=states.TICK),
            states.State(TAG_TWO, t("manager.tageditor.two_spellings"),
                         t("manager.tageditor.two_spellings.meaning"), tone=states.WARN)))

COLUMNS = [
    grid.identifier("tag", t("manager.tageditor.tag"), 220, pinned="left",
                    help=grid.NO_HELP,
                    **renderers.drawable("chips", list="tag_list",
                                         looks=renderers.TAG_LOOKS)),
    grid.column("same", t("manager.tageditor.spelled_as"), 160,
                help=t("manager.tageditor.spelled_as.help")),
    grid.column("description", t("manager.workbench.description"), 280,
                help=grid.NO_HELP),
    grid.column("games", t("manager.tageditor.games"), type="numericColumn",
                help=grid.NO_HELP),
    grid.column("tables", t("manager.tageditor.tables"), type="numericColumn",
                help=t("manager.tageditor.tables.help")),
    grid.column("source", t("manager.tags.from"), 200, help=t("manager.tags.from.help")),
    USE.column("use", t("manager.tageditor.use"), 120),
    SPELLING.column("spelling", t("manager.tageditor.spelling"), 140),
]
_ALL = [one["field"] for one in COLUMNS]
_SHOWN = ("tag", "description", "games", "tables", "source")

VIEWS: dict[str, list[str] | views.Preset] = {
    "manager.view.everything": views.Preset(
        columns=_SHOWN, help=t("manager.view.tags_everything.help")),
    "manager.tageditor.unused": views.Preset(
        columns=_SHOWN, filters={"use": {"values": [TAG_UNUSED]}},
        help=t("manager.view.tags_unused.help")),
    "manager.tageditor.two_spellings": views.Preset(
        columns=("tag", "same", *_SHOWN[1:]), filters={"spelling": {"values": [TAG_TWO]}},
        sort=({"colId": "same", "sort": "asc", "sortIndex": 0},
              {"colId": "games", "sort": "desc", "sortIndex": 1}),
        help=t("manager.view.tags_two_spellings.help")),
}


def rows_by_key(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """The spellings that share a word, most-used first inside each group.

    Only groups with more than one member: a tag nobody has spelled twice is not
    something to act on, and listing it would bury the ones that are.
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if row.get("sources"):
            continue
        groups.setdefault(str(row.get("same") or ""), []).append(row)
    return [sorted(group, key=lambda r: (-int(r.get("games") or 0), str(r.get("tag"))))
            for group in groups.values() if len(group) > 1]


def without_menu_where_derived(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every row the grid is given goes through this: `acts` has nothing for a derived tag,
    and its row says so."""
    return [{**row, grid.NO_MENU: bool(row.get("sources"))} for row in rows]


def build(rows: list[dict[str, Any]], library: Any,
          on_select: Callable[[dict | None], Any],
          state: dict[str, Any],
          rerender: Callable[[], None] | None = None) -> None:
    """The editor. The duplicates lead, because they are what somebody came here for."""
    tag_chips.install(library.tag_looks())
    rows = without_menu_where_derived(rows)

    @on_page
    async def sweep(call: Callable[..., Any], *args: Any, said: str = "") -> None:
        try:
            changed = await offload.io(call, *args)
        except Exception as exc:
            ui.notify(t("said.action_failed"), caption=why(exc), type="negative")
            return
        ui.notify(t("manager.tageditor.game_changed", said=said, count=changed),
                  type="positive")
        if rerender is not None:
            rerender()

    @on_page
    async def merge(group: list[dict[str, Any]]) -> None:
        into = str(group[0].get("tag") or "")
        others = [str(r.get("tag")) for r in group[1:]]
        if not await confirm.ask(
                t("manager.tageditor.merge", into=(into)),
                detail=t("manager.tageditor.merge_detail"),
                lines=[t("manager.tageditor.tag_games", tag=r["tag"], count=r["games"])
                       for r in group[1:]],
                confirm=t("word.merge"), icon=verbs.MERGE):
            return
        await sweep(library.merge_tags, others + [into], into,
                said=t("manager.tageditor.merged", into=(into)))

    duplicates = rows_by_key(rows)
    if duplicates:
        with ui.element("div").classes("manager-card w-full mb-2"):
            ui.label(t("manager.tageditor.similar")).classes("manager-card-title")
            ui.label(t("manager.tageditor.merge_note")) \
                .classes("manager-help")
            for group in duplicates:
                with ui.row().classes("items-center gap-2 w-full no-wrap "
                                      "manager-member-row"):
                    ui.label(" · ".join(t("manager.tageditor.tag_used", tag=r["tag"],
                                          games=r["games"]) for r in group)) \
                        .classes("manager-member-name grow min-w-0 truncate")
                    ui.button(t("word.merge"),
                        icon=verbs.MERGE, on_click=lambda _, g=group: merge(g)) \
                        .props("flat dense no-caps size=sm") \
                        .classes("manager-action manager-action--inline")

    @on_page
    async def write_down() -> None:
        wanted = await _new_tag(library)
        if not wanted:
            return
        name, changes = wanted
        try:
            await offload.io(library.put_tag, name, changes)
        except Exception as exc:
            ui.notify(t("said.action_failed"), caption=why(exc), type="negative")
            return
        if rerender is not None:
            rerender()

    from manager.library.games import view_control

    with ui.row().classes("w-full items-center gap-2 px-3 py-2 mb-2 shrink-0 "
                          "manager-panel manager-grid-bar"):
        bar = panel.grid_bar()
        wire_views, _picker, showing, describe = view_control(
            library, SCOPE, VIEWS, _ALL, COLUMNS, bar=bar)
        describe()
        with bar.top, panel.bar_end():
            search = panel.search(t("manager.tageditor.search_tags"))
        with bar.bottom, panel.bar_end():
            count = ui.label(t("manager.tageditor.tags_counted", count=len(rows))) \
                .classes("text-xs manager-label")
            show_selection = panel.selection_menu(
                t("manager.tageditor.tags_menu"), lambda: grid.selection(table),
                lambda chosen: acts(library, chosen, redrawn),
                lambda: table.run_grid_method("deselectAll"))
            panel.add_action([(t("manager.tageditor.new_tag"), write_down)],
                             empty=not rows)

    if not rows:
        ui.label(t("manager.tageditor.empty")) \
            .classes("manager-help p-4")
        return
    by_id = {row["id"]: row for row in rows}

    picked: list[dict[str, Any]] = []

    async def redrawn(_next: str | None) -> None:
        if rerender is not None:
            rerender()

    def said() -> str:
        if picked:
            return grid.selection_said(table, len(picked), t(
                "manager.tageditor.selected", picked=len(picked), shown=shown["rows"]))
        if shown["rows"] != len(rows):
            return t("manager.tageditor.tags_of", shown=shown["rows"], count=len(rows))
        return t("manager.tageditor.tags_counted", count=len(rows))

    def on_selected(chosen: list[dict[str, Any]]) -> None:
        picked[:] = chosen
        show_selection(bool(chosen))
        count.text = said()

    def on_context(row: dict | None) -> None:
        if not row:
            menu.clear()
            return
        derived = library.derived_tags()
        on = [one for one in grid.menu_rows(table, row)
              if str(one.get("tag") or "") not in derived]
        panel.verb_menu(menu, str(row.get("tag") or "") if len(on) == 1
                        else t("manager.tageditor.tags_counted", count=len(on)),
                        panel.for_selection(acts(library, on, redrawn), len(on)))

    async def on_header_context(col_id: str | None) -> None:
        await grid.header_menu(menu, table, COLUMNS, col_id)

    shown = {"rows": len(rows)}
    with ui.element("div").classes("w-full grow min-h-0 flex flex-col"):
        table = grid.build(COLUMNS, rows, SCOPE, on_selected, on_context, on_header_context,
                           view_of=showing)
        menu = ui.context_menu()
    wire_views(table)

    async def counted() -> None:
        seen = await table.run_grid_method("getDisplayedRowCount")
        shown["rows"] = int(seen if isinstance(seen, int) else len(rows))
        count.text = said()

    table.on("modelUpdated", counted)
    grid.on_row_focus(SCOPE, lambda event: on_select(by_id.get(grid.focused_row(event))))
    search.on_value_change(
        lambda: table.run_grid_method("setGridOption", "quickFilterText",
                                      search.value or ""))

    async def refresh_rows() -> None:
        fresh = without_menu_where_derived(library.tag_rows())
        rows[:] = fresh
        by_id.clear()
        by_id.update({row["id"]: row for row in fresh})
        tag_chips.install(library.tag_looks())
        table.run_grid_method("setGridOption", "rowData", fresh)
        grid.redraw(table)

    state["refresh_tags"] = refresh_rows


async def _new_tag(library: Library) -> tuple[str, dict[str, str]] | None:
    """The panel's three fields, asked before anything carries the tag."""
    known = set(library.tag_looks())
    held: dict[str, Any] = {"color": ""}
    fields: dict[str, Any] = {}

    def draw_name() -> None:
        fields["name"] = frame.field()
        fields["name"].on_value_change(renamed)

    def renamed() -> None:
        refused("")
        draw_colors()

    def draw_said() -> None:
        fields["said"] = frame.field(lines=2)

    def draw_colors() -> None:
        box = fields.get("colors") or ui.element("div")
        fields["colors"] = box
        box.clear()
        with box:
            tag_chips.swatches(held["color"], derived_color(_named(fields)), pick)

    def pick(color: str) -> None:
        held["color"] = color
        draw_colors()

    def refused(why: str) -> None:
        fields["name"].props["error"] = bool(why)
        fields["name"].props["error-message"] = why

    def keep() -> None:
        name = _named(fields)
        if not name:
            refused(t("said.needs_name"))
        elif name in known:
            refused(t("manager.tageditor.duplicate"))
        else:
            box.submit((name, {"description": str(fields["said"].value or "").strip(),
                               "color": held["color"]}))

    with frame.opened(t("manager.tageditor.add_title"), ignore_outside_click=True) as box:
        panel.facts(ui, [(t("manager.tageditor.tag"), draw_name),
                         (t("manager.workbench.description"), draw_said),
                         (t("manager.tags.color"), draw_colors)])
        with frame.footer():
            frame.cancel(lambda: box.submit(None))
            add = frame.answer(t("word.add"), keep, icon=verbs.CREATE)
    frame.focus(box, fields["name"])
    frame.enter_presses(add)
    return await box


def acts(library: Any, rows_on: list[dict[str, Any]],
         after: Callable[[str | None], Awaitable[None]]) -> list[panel.Verb]:
    """What can be done to the tags `rows_on`. A derived tag is nobody's to change, so a row
    for one is left out, and where that is every row there is nothing to offer. `after` is
    given the tag to show next, or None when there is no one to follow."""
    derived = library.derived_tags()
    mine = [one for one in rows_on if str(one.get("tag") or "") not in derived]
    if not mine:
        return []
    tags = [str(one.get("tag") or "") for one in mine]
    several = len(tags) > 1
    games = {str(one.get("tag") or ""): int(one.get("games") or 0) for one in mine}
    others = sorted((one for one in library.tag_looks() if one not in tags and one not in derived),
                    key=str.casefold)

    def counted(tag: str) -> str:
        return t("manager.tageditor.tag_games", tag=tag, count=games[tag])

    @on_page
    async def rename_them() -> None:
        renamed: list[str] = []

        async def ask(row: dict[str, Any], place: str) -> Any:
            said = await rename.ask(
                str(row["tag"]), title=t("manager.tageditor.rename_tag"),
                label=t("manager.tageditor.tag"), hint=t("manager.tageditor.rename_hint"),
                place=place if several else "", walking=several)
            if (said in (walks.SKIPPED, walks.STOPPED) or said not in library.tag_looks()
                    or await confirm.ask(t("manager.tageditor.merge", into=said),
                                         detail=t("manager.tageditor.merge_detail"),
                                         confirm=t("word.merge"), icon=verbs.MERGE)):
                return said
            return walks.SKIPPED

        async def write(row: dict[str, Any], said: str) -> None:
            await offload.io(library.merge_tags, [str(row["tag"])], said)
            renamed.append(said)

        if await walks.each(mine, ask, write, t("said.action_failed")):
            ui.notify(t("manager.tageditor.renamed", said=renamed[0]) if not several
                      else t("manager.tageditor.renamed_several", count=len(renamed)),
                      type="positive")
            await after(renamed[0] if not several else None)

    @on_page
    async def merge(into: str) -> None:
        if not await confirm.ask(t("manager.tageditor.merge", into=into),
                                 detail=t("manager.tageditor.merge_detail"),
                                 lines=[counted(tag) for tag in tags],
                                 confirm=t("word.merge"), icon=verbs.MERGE):
            return
        try:
            await offload.io(library.merge_tags, tags, into)
        except Exception as exc:  # noqa: BLE001
            ui.notify(t("said.action_failed"), caption=why(exc), type="negative")
            return
        ui.notify(t("manager.tageditor.merged", into=into), type="positive")
        await after(into)

    @on_page
    async def delete() -> None:
        if several:
            asked = await confirm.ask(
                t("manager.tageditor.delete_several", count=len(tags)),
                detail=t("manager.tageditor.delete_several_detail"),
                lines=[counted(tag) for tag in tags], confirm=t("word.delete"),
                icon=verbs.DELETE)
        else:
            asked = await confirm.ask(
                t("manager.tageditor.delete", tag=tags[0]),
                detail=t("manager.tageditor.delete_detail", count=games[tags[0]]),
                confirm=t("word.delete"), icon=verbs.DELETE)
        if not asked:
            return
        gone = 0
        try:
            for tag in tags:
                await offload.io(library.delete_tag, tag)
                gone += 1
        except Exception as exc:  # noqa: BLE001
            ui.notify(t("said.action_failed"), caption=why(exc), type="negative")
        if not gone:
            return
        ui.notify(t("manager.tageditor.deleted", tag=tags[0]) if not several
                  else t("manager.tageditor.deleted_several", count=gone), type="positive")
        await after(None)

    offered = [panel.Verb(t("manager.tageditor.rename"), rename_them, in_panel=False)]
    if others:
        offered.append(panel.Verb(t("manager.tags.merge_into"), choices=tuple(
            (other, partial(merge, other)) for other in others)))
    offered.append(panel.Verb(t("word.delete"), delete, danger=True))
    return offered


def _named(fields: dict[str, Any]) -> str:
    return " ".join(str(fields["name"].value or "").split())
