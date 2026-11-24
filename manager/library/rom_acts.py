"""Replacing and removing a game's ROM files, asked the same way from the grids and the panel.

A ROM file is read by the tables whose ROM name it carries, so both acts say who loses it.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from pathlib import PurePosixPath
from typing import Any

from nicegui import run, ui

from common.failures import why
from common.i18n import t
from manager.kit import confirm, offload, verbs
from manager.kit.on_page import on_page
from manager.library import mediasource

logger = logging.getLogger("vpinfe.manager.library.rom_acts")

KIND = "rom"


def users_of(row: dict[str, Any], tables: Sequence[dict[str, Any]]) -> list[str]:
    """The names of the tables that read this file, as their files are called."""
    wanted = {str(one) for one in row.get("users") or []}
    return [PurePosixPath(str(table.get("filename") or table.get("id") or "")).stem
            for table in tables if str(table.get("id")) in wanted]


def removal(row: dict[str, Any], tables: Sequence[dict[str, Any]]) -> tuple[str, list[str]]:
    """What Remove says of this file, and the tables it names."""
    users = users_of(row, tables)
    return t("manager.workbench.remove_asset.rom_used" if users
             else "manager.workbench.remove_asset.rom_unused"), users


async def _modified(library: Any, game_id: str, path: str) -> str | None:
    """When the file last changed, or None where that cannot be read."""
    try:
        return (await offload.io(library.asset_detail, game_id, path)).get("modified")
    except Exception:  # noqa: BLE001 - unreadable reads as changed, so nothing is removed
        logger.debug("No detail for %s", path, exc_info=True)
        return None


@on_page
async def replace(context: dict[str, Any], row: dict[str, Any],
                  done: Callable[[], Awaitable[Any]]) -> None:
    """Take new files in beside this one, then drop it unless one of them has its name.

    The new file keeps its own name, which is what a table asks for, so the ask is made
    before the name is known and says what a table loses if it differs.
    """
    library, game_id = context["library"], context["game_id"]
    name, path = str(row.get("file") or ""), str(row.get("path") or "")
    try:
        tables = await offload.io(library.tables_for, game_id)
    except Exception:  # noqa: BLE001 - the ask is made without the table names
        logger.debug("No tables for %s", game_id, exc_info=True)
        tables = []
    users = users_of(row, tables)
    if users and not await confirm.ask(
            t("manager.rom.replace_title", name=name), detail=t("manager.rom.replace_detail"),
            lines=users, confirm=t("word.replace"), icon=verbs.REPLACE):
        return
    before = {str(one.get("file")) for one in await offload.io(library.roms_of, game_id)}
    stamp = await _modified(library, game_id, path)

    @on_page
    async def imported() -> None:
        held = [str(one.get("file")) for one in await offload.io(library.roms_of, game_id)]
        fresh = [one for one in held if one not in before]
        untouched = stamp is not None and await _modified(library, game_id, path) == stamp
        if fresh and name in held and untouched:
            try:
                await run.io_bound(library.remove_asset, game_id, path)
            except Exception as exc:  # noqa: BLE001
                ui.notify(t("said.remove_failed"), caption=why(exc), type="negative")
            else:
                ui.notify(t("manager.rom.replaced", old=name, new=", ".join(fresh)),
                          type="positive")
        await done()

    mediasource.open_folder_sources(context, KIND, str(row.get("label") or name), imported,
                                    path)
