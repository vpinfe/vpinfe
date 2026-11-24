"""Go back to 2.x: the count first, then VPinFE quits and puts the copies back."""

from __future__ import annotations

from typing import Any

from nicegui import run, ui

from common.backups import copies as kept_copies
from common.failures import why
from common.i18n import t
from manager.kit import confirm, verbs
from manager.kit.data import Library
from manager.kit.on_page import on_page


def available(rows: list[dict[str, Any]]) -> bool:
    """Whether a listed copy is one going back could put in place: a game's or the
    collections' own 2.x file, of something still there."""
    return any(row["written_by"] == kept_copies.BY_2X and not row["gone"] and not row["within"]
               and row["kind"] in (kept_copies.KIND_GAME, kept_copies.KIND_COLLECTIONS)
               for row in rows)


@on_page
async def ask(library: Library) -> None:
    try:
        found = await run.io_bound(library.going_back_plan) or {}
    except Exception as exc:  # noqa: BLE001 - the reason belongs on the page
        ui.notify(t("said.action_failed"), caption=why(exc), type="negative")
        return
    games, collections = int(found.get("games") or 0), bool(found.get("collections"))
    if not games and not collections:
        ui.notify(t("error.backups.no_2x"), type="warning")
        return
    lines = [t("manager.backups.go_back_games", count=games)] if games else []
    if collections:
        lines.append(t("manager.backups.go_back_collections"))
    if not await confirm.ask(
            t("manager.backups.go_back_ask"),
            detail=[t("manager.backups.go_back_detail"), t("manager.backups.go_back_kept")],
            lines=lines, confirm=t("manager.backups.go_back_confirm"), icon=verbs.RESTORE,
            danger=False):
        return
    try:
        await run.io_bound(library.go_back_to_2x)
    except Exception as exc:  # noqa: BLE001 - the reason belongs on the page
        ui.notify(t("said.action_failed"), caption=why(exc), type="negative")
        return
    ui.notify(t("manager.backups.going_back"), type="positive")
