"""Discovery's Marks grid: one row for each mark a person has set.

Reached from Home, never from a segment of its own. Stop Ignoring, Add to Wanted and Remove
from Wanted act on a selection of any mix of kinds.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from nicegui import ui

from common import icons
from common.i18n import t
from common.media.media_specs import media_label_map
from common.online import vps_find, vps_marked, vps_marks
from manager.kit import game_tables, panel, thumbnails, views, when
from manager.kit import grid as grid_module
from manager.kit.data import Library
from manager.kit.on_page import on_page
from manager.library import discovery_ask, discovery_marks

SCOPE = "discovery_marks"
# The name of this grid in the page's state. It has no segment of its own: Home leads to it.
TAB = "marks"

# The views, each under the catalog key of its name.
EVERYTHING = "manager.view.everything"
IGNORED_VIEW = "manager.discovery.mark.ignored"
WANTED_VIEW = "manager.discovery.mark.wanted"

_KIND_WORDS = {
    vps_marks.GAME: "manager.discovery.marks.kind.game",
    vps_marks.RELEASE: "manager.discovery.marks.kind.release",
    vps_marks.GAP: "manager.discovery.marks.kind.gap",
    vps_marks.VERSION: "manager.discovery.marks.kind.version",
    vps_marks.UPDATES: "manager.discovery.marks.kind.updates",
}
_SINCE_SAID: dict[str, Any] = {
    ":valueFormatter": "params => (params.data || {}).since_said || ''"}
_NEWEST = ({"colId": "since", "sort": "desc", "sortIndex": 0},)
_SHOWN = "getElement(%d).api.getDisplayedRowCount()"

_FIELDS = ("name", "kind", "detail", discovery_marks.FIELD, "since")
_SHORT = ("name", "kind", "detail", "since")

VIEWS: dict[str, list[str] | views.Preset] = {
    EVERYTHING: views.Preset(
        columns=_FIELDS, sort=_NEWEST, help=t("manager.discovery.marks.everything.help")),
    IGNORED_VIEW: views.Preset(
        columns=_SHORT, sort=_NEWEST,
        filters={discovery_marks.FIELD: {"values": [vps_marks.IGNORED]}},
        help=t("manager.discovery.marks.ignored.help")),
    WANTED_VIEW: views.Preset(
        columns=_SHORT, sort=_NEWEST,
        filters={discovery_marks.FIELD: {"values": [vps_marks.WANTED]}}),
}


def columns() -> list[dict[str, Any]]:
    return [
        grid_module.identifier(
            "name", t("word.game"), 260, pinned="left", subtitle="said",
            picture=thumbnails.FIELD, glyph=icons.GAMES,
            help=grid_module.NO_HELP,
            **{":getQuickFilterText":
               "params => [params.data.name, params.data.said, params.data.detail]"
               ".filter(Boolean).join(' ')"}),
        grid_module.column(
            "kind", t("word.kind"), 140, help=grid_module.NO_HELP,
            **grid_module.choice_filter([{"value": scope, "label": t(word)}
                                         for scope, word in _KIND_WORDS.items()])),
        grid_module.column("detail", t("manager.discovery.marks.detail"), 200, flex=1,
                           help=t("manager.discovery.marks.detail.help")),
        discovery_marks.column(),
        grid_module.column(
            "since", t("manager.discovery.marks.since"),
            help=grid_module.NO_HELP,
            **grid_module.window_filter([(t(word), days)
                                         for word, days in discovery_ask.SINCE_DAYS]),
            **_SINCE_SAID),
    ]


def kind_words(listed: Mapping[str, str]) -> dict[str, str]:
    """What each VPS kind and each media kind is called: the kinds `listed` names (VPS key to
    word), then the media kinds a tab only a source fills is named for."""
    return {**media_label_map(), **listed}


def detail_of(item: Mapping[str, Any], words: Mapping[str, str]) -> str:
    """Which release, kind or version a mark is on, as a line: the kind and version of a
    release with who made it, the kind of a gap, the picture a source replaced."""
    if item["scope"] == vps_marks.GAME:
        return ""
    word = words.get(item["media"] or item["listing"], item["media"] or item["listing"])
    if item["scope"] == vps_marks.GAP:
        return word
    line = " ".join(part for part in (word, item["version"]) if part)
    if item["scope"] == vps_marks.RELEASE and item["authors"]:
        line = " · ".join((line, ", ".join(item["authors"][:2])))
    return line


def called(item: Mapping[str, Any], words: Mapping[str, str]) -> str:
    """The mark's thing as a notice names it."""
    word = words.get(item["media"] or item["listing"], item["media"] or item["listing"])
    if item["scope"] == vps_marks.GAME:
        return str(item["game"])
    if item["scope"] == vps_marks.GAP:
        return t("manager.discovery.mark.gap_name", game=item["game"], kind=word)
    return discovery_marks.release_name(str(item["game"]), word, str(item["version"]))


def rows(items: Sequence[Mapping[str, Any]], words: Mapping[str, str]) -> list[dict[str, Any]]:
    """One grid row for each of `items` (`vps_marked.listed`'s answer). `words` is `kind_words`."""
    return [{
        "id": vps_marks.key(item["scope"], item["id"], item["detail"]),
        "mark_scope": item["scope"], "mark_id": item["id"], "mark_detail": item["detail"],
        "vps_id": item["vps_id"],
        "name": item["game"],
        "said": game_tables.made(dict(item)),
        thumbnails.FIELD: item["img_url"],
        "kind": item["scope"],
        "detail": detail_of(item, words),
        "called": called(item, words),
        discovery_marks.FIELD: item["state"],
        "since": item["since"],
        "held": bool(item.get("held")),
        "since_said": when.ago(item["since"]) if item["since"] else "",
    } for item in items]


def subjects(chosen: Sequence[Mapping[str, Any]]) -> list[discovery_marks.Subject]:
    return [discovery_marks.Subject(
        str(one["mark_scope"]), str(one["mark_id"]), str(one["called"]),
        str(one[discovery_marks.FIELD]), str(one["mark_detail"]), held=bool(one.get("held")))
        for one in chosen]


def summary(count: int) -> str:
    return t("manager.discovery.marks.summary", count=count)


async def fill(
        body: Any, *, library: Library, state: dict[str, Any], feed: Callable[..., None],
        found: Callable[[], Awaitable[Any]],
        words: Mapping[str, str], games: list[dict[str, Any]], people: list[dict[str, Any]],
        on_select: Callable[[Any], Any],
        open_page: Callable[[dict[str, str]], Awaitable[None]]) -> None:
    """Draw the grid into `body`. `found` answers the catalog as `GET /library/discovery`
    does, `words` is `kind_words`, and `games` and `people` are the search's. `open_page`
    opens the page of an author, maker or theme the search picked. `feed` is the tab row's
    box as the page was drawn, which `discovery_search.feeder` gives."""
    # Imported here: `games` imports modules that import this one.
    from manager.library.games import view_control

    shown: dict[str, Any] = {"rows": {}, "count": 0, "selected": 0}
    held: list[dict[str, Any]] = []

    def load(catalog: Mapping[str, Any]) -> None:
        built = rows(vps_marked.listed(dict(catalog), discovery_marks.read(library)), words)
        held[:] = built
        shown["rows"] = {row["id"]: row for row in built}
        shown["count"] = len(built)

    load(await found())
    grid_columns = grid_module.with_art(columns(), library.thumbnail_look())
    fields = list(_FIELDS)

    @on_page
    async def choose(group: str, one: dict[str, Any]) -> None:
        if group == vps_find.GAME:
            await on_select({"id": str(one["id"])})
        else:
            await open_page({"type": group, "name": str(one["name"])})

    with body:
        with ui.row().classes(
                "w-full items-center gap-2 px-3 py-2 mb-2 shrink-0 "
                "manager-panel manager-grid-bar manager-grid-bar--flow"):
            bar = panel.grid_bar()
            wire_views, _picker, showing, describe = view_control(
                library, SCOPE, VIEWS, fields, grid_columns, bar=bar, art_in_lists=True)
            describe()
            with bar.bottom, panel.bar_end():
                count = ui.label(summary(shown["count"])).classes("text-xs manager-label")
                show_selection = panel.selection_menu(
                    t("manager.discovery.marks.actions_selected"), lambda: picked(),
                    lambda chosen: acts(chosen), lambda: table.run_grid_method("deselectAll"))

        with ui.element("div") as nothing:
            panel.facts(ui, [panel.intro(t("manager.discovery.marks.empty"))])
        nothing.set_visibility(not held)

        def counted_text() -> str:
            if shown["selected"]:
                return grid_module.selection_said(
                    table, shown["selected"],
                    t("manager.discovery.selected", picked=shown["selected"],
                      shown=shown["count"]))
            return summary(shown["count"])

        def on_selected(chosen_rows: list[dict[str, Any]]) -> None:
            shown["selected"] = len(chosen_rows)
            show_selection(bool(chosen_rows))
            count.text = counted_text()

        def acts(chosen: list[dict[str, Any]]) -> list[panel.Verb]:
            return discovery_marks.listed_verbs(library, subjects(chosen), after)

        def fill_menu(row: dict | None) -> None:
            one = shown["rows"].get(str((row or {}).get("id") or ""))
            if one is None:
                menu.clear()
                return
            on = grid_module.menu_rows(table, one)
            panel.verb_menu(menu, one["called"] if len(on) == 1
                            else t("manager.discovery.marks.selected_n", count=len(on)),
                            panel.for_selection(acts(on), len(on)))

        async def on_header_context(col_id: str | None) -> None:
            await grid_module.header_menu(menu, table, grid_columns, col_id)

        with ui.element("div").classes("w-full grow min-h-0 flex flex-col"):
            table: ui.aggrid = grid_module.build(grid_columns, held, SCOPE,
                                                 on_select_rows=on_selected,
                                                 on_context=fill_menu,
                                                 on_header_context=on_header_context,
                                                 view_of=showing)
            menu: ui.context_menu = ui.context_menu()
        grid_module.on_row_focus(
            SCOPE, lambda event: on_select(
                {"id": str((shown["rows"].get(grid_module.focused_row(event)) or {})
                           .get("vps_id") or "")}) if grid_module.focused_row(event) in shown[
                               "rows"] else None)
        feed(
            people, games, choose,
            narrow=lambda text: table.run_grid_method("setGridOption", "quickFilterText", text))
        table.on("modelUpdated", lambda: _counted_in_view())
        wire_views(table)

    def picked() -> list[dict[str, Any]]:
        return [shown["rows"][str(r["id"])] for r in grid_module.selection(table)
                if str(r.get("id")) in shown["rows"]]

    async def _counted_in_view() -> None:
        shown["count"] = int(await ui.run_javascript(_SHOWN % table.id) or 0)
        count.text = counted_text()

    @on_page
    async def after() -> None:
        """The marks read again, the rows drawn from them, and the open pane put right."""
        await discovery_marks.refresh(library)
        load(await found())
        table.run_grid_method("setGridOption", "rowData", held)
        nothing.set_visibility(not held)
        count.text = counted_text()
        chosen = state.get("entry")
        if chosen and state.get("workbench"):
            await on_select({"id": chosen})

    state["redraw_discovery"] = discovery_marks.recounting(state, after)
