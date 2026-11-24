"""Changing the backups folder: what becomes of the backups already taken.

Asked once, when the folder is changed. Move makes the new folder the setting and carries
the backups across as a job; Leave them saves the setting and the old folder is no longer
listed, so the question names where they stay.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from nicegui import background_tasks, ui

from common.failures import why
from common.i18n import t
from manager.kit import confirm, offload, verbs
from manager.kit.data import Library
from manager.kit.on_page import on_page

logger = logging.getLogger("vpinfe.manager.backups.backups_move")

_POLL_S = 1.0
_POLLS = 7200


def asked_first(library: Library, save: Callable[[Any], Any],
                refill: Callable[[], None]) -> Callable[[Any], Any]:
    """`save`, once the person has said what happens to the backups in the folder being
    left. Nothing is asked when that folder holds none."""
    async def asked(value: Any) -> bool:
        folder = str(value or "")
        try:
            found = await offload.io(library.backups_folder_change, folder)
        except Exception:  # noqa: BLE001 - the question is lost, not the save
            logger.warning("Could not tell what is in the old backups folder", exc_info=True)
            return bool(await save(value))
        moving = int(found.get("moving") or 0)
        if not moving:
            return bool(await save(value))
        old = str(found.get("old") or "")
        stays = t("manager.backups_move.stays", folder=old)
        if found.get("problem"):
            if not await confirm.ask(
                    t("manager.backups_move.blocked", count=moving),
                    detail=[str(found["problem"]), stays],
                    confirm=t("manager.backups_move.leave"), icon=verbs.KEEP, danger=False):
                refill()
                return False
            return bool(await save(value))
        already = int(found.get("already") or 0)
        answer = await confirm.ask(
            t("manager.backups_move.ask", count=moving),
            detail=[*([t("manager.backups_move.in_new", count=already)] if already else []),
                    t("manager.backups_move.same_name"), stays],
            confirm=t("word.move"), icon=verbs.MOVE, danger=False,
            second=(t("manager.backups_move.leave"), verbs.KEEP))
        if answer == confirm.Answer.CANCEL:
            refill()
            return False
        if answer == confirm.Answer.SECOND:
            return bool(await save(value))
        return await _move(library, folder, refill)

    return asked


@on_page
async def _move(library: Library, folder: str, refill: Callable[[], None]) -> bool:
    try:
        job = await offload.io(library.move_backups_folder, folder)
    except Exception as exc:  # noqa: BLE001 - said, and nothing changed
        ui.notify(t("manager.backups_move.could_not_start"), caption=why(exc), type="warning")
        refill()
        return False
    ui.notify(t("manager.backups_move.started"), type="info")
    background_tasks.create(_report(library, str(job.get("id") or "")),
                            name="manager-backups-move")
    return True


@on_page
async def _report(library: Library, job_id: str) -> None:
    """Say how the move ended. The Jobs view has it either way."""
    for _ in range(_POLLS):
        await asyncio.sleep(_POLL_S)
        try:
            found = await offload.io(library.job, job_id)
        except Exception:  # noqa: BLE001 - the Jobs view still has it
            return
        if found.get("state") == "running":
            continue
        if found.get("state") == "failed":
            ui.notify(t("manager.backups_move.failed"), caption=str(found.get("error") or ""),
                      type="negative")
            return
        result = found.get("result") or {}
        left, failed = len(result.get("left") or ()), len(result.get("failed") or ())
        said = t("manager.backups_move.moved", count=int(result.get("moved") or 0))
        if failed:
            ui.notify(said, caption=t("manager.backups_move.failed_some", count=failed,
                                      folder=str(result.get("from") or "")), type="warning")
        elif left:
            ui.notify(said, caption=t("manager.backups_move.left_some", count=left), type="info")
        else:
            ui.notify(said, type="positive")
        return
