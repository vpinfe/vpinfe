"""The library seen by media file rather than by game.

Its own subject, not a lens on the games grid. A matrix of games against kinds answers
coverage well and can hold one fact per cell; a file has several, and the question
"everything a stranger placed" spans twenty columns there and one here.

The row that matters is the one with nothing in it. A selection under Games is a
selection of games whatever column you filtered, so the kind is never part of what a
bulk action is given - which is why filling gaps in bulk has had nowhere to live.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from nicegui import ui

from common import icons
from common.games import asset_origin
from common.i18n import t
from common.media.media_specs import media_label_map
from manager.devices import plays_on
from manager.kit import game_tables, grid, media_ownership, offload, panel, thumbnails, views
from manager.library import file_acts, file_states
from manager.library.games import view_control

SCOPE = "manager.media.columns"

_SCOPE_CHOICES = [{"value": "game", "label": t("word.game")},
                  {"value": "table", "label": t("word.table")}]

_SOURCE_CHOICES = ([{"value": name, "label": name}
                    for name in media_ownership.source_names()]
                   + [{"value": "", "label": t("word.no_file")}])


def _kind_choices(library: Any) -> list[dict[str, Any]]:
    """The kinds of media the library collects, as a filter lists them. Fixed, like the
    reasons beside it, so a kind nothing holds yet is still one to ask about."""
    kept = library.kept_kinds()["media"]
    return [{"value": word, "label": word}
            for kind, word in sorted(media_label_map().items(), key=lambda one: one[1].lower())
            if kind in kept]


def _with_kind_filter(columns: list[dict[str, Any]], library: Any) -> list[dict[str, Any]]:
    return [{**one, **grid.choice_filter(_kind_choices(library))}
            if one["field"] == "label" else one for one in columns]


def _standing_in(via: str) -> str:
    """What is covering an empty slot on the cabinet, said short.

    Worth a column because this lens reads "Missing" against a machine that is visibly
    showing something, and both are true: the file belongs to another slot.
    """
    if via.startswith("set:"):
        return t("manager.media.set_named", name=via.split(":", 1)[1])
    if via.startswith("fallback:"):
        kind = via.split(":", 1)[1]
        return media_label_map().get(kind, kind)
    return ""



def _reason(row: dict[str, Any], refused: bool = False) -> str:
    via = str(row.get("via") or "")
    if via in (file_states.ORPHAN, file_states.UNUSED, file_states.UNSUPPORTED):
        return via
    if not row.get("present"):
        return file_states.MISSING
    return file_states.WONT_PLAY if refused else file_states.IN_USE


def _refused(row: dict[str, Any], devices: list[dict[str, Any]], local_id: str) -> bool:
    """Whether a device that plays this library cannot play this video, from the codec
    its header says."""
    return bool(plays_on.refusals(row.get("video_codec"), devices, local_id))


def reason_of(row: dict[str, Any], devices: list[dict[str, Any]], local_id: str) -> str:
    """What `reason` says of a raw row, for a count that must agree with the grid."""
    return _reason(row, _refused(row, devices, local_id))


def rows(found: list[dict[str, Any]], devices: list[dict[str, Any]] | None = None,
         local_id: str = "") -> list[dict[str, Any]]:
    """The API's media, flattened for a grid.

    Three facts kept apart, because one column carrying two of them is what made a row
    read as a contradiction: `used_by` counts tables and only ever describes a file,
    `reason` speaks only when a file is not the one being used, and `path` says which
    file, name and folder together. `devices` is what a video is judged against, and
    none leaves every video playing.
    """
    devices = devices or []
    return [{**row,
             "year": game_tables.year_of(row.get("year")),
             "scope": game_tables.scope_of(row),
             # One path, not a name and a folder: `medias/` or the game folder is two
             # values across a whole library, which is a column that says the same
             # thing on nearly every row.
             "path": row.get("path") or "",
             "used_by": row.get("serves"),
             "reason": reason_of(row, devices, local_id),
             "source": media_ownership.source_name(str(row.get("origin") or "")),
             "match": str(row.get("matched_to") or ""),
             "covered_by": _standing_in(str(row.get("standing_in") or "")),
             grid.NO_MENU: file_acts.is_loose(row),
             grid.NO_DROP: row.get("via") in (file_states.ORPHAN, file_states.UNSUPPORTED)}
            for row in found]


# The groups the column picker offers, the way Games and Tables already group theirs.
_FILE = "word.file"
_GAME = "word.game"
_SOURCE = "word.source"

COLUMNS: list[dict[str, Any]] = [
    grid.identifier("game", t("word.name"), 240, pinned="left", group=t(_FILE),
                subtitle=("said", "", "said_built"), picture=thumbnails.FIELD,
                glyph=icons.GAMES, built_tip=t("manager.media_ownership.named_for"),
                help=t("manager.media.name.help")),
    grid.column("scope", t("manager.media.scope"), 100, group=t(_FILE),
                **grid.choice_filter(_SCOPE_CHOICES),
                help=t("manager.media.scope.help")),
    # The value stays the kind, which is what the Kind filter lists; a later page shows
    # as "Flyer, page 2" and the filter still finds it under Flyer.
    grid.column("label", t("word.kind"), 160, group=t(_FILE),
                help=t("manager.media.kind.help"),
                **{":valueFormatter": "params => (params.data || {}).named || params.value"}),
    grid.column("path", t("word.path"), 300, group=t(_FILE),
                help=t("manager.media.path.help")),
    grid.column("source", t("word.source"), 165, group=t(_SOURCE),
                **grid.choice_filter(_SOURCE_CHOICES),
                help=t("manager.media.source.help")),
    grid.column("used_by", t("word.used_by"), type="numericColumn", group=t(_FILE),
                help=t("manager.media.used_by.help")),
    file_states.table("manager.media.unused_meaning", converts=True).column(
        "reason", t("word.in_use"), 150, help=t("manager.media.reason.help"), group=t(_FILE)),
    grid.column("match", t("manager.media.match"), 140, group=t(_SOURCE),
                help=t("manager.media.match.help")),
    grid.column("covered_by", t("manager.media.covered"), 130, group=t(_FILE),
                help=t("manager.media.covered_by.help")),
    grid.column("manufacturer", t("word.manufacturer"), 150, group=t(_GAME),
                help=t("help.who_made_it")),
    grid.year_column("year", t("word.year"), group=t(_GAME),
                     help=t("help.year_released")),
]

_ALL = [definition["field"] for definition in COLUMNS]

# A built-in may filter where its name is what somebody would predict the filter from,
# and says in `help` what it is for rather than what it filters - a reader can see which
# rows are here; what they cannot see is why this was worth building a view for.
SOURCES = "manager.view.sources"
UNSUPPORTED_VIEW = "manager.view.unsupported"
WONT_PLAY_VIEW = "manager.view.wont_play"


def recorded() -> dict[str, Any]:
    """The filter onto the files recordings placed."""
    return {"source": {"values": [media_ownership.source_name(asset_origin.RECORDED)]}}


def arrive_on(library: Any, key: str) -> None:
    """Make this built-in the view Media opens on next. Writes, so not on the loop."""
    custom, _active = views.stored(library, SCOPE)
    views.remember(library, SCOPE, custom, views.builtin_id(key))


def arrive_on_sources(library: Any) -> bool:
    """Make Sources the view Media opens on next."""
    arrive_on(library, SOURCES)
    return True


VIEWS: dict[str, list[str] | views.Preset] = {
    "manager.view.have": views.Preset(
        columns=("game", "label", "path", "source", "used_by", "reason"),
        sort=({"colId": "game", "sort": "asc", "sortIndex": 0},),
        filters={"reason": {"values": [file_states.IN_USE, file_states.WONT_PLAY,
                                       file_states.ORPHAN, file_states.UNUSED,
                                       file_states.UNSUPPORTED]}},
        help=t("manager.media.view_have.help")),
    "manager.view.missing": views.Preset(
        columns=("game", "label", "reason"),
        sort=({"colId": "game", "sort": "asc", "sortIndex": 0},),
        filters={"reason": {"values": [file_states.MISSING]}},
        help=t("manager.media.view_missing.help")),
    "manager.view.orphans": views.Preset(
        columns=("game", "label", "path", "reason"),
        sort=({"colId": "game", "sort": "asc", "sortIndex": 0},),
        filters={"reason": {"values": [file_states.ORPHAN]}},
        help=t("manager.media.view_orphans.help")),
    "manager.view.unused": views.Preset(
        columns=("game", "label", "path", "source", "used_by", "reason"),
        sort=({"colId": "game", "sort": "asc", "sortIndex": 0},),
        filters={"reason": {"values": [file_states.UNUSED]}},
        help=t("manager.media.view_unused.help")),
    UNSUPPORTED_VIEW: views.Preset(
        columns=("game", "label", "path", "reason"),
        sort=({"colId": "game", "sort": "asc", "sortIndex": 0},),
        filters={"reason": {"values": [file_states.UNSUPPORTED]}},
        help=t("manager.media.view_unsupported.help")),
    WONT_PLAY_VIEW: views.Preset(
        columns=("game", "label", "path", "reason"),
        sort=({"colId": "game", "sort": "asc", "sortIndex": 0},),
        filters={"reason": {"values": [file_states.WONT_PLAY]}},
        help=t("manager.media.view_wont_play.help")),
    SOURCES: views.Preset(
        columns=("game", "label", "path", "source", "used_by", "match"),
        sort=({"colId": "source", "sort": "asc", "sortIndex": 0},
              {"colId": "game", "sort": "asc", "sortIndex": 1}),
        filters={"reason": {"values": [file_states.IN_USE, file_states.WONT_PLAY]}},
        help=t("manager.media.view_sources.help")),
    "manager.view.everything": views.Preset(
        columns=tuple(_ALL),
        help=t("manager.media.view_everything.help")),
}


def _judged(library: Any, state: dict[str, Any]
            ) -> Callable[[list[dict[str, Any]]], list[dict[str, Any]]]:
    """`rows`, with a video judged against the devices this page last read."""
    return lambda found: rows(found, list(state.get("devices") or []),
                              str(library.discovery().get("install_id") or ""))


async def refill(library: Any, table: Any, built: list[dict[str, Any]],
                 by_id: dict[str, Any], game_ids: list[str],
                 shape: Callable[[list[dict[str, Any]]], list[dict[str, Any]]] = rows) -> None:
    """Read these games' rows again and swap them in place: the search, the view and
    the rest of the selection stay as they were."""
    for game_id in game_ids:
        library.forget_media(game_id)
    found = await offload.io(library.load_media_rows)
    if table.is_deleted:
        return
    wanted = set(game_ids)
    fresh = shape([row for row in found if str(row["game_id"]) in wanted])
    grid.replace_rows(table, built, by_id, fresh, lambda row: str(row["game_id"]) in wanted)


def build(found: list[dict[str, Any]], library: Any,
          on_select: Callable[[dict | None], Any],
          state: dict[str, Any] | None = None,
          rescan: Callable[[], Any] | None = None) -> None:
    """The media lens: one row per file, and one per file that is not there."""
    state = state if state is not None else {}
    shape = _judged(library, state)
    built = shape(found)
    columns = _with_kind_filter(
        grid.with_art(grid.with_derived_facets(COLUMNS, built), library.thumbnail_look()), library)
    selected: list[dict[str, Any]] = []

    # What is on screen, which on this page is rarely the whole library: the point of
    # the grid is narrowing, and a total that ignores the filter answers a question
    # nobody asked. Held rather than recomputed, because the count arrives from the
    # grid and the selection changes without it.
    on_screen = {"rows": len(built)}

    def said(picked: int) -> str:
        if picked:
            return grid.selection_said(table, picked, t(
                "manager.media.selected", picked=(picked), shown=on_screen["rows"]))
        if on_screen["rows"] == len(built):
            return t("manager.media.media_missing", count=(len(built)),
                     gaps=(sum(1 for row in built if not row.get("present"))))
        return t("manager.media.media", shown=on_screen["rows"], count=len(built))

    with ui.row().classes("w-full items-center gap-2 px-3 py-2 mb-2 shrink-0 "
                                  "manager-panel manager-grid-bar"):
        bar = panel.grid_bar()
        wire_views, _picker, showing, describe = view_control(library, SCOPE, VIEWS,
                                                    _ALL, columns, bar=bar,
                                                    arriving=state.pop("arriving", None),
                                                    art_in_lists=True)
        describe()
        with bar.top, panel.bar_end():
            search = panel.search(t("manager.media.search_media"))
        with bar.bottom, panel.bar_end():
            count = ui.label(said(0)).classes("text-xs manager-label")
            show_selection = panel.selection_menu(
                t("manager.media.media_menu"), lambda: grid.selection(table),
                lambda chosen: acts_on(chosen),
                lambda: table.run_grid_method("deselectAll"))
            if rescan is not None:
                panel.refresh(rescan, t("manager.media.rescan"))

    async def changed(ids: list[str]) -> None:
        await refill(library, table, built, by_id, ids, shape)

    def acts_on(chosen: list[dict[str, Any]]) -> list[panel.Verb]:
        return file_acts.media_acts(library, file_acts.files_of(library, chosen), state,
                                    changed)

    def on_select_rows(picked: list[dict[str, Any]]) -> None:
        selected[:] = picked
        show_selection(bool(picked))
        count.text = said(len(picked))

    def on_context(row: dict | None) -> None:
        if not row:
            menu.clear()
            return
        on = grid.menu_rows(table, row)
        panel.verb_menu(
            menu, str(row["game"]) + " - " + str(row.get("named") or row["label"]) if len(on) == 1
            else t("manager.media.files", count=len(on)),
            panel.for_selection(acts_on(on), len(on)))

    by_id = {row["id"]: row for row in built}
    grid.on_row_focus(SCOPE,
                      lambda event: on_select(by_id.get(grid.focused_row(event))))

    async def on_header_context(col_id: str | None) -> None:
        await grid.header_menu(menu, table, columns, col_id)

    with ui.element("div").classes("w-full grow min-h-0 flex flex-col"):
        table: ui.aggrid = grid.build(columns, built, SCOPE, on_select_rows, on_context,
                                      on_header_context=on_header_context,
                                      view_of=showing)
        menu: ui.context_menu = ui.context_menu()
    search.on_value_change(
        lambda: table.run_grid_method("setGridOption", "quickFilterText",
                                      search.value or ""))

    async def counted() -> None:
        """Whatever narrowed the rows - a filter, the search, a view - lands here.

        `modelUpdated` is the one event every one of them fires, so the count follows
        all of them without each having to remember to say so.
        """
        found = await table.run_grid_method("getDisplayedRowCount")
        on_screen["rows"] = int(found if isinstance(found, int) else len(built))
        count.text = said(len(selected))

    table.on("modelUpdated", counted)

    async def refresh_files(game_id: str) -> None:
        fresh = shape(await offload.io(library.files_of, "media", game_id))
        grid.replace_rows(table, built, by_id, fresh, lambda row: row["game_id"] == game_id)

    state["refresh_files"] = refresh_files
    wire_views(table)
