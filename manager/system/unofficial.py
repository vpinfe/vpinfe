"""Unofficial extensions that are running, and the one way to stop them: switch each off and
restart VPinFE, so a report is made from a run without them.

Shared by About, which names them, and by the Support Package, which cannot be made while
they run.
"""

from __future__ import annotations

import logging

from nicegui import ui

from common import extensions
from common.failures import why
from common.i18n import t
from common.support import reopen
from manager.kit import confirm, offload, panel, verbs
from manager.kit.api import ApiClient
from manager.kit.on_page import on_page

logger = logging.getLogger("vpinfe.manager.system.unofficial")


def running() -> list[str]:
    """The names of the unofficial extensions running now."""
    return [one.name for one in extensions.unofficial_running()]


def named(names: list[str]) -> str:
    return ", ".join(names)


@on_page
async def turn_off_and_restart(names: list[str], *, reopen_package: bool) -> bool:
    """Ask, switch each off, and restart. True once the restart has been requested.
    `reopen_package` leaves the note that opens the Support Package again afterwards."""
    label = t("manager.unofficial.turn_off")
    detail = [t("manager.unofficial.detail", names=named(names))]
    if reopen_package:
        detail.append(t("manager.unofficial.detail_package"))
    detail.append(t("manager.unofficial.detail_back"))
    if not await confirm.ask(t("manager.unofficial.ask"), detail=detail, confirm=label,
                             icon=verbs.RESET, danger=False):
        return False
    try:
        done = await offload.io(_switch_off_and_restart, names, reopen_package)
    except Exception as exc:  # noqa: BLE001 - the reason belongs on the page
        reopen.take()
        ui.notify(t("said.action_failed"), caption=why(exc), type="negative")
        return False
    if not done.get("performed"):
        reopen.take()
        ui.notify(t("manager.devices.not_happen", label=label), type="warning")
        return False
    ui.notify(t("manager.unofficial.restarting"), type="info")
    return True


def _switch_off_and_restart(names: list[str], reopen_package: bool) -> dict:
    """All three steps in one call on a worker thread. Switching an extension off redraws
    the page, which cancels a handler still waiting between the steps, so the restart
    would never be asked for."""
    for name in names:
        extensions.switch(name, False)
    if reopen_package:
        reopen.ask()
    return ApiClient().perform_action("vpinfe", "restart")


def draw_line(names: list[str]) -> None:
    """The line About carries while they run: which ones, and the way to turn them off."""
    with ui.element("div").classes("manager-attention w-full"):
        ui.icon("error_outline").classes("manager-attention-icon")
        ui.label(t("manager.unofficial.running", names=named(names))) \
            .classes("manager-attention-line grow")
        panel.action(t("manager.unofficial.turn_off"),
                     lambda: turn_off_and_restart(names, reopen_package=False),
                     icon=verbs.RESET, hint=t("manager.unofficial.turn_off_hint"))()
