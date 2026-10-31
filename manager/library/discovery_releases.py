"""Discovery's Releases tab: one row for each release VPS lists.

Who made a release, when it was added, its format, features and whether it is a mod are
columns with their filters, like every other grid; the search, the saved views and the
pane are the Games tab's. A row stands for a release and a press on it opens its game's
pane.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from nicegui import ui

from common import icons
from common.i18n import t
from common.online import vps_find, vps_formats, vps_marks, vps_question
from manager.kit import grid as grid_module
from manager.kit import panel, thumbnails, views, when
from manager.kit.data import Library
from manager.kit.on_page import on_page
from manager.library import discovery_ask, discovery_marks, discovery_states

SCOPE = "discovery_releases"
# The name of this tab in the address and in the view a page sends it to.
TAB = "releases"

HAVE, MOD = discovery_states.HAVE, discovery_states.MOD
MISSING = discovery_states.MISSING
NOT_COMPARED = discovery_states.NOT_COMPARED

_ADDED_SAID: dict[str, Any] = {
    ":valueFormatter": "params => (params.data || {}).added_said || ''"}
_NEWEST =({"colId": "added", "sort": "desc", "sortIndex": 0},)
_SHOWN = "getElement(%d).api.getDisplayedRowCount()"


def _formats(release_rows: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    """The table formats the rows carry, most used first, and a choice for the releases
    that name none."""
    seen: dict[str, int] = {}
    for one in release_rows:
        seen[str(one["format"])] = seen.get(str(one["format"]), 0) + 1
    named = sorted((word for word in seen if word), key=lambda word: (-seen[word], word))
    choices = [{"value": word, "label": word} for word in named]
    if "" in seen:
        choices.append({"value": "", "label": t("manager.games.not_recorded")})
    return choices


def columns(kind_words: Mapping[str, str],
            release_rows: Sequence[Mapping[str, Any]] = ()) -> list[dict[str, Any]]:
    """The grid's columns; `kind_words` is the VPS kinds the library collects, to the word
    each is called, and `release_rows` the rows the format filter is made from."""
    return [
        grid_module.identifier(
            "name", t("word.game"), 260, pinned="left", subtitle="said",
            picture=thumbnails.FIELD, glyph=icons.GAMES,
            help=t("manager.discovery.releases.name.help"),
            **{":getQuickFilterText":
               "params => [params.data.name, params.data.said, params.data.find, "
               "params.data.authors, params.data.version].filter(Boolean).join(' ')"}),
        discovery_marks.column(),
        grid_module.column(
            "kind", t("word.kind"), help=t("manager.discovery.releases.kind.help"),
            **grid_module.choice_filter([{"value": listing, "label": t(word)}
                                         for listing, word in kind_words.items()])),
        grid_module.column("version", t("word.version"),
                           help=t("manager.discovery.releases.version.help")),
        grid_module.list_column(
            "authors", t("manager.discovery.ask.group.author"), 200,
            help=t("manager.discovery.releases.authors.help"), plain=True),
        grid_module.column(
            "added", t("manager.discovery.ask.added"),
            help=t("manager.discovery.releases.added.help"),
            **grid_module.window_filter([(t(word), days)
                                         for word, days in discovery_ask.SINCE_DAYS]),
            **_ADDED_SAID),
        discovery_states.held().column(
            "held", t("manager.view.have"), help=t("manager.discovery.releases.held.help")),
        grid_module.column("format", t("manager.discovery.ask.format_pick"),
                           help=t("manager.discovery.releases.format.help"),
                           **grid_module.choice_filter(_formats(release_rows))),
        grid_module.list_column(
            "features", t("manager.games.features"), 200,
            help=t("manager.discovery.releases.features.help"), plain=True),
        discovery_states.mod().column(
            "mod", t("manager.games.mod"), help=t("manager.discovery.releases.mod.help")),
        discovery_states.in_library().column(
            "in_library", t("manager.discovery.in_library"),
            help=t("manager.discovery.in_library.help")),
    ]


_FIELDS = ("name", discovery_marks.FIELD, "kind", "version", "authors", "added", "held",
           "format", "features", "mod", "in_library")

VIEWS: dict[str, list[str] | views.Preset] = {
    "manager.view.everything": views.Preset(
        columns=_FIELDS, sort=_NEWEST,
        help=t("manager.view.releases_everything.help")),
}


def _formats_fitting(wanted: Collection[str], filed: Collection[str] | None) -> list[str]:
    """The formats asked for, and where the releases' own are known the spellings and the
    empty one that fit them."""
    out = list(wanted)
    if filed is not None:
        out += [one for one in sorted(set(filed)) if one not in out
                and vps_formats.fits(one, wanted)]
    return out


def arrival_filters(question: Mapping[str, Any], *, counted: bool,
                    filed: Collection[str] | None = None) -> dict[str, Any]:
    """A question about releases (`vps_question`'s keys) as filters on the columns.

    Have and In Library are left out before the library has been counted: the columns read
    Not Compared Yet then, and a filter on them would empty the grid. `filed`, where it is
    known, is every format the releases are filed under: a format question then keeps the releases
    that fit it as `vps_formats.fits` reads it, so one filed under no format stays."""
    out: dict[str, Any] = {}
    if question.get("since"):
        out["added"] = {"days": int(question["since"])}
    if question.get("by"):
        out["authors"] = {"values": list(question["by"])}
    if question.get("kind"):
        out["kind"] = {"values": [question["kind"]]}
    if question.get("format"):
        out["format"] = {"values": _formats_fitting(question["format"], filed)}
    if question.get("mod"):
        out["mod"] = {"values": [MOD if question["mod"] == "mods" else ""]}
    if counted and question.get("held"):
        out["held"] = {"values": [HAVE if question["held"] == "have" else MISSING]}
    if counted and question.get("owned"):
        out["in_library"] = {"values": [question["owned"]]}
    return out


def rows(found: Mapping[str, Sequence[tuple[str, Mapping[str, Any]]]],
         games: Mapping[str, Mapping[str, Any]], people: Sequence[Mapping[str, Any]],
         *, counted: bool, marks: vps_marks.Marks | None = None) -> list[dict[str, Any]]:
    """One grid row for each release in `found` (`vps_question.games_found`'s answer).

    `games` is the Games tab's rows by id. A release is Have only once the library has been
    counted, since before that nothing is known to be held. A release wears Ignored when it
    or its game is ignored, and Wanted when it is."""
    marks = marks or vps_marks.Marks()
    names = [str(one["name"]) for one in people]
    now = datetime.now(UTC)
    said_on: dict[Any, str] = {}
    out = []
    for game_id, fitting in found.items():
        game = games.get(game_id)
        if game is None:
            continue
        find = " ".join(filter(None, [game.get("game_type"), *(game.get("themes") or ())]))
        for listing, release in fitting:
            added = str(release.get("added") or "")
            said = _said(added, now, said_on)
            own = marks.state(vps_marks.RELEASE, str(release["id"]))
            out.append({
                "id": f"{game_id}:{release['id']}",
                "release_id": str(release["id"]),
                "game_id": game_id,
                "name": game["name"],
                "said": game["said"],
                "find": find,
                thumbnails.FIELD: game.get(thumbnails.FIELD) or "",
                "kind": listing,
                "version": release.get("version") or "",
                "authors": [names[at] for at in release.get("by") or ()],
                "added": added,
                "added_said": said,
                "held": (NOT_COMPARED if not counted
                         else HAVE if release.get("held") else MISSING),
                "format": release.get("format") or "",
                "features": list(release.get("features") or ()),
                "mod": MOD if release.get("mod") else "",
                "in_library": game.get("in_library") or NOT_COMPARED,
                discovery_marks.OWN: own,
                discovery_marks.FIELD: discovery_marks.IGNORED if marks.release_ignored(
                    str(release["id"]), game_id) else own,
            })
    return out


def _said(added: str, now: datetime, said_on: dict[Any, str]) -> str:
    """How long ago `added` was. Past the recent window the words are the local date, so
    stamps on one date share a call: `when.ago` reads the settings on each, which across
    every release is seconds."""
    at = when.parsed(added)
    key = added if at is None or now - at < when.RECENT else at.astimezone().date()
    if key not in said_on:
        said_on[key] = when.ago(added, now) if added else ""
    return said_on[key]


def summary(count: int) -> str:
    return t("manager.discovery.releases.summary", count=count)


async def fill(
        body: Any, *, library: Library, state: dict[str, Any], feed: Callable[..., None],
        games: list[dict[str, Any]], people: list[dict[str, Any]], releases: dict[str, Any],
        kind_words: dict[str, str],
        counted: bool, compared: str, comparing: Callable[[], object],
        on_select: Callable[[Any], Any], reread: Callable[[], Awaitable[None]],
        open_page: Callable[[dict[str, str]], Awaitable[None]],
        acts: Callable[[list[dict[str, Any]], Callable[[], Awaitable[None]], bool],
                       list[panel.Verb]],
        release_acts: Callable[[list[dict[str, Any]], Callable[[], Awaitable[None]]],
                               list[panel.Verb]]) -> None:
    """Draw the tab into `body`. `games`, `people` and `releases` are the Games tab's, and
    `reread` puts fresh ones in them where they stand. `feed` is the tab row's box as the
    page was drawn, which `discovery_search.feeder` gives. `open_page` opens the page of an
    author, maker or theme the search picked. `acts` is for the games the rows are of and
    `release_acts` for the releases themselves."""
    # Imported here: `games` imports modules that import this one.
    from manager.library.games import view_control

    by_id = {row["id"]: row for row in games}
    shown: dict[str, Any] = {"rows": {}, "count": 0, "selected": 0}
    # The grid resolves a ticked id against this list, so it is kept as the rows drawn.
    held: list[dict[str, Any]] = []

    def load() -> None:
        found = vps_question.games_found({}, people, games, releases)
        built = rows(found, by_id, people, counted=counted,
                     marks=discovery_marks.read(library))
        held[:] = built
        shown["rows"] = {row["id"]: row for row in built}
        shown["count"] = len(built)

    load()
    grid_columns = grid_module.with_art(columns(kind_words, held),
                                        library.thumbnail_look())
    fields = list(_FIELDS)
    arrival = discovery_ask.opening_on(state, TAB)
    opened = views.opened(views.builtins(VIEWS)[0], arrival["name"], arrival["filters"],
                          fields) if arrival else None

    @on_page
    async def choose(group: str, one: dict[str, Any]) -> None:
        if group == vps_find.GAME:
            await on_select(by_id.get(str(one["id"])))
        else:
            await open_page({"type": group, "name": str(one["name"])})

    with body:
        with ui.row().classes(
                "w-full items-center gap-2 px-3 py-2 mb-2 shrink-0 "
                "manager-panel manager-grid-bar manager-grid-bar--flow"):
            bar = panel.grid_bar()
            wire_views, _picker, showing, describe = view_control(
                library, SCOPE, VIEWS, fields, grid_columns, bar=bar,
                art_in_lists=True, opened=opened,
                left_opened=lambda: discovery_ask.drop_opening(state, TAB))
            describe()
            with bar.bottom, panel.bar_end():
                count = ui.label(summary(shown["count"])).classes("text-xs manager-label")
                show_selection = panel.selection_menu(
                    t("manager.discovery.actions_selected"), lambda: picked(),
                    lambda chosen: verbs_of(chosen, bool(grid_module.hidden_count(table))),
                    lambda: table.run_grid_method("deselectAll"))
                if compared:
                    ui.label(compared).classes("text-xs manager-label")

        if not games:
            panel.facts(ui, [panel.intro(t("manager.discovery.empty"))])
        if not counted:
            with ui.row().classes("w-full items-center gap-2 px-3 pb-2 shrink-0"):
                comparing()

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

        def games_of(release_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            """The games the releases are for, each once, in the order first met."""
            ids = dict.fromkeys(str(one.get("game_id")) for one in release_rows)
            return [by_id[game_id] for game_id in ids if game_id in by_id]

        def verbs_of(on: list[dict[str, Any]], check: bool) -> list[panel.Verb]:
            """What the releases can be given, then what their games can. `check` is where
            the games are asked about before Add, because rows a filter hides are chosen."""
            return discovery_marks.distinguish(
                [*release_acts(on, after), *acts(games_of(on), after, check)])

        def fill_menu(row: dict | None) -> None:
            one = shown["rows"].get(str((row or {}).get("id") or ""))
            if one is None:
                menu.clear()
                return
            on = grid_module.menu_rows(table, one)
            chosen = games_of(on)
            panel.verb_menu(menu, one["name"] if len(on) == 1
                            else t("manager.discovery.selected_n", count=len(chosen)),
                            panel.for_selection(
                                verbs_of(on, len(on) > 1 and bool(grid_module.hidden_count(table))),
                                len(chosen)))

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
                by_id.get(str((shown["rows"].get(grid_module.focused_row(event)) or {})
                              .get("game_id") or ""))))
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
        """The rows read again from the library, the grid and the pane put right."""
        await reread()
        by_id.clear()
        by_id.update({row["id"]: row for row in games})
        load()
        table.run_grid_method("setGridOption", "rowData", held)
        count.text = counted_text()
        chosen = state.get("entry")
        if chosen and state.get("workbench"):
            await on_select(by_id.get(chosen))

    state["redraw_discovery"] = discovery_marks.recounting(state, after)
