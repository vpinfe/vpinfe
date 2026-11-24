"""Presets: Cabinet and Desktop, each a Cabinet Mode and a View Mode set together.

A preset sets VPinFE's Cabinet Mode and the View Mode of one Visual Pinball launcher, the
default launcher where no launcher is named. Any View Mode other than Cabinet counts as
Desktop, so a launcher on Desktop, Never FSS matches Desktop and is left as it is.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import Any

from nicegui import run, ui

from apps.vpx import screens as vpx
from common import apps
from common.failures import why
from common.games import launchers as launcher_model
from common.i18n import t
from manager.kit import dialog, offload, undo, verbs
from manager.kit import panel as panel_kit
from manager.kit.api import ApiClient
from manager.kit.on_page import on_page
from manager.settings import displays

logger = logging.getLogger("vpinfe.manager.settings.presets")

CABINET_VIEW = vpx.CABINET_VIEW
PROGRAM = "vpx"
DESKTOP_VIEW = "0"
CAB_MODE = "cab_mode"
VIEW_MODE = "view_mode"


@dataclass(frozen=True)
class Preset:
    key: str
    cabinet: bool

    @property
    def name(self) -> str:
        return t("manager.presets.cabinet" if self.cabinet else "manager.presets.desktop")

    @property
    def help(self) -> str:
        return t("manager.presets.cabinet_help" if self.cabinet
                 else "manager.presets.desktop_help")


CABINET = Preset("cabinet", True)
DESKTOP = Preset("desktop", False)
PRESETS = (CABINET, DESKTOP)


@dataclass(frozen=True)
class Now:
    """What the settings are now. `view` is the launcher's View Mode as it is stored, or
    None where there is no launcher with a settings file to hold one."""
    cabinet: bool
    view: str | None = None
    launcher_id: str | None = None

    @property
    def view_cabinet(self) -> bool | None:
        return None if self.view is None else self.view == CABINET_VIEW


@dataclass(frozen=True)
class Change:
    key: str
    before: str
    after: str


def matching(now: Now) -> Preset | None:
    """The preset every setting it covers already has, if any."""
    for preset in PRESETS:
        if now.cabinet == preset.cabinet and now.view_cabinet in (None, preset.cabinet):
            return preset
    return None


def view_name(value: str | None) -> str:
    stored = value if value in ("0", "1", "2") else DESKTOP_VIEW
    return apps.choice_word(PROGRAM, vpx.VIEW_MODE, stored)


def cab_name(on: bool) -> str:
    return t("word.on" if on else "word.off")


def changes(now: Now, preset: Preset) -> list[Change]:
    found = []
    if now.cabinet != preset.cabinet:
        found.append(Change(CAB_MODE, cab_name(now.cabinet), cab_name(preset.cabinet)))
    if now.view_cabinet is not None and now.view_cabinet != preset.cabinet:
        found.append(Change(VIEW_MODE, view_name(now.view),
                            view_name(CABINET_VIEW if preset.cabinet else DESKTOP_VIEW)))
    return found


def launcher_for_default() -> str | None:
    """The default Visual Pinball launcher's id where it has a program to hold settings."""
    launcher = launcher_model.default_launcher(PROGRAM)
    return launcher.launcher_id if launcher is not None and launcher.has_program() else None


def read(launcher_id: str | None) -> Now:
    """The settings now. Blocking. Raises where the launcher's settings cannot be read."""
    client = ApiClient()
    cabinet = displays.cabinet(client.config_values(), client.config_schema())
    if launcher_id is None:
        return Now(cabinet)
    held = client.launcher_config(launcher_id).get("values") or {}
    return Now(cabinet, str((held.get(vpx.VIEW_MODE) or {}).get("value") or DESKTOP_VIEW),
               launcher_id)


class ViewModeError(Exception):
    """Cabinet Mode was written and the launcher's View Mode was not."""


def write(cabinet: bool, view: str | None, launcher_id: str | None) -> None:
    """Cabinet Mode, and the View Mode where one is given and there is a launcher. Blocking.
    A failed Cabinet Mode write raises as it is; a failed View Mode write after it raises
    `ViewModeError`, so the caller knows Cabinet Mode has already changed."""
    client = ApiClient()
    client.put_config({"presentation": {"cab_mode": cabinet}})
    if view is not None and launcher_id is not None:
        try:
            client.write_launcher_config(launcher_id, {vpx.VIEW_MODE: view}, create=True)
        except Exception as exc:
            raise ViewModeError(launcher_id) from exc


def apply(now: Now, preset: Preset) -> None:
    """Sets what differs. A View Mode already Desktop in either form is left as it is."""
    view = None
    if now.view_cabinet is not None and now.view_cabinet != preset.cabinet:
        view = CABINET_VIEW if preset.cabinet else DESKTOP_VIEW
    write(preset.cabinet, view, now.launcher_id)


def restore(before: Now, preset: Preset) -> None:
    """Puts back what `apply` changed, from what was there before it."""
    view = before.view if before.view_cabinet not in (None, preset.cabinet) else None
    write(before.cabinet, view, before.launcher_id)


def _where(change: Change, this_launcher: bool) -> str:
    if change.key == CAB_MODE:
        return t("manager.presets.where_cabinet_mode")
    return t("manager.presets.where_this" if this_launcher else "manager.presets.where_default")


def _label(change: Change) -> str:
    return t("config.presentation.cab_mode.label" if change.key == CAB_MODE
             else "manager.presets.view_mode")


def _notes(found: list[Change]) -> str:
    said = []
    if any(one.key == CAB_MODE for one in found):
        said.append(t("manager.presets.reopen"))
    if any(one.key == VIEW_MODE for one in found):
        said.append(t("manager.presets.next_table"))
    return " ".join(said)


@on_page
async def choose(launcher_id: str | None, refill: Callable[[], Any]) -> None:
    """The dialog. `launcher_id` is the launcher the page is of, or None for the default one.
    `refill` redraws the page once the settings changed or were put back."""
    try:
        which = launcher_id or await run.io_bound(launcher_for_default)
        now = await offload.io(read, which)
    except Exception as exc:  # noqa: BLE001 - the reason belongs on the page
        ui.notify(t("said.settings_unreadable"), caption=why(exc), type="negative")
        return
    picked: list[Preset | None] = [matching(now)]
    with dialog.opened(t("manager.presets.title")) as box:
        group = ui.column().classes("manager-presets-choices")
        group.props("role=radiogroup")
        group.props["aria-label"] = t("manager.presets.title")
        ui.label(t("manager.presets.changes")).classes("manager-presets-heading")
        area = ui.element("div").classes("manager-presets-changes")
        with dialog.footer():
            dialog.cancel(lambda: box.submit(None))
            go = dialog.answer(t("manager.presets.apply"), lambda: box.submit(picked[0]),
                               icon=verbs.DONE)

    rows: dict[str, tuple[ui.element, ui.icon]] = {}

    def draw() -> None:
        pick = picked[0]
        found = changes(now, pick) if pick is not None else []
        for preset in PRESETS:
            row, radio = rows[preset.key]
            chosen = preset is pick
            row.classes(add="manager-source-row--chosen" if chosen else "",
                        remove="" if chosen else "manager-source-row--chosen")
            row.props["aria-checked"] = "true" if chosen else "false"
            radio.props["name"] = "radio_button_checked" if chosen else "radio_button_unchecked"
            radio.update()
        area.clear()
        with area:
            if pick is None:
                ui.label(t("manager.presets.none_match")).classes("manager-help")
            elif not found:
                ui.label(t("manager.presets.already", name=pick.name)).classes("manager-help")
            else:
                for one in found:
                    _row(one, launcher_id is not None)
                ui.label(_notes(found)).classes("manager-help manager-presets-note")
        go.set_enabled(bool(found))

    def choose_one(preset: Preset) -> None:
        picked[0] = preset
        draw()

    with group:
        for preset in PRESETS:
            rows[preset.key] = _choice(preset, preset is matching(now),
                                       partial(choose_one, preset))
    draw()
    pick = await box
    if pick is None:
        return
    await _apply(now, pick, refill)


def _choice(preset: Preset, current: bool, on_pick: Callable[[], Any]
            ) -> tuple[ui.element, ui.icon]:
    """One radio row, drawn once: picking changes it in place so the focus stays on it."""
    row = ui.element("div").classes(
        "manager-source-row manager-source-row--pick manager-wizard-pick manager-presets-pick")
    row.props("role=radio tabindex=0 aria-checked=false")
    row.on("click", on_pick)
    row.on("keydown.space.prevent", on_pick)
    row.on("keydown.enter.prevent", on_pick)
    with row:
        radio = ui.icon("radio_button_unchecked").classes("manager-presets-radio")
        with ui.column().classes("gap-0 min-w-0 grow manager-wizard-pick-text"):
            ui.label(preset.name).classes("manager-source-name")
            ui.label(preset.help).classes("manager-help")
        if current:
            panel_kit.state(t("word.current"), "on")()
    return row, radio


def _row(change: Change, this_launcher: bool) -> None:
    with ui.element("div").classes("manager-presets-change"):
        with ui.column().classes("gap-0 min-w-0"):
            ui.label(_label(change)).classes("manager-presets-key")
            ui.label(_where(change, this_launcher)).classes("manager-help")
        with ui.element("div").classes("manager-presets-values"):
            ui.label(change.before).classes("manager-help")
            ui.icon("arrow_forward").classes("manager-presets-arrow")
            ui.label(change.after).classes("manager-presets-to")


@on_page
async def _apply(now: Now, preset: Preset, refill: Callable[[], Any]) -> None:
    done = changes(now, preset)
    if not done:
        return
    try:
        await run.io_bound(apply, now, preset)
    except ViewModeError as exc:
        # Cabinet Mode has changed and the View Mode has not: put it back rather than leave
        # the two apart.
        await run.io_bound(write, now.cabinet, None, None)
        refill()
        ui.notify(t("said.save_failed"), caption=why(exc.__cause__ or exc), type="negative")
        return
    except Exception as exc:  # noqa: BLE001 - the reason belongs on the page
        ui.notify(t("said.save_failed"), caption=why(exc), type="negative")
        return

    async def back() -> None:
        await run.io_bound(restore, now, preset)
        refill()

    undo.offer(t("manager.presets.applied", name=preset.name,
                 changed=t("manager.presets.changed", count=len(done))), back)
    refill()
