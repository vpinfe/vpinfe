"""Discovery's front page, drawn from what `GET /discovery/home` answers: the games of the
library with a later version of something they hold, what VPS added lately, the games the
library holds that lack a kind VPS lists, the games VPS lists that it has none of, the ones
among them that share a title with a game it holds, the games of the library that need a
look, the ones with no VPS match that look like an entry, the ones whose release page was
opened and not imported, whose work it holds, and the views it has saved.

A tile opens its game in the pane, an author's name opens the author's page, and See All
opens a grid on a named view of the same question. The counts on Your Views need the whole
catalog, which loads after the page is drawn; the shelves do not wait for it.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from functools import partial
from typing import Any

from nicegui import background_tasks, ui

from common import icons
from common.failures import why
from common.i18n import t
from common.media.media_specs import IMAGE_FAMILY, MEDIA_SPECS, media_label_map
from common.online import vps_find, vps_home
from manager.kit import (
    busy,
    candidates,
    deeplink,
    dropdown,
    game_tables,
    panel,
    remembered,
    verbs,
    views,
    when,
)
from manager.library import (
    discovery_ask,
    discovery_marks,
    discovery_marks_grid,
    discovery_pictures,
    discovery_releases,
    discovery_shelves,
)

logger = logging.getLogger("vpinfe.manager.library.discovery_home")

ADDRESS = "/manager?" + deeplink.query({"view": "discovery"})
SHELVES_ADDRESS = "/manager?" + deeplink.query({"view": "settings",
                                                "settings_page": discovery_shelves.PAGE})
WINDOW_KEY = "discovery_window"

WINDOWS = {days: word for word, days in discovery_ask.SINCE_DAYS}
DEFAULT_DAYS = 7

_CHIP_WORDS = {vps_home.ALL: "manager.discovery.chip.all",
               vps_home.YOURS: "manager.discovery.chip.yours"}


WINDOW_WIDTH = "w-36"
KINDS_WIDTH = "w-44"
GAPS_WIDTH = "w-56"


def _heading() -> ui.row:
    """A shelf's heading row: the heading on the page's edge, its controls and See All at the
    right end, in `_end`."""
    return ui.row().classes("manager-heading-row w-full items-center gap-2")


def _end() -> ui.row:
    """What sits at a heading row's right end, on the same indent as the tiles under it."""
    return ui.row().classes("items-center gap-2 no-wrap pr-3")


def window_days(value: Any) -> int:
    return value if value in WINDOWS else DEFAULT_DAYS


def remembered_days() -> int:
    return window_days(remembered.get(WINDOW_KEY))


def _fits(cell: Any, values: list[Any]) -> bool:
    """Whether a cell is one of the values picked, or for a list cell holds any of them."""
    if isinstance(cell, (list, tuple, set)):
        return any(one in values for one in cell)
    return cell in values


def view_count(view: views.View, rows: list[dict[str, Any]], *, compared: bool) -> int | None:
    """How many rows a view shows, where its filters are plain value lists; None where it
    holds a filter this does not read, or reads a column that is empty until the library is
    compared, since a count that may be wrong is worse than none."""
    if view.filters and not compared:
        return None
    shown = rows
    for field, model in view.filters.items():
        values = model.get("values") if isinstance(model, dict) else None
        if not isinstance(values, list):
            return None
        shown = [one for one in shown if _fits(one.get(field), values)]
    return len(shown)


def new_filters(days: int, chip: str, kept: list[str], *, counted: bool) -> dict[str, Any]:
    """The Releases filters that open on what a chip of the New shelf counts: added in the
    window, of a kind the shelf shows - the chip's own, or every kept kind it has a place
    for - and in the library for Your Games."""
    question: dict[str, Any] = {"since": days}
    if chip == vps_home.YOURS:
        question["owned"] = "yes"
    filters = discovery_releases.arrival_filters(question, counted=counted)
    filters["kind"] = {"values": [chip] if chip not in (vps_home.ALL, vps_home.YOURS)
                       else [one for one in kept if one not in vps_home.NOT_SHOWN]}
    return filters


def version_line(release: dict[str, Any]) -> str:
    """A release's version, and the one it held before VPS changed it where it was re-dated."""
    version, was = release.get("version") or "", release.get("was") or ""
    if version and was:
        return t("manager.discovery.version_was", version=version, was=was)
    return version


def _kind_words(listing: str) -> tuple[str, str]:
    """A kind by its VPS key: one release of it, and several."""
    # Imported here: `discovery` draws this page.
    from manager.library.discovery import KINDS
    from manager.library.discovery_ask import KIND_MANY
    one = next((t(word) for listed, _field, word in KINDS if listed == listing), listing)
    return one, t(KIND_MANY[listing]) if listing in KIND_MANY else one


# What a tab of games lacking a media kind with no VPS kind of its own is called, in the plural.
_MEDIA_GAPS = {
    "playfield": "manager.discovery.gap.playfield",
    "playfield_fss": "manager.discovery.gap.playfield_fss",
    "playfield_video": "manager.discovery.gap.playfield_video",
    "scoreview": "manager.discovery.gap.scoreview",
    "scoreview_video": "manager.discovery.gap.scoreview_video",
    "real_dmd": "manager.discovery.gap.real_dmd",
    "real_dmd_color": "manager.discovery.gap.real_dmd_color",
    "cab": "manager.discovery.gap.cab",
    "flyer": "manager.discovery.gap.flyer",
    "audio": "manager.discovery.gap.audio",
    "backglass": "manager.discovery.gap.backglass",
}


_BACKGLASSES = ("b2sFiles", "backglass")

# How many moves a tile lists before it counts the rest.
MOVES_SHOWN = 2


def move_line(move: dict[str, Any]) -> str:
    """One line of a tile: what has a later version, and the version it moved from and to."""
    kind = _kind_words(move["kind"])[0]
    if move["then"] and move["now"]:
        return t("manager.discovery.update.moved", kind=kind, then=move["then"], now=move["now"])
    return t("manager.discovery.update.later", kind=kind, now=move["now"]) \
        if move["now"] else kind


def is_picture(move: dict[str, Any]) -> bool:
    """Whether a move is a picture a source has replaced."""
    return bool(move.get("md5"))


def pictures_line(moves: list[dict[str, Any]]) -> str:
    """The one line a game's replaced pictures share, naming them: "Wheel and Backglass
    pictures", or just the names where one is not a picture (a video, a sound)."""
    labels = media_label_map()
    kinds = [str(one["kind"]) for one in moves]
    names = [labels.get(kind, kind) for kind in kinds]
    named = t("manager.collection_rules.and_last", rest=t(
        "manager.collection_rules.list_join").join(names[:-1]), last=names[-1]) \
        if len(names) > 1 else names[0]
    images = {spec.kind for spec in MEDIA_SPECS if spec.family == IMAGE_FAMILY}
    return t("manager.discovery.update.pictures", kinds=named) \
        if all(kind in images for kind in kinds) else named


def tile_lines(moves: list[dict[str, Any]]) -> list[str]:
    """The lines of a tile: one for each move, with the replaced pictures together in one,
    after the rest."""
    pictures = [one for one in moves if is_picture(one)]
    return [*(move_line(one) for one in moves if not is_picture(one)),
            *([pictures_line(pictures)] if pictures else [])]


def _gap_words(kind: str) -> str:
    """What a game lacks, in the plural a tab names it with: a VPS kind, which holds the
    media kind filed with it, or a media kind with none. Backglass is both an asset and a
    media kind, so each says which."""
    if kind == "b2sFiles":
        return t("manager.discovery.gap.backglass_asset")
    if kind in vps_home.GAP_KINDS:
        return _kind_words(kind)[1]
    return t(_MEDIA_GAPS[kind]) if kind in _MEDIA_GAPS else media_label_map().get(kind, kind)


def _gap_listed(kind: str) -> str:
    """What the Missing From list calls a tab: `_gap_words`, except that both Backglass tabs
    are Backglasses there, each told apart by the heading it sits under."""
    return t("manager.discovery.gap.backglasses") if kind in _BACKGLASSES else _gap_words(kind)


def _gap_boxed(kind: str) -> str:
    """What the closed Missing From box calls a tab, where no heading is in view."""
    return t("manager.discovery.gap.backglasses_asset" if kind == "b2sFiles"
             else "manager.discovery.gap.backglasses_media")


def _gap_section(kind: str) -> str:
    """The heading a tab of games lacking `kind` sits under in the Missing From dropdown."""
    return t("manager.discovery.gap.assets" if kind in vps_home.ASSET_GAPS
             else "manager.discovery.gap.media")


def fill(body: Any, home: dict[str, Any], *, library: Any, scope: str,
         view_presets: dict[str, Any],
         catalog: Callable[[], Awaitable[tuple[list[dict[str, Any]], list[dict[str, Any]]]]],
         reread: Callable[[int], Awaitable[dict[str, Any]]],
         on_select: Callable[[Any], Any],
         see_new: Callable[[int, str], Any],
         see_missing: Callable[[list[str]], Any],
         see_updates: Callable[[list[str]], Any],
         see_dont_have: Callable[[], Any],
         see_family: Callable[[], Any],
         see_health: Callable[[str], Any],
         see_unmatched: Callable[[], Any],
         match_game: Callable[[dict[str, Any]], Awaitable[bool]],
         see_picture: Callable[[str, str], Any],
         open_view: Callable[[str], Any],
         open_releases_view: Callable[[str], Any],
         open_marks: Callable[[str], Any],
         open_subject: Callable[[dict[str, str]], Any],
         marking: discovery_marks.Marking,
         shelves: Sequence[str] = discovery_shelves.IDS) -> Callable[[], Awaitable[None]]:
    """Draw Home into `body` from `home`, the answer for the remembered window.

    `catalog()` gives the people and the All Games rows once the catalog is in memory; `reread`
    answers for another window. `see_new(days, chip)`, `see_missing(game_ids)` and
    `see_updates(game_ids)` open the grid on the shelf's question, `see_dont_have()` and
    `see_family()` All Games on the games the shelf draws from, `see_unmatched()` the Games grid
    on its Unmatched view, `match_game(game)` the Match picker on one game, `see_health(finding)`
    Games on the view that lists a Library Health finding's games, `see_picture(vps_id, kind)`
    a game's pane on the section of that media kind, `open_view(id)` on a saved view of
    All Games, `open_releases_view(id)` on one of Releases, `open_marks(view)` the Marks grid on
    its Ignored or Wanted view, and `open_subject` an author's page. `marking` is the menu a
    tile opens on a right click or a long press, and what says a release is Wanted. `shelves`
    are the ids of the shelves to draw, top to bottom; a shelf left out is not drawn at all.
    Returns the coroutine that reads the shelves again and draws them where they stand."""
    shown: dict[str, Any] = {"home": home, "chip": vps_home.ALL, "lack": "", "finding": "",
                             "rows": None, "people": [], "asked": home["days"]}
    with body:
        boxes = {one: ui.column().classes("w-full gap-2") for one in shelves}
        if not boxes:
            with ui.row().classes("items-center gap-2"):
                ui.label(t("manager.discovery.no_shelves")).classes("manager-help")
                panel.link(t("manager.discovery.no_shelves.choose"), to=SHELVES_ADDRESS)()
    if discovery_shelves.NEW in boxes:
        with boxes[discovery_shelves.NEW]:
            with _heading():
                ui.label(t("manager.discovery.new_shelf")).classes(
                    "manager-fact-heading shrink-0")
                ui.space()
                with _end():
                    window = dropdown.Select(
                        {days: t(word) for days, word in WINDOWS.items()},
                        value=home["days"], look=dropdown.OUTLINED,
                        on_change=lambda event: pick_window(event.value)).classes(WINDOW_WIDTH)
                    kinds_slot = ui.element("div").classes("contents")
                    see_all_slot = ui.element("div").classes("contents")
            tiles_box = ui.column().classes("w-full gap-2")

    def menu_on(card: Any, shelf: str, item: dict[str, Any], listing: str) -> None:
        """The tile's right click and long press."""
        word = media_label_map().get(listing) or (_kind_words(listing)[0] if listing else "")
        if listing in ("b2sFiles", "backglass"):
            word = _gap_words(listing)
        with card:
            menu = ui.context_menu()
        panel.verb_menu(menu, marking.title(shelf, item), [
            *(discovery_pictures.take_verb(library, item, refresh)
              if shelf == discovery_marks.MISSING else []),
            *marking.verbs(shelf, item, listing, word)])

    def update_menu(card: Any, item: dict[str, Any]) -> None:
        """The right click and long press of an Updates tile."""
        entries = marking.update_verbs(item, lambda listing: _kind_words(listing)[0])
        if not entries:
            return
        with card:
            menu = ui.context_menu()
        panel.verb_menu(menu, marking.title(discovery_marks.UPDATE, item), entries)

    def open_tile(item: dict[str, Any]) -> Any:
        """The game's pane, on the section of the first thing that has a later version: a
        file before any picture, in the order the moves come."""
        first = next((one for one in item["moves"] if not is_picture(one)), item["moves"][0])
        return see_picture(item["vps_id"], str(first["kind"]))

    def update_tile(parent: Any, item: dict[str, Any]) -> None:
        with parent, ui.element("div").classes("manager-shelf-tile") as card:
            card.props("tabindex=0 role=button")
            # A game matched to no entry has no Discovery pane: it opens in the Games list.
            opens = (lambda one=item: open_tile(one)) if item["vps_id"] \
                else (lambda one=item: see_updates([one["game_id"]]))
            card.on("click", lambda _e: opens())
            card.on("keydown.enter", lambda _e: opens())
            candidates.thumb(item["img_url"], icons.TABLES, t("manager.discovery.no_picture"))
            with ui.column().classes("gap-0 min-w-0"):
                ui.label(item["game"]).classes("manager-shelf-name")
                lines = tile_lines(item["moves"])
                for line in lines[:MOVES_SHOWN]:
                    ui.label(line).classes("text-xs manager-help truncate")
                if len(lines) > MOVES_SHOWN:
                    ui.label(t("manager.discovery.update.more",
                               count=len(lines) - MOVES_SHOWN)) \
                        .classes("text-xs manager-help")
            update_menu(card, item)

    def draw_updates() -> None:
        box = boxes[discovery_shelves.UPDATES]
        box.clear()
        updates = shown["home"]["updates"]
        if not updates["count"]:
            return
        with box:
            with _heading():
                ui.label(t("manager.discovery.updates")).classes("manager-fact-heading")
                ui.space()
                with _end():
                    panel.link(t("manager.discovery.see_all"), to=ADDRESS,
                               on_click=lambda: see_updates(updates["game_ids"]))()
            row = ui.row().classes("manager-shelf w-full px-3")
            for one in updates["games"]:
                update_tile(row, one)

    def tile(parent: Any, release: dict[str, Any]) -> None:
        with parent, ui.element("div").classes("manager-shelf-tile") as card:
            card.props("tabindex=0 role=button")
            card.on("click", lambda _e, one=release: on_select({"id": one["vps_id"]}))
            card.on("keydown.enter", lambda _e, one=release: on_select({"id": one["vps_id"]}))
            candidates.thumb(release["img_url"], icons.TABLES, t("manager.discovery.no_picture"))
            with ui.column().classes("gap-0 min-w-0"):
                ui.label(_kind_words(release["listing"])[0]).classes("text-xs manager-label")
                with ui.row().classes("items-center gap-2 no-wrap min-w-0"):
                    ui.label(release["game"]).classes("manager-shelf-name truncate")
                    if release["held"]:
                        ui.label(t("manager.discovery.have")).classes(
                            "manager-tier manager-tier--on")
                ui.label(" · ".join(one for one in (
                    ", ".join(release["authors"][:2]), when.ago(release["added"])) if one)) \
                    .classes("text-xs manager-help truncate")
                if version_line(release):
                    ui.label(version_line(release)).classes("text-xs manager-help truncate")
                if release["mod"]:
                    ui.label(t("manager.discovery.mod")).classes("text-xs manager-help")
            if marking.wanted_release(release["id"]):
                ui.icon(verbs.WANTED, size="18px").classes("manager-cell-mark").tooltip(
                    t("manager.discovery.mark.wanted.help"))
            menu_on(card, discovery_marks.NEW, release, release["listing"])

    def chip_words() -> dict[str, str]:
        return {chip: t(_CHIP_WORDS[chip]) if chip in _CHIP_WORDS else _kind_words(chip)[1]
                for chip, shelf in shown["home"]["new"].items()
                if shelf["count"] or chip == vps_home.ALL}

    def draw_shelf() -> None:
        kinds_slot.clear()
        see_all_slot.clear()
        words = chip_words()
        if shown["chip"] not in words:
            shown["chip"] = vps_home.ALL
        if shown["home"]["new"][vps_home.ALL]["count"]:
            with kinds_slot:
                dropdown.Select(words, value=shown["chip"], look=dropdown.OUTLINED,
                                counts={chip: shown["home"]["new"][chip]["count"]
                                        for chip in words},
                                on_change=lambda event: pick_chip(event.value)
                                ).classes(KINDS_WIDTH)
            with see_all_slot:
                panel.link(t("manager.discovery.see_all"), to=ADDRESS,
                           on_click=lambda: see_new(shown["home"]["days"], shown["chip"]))()
        draw_tiles()

    def draw_tiles() -> None:
        """The tiles of the kind picked, drawn again without the dropdown that picked it."""
        tiles_box.clear()
        everything = shown["home"]["new"]
        if not everything[vps_home.ALL]["count"]:
            return
        with tiles_box:
            row = ui.row().classes("manager-shelf w-full px-3")
            for one in everything[shown["chip"]]["releases"]:
                tile(row, one)

    async def refresh() -> None:
        """Read the shelves again for the window on show and draw them where they stand, so a
        tab or chip picked stays picked."""
        try:
            shown["home"] = await reread(shown["home"]["days"])
        except Exception as exc:  # noqa: BLE001 - the shelves stay as they were
            with body:
                ui.notify(t("manager.discovery.read_failed"), caption=why(exc), type="negative")
            return
        draw_shelves(but=(discovery_shelves.UPDATES, discovery_shelves.VIEWS))

    async def pick_window(days: int) -> None:
        """Read the shelf for another window. The window is remembered once the read has
        answered; a read that fails leaves the shelf and the window as they were, and the
        answer to an earlier pick that arrives after a later one is dropped."""
        shown["asked"] = days
        if days == shown["home"]["days"]:
            return
        try:
            fresh = await reread(days)
        except Exception as exc:  # noqa: BLE001 - the shelf stays as it was
            if shown["asked"] == days:
                window.set_value(shown["home"]["days"])
                with body:
                    ui.notify(t("manager.discovery.read_failed"), caption=why(exc),
                              type="negative")
            return
        if shown["asked"] != days:
            return
        remembered.put(WINDOW_KEY, days)
        shown["home"] = fresh
        draw_shelves(but=(discovery_shelves.MISSING, discovery_shelves.VIEWS))

    def pick_chip(chip: str) -> None:
        shown["chip"] = chip
        draw_tiles()

    def lacking_tabs() -> dict[str, int]:
        """The tabs with games in them, Assets then Media, each by name."""
        counted = {listing: gap["count"] for listing, gap in shown["home"]["missing"].items()
                   if gap["count"]}
        return dict(sorted(counted.items(),
                           key=lambda one: (one[0] not in vps_home.ASSET_GAPS,
                                            _gap_listed(one[0]).casefold())))

    def draw_lacking() -> None:
        box = boxes[discovery_shelves.MISSING]
        box.clear()
        tabs = lacking_tabs()
        if not tabs:
            return
        listing = shown["lack"] if shown["lack"] in tabs else next(iter(tabs))
        with box:
            with _heading():
                ui.label(t("manager.discovery.missing_from")).classes(
                    "manager-fact-heading shrink-0")
                ui.space()
                with _end():
                    dropdown.Select(
                        {tab: _gap_listed(tab) for tab in tabs}, value=listing,
                        look=dropdown.OUTLINED, on_change=lambda event: pick_lack(event.value),
                        sections={tab: _gap_section(tab) for tab in tabs}, counts=tabs,
                        shown={tab: _gap_boxed(tab) for tab in tabs if tab in _BACKGLASSES}
                    ).classes(GAPS_WIDTH)
                    lacking_see_all = ui.element("div").classes("contents")
            lacking_tiles = ui.row().classes("manager-shelf w-full px-3")
        shown["lacking_slots"] = (lacking_see_all, lacking_tiles)
        draw_gaps(listing)

    def draw_gaps(listing: str) -> None:
        """The See All and the tiles of one kind, drawn again without the dropdown that
        picked it."""
        see_all, row = shown["lacking_slots"]
        see_all.clear()
        row.clear()
        gap = shown["home"]["missing"][listing]
        with see_all:
            if ids := gap.get("game_ids"):
                panel.link(t("manager.discovery.see_all"), to=ADDRESS,
                           on_click=lambda: see_missing(ids))()
        for game in gap["games"]:
            with row, ui.element("div").classes("manager-shelf-tile") as card:
                card.props("tabindex=0 role=button")
                card.on("click", lambda _e, game=game: on_select({"id": game["vps_id"]}))
                card.on("keydown.enter",
                        lambda _e, game=game: on_select({"id": game["vps_id"]}))
                candidates.thumb(game.get("picture") or game["img_url"], icons.TABLES,
                                 t("manager.discovery.no_picture"))
                with ui.column().classes("gap-0 min-w-0 grow"):
                    ui.label(game["name"]).classes("manager-shelf-name")
                    if game["vps"]:
                        ui.label(t("manager.discovery.lacking.on_vps",
                                   count=game["vps"])).classes("text-xs manager-help")
                    if game["source"]:
                        ui.label(t("manager.discovery.lacking.from_source",
                                   count=game["source"])).classes("text-xs manager-help")
                discovery_pictures.take_button(library, game, refresh)
                menu_on(card, discovery_marks.MISSING, game, listing)

    def pick_lack(listing: str) -> None:
        shown["lack"] = listing
        draw_gaps(listing)

    def draw_dont_have() -> None:
        box = boxes[discovery_shelves.DONT_HAVE]
        box.clear()
        lacked = shown["home"]["dont_have"]
        if not lacked["games"]:
            return
        with box:
            with _heading():
                ui.label(t("manager.discovery.dont_have")).classes(
                    "manager-fact-heading shrink-0").tooltip(t("manager.discovery.dont_have.help"))
                ui.label(t("manager.discovery.dont_have.order")).classes(
                    "text-xs manager-help")
                ui.space()
                with _end():
                    panel.link(t("manager.discovery.see_all"), to=ADDRESS,
                               on_click=see_dont_have)()
            row = ui.row().classes("manager-shelf w-full px-3")
            for game in lacked["games"]:
                dont_have_tile(row, game)

    def dont_have_tile(parent: Any, game: dict[str, Any]) -> None:
        with parent, ui.element("div").classes("manager-shelf-tile") as card:
            card.props("tabindex=0 role=button")
            card.on("click", lambda _e, one=game: on_select({"id": one["vps_id"]}))
            card.on("keydown.enter", lambda _e, one=game: on_select({"id": one["vps_id"]}))
            candidates.thumb(game["img_url"], icons.TABLES, t("manager.discovery.no_picture"))
            with ui.column().classes("gap-0 min-w-0"):
                ui.label(t("word.game")).classes("text-xs manager-label")
                ui.label(game["game"]).classes("manager-shelf-name")
                if made := game_tables.made(game):
                    ui.label(made).classes("text-xs manager-help truncate")
                ui.label(t("manager.discovery.dont_have.authors", count=game["authors"])) \
                    .classes("text-xs manager-help")
            if marking.wanted_game(game["vps_id"]):
                ui.icon(verbs.WANTED, size="18px").classes("manager-cell-mark").tooltip(
                    t("manager.discovery.mark.wanted.help"))
            menu_on(card, discovery_marks.DONT_HAVE, game, "")

    def draw_family() -> None:
        box = boxes[discovery_shelves.OTHER_VERSIONS]
        box.clear()
        related = shown["home"]["family"]
        if not related["games"]:
            return
        with box:
            with _heading():
                ui.label(t("manager.discovery.family")).classes(
                    "manager-fact-heading shrink-0").tooltip(t("manager.discovery.family.help"))
                ui.label(t("manager.discovery.dont_have.order")).classes(
                    "text-xs manager-help")
                ui.space()
                with _end():
                    panel.link(t("manager.discovery.see_all"), to=ADDRESS,
                               on_click=see_family)()
            row = ui.row().classes("manager-shelf w-full px-3")
            for game in related["games"]:
                family_tile(row, game)

    def family_tile(parent: Any, game: dict[str, Any]) -> None:
        with parent, ui.element("div").classes("manager-shelf-tile") as card:
            card.props("tabindex=0 role=button")
            card.on("click", lambda _e, one=game: on_select({"id": one["vps_id"]}))
            card.on("keydown.enter", lambda _e, one=game: on_select({"id": one["vps_id"]}))
            candidates.thumb(game["img_url"], icons.TABLES, t("manager.discovery.no_picture"))
            with ui.column().classes("gap-0 min-w-0"):
                ui.label(t("word.game")).classes("text-xs manager-label")
                ui.label(game["game"]).classes("manager-shelf-name")
                if made := game_tables.made(game):
                    ui.label(made).classes("text-xs manager-help truncate")
                held = game["held"]
                ui.label(t("manager.discovery.family.held", made=game_tables.made(held[0]))
                         if len(held) == 1 and game_tables.made(held[0])
                         else t("manager.discovery.family.held_many", count=len(held))) \
                    .classes("text-xs manager-help truncate")

    def draw_unmatched() -> None:
        box = boxes[discovery_shelves.MATCH]
        box.clear()
        likely = shown["home"]["unmatched"]
        if not likely["games"]:
            return
        with box:
            with _heading():
                ui.label(t("manager.discovery.unmatched")).classes(
                    "manager-fact-heading shrink-0").tooltip(t("manager.discovery.unmatched.help"))
                ui.space()
                with _end():
                    panel.link(t("manager.discovery.see_all"), to=ADDRESS,
                               on_click=see_unmatched)()
            row = ui.row().classes("manager-shelf w-full px-3")
            for game in likely["games"]:
                unmatched_tile(row, game)

    async def match(game: dict[str, Any]) -> None:
        """The Match picker on the game, then the shelves again if it changed the match: a game
        just matched is off this one."""
        if await match_game({"id": game["game_id"], "name": game["game"]}):
            await refresh()

    def unmatched_tile(parent: Any, game: dict[str, Any]) -> None:
        with parent, ui.element("div").classes("manager-shelf-tile") as card:
            card.props("tabindex=0 role=button")
            card.on("click", lambda _e, one=game: match(one))
            card.on("keydown.enter", lambda _e, one=game: match(one))
            candidates.thumb(game["suggestion"]["img_url"], icons.TABLES,
                             t("manager.discovery.no_picture"))
            with ui.column().classes("gap-0 min-w-0"):
                ui.label(t("word.game")).classes("text-xs manager-label")
                ui.label(game["game"]).classes("manager-shelf-name")
                if made := game_tables.made(game):
                    ui.label(made).classes("text-xs manager-help truncate")
                ui.label(t("manager.discovery.unmatched.looks_like",
                           made=game_tables.made(game["suggestion"]))) \
                    .classes("text-xs manager-help truncate")

    def health_findings() -> dict[str, int]:
        return {finding: found["count"] for finding, found in shown["home"]["health"].items()
                if found["count"]}

    def draw_health() -> None:
        box = boxes[discovery_shelves.HEALTH]
        box.clear()
        findings = health_findings()
        if not findings:
            return
        finding = shown["finding"] if shown["finding"] in findings else next(iter(findings))
        with box:
            with _heading():
                ui.label(t("manager.discovery.health")).classes(
                    "manager-fact-heading shrink-0").tooltip(t("manager.discovery.health.help"))
                ui.space()
                with _end():
                    dropdown.Select(
                        {each: t("manager.discovery.counted", count=count,
                                 name=t(f"manager.discovery.health.{each}"))
                         for each, count in findings.items()}, value=finding,
                        look=dropdown.OUTLINED, on_change=lambda event: pick_finding(event.value)
                    ).classes(GAPS_WIDTH)
                    health_see_all = ui.element("div").classes("contents")
            health_tiles = ui.row().classes("manager-shelf w-full px-3")
        shown["health_slots"] = (health_see_all, health_tiles)
        draw_findings(finding)

    def draw_findings(finding: str) -> None:
        """The See All and the tiles of one finding, drawn again without the dropdown that
        picked it."""
        see_all, row = shown["health_slots"]
        see_all.clear()
        row.clear()
        with see_all:
            panel.link(t("manager.discovery.see_all"), to=ADDRESS,
                       on_click=lambda: see_health(finding))()
        for game in shown["home"]["health"][finding]["games"]:
            health_tile(row, finding, game)

    def pick_finding(finding: str) -> None:
        shown["finding"] = finding
        draw_findings(finding)

    def health_tile(parent: Any, finding: str, game: dict[str, Any]) -> None:
        """The entry's pane where there is one, else Games on the finding's view."""
        def opens() -> Any:
            return on_select({"id": game["vps_id"]}) if game["vps_id"] else see_health(finding)

        with parent, ui.element("div").classes("manager-shelf-tile") as card:
            card.props("tabindex=0 role=button")
            card.on("click", lambda _e: opens())
            card.on("keydown.enter", lambda _e: opens())
            candidates.thumb(game["img_url"], icons.TABLES, t("manager.discovery.no_picture"))
            with ui.column().classes("gap-0 min-w-0"):
                ui.label(t("word.game")).classes("text-xs manager-label")
                ui.label(game["game"]).classes("manager-shelf-name")
                if made := game_tables.made(game):
                    ui.label(made).classes("text-xs manager-help truncate")
                if finding == vps_home.DUPLICATES:
                    ui.label(t("manager.discovery.health.shared",
                               count=len(game["game_ids"]))).classes("text-xs manager-help")

    def draw_opened() -> None:
        box = boxes[discovery_shelves.OPENED]
        box.clear()
        pages = shown["home"]["opened"]
        if not pages["games"]:
            return
        with box:
            with _heading():
                ui.label(t("manager.discovery.opened_games")).classes(
                    "manager-fact-heading shrink-0").tooltip(
                        t("manager.discovery.opened_games.help"))
                ui.label(t("manager.discovery.opened_games.order")).classes(
                    "text-xs manager-help")
            row = ui.row().classes("manager-shelf w-full px-3")
            for game in pages["games"]:
                opened_tile(row, game)

    def opened_tile(parent: Any, game: dict[str, Any]) -> None:
        with parent, ui.element("div").classes("manager-shelf-tile") as card:
            card.props("tabindex=0 role=button")
            card.on("click", lambda _e, one=game: on_select({"id": one["vps_id"]}))
            card.on("keydown.enter", lambda _e, one=game: on_select({"id": one["vps_id"]}))
            candidates.thumb(game["img_url"], icons.TABLES, t("manager.discovery.no_picture"))
            with ui.column().classes("gap-0 min-w-0"):
                ui.label(t("word.game")).classes("text-xs manager-label")
                ui.label(game["game"]).classes("manager-shelf-name")
                if made := game_tables.made(game):
                    ui.label(made).classes("text-xs manager-help truncate")
                ui.label(t("manager.discovery.opened", when=when.ago(game["at"], inline=True))) \
                    .classes("text-xs manager-help truncate")

    def draw_wanted() -> None:
        box = boxes[discovery_shelves.WANTED]
        box.clear()
        wanted = shown["home"]["wanted"]
        if not wanted["items"]:
            return
        with box:
            with _heading():
                ui.label(t("manager.discovery.mark.wanted")).classes("manager-fact-heading")
                ui.space()
                with _end():
                    panel.link(t("manager.discovery.see_all"), to=ADDRESS,
                               on_click=lambda: open_marks(discovery_marks_grid.WANTED_VIEW))()
            row = ui.row().classes("manager-shelf w-full px-3")
            for item in wanted["items"]:
                wanted_tile(row, item)

    def wanted_tile(parent: Any, item: dict[str, Any]) -> None:
        with parent, ui.element("div").classes("manager-shelf-tile") as card:
            card.props("tabindex=0 role=button")
            card.on("click", lambda _e, one=item: on_select({"id": one["vps_id"]}))
            card.on("keydown.enter", lambda _e, one=item: on_select({"id": one["vps_id"]}))
            candidates.thumb(item["img_url"], icons.TABLES, t("manager.discovery.no_picture"))
            with ui.column().classes("gap-0 min-w-0"):
                if item["listing"]:
                    ui.label(_kind_words(item["listing"])[0]).classes("text-xs manager-label")
                ui.label(item["game"]).classes("manager-shelf-name")
                if item["authors"]:
                    ui.label(", ".join(item["authors"][:2])).classes(
                        "text-xs manager-help truncate")
                if item["version"]:
                    ui.label(item["version"]).classes("text-xs manager-help truncate")
            menu_on(card, discovery_marks.WANT, item, item["listing"])

    def draw_authors() -> None:
        box = boxes[discovery_shelves.AUTHORS]
        box.clear()
        collected = shown["home"]["authors"]
        if not collected:
            return
        with box:
            with _heading():
                ui.label(t("manager.discovery.authors")).classes("manager-fact-heading")
            with ui.row().classes("manager-shelf w-full px-3"):
                for one in collected:
                    author = {"type": vps_find.AUTHOR, "name": one["name"]}
                    with panel.link_tile(
                            to="/manager?" + deeplink.query({"view": "discovery",
                                                             "subject": author}),
                            classes="manager-shelf-tile manager-shelf-tile--plain "
                                    "manager-link--inplace") as card:
                        card.on("click", lambda _e, who=author: open_subject(who))
                        ui.label(one["name"]).classes("manager-shelf-name")
                        with ui.row().classes("items-center gap-1 no-wrap"):
                            ui.label(t("manager.discovery.authors.held", count=one["held"])) \
                                .classes("text-xs manager-help")
                            if one["new"]:
                                ui.label("·").classes("text-xs manager-help")
                                ui.label(t("manager.discovery.authors.new", count=one["new"])) \
                                    .classes("text-xs manager-label")

    def draw_views() -> None:
        box = boxes[discovery_shelves.VIEWS]
        box.clear()
        browse, _active = views.stored(library, scope)
        saved, _active = views.stored(library, discovery_releases.SCOPE)
        compared = bool(shown["home"]["counted"])
        ignored = None if shown["rows"] is None else marking.ignored_count()
        with box:
            with _heading():
                ui.label(t("manager.discovery.views")).classes("manager-fact-heading")
            with ui.row().classes("manager-shelf w-full px-3"):
                for one in views.builtins(view_presets) + browse:
                    count = None if shown["rows"] is None \
                        else view_count(one, shown["rows"], compared=compared)
                    view_tile(t("manager.discovery.tab.browse"), one.name, count,
                              partial(open_view, one.id))
                for one in views.builtins(discovery_releases.VIEWS) + saved:
                    view_tile(t("manager.discovery.tab.releases"), one.name, None,
                              partial(open_releases_view, one.id))
                if ignored:
                    view_tile(t("manager.discovery.marks.place"),
                              t("manager.discovery.mark.ignored"), ignored,
                              lambda: open_marks(discovery_marks_grid.IGNORED_VIEW),
                              many="manager.discovery.marks.summary")

    def view_tile(place: str, name: str, count: int | None, opens: Callable[[], Any], *,
                  many: str = "manager.discovery.views.count") -> None:
        """A saved view: where it opens, its name and how many rows it shows where that is
        known."""
        with ui.element("div").classes("manager-shelf-tile manager-shelf-tile--plain") as card:
            card.props("tabindex=0 role=button")
            card.on("click", lambda _e: opens())
            card.on("keydown.enter", lambda _e: opens())
            ui.label(place).classes("text-xs manager-label")
            ui.label(name).classes("manager-shelf-name")
            if count is not None:
                ui.label(t(many, count=count)).classes("text-xs manager-help")

    async def learn() -> None:
        try:
            shown["people"], shown["rows"] = await catalog()
        except Exception:  # noqa: BLE001 - the shelves stand without search and counts
            logger.warning("manager: could not read the catalog for Discovery's search",
                           exc_info=True)
            return
        if discovery_shelves.VIEWS in boxes:
            draw_views()

    draws: dict[str, Callable[[], None]] = {
        discovery_shelves.UPDATES: draw_updates,
        discovery_shelves.NEW: draw_shelf,
        discovery_shelves.WANTED: draw_wanted,
        discovery_shelves.MISSING: draw_lacking,
        discovery_shelves.DONT_HAVE: draw_dont_have,
        discovery_shelves.OTHER_VERSIONS: draw_family,
        discovery_shelves.MATCH: draw_unmatched,
        discovery_shelves.HEALTH: draw_health,
        discovery_shelves.OPENED: draw_opened,
        discovery_shelves.AUTHORS: draw_authors,
        discovery_shelves.VIEWS: draw_views,
    }

    def draw_shelves(*, but: tuple[str, ...] = ()) -> None:
        for one in boxes:
            if one not in but:
                draws[one]()

    draw_shelves()
    if discovery_shelves.VIEWS in boxes:
        busy.start(boxes[discovery_shelves.VIEWS], learn)
    else:
        background_tasks.create(learn(), name="discovery-learn")
    return refresh
