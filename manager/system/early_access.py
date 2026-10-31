"""The offer, on a beta release's last page, to take each new beta as it is published.

It is the `device/update_channel` setting Settings edits, written the moment the switch
moves. The first draw on an install sets the Beta channel and marks the install's row; after
that the switch only reads the setting.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import Any

from nicegui import background_tasks, run, ui

from common.devices import device_registry
from common.failures import why
from common.i18n import t
from common.online import app_updater
from common.release import release_notes
from manager.kit import offload, panel
from manager.kit.api import ApiClient
from manager.kit.on_page import on_page

logger = logging.getLogger("vpinfe.manager.system.early_access")

SECTION, KEY = app_updater.CHANNEL_SETTING
OFFERED_KEY = "early_access_offered"


@dataclass
class _Offer:
    """What the switch shows, kept here so a redraw of the page draws what was last set."""

    on: bool
    first: bool


def _set(on: bool) -> None:
    ApiClient().put_config({SECTION: {KEY: app_updater.BETA if on else app_updater.STABLE}})


def _first() -> None:
    _set(True)
    device_registry.get_device_registry().set_self(**{OFFERED_KEY: True})


def _read() -> _Offer:
    first = not device_registry.get_device_registry().self_get(OFFERED_KEY, False)
    return _Offer(on=first or app_updater.channel() == app_updater.BETA, first=first)


def _draw(state: _Offer) -> Callable[[], None]:
    held: list[Any] = []

    def _show(on: bool) -> None:
        state.on = on
        for control in held:
            control.set_value(on)

    @on_page
    async def changed(event: Any) -> None:
        on = bool(event.value)
        if on == state.on:
            return
        try:
            await run.io_bound(_set, on)
        except Exception as exc:  # noqa: BLE001 - the reason belongs on the page
            ui.notify(t("manager.settings.save_failed"), caption=why(exc), type="negative")
            _show(state.on)
        else:
            state.on = on

    @on_page
    async def settle() -> None:
        try:
            await run.io_bound(_first)
        except Exception as exc:  # noqa: BLE001 - the reason belongs on the page
            ui.notify(t("manager.settings.save_failed"), caption=why(exc), type="negative")
            _show(await offload.io(lambda: app_updater.channel() == app_updater.BETA))

    def draw() -> None:
        held.clear()
        if state.first:
            state.first = False
            background_tasks.create(settle(), name="early-access-first")
        with ui.element("div").classes("w-full mt-4"):
            panel.facts(ui, [
                (panel.HEADING, t("manager.early_access.title")),
                (panel.LEDE, partial(panel.line, t("manager.early_access.note"))),
                (t("manager.early_access.switch"),
                 panel.switch(state.on, changed, on_control=lambda control: held.append(control))),
            ])

    return draw


async def offer(version: str, *, again: bool = False) -> Callable[[], None] | None:
    """What a last page draws below its own content, or None where there is nothing to
    offer: a release that is not a beta, a page asked for again, or a setting that cannot
    be read. The page never waits on this failing."""
    if again or not release_notes.is_prerelease(version):
        return None
    try:
        return _draw(await offload.io(_read))
    except Exception:
        logger.warning("Could not read the early access setting", exc_info=True)
        return None
