"""Discovery: every game the catalog lists, against what the library holds.

One row per catalog entry. A kind the library holds a file of reads Matched where a file is
bound to a release VPS lists and Unmatched where none is. A kind VPS lists that the library
holds no file of reads Missing; any other kind is blank, and so is every cell while the
library has never been counted. A game that is not in the library is Missing on every kind
its entry lists.

The side pane is `discovery_pane`: a Game section and a section per kind the entry lists.
Kinds the library does not keep are not drawn.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Collection, Mapping
from typing import Any, cast

from nicegui import run, ui

from common import icons
from common.failures import why
from common.games import launchers
from common.i18n import t
from common.online import (
    vps_find,
    vps_formats,
    vps_home,
    vps_kinds,
    vps_marks,
    vps_question,
    vps_releases,
)
from common.runtime.jobs import KIND_VPS_ROLLUP
from manager.kit import (
    busy,
    confirm,
    deeplink,
    game_tables,
    offload,
    panel,
    thumbnails,
    verbs,
    views,
    when,
)
from manager.kit import grid as grid_module
from manager.kit.data import Library
from manager.kit.failed_together import Failures
from manager.kit.on_page import on_page
from manager.library import (
    discovery_ask,
    discovery_get,
    discovery_home,
    discovery_marks,
    discovery_marks_grid,
    discovery_releases,
    discovery_search,
    discovery_shelves,
    discovery_states,
    import_dialog,
    vps_match,
    vps_records,
)

logger = logging.getLogger("vpinfe.manager.library.discovery")

SCOPE = "discovery"
OWN_ADDRESS = "/manager?" + deeplink.query({"view": "discovery"})

HOME = "home"
BROWSE = "browse"
RELEASES = discovery_releases.TAB
# Reached from Home, so it has no segment of its own.
MARKS = discovery_marks_grid.TAB
# The grids whose saved views this page reads and writes.
VIEW_SCOPES = (SCOPE, discovery_releases.SCOPE, discovery_marks_grid.SCOPE)

HAVE = "have"
MATCHED, UNMATCHED = discovery_ask.MATCHED, discovery_ask.UNMATCHED
MISSING = discovery_states.MISSING
BLANK = "blank"
YES, NO = discovery_states.YES, discovery_states.NO
NOT_COMPARED = discovery_ask.NOT_COMPARED

# VPS's own key for the kind, the field the row carries it in, and the word it is called.
KINDS: tuple[tuple[str, str, str], ...] = (
    ("tableFiles", "kind_table", "word.table"),
    ("b2sFiles", "kind_backglass", "asset.kind.backglass.label"),
    ("romFiles", "kind_rom", "asset.kind.rom.label"),
    ("pupPackFiles", "kind_pup_pack", "asset.kind.pup_pack.label"),
    ("altColorFiles", "kind_color", "manager.discovery.color_kind"),
    ("altSoundFiles", "kind_altsound", "asset.kind.altsound.label"),
    ("povFiles", "kind_pov", "asset.kind.pov.label"),
    ("ruleFiles", "kind_rules", "guide.kind.rule_sheet.label"),
    ("wheelArtFiles", "kind_wheel", "media.kind.wheel.label"),
    ("topperFiles", "kind_topper", "media.kind.topper.label"),
)

_POLL_S = 2.0


def _kind_column(listed_as: str, field: str, word: str) -> dict[str, Any]:
    label = t(word)
    return discovery_states.kind().column(
        field, label, help=t("manager.discovery.kind.help", kind=label))


CATALOG_FACET_CEILING = 300
TABLE_AUTHORS = "table_authors"
PLAYABLE = "playable"
HELD = "held"
OTHER_HELD = "other_held"

COLUMNS = [
    grid_module.identifier("name", t("word.game"), 260, pinned="left",
                           subtitle="said", picture=thumbnails.FIELD, glyph=icons.GAMES,
                           help=t("manager.discovery.name.help"),
                           **{":getQuickFilterText":
                              "params => [params.data.name, params.data.said, "
                              "params.data.game_type, ...(params.data.themes || [])]"
                              ".filter(Boolean).join(' ')"}),
    discovery_marks.column(neither=t("word.none")),
    discovery_states.in_library().column(
        "in_library", t("manager.discovery.in_library"),
        help=t("manager.discovery.in_library.help")),
    *(_kind_column(*kind) for kind in KINDS),
    discovery_states.gap().column(
        "gap", t("manager.discovery.gaps"), help=t("manager.discovery.gaps.help")),
    discovery_states.playable().column(
        PLAYABLE, t("manager.discovery.playable"), help=t("manager.discovery.playable.help")),
    discovery_states.matched().column(
        HELD, t("manager.discovery.matched"), help=t("manager.discovery.matched.help")),
    discovery_states.other_version().column(
        OTHER_HELD, t("manager.discovery.other_version"),
        help=t("manager.discovery.other_version.help")),
    grid_module.column(TABLE_AUTHORS, t("manager.discovery.table_authors"),
                       help=t("manager.discovery.table_authors.help"),
                       **grid_module.NUMBER_COLUMN),
    grid_module.column("manufacturer", t("word.manufacturer"), help=t("help.who_made_it")),
    grid_module.year_column("year", t("word.year"), help=t("help.year_released")),
    grid_module.column("game_type", t("manager.games.type"),
                       help=t("manager.games.type.help")),
    grid_module.list_column("themes", t("manager.games.game_themes"), 200,
                            help=t("manager.games.themes.help")),
]

_KIND_FIELDS = [field for _listed, field, _word in KINDS]
# The game's own facts are on the Game cell's second line and in the pane. They stay in the
# column picker for sorting and filtering, and no view turns them on.
_PICKED = ("manufacturer", "year", "game_type", "themes")
# Table Authors is the Games You Don't Have view's own column. Playable, Have Other Version
# and Matched are shown only by a view opened on them.
_NOT_EVERYWHERE = (*_PICKED, TABLE_AUTHORS, PLAYABLE, HELD, OTHER_HELD)
_ALL = [definition["field"] for definition in COLUMNS]
_BY_NAME = ({"colId": "name", "sort": "asc", "sortIndex": 0},)

VIEWS: dict[str, list[str] | views.Preset] = {
    "manager.view.everything": views.Preset(
        columns=tuple(field for field in _ALL if field not in _NOT_EVERYWHERE),
        sort=_BY_NAME, help=t("manager.view.discovery_everything.help")),
    "manager.view.missing_games": views.Preset(
        columns=("name", *_KIND_FIELDS, "gap"), sort=_BY_NAME,
        filters={"in_library": {"values": [YES]}, "gap": {"values": [MISSING]}},
        help=t("manager.view.missing_games.help")),
}


COUNTED_FIELDS = frozenset({"in_library", "gap", HELD, OTHER_HELD, *_KIND_FIELDS})
"""The columns that are blank until the library has been counted."""


def dont_have_opening(*, counted: bool) -> dict[str, Any]:
    """The view Games You Don't Have opens All Games on: the games not in the library that have
    a table, the ones the most people made a table for first. Ignored games are left out as
    they are on the shelf, a Wanted one stays. Before the library is compared nothing is
    known to be missing, so only the table filter is set."""
    filters: dict[str, Any] = {TABLE_AUTHORS: {"filterType": "number", "type": "notBlank"}}
    if counted:
        filters["in_library"] = {"values": [NO]}
        filters[discovery_marks.FIELD] = {"values": ["", vps_marks.WANTED]}
    return {"name": t("manager.discovery.dont_have"), "filters": filters,
            "sort": ({"colId": TABLE_AUTHORS, "sort": "desc", "sortIndex": 0},
                     {"colId": "name", "sort": "asc", "sortIndex": 1})}


def family_opening(*, counted: bool) -> dict[str, Any]:
    """The view Other Versions of Your Games opens All Games on: the games not in the library
    that share a title with one it holds and have a table, in the shelf's order, ignored
    games left out. Before the library is compared nothing is known to be held, so only the
    table filter is set."""
    opening = dont_have_opening(counted=counted)
    if counted:
        opening["filters"][OTHER_HELD] = {"values": [YES]}
    return {**opening, "name": t("manager.discovery.family")}


def _state(kept: bool, listed: bool, counted: bool, in_library: bool, files: bool,
           matched: bool) -> str:
    """What a game's cell for one kind reads: Matched for a file tied to a release VPS lists,
    Unmatched for a file tied to none, whether or not VPS lists one of that kind, and
    Missing for a kind VPS lists that the game has no file of. Not compared where VPS lists
    the kind and the library has not been counted; blank where the kind is not collected or
    VPS lists none."""
    if not kept:
        return ""
    if not counted:
        return NOT_COMPARED if listed else ""
    if in_library and files:
        return MATCHED if matched else UNMATCHED
    return MISSING if listed else ""


def _playable(releases: Mapping[str, Any], formats: Collection[str]) -> str:
    """Yes where the game has a table the apps in use play: one filed under none of the
    formats fits any app, and no formats means every app plays all."""
    return YES if any(vps_formats.fits(one.get("format"), formats)
                      for one in releases.get(vps_releases.TABLES) or []) else NO


def _held(releases: Mapping[str, Any], kept: Collection[str] | None) -> str:
    """Yes where the library holds at least one release of the game, of a kind it collects."""
    return YES if any(one.get("held") for listing, found in releases.items()
                      if kept is None or listing in kept for one in found) else NO


def _matched(releases: Mapping[str, Any], listed_as: str) -> bool:
    return any(one.get("held") for one in releases.get(listed_as) or [])


def rows(found: dict[str, Any], kept: Collection[str] | None = None,
         marks: vps_marks.Marks | None = None,
         formats: Collection[str] = ()) -> list[dict[str, Any]]:
    """One grid row per named catalog entry. `found` is what `GET /library/discovery` answers.

    `kept` is the VPS kinds the library collects; a kind outside it is blank on every row
    and counts toward no gap. None keeps them all. `marks` says which entries are Wanted or
    Ignored. `formats` is the table formats the launchers play, which Table Authors counts and
    Playable is read against; empty counts every one."""
    counted = bool(found.get("counted"))
    marks = marks or vps_marks.Marks()
    titles = vps_home.held_titles(found)
    out = []
    for entry in found.get("rows") or []:
        if not (entry.get("name") or "").strip():
            continue
        listed = set(entry.get("listed") or [])
        held = set(entry.get("held") or [])
        in_library = bool(entry.get("in_library"))
        releases = entry.get("releases") or {}
        row: dict[str, Any] = {
            "id": entry["vps_id"],
            "name": entry.get("name") or "",
            "manufacturer": entry.get("manufacturer") or "",
            "year": game_tables.year_of(entry.get("year")),
            "game_type": entry.get("type") or "",
            thumbnails.FIELD: entry.get("img_url") or "",
            "themes": list(entry.get("themes") or []),
            "url": entry.get("url") or "",
            "in_library": NOT_COMPARED if not counted else (YES if in_library else NO),
            "is_in_library": in_library and counted,
            "listed": sorted(listed),
            TABLE_AUTHORS: vps_home.table_authors(entry, formats),
            discovery_marks.FIELD: marks.state(vps_marks.GAME, str(entry["vps_id"])),
            PLAYABLE: "" if kept is not None and vps_releases.TABLES not in kept
            else _playable(releases, formats),
            HELD: _held(releases, kept) if counted else NOT_COMPARED,
            OTHER_HELD: (NOT_COMPARED if not counted else YES if len(
                titles.get(vps_home.title_key(entry["name"]), ())) > (1 if in_library else 0)
                else NO),
        }
        states = []
        for listed_as, field, _word in KINDS:
            row[field] = _state(kept is None or listed_as in kept, listed_as in listed,
                                counted, in_library, listed_as in held,
                                _matched(releases, listed_as))
            states.append(row[field])
        row["said"] = game_tables.made(row)
        row["gap"] = (NOT_COMPARED if not counted
                      else MISSING if MISSING in states else "")
        out.append(row)
    return out


def rows_of(library: Library, found: dict[str, Any]) -> list[dict[str, Any]]:
    """`rows` for what the library keeps, what is marked and what the launchers play now."""
    return rows(found, kept_listings(library), discovery_marks.read(library),
                launchers.playable_formats(launchers.get_launcher_store().launchers()))


def lacks_kind(games: list[dict[str, Any]], release: Mapping[str, Any]) -> bool:
    """Whether Ignore Missing {Kind} does anything for a Releases row: its game is in the
    library, lacks the kind the release is of, and Home has a missing tab for that kind."""
    if release["kind"] not in vps_home.GAP_KINDS:
        return False
    field = next((field for listed_as, field, _word in KINDS if listed_as == release["kind"]), "")
    game = next((one for one in games if one["id"] == release["game_id"]), None)
    return game is not None and bool(game["is_in_library"]) and game.get(field) == MISSING


def releases_of(found: dict[str, Any], kept: list[str]) -> dict[str, Any]:
    """Each game's releases by VPS id, of the kinds the library collects."""
    out = {}
    for entry in found.get("rows") or []:
        mine = {kind: one for kind, one in (entry.get("releases") or {}).items() if kind in kept}
        if mine:
            out[str(entry["vps_id"])] = mine
    return out


def row_of(library: Library, vps_id: str) -> dict[str, Any] | None:
    found = rows_of(library, library.catalog())
    return next((one for one in found if one["id"] == vps_id), None)


def summary(shown: list[dict[str, Any]], counted: bool) -> str:
    if not counted:
        return t("manager.discovery.summary", count=len(shown))
    return t("manager.discovery.summary_compared", count=len(shown),
             held=sum(1 for one in shown if one["is_in_library"]))


def tab_of(state: dict[str, Any]) -> str:
    """The tab chosen; All Games where the address named a subject or a game, else Home."""
    if state.get("subject"):
        return BROWSE
    chosen = state.get("discovery_tab")
    if chosen in (HOME, BROWSE, RELEASES, MARKS):
        return str(chosen)
    return BROWSE if state.get("entry") else HOME


async def _redrawn(state: dict[str, Any], on_select: Callable[[Any], Any],
                   rerender: Callable[[], None]) -> None:
    """The page drawn again, then the pane of the entry that was open drawn again too: a
    page redraw empties the pane, and the address still names the entry."""
    rerender()
    chosen = state.get("entry")
    if chosen and state.get("workbench"):
        await on_select({"id": chosen})


async def _warm_views(library: Library) -> None:
    """Read the saved views before anything asks for them. A write made over an unread
    cache would replace the views that are saved."""
    await run.io_bound(library.warm, *(scope + views.VIEWS_SUFFIX for scope in VIEW_SCOPES))


def build(library: Library, state: dict[str, Any], on_select: Callable[[Any], Any],
          rerender: Callable[[], None]) -> None:
    tab = tab_of(state)
    if not state.get("subject"):
        state["discovery_tab"] = tab

    def switch(event: Any) -> None:
        state["discovery_tab"] = event.value
        state["subject"] = None
        deeplink.sync(state, push=True)
        rerender()

    with ui.row().classes("w-full items-stretch no-wrap gap-3 shrink-0 manager-tabs-row"):
        # The tab is named by a string, which NiceGUI takes and its stubs do not list.
        with ui.tabs(value=cast(Any, None if tab == MARKS else tab), on_change=switch) \
                .props("dense no-caps align=left shrink"):
            ui.tab(HOME, label=t("manager.discovery.tab.home"))
            ui.tab(BROWSE, label=t("manager.discovery.tab.browse"))
            ui.tab(RELEASES, label=t("manager.discovery.tab.releases"))
        opening = None if state.get("subject") else discovery_ask.opening_on(state, tab)
        box = state[discovery_search.KEY] = discovery_search.SearchBox(
            str((opening or {}).get("search") or ""))
        if opening is not None:
            box.input.on_value_change(
                lambda event: discovery_ask.keep_search(opening, event.value))
    body = ui.column().classes("w-full grow min-h-0 gap-0")
    fill = {HOME: _fill_home, RELEASES: _fill_releases, MARKS: _fill_marks}.get(tab, _fill)
    busy.fill(body, lambda: fill(library, body, state, on_select, rerender))


async def _home_shelves(library: Library) -> list[str]:
    """The shelves Home draws, in the order the settings give. The default arrangement where
    they cannot be read: a front page with every shelf beats one that does not draw."""
    try:
        return discovery_shelves.shown(await offload.io(library.manager_policy))
    except Exception:  # noqa: BLE001 - whatever the read raised, Home still draws
        logger.warning("manager: could not read which Home shelves to draw", exc_info=True)
        return discovery_shelves.shown(None)


async def _fill_home(library: Library, body: Any, state: dict[str, Any],
                     on_select: Callable[[Any], Any], rerender: Callable[[], None]) -> None:
    """Home: every shelf from one read that asks no site, so it draws before the catalog is
    in memory. The search box and the counts on Your Views wait for the catalog."""
    feed = discovery_search.feeder(state)
    await _warm_views(library)
    await discovery_marks.refresh(library)
    try:
        home = await offload.io(library.discovery_home, discovery_home.remembered_days())
    except Exception as exc:  # noqa: BLE001 - this page says why, never 500s
        with body:
            panel.facts(ui, [panel.intro(t("manager.discovery.read_failed"), hint=why(exc))])
        return
    shelves = await _home_shelves(library)
    if (update_count := state.get("update_count")) is not None:
        update_count.from_home(int(home["updates"]["count"]))
    people: list[dict[str, Any]] = []

    @on_page
    async def choose(group: str, one: dict[str, Any]) -> None:
        if group == vps_find.GAME:
            await on_select({"id": str(one["id"])})
        else:
            open_page(state, people, rerender, {"type": group, "name": str(one["name"])})

    async def catalog() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        found = await _read(library)
        people[:] = found.get("people") or []
        built = rows_of(library, found)
        feed(people, built, choose)
        return people, built

    async def reread(days: int) -> dict[str, Any]:
        return await offload.io(library.discovery_home, days)

    @on_page
    async def to_view(view_id: str) -> None:
        custom, _active = views.stored(library, SCOPE)
        await run.io_bound(views.remember, library, SCOPE, custom, view_id)
        discovery_ask.drop_opening(state, BROWSE)
        state["discovery_tab"] = BROWSE
        deeplink.sync(state, push=True)
        rerender()

    @on_page
    async def to_releases_view(view_id: str) -> None:
        await _open_view(library, state, rerender, discovery_releases.SCOPE, RELEASES, view_id)

    @on_page
    async def to_marks(view_key: str) -> None:
        await _open_view(library, state, rerender, discovery_marks_grid.SCOPE, MARKS,
                         views.builtin_id(view_key))

    def see_new(days: int, chip: str) -> None:
        name = t("manager.discovery.new_view", window=t(discovery_home.WINDOWS[days]))
        open_page(state, people, rerender, None, tab=RELEASES, opening={
            "name": name, "filters": discovery_home.new_filters(
                days, chip, kept_listings(library), counted=bool(home["counted"]))})

    def see_missing(game_ids: list[str]) -> Any:
        """Games, narrowed to the games the tab counts."""
        from manager.library import games
        from manager.overview import needs
        return state["open_on"](needs.Opens("games", keys={games.LACKING_KEY: tuple(game_ids)}))

    def see_dont_have() -> None:
        """All Games, on the games the shelf draws from."""
        open_page(state, people, rerender, None, tab=BROWSE,
                  opening=dont_have_opening(counted=bool(home["counted"])))

    def see_family() -> None:
        """All Games, on the games the shelf draws from."""
        open_page(state, people, rerender, None, tab=BROWSE,
                  opening=family_opening(counted=bool(home["counted"])))

    def see_health(finding: str) -> Any:
        """Games, on the view that lists the finding's games."""
        from manager.overview import needs
        return state["open_on"](needs.duplicates_opens() if finding == vps_home.DUPLICATES
                                else needs.no_tables_opens())

    def see_unmatched() -> Any:
        """Games, on its Unmatched view, which also lists the games with no likely entry."""
        from manager.library import games
        from manager.overview import needs
        return state["open_on"](needs.Opens("games", games.UNMATCHED))

    async def match_game(game: dict[str, Any]) -> bool:
        """The Match picker on this game, which says what it picked once it is done. Whether it
        changed the match; the catalog is read again only then."""
        if not await vps_match.walk(library, [game]):
            return False
        library.forget_catalog()
        return True

    def see_updates(game_ids: list[str]) -> Any:
        """Games, narrowed to the games with an update."""
        from manager.library import games
        from manager.overview import needs
        return state["open_on"](needs.Opens("games", keys={games.UPDATES_KEY: tuple(game_ids)}))

    async def see_picture(vps_id: str, kind: str) -> None:
        """The game's pane, open on the section of this media kind."""
        from manager.library import discovery_kinds
        state["section"] = discovery_kinds.section_key(kind)
        await on_select({"id": vps_id})

    def go_to(subject: dict[str, str]) -> None:
        open_page(state, people, rerender, subject)

    @on_page
    async def redraw_home() -> None:
        library.forget_catalog()
        await _redrawn(state, on_select, rerender)

    state["open_author"] = lambda name: go_to({"type": vps_find.AUTHOR, "name": name})
    state["redraw_discovery"] = redraw_home
    discovery_home.fill(
        body, home, library=library, scope=SCOPE, view_presets=VIEWS, catalog=catalog,
        reread=reread, on_select=on_select, see_new=see_new, see_missing=see_missing,
        see_updates=see_updates, see_dont_have=see_dont_have, see_family=see_family,
        see_health=see_health, see_unmatched=see_unmatched, match_game=match_game,
        see_picture=see_picture,
        open_view=to_view, open_releases_view=to_releases_view, open_marks=to_marks,
        open_subject=go_to, marking=discovery_marks.Marking(library, redraw_home),
        shelves=shelves)
    if not home["counted"]:
        with body:
            comparing_line(library, rerender)


@on_page
async def _open_view(library: Library, state: dict[str, Any], rerender: Callable[[], None],
                     scope: str, tab: str, view_id: str) -> None:
    """The grid on `tab`, showing the view `view_id` of its saved views."""
    custom, _active = views.stored(library, scope)
    await run.io_bound(views.remember, library, scope, custom, view_id)
    discovery_ask.drop_opening(state, tab)
    state["discovery_tab"] = tab
    deeplink.sync(state, push=True)
    rerender()


async def _fill_marks(library: Library, body: Any, state: dict[str, Any],
                      on_select: Callable[[Any], Any], rerender: Callable[[], None]) -> None:
    feed = discovery_search.feeder(state)
    await _warm_views(library)
    await discovery_marks.refresh(library)
    try:
        found = await _read(library)
    except Exception as exc:  # noqa: BLE001 - this page says why, never 500s
        with body:
            panel.facts(ui, [panel.intro(t("manager.discovery.read_failed"), hint=why(exc))])
        return
    people = list(found.get("people") or [])

    async def go_to(subject: dict[str, str]) -> None:
        open_page(state, people, rerender, subject)

    state["open_author"] = lambda name: go_to({"type": vps_find.AUTHOR, "name": name})
    await discovery_marks_grid.fill(
        body, library=library, state=state, feed=feed, found=lambda: _read(library),
        words=discovery_marks_grid.kind_words({listed_as: t(word) for listed_as, _field, word
                                               in KINDS}),
        games=rows_of(library, found), people=people, on_select=on_select, open_page=go_to)


async def _fill_releases(library: Library, body: Any, state: dict[str, Any],
                         on_select: Callable[[Any], Any], rerender: Callable[[], None]) -> None:
    feed = discovery_search.feeder(state)
    await _warm_views(library)
    await discovery_marks.refresh(library)
    try:
        found = await _read(library)
    except Exception as exc:  # noqa: BLE001 - this page says why, never 500s
        with body:
            panel.facts(ui, [panel.intro(t("manager.discovery.read_failed"), hint=why(exc))])
        return
    kept_now = kept_listings(library)
    built = rows_of(library, found)
    people = list(found.get("people") or [])
    releases = releases_of(found, kept_now)
    kind_words = {listed_as: word for listed_as, _field, word in KINDS if listed_as in kept_now}

    async def reread() -> None:
        if not library.has_catalog():
            await offload.io(library.load_catalog)
        again = library.catalog()
        built[:] = rows_of(library, again)
        people[:] = again.get("people") or []
        releases.clear()
        releases.update(releases_of(again, kept_listings(library)))

    async def go_to(subject: dict[str, str]) -> None:
        open_page(state, people, rerender, subject)

    state["open_author"] = lambda name: go_to({"type": vps_find.AUTHOR, "name": name})
    await discovery_releases.fill(
        body, library=library, state=state, feed=feed, games=built, people=people,
        releases=releases, kind_words=kind_words,
        counted=bool(found.get("counted")), compared=_counted_text(found),
        comparing=lambda: comparing_line(library, rerender),
        on_select=on_select, reread=reread, open_page=go_to,
        acts=lambda chosen, after, check: acts(library, chosen, after, check=check,
                                               wanting=False),
        release_acts=lambda chosen, after: discovery_marks.release_verbs(
            library, chosen, after, kind_word=lambda listing: t(kind_words[listing]),
            lacks=lambda one: lacks_kind(built, one)))


def open_page(state: dict[str, Any], people: list[dict[str, Any]],
              rerender: Callable[[], None], subject: dict[str, str] | None, *,
              opening: dict[str, Any] | None = None, tab: str = "") -> None:
    """Go to an author's, maker's or theme's page, or leave it (`subject` None) - for a
    grid on `tab`, opened on the view `opening` names."""
    if subject and subject["type"] == vps_find.AUTHOR:
        known = vps_question.person_named(subject["name"], people)
        subject = {**subject, "name": str(known["name"]) if known else subject["name"]}
    state["subject"] = subject
    if opening:
        state["opening"] = {**opening, "tab": tab}
    if tab:
        state["discovery_tab"] = tab
    deeplink.sync(state, push=True)
    rerender()


def _filter_on(definition: dict[str, Any] | None, value: str) -> dict[str, Any]:
    """The model that picks `value` on a column, whichever kind of filter it has."""
    if definition is not None and definition.get(":filter") in (
            grid_module.CHOICE_FILTER, grid_module.LIST_FILTER):
        return {"values": [value]}
    return {"filterType": "text", "type": "equals", "filter": value}


def browse_filters(subject: dict[str, str], question: dict[str, Any],
                   columns: list[dict[str, Any]], *, counted: bool) -> dict[str, Any]:
    """What a maker's or a theme's page asks, as filters on the All Games columns. In Library
    and the kind columns are left out before the library has been counted: the columns hold
    nothing then, and a filter on them would empty the grid.

    Without is a game of the library with a file missing: the kind's cell reads Missing, or
    Anything Missing where no kind is picked."""
    by_field = {one["field"]: one for one in columns}
    out: dict[str, Any] = {}
    if subject["type"] == "maker":
        out["manufacturer"] = _filter_on(by_field.get("manufacturer"), subject["name"])
    else:
        out["themes"] = {"values": [subject["name"]]}
    if question.get("format"):
        out[PLAYABLE] = {"values": [YES]}
    if not counted:
        return out
    if question.get("owned"):
        out["in_library"] = {"values": [question["owned"]]}
    field = next((field for listed_as, field, _word in KINDS
                  if listed_as == question.get("kind")), "")
    if field:
        out[field] = {"values": [MATCHED, UNMATCHED, MISSING]}
    if question.get("held") == "missing":
        out[field or "gap"] = {"values": [MISSING]}
    elif question.get("held") == "have" and field:
        out[field] = {"values": [MATCHED, UNMATCHED]}
    return out


def opening_for(subject: dict[str, str], question: dict[str, Any],
                columns: list[dict[str, Any]], *, counted: bool, search: str = "",
                releases: Mapping[str, Any] | None = None) -> tuple[str, dict[str, Any]]:
    """The tab a page's Open in Grid goes to and the view it opens: an author's releases on
    Releases, a maker's or theme's games on All Games. The view is named for the page, and for
    the kind only where the grid has a filter on that kind. `search` is what was typed in the
    tab row's box, which the grid is opened with; `releases` is `releases_of`'s, which says
    the formats a release is filed under."""
    if subject["type"] == vps_find.AUTHOR:
        tab, filters, kind_filter = RELEASES, discovery_releases.arrival_filters(
            question, counted=counted,
            filed=None if releases is None else formats_filed(releases)), "kind"
    else:
        tab, filters = BROWSE, browse_filters(subject, question, columns, counted=counted)
        kind_filter = next((field for listed_as, field, _word in KINDS
                            if listed_as == question.get("kind")), "")
    name = subject["name"]
    if question.get("kind") in discovery_ask.KIND_MANY and kind_filter in filters:
        name = t("manager.discovery.ask.view_name", name=name,
                 kind=t(discovery_ask.KIND_MANY[question["kind"]]))
    opening: dict[str, Any] = {"name": name, "filters": filters}
    if search.strip():
        opening["search"] = search
    return tab, opening


def formats_filed(releases: Mapping[str, Any]) -> set[str]:
    """Every format a release is filed under, the empty one for a release filed under none.
    `releases` is `releases_of`'s, by game."""
    return {str(one.get("format") or "") for mine in releases.values()
            for found in mine.values() for one in found}


async def _read(library: Library) -> dict[str, Any]:
    if library.has_catalog():
        return library.catalog()
    return await offload.io(library.load_catalog)


def comparing_line(library: Library, rerender: Callable[[], None]) -> ui.label:
    """Says the library is being compared with VPS while that job runs, and draws the page
    again when one ends after this line was drawn. It never starts one: the sources watch
    owes and runs it."""
    line = ui.label(t("manager.discovery.comparing")).classes("text-xs manager-label")
    line.set_visibility(False)
    drawn = time.time()

    @on_page
    async def look() -> None:
        try:
            listed = await offload.io(library.jobs)
        except Exception:  # noqa: BLE001 - the page's own job line reports a failed read
            return
        ours = [one for one in listed if one.get("kind") == KIND_VPS_ROLLUP]
        running = any(one.get("state") == "running" for one in ours)
        line.set_visibility(running)
        if not running and any(one.get("state") == "done"
                               and float(one.get("finished_at") or 0) >= drawn
                               for one in ours):
            library.forget_catalog()
            rerender()

    ui.timer(_POLL_S, look)
    return line


def grid_columns(library: Library, built: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The columns the grid declares: a kind's columns only for a kind the library collects."""
    off = {field for listed_as, field, _word in KINDS if not kept(library, listed_as)}
    return grid_module.with_art(
        grid_module.with_derived_facets([one for one in COLUMNS if one["field"] not in off],
                                        built, CATALOG_FACET_CEILING),
        library.thumbnail_look())


async def _fill(library: Library, body: Any, state: dict[str, Any],
                on_select: Callable[[Any], Any], rerender: Callable[[], None]) -> None:
    # Imported here: `games` imports modules that import this one.
    from manager.library.games import view_control

    feed = discovery_search.feeder(state)
    await _warm_views(library)
    await discovery_marks.refresh(library)
    try:
        found = await _read(library)
    except Exception as exc:  # noqa: BLE001 - this page says why, never 500s
        with body:
            panel.facts(ui, [panel.intro(t("manager.discovery.read_failed"), hint=why(exc))])
        return
    counted = bool(found.get("counted"))
    built = rows_of(library, found)
    people = list(found.get("people") or [])
    kept_now = kept_listings(library)
    releases = releases_of(found, kept_now)
    kind_words = {listed_as: word for listed_as, _field, word in KINDS if listed_as in kept_now}

    async def go_to(subject: dict[str, str] | None) -> None:
        """Open a subject's page, or leave it."""
        open_page(state, people, rerender, subject)

    async def open_grid(subject: dict[str, str], question: dict[str, Any], search: str) -> None:
        """Open the grid that answers the page's question, as a view named for the page."""
        tab, opening = opening_for(subject, question, grid_columns(library, built),
                                   counted=counted, search=search, releases=releases)
        open_page(state, people, rerender, None, opening=opening, tab=tab)

    by_id = {row["id"]: row for row in built}

    @on_page
    async def choose(group: str, one: dict[str, Any]) -> None:
        if group == vps_find.GAME:
            await on_select(by_id.get(str(one["id"])))
        else:
            await go_to({"type": group, "name": str(one["name"])})

    state["open_author"] = lambda name: go_to({"type": vps_find.AUTHOR, "name": name})
    if state.get("subject"):
        subject = state["subject"]
        known = vps_question.person_named(subject["name"], people) \
            if subject["type"] == vps_find.AUTHOR else None
        renamed = known is not None and known["name"] != subject["name"]
        if known is not None and renamed:
            state["subject"] = subject = {**subject, "name": str(known["name"])}

        @on_page
        async def redraw_subject() -> None:
            await _redrawn(state, on_select, rerender)

        state["redraw_discovery"] = discovery_marks.recounting(state, redraw_subject)
        with body:
            if renamed:
                deeplink.sync(state)
            narrow = discovery_ask.page(
                subject=subject, state=state, people=people, rows=built,
                releases=releases, kinds=list(kind_words),
                fields={listed_as: field for listed_as, field, _word in KINDS},
                address=OWN_ADDRESS, open_game=on_select,
                leave=lambda: go_to(None),
                see_all=lambda question, search: open_grid(subject, question, search))
        feed(people, built, choose, narrow=narrow)
        return
    columns = grid_columns(library, built)
    on_screen: dict[str, Any] = {"ids": [row["id"] for row in built], "selected": 0}
    fields = [one["field"] for one in columns]
    arrival = discovery_ask.opening_on(state, BROWSE)
    opened = views.opened(views.builtins(VIEWS)[0], arrival["name"], arrival["filters"],
                          fields, sort=tuple(arrival.get("sort") or ())) if arrival else None

    with body:
        with ui.row().classes(
                "w-full items-center gap-2 px-3 py-2 mb-2 shrink-0 "
                "manager-panel manager-grid-bar manager-grid-bar--flow"):
            bar = panel.grid_bar()
            wire_views, _picker, showing, describe = view_control(
                library, SCOPE, VIEWS, fields, columns, bar=bar,
                art_in_lists=True, opened=opened,
                left_opened=lambda: discovery_ask.drop_opening(state, BROWSE),
                unfiltered=frozenset() if counted else COUNTED_FIELDS)
            describe()
            with bar.bottom, panel.bar_end():
                count = ui.label(summary(built, counted)).classes("text-xs manager-label")
                show_selection = panel.selection_menu(
                    t("manager.discovery.actions_selected"), lambda: picked(),
                    lambda chosen: acts(library, chosen, after,
                                        check=bool(grid_module.hidden_count(table))),
                    lambda: table.run_grid_method("deselectAll"))
                ui.label(_counted_text(found)).classes("text-xs manager-label")

        if not built:
            panel.facts(ui, [panel.intro(t("manager.discovery.empty"))])
        if not counted:
            comparing_line(library, rerender)

        def counted_text() -> str:
            if on_screen["selected"]:
                return grid_module.selection_said(
                    table, on_screen["selected"],
                    t("manager.discovery.selected", picked=on_screen["selected"],
                      shown=len(on_screen["ids"])))
            return summary([by_id[one] for one in on_screen["ids"] if one in by_id], counted)

        def on_selected(chosen_rows: list[dict[str, Any]]) -> None:
            on_screen["selected"] = len(chosen_rows)
            show_selection(bool(chosen_rows))
            count.text = counted_text()

        def fill_menu(row: dict | None) -> None:
            one = by_id.get(str((row or {}).get("id") or ""))
            if one is None:
                menu.clear()
                return
            on = grid_module.menu_rows(table, one)
            panel.verb_menu(menu, one["name"] if len(on) == 1
                            else t("manager.discovery.selected_n", count=len(on)),
                            panel.for_selection(
                                acts(library, on, after,
                                     check=len(on) > 1 and bool(grid_module.hidden_count(table))),
                                len(on)))

        async def on_header_context(col_id: str | None) -> None:
            await grid_module.header_menu(menu, table, columns, col_id)

        with ui.element("div").classes("w-full grow min-h-0 flex flex-col"):
            table: ui.aggrid = grid_module.build(columns, built, SCOPE,
                                                 on_select_rows=on_selected,
                                                 on_context=fill_menu,
                                                 on_header_context=on_header_context,
                                                 view_of=showing)
            menu: ui.context_menu = ui.context_menu()
        grid_module.on_row_focus(
            SCOPE, lambda event: on_select(by_id.get(grid_module.focused_row(event))))
        feed(
            people, built, choose,
            narrow=lambda text: table.run_grid_method("setGridOption", "quickFilterText", text))
        table.on("modelUpdated", lambda: _counted_in_view())
        wire_views(table)

    def picked() -> list[dict[str, Any]]:
        return [by_id[str(r["id"])] for r in grid_module.selection(table)
                if str(r.get("id")) in by_id]

    async def _counted_in_view() -> None:
        drawn = await ui.run_javascript(_SHOWN % table.id) or []
        on_screen["ids"] = [str(one) for one in drawn]
        count.text = counted_text()

    @on_page
    async def after() -> None:
        """The rows read again from the library, the grid and the pane put right."""
        if not library.has_catalog():
            await offload.io(library.load_catalog)
        again = library.catalog()
        fresh = rows_of(library, again)
        built[:] = fresh
        by_id.clear()
        by_id.update({row["id"]: row for row in fresh})
        people[:] = again.get("people") or []
        releases.clear()
        releases.update(releases_of(again, kept_listings(library)))
        table.run_grid_method("setGridOption", "rowData", built)
        chosen = state.get("entry")
        if chosen and state.get("workbench"):
            await on_select(by_id.get(chosen))

    @on_page
    async def settled(entry_id: str) -> bool:
        """One entry's row read again and put right where it stands, and the open pane with
        it where that is the entry. False where the pane has to be drawn again."""
        if not library.has_catalog():
            await offload.io(library.load_catalog)
        row = next((one for one in rows_of(library, library.catalog())
                    if one["id"] == entry_id), None)
        if row is None or entry_id not in by_id:
            return False
        if state.get("entry") == entry_id:
            pane = state.get("settle_pane_discovery")
            if not state.get("workbench") or not callable(pane) or not await pane(row):
                return False
        grid_module.transact(table, built, {"update": [row]}, by_id)
        count.text = counted_text()
        return True

    state["redraw_discovery"] = discovery_marks.recounting(state, after)
    state["settle_discovery"] = settled


_SHOWN = """(() => {
  const ids = [];
  getElement(%d).api.forEachNodeAfterFilterAndSort(node => ids.push(node.data.id));
  return ids;
})()"""


def _counted_text(found: dict[str, Any]) -> str:
    if not found.get("counted"):
        return ""
    return t("manager.discovery.compared", when=when.ago(found["counted"], inline=True))


def acts(library: Library, rows_on: list[dict[str, Any]],
         after: Callable[[], Awaitable[None]], *, check: bool = False,
         wanting: bool = True) -> list[panel.Verb]:
    """What can be done to the entries a menu is open on. `check` is where the selection
    holds rows a search, a filter or a view leaves out, which `add` asks about first.
    `wanting` is False where a release, not the game, is what can be Wanted.

    Add waits for the comparison, which says nothing of why: before one, every entry reads
    as not in the library."""
    absent = [one for one in rows_on if one["in_library"] == NO]
    uncounted = any(one["in_library"] == NOT_COMPARED for one in rows_on)
    hint = "" if absent or uncounted else t("manager.discovery.already_in")
    return [panel.Verb(
        t("manager.discovery.add"),
        (lambda: add(library, absent, after, check=check)) if absent else None,
        hint=hint, icon=verbs.ADD),
        *discovery_marks.game_verbs(library, rows_on, after, wanting=wanting)]


def pane_acts(library: Library, row: dict[str, Any], game_id: str,
              after: Callable[[], Awaitable[None]]) -> list[panel.Verb]:
    """The pane's own menu: `acts` for the entry, then the two places it can be opened.

    Open in Games is dimmed with its reason while no game holds the entry, which is also
    every entry before the first comparison."""
    out = acts(library, [row], after)
    opened = t("manager.discovery.to_games")
    if game_id:
        out.append(panel.Verb(
            opened, to="/manager?" + deeplink.query({"view": "games", "game": game_id})))
    else:
        hint = {NO: t("manager.discovery.absent")}.get(row["in_library"], "")
        out.append(panel.Verb(opened, hint=hint))
    if row["url"]:
        out.append(panel.Verb(t("manager.discovery.picture_open"), to=row["url"]))
    return out


@on_page
async def add(library: Library, entries: list[dict[str, Any]],
              after: Callable[[], Awaitable[None]], *, check: bool = False) -> None:
    """Make a game with no tables from each entry, in the place new games go."""
    if len(entries) > 1 or check:
        if not await confirm.ask(
                t("manager.discovery.add_many", count=len(entries)),
                detail=t("manager.discovery.add_detail"),
                lines=[one["name"] for one in entries], confirm=t("manager.discovery.add"),
                icon=verbs.ADD, danger=False):
            return
    where = await import_dialog.ask_where(library)
    if where is None:
        return
    made = 0
    failed = Failures()
    for entry in entries:
        try:
            await offload.io(library.add_game_from_vps, entry["id"], where)
        except Exception as exc:  # noqa: BLE001 - one refusal does not end the rest
            logger.warning("manager: could not add %s", entry["id"], exc_info=True)
            failed.add(entry["name"], why(exc))
            continue
        made += 1
    failed.say("manager.discovery.add_failed")
    if made:
        ui.notify(t("manager.discovery.added", count=made), type="positive")
        await after()


_TYPE_WORDS = {"SS": "manager.discovery.type.ss", "EM": "manager.discovery.type.em",
               "PM": "manager.discovery.type.pm"}


def type_word(code: str) -> str:
    """VPS's two letters spelled out, or as given for a code this does not know."""
    key = _TYPE_WORDS.get(code.strip().upper())
    return t(key) if key else code


def kept_ours(library: Library, listed_as: str) -> set[str]:
    """Which of our kinds behind a VPS kind this library collects. The table has no switch."""
    kind = vps_kinds.BY_LISTING.get(listed_as)
    if kind is None:
        return {listed_as}
    return set(kind.ours) & library.kept_kinds()[kind.held_in]


def kept(library: Library, listed_as: str) -> bool:
    return bool(kept_ours(library, listed_as))


def kept_listings(library: Library) -> list[str]:
    """The VPS kinds this library collects, in the order the grid draws them."""
    return [listed_as for listed_as, _field, _word in KINDS if kept(library, listed_as)]


async def owner_state(context: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    """The game that holds this entry, and what it holds of it. ("", None) where none does
    or it cannot be read, which the pane answers from the row's own counts.

    Read once per drawing of the pane, whichever section asks first: both sections draw
    from the same answer."""
    if "owner" not in context:
        context["owner"] = asyncio.ensure_future(
            _read_owner(context["library"], context["discovery"]))
    return await context["owner"]


async def _read_owner(library: Library,
                      row: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    if not row["is_in_library"]:
        return "", None
    try:
        owned, _other = await offload.io(library.owned, [row["id"]])
        game_id = str((owned.get(row["id"]) or {}).get("game_id") or "")
        if not game_id:
            return "", None
        return game_id, await offload.io(library.vps_held, game_id)
    except Exception:  # noqa: BLE001 - the pane still draws from the row
        logger.warning("manager: could not read what %s holds", row["id"], exc_info=True)
        return "", None


def standing(library: Library, row: dict[str, Any],
             held: dict[str, Any] | None) -> dict[str, str]:
    """Have or Missing for each kept kind VPS lists. Empty where the library says nothing:
    the game is not in it, or it has never been counted."""
    if not row["is_in_library"]:
        return {}
    said: dict[str, str] = {}
    for listed_as, field, _word in KINDS:
        if listed_as not in row["listed"] or not kept(library, listed_as):
            continue
        if held is None:
            answer = HAVE if row[field] in (MATCHED, UNMATCHED) else row[field]
        else:
            has = (vps_records.held_of(held, listed_as)
                   or vps_records.kind_state(held, listed_as).get("held"))
            answer = HAVE if has else MISSING
        if answer:
            said[listed_as] = answer
    return said


# The VPS kinds that are art rather than files: they sit among the media, with the files
# a source holds of the same kind.
MEDIA_LISTINGS = {"wheelArtFiles": "wheel", "topperFiles": "topper", "ruleFiles": "rule_sheet"}


def media_bands(listed: list[tuple[str, str, str]],
                offers: Collection[str]) -> list[tuple[str, tuple[str, str, str]]]:
    """Each media kind with something to show, wheel and topper first, with the VPS kind
    behind it where VPS lists one."""
    bands: dict[str, tuple[str, str, str]] = {}
    for kind in listed:
        if kind[0] in MEDIA_LISTINGS:
            bands[MEDIA_LISTINGS[kind[0]]] = kind
    for media_kind in offers:
        bands.setdefault(media_kind, ("", "", f"media.kind.{media_kind}.label"))
    return sorted(bands.items(), key=lambda one: discovery_get.media_order(one[0]))


async def media_present(library: Library, game_id: str) -> dict[str, Any] | None:
    """What media the game has, by kind. None where the game is not in the library or it
    cannot be read, which marks nothing Missing."""
    if not game_id:
        return None
    try:
        return await offload.io(library.media_for, game_id, None)
    except Exception:  # noqa: BLE001 - the kinds still draw, unmarked
        logger.warning("manager: could not read the media %s has", game_id, exc_info=True)
        return None


def holds(held: dict[str, Any] | None, listed_as: str) -> bool:
    """Whether the owning game holds anything of one VPS kind."""
    if held is None:
        return False
    return bool(vps_records.held_of(held, listed_as)
                or vps_records.kind_state(held, listed_as).get("held"))
