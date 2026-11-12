"""The games grid: one row per game, with views over the same rows."""

from __future__ import annotations

import inspect
import json
import logging
from collections import Counter
from collections.abc import Callable, Collection, Mapping
from dataclasses import replace
from functools import partial
from typing import Any

from nicegui import run, ui

from common import icons
from common.config.labels import humanize
from common.games import asset_registry, asset_resolver, ties
from common.i18n import t
from common.media.media_specs import media_label_map
from manager.kit import (
    art,
    busy,
    deeplink,
    dropdown,
    game_tables,
    grid,
    media_ownership,
    offload,
    panel,
    renderers,
    report_groups,
    stars,
    states,
    table_features,
    tag_chips,
    thumbnails,
    verbs,
    views,
)
from manager.kit import dialog as frame
from manager.kit.on_page import on_page
from manager.library import (
    collection_adds,
    drop_target,
    game_acts,
    game_states,
    library_add,
    mediaview,
    row_drag,
)
from manager.settings import sections
from manager.settings import settings as settings_page
from manager.workbench import all_settings

logger = logging.getLogger("vpinfe.manager.library.games")

SCOPE = "manager.games.columns"

# The groups are the grain distinction, surfaced in the column picker: what
# the row *is*, what it rolls up, and what it has. Media sits last because it is most
# of the list and least of the use - and last is where it already was, so the grouping
# names a seam that was there rather than moving anything.
_GAME = "word.game"
_ASSETS = "manager.view.assets"
_MEDIA = "manager.view.media"
_FEATURES_VIEW = "manager.game_tables.features"

# What the games resource calls an asset is not always what `asset_registry` calls it -
# `settings` is the table INI, and one `alt_color` covers both Serum and VNI. Named
# here because the two vocabularies have not been reconciled, and a column headed
# "Settings" says nothing about which file it means.
# Only where this surface has to differ from the registry's own label. Everything
# else asks `asset_registry`, which is where a kind's acronyms are cased once.
# `alt_color` and `alt_sound` are here because the games resource still names them
# its own way - one `alt_color` covering the registry's Serum and VNI.
_ASSET_LABELS = {
    "alt_color": "manager.games.alt_color",
    "alt_sound": "manager.games.altsound",
    # The `.directb2s`, which media also calls a backglass - one is the file that
    # drives the second screen, the other is a picture of it. A column header has no
    # group heading beside it, so the two cannot both be "Backglass".
    "backglass": "manager.games.b2s",
    "b2spro": "manager.games.b2spro",
}


def _asset_label(key: str) -> str:
    """The registry's word for a kind, then this surface's override, then humanize."""
    if key in _ASSET_LABELS:
        return t(_ASSET_LABELS[key])
    try:
        return asset_registry.spec_for(key).label
    except KeyError:
        return humanize(key)
# Five stars, filled to the value, and they are the control as well as the picture -
# which is what lets the row menu drop its "Rate" item. A rating is one number a person
# reads at a glance and sets in one click; a menu to open a dialog to pick a number is
# three acts for that.
#
# Clicking the star a rating already stands on clears it. That is how every star widget
# behaves, and the alternative is a sixth control on a column this narrow.
_RATING_CHOICES = ([{"value": 0, "label": t("manager.games.unrated")},
                    {"value": 0.5, "label": "", "mark": f"{stars.STAR} {stars.HALF}"}]
                   + [{"value": n, "label": "", "mark": f"{stars.STAR} {stars.LIT}",
                       "repeat": n} for n in range(1, 6)])


# Delegated once, in the capture phase for the same reason the enlarge is: the cell's
# own click would move the focused row, and rating a row you can see is not a request
# to go and look at it.
COLUMNS = [
    grid.identifier("name", t(_GAME), 280, pinned="left", group=t(_GAME),
                subtitle="said", picture=thumbnails.FIELD, glyph=icons.GAMES,
                help=t("manager.games.game.help"),
                **row_drag.source(row_drag.GAMES)),
    # Always, including 1: it is the only thing saying the row collapses its tables,
    # and it qualifies everything to its right. "Table Count" rather
    # than "Tables", which read as the tables themselves - this is a number about the
    # game, and it belongs with the game's other facts.
    grid.column("table_count", t("word.table_count"), type="numericColumn", group=t(_GAME),
                filter="agNumberColumnFilter", help=t("manager.games.table_count.help")),
    game_states.vps_match().column("vps_match", t("manager.games.vps_match"), group=t(_GAME)),
    game_states.vps_entry().column("vps_shared", t("manager.games.vps_entry"),
                                   group=t(_GAME)),
    game_states.how_matched().column("vps_matched_by", t("manager.games.how_matched"), 160,
                                     group=t(_GAME)),
    # No ROM or Version here: ROM is an asset (`asset_registry`), Version has no
    # game-level meaning, and both were the default table's shown as the game's.
    # Named for whose rating it is, because the tables grid has one too and "Rating"
    # in two places invites the reader to assume they are the same number.
    grid.column("rating", t("manager.games.game_rating"), group=t(_GAME),
                help=t("manager.games.game_rating.help"),
                cellClass="manager-stars-cell",
                **{**grid.choice_filter(_RATING_CHOICES, stars=True),
                   ":cellRenderer": stars.renderer("game")}),
    grid.list_column("tags", t("manager.workbench.tags"), 200, group=t(_GAME),
                     help=t("manager.games.tags.help"), looks=renderers.TAG_LOOKS),
    grid.list_column("collections", t("manager.workbench.collections"), 200,
                     group=t(_GAME), help=t("manager.games.collections.help"),
                     looks=renderers.COLLECTION_LOOKS),
    grid.column("game_type", t("manager.games.type"), group=t(_GAME),
                help=t("manager.games.type.help")),
    # Qualified for the reason Game Rating is: an install has a *frontend* theme and will
    # have a Manager one, so "Themes" in a column header is three things one screen apart.
    # The panel says "Themes" plainly, because a group headed Machine has said which.
    grid.list_column("themes", t("manager.games.game_themes"), 200, group=t(_GAME),
                     help=t("manager.games.themes.help")),
    grid.column("manufacturer", t("word.manufacturer"), group=t(_GAME),
                help=t("help.who_made_it")),
    grid.year_column("year", t("word.year"), group=t(_GAME),
                     help=t("help.year_released")),
    game_states.hidden(game=True).column("hidden", game_tables.FRONTEND, group=t(_GAME)),
    game_states.private().column("private", game_tables.COMMUNITY, group=t(_GAME)),
]

# Presets, not a replacement for choosing columns: a view sets which columns are
# shown, and anything the user changes afterwards is theirs and is what persists.
# Only fields that belong to a game. `rom` and `version` do not: game_repository reads
# them from the default table, so at this grain they report one table's and call them
# the game's. Tables is where that question is answered.
# Which part of a game a view is about, so a row lands there rather than on Details
# and one more click. Only where the view leaves no doubt - the rest open wherever the
# panel was, which is what stepping down a list needs.
VIEW_SECTIONS = {views.builtin_id(_MEDIA): "media"}

# A column that reports a state, and the panel section that changes it. Clicking the
# word is the only thing to do with it, so the click lands where it is changed rather
# than on Details and one more click.
COLUMN_SECTIONS = {"vps_match": "game_details", "vps_shared": "game_details",
                   "hidden": "game_details", "private": "game_details"}

# The grid on its Unmatched filter, as `state["arriving"]`.
UNMATCHED = {"vps_match": {"values": [game_tables.VPS_UNMATCHED]}}
UNMATCHED_VIEW = "manager.view.unmatched"

# Games with no table, such as ones added from Discovery or whose last table was removed.
NO_TABLES_VIEW = "manager.view.no_tables"
NO_TABLES = {"table_count": {"filterType": "number", "type": "equals", "filter": 0}}

DUPLICATES_VIEW = "manager.view.duplicates"
DUPLICATES = {"vps_shared": {"values": [game_tables.VPS_SHARED]}}

# The key in `Opens.keys` naming the built-in view Games should open on.
ARRIVE_VIEW = "arrive_view"


def arrive_on(library: Any, key: str) -> None:
    """Make this built-in the view the Games grid opens on. Writes, so not on the loop."""
    kept, _active = views.stored(library, SCOPE)
    views.remember(library, SCOPE, kept, views.builtin_id(key))

GAME_VIEWS: dict[str, list[str] | views.Preset] = {
    "manager.view.details": views.Preset(
        columns=("name", "table_count", "vps_match", "rating", "tags", "collections",
                 "game_type", "themes", "manufacturer", "year"),
        help=t("manager.view.details.help")),
    # Media and Assets are built from what the library reports it has, so both are
    # filled at render time. Two views, not one: they answer different questions - what
    # a game looks like, and what it needs to play as intended - and a matrix that mixes
    # them is neither.
    _MEDIA: views.Preset(help=t("manager.view.game_media.help")),
    _ASSETS: views.Preset(help=t("manager.view.game_assets.help")),
    NO_TABLES_VIEW: views.Preset(
        columns=("name", "table_count", "vps_match", "game_type", "manufacturer", "year"),
        filters=NO_TABLES, help=t("manager.view.no_tables.help")),
    DUPLICATES_VIEW: views.Preset(
        columns=("name", "table_count", "vps_shared", "manufacturer", "year"),
        filters=DUPLICATES, help=t("manager.view.duplicates.help")),
    UNMATCHED_VIEW: views.Preset(
        columns=("name", "vps_match", "manufacturer", "year"),
        filters=UNMATCHED, help=t("manager.view.unmatched.help")),
    "manager.view.frontend": views.Preset(
        columns=("name", "collections", "hidden", "private"),
        help=t("manager.view.frontend.help")),
}

_ALL = [definition["field"] for definition in COLUMNS]


# What one row is: three grains of the library the user owns - the folder, the
# launchable file inside it, and the asset that resolved for it. Everything here has to
# be something the workbench can answer for, which is what keeps a catalog out.

def asset_columns(keys: list[str]) -> list[dict[str, Any]]:
    """One column per asset kind, availability only.

    A group rather than a single count. What a reader wants is which of them a game
    has, and a number cannot say that.
    """
    labels = {key: _asset_label(key) for key in keys}
    width = max((grid.header_width(label) for label in labels.values()), default=92)
    has = game_states.asset()
    return [has.column(f"asset_{key}", label, width, group=t(_ASSETS),
                       help=t("help.asset_kind", label=label),
                       cellClass=drop_target.ASSET_CELL,
                       cellStyle={"textAlign": "center"})
            for key, label in sorted(labels.items(), key=lambda kv: kv[1].lower())]


# What the funnel offers on a media column, in the legend's own words and marks. The
# value is what the cell holds - "" for missing, which is a blank cell and so a choice
# like any other rather than the one state the filter cannot express.
#
# And no mark on Missing: the cell draws nothing for it, so a glyph here would offer a
# mark the grid never puts on screen. Same reason the legend leaves it out.
_STATE_CHOICES = [
    {"value": "" if key == media_ownership.MISSING
     else media_ownership.tier_for(key).noun,
     "label": t(media_ownership.tier_for(key).noun),
     "mark": ("" if key == media_ownership.MISSING
              else f"manager-mark {media_ownership.tier_for(key).mark}")}
    for key in media_ownership.STATES
]

# Only a per-table asset can be Unused: it is named for this table and a more specific file wins.
_UNUSED_CHOICE = {
    "value": media_ownership.tier_for(media_ownership.UNUSED).noun,
    "label": t(media_ownership.tier_for(media_ownership.UNUSED).noun),
    "mark": f"manager-mark {media_ownership.tier_for(media_ownership.UNUSED).mark}",
}


# Delegated and installed once: NiceGUI strips inline handlers off raw HTML, and a
# renderer runs again on every scroll. The click is taken in the *capture* phase,
# because AG Grid's handler sits between the cell and the document - stopping the event
# on the way back up is too late, and enlarging would also pick the slot.
_CELL_MEDIA = """
if (!window.__hubCellMedia) {
  window.__hubCellMedia = true;
  const clip = (el) => el && el.closest
    ? el.closest('.manager-media-cell')?.querySelector('video') : null;
  document.addEventListener('mouseover', (e) => {
    const v = clip(e.target);
    if (v && v.paused) v.play().catch(() => {});
  });
  document.addEventListener('mouseout', (e) => {
    const v = clip(e.target);
    if (!v) return;
    const cell = e.target.closest('.manager-media-cell');
    if (e.relatedTarget && cell && cell.contains(e.relatedTarget)) return;
    v.pause();
    v.currentTime = 0.1;
  });
  document.addEventListener('click', (e) => {
    const zoom = e.target.closest && e.target.closest('.manager-cell-zoom');
    if (!zoom) return;
    e.stopPropagation();
    emitEvent('hub_media_zoom', {game: zoom.dataset.game, kind: zoom.dataset.kind});
  }, true);
}
"""


def media_columns(kinds: list[str], shown: list[str] | None = None) -> list[dict[str, Any]]:
    """One width for the kinds `shown`, every kind by default, set by the widest line any
    of them needs. A kind not shown is at least as wide, and as wide as its own header
    where that is wider.

    A ragged set of widths reads as noise in a matrix whose cells are all one glyph -
    the columns should scan as a grid, so they are sized together rather than each to
    its own header.
    """
    # The registry's own label, never the key. `media_specs` carries the name a person
    # reads for each kind - acronyms already cased - and deriving one from the key
    # instead is what put "real dmd color" in the column picker.
    labels = media_label_map()
    # Ordered by what is shown, not by the key behind it. Sorting on the key put FSS
    # between Playfield and Playfield Video, and DMD after Real DMD Color - a list that
    # looks unsorted because it is sorted on something the reader cannot see.
    headers = {kind: grid.two_line(labels.get(kind) or humanize(kind))
               for kind in sorted(kinds, key=lambda k: (labels.get(k) or k).lower())}
    sized = kinds if shown is None else shown
    width = max((grid.header_width(header) for kind, header in headers.items()
                 if kind in sized), default=92)
    return [grid.column(f"media_{kind}", header, max(width, grid.header_width(header)),
                        cellClass=f"manager-media-cell {drop_target.MEDIA_CELL}",
                        group=t(_MEDIA),
                        help=t("help.media_kind", label=(header)),
                        **grid.choice_filter(_STATE_CHOICES),
                        **renderers.drawable("mark", "picture"))
            for kind, header in headers.items()]


def media_zoomed(state: dict[str, Any], event: Any) -> Any:
    """A media cell's zoom, answered by the grid drawn last."""
    zoom = state.get("media_zoom")
    return zoom(event) if zoom else None


def rated(state: dict[str, Any], event: Any) -> Any:
    """A star clicked in a grid, written by the grid drawn last - Games or Tables."""
    rate = state.get("rate")
    return rate(event) if rate else None


def _collected(kinds: list[str], library: Any) -> list[str]:
    kept = library.kept_kinds()["media"]
    return [kind for kind in kinds if kind in kept]


def grid_columns(rows: list[dict[str, Any]], kinds: list[str],
                 library: Any) -> list[dict[str, Any]]:
    """Every column the Games grid declares: a media column for every kind the library
    collects, whether or not any game has one yet. `kinds` are the ones some game has."""
    return grid.with_art(grid.with_derived_facets(COLUMNS, rows), library.thumbnail_look()) \
        + asset_columns(folder_asset_keys(library)) \
        + media_columns(sorted(library.kept_kinds()["media"]),
                        shown=_collected(kinds, library))


def grid_presets(kinds: list[str], library: Any) -> dict[str, list[str] | views.Preset]:
    """The built-in views, the media one showing the collected kinds some game has."""
    return {**GAME_VIEWS,
            _MEDIA: views.Preset(
                columns=("name", *[f"media_{kind}" for kind in _collected(kinds, library)]),
                help=t("manager.view.game_media.help")),
            _ASSETS: views.Preset(
                columns=("name", *[f"asset_{key}" for key in folder_asset_keys(library)]),
                help=t("manager.view.game_assets.help"))}


def _nothing_yet(words: str, library: Any, state: dict[str, Any],
                 rescan: Callable[[], Any] | None, view: str) -> None:
    """What a page shows in place of a grid with no rows: where rows come from, a way to
    add some, and a way to pick up ones that arrived by hand. A link's filter is spent
    here, not held for the first row that appears."""
    for key in ("arriving", "launcher", "sets", "reported", NEW_KEY, UNFILLED_KEY, LACKING_KEY,
                UPDATES_KEY):
        state.pop(key, None)
    sections.metadata_line(library, state)
    panel.facts(ui, [panel.intro(t(words, page=t("manager.section.library_folders")))])
    library_add.add_action(library, state, view, empty=True)
    if rescan is not None:
        panel.action(t("manager.games.rescan"), rescan, icon=verbs.REFRESH,
                     hint=t("manager.games.rescan_hint"))()


def _narrow(table: Any, search: Any, state: dict[str, Any]) -> None:
    """Narrow the grid to the search box. `state["searching"]` tells the page a redraw
    would empty the box."""
    state["searching"] = bool(search.value)
    table.run_grid_method("setGridOption", "quickFilterText", search.value or "")


def build(rows: list[dict[str, Any]], kinds: list[str], library: Any,
          on_select: Callable[[dict | None], Any],
          state: dict[str, Any] | None = None,
          rerender: Callable[[], None] | None = None,
          rescan: Callable[[], Any] | None = None,
          on_selection: Callable[[list[str]], Any] | None = None) -> None:
    state = state if state is not None else {}
    state["ticked"] = 0
    state["searching"] = False
    if not rows:
        _nothing_yet("manager.games.empty", library, state, rescan, "games")
        return
    tag_chips.install(library.tag_looks())
    renderers.install_collection_looks(library.smart_collections(), ui.context.client)
    only_new = state.pop(NEW_KEY, None)
    unfilled = state.pop(UNFILLED_KEY, None)
    lacking = state.pop(LACKING_KEY, None)
    updating = state.pop(UPDATES_KEY, None)
    rows = stamp_updates(
        stamp_lacking(stamp_unfilled(stamp_new(rows, only_new), unfilled), lacking), updating)
    columns = grid_columns(rows, kinds, library)
    if only_new is not None:
        columns.append(new_column())
    if unfilled is not None:
        columns.append(unfilled_column())
    if lacking is not None:
        columns.append(lacking_column())
    if updating is not None:
        columns.append(updates_column())
    all_fields = [definition["field"] for definition in columns]
    selected: list[dict[str, Any]] = []

    sections.metadata_line(library, state)
    with ui.row().classes("w-full items-center gap-2 px-3 py-2 mb-2 shrink-0 "
                                  "manager-panel manager-grid-bar"):
        bar = panel.grid_bar()
        presets = grid_presets(kinds, library)
        def annotate() -> None:
            """The key to the marks, on the line that says what the view is for.

            Read from the vocabulary rather than restated here, and each entry carries
            its own explanation on hover - a legend that names a state without saying
            what it means is half a legend.
            """
            with ui.row().classes("items-center gap-3 no-wrap shrink-0 text-xs "
                                  "opacity-60 manager-tier-key") as legend:
                for key in media_ownership.LEGEND:
                    tier = media_ownership.tier_for(key)
                    with ui.row().classes("items-center gap-1 no-wrap") \
                            .tooltip(t(tier.why)):
                        ui.element("span").classes(f"manager-mark {tier.mark}")
                        ui.label(t(tier.noun))
            legend.bind_visibility_from(view_picker, "value",
                                        lambda value: value == views.builtin_id(_MEDIA))

        narrowed = str(state.get("collection") or "")
        # Taken once: a filter a link asked for is not put back on every redraw after.
        arriving = state.pop("arriving", None) \
            or ({"collections": {"values": [narrowed]}} if narrowed else None)
        if only_new is not None or unfilled is not None or lacking is not None \
                or updating is not None:
            arriving = {}
            if only_new is not None:
                arriving[NEW_COLUMN] = {"values": [new_label()]}
            if unfilled is not None:
                arriving[UNFILLED_COLUMN] = {"values": [unfilled_label()]}
            if lacking is not None:
                arriving[LACKING_COLUMN] = {"values": [lacking_label()]}
            if updating is not None:
                arriving[UPDATES_COLUMN] = {"values": [updates_label()]}
        wire_views, view_picker, showing, describe = view_control(
            library, SCOPE, presets, all_fields, columns, bar=bar, annotate=annotate,
            arriving=arriving, art_in_lists=True)
        describe()
        with bar.top, panel.bar_end():
            search = panel.search(t("manager.games.search_games"))
        with bar.bottom, panel.bar_end():
            # The selection count sits with the total: it is the same fact - how much am I
            # looking at - and it costs no vertical space of its own.
            count = ui.label(t("manager.games.games", count=len(rows))) \
                .classes("text-xs manager-label")
            show_edit = panel.edit_selected("manager.games.edit_games",
                                            lambda: edit_these())
            show_selection = panel.selection_menu(
                t("manager.games.games_menu"), lambda: grid.selection(table),
                lambda chosen: game_verbs(chosen, adding["known"]),
                lambda: table.run_grid_method("deselectAll"), ready=lambda: read_collections())
            library_add.add_action(library, state, "games", empty=False)
            if rescan is not None:
                panel.refresh(rescan, t("manager.games.rescan"))

    shown: dict[str, int] = {"rows": len(rows)}
    adding: dict[str, collection_adds.Read | None] = {"known": None}

    def said() -> str:
        if selected:
            return grid.selection_said(table, len(selected), t(
                "manager.games.selected", picked=len(selected), shown=shown["rows"]))
        if shown["rows"] != len(rows):
            return t("manager.games.games_of", shown=shown["rows"], count=len(rows))
        return t("manager.games.games", count=len(rows))

    # Two or more ticked and the panel is about them; a row clicked shows that row and
    # leaves the ticks, and the count is the way back.
    viewing: dict[str, Any] = {"on": False, "ids": set()}

    def linked() -> None:
        """The count, as a link back to the selection while the panel shows a row."""
        back = on_selection is not None and len(selected) >= 2 and not viewing["on"]
        count.classes(add="manager-link cursor-pointer" if back else None,
                      remove=None if back else "manager-link cursor-pointer")
        if back:
            count._props["title"] = t("manager.games.selection_back")
        else:
            count._props.pop("title", None)
        count.update()

    async def follow_selection() -> None:
        ids = {str(one["id"]) for one in selected}
        if on_selection is None:
            return
        if len(ids) >= 2:
            if ids != viewing["ids"]:
                viewing["ids"], viewing["on"] = ids, True
                await read_collections()
                await on_selection(sorted(ids))
        else:
            was, viewing["ids"], viewing["on"] = viewing["on"], set(), False
            if was:
                answer = on_select(by_id.get(str(state.get("game") or "")))
                if inspect.isawaitable(answer):
                    await answer
        linked()

    async def show_again() -> None:
        if on_selection is not None and len(selected) >= 2:
            viewing["ids"], viewing["on"] = {str(one["id"]) for one in selected}, True
            await read_collections()
            await on_selection(sorted(viewing["ids"]))
            linked()

    count.on("click", show_again)

    async def edit_these() -> None:
        await show_again()
        if callable(opened := state.get("open_pane")):
            opened()

    async def on_select_rows(rows_selected: list[dict[str, Any]]) -> None:
        selected[:] = rows_selected
        state["ticked"] = len(rows_selected)
        show_selection(bool(rows_selected))
        show_edit(len(rows_selected) if on_selection is not None else 0)
        count.text = said()
        await follow_selection()

    by_id = {row["id"]: row for row in rows}

    def focused(event: Any) -> Any:
        """The row the keyboard or a click landed on, and the part of it in question.

        A media cell is a question about one slot, so the panel opens there. A view only
        names its section. Focus never reopens a panel somebody shut; a press does.
        """
        if viewing["on"] and grid.ticked(event):
            return None
        row = by_id.get(grid.focused_row(event))
        column = grid.focused_column(event)
        if row and column.startswith("media_"):
            state["section"] = "media"
            picked = state.setdefault("slot", {"kind": None})
            picked["kind"] = column[len("media_"):]
            picked.pop("file", None)
        elif row and state.get("section") != deeplink.COLLAPSED:
            section = (COLUMN_SECTIONS.get(column)
                       or VIEW_SECTIONS.get(str(view_picker.value or "")))
            if section:
                state["section"] = section
        viewing["on"] = False
        linked()
        return on_select(row)

    def zoom_media(event: Any) -> None:
        """Enlarge the file a cell is showing - the same viewer the media map opens."""
        args = event.args if isinstance(event.args, dict) else {}
        game_id, kind = str(args.get("game") or ""), str(args.get("kind") or "")
        if game_id and kind:
            version = (library.media.get(game_id) or {}).get(kind, {}).get("version")
            mediaview.open_viewer(art.media(game_id, kind, version=version), kind,
                                  media_label_map().get(kind, kind))

    async def panel_follows(rated: set[str]) -> None:
        """A rating given in the grid shows in the panel beside it. The panel reads the
        library when it is drawn, so drawing it again is all it takes."""
        if table.is_deleted:
            return
        if viewing["on"] and on_selection is not None and viewing["ids"] & rated:
            await on_selection(sorted(viewing["ids"]))
        elif str(state.get("game") or "") in rated:
            answer = on_select(by_id.get(str(state["game"])))
            if inspect.isawaitable(answer):
                await answer

    rate_games = stars.game_rater(rows, by_id, lambda: table, library, then=panel_follows)
    rate_row = stars.rating_handler(rows, by_id, lambda: table, library,
                                    then=panel_follows)

    grid.on_row_focus(SCOPE, focused)
    state["media_zoom"] = zoom_media
    state["rate"] = rate_row
    state["rate_games"] = rate_games
    ui.run_javascript(_CELL_MEDIA)
    ui.run_javascript(stars.CLICK_JS)

    async def on_context(row: dict | None) -> None:
        if row is None:
            _fill_menu()
            return
        _fill_menu(on=grid.menu_rows(table, row),
                   known=await collection_adds.read(library, narrowed_to()))

    def narrowed_to() -> str:
        """The collection the grid is filtered to, while it is one."""
        return str(state.get("collection") or "")

    async def read_collections() -> None:
        adding["known"] = await collection_adds.read(library, narrowed_to())

    def offer(games: list[dict[str, Any]], subject: str) -> collection_adds.Offer:
        return collection_adds.Offer(
            library, [collection_adds.Row(str(one["id"])) for one in games],
            collection_adds.GAMES, subject,
            then=partial(refresh_games, [str(one["id"]) for one in games]),
            narrowed=narrowed_to())

    def game_verbs(chosen: list[dict[str, Any]],
                   known: collection_adds.Read | None) -> list[panel.Verb]:
        subject = str(chosen[0].get("name") or "") if len(chosen) == 1 else ""
        return game_acts.game_acts(
            library, chosen, state, refresh_games, rate=rate_games,
            collections=game_acts.collections_of(offer(chosen, subject), known))

    async def on_header_context(col_id: str | None) -> None:
        _fill_menu(col_id=col_id, pinning=await grid.pinning(table, col_id))

    def _fill_menu(col_id: str | None = None, pinning: grid.Pinning | None = None,
                   on: list[dict[str, Any]] | None = None,
                   known: collection_adds.Read | None = None) -> None:
        """One menu, filled for whatever was right-clicked.

        Two menus cannot both hang off the grid wrapper, and the wrapper sees every
        right-click - so one menu, filled from what was under the pointer. Otherwise the
        row menu opens over a header, offering to launch a column.
        """
        context_menu.clear()
        if pinning is not None:
            with context_menu:
                grid.column_menu(context_menu, table, columns, col_id, pinning)
        elif on:
            header = (str(on[0].get("name") or "") if len(on) == 1
                      else t("manager.games.games", count=len(on)))
            panel.verb_menu(context_menu, header,
                            panel.for_selection(game_verbs(on, known), len(on)))

    # The menu hangs off a wrapper, not off the grid: ui.aggrid's Vue template is a bare
    # <div> with no slot, so a child of it is never rendered and the menu silently does
    # not exist. Anchoring to an element that does render its children is the fix.
    # The wrapper has to carry the flex chain too, not just anchor the menu: as a plain
    # block it collapsed to its content height and the grid inside it never filled.
    with ui.element("div").classes("w-full grow min-h-0 flex flex-col"):
        table: ui.aggrid = grid.build(columns, rows, SCOPE, on_select_rows, on_context,
                                      on_header_context, view_of=showing)
        context_menu: ui.context_menu = ui.context_menu()

    async def rows_again(ids: set[str]) -> list[dict[str, Any]]:
        """These games' rows, read as a full draw reads them."""
        await run.io_bound(library.load_games_grid, True)
        renderers.install_collection_looks(library.smart_collections(), table.client)
        return [row for row in await offload.io(library.game_rows) if row.get("id") in ids]

    async def refresh_game(game_id: str) -> None:
        """Put one game's row back on screen after something changed it.

        A transaction rather than a page rebuild. A rebuild reads as the grid flashing:
        scroll position, focus and the open panel all go, for a write that touched one
        row. `getRowId` is the row's id, so a transaction leaves all three alone.
        """
        fresh = await rows_again({game_id})
        if fresh:
            grid.transact(table, rows, {"update": fresh}, by_id)

    state["refresh_game"] = refresh_game

    async def refresh_games(ids: list[str]) -> None:
        """The Collections column of the rows an add or its Undo touched, and the panel
        where it is showing one of them: its rail counts them. A game the library no
        longer holds leaves the grid. Nothing where the grid has gone."""
        if table.is_deleted or state.get("view") != "games":
            return
        wanted = set(ids)
        fresh = await rows_again(wanted)
        left = {str(one["id"]) for one in fresh}
        grid.transact(table, rows, {
            "update": fresh,
            "remove": [{"id": one} for one in wanted - left if one in by_id]}, by_id)
        viewing["ids"] -= wanted - left
        viewing["on"] = viewing["on"] and bool(viewing["ids"])
        selected[:] = [one for one in selected if str(one["id"]) in by_id]
        if viewing["on"] and on_selection is not None and viewing["ids"]:
            selected[:] = [by_id[str(one["id"])] for one in selected if str(one["id"]) in by_id]
            await on_selection(sorted(viewing["ids"]))
        elif state.get("game") in wanted:
            answer = on_select(by_id.get(str(state["game"])))
            if inspect.isawaitable(answer):
                await answer

    state["refresh_games"] = refresh_games
    state["selection_verbs"] = lambda: panel.for_selection(
        game_verbs(list(selected), adding["known"]), len(selected))

    async def counted() -> None:
        seen = await table.run_grid_method("getDisplayedRowCount")
        shown["rows"] = seen if isinstance(seen, int) else len(rows)
        count.text = said()

    @on_page
    async def follow_the_filter() -> None:
        """The address names the collection the grid is narrowed to, while it is one."""
        try:
            model = await table.run_grid_method("getFilterModel") or {}
        except TimeoutError:
            return
        values = list((model.get("collections") or {}).get("values") or [])
        one = values[0] if len(values) == 1 and values[0] else None
        if one != (state.get("collection") or None):
            state["collection"] = one
            deeplink.sync(state)

    table.on("modelUpdated", counted)
    table.on("filterChanged", follow_the_filter, args=[])
    wire_views(table)
    search.on_value_change(lambda: _narrow(table, search, state))


# A tick where it is true and nothing where it is not, so a column of them is scanned
# rather than read. The value stays boolean underneath, which is what lets the column
# sort and filter - a column of "Yes"/"" strings would sort alphabetically and filter
# as text.
# The community text filter does not read a boolean well: it offers a select reading
# "Choose one / True / False" - "Choose one" twice, since the placeholder is also the
# first option, and the two words that follow are the wire's, not a person's. Two
# choices in the column's own terms instead.
# One column per feature. Not used draws nothing at all, so what a reader sees down a
# column is the tables that have it - and, where a scan is mid-flight, the ones nobody
# has read yet. A tick for the plain yes, the same as the asset and media columns; the
# shaped circle is kept for the state that is neither yes nor no.
_FEATURE_MARKS = {
    key: {"mark": table_features.state_for(key).mark,
          "glyph": table_features.state_for(key).glyph,
          "cls": table_features.state_for(key).glyph_class,
          "why": states.line(table_features.state_for(key).noun,
                             table_features.state_for(key).why)}
    for key in table_features.STATES
}

_FEATURE_RENDERER = (
    "params => {"
    " const m = " + json.dumps(_FEATURE_MARKS) + ";"
    " const v = params.value;"
    " const t = m[v === null || v === undefined ? '" + table_features.UNKNOWN
    + "' : (v ? '" + table_features.IN_SCRIPT + "' : '" + table_features.UNUSED + "')];"
    " if (!t) return '';"
    " if (t.glyph) return '<span class=\"' + t.cls + '\" title=\"' + t.why + '\">'"
    " + t.glyph + '</span>';"
    " if (!t.mark) return '';"
    " return '<span class=\"manager-mark ' + t.mark + '\" title=\"' + t.why"
    " + '\"></span>'; }"
)

# The value is a boolean and null, so the funnel offers the three words rather than a
# text box somebody has to know to type "true" into. Null arrives as "" - the component
# reads an absent value that way, which is what makes "not parsed yet" pickable.
_FEATURE_CHOICES: list[dict[str, Any]] = [
    {"value": value, "label": table_features.state_for(key).noun,
     "tip": states.line(table_features.state_for(key).noun, table_features.state_for(key).why),
     **({"glyph": table_features.state_for(key).glyph,
         "glyphClass": table_features.state_for(key).glyph_class}
        if table_features.state_for(key).glyph else {})}
    for key, value in ((table_features.IN_SCRIPT, True), (table_features.UNKNOWN, ""),
                       (table_features.UNUSED, False))
]

_FEATURES = "manager.games.features"

FEATURE_COLUMNS = [
    grid.column(f"feature_{key}", label, group=t(_FEATURES),
                help="\n".join([t("help.table_feature", label=(label)),
                                *(choice["tip"] for choice in _FEATURE_CHOICES)]),
                cellClass="manager-media-cell",
                **{**grid.choice_filter(_FEATURE_CHOICES),
                   ":cellRenderer": _FEATURE_RENDERER})
    for key, label in table_features.LABELS.items()
]


# One row per launchable file. The game's name leads, because a filename alone does not
# say what the thing is - and it is pinned, because scrolling right to see which game a
# row belongs to is the failure this subject exists to fix.
_TABLE = "manager.games.table"
_IN_PLAY = "manager.games.library"

# Marks the tables that were deliberately pointed somewhere, so a reader can see what
# changing the default will and will not move. Only on those: a mark on every row would
# say nothing, and following the default is the ordinary case.
SET_HERE_MARK = "● "

# The mark drawn as the picker's dot: the accent, or amber where the table falls back.
_LAUNCHER_CELL = (
    "params => {" + renderers._ESCAPE +
    f" const mark = {json.dumps(SET_HERE_MARK)}; const said = String(params.value || '');"
    " if (!said.startsWith(mark)) return esc(said);"
    " const off = (params.data || {}).launcher_falls_back;"
    f" const tip = off ? {json.dumps(t('manager.games.launcher_falls_back'))}"
    f" : {json.dumps(t('manager.workbench.chosen_here'))};"
    " return '<span class=\"manager-mark manager-mark--full manager-named-mark'"
    " + (off ? ' manager-named-mark--off' : '') + '\" title=\"' + esc(tip) + '\"></span>'"
    " + esc(said.slice(mark.length)); }"
)
_LAUNCHER_DRAWN: dict[str, Any] = {":cellRenderer": _LAUNCHER_CELL}

# What a table's settings file changes for it, by kind. The cell holds the kind, which a
# filter and a saved view keep, and draws `settings_said`.
_OWN_SETTINGS, _POINT_OF_VIEW, _FROM_GAME = "own", "point_of_view", "from_game"
_GAME_SCOPE = t("manager.workbench.this_game")
_SETTINGS_CHOICES = [
    {"value": _OWN_SETTINGS, "label": t("manager.games.filter_own")},
    {"value": _POINT_OF_VIEW, "label": t("asset.kind.pov.label")},
    {"value": _FROM_GAME, "label": t("manager.games.settings_from", scope=_GAME_SCOPE)},
    {"value": "", "label": t("manager.games.filter_same")},
]
_SETTINGS_DRAWN: dict[str, Any] = {
    ":valueFormatter": "params => (params.data || {}).settings_said || ''",
    ":comparator": "(a, b, nodeA, nodeB) => {"
                   " const n = node => ((node && node.data) || {}).settings_count || 0;"
                   " return n(nodeA) - n(nodeB) || String(a).localeCompare(String(b)); }",
}

# Whether a table's release is a mod, which the filter keeps, drawn as what it is a mod of.
_MOD = "mod"
_MOD_DRAWN: dict[str, Any] = {
    ":valueFormatter": "params => (params.data || {}).mod_of_said || ''",
    ":comparator": "(a, b, nodeA, nodeB, descending) => {"
                   " const said = node => ((node && node.data) || {}).mod_of_said || '';"
                   " const x = said(nodeA), y = said(nodeB);"
                   " if (!x || !y) { if (x === y) return 0; const last = x ? -1 : 1;"
                   "  return descending ? -last : last; }"
                   " return x.localeCompare(y); }",
}

# Which settings those are, by key.
OWN_SETTINGS_COLUMN = "own_settings"

# The group of a kept report a table's game is in; a column only while a report asked for it.
REPORTED_COLUMN = "reported"

# The ids that joined an Overview row since it was dismissed, as `state[NEW_KEY]`, and the
# column that lets a grid narrow to them.
NEW_KEY = "only_new"
NEW_COLUMN = "new_since"

# The ids of the games with a blank slot that no source has a file for, as
# `state[UNFILLED_KEY]`, and the column that lets the Games grid narrow to them.
UNFILLED_KEY = "unfilled_games"
UNFILLED_COLUMN = "unfilled"

# The ids of the games a Home tab of missing media counts, as `state[LACKING_KEY]`, and the
# column that lets the Games grid narrow to them.
LACKING_KEY = "lacking_games"
LACKING_COLUMN = "lacking"

# The ids of the games Home's Updates counts, as `state[UPDATES_KEY]`, and the column that
# lets the Games grid narrow to them.
UPDATES_KEY = "updated_games"
UPDATES_COLUMN = "updates"

# The game's rating on a table's row: `rating` there is the table's own.
GAME_RATING = "game_rating"

# A column whose answer a panel section holds, so focusing it opens there.
TABLE_COLUMN_SECTIONS = {"settings": "table_settings", OWN_SETTINGS_COLUMN: "table_settings"}

# The Tables grid on the tables whose ROM is missing, as `state["arriving"]`.
ROM_MISSING = {"rom_state": {"values": [game_states.NOT_INSTALLED]}}
UNTIED = {"release": {"values": [ties.UNTIED]}}

TABLE_COLUMNS = [
    grid.identifier("game", t(_TABLE), 270, pinned="left", group=t(_GAME),
                subtitle=("said", "", "said_built"),
                picture=thumbnails.FIELD, glyph=icons.TABLES,
                help=t("manager.games.table_game.help"),
                **row_drag.source(row_drag.TABLES)),
    grid.column("version", t("word.version"), group=t(_TABLE),
                help=t("manager.games.version.help")),
    grid.column("on_vps", t("manager.games.on_vps"), group=t(_TABLE),
                help=t("manager.games.on_vps.help")),
    game_states.release().column("release", t("manager.games.release"), 150, group=t(_TABLE)),
    grid.column("mod_of", t("manager.games.mod_of"), 200, group=t(_TABLE),
                help=t("manager.games.mod_of.help"),
                **{**grid.choice_filter([
                    {"value": _MOD, "label": t("manager.games.mod")},
                    {"value": "", "label": t("manager.games.no_mod_known")}],
                    formatted=True), **_MOD_DRAWN}),
    grid.column("author", t("word.author"), 160, group=t(_TABLE),
                help=t("manager.games.author.help")),
    grid.column("rom", t("manager.games.rom"), 110, group=t(_TABLE),
                help=t("manager.games.rom.help")),
    game_states.rom().column("rom_state", t("manager.games.rom_state"), 130, group=t(_TABLE)),
    grid.column("launcher", t("manager.games.launcher"), group=t(_TABLE),
                help=t("manager.games.launcher.help"),
                **_LAUNCHER_DRAWN),
    grid.column("settings", t("manager.workbench.table_settings"), 155, group=t(_TABLE),
                help=t("manager.games.settings.help"),
                **{**grid.choice_filter(_SETTINGS_CHOICES, formatted=True),
                   **_SETTINGS_DRAWN}),
    grid.list_column(OWN_SETTINGS_COLUMN, t("manager.games.own_settings"), 240, group=t(_TABLE),
                     help=t("manager.games.own_settings.help"),
                     looks=renderers.SETTING_LOOKS),
    grid.column(GAME_RATING, t("manager.games.game_rating"), group=t(_GAME),
                help=t("manager.games.game_rating.help"),
                cellClass="manager-stars-cell",
                **{**grid.choice_filter(_RATING_CHOICES, stars=True),
                   ":cellRenderer": stars.renderer("game", on_table=True)}),
    grid.column("rating", t("manager.games.table_rating"), group=t(_TABLE),
                help=t("manager.games.table_rating.help"),
                cellClass="manager-stars-cell",
                **{**grid.choice_filter(_RATING_CHOICES, stars=True),
                   ":cellRenderer": stars.renderer("table")}),
    grid.list_column("tags", t("manager.workbench.tags"), 200, group=t(_TABLE),
                     help=t("manager.games.table_tags.help"), looks=renderers.TAG_LOOKS),
    # One column per fact rather than one word folding three together. "Status" cannot
    # stay one column anyway - has an update, missing its rom and the rest are all
    # status - and folded, a table that is both the default and hidden reads as only
    # one of them. Each of these sorts and filters on its own, which is what a list is
    # for. A summary column can be built later, deliberately, from these.
    # Not a tick: a locked default and an automatic one are different facts.
    game_states.default().column("default_state", game_tables.DEFAULT_LABEL,
                                 group=t(_IN_PLAY)),
    # Each column's own words, not a generic pair: "Hidden: Yes" is a question about a
    # question, where "Hidden / Offered" is the fact and its opposite.
    game_states.hidden(game=False).column("hidden", t("word.hidden"), group=t(_IN_PLAY)),
    game_states.missing().column("missing", t("manager.games.missing"), group=t(_IN_PLAY)),
    # Last and widest: it is the identifier of record, and the part that tells two
    # tables of one game apart sits at its end.
    grid.column("filename", game_tables.FILE, 420, group=t(_TABLE),
                help=t("manager.games.filename.help")),
    *FEATURE_COLUMNS,
]

# The kinds `resolve_for_table` answers for, one file per table. A game's folder holds the
# rest - a PUP pack belongs to the game and has no per-table answer.
TABLE_ASSET_KEYS = asset_resolver.PER_TABLE_KINDS


def folder_asset_keys(library: Any) -> list[str]:
    """The asset kinds a game's folder holds whole, which the per-table kinds are not."""
    return [key for key in library.asset_keys() if key not in TABLE_ASSET_KEYS]


def table_asset_keys(library: Any) -> list[str]:
    """`TABLE_ASSET_KEYS` the library collects, in the same order."""
    kept = library.kept_kinds()["asset"]
    return [key for key in TABLE_ASSET_KEYS if key in kept]


def table_asset_columns(keys: list[str]) -> list[dict[str, Any]]:
    """One column per asset kind, for the tables grid, saying *whose* file answers.

    Not the games grid's tick. These kinds resolve per table, so the honest answer here
    has three values, not two: this table's own file, the game's, or none.
    `conventions.md`: a binary fact is a tick, a fact with more answers is a shaped
    circle, and these carry the media map's own marks because it is the same question
    about a different file.

    The games grid reports these from a folder scan, so a game whose only `.directb2s`
    is named for one table reads "has one" while its sibling launches without a
    backglass. This is the grain that answers it.
    """
    labels = {key: _asset_label(key) for key in keys}
    headers = {key: grid.two_line(label)
               for key, label in sorted(labels.items(), key=lambda kv: kv[1].lower())}
    width = max((grid.header_width(header) for header in headers.values()), default=92)
    return [grid.column(f"asset_{key}", header, width,
                        cellClass=f"manager-media-cell {drop_target.ASSET_CELL}",
                        group=t(_ASSETS),
                        help=t("help.table_asset_kind", label=(header)),
                        **grid.choice_filter([*_STATE_CHOICES, _UNUSED_CHOICE]),
                        **renderers.drawable("mark"))
            for key, header in headers.items()]


TABLE_VIEWS: dict[str, list[str] | views.Preset] = {
    # Default and Hidden ride in every preset: which table a game offers and whether it
    # is offered at all are the questions this view exists to answer, and a preset that
    # hides them is a list of files.
    #
    # Named for the workbench groups. "Files" and "Play" were one question asked twice -
    # both were app and on-disk state, which is whether this thing runs - so they are
    # Launch, once.
    "manager.game_tables.table_file": views.Preset(
        columns=("game", "default_state", "hidden", "filename"),
        help=t("manager.view.table_file.help")),
    "manager.game_tables.launch": views.Preset(
        columns=("game", "rom", "rom_state", "launcher", "settings",
                 "default_state", "hidden", "missing", "filename"),
        help=t("manager.view.launch.help")),
    "manager.view.untied": views.Preset(
        columns=("game", "version", "release", "default_state", "hidden", "filename"),
        filters={"release": {"values": [ties.UNTIED]}},
        help=t("manager.view.untied.help")),
    "manager.view.ratings": views.Preset(
        columns=("game", GAME_RATING, "rating", "tags"),
        help=t("manager.view.ratings.help")),
    # Its own view, not seven more columns on Launch: this is a matrix, the same shape as
    # Media on the games grid, and Launch stays a list somebody can read across.
    _FEATURES_VIEW: views.Preset(
        columns=("game", *[f"feature_{key}" for key in table_features.LABELS]),
        help=t("manager.view.features.help")),
}


def _table_label(row: dict[str, Any]) -> str:
    """A row menu's heading: the game, then which of its tables this is.

    The pair, on one line, because a menu header has one. `subject` owns the second
    half so this cannot drift from the header, the collection row and the panel.
    """
    said = game_tables.table_name(row)
    game = row.get("game") or ""
    return f"{game}{game_tables.JOIN}{said}" if game and said else (game or said)


def _launcher_word(row: dict[str, Any]) -> str:
    """What plays this table, marked where the table chose it rather than inherited it."""
    name = str(row.get("launcher_name") or "")
    if not name:
        return ""
    return f"{SET_HERE_MARK}{name}" if row.get("launcher_set_here") else name


def _resolved_word(entry: dict[str, Any] | None) -> str:
    """A per-table asset resolution as the tier word a cell draws from. Empty for none,
    which is what a blank cell means everywhere else in these matrices."""
    tier = media_ownership.for_resolution((entry or {}).get("resolution"))
    return "" if tier.key == media_ownership.MISSING else tier.noun


def row_transaction(showing: dict[str, dict[str, Any]], game_id: str,
                    fresh: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """What to tell the grid after one game's rows were rewritten.

    Which of add, update and remove is needed is decided by comparing what came back
    with what is on screen, so one call answers for a table arriving, one changing and
    one going without being told which happened - and a write that does two of those at
    once, which giving a game its first table does: a row arrives and the default moves.

    Only that game's rows are considered on either side. Every other row on screen
    belongs to a game this write did not touch, and offering them as removals would
    empty the grid.
    """
    here = {row_id for row_id, row in showing.items()
            if row.get("game_id") == game_id}
    came_back = {row["id"] for row in fresh}
    transaction: dict[str, list[dict[str, Any]]] = {}
    arrived = [row for row in fresh if row["id"] not in here]
    changed = [row for row in fresh if row["id"] in here]
    gone = [{"id": row_id} for row_id in sorted(here - came_back)]
    if arrived:
        transaction["add"] = arrived
    if changed:
        transaction["update"] = changed
    if gone:
        transaction["remove"] = gone
    return transaction


def add_index(built: list[dict[str, Any]], game_id: str,
              transaction: dict[str, Any]) -> None:
    """Set the transaction's `addIndex` so what arrived follows its game's rows. Without
    it an unsorted grid puts an add after the last row of all."""
    if not transaction.get("add"):
        return
    at = next((index for index, row in enumerate(built) if row.get("game_id") == game_id),
              len(built))
    transaction["addIndex"] = at + len(transaction.get("update", ()))


def _rom_state(row: dict[str, Any]) -> str:
    """Blank where a table names no ROM; unknown where VPinFE cannot check, which is not a fault."""
    if not row.get("rom"):
        return ""
    installed = row.get("rom_installed")
    if installed is None:
        return game_states.UNKNOWN
    return game_states.INSTALLED if installed else game_states.NOT_INSTALLED


def table_rows(rows: list[dict[str, Any]], art: bool = False) -> list[dict[str, Any]]:
    """The API's tables, flattened for a grid, each with its thumbnail while `art`.

    `hidden` and `missing` hold their state's token, or "" where the table is offered and
    on disk: `missing` is the API's `available` turned round, so the notable state is the
    one with a word.

    `rows` holds every table of each game it covers: whether a game has a choice is
    counted from them.
    """
    held = Counter(str(row.get("game_id") or "") for row in rows)
    bases = ties.patch_bases(rows)
    return [{**row,
             # A table's own rating is offered only where its game has a choice of tables.
             "own_marks": held[str(row.get("game_id") or "")] > 1,
             "hidden": game_states.HIDDEN if row.get("hidden") else "",
             "missing": game_states.MISSING if not row.get("available", True) else "",
             # The file where there is one, and the name its program knows it by where
             # there is not. A blank cell in the column that identifies the row would
             # read as a fault rather than as a different kind of thing.
             "filename": (row.get("filename") or row.get("key")
                          or row.get("reference") or ""),
             # Whose file answers for this table, in the media map's own words - the
             # cell holds the word and the mark is drawn from it, the way media cells
             # do. Only the per-table kinds; the rest belong to the folder.
             **{f"asset_{key}": _resolved_word((row.get("assets") or {}).get(key))
                for key in TABLE_ASSET_KEYS},
             "rom_state": _rom_state(row),
             # What the table is named by, so a column and the name beside it agree.
             "version": game_tables.named_version_and_authors(row)[0],
             "author": game_tables.named_version_and_authors(row)[1],
             "on_vps": str((row.get("source") or {}).get("version") or ""),
             "release": ties.release_state(row, bases),
             **_mod_cell((row.get("source") or {}).get("mod_of")),
             "tags": [*((row.get("user") or {}).get("tags") or []),
                      *(row.get("derived_tags") or [])],
             "said": game_tables.made(row),
             "said_built": game_tables.table_name(row),
             "said_built_file": game_tables.names_a_file(row),
             # The name, not the id: `app_name` says why, and the column has to sort
             # and filter on what a reader can see rather than on what is stored.
             # The name plus a mark for chosen against inherited. One cell, because the
             # answer is one fact - what plays this table - and which of the two it is
             # only matters as a qualifier on it.
             "launcher": _launcher_word(row),
             "launcher_falls_back": bool(row.get("launcher_falls_back")),
             **_settings_cell(row),
             # One field per feature: a grid column reads a field, and the payload's
             # nested dict would have every column reaching into the same object.
             # `.get` rather than a default of False - a table nobody parsed answers
             # null for all seven, and inventing False here would lose that.
             **{f"feature_{key}": (row.get("features") or {}).get(key)
                for key in table_features.LABELS},
             "default_state": _default_cell(row, held[str(row.get("game_id") or "")]),
             **({thumbnails.FIELD: thumbnails.address(row)} if art else {})}
            for row in rows]


def stamp_report(built: list[dict[str, Any]],
                 group: report_groups.Group | None) -> list[dict[str, Any]]:
    """Each row says which group of the report it belongs to, in the Import column."""
    if group is not None:
        ids = set(group.game_ids)
        for row in built:
            row[REPORTED_COLUMN] = [group.label] if row.get("game_id") in ids else []
    return built


def new_label() -> str:
    return t("manager.games.new_group")


def new_column() -> dict[str, Any]:
    return grid.list_column(NEW_COLUMN, t("manager.games.new_column"), 120,
                            help=t("manager.games.new_column.help"))


def unfilled_label() -> str:
    return t("manager.games.unfilled_group")


def unfilled_column() -> dict[str, Any]:
    return grid.list_column(UNFILLED_COLUMN, t("manager.games.unfilled_column"), 120,
                            help=t("manager.games.unfilled_column.help"))


def lacking_label() -> str:
    return t("manager.games.lacking_group")


def lacking_column() -> dict[str, Any]:
    return grid.list_column(LACKING_COLUMN, t("manager.games.lacking_column"), 120,
                            help=t("manager.games.lacking_column.help"))


def updates_label() -> str:
    return t("manager.games.updates_group")


def updates_column() -> dict[str, Any]:
    return grid.list_column(UPDATES_COLUMN, t("manager.games.updates_column"), 120,
                            help=t("manager.games.updates_column.help"))


def _stamped(built: list[dict[str, Any]], ids: Collection[str] | None, column: str,
             label: str) -> list[dict[str, Any]]:
    """Copies, so the library's own rows carry no mark."""
    if ids is None:
        return built
    return [{**row, column: [label] if str(row.get("id")) in ids else []} for row in built]


def stamp_new(built: list[dict[str, Any]],
              ids: Collection[str] | None) -> list[dict[str, Any]]:
    """Each row says whether it joined a dismissed row's count since, in the New column."""
    return _stamped(built, ids, NEW_COLUMN, new_label())


def stamp_unfilled(built: list[dict[str, Any]],
                   ids: Collection[str] | None) -> list[dict[str, Any]]:
    """Each row says whether no source has a file for a slot it is missing, in the Get Media
    column."""
    return _stamped(built, ids, UNFILLED_COLUMN, unfilled_label())


def stamp_lacking(built: list[dict[str, Any]],
                  ids: Collection[str] | None) -> list[dict[str, Any]]:
    """Each row says whether a Home tab of missing media counts it, in the Missing column."""
    return _stamped(built, ids, LACKING_COLUMN, lacking_label())


def stamp_updates(built: list[dict[str, Any]],
                  ids: Collection[str] | None) -> list[dict[str, Any]]:
    """Each row says whether Home's Updates counts it, in the Updates column."""
    return _stamped(built, ids, UPDATES_COLUMN, updates_label())


def _mod_cell(mod: dict[str, Any] | None) -> dict[str, Any]:
    said = game_tables.mod_of_said(mod)
    return {"mod_of": _MOD if said else "", "mod_of_said": said}


def _settings_cell(row: dict[str, Any]) -> dict[str, Any]:
    """What the table's settings file changes for it: the kind, its words, and how many.
    None where the launcher playing it runs a program that keeps no settings."""
    configurable = bool(row.get("launcher_app_configurable"))
    here = int(row.get("launcher_settings_here") or 0) if configurable else 0
    from_game = int(row.get("launcher_settings_from_folder") or 0) if configurable else 0
    if here == 1 and row.get("launcher_point_of_view"):
        kind, said = _POINT_OF_VIEW, t("asset.kind.pov.label")
    elif here:
        kind, said = _OWN_SETTINGS, t("manager.games.settings_own", count=here)
    elif from_game:
        kind, said = _FROM_GAME, t("manager.games.settings_from_game", count=from_game,
                                   scope=_GAME_SCOPE)
    else:
        kind, said = "", ""
    return {"settings": kind, "settings_said": said, "settings_count": here or from_game,
            OWN_SETTINGS_COLUMN: list(row.get("launcher_settings_keys") or []) if configurable
            else []}


def own_setting_names(groups: Mapping[str, list]) -> dict[str, str]:
    """What each Own Settings chip says, by what a row lists: each launcher's settings
    by name, and a saved camera as the Settings column says it."""
    return {**{key: said for held in groups.values()
               for key, said in all_settings.setting_names(held).items()},
            _POINT_OF_VIEW: t("asset.kind.pov.label")}


def setting_their_own(rows: list[dict[str, Any]], launcher_id: str,
                      keys: str) -> dict[str, Any] | None:
    """A filter on the launcher's tables that set any of `keys`, comma separated, for
    themselves, as an address asks for one. None where it names no launcher a table
    uses, or no setting."""
    name = next((str(row.get("launcher_name") or "") for row in rows
                 if launcher_id and row.get("launcher") == launcher_id), "")
    wanted = [key for key in keys.split(",") if key]
    if not name or not wanted:
        return None
    return {"launcher": {"filterType": "text", "operator": "OR", "conditions": [
                {"filterType": "text", "type": "equals", "filter": said}
                for said in (name, f"{SET_HERE_MARK}{name}")]},
            OWN_SETTINGS_COLUMN: {"values": wanted}}


def _default_cell(row: dict[str, Any], held: int) -> str:
    kind = str(row.get("default_kind") or "")
    return game_states.ONLY_TABLE if kind == game_tables.DERIVED and held < 2 else kind


def _tables_said(built: list[dict[str, Any]], shown: int) -> str:
    if shown == len(built):
        return t("manager.games.tables_games", count=len(built),
                 games=len({row["game_id"] for row in built}))
    return t("manager.games.tables_of", shown=shown, count=len(built))


def build_tables(rows: list[dict[str, Any]], library: Any,
                 on_select: Callable[[dict | None], Any],
                 state: dict[str, Any] | None = None,
                 rerender: Callable[[], None] | None = None,
                 rescan: Callable[[], Any] | None = None,
                 on_selection: Callable[[list[str]], Any] | None = None) -> None:
    """The library seen by launchable file rather than by folder.

    Its own builder rather than a branch inside the games grid: the columns, the views
    and the row identity are all different, and the two sharing one function would be a
    long argument about which subject each line is for.
    """
    state = state if state is not None else {}
    state["ticked"] = 0
    state["searching"] = False
    if not rows:
        _nothing_yet("manager.games.empty_tables", library, state, rescan, "tables")
        return
    tag_chips.install(library.tag_looks())
    renderers.install_setting_names(own_setting_names(library.setting_groups()))
    look = library.thumbnail_look()
    reported = state.pop("reported", None)
    only_new = state.pop(NEW_KEY, None)
    built = stamp_new(stamp_report(table_rows(rows, look is not None), reported), only_new)
    # Taken once, as the Games grid takes its own.
    arriving = setting_their_own(rows, str(state.pop("launcher", None) or ""),
                                 str(state.pop("sets", None) or ""))
    arriving = state.pop("arriving", None) or arriving
    if reported is not None:
        arriving = {REPORTED_COLUMN: {"values": [reported.label]}}
    if only_new is not None:
        arriving = {NEW_COLUMN: {"values": [new_label()]}}
    table_columns = grid.with_art(TABLE_COLUMNS, look) \
        + table_asset_columns(table_asset_keys(library))
    if reported is not None:
        table_columns.append(grid.list_column(
            REPORTED_COLUMN, t("manager.games.import_group"), 160,
            help=t("manager.games.import_group.help")))
    if only_new is not None:
        table_columns.append(new_column())
    fields = [definition["field"] for definition in table_columns]

    sections.metadata_line(library, state)
    sections.no_tables_line(library, state)
    with ui.row().classes("w-full items-center gap-2 px-3 py-2 mb-2 shrink-0 "
                                  "manager-panel manager-grid-bar"):
        bar = panel.grid_bar()
        presets = {**TABLE_VIEWS,
                   _ASSETS: views.Preset(
                       columns=("game", "rom", "rom_state",
                                *[f"asset_{key}" for key in table_asset_keys(library)]),
                       help=t("manager.view.table_assets.help"))}

        def annotate() -> None:
            """Every glyph column carries a key, including the state drawn as nothing,
            which is the one a reader is least able to work out from the grid.

            The states this library actually has, though: a table is unparsed only
            between discovery finding it and the enrichment job reaching it, so a line
            explaining a mark nobody can see is spent on every visit for a window most
            people never look through.
            """
            with ui.row().classes("items-center gap-3 no-wrap shrink-0 text-xs "
                                  "opacity-60 manager-tier-key") as legend:
                # `shown`, not `state`: this function's own `state` is the page's, and a
                # loop variable by that name quietly replaced it for everything after the
                # legend - which nothing noticed until something further down wrote to it.
                for key in table_features.states_in(built):
                    shown = table_features.state_for(key)
                    with ui.row().classes("items-center gap-1 no-wrap") \
                            .tooltip(shown.why):
                        if shown.glyph:
                            ui.label(shown.glyph).classes(shown.glyph_class)
                        else:
                            ui.element("span").classes(
                                f"manager-mark {shown.mark}".strip()
                                if shown.mark else "manager-mark-none")
                        ui.label(shown.noun)
            legend.bind_visibility_from(view_picker, "value",
                                        lambda value: value == views.builtin_id(_FEATURES_VIEW))

        wire_views, view_picker, showing, describe = view_control(
            library, f"{SCOPE}.tables", presets, fields, table_columns, bar=bar,
            annotate=annotate, arriving=arriving, art_in_lists=True)
        describe()
        with bar.top, panel.bar_end():
            search = panel.search(t("manager.games.search_tables"))
        with bar.bottom, panel.bar_end():
            count = ui.label(_tables_said(built, len(built))) \
                .classes("text-xs manager-label")
            show_edit = panel.edit_selected("manager.games.edit_tables",
                                            lambda: edit_these())
            show_selection = panel.selection_menu(
                t("manager.games.tables_menu"), lambda: grid.selection(table),
                lambda chosen: table_verbs(chosen, adding["known"]),
                lambda: table.run_grid_method("deselectAll"),
                ready=lambda: read_collections())
            library_add.add_action(library, state, "tables", empty=False)
            if rescan is not None:
                panel.refresh(rescan, t("manager.games.rescan"))

    # The workbench follows the focused row, the same way it does under Games - focus
    # rather than selection, so arrowing down the list is a sweep and the checkboxes
    # stay whatever a bulk action left them.
    by_id = {row["id"]: row for row in built}

    async def panel_follows(rated: set[str]) -> None:
        """A rating given in the grid shows in the panel beside it, whichever of the
        game's own or one of its tables' ratings it was. Drawing the panel again is all
        it takes: it reads the library when it is drawn."""
        if table.is_deleted:
            return
        if viewing["on"] and on_selection is not None:
            await on_selection(sorted(viewing["ids"]))
        elif str(state.get("game") or "") in rated:
            answer = on_select(by_id.get(str(state.get("table") or "")))
            if inspect.isawaitable(answer):
                await answer

    rate_row = stars.rating_handler(built, by_id, lambda: table, library, GAME_RATING,
                                    then=panel_follows)

    def focused(event: Any) -> Any:
        if viewing["on"] and grid.ticked(event):
            return None
        row = by_id.get(grid.focused_row(event))
        section = TABLE_COLUMN_SECTIONS.get(grid.focused_column(event))
        if row and section and state.get("section") != deeplink.COLLAPSED:
            state["section"] = section
        viewing["on"] = False
        linked()
        return on_select(row)

    grid.on_row_focus(f"{SCOPE}.tables", focused)
    state["rate"] = rate_row
    ui.run_javascript(stars.CLICK_JS)

    adding: dict[str, collection_adds.Read | None] = {"known": None}
    selected: list[dict[str, Any]] = []
    displayed: dict[str, int] = {"rows": len(built)}

    def said() -> str:
        if selected:
            return grid.selection_said(table, len(selected), t(
                "manager.games.selected", picked=len(selected), shown=displayed["rows"]))
        return _tables_said(built, displayed["rows"])

    # Two or more ticked and the panel is about them; a row clicked shows that row and
    # leaves the ticks, and the count is the way back.
    viewing: dict[str, Any] = {"on": False, "ids": set()}

    def linked() -> None:
        """The count, as a link back to the selection while the panel shows a row."""
        back = on_selection is not None and len(selected) >= 2 and not viewing["on"]
        count.classes(add="manager-link cursor-pointer" if back else None,
                      remove=None if back else "manager-link cursor-pointer")
        if back:
            count._props["title"] = t("manager.games.selection_back")
        else:
            count._props.pop("title", None)
        count.update()

    async def follow_selection() -> None:
        ids = {str(one["id"]) for one in selected}
        if on_selection is None:
            return
        if len(ids) >= 2:
            if ids != viewing["ids"]:
                viewing["ids"], viewing["on"] = ids, True
                await read_collections()
                await on_selection(sorted(ids))
        else:
            was, viewing["ids"], viewing["on"] = viewing["on"], set(), False
            if was:
                answer = on_select(by_id.get(str(state.get("table") or "")))
                if inspect.isawaitable(answer):
                    await answer
        linked()

    async def show_again() -> None:
        if on_selection is not None and len(selected) >= 2:
            viewing["ids"], viewing["on"] = {str(one["id"]) for one in selected}, True
            await read_collections()
            await on_selection(sorted(viewing["ids"]))
            linked()

    count.on("click", show_again)

    async def edit_these() -> None:
        await show_again()
        if callable(opened := state.get("open_pane")):
            opened()

    async def on_select_rows(rows_selected: list[dict[str, Any]]) -> None:
        selected[:] = rows_selected
        state["ticked"] = len(rows_selected)
        show_selection(bool(rows_selected))
        show_edit(len(rows_selected) if on_selection is not None else 0)
        count.text = said()
        await follow_selection()

    async def on_context(row: dict | None) -> None:
        if row is None:
            _fill()
            return
        _fill(on=grid.menu_rows(table, row), known=await collection_adds.read(library))

    async def read_collections() -> None:
        adding["known"] = await collection_adds.read(library)

    def offer(tables: list[dict[str, Any]], subject: str) -> collection_adds.Offer:
        return collection_adds.Offer(
            library, [collection_adds.Row(str(one["game_id"]), str(one["id"]))
                      for one in tables],
            collection_adds.TABLES, subject,
            then=partial(after, [str(one["game_id"]) for one in tables], False))

    def table_verbs(chosen: list[dict[str, Any]],
                    known: collection_adds.Read | None) -> list[panel.Verb]:
        siblings: dict[str, list[dict[str, Any]]] = {}
        for item in by_id.values():
            siblings.setdefault(str(item.get("game_id")), []).append(item)
        targets = [game_acts.Target(str(one["game_id"]), str(one.get("game") or ""), one,
                                    siblings.get(str(one["game_id"]), [one]))
                   for one in chosen]
        subject = _table_label(chosen[0]) if len(chosen) == 1 else ""
        return game_acts.table_acts(
            library, targets, after, state,
            game_acts.collections_of(offer(chosen, subject), known))

    async def on_header_context(col_id: str | None) -> None:
        _fill(col_id=col_id, pinning=await grid.pinning(table, col_id))

    with ui.element("div").classes("w-full grow min-h-0 flex flex-col"):
        # The selection feeds the bulk menu, never `on_select`: that one is about the
        # focused row, and the grid hands this handler the whole selected list.
        table = grid.build(table_columns, built, f"{SCOPE}.tables", on_select_rows,
                           on_context=on_context,
                           on_header_context=on_header_context, view_of=showing)
        menu: ui.context_menu = ui.context_menu()

    async def counted() -> None:
        seen = await table.run_grid_method("getDisplayedRowCount")
        displayed["rows"] = seen if isinstance(seen, int) else len(built)
        count.text = said()

    table.on("modelUpdated", counted)

    async def refresh_game(game_id: str) -> None:
        """Put one game's rows back after something changed them.

        Every row of that game, not the one acted on: a game can gain a table or lose
        one, and the default moves between them in the same write. Which of the three a
        row needs is decided by comparing what came back with what is showing, so this
        answers for an add, an edit and a removal without being told which it was.
        """
        await refresh_tables([game_id])

    async def refresh_tables(game_ids: list[str]) -> None:
        """Several games' rows, put back from one read of the tables."""
        # From the by-file lens, which is what the grid was built from. The game's own
        # sub-resource describes a table and not where it sits in a library, so it
        # carries no game name, manufacturer, year or resolved rom: patching from it
        # blanks four columns on exactly the rows just acted on.
        rows_now = await offload.io(library.load_tables)
        for game_id in set(game_ids):
            fresh = stamp_new(
                stamp_report(table_rows([item for item in rows_now
                                         if item.get("game_id") == game_id],
                                        look is not None), reported), only_new)
            transaction = row_transaction(by_id, game_id, fresh)
            add_index(built, game_id, transaction)
            if transaction:
                grid.transact(table, built, transaction, by_id)
        selected[:] = [by_id[str(one["id"])] for one in selected if str(one["id"]) in by_id]

    async def open_table(table_id: str) -> None:
        """Focus a table's row, which opens it in the panel; a row a filter hides is
        opened without it."""
        if table_id in by_id and await grid.focus_row(table, table_id, "game"):
            return
        opened = on_select(by_id.get(table_id))
        if inspect.isawaitable(opened):
            await opened

    state["refresh_game"] = refresh_game
    state["refresh_tables"] = refresh_tables
    state["selection_verbs"] = lambda: panel.for_selection(
        table_verbs(list(selected), adding["known"]), len(selected))
    state["keep_selected"] = lambda ids: grid.keep_only(table, ids)
    state["open_table"] = open_table

    async def after(game_ids: list[str], gone: bool) -> None:
        """Put only what changed back on screen. Rebuilding the page reads as the grid
        flashing: scroll position, focus and the open panel all go, for a write that
        touched one game's rows."""
        if table.is_deleted or state.get("view") != "tables":
            return
        for game_id in game_ids:
            await refresh_game(game_id)
        if viewing["on"] and on_selection is not None:
            await on_selection(sorted(viewing["ids"]))
            return
        # The panel is about one of these rows and would otherwise still show what
        # the write changed. `on_select` redraws the workbench alone, not the page.
        answer = on_select(by_id.get(str(state.get("table") or "")))
        if inspect.isawaitable(answer):
            await answer

    def _fill(col_id: str | None = None, pinning: grid.Pinning | None = None,
              on: list[dict[str, Any]] | None = None,
              known: collection_adds.Read | None = None) -> None:
        """One menu, filled for whatever was right-clicked.

        Two menus cannot both hang off the grid wrapper, and the wrapper sees every
        right-click - so the header's entries and the row's share this one.
        """
        menu.clear()
        if pinning is not None:
            with menu:
                grid.column_menu(menu, table, table_columns, col_id, pinning)
        elif on:
            header = (_table_label(on[0]) if len(on) == 1
                      else t("manager.games.tables", count=len(on)))
            panel.verb_menu(menu, header, panel.for_selection(table_verbs(on, known), len(on)))

    wire_views(table)
    search.on_value_change(lambda: _narrow(table, search, state))


def _by_group(columns: list[dict[str, Any]]) -> list[tuple[str, list[dict]]]:
    """The columns bucketed by their group, groups in the order first declared.

    By meaning rather than by position: File is deliberately the last column in the
    Tables grid and is still a fact about the table, so a picker that mirrored column
    order would print the Table heading twice with the play states between them. What
    the picker answers is *which columns exist*; where they sit is the grid's business
    and the user drags that themselves.
    """
    order: list[str] = []
    groups: dict[str, list[dict]] = {}
    for definition in columns:
        heading = str(definition.get(grid.GROUP_KEY) or "")
        if heading not in groups:
            order.append(heading)
            groups[heading] = []
        groups[heading].append(definition)
    return [(heading, groups[heading]) for heading in order]


def view_control(library: Any, scope: str,
                 presets: Mapping[str, list[str] | views.Preset],
                 all_fields: list[str],
                 columns: list[dict[str, Any]], *, bar: Any,
                 annotate: Callable[[], None] | None = None,
                 arriving: dict[str, Any] | None = None,
                 art_in_lists: bool = False,
                 opened: views.View | None = None,
                 left_opened: Callable[[], None] | None = None,
                 unfiltered: frozenset[str] = frozenset()) -> Any:
    """One control for how the rows are presented: which view, and what is in it.

    Built here in the toolbar and wired once the grid exists, because the widgets have
    to sit above the grid and the behavior needs the grid to talk to.

    Returns `(wire, picker, showing, describe)`. Call `wire(table)` once the grid
    exists, inside a wrapper of its own that is held busy until the view is on it; the
    picker is handed back so a caller can hang a binding off which view is
    showing, `showing()` answers the same question for the grid's own geometry, and
    `describe()` draws the view's own line - called last, so it takes the row's full
    width and falls to the foot of the bar.

    `arriving` is a filter model the address asked for. It stands in for the filters of
    the first view put on the grid, and each column it filters is shown.

    `art_in_lists` is for a grid that lists games, tables or collections by name.

    `opened` is a view the page was sent to (`views.opened`): listed after the built-ins,
    showing first, and kept out of the user's own views until they save it. `left_opened`
    is called once another view is picked or the opened one is saved, so a redraw of the
    page does not bring it back.

    `unfiltered` names columns whose filters are not put on the grid yet, whether the view
    is a built-in or a saved one: a grid whose rows are blank there would be emptied by
    them. The views keep those filters, and a view saved from the grid takes them from the
    view it started from, so what is stored never loses one.
    """
    custom, active = views.stored(library, scope)
    # Before the grid is built from `columns`, which is the same list.
    starts = [opened] if opened is not None else []
    grid.room_for_sort_order(columns, [view.sort for view in views.builtins(presets) + starts])

    def listing() -> list[Any]:
        return views.builtins(presets) + starts + held["custom"]

    held: dict[str, Any] = {"custom": custom}
    known = listing()
    if opened is not None:
        active = opened.id
    elif active not in {view.id for view in known}:
        active = known[0].id
    held.update({"views": known, "active": active, "modified": False, "drawing": {},
                 "baseline": None, "over": None, "on_screen": None,
                 "whole": None})

    top = bar.top
    with top:
        picker = panel.DescribedSelect(
            {view.id: _view_name(view) for view in known}, value=active,
            label=t("word.view"),
            describes={_view_name(v): (v.help or "") for v in known},
            look=dropdown.OUTLINED).classes("w-52 manager-view-picker")
    # Inside the button, not beside it: a q-menu anchors to its parent, and as a
    # sibling this one anchored to the toolbar row and opened 726px away.
    with top:
        menu_button = ui.button(icon=verbs.TUNE).props("flat dense round size=sm") \
            .classes("manager-view-menu") \
            .tooltip(t("manager.games.view_menu"))
        with menu_button:
            menu = ui.menu()

    def current() -> Any:
        return next(v for v in held["views"] if v.id == held["active"])

    def _reoption(active: str) -> None:
        """The picker's options, and the line each one carries."""
        picker.describes = {_view_name(v): (v.help or "") for v in held["views"]}
        picker.set_options({v.id: _view_name(v) for v in held["views"]}, value=active)

    said: dict[str, Any] = {"box": None}

    def describe() -> None:
        """The view's own line, at the foot of the bar.

        Drawn for a saved view whether or not anything has been written: it is the
        control you write it in, and one that appears only once filled is one nobody
        finds.
        """
        with bar.bottom, ui.row().classes("items-center gap-3 no-wrap manager-view-line"):
            said["box"] = ui.element("div").classes("min-w-0 manager-view-purpose")
            if annotate is not None:
                annotate()
        _show_purpose()

    def _show_purpose() -> None:
        box = said.get("box")
        if box is None:
            return
        box.clear()
        with box:
            ui.label(getattr(current(), "help", "") or "") \
                .classes("truncate manager-view-purpose-text")

    def wire(table: ui.aggrid) -> None:
        def draw(shown: list[str]) -> None:
            """Put the drawing in `held` on screen, and the row height it needs."""
            renderers.draw_as(scope, held["drawing"])
            height = renderers.row_px(held["drawing"], shown, grid.base_row_px(columns))
            table.run_grid_method("setGridOption", "rowHeight", height)
            # The same number twice: the option lays the row out, and `--ag-row-height`
            # is what a cell's line box is derived from. Set on the node - `.style()`
            # would make nicegui rebuild the grid and lose the view.
            ui.run_javascript(
                f"getElement({table.id}).$el.style.setProperty("
                f"'--ag-row-height', '{height}px')")
            # Rows already drawn this way are left standing: a switch between views that
            # present rows alike would otherwise blink every visible row.
            if held["on_screen"] != (dict(held["drawing"]), height):
                held["on_screen"] = (dict(held["drawing"]), height)
                table.run_grid_method("redrawRows")

        @on_page
        async def apply(view: Any, over: dict[str, Any] | None = None) -> None:
            """Put a view on the grid: which columns, sorted how, filtered to what -
            and then this view's own widths, which the grid does not carry across a
            switch."""
            _show_purpose()
            held["whole"] = views.arrived(view, over, all_fields)
            baseline = views.without_filters(held["whole"], unfiltered)
            held["baseline"], held["over"] = baseline, over
            wanted = views.visible_columns(baseline, all_fields)
            # Leaving the grid as it is keeps it usable, and the notify says why. The
            # old fallback showed every column instead, which reads as the view
            # misbehaving rather than as a view that has gone stale.
            if wanted is None:
                ui.notify(t("manager.games.view_stale", name=(view.name)),
                        type="warning")
                await _refresh()
                return
            table.run_grid_method("setColumnsVisible", wanted, True)
            table.run_grid_method("setColumnsVisible",
                                  [f for f in all_fields if f not in wanted], False)
            # defaultState clears the sort on every column this view does not name,
            # or an old sort would survive a switch and the view would be a lie.
            table.run_grid_method("applyColumnState",
                                  {"state": list(view.sort),
                                   "defaultState": {"sort": None}})
            # Always set, even to nothing: a view that filters nothing has to clear
            # what the last one filtered, which is what makes picking one a way out
            # rather than a hope.
            table.run_grid_method("setFilterModel", baseline.filters or None)
            held["drawing"] = dict(view.drawn)
            draw(wanted)
            await grid.apply_layout(table, scope, columns, lambda: held["active"])
            await _refresh()

        async def _seen() -> tuple:
            """What the grid is actually showing, in the terms a view is written in.

            AG Grid's own generated columns - the checkbox, chiefly - are in the state
            and are nobody's view, so they are dropped. Left in, every view reads as
            modified the moment it is applied.
            """
            state = [entry for entry in
                     (await table.run_grid_method("getColumnState") or [])
                     if not str(entry.get("colId", "")).startswith("ag-Grid-")]
            model = await table.run_grid_method("getFilterModel") or {}
            shown = tuple(entry["colId"] for entry in state if not entry.get("hide"))
            return shown, tuple(state), model

        async def _refresh() -> None:
            try:
                shown, sort, model = await _seen()
            except TimeoutError:
                # Asking the grid what it is showing is a round trip to the browser,
                # and this one is only to decide whether to mark the view modified.
                # A browser that is busy, or a tab being closed, is not worth failing
                # the thing that called us - the next interaction asks again.
                logger.debug("manager: the grid did not answer in time; "
                             "leaving the view mark as it is")
                return
            changed = views.differs(held["baseline"] or current(), shown, sort, model,
                                    held["drawing"])
            # On the picker rather than beside it: the drift is a fact about the view
            # that is selected, so it belongs to the control that names it.
            picker.props(add="suffix=modified") if changed \
                else picker.props(remove="suffix")
            # Recorded, not pushed at controls. The menu is built when it opens, so it
            # reads this - which is one fewer thing to keep in step than a set of
            # buttons that show and hide themselves.
            held["modified"] = changed

        async def keep_views(active: str) -> None:
            """Write the user's views. Off the loop - this is an HTTP call, and the
            client refuses one made from a page's own handler."""
            await run.io_bound(views.remember, library, scope, held["custom"], active)

        @on_page
        async def pick(view_id: str) -> None:
            held["active"] = view_id
            if view_id != views.OPENED and left_opened:
                left_opened()
            await keep_views(view_id)
            await apply(current())

        @on_page
        async def save(name: str, said: str = "") -> None:
            shown, sort, model = await _seen()
            # What the grid was not given is carried from the view this one started from.
            if held["whole"] is not None:
                model = {**{name: kept for name, kept in held["whole"].filters.items()
                            if name in unfiltered}, **model}
            # Saving over a name replaces that view and keeps its id, so anything
            # keyed to it survives. A new name mints a new id - never a slug of the
            # name, or renaming would orphan the view's own geometry.
            wanted = name.strip()
            standing = next((v for v in held["custom"]
                             if v.name.strip().lower() == wanted.lower()), None)
            view = views.View(id=standing.id if standing else views.mint_id(),
                              name=wanted,
                              builtin=False, columns=shown,
                              sort=tuple(e for e in sort if e.get("sort")),
                              filters=model, help=said.strip(),
                              drawn=dict(held["drawing"]))
            held["custom"] = [v for v in held["custom"] if v.id != view.id] + [view]
            if held["active"] == views.OPENED:
                starts.clear()
                if left_opened:
                    left_opened()
            held["views"] = listing()
            held["active"] = view.id
            held["whole"], held["over"] = view, None
            held["baseline"] = views.without_filters(view, unfiltered)
            await keep_views(view.id)
            _reoption(view.id)
            await _refresh()
            ui.notify(t("manager.games.saved_view", name=(view.name)), type="positive")

        async def rename(name: str, said: str = "") -> None:
            """The view's words, changed without touching what it holds."""
            view = current()
            kept = [v if v.id != view.id
                    else replace(v, name=name.strip(), help=said.strip())
                    for v in held["custom"]]
            held["custom"] = kept
            held["views"] = listing()
            await keep_views(held["active"])
            _reoption(held["active"])
            _show_purpose()

        @on_page
        async def delete() -> None:
            view = current()
            if view.builtin:
                return
            held["custom"] = [v for v in held["custom"] if v.id != view.id]
            held["views"] = listing()
            held["active"] = held["views"][0].id
            await keep_views(held["active"])
            _reoption(held["active"])
            await apply(current())

        async def fill_menu() -> None:
            """Everything about how this view looks, in one menu.

            Built when it opens rather than kept in step: which columns are showing is
            the grid's to answer, and a checklist rebuilt from it cannot go stale.
            """
            column_state = await table.run_grid_method("getColumnState") or []
            hidden = {entry.get("colId") for entry in column_state if entry.get("hide")}
            view = current()
            menu.clear()
            with menu:
                ui.menu_item(t("manager.games.save"), lambda: _ask_name(
                    save, named=view.name if view.id == views.OPENED else "")) \
                    .classes("manager-menu-item")
                # Only where they mean something: there is nothing to revert to until
                # the screen has drifted, and nothing to delete unless it is the
                # user's own view.
                if held["modified"]:
                    ui.menu_item(t("manager.games.revert"),
                                 lambda: apply(current(), held["over"])) \
                        .classes("manager-menu-item")
                if not view.builtin:
                    ui.menu_item(t("manager.games.rename_view"),
                                 lambda v=view: _ask_name(
                                     rename, named=v.name, said=v.help,
                                     title=t("manager.games.rename_view"))) \
                        .classes("manager-menu-item")
                if not view.builtin and not held["modified"]:
                    ui.menu_item(t("manager.games.delete_view"), delete) \
                        .classes("manager-menu-item manager-menu-danger")
                if art_in_lists:
                    ui.separator()
                    panel.menu_link(t("manager.games.thumbnails"),
                                    to=settings_page.address_for("manager"))
                drawings(hidden)
                ui.separator()
                # An explicit column: the menu lays its children out inline otherwise,
                # so twenty checkboxes wrap into a paragraph rather than a list.
                with ui.column().classes("gap-0 w-full py-1 items-stretch"):
                    for heading, group in _by_group(columns):
                        # Named groups only. A grid whose columns declare none reads
                        # as one list, which is right when there are eight of them.
                        if heading:
                            ui.item_label(heading).props("header") \
                                .classes("manager-menu-header")
                        for definition in group:
                            field = definition["field"]
                            label = str(definition.get("headerName") or field) \
                                .replace("\n", " ")
                            box = ui.checkbox(label, value=field not in hidden,
                                        on_change=lambda event, f=field:
                                        table.run_grid_method("setColumnsVisible",
                                                              [f], event.value)) \
                                .props("dense").classes("manager-menu-item w-full")
                            explains = str(definition.get("headerTooltip") or "")
                            if explains:
                                with box:
                                    ui.tooltip(explains).classes("manager-menu-tip")

        def drawings(hidden: set[str]) -> None:
            """How each shown group of columns is drawn, where it can be drawn more
            than one way."""
            shown = [d for d in columns if d["field"] not in hidden]
            for heading, group in _by_group(shown):
                offered = [renderers.choices(d) for d in group if len(renderers.choices(d)) > 1]
                if not offered:
                    continue
                names = offered[0]
                fields = [d["field"] for d in group if renderers.choices(d) == names]
                now = held["drawing"].get(fields[0], names[0])
                ui.item_label(t("manager.games.show_as", group=heading or t("word.columns"))) \
                    .props("header").classes("manager-menu-header")
                def pick(event: Any, f: list[str] = fields, n: tuple[str, ...] = names) -> Any:
                    return redraw(f, n, str(event.value))
                panel.segmented({name: t(renderers.REGISTRY[name].label) for name in names},
                                now, pick).classes("q-mx-sm q-mb-xs")

        @on_page
        async def redraw(fields: list[str], names: tuple[str, ...], chosen: str) -> None:
            for one in fields:
                if chosen == names[0]:
                    held["drawing"].pop(one, None)
                else:
                    held["drawing"][one] = chosen
            state = await table.run_grid_method("getColumnState") or []
            draw([e["colId"] for e in state if not e.get("hide")])
            await _refresh()

        menu_button.on_click(fill_menu)
        picker.on_value_change(lambda event: pick(event.value))
        # The grid reports its own changes; the marker follows them rather than being
        # recomputed on a timer that would outlive the grid.
        for event in ("columnVisible", "sortChanged", "filterChanged"):
            table.on(event, lambda: _refresh(), args=[])
        wrapper = table.parent_slot.parent if table.parent_slot is not None else table
        busy.fill(wrapper, lambda: apply(current(), arriving))

    return wire, picker, lambda: held["active"], describe


def _view_name(view: Any) -> str:
    """As typed, or as `views.builtins` read it. Looking it up again here would build a
    key out of the translation."""
    return str(view.name or "")


def _ask_name(save: Callable[..., Any], *, named: str = "",
              said: str = "", title: str = "") -> None:
    """Name a view and say what it is for, in one place."""
    held: dict[str, Any] = {}

    def draw_name() -> None:
        held["name"] = frame.field(named)
        held["name"].on_value_change(cleared)

    def cleared() -> None:
        held["name"].props["error"] = False

    async def keep() -> None:
        name = held["name"]
        if not (name.value or "").strip():
            name.props["error"] = True
            name.props["error-message"] = t("said.needs_name")
            return
        box.close()
        await save(name.value, held["purpose"].value or "")

    with frame.opened(title or t("manager.games.save_view"), ignore_outside_click=True) as box:
        panel.facts(ui, [
            (t("word.name"), draw_name),
            (t("manager.workbench.description"),
             lambda: held.update(purpose=frame.field(
                 said, placeholder=t("manager.games.view_hint"))))])
        with frame.footer():
            frame.cancel(box.close)
            go = frame.answer(t("word.save"), keep, icon=verbs.SAVE)
    frame.focus(box, held["name"])
    frame.enter_presses(go)
    box.open()
