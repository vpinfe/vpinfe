
"""Binding a game to its VPS entry, from the panel or from a grid selection.

Both callers come through here so there is one picker rather than two that drift.

**Nothing here ranks a result and nothing here assigns one.** A ranker was built for this
list and retired for scoring at chance, so the order is the one VPS answers in and the
choice is always a person's.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import Any

from nicegui import run, ui

from common import icons
from common.i18n import t
from manager.kit import candidates, dialog, game_tables, offload, panel, verbs, walks
from manager.kit.on_page import on_page

# A NUL cannot occur in a VPS id, so neither of these can be mistaken for one.
CANCELLED = walks.SKIPPED
CLEARED = "\x00none"
STOPPED = walks.STOPPED

NO_PICTURE = t("manager.vps_match.no_picture")


@on_page
async def ask(library: Any, game: dict[str, Any], place: str = "",
              walking: bool = False) -> str | None:
    """Ask which VPS entry this game is. The caller writes; this only asks.

    Answers with the chosen id, `""` to go back to the scan's own answer, `CLEARED` to
    say the machine is in no catalog, `CANCELLED` to do nothing, or `STOPPED` when
    walking. `place` is "3 of 34", empty for a game.

    `walking` splits Cancel into Skip and Stop, which are different intents in a run and
    cannot share one button.
    """
    bound = str(game.get("vps_id") or "")
    entry = await offload.io(library.vps_entry, bound) if bound else {}
    held = await offload.io(library.vps_catalog_held)
    picked = {"id": bound}
    scanned = str((game.get("discovered") or {}).get("vps_id") or "")
    behind = (await offload.io(library.vps_entry, scanned)
              if scanned and scanned != bound else {})

    with dialog.opened(t("manager.vps_match.match_named", name=str(game.get("name") or "")),
                       size="wide", ignore_outside_click=True) as box:
        if place:
            ui.label(place).classes("manager-help")

        field: dict[str, Any] = {}
        if entry or behind:
            with dialog.band(t("manager.vps_match.matched_to")):
                pass
            with dialog.well():
                if entry:
                    entry_row(entry)
                if behind:
                    guessed = not (game.get("discovered") or {}).get("vps_matched_by")
                    _own_pick(behind, cleared=not entry, guessed=guessed,
                              back=lambda: box.submit(""))
        with dialog.band(t("manager.vps_match.find_another" if entry or behind
                           else "manager.vps_match.title")):
            field["count"] = ui.label("").classes("manager-band-count")
        with dialog.well():
            with ui.row().classes("manager-well-head no-wrap"):
                with ui.element("div").classes("manager-fact-edit grow"):
                    field["box"] = ui.input(value=_seed(game)) \
                        .props("dense borderless clearable debounce=0 autofocus") \
                        .classes("manager-edit-field")
                ui.button(t("manager.vps_match.search"), icon=verbs.SEARCH,
                          on_click=lambda: look()).props("flat dense no-caps size=sm") \
                    .classes("manager-action shrink-0")
            found = ui.column().classes("w-full gap-0 manager-well-rows")

        with dialog.footer():
            if entry:
                dialog.aside(t("manager.vps_match.clear_match"),
                             lambda: box.submit(CLEARED), icon=verbs.UNMATCH)
            dialog.leave(box, walking)
            update = dialog.answer(t("manager.vps_match.pick"),
                                   lambda: box.submit(str(picked["id"])), icon=verbs.MATCH)
        update.disable()

        rows: list[dict[str, Any]] = []

        def take(vps_id: str) -> None:
            picked["id"] = vps_id
            update.set_enabled(vps_id != bound)
            draw()

        def draw() -> None:
            found.clear()
            with found:
                for row in rows:
                    this = str(row.get("vps_id") or "")
                    entry_row(row, pick=partial(take, this),
                              chosen=this == picked["id"], bound=this == bound)

        async def look() -> None:
            said = str(field["box"].value or "").strip()
            rows[:] = await offload.io(library.vps_search, said, 40) if said else []
            field["count"].text = (t("manager.vps_match.found", count=len(rows))
                                   if said and held and rows else "")
            if not said or not rows:
                found.clear()
                with found:
                    ui.label(t("manager.vps_match.no_vps_data") if not held
                             else t("manager.vps_match.search_hint") if not said
                             else t("manager.vps_match.no_results", said=(said))) \
                        .classes("manager-help")
                return
            draw()

        field["box"].on("keydown.enter", look)
        await look()

    return await box


@on_page
async def walk(library: Any, games: list[dict[str, Any]]) -> int:
    """Work through these games' matches, one picker at a time. How many it changed."""
    several = len(games) > 1

    async def ask_one(game: dict[str, Any], place: str) -> Any:
        # A grid row says only whether a game is unmatched, so `ask` handed one draws
        # every game as unmatched: no Matched to, no Current.
        record = next((one for one in library.games
                       if str(one.get("id")) == str(game.get("id"))), game)
        return await ask(library, record, walking=several, place=place if several else "")

    async def write(game: dict[str, Any], picked: Any) -> None:
        game_id = str(game.get("id") or "")
        if picked == CLEARED:
            await run.io_bound(library.declare_no_match, game_id)
        else:
            await run.io_bound(library.set_game_overrides, game_id,
                               {"alt_vps_id": str(picked)})

    changed = await walks.each(games, ask_one, write, t("manager.vps_match.set_failed"))
    # Said once at the end rather than per game: a toast after every pick in a run of
    # thirty is noise covering the thing you are looking at.
    ui.notify(t("manager.vps_match.match_set", count=changed) if changed
              else t("manager.vps_match.nothing_changed"), type="positive" if changed else "info")
    return changed


def _seed(game: dict[str, Any]) -> str:
    """The query the search opens on: a starting point, visible and editable."""
    said = str(game.get("name") or "")
    if game.get("vps_id"):
        return said
    answered = game.get("overrides") or {}
    parts = [said] + [str(answered.get(key) or "").strip()
                      for key in ("alt_manufacturer", "alt_year")]
    return " ".join(part for part in parts if part)


_OWN_PICK_WORDS = {
    # (cleared, guessed): the line, then the button that goes back.
    (False, True): ("manager.vps_match.you_picked", "manager.vps_match.go_back"),
    (True, True): ("manager.vps_match.you_cleared", "manager.vps_match.go_back"),
    (False, False): ("manager.vps_match.picked_prior",
                     "manager.vps_match.restore_earlier"),
    (True, False): ("manager.vps_match.cleared_prior",
                    "manager.vps_match.restore_earlier"),
}


def _own_pick(found: dict[str, Any], *, cleared: bool, guessed: bool,
              back: Callable[[], None]) -> None:
    said = game_tables.made(found)
    name = str(found.get("name") or "") + (f" ({said})" if said else "")
    line, button = _OWN_PICK_WORDS[(cleared, guessed)]
    with ui.row().classes("items-center gap-3 w-full no-wrap manager-vps-own"):
        ui.label(t(line, name=name)).classes("manager-help grow min-w-0")
        ui.button(t(button), icon=verbs.REVERT, on_click=back) \
            .props("flat dense no-caps size=sm").classes("manager-action shrink-0")


def entry_row(row: dict[str, Any], *, pick: Callable[[], None] | None = None,
               chosen: bool = False, bound: bool = False,
               trailing: Callable[[], None] | None = None) -> None:
    """One VPS entry, in the shape the games grid draws a game in.

    `pick` absent draws it without making it a target. `trailing` puts one control before
    the way out to the catalog, which is always last.
    """
    said = game_tables.made(row)
    url = str(row.get("url") or "")

    def end() -> None:
        with ui.row().classes("items-center gap-2 no-wrap shrink-0"):
            if bound:
                ui.label(t("manager.vps_match.matched_here")).classes("manager-matched-here")
            if trailing is not None:
                with ui.element("div").on("click.stop", lambda: None):
                    trailing()
            if url:
                # Or reading the entry would also pick it.
                with ui.element("div").on("click.stop", lambda: None):
                    panel.out(to=url, hint=t("manager.vps_match.open_in_vps"))()

    candidates.choice(str(row.get("img_url") or ""), str(row.get("name") or ""),
                      said, pick, glyph=icons.GAMES, chosen=chosen,
                      trailing=end, entry=True, missing=NO_PICTURE)
