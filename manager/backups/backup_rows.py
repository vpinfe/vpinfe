"""The Backups row on the panel of a game, a launcher or a theme.

The row says how many copies there are and when the newest was taken, and opens the copies of
this one thing in the grid System > Backups draws, so the verbs and the Undo on a restore are
the same there. Back Up Now and Restore are in that dialog.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlencode

from nicegui import run, ui

from common.backups import copies as kept_copies
from common.failures import why
from common.i18n import t
from manager.backups import backups
from manager.kit import dialog as frame
from manager.kit import grid as grid_module
from manager.kit import offload, panel, verbs, walks, when
from manager.kit.data import Library
from manager.kit.on_page import on_page

SCOPE = "backups.subject"

COLUMNS = [
    grid_module.identifier("taken", t("manager.backups.taken"), 190, pinned="left",
                           tooltipField="taken_utc", help=t("manager.backups.taken.help"),
                           **backups.TAKEN_FILTER),
    grid_module.column("reason_text", t("manager.backups.reason"), 150,
                       help=t("manager.backups.reason.help")),
    grid_module.column("note", t("manager.backups.note"), 200, flex=1,
                       help=t("manager.backups.note.help")),
    grid_module.column("size", t("word.size"), 100, type="numericColumn",
                       help=grid_module.NO_HELP,
                       **{":valueFormatter": "params => (params.data || {}).size_text || ''"}),
]


class Unreadable(list):
    """No copies, because the listing failed: `why` says what."""

    def __init__(self, why_not: str) -> None:
        super().__init__()
        self.why = why_not


def count_said(found: list[dict[str, Any]]) -> str:
    """How many copies there are: `3 copies`."""
    if isinstance(found, Unreadable):
        return t("manager.backups.unreadable")
    return t("manager.backups.count", count=len(found)) if found else t("word.none")


def latest_said(found: list[dict[str, Any]]) -> str:
    """When the newest was taken and why, under the count: `Newest 2 hours ago · Before a restore`.
    Empty where there are no copies. A copy you took yourself says its note, if it has one."""
    if isinstance(found, Unreadable):
        return found.why
    if not found:
        return ""
    newest = max(found, key=lambda one: str(one.get("taken") or ""))
    latest = t("manager.backups.latest", count=len(found),
               when=when.ago(newest.get("taken"), inline=True))
    reason = str(newest.get("reason") or "")
    detail = (t(backups.REASON_WORDS[reason]) if reason in backups.REASON_WORDS
              and reason != kept_copies.REASON_YOURS else str(newest.get("note") or "").strip())
    return t("manager.backups.latest_why", latest=latest, why=detail) if detail else latest


async def listed(library: Library, subject_kind: str, subject_id: str) -> list[dict[str, Any]]:
    """The copies of one thing. Where they cannot be read the row still draws: an `Unreadable`
    that says why, which is empty like any other list."""
    try:
        said = await offload.io(library.backups, subject_kind, subject_id)
        return list(said.get("copies") or [])
    except Exception as exc:  # noqa: BLE001 - a row that cannot count says so, never breaks the panel
        return Unreadable(why(exc))


@on_page
async def back_up(library: Library, subject_kind: str, subject_id: str, name: str,
                  after: Callable[[], Awaitable[None]], *, place: str = "",
                  walking: bool = False) -> Any:
    """One copy of this game or launcher's settings file, named as `name` suggests.

    Answers what `backups.ask_details` does, for a walk through several."""
    games = [subject_id] if subject_kind == kept_copies.SUBJECT_GAME else []
    launcher = subject_id if subject_kind == kept_copies.SUBJECT_LAUNCHER else ""

    @on_page
    async def take(chosen: str, note: str) -> None:
        try:
            await run.io_bound(lambda: library.take_backup(
                "games" if games else "launcher", games=games, launcher=launcher,
                name=chosen, note=note))
        except Exception as exc:  # noqa: BLE001 - the reason belongs on the page
            ui.notify(t("manager.backups.back_up_failed"), caption=why(exc), type="negative")
            return
        ui.notify(t("manager.backups.backed_up"), type="positive")
        await after()

    return await backups.ask_details(name, take, place=place, walking=walking)


@on_page
async def back_up_games(library: Library, games: list[dict[str, Any]],
                        after: Callable[[], Awaitable[None]] | None = None) -> None:
    """One backup of these games, chosen where the games are."""
    ids = [str(one["id"]) for one in games]

    @on_page
    async def take(chosen: str, note: str) -> None:
        try:
            await run.io_bound(lambda: library.take_backup(
                "games", games=ids, name=chosen, note=note))
        except Exception as exc:  # noqa: BLE001 - the reason belongs on the page
            ui.notify(t("manager.backups.back_up_failed"), caption=why(exc), type="negative")
            return
        ui.notify(t("manager.backups.done_games", count=len(ids)), type="positive")
        if after is not None:
            await after()

    await backups.ask_details(
        kept_copies.games_named(str(one.get("name") or "") for one in games), take)


def entry(library: Library, subject_kind: str, subject_id: str, name: str,
          found: list[dict[str, Any]], after: Callable[[], Awaitable[None]], *,
          back_up_now: bool = True, hint: str = "") -> tuple[Any, Any, panel.Pick]:
    """The Backups fact: a summary that opens the copies. A non-empty `hint` says why Back Up Now
    and Restore are off inside."""
    async def choose() -> None:
        await open_for(library, subject_kind, subject_id, name, after,
                       back_up_now=back_up_now, hint=hint)

    def draw() -> None:
        sub = latest_said(found)
        with ui.element("div").classes("manager-summary").tooltip(t("manager.backups.show")):
            with ui.element("div").classes("manager-summary-text"):
                ui.label(count_said(found)).classes(
                    "manager-fact-value truncate min-w-0"
                    + (" manager-summary-warn" if isinstance(found, Unreadable) else ""))
                if sub:
                    ui.label(sub).classes("manager-summary-sub truncate min-w-0")
            ui.icon(verbs.DRILL).classes("manager-summary-go")
    return (t("manager.section.backups"), draw, panel.Pick(choose))


def _none_said(found: list[dict[str, Any]]) -> str:
    """What the dialog says where it lists nothing: there are none, or why it could not tell."""
    return found.why if isinstance(found, Unreadable) else t("manager.backups.nothing_to_restore")


def _open_in_backups(name: str) -> None:
    ui.navigate.to("/manager?" + urlencode([("view", "backups"), ("find", name)]))


async def open_for(library: Library, subject_kind: str, subject_id: str, name: str,
                   after: Callable[[], Awaitable[None]], *, place: str = "",
                   walking: bool = False, back_up_now: bool = True, hint: str = "") -> Any:
    """The copies of one thing, to restore from or add to. Opens with none too. A non-empty
    `hint` says why Back Up Now and Restore are off. Answers when the dialog closes:
    `walks.STOPPED` if a walk was ended from it, else `walks.SKIPPED`. The panel behind redraws
    once, on close, when a copy was taken or put back."""
    found = await listed(library, subject_kind, subject_id)
    built = backups.rows(found, parts=True)
    by_id = {one["id"]: one for one in built}
    changed: list[bool] = []
    seek = next((str(one["subject_name"]) for one in built if one.get("subject_name")), name)

    with frame.opened(t("manager.backups.title", name=name),
                      classes="manager-backups-card") as box:
        with ui.row().classes("w-full items-center justify-between no-wrap px-3 -mt-2"):
            ui.label(place).classes("manager-help")
            frame.quiet(t("manager.backups.open_in"), lambda: _open_in_backups(seek),
                        icon=verbs.GO)
        with ui.element("div").classes("w-full grow min-h-0 flex flex-col px-3") as body:
            table: ui.aggrid = grid_module.build(
                COLUMNS, built, SCOPE,
                on_select_rows=lambda picked: go.set_enabled(bool(picked) and not hint),
                on_context=lambda row_in: fill(row_in),
                on_header_context=lambda col_id: header(col_id))
            menu: ui.context_menu = ui.context_menu()
        empty = ui.label(_none_said(found)).classes(
            "manager-help manager-backups-none px-3")

        def show(now: list[dict[str, Any]]) -> None:
            empty.set_text(_none_said(now))
            body.set_visibility(bool(by_id))
            empty.set_visibility(not by_id)

        def picked_rows() -> list[dict[str, Any]]:
            return [by_id[str(one["id"])] for one in grid_module.selection(table)
                    if str(one.get("id")) in by_id]

        def fill(row_in: dict | None) -> None:
            one = by_id.get(str((row_in or {}).get("id") or ""))
            if one is None:
                menu.clear()
                return
            on = grid_module.menu_rows(table, one)
            offered = backups.acts(library, on, refresh,
                                   check=len(on) > 1 and bool(grid_module.hidden_count(table)))
            if hint:
                offered[0] = panel.Verb(t("word.restore"), None, hint=hint)
            panel.verb_menu(menu, backups.name_of(one) if len(on) == 1
                            else t("manager.backups.selected_n", count=len(on)),
                            panel.for_selection(offered, len(on)))

        async def header(col_id: str | None) -> None:
            await grid_module.header_menu(menu, table, COLUMNS, col_id)

        @on_page
        async def refresh() -> None:
            now = await listed(library, subject_kind, subject_id)
            fresh = backups.rows(now, parts=True)
            by_id.clear()
            by_id.update({one["id"]: one for one in fresh})
            table.run_grid_method("setGridOption", "rowData", fresh)
            show(now)
            changed.append(True)

        async def take() -> None:
            await back_up(library, subject_kind, subject_id, name, refresh)

        @on_page
        async def put_back() -> None:
            restored = False

            async def refreshed() -> None:
                nonlocal restored
                restored = True
                await refresh()

            await backups.restore(library, picked_rows(), refreshed,
                                  check=bool(grid_module.hidden_count(table)))
            if walking and restored:
                box.close()

        with frame.footer():
            if back_up_now:
                aside = frame.aside(t("manager.backups.take_now"), take, icon=verbs.ADD)
                aside.set_enabled(not hint)
                if hint:
                    aside.tooltip(hint)
            if walking:
                frame.leave(box, True)
            else:
                frame.cancel(box.close, t("word.close"))
            go = frame.answer(t("word.restore"), put_back, icon=verbs.RESTORE)
        go.set_enabled(False)
        show(found)
        if hint:
            go.tooltip(hint)
    answer = await box or walks.SKIPPED
    if changed:
        await after()
    return answer
