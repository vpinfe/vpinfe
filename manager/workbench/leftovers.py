"""The notice on a launcher's page that offers to move what an upgrade left behind.

Not Now keeps the notice to one line, remembered over every setting it lists, and sets the
Overview row aside over the ones a move acts on. Either comes back when a later upgrade
leaves more behind.
"""

from __future__ import annotations

import logging
from typing import Any

from nicegui import run, ui

from common.apps.contract import PLACE, TURN_ON
from common.failures import why
from common.i18n import t
from manager.kit import leftover_ids, offload, order_notice, panel, undo, verbs
from manager.kit.on_page import on_page

logger = logging.getLogger("vpinfe.manager.workbench.leftovers")

ROW, NOTICE = leftover_ids.ROW, leftover_ids.NOTICE


def is_set_aside(every: tuple[str, ...], record: dict[str, list[str]]) -> bool:
    """Whether every setting the notice lists was dismissed with it."""
    return bool(every) and set(every) <= set(record.get(NOTICE, ()))


async def notice(context: dict[str, Any]) -> None:
    """Draw the notice above the launcher's sections, or nothing where its program left
    nothing behind or the read failed."""
    library, launcher = context["library"], context["launcher"]
    launcher_id = str(launcher["launcher_id"])
    if not launcher.get("has_config"):
        return
    try:
        found = await offload.io(library.launcher_leftovers, launcher_id)
    except Exception:  # noqa: BLE001 - the page is about the launcher, not about this
        logger.debug("manager: could not read what an upgrade left behind", exc_info=True)
        return
    items = list(found.get("items") or [])
    if not items:
        return
    ids = leftover_ids.of(launcher_id, items)
    every = leftover_ids.every(launcher_id, items)
    record = await _record(library, launcher_id, every)
    opened = context["state"].setdefault("leftovers_open", set())
    if is_set_aside(every, record) and launcher_id not in opened:
        _one_line(context, launcher_id, len(every))
        return
    _full(context, launcher_id, items, ids, every)


async def _record(library: Any, launcher_id: str, every: tuple[str, ...]) -> dict[str, list[str]]:
    """What was dismissed, read now: only Overview loads it, and this page may be the first
    one drawn. Ids of this launcher that name nothing it lists any more are dropped."""
    record = await offload.io(library.refresh_dismissed)
    own = [one for one in record.get(NOTICE, ()) if one.startswith(leftover_ids.scope(launcher_id))]
    stale = [one for one in own if one not in every]
    if stale:
        try:
            await run.io_bound(library.merge_dismissed, NOTICE,
                               [one for one in own if one in every],
                               scope=leftover_ids.scope(launcher_id))
        except Exception:  # noqa: BLE001 - the next draw tries again
            logger.debug("manager: could not forget old dismissals", exc_info=True)
    return record


def _one_line(context: dict[str, Any], launcher_id: str, count: int) -> None:
    async def review() -> None:
        context["state"]["leftovers_open"].add(launcher_id)
        await context["rebuild"]()

    with ui.element("div").classes("manager-attention w-full mb-2"):
        ui.icon("error_outline").classes("manager-attention-icon")
        ui.label(t("manager.leftovers.line", count=count)).classes("manager-attention-line grow")
        panel.action(t("manager.leftovers.review"), review, icon=verbs.EXPAND,
                     hint=panel.NO_HINT)()


def _full(context: dict[str, Any], launcher_id: str, items: list[dict[str, Any]],
          ids: tuple[str, ...], every: tuple[str, ...]) -> None:
    library = context["library"]

    async def settled() -> None:
        context["state"]["leftovers_open"].discard(launcher_id)
        await context["rebuild"]()

    @on_page
    async def back(token: str) -> None:
        done = await run.io_bound(library.undo_launcher_leftovers, launcher_id, token) or {}
        order_notice.tell(done.get("order_failed"))
        await settled()

    @on_page
    async def move() -> None:
        try:
            done = await run.io_bound(library.move_launcher_leftovers, launcher_id) or {}
        except Exception as exc:  # noqa: BLE001 - the reason belongs on screen
            logger.warning("manager: leftovers were not moved", exc_info=True)
            ui.notify(t("manager.leftovers.could_not"), caption=why(exc), type="negative")
            return
        held_back = list(done.get("held_back") or [])
        files = sorted({str(one["path"]) for each in held_back
                        for one in each.get("files") or ()})
        if files:
            ui.notify(t("manager.leftovers.held_back", count=len(files)),
                      caption="\n".join(files), type="warning", multi_line=True)
        if limit := max((int(each.get("limit") or 0) for each in held_back), default=0):
            ui.notify(t("manager.leftovers.held_over", limit=limit), type="warning")
        order_notice.tell(done.get("order_failed"))
        token = str(done.get("undo") or "")
        if token:
            undo.offer(t("manager.leftovers.moved", count=len(done.get("moved") or ())),
                       lambda: back(token), done=t("manager.leftovers.put_back"))
        await settled()

    @on_page
    async def turn_on(key: str) -> None:
        try:
            done = await run.io_bound(library.turn_on_launcher_leftover, launcher_id, key) or {}
        except Exception as exc:  # noqa: BLE001 - the reason belongs on screen
            logger.warning("manager: a setting was not turned on", exc_info=True)
            ui.notify(t("manager.leftovers.could_not"), caption=why(exc), type="negative")
            return
        token = str(done.get("undo") or "")
        undo.offer(t("manager.leftovers.turned_on"), lambda: back(token),
                   done=t("manager.leftovers.put_back"))
        await settled()

    @on_page
    async def not_now() -> None:
        scope = leftover_ids.scope(launcher_id)
        try:
            await run.io_bound(library.merge_dismissed, NOTICE, list(every), scope=scope)
            if ids:
                await run.io_bound(library.merge_dismissed, ROW, list(ids), scope=scope)
        except Exception as exc:  # noqa: BLE001 - the reason belongs on screen
            logger.warning("manager: a dismissal was not saved", exc_info=True)
            ui.notify(t("manager.needs.could_not"), caption=why(exc), type="negative")
            return
        await settled()

    with ui.column().classes("w-full gap-1 mb-2"):
        with ui.element("div").classes("manager-attention w-full"):
            ui.icon("error_outline").classes("manager-attention-icon")
            ui.label(t("manager.leftovers.heading")).classes("manager-attention-line grow")
            if ids:
                panel.action(t("manager.leftovers.move"), move, icon=verbs.MOVE,
                             hint=t("manager.leftovers.move.help"))()
            panel.action(t("manager.leftovers.not_now"), not_now, icon=verbs.SKIP,
                         hint=t("manager.leftovers.not_now.help"))()
        for one in items:
            _row(one, turn_on)


def _row(one: dict[str, Any], turn_on: Any) -> None:
    with ui.row().classes("items-center gap-2 w-full no-wrap"):
        ui.label(str(one.get("text") or one["key"])).classes("manager-help")
        if one.get("action") == PLACE:
            ui.label(t("manager.leftovers.files", count=int(one.get("files") or 0))
                     if one.get("files") else t("manager.leftovers.no_files")) \
                .classes("manager-help")
        if one.get("action") == TURN_ON:
            panel.action(t("manager.leftovers.turn_on"), lambda key=one["key"]: turn_on(key),
                         icon=verbs.POWER, hint=panel.NO_HINT)()
