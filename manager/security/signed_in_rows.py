"""The Signed In row on Settings > Security: how many browsers another device signed in, and
the list of them to pick from and sign out.

The row says the count and when the newest was last used and opens the list in a grid, so
the verbs and the filters are the ones every other grid has.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from nicegui import run, ui

from common.i18n import t
from common.security import sign_in
from manager.kit import confirm, offload, panel, renderers, verbs, when
from manager.kit import dialog as frame
from manager.kit import grid as grid_module
from manager.kit.data import Library
from manager.kit.on_page import on_page

SCOPE = "security.signed_in"

_BROWSER = (
    "params => {" + renderers._ESCAPE +
    f" const here = esc({json.dumps(t('manager.security.this_browser'))});"
    " const said = esc(params.value == null ? '' : params.value);"
    " return (params.data || {}).here ? said + ' <span class=\"manager-member-chip"
    " manager-cell-chip manager-chip-quiet\">' + here + '</span>' : said; }"
)

COLUMNS = [
    grid_module.identifier("browser", t("manager.security.col_browser"), 200, flex=1,
                           help=grid_module.NO_HELP,
                           **{":cellRenderer": _BROWSER}),
    grid_module.column("address", t("manager.security.col_address"), 130,
                       help=grid_module.NO_HELP),
    grid_module.column("access", t("manager.security.col_access"), 90,
                       help=t("manager.security.col_access.help")),
    grid_module.column("created", t("manager.security.col_first_seen"), 130,
                       help=t("manager.security.col_first_seen.help"),
                       tooltipField="created_at",
                       **{":valueFormatter": "params => (params.data || {}).created_day || ''"}),
    grid_module.column("last_used", t("manager.security.col_last_used"), 160,
                       help=t("manager.security.col_last_used.help"), **when.cell("last_used")),
]


def kind_label(kind: str) -> str:
    return t("manager.security.kind_remote" if kind == sign_in.REMOTE
             else "manager.security.kind_owner")


def _stamp(epoch: Any) -> str:
    """UTC, so the columns sort on it as text and agree with each other."""
    return datetime.fromtimestamp(float(epoch or 0), UTC).isoformat()


def rows(sessions: list[dict[str, Any]], mine: str) -> list[dict[str, Any]]:
    """The sessions as grid rows, the most recently used first."""
    out = []
    for one in sorted(sessions, key=lambda row: float(row.get("last_used") or 0), reverse=True):
        made = _stamp(one.get("created"))
        out.append(when.said({
            "id": str(one["id"]),
            "browser": str(one.get("agent") or "") or t("manager.security.browser_unknown"),
            "here": str(one["id"]) == mine,
            "address": str(one.get("address") or ""),
            "access": kind_label(str(one.get("kind") or "")),
            "created": made, "created_at": when.local(made),
            "created_day": when.day(when.from_epoch(float(one.get("created") or 0))),
            "last_used": _stamp(one.get("last_used"))}, "last_used"))
    return out


SIGN_OUT_HERE = ("async () => { await fetch('/api/v1/auth/sign_out', "
                 "{method: 'POST', credentials: 'same-origin'}); location.replace('/signin'); }")


async def end_sessions(library: Library, ids: list[str], mine: str) -> bool:
    """Sign these browsers out, except the one drawing the page: its own sign-in is ended by
    the page itself, since once it is gone the Manager has no session to call with. True when
    that is still to do."""
    for session_id in ids:
        if session_id != mine:
            await run.io_bound(library.sign_out_session, session_id)
    return mine in ids


def count_said(sessions: list[dict[str, Any]]) -> str:
    """How many browsers are signed in: `3 browsers`."""
    return t("manager.security.count", count=len(sessions))


def latest_said(sessions: list[dict[str, Any]]) -> str:
    """When the newest was last used, under the count: `Last used 2 hours ago`."""
    newest = max(float(one.get("last_used") or 0) for one in sessions)
    return t("manager.security.last_used", when=when.ago(_stamp(newest), inline=True))


def entry(label: str, sessions: list[dict[str, Any]],
          choose: Callable[[], Awaitable[None]]) -> tuple[Any, ...]:
    """The Signed In fact: a summary that opens the list, or the plain words where nobody
    is signed in."""
    if not sessions:
        return (label, t("manager.security.none"))

    def draw() -> None:
        with ui.element("div").classes("manager-summary").tooltip(t("manager.security.show_list")):
            with ui.element("div").classes("manager-summary-text"):
                ui.label(count_said(sessions)).classes("manager-fact-value truncate min-w-0")
                ui.label(latest_said(sessions)).classes("manager-summary-sub truncate min-w-0")
            ui.icon(verbs.DRILL).classes("manager-summary-go")

    return (label, draw, panel.Pick(choose))


async def open_list(library: Library, sessions: list[dict[str, Any]], mine: str,
                    after: Callable[[], Awaitable[None]]) -> None:
    """The browsers signed in, to pick from and sign out. The page behind redraws once, on
    close, when anyone was signed out."""
    built = rows(sessions, mine)
    by_id = {one["id"]: one for one in built}
    changed: list[bool] = []

    with frame.opened(t("manager.security.signed_in"),
                      classes="manager-signed-in-card") as box:
        with ui.element("div").classes("w-full grow min-h-0 flex flex-col px-3"):
            table: ui.aggrid = grid_module.build(
                COLUMNS, built, SCOPE,
                on_select_rows=lambda picked: go.set_enabled(bool(picked)),
                on_context=lambda row_in: fill(row_in),
                on_header_context=lambda col_id: header(col_id))
            menu: ui.context_menu = ui.context_menu()

        def picked_ids() -> list[str]:
            return [str(one["id"]) for one in grid_module.selection(table)
                    if str(one.get("id")) in by_id]

        def fill(row_in: dict | None) -> None:
            one = by_id.get(str((row_in or {}).get("id") or ""))
            if one is None:
                menu.clear()
                return
            on = [str(row["id"]) for row in grid_module.menu_rows(table, one)]
            panel.verb_menu(menu, one["browser"] if len(on) == 1
                            else t("manager.security.selected_n", count=len(on)),
                            [panel.Verb(t("manager.security.sign_out"), lambda: sign_out(on))])

        async def header(col_id: str | None) -> None:
            await grid_module.header_menu(menu, table, COLUMNS, col_id)

        @on_page
        async def refresh() -> None:
            now = rows(await offload.io(library.sessions), mine)
            by_id.clear()
            by_id.update({one["id"]: one for one in now})
            if not now:
                box.close()
                return
            table.run_grid_method("setGridOption", "rowData", now)

        @on_page
        async def sign_out(ids: list[str]) -> None:
            if mine in ids and not await confirm.ask(
                    t("manager.security.here_question"),
                    detail=t("manager.security.here_detail"),
                    confirm=t("manager.security.sign_out"), icon=verbs.SIGN_OUT,
                    danger=False):
                return
            changed.append(True)
            if await end_sessions(library, ids, mine):
                ui.run_javascript(f"({SIGN_OUT_HERE})()")
                return
            await refresh()

        @on_page
        async def sign_out_everyone() -> None:
            if not await confirm.ask(
                    t("manager.security.everyone_question"),
                    detail=t("manager.security.everyone_detail"),
                    confirm=t("manager.security.sign_out_all"), icon=verbs.SIGN_OUT,
                    danger=False):
                return
            await run.io_bound(library.sign_out_everyone)
            if mine:
                ui.run_javascript(f"({SIGN_OUT_HERE})()")
                return
            changed.append(True)
            box.close()

        with frame.footer():
            frame.aside(t("manager.security.sign_out_all"), sign_out_everyone,
                        icon=verbs.SIGN_OUT).tooltip(t("manager.security.everyone_hint"))
            frame.cancel(box.close, t("word.close"))
            go = frame.answer(t("manager.security.sign_out"), lambda: sign_out(picked_ids()),
                              icon=verbs.SIGN_OUT)
        go.set_enabled(False)
    await box
    if changed:
        await after()
