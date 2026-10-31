"""A game's match to VPS, drawn the same in the Game panel and in the file picker.

The group is the entry row (or what stands in for it), how the match was made, and the
one act over it. `pick_a_match` asks which entry the game is and writes the answer.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from nicegui import run, ui

from common.failures import why
from common.i18n import t
from manager.kit import dialog as frame
from manager.kit import game_tables, panel, verbs
from manager.kit.on_page import on_page
from manager.library import vps_match

FULL = panel.FULL
HEADING = panel.HEADING

# What the catalog calls these against what a person does. Only where the two differ:
# `Manufacturer` needs no translating and a map that repeats it is a map nobody trusts.
DETAIL_WORDS = {
    "Title": t("word.name"),
    "IPDBId": "IPDB",
    "guides": t("manager.workbench.guides"),
    "Themes": t("manager.workbench.theme"),
}


def game_match_gap(vps_id: str, declared: bool, held: bool) -> tuple[str, str, str]:
    """What a game's Match group says when there is no entry to draw: the state's key,
    what its absence costs, and the key of the line under it."""
    if declared:
        return ("manager.workbench.no_match", "off", "manager.workbench.cleared_match")
    if not vps_id:
        return ("manager.workbench.not_matched", "warn", "")
    if not held:
        return ("word.unknown", "unknown", "manager.workbench.no_vps_data")
    return ("manager.workbench.not_listed", "warn",
            "manager.workbench.game_unlisted")


def match_rows(found: dict[str, Any], vps_id: str, declared: bool,
               held: bool, how: str = "") -> list[tuple[Any, Any]]:
    """The machine this game is matched to, and how the match was made."""
    if found.get("name"):
        return [(FULL, lambda: vps_match.entry_row(found, trailing=how_matched(how)))]
    return unmatched(game_match_gap(vps_id, declared, held))


def how_matched(how: str) -> Callable[[], None] | None:
    if how not in game_tables.HOW_MATCHED:
        return None
    hint = t("manager.game_tables.auto_matched.help") if how == "auto" else ""
    return panel.state(game_tables.HOW_MATCHED[how], "on", hint=hint)


def unmatched(gap: tuple[str, str, str], hint: str = "") -> list[tuple[Any, Any]]:
    said, level, under = gap
    rows: list[tuple[Any, Any]] = [(FULL, panel.state(t(said), level, hint=hint))]
    return rows + [panel.intro(t(under))] if under else rows


def change_match(pick: Callable[[], Any], *, matched: bool,
                 release: bool = False) -> Callable[[], None]:
    """The one act a Match group offers, named for whether there is a match to change.
    `release` is the table's group, which matches a build rather than a game."""
    said = t("manager.workbench.change_match") if matched \
        else t("manager.workbench.match_vps")
    if release:
        hint = t("manager.workbench.change_release_match.help") if matched \
            else t("manager.workbench.match_release.help")
    else:
        hint = t("manager.workbench.change_match.help") if matched \
            else t("manager.workbench.match_vps.help")

    def draw() -> None:
        with ui.element("div").classes("manager-slot-actions"):
            panel.action(said, pick, icon=verbs.MATCH, hint=hint)()

    return draw


@on_page
async def pick_a_match(context: dict[str, Any]) -> None:
    """Ask which catalog entry this game is, and write the answer.

    The picker itself is `manager.library.vps_match`, because the grid offers the same act over
    a selection and two copies of it would drift. What stays here is the write: the caller
    owns the game in front of it and knows how to redraw itself afterwards.
    """
    picked = await vps_match.ask(context["library"], context["game"])
    if picked is vps_match.CANCELLED:
        return
    library = context["library"]
    try:
        if picked == vps_match.CLEARED:
            await run.io_bound(library.declare_no_match, context["game_id"])
            await context["rebuild"]()
            return
        await match_to(library, context["game_id"], str(picked))
    except Exception as exc:
        ui.notify(t("manager.workbench.save_failed"), caption=why(exc), type="negative")
    await context["rebuild"]()


async def match_to(library: Any, game_id: str, vps_id: str) -> None:
    """Write the game's match to one entry, then offer the entry's details.

    Raises what the library raises: the caller says what failed and redraws itself.
    """
    await run.io_bound(library.set_game_overrides, game_id, {"alt_vps_id": vps_id})
    differs = await run.io_bound(library.vps_details, game_id)
    if differs:
        wanted = await confirm_details(differs)
        if wanted:
            await run.io_bound(library.adopt_vps_details, game_id, wanted)


async def confirm_details(differs: list[dict[str, Any]]) -> list[str]:
    """Ask which of the new entry's details to take, and answer with their names.

    Everything is ticked to start, so confirming without reading takes the whole entry -
    which is what somebody who has just said "this is that machine" means.
    """
    chosen = {str(item.get("field") or ""): True for item in differs}

    with frame.opened(t("manager.workbench.new_entry_question"), wide=True,
                      ignore_outside_click=True) as box:
        for new in (False, True):
            rows = [one for one in differs if bool(one.get("new")) is new]
            if not rows:
                continue
            panel.facts(ui, [(panel.HEADING, t("manager.workbench.details_new") if new
                              else t("manager.workbench.details_changed"))])
            with ui.column().classes("w-full gap-0 px-3"):
                for item in rows:
                    _detail_choice(item, chosen)
        with frame.footer():
            frame.cancel(lambda: box.submit(False))
            frame.answer(t("manager.workbench.use_selected"), lambda: box.submit(True),
                         icon=verbs.ACCEPT)

    if not await box:
        return []
    return [field for field, take in chosen.items() if take]


def _detail_choice(item: dict[str, Any], chosen: dict[str, bool]) -> None:
    """One field, with what it would become - and what it is now, where there is one."""
    field = str(item.get("field") or "")
    with ui.row().classes("items-baseline gap-2 w-full no-wrap"):
        box = ui.checkbox(value=True).props("dense")
        box.on_value_change(lambda _, key=field, b=box: chosen.__setitem__(key,
                                                                          bool(b.value)))
        ui.label(DETAIL_WORDS.get(field, field)).classes("manager-diff-field")
        if not item.get("new"):
            ui.label(str(item.get("ours") or "-")).classes("manager-diff-was")
            ui.icon("arrow_forward").classes("manager-diff-arrow")
        ui.label(str(item.get("theirs") or "-")).classes("manager-help truncate")
