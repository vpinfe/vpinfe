"""Window placement: the rules file on Sway and Hyprland, and what GNOME and KDE cannot do.

Drawn as rows another page mounts, over `common.host.placement`, which writes nothing the
person did not agree to: the rules file is VPinFE's own, and the one line in the person's
config goes in only on Add it.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from nicegui import run, ui

from common import apps
from common.capture.adapters import NOT_FOUND, SCREEN_SECTIONS, screen_of
from common.capture.adapters.wlr import WlrAdapter, reload_config
from common.config.paths import get_ini_config
from common.failures import why
from common.host import display_service, placement
from common.i18n import t
from frontend import theme_windows
from manager.kit import clipboard, offload, panel
from manager.kit import dialog as frame
from manager.kit.on_page import on_page
from manager.settings.displays import Write, stored

logger = logging.getLogger("vpinfe.manager.settings.placement_panel")

XWAYLAND_FLAG = "--ozone-platform=x11"


@dataclass(frozen=True)
class Reading:
    """What this session offers, the rules as the screens stand, and why the screens could
    not be read when they could not."""

    offer: placement.Offer
    text: str = ""
    problem: str = ""


def _rows(outputs: Sequence[Any]) -> tuple[list[placement.Row], bool]:
    """The windows with the output each goes to, and whether a screen the person set is
    one the desktop does not report - rules written then would place nothing."""
    config = get_ini_config()
    monitors = display_service.get_display_monitors()
    found = []
    missed = False
    for name in theme_windows.active(config):
        role = theme_windows.canonical(name)
        if role not in SCREEN_SECTIONS or not theme_windows.window_title(name):
            continue
        screen = screen_of(role, outputs, config, monitors)
        found.append(placement.Row(name, role, screen.output.name if screen.output else ""))
        missed = missed or screen.reason == NOT_FOUND
    return found, missed


def _titles() -> list[placement.AppWindow]:
    """The windows of every app that shows its own, by the title it gives them."""
    return [placement.AppWindow(title, role)
            for app in apps.all_apps() if app.capture is not None
            for title, role in app.capture.titles().items()]


def read(env: Mapping[str, str]) -> Reading | None:
    """None where there is nothing to say: X11, another OS, and VPinOS."""
    from common.host import vpinos

    found = None if vpinos.detected() else placement.offer(env)
    if found is None:
        return None
    if found.kind not in (placement.SWAY, placement.HYPRLAND):
        return Reading(found)
    outputs: list[Any] = []
    problem = ""
    try:
        outputs = WlrAdapter(env).outputs()
    except (OSError, ValueError) as exc:
        problem = why(exc)
    rows, missed = _rows(outputs)
    if missed and not problem:
        problem = t("manager.placement.screen_missing")
    return Reading(found, placement.rules(found.kind, rows, _titles()), problem)


def _held(found: placement.Offer) -> str:
    try:
        return found.rules.read_text(encoding="utf-8") if found.rules else ""
    except (OSError, UnicodeDecodeError):
        return ""


def resync(env: Mapping[str, str] = os.environ) -> bool:
    """Writes the rules again for the screens as they stand, and has the compositor read
    them, when the person has already added the line and the rules would change. False
    when there was nothing to do. Raises OSError, and writes nothing when the screens
    could not be read, since rules that place nothing would replace working ones."""
    reading = read(env)
    if reading is None or not reading.offer.added:
        return False
    if reading.problem:
        raise OSError(t("manager.placement.read_failed"))
    if _held(reading.offer) == reading.text:
        return False
    placement.write_rules(reading.offer.kind, reading.text)
    reload_config(env)
    return True


def _name(kind: str) -> str:
    return t(f"manager.placement.name.{kind}")


def _chromium_options(values: Mapping[str, Any], schema: Sequence[dict]) -> str:
    return str(stored(values, schema, "chromium", "options") or "")


def rows(reading: Reading | None, values: Mapping[str, Any], schema: Sequence[dict],
         write: Write, refill: Callable[[], None],
         env: Mapping[str, str] = os.environ) -> list[tuple[Any, Any]]:
    if reading is None:
        return []
    found = reading.offer
    entries: list[tuple[Any, Any]] = [(panel.HEADING, t("manager.placement.heading"))]
    if found.kind in (placement.SWAY, placement.HYPRLAND):
        entries += _rules_rows(reading, refill, env)
    else:
        entries += _xwayland_rows(found, values, schema, write, refill)
    return entries


def _rules_rows(reading: Reading, refill: Callable[[], None],
                env: Mapping[str, str]) -> list[tuple[Any, Any]]:
    found = reading.offer
    compositor = _name(found.kind)
    assert found.rules is not None

    def unreadable() -> bool:
        if reading.problem:
            ui.notify(t("manager.placement.read_failed"), caption=reading.problem,
                      type="warning")
        return bool(reading.problem)

    @on_page
    async def add_it() -> None:
        if unreadable():
            return
        try:
            await run.io_bound(placement.add, found, reading.text)
        except OSError as exc:
            ui.notify(t("manager.placement.add_failed"), caption=why(exc), type="negative")
            return
        try:
            await run.io_bound(reload_config, env)
        except OSError as exc:
            ui.notify(t("manager.placement.reload_failed", compositor=compositor),
                      caption=why(exc), type="warning")
        else:
            ui.notify(t("manager.placement.added_to", config=str(found.config)),
                      type="positive")
        refill()

    @on_page
    async def show_line() -> None:
        if unreadable():
            return
        try:
            await offload.io(placement.write_rules, found.kind, reading.text)
        except OSError as exc:
            ui.notify(t("manager.placement.write_failed"), caption=why(exc), type="negative")
            return
        _show_line(found, compositor)

    def actions() -> None:
        with ui.row().classes("manager-fact-edit gap-2"):
            if found.config is not None:
                panel.action(t("manager.placement.add"), add_it, icon="add",
                             hint=t("manager.placement.add.help", config=str(found.config),
                                    compositor=compositor))()
            panel.action(t("manager.placement.add_myself"), show_line, icon="content_copy",
                         hint=t("manager.placement.add_myself.help"))()

    entries: list[tuple[Any, Any]] = [
        panel.intro(t("manager.placement.intro.help")),
        (t("manager.placement.rules_file"), str(found.rules))]
    if reading.problem:
        entries.append((t("manager.placement.screens"), panel.state(
            t("manager.placement.read_failed"), "warn", hint=reading.problem)))
    if found.added:
        entries.append((t("manager.placement.config_file"), panel.state(
            t("manager.placement.added"), "on", beside=str(found.config))))
        return entries
    entries.append((t("manager.placement.config_file"),
                    str(found.config) if found.config is not None
                    else t("manager.placement.no_config")))
    if found.config is None:
        entries.append(panel.note(t("manager.placement.no_config.help", compositor=compositor)))
    entries.append(("", actions))
    return entries


def _show_line(found: placement.Offer, compositor: str) -> None:
    with frame.opened(t("manager.placement.line_title"), size="wide") as box:
        ui.label(t("manager.placement.line.help", compositor=compositor,
                   config=str(found.config) if found.config else
                   t("manager.placement.your_config", compositor=compositor))) \
            .classes("manager-help")
        with ui.element("div").classes("w-full"):
            ui.textarea(value=found.line).props("outlined readonly rows=2") \
                .classes("w-full manager-log")
        with frame.footer():
            frame.cancel(box.close, t("word.close"))
            panel.action(t("word.copy"),
                         lambda event: clipboard.copied(bool(event.args), found.line),
                         icon="content_copy", hint=t("manager.placement.copy.help"),
                         js=clipboard.COPY_JS % json.dumps(found.line))()
    box.open()


def _xwayland_rows(found: placement.Offer, values: Mapping[str, Any], schema: Sequence[dict],
                   write: Write, refill: Callable[[], None]) -> list[tuple[Any, Any]]:
    desktop = _name(found.kind)
    held = _chromium_options(values, schema)
    on = XWAYLAND_FLAG in held.split()

    async def use() -> None:
        joined = "\n".join((held.rstrip(), XWAYLAND_FLAG)) if held.strip() else XWAYLAND_FLAG
        if await write({"chromium": {"options": joined}}):
            refill()

    entries: list[tuple[Any, Any]] = [
        panel.intro(t("manager.placement.cannot.help", desktop=desktop))]
    if on:
        entries.append((t("manager.placement.xwayland"),
                        panel.state(t("manager.placement.xwayland_on"), "on",
                                    beside=XWAYLAND_FLAG)))
    else:
        entries.append((t("manager.placement.xwayland"), panel.action(
            t("manager.placement.use_xwayland"), use, icon="add",
            hint=t("manager.placement.use_xwayland.help", flag=XWAYLAND_FLAG))))
    return entries
