"""The flags the browser opens a window with, on the Browser settings page.

Three settings decide them - the options to add, the built-in ones to leave out and the
switch that turns the built-in ones on - and none of them says what the browser is then
run with. This is that reading, kept true as each of the three is saved.
"""
from __future__ import annotations

import html
import json
import logging
import shlex
from collections.abc import Callable, Mapping
from typing import Any

from nicegui import ui

from common.devices import device_client
from common.i18n import t
from common.values import is_truthy
from manager.kit import clipboard, panel, remembered

logger = logging.getLogger("vpinfe.manager.settings.chrome_options")

SECTION = "chromium"
ADD = "options"
LEAVE_OUT = "options_exclude"
DEFAULTS = "defaults"
KEYS = (ADD, LEAVE_OUT, DEFAULTS)


RESULT = "result"
BREAKDOWN = "breakdown"
VIEW = "chrome_options.view"
MARKS = ("window", "yours", "replaced")


def _parsed(held: Mapping[str, Any], key: str) -> list[str]:
    try:
        return device_client.local().parse_browser_options(str(held.get(key) or ""))
    except ValueError:
        return []


def lines(held: Mapping[str, Any]) -> list[Any]:
    """What a window is started with, given the three settings as they stand, as the
    frontend's `OptionLine`s.

    A line that will not parse - an unbalanced quote - adds nothing, which is what the
    frontend does with it when it opens a window.
    """
    return device_client.local().browser_option_lines(
        _parsed(held, ADD),
        include_default_options=is_truthy(held.get(DEFAULTS), default=True),
        exclude_options=_parsed(held, LEAVE_OUT))


def unmatched_quote(held: Mapping[str, Any]) -> bool:
    try:
        device_client.local().parse_browser_options(str(held.get(ADD) or ""))
    except ValueError:
        return True
    return False


def replaced_window_options(shown: list[Any]) -> list[str]:
    """The options that place a window which an added option replaces. Not `--lang`,
    which names a language and places nothing."""
    return [option_name(line.text) for line in shown
            if line.places and line.replaced]


def option_name(option: str) -> str:
    return option.split("=")[0]


def _entries(raw: Any, names: list[str]) -> list[str]:
    """What is left out, as the picker lists it: a known option by its name, which is how
    VPinFE matches it, and anything else as written."""
    out: list[str] = []
    try:
        listed = shlex.split(str(raw or ""))
    except ValueError:
        listed = str(raw or "").split()
    for entry in listed:
        entry = entry.split("=")[0] if entry.split("=")[0] in names else entry
        if entry not in out:
            out.append(entry)
    return out


def leave_out(option: dict, value: Any, save: Callable[[Any], Any], *,
              section: Mapping[str, Any], writable: bool = True,
              refill: Callable[[], None] | None = None,
              suggestions: dict[str, Any] | None = None,
              on_control: Callable[[Any], Any] | None = None) -> Callable[[], None]:
    """VPinFE's own options to pick from, typed into to find one.

    A stored entry that is not one of them stays in the list, marked, so it can be seen
    and taken out - dropping it silently would change what a window starts with.
    """
    names = device_client.local().browser_default_option_names()
    held = _entries(value, names)
    options = {name: name for name in names}
    options.update({entry: t("manager.settings.chrome_unknown", option=entry)
                    for entry in held if entry not in options})
    on = is_truthy(section.get(DEFAULTS), default=True)

    def changed(event: Any) -> Any:
        return save("\n".join(event.value or []))

    return panel.multi_select(options, held, changed, disabled=not writable or not on,
                              typed=True, placeholder=t("manager.settings.chrome_skip.none"),
                              on_control=on_control)


class Preview:
    """The effective flags as one block, redrawn after each save of a setting behind them."""

    def __init__(self, values: Mapping[str, Any]) -> None:
        self.held: dict[str, Any] = dict(values.get(SECTION) or {})
        self.box: Any = None
        self.picker: Any = None
        self.listing: Any = None
        self.copy_box: Any = None
        self.warnings: Any = None
        self.view = BREAKDOWN if remembered.get(VIEW) == BREAKDOWN else RESULT
        self.writable = True

    def after(self, key: str, save: Callable[[Any], Any]) -> Callable[[Any], Any]:
        async def saved(value: Any) -> bool:
            done = bool(await save(value))
            if done:
                self.held[key] = value
                self.draw_inside()
                if key == DEFAULTS:
                    self.grey()
            return done

        return saved

    def leave_out(self, option: dict, value: Any, save: Callable[[Any], Any], *,
                  section: Mapping[str, Any], writable: bool) -> Callable[[], None]:
        """The picker, kept hold of so the switch above it can grey it out as it is set."""
        self.writable = writable

        def keep(control: Any) -> None:
            self.picker = control

        return leave_out(option, value, save, section=section, writable=writable,
                         on_control=keep)

    def grey(self) -> None:
        if self.picker is None or not self.writable:
            return
        if is_truthy(self.held.get(DEFAULTS), default=True):
            self.picker.enable()
        else:
            self.picker.disable()

    def row(self) -> tuple[str, Callable[[], None]]:
        def draw() -> None:
            self.box = ui.column().classes("gap-1 w-full min-w-0 manager-measured")
            with self.box:
                self.listing = ui.column().classes("manager-options w-full min-w-0 gap-0")
                with ui.row().classes("items-center gap-2 w-full no-wrap"):
                    panel.segmented({RESULT: t("manager.settings.chrome_view.result"),
                                     BREAKDOWN: t("manager.settings.chrome_view.breakdown")},
                                    self.view, self.choose).classes("manager-units shrink-0")
                    self.copy_box = ui.element("div").classes("shrink-0")
                    ui.label(t("manager.settings.chrome_effective.help")) \
                        .classes("manager-help grow min-w-0")
            self.draw_inside()

        return t("manager.settings.chrome_effective"), draw

    def warning_row(self) -> tuple[Any, Callable[[], None]]:
        """Under Additional Options: what is wrong with what was typed there."""
        def draw() -> None:
            self.warnings = ui.column().classes("gap-1 w-full min-w-0")
            self.say_warnings(lines(self.held))

        return (panel.ASIDE, draw)

    def choose(self, event: Any) -> None:
        self.view = BREAKDOWN if event.value == BREAKDOWN else RESULT
        remembered.put(VIEW, self.view)
        self.draw_inside()

    def draw_inside(self) -> None:
        if self.box is None:
            return
        shown = lines(self.held)
        text = "\n".join(line.text for line in shown)
        self.listing.clear()
        with self.listing:
            for line in shown:
                self.draw_line(line)
            if self.view == BREAKDOWN:
                with ui.row().classes("gap-4 pt-2"):
                    for mark in MARKS:
                        ui.label(t(f"manager.settings.chrome_mark.{mark}")) \
                            .classes(f"manager-option--{mark}")
        self.copy_box.clear()
        with self.copy_box:
            panel.action(t("word.copy"),
                         lambda event: clipboard.copied(bool(event.args), text),
                         icon="content_copy",
                         hint=t("manager.settings.chrome_copy.help"),
                         js=clipboard.COPY_JS % json.dumps(text))()
        self.say_warnings(shown)

    def draw_line(self, line: Any) -> None:
        """One option, broken at a comma when it does not fit - never inside a word."""
        marked = self.view == BREAKDOWN
        classes = "manager-option"
        tip = ""
        text = html.escape(line.text).replace(",", ",<wbr>")
        if marked:
            if line.replaced:
                classes, tip = classes + " manager-option--replaced", "replaced"
            elif line.source == "window":
                classes, tip = classes + " manager-option--window", "window"
            elif line.source == "yours":
                classes += " manager-option--yours"
            if line.merged:
                head = line.text[:len(line.text) - len(",".join(line.merged))]
                mine = html.escape(",".join(line.merged)).replace(",", ",<wbr>")
                text = (html.escape(head).replace(",", ",<wbr>")
                        + f'<span class="manager-option--yours">{mine}</span>')
        shown = ui.html(text, sanitize=False, tag="div").classes(classes)
        if tip:
            shown.tooltip(t(f"manager.settings.chrome_mark.{tip}"))

    def say_warnings(self, shown: list[Any]) -> None:
        if self.warnings is None:
            return
        said = []
        if unmatched_quote(self.held):
            said.append(t("manager.settings.chrome_unmatched"))
        if replaced := replaced_window_options(shown):
            said.append(t("manager.settings.chrome_replaces_window",
                          options=", ".join(replaced)))
        self.warnings.clear()
        with self.warnings:
            for text in said:
                with ui.element("div").classes("manager-attention w-full"):
                    ui.icon("error_outline").classes("manager-attention-icon")
                    ui.label(text).classes("manager-attention-line")
