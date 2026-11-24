"""Clean up: ready-made groups of copies, ticked by hand and deleted once."""

from __future__ import annotations

import calendar
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from nicegui import run, ui

from common import i18n
from common.backups import copies as kept_copies
from common.failures import why
from common.i18n import t
from manager.kit import confirm, panel, verbs, when
from manager.kit import dialog as frame
from manager.kit.data import Library
from manager.kit.on_page import on_page

TWO_X = "two_x"
GONE = "gone"
YOURS = "yours"
AUTOMATIC = "automatic"
GROUPS = (TWO_X, GONE, YOURS, AUTOMATIC)
AGED = (YOURS, AUTOMATIC)

START_MONTHS = 3
MOST_MONTHS = 120

GROUP_WORDS = {
    TWO_X: "manager.backups.group.two_x",
    GONE: "manager.backups.gone",
    YOURS: "manager.backups.group.yours",
    AUTOMATIC: "manager.backups.group.automatic",
}
# What the confirm names where an age is part of the group.
LINE_WORDS = {
    TWO_X: "manager.backups.group.two_x",
    GONE: "manager.backups.gone",
    YOURS: "manager.backups.group.yours_age",
    AUTOMATIC: "manager.backups.group.automatic_age",
}


def months_before(now: datetime, months: int) -> datetime:
    """The same moment `months` calendar months ago, held to the last day of a shorter month."""
    index = now.year * 12 + now.month - 1 - months
    year, month = divmod(index, 12)
    last = calendar.monthrange(year, month + 1)[1]
    return now.replace(year=year, month=month + 1, day=min(now.day, last))


def members(rows: list[dict[str, Any]], months: dict[str, int],
            now: datetime | None = None) -> dict[str, list[dict[str, Any]]]:
    """The listed copies each group would delete. A copy with no date is in no group that
    asks its age, and a copy can be in several groups."""
    now = now or datetime.now(UTC)
    before = {group: months_before(now, months[group]) for group in AGED}
    found: dict[str, list[dict[str, Any]]] = {group: [] for group in GROUPS}
    for row in rows:
        if row["within"]:
            continue
        if row["written_by"] == kept_copies.BY_2X:
            found[TWO_X].append(row)
        if row["gone"]:
            found[GONE].append(row)
        taken = when.parsed(row["taken"])
        if taken is None:
            continue
        group = YOURS if row["reason"] == kept_copies.REASON_YOURS else AUTOMATIC
        if taken < before[group]:
            found[group].append(row)
    return found


def union(found: dict[str, list[dict[str, Any]]], ticked: set[str]) -> list[dict[str, Any]]:
    """Each copy once, whichever of the ticked groups holds it."""
    chosen: dict[str, dict[str, Any]] = {}
    for group in GROUPS:
        if group in ticked:
            chosen.update({row["id"]: row for row in found[group]})
    return list(chosen.values())


def counted(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return t("word.none")
    return t("manager.backups.summary", count=len(rows),
             size=i18n.size(sum(int(one["size"]) for one in rows)))


@on_page
async def ask(library: Library, rows: list[dict[str, Any]],
              refresh: Callable[[], Awaitable[None]]) -> None:
    """`rows` is the page's own list, which `refresh` reads again in place."""
    await refresh()
    if not rows:
        return
    months = {group: START_MONTHS for group in AGED}
    ticked: set[str] = set()
    held: dict[str, Any] = {}

    def found() -> dict[str, list[dict[str, Any]]]:
        return members(rows, months)

    def update() -> None:
        now = found()
        for group in GROUPS:
            held[group].text = counted(now[group])
        chosen = union(now, ticked)
        held["total"].text = counted(chosen) if ticked else ""
        held["delete"].set_enabled(bool(chosen))

    def tick(group: str, value: bool) -> None:
        (ticked.add if value else ticked.discard)(group)
        update()

    def age(group: str, value: Any) -> None:
        if value:
            months[group] = max(1, min(MOST_MONTHS, int(value)))
            update()

    def draw(group: str) -> Callable[[], None]:
        def inside() -> None:
            with ui.row().classes("items-center gap-3 no-wrap"):
                panel.switch(False, lambda e: tick(group, bool(e.value)))()
                if group in AGED:
                    panel.number(months[group], lambda e: age(group, e.value),
                                 low=1, high=MOST_MONTHS,
                                 unit=t("manager.backups.group.months"))()
                held[group] = ui.label("").classes("manager-help")
        return inside

    @on_page
    async def go() -> None:
        now = found()
        chosen = union(now, ticked)
        if not chosen:
            return
        held["delete"].disable()
        lines = [t("manager.backups.clean_line", group=t(LINE_WORDS[group],
                                                          count=months.get(group, 0)),
                   counted=counted(now[group]))
                 for group in GROUPS if group in ticked]
        detail = [t("manager.backups.delete_detail")]
        if any(row["written_by"] == kept_copies.BY_2X for row in chosen):
            detail.append(t("manager.backups.group.two_x_help"))
        question = t("manager.backups.delete_one") if len(chosen) == 1 \
            else t("manager.backups.delete_many", count=len(chosen))
        if not await confirm.ask(question, detail=detail, lines=lines,
                                 confirm=t("word.delete"), icon=verbs.DELETE):
            held["delete"].enable()
            return
        box.close()
        for row in chosen:
            try:
                await run.io_bound(library.delete_backup, row["id"])
            except Exception as exc:  # noqa: BLE001 - the reason belongs on the page
                ui.notify(t("said.delete_failed"), caption=why(exc), type="negative")
                await refresh()
                return
        ui.notify(t("manager.backups.deleted", count=len(chosen)), type="positive")
        await refresh()

    with frame.opened(t("manager.backups.clean_title")) as box:
        panel.facts(ui, [
            (t(GROUP_WORDS[TWO_X]), draw(TWO_X)),
            (panel.ASIDE, lambda: panel.line(t("manager.backups.group.two_x_help"))),
            (t(GROUP_WORDS[GONE]), draw(GONE)),
            (t(GROUP_WORDS[YOURS]), draw(YOURS)),
            (t(GROUP_WORDS[AUTOMATIC]), draw(AUTOMATIC)),
        ])
        held["total"] = ui.label("").classes("manager-help")
        with frame.footer():
            frame.cancel(box.close)
            held["delete"] = frame.answer(t("word.delete"), go, icon=verbs.DELETE, danger=True)
    update()
    box.open()
