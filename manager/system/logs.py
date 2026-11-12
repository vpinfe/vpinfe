
"""What this install has written down.

A scrolling viewport with a control bar above it, not a grid. A log is one tall column
you read down, and paging it into rows of columns is the shape that made the old Logs
page hard to scan.

The records arrive already assembled: `read_log` folds a traceback's continuation lines
into the record that caused them, so a level filter cannot drop the half carrying the
reason.

**Two views over the same records.** Stream answers *what happened just now*; Digest
answers *what is wrong with this machine* by collapsing repeats - a machine can log one
mistake four thousand times, and a list of four thousand rows hides that it is one.

**One select names what is read.** VPinFE, its previous run, and Visual Pinball.

Streaming is a timer on this page rather than a transport of its own. The Manager is
server-rendered and already holds a live socket to the browser, so the tail pushes over
the one that is there.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from nicegui import ui

from common.failures import why
from common.i18n import t
from manager.devices import devices as devices_page
from manager.kit import busy, clipboard, deeplink, dropdown, offload, panel, verbs
from manager.kit.data import Library
from manager.kit.on_page import on_page
from manager.system import support as support_dialog

logger = logging.getLogger("vpinfe.manager.system.logs")

# The cadence everything else on this shell ticks at.
EVERY_SECONDS = 2.0

# Enough to see what led to something without turning the page into the whole file. The
# API caps it at the same number.
RECORDS = 500

LEVELS = ("", "DEBUG", "INFO", "WARNING", "ERROR")

# The views, by the value the address carries. The stream is the empty one.
STREAM, DIGEST = "", "digest"
VIEWS = (STREAM, DIGEST)


def build(library: Library, state: dict[str, Any], redraw: Callable[[], None]) -> None:
    """The control bar, and the viewport under it."""
    held: dict[str, Any] = {
        "level": state.get("log_level", ""),
        "contains": state.get("log_contains", ""),
        "source": state.get("log_source", ""),
        "wrap": state.get("log_wrap", True),
        "view": state.get("log_view") if state.get("log_view") in VIEWS else STREAM,
        "bar_for": None,
        "error": "",
        "follow": state.get("log_follow", True),
        "records": [],
        "sources": [],
        "visual_pinball": "",
        "path": "",
    }

    with ui.column().classes("w-full grow min-h-0 gap-2"):
        bar = ui.row().classes("items-center gap-2 w-full no-wrap px-3 pt-2")
        # `min-h-0`, or the viewport grows to its content and the page scrolls instead of
        # the log.
        # `manager-log` is the treatment the device panel already reads a log with -
        # monospaced, so timestamps and levels line up down the column, and the message
        # under its own header rather than beside it, which is the only shape a
        # traceback fits in.
        viewport = ui.column().classes(
            "w-full grow min-h-0 overflow-auto gap-0 manager-log")

    @on_page
    async def load() -> None:
        try:
            found = await offload.io(library.logs, RECORDS, held["level"],
                                       held["contains"], held["source"])
        except Exception as exc:  # noqa: BLE001 - this page says why, never 500s
            held["records"], held["error"] = [], why(exc)
        else:
            held["error"] = ""
            held["records"] = list(found.get("records") or [])
            _note_sources(held, found)
            if not _known(held):
                # A rotation that has gone, or a launcher that no longer names an ini.
                held["source"] = state["log_source"] = ""
                held["bar_for"] = None
                return await load()
        if held["bar_for"] != _bar_key(held):
            _draw_bar(bar, library, held, load, state)
        await _draw(viewport, held)

    busy.fill(viewport, load)
    # Only while following, and only on a file that is still being written: a rotated log
    # does not change, so re-reading it is a request that can only return what it just
    # returned.
    ui.timer(EVERY_SECONDS, lambda: load() if held["follow"] and _live(held) else None)


def _note_sources(held: dict[str, Any], found: dict[str, Any]) -> None:
    held["sources"] = list(found.get("sources") or [])
    held["visual_pinball"] = str(found.get("visual_pinball") or "")
    held["path"] = str(found.get("path") or "")


def _live(held: dict[str, Any]) -> bool:
    """Whether the file chosen is one that is still being written: the current VPinFE log,
    or Visual Pinball's. A rotation is finished."""
    return not held["source"] or held["source"] == held["visual_pinball"]


def _bar_key(held: dict[str, Any]) -> tuple[Any, ...]:
    """What the bar is drawn from. A tick that finds the same key leaves the bar alone:
    clearing it closes a menu somebody has open."""
    files = (tuple(held["sources"]), held["visual_pinball"])
    return (held["source"], held["path"], held["follow"], held["wrap"], *files)


def _known(held: dict[str, Any]) -> bool:
    """Whether the source held is one the last read listed."""
    return held["source"] in ("", *held["sources"], held["visual_pinball"])


def _source_options(held: dict[str, Any]) -> dict[str, str]:
    """What can be read, named for what it is. The first file is the log being written;
    the ones after it are earlier runs, newest first."""
    options = {"": t("manager.logs.vpinfe")}
    for number, name in enumerate(held["sources"][1:], 1):
        options[name] = (t("manager.logs.vpinfe_previous") if number == 1
                         else t("manager.logs.vpinfe_previous_n", number=number))
    if held["visual_pinball"]:
        options[held["visual_pinball"]] = t("manager.logs.visual_pinball")
    # A read that failed names no files, and a select refuses a value it does not list.
    options.setdefault(held["source"], held["source"])
    return options


def _draw_bar(bar: Any, library: Library, held: dict[str, Any],
              reload: Callable[[], Any], state: dict[str, Any]) -> None:
    bar.clear()
    with bar:
        find: list[ui.input] = []

        def remember(key: str, value: Any) -> Any:
            """Hold the choice, persist it, and redraw. Returns the redraw, which NiceGUI
            runs only when a handler hands it back."""
            held[key] = value
            state[f"log_{key}"] = value
            # The bar is not drawn again for a choice, so text typed and not entered is
            # put back to the search that is in force.
            if key != "contains" and find:
                find[0].value = held["contains"]
            if key == "view":
                deeplink.sync(state, push=True)
            return reload()

        source = dropdown.Select(_source_options(held), look=dropdown.OUTLINED,
                                 value=held["source"]) \
            .classes("w-44") \
            .on_value_change(lambda e: remember("source", e.value or ""))
        if held["path"]:
            source.tooltip(held["path"])
        held["bar_for"] = _bar_key(held)

        dropdown.Select({STREAM: t("manager.logs.stream"), DIGEST: t("manager.logs.digest")},
                        value=held["view"], look=dropdown.OUTLINED) \
            .classes("w-32") \
            .on_value_change(lambda e: remember("view", e.value or STREAM))

        dropdown.Select({one: (t(f"config.logger.level.choice.{one.lower()}") if one
                               else t("manager.logs.all_levels")) for one in LEVELS},
                        value=held["level"], look=dropdown.OUTLINED).classes("w-36") \
            .on_value_change(lambda e: remember("level", e.value or ""))

        find.append(ui.input(placeholder=t("manager.logs.find"), value=held["contains"])
                    .props("dense outlined clearable").classes("grow min-w-0")
                    .on("keydown.enter", lambda e: remember("contains", e.sender.value or "")))

        if _live(held):
            follow = ui.button(icon="play_arrow" if held["follow"] else "pause",
                               on_click=lambda: remember("follow", not held["follow"])) \
                .props("flat dense round size=sm")
            follow.tooltip(t("manager.logs.follow_on") if held["follow"]
                           else t("manager.logs.follow_off"))
            if held["follow"]:
                follow.props(add="color=primary")
        else:
            # A rotated file does not change, so following it is a control that would
            # do nothing. Refresh is what makes sense there.
            ui.button(icon=verbs.REFRESH, on_click=lambda: reload()) \
                .props("flat dense round size=sm").tooltip(t("word.reread"))

        _file_menu(library, held, remember)


def _file_menu(library: Library, held: dict[str, Any],
               remember: Callable[[str, Any], Any]) -> None:
    """What is done to the file or to how it is drawn, kept out of the bar because none
    of it is wanted on every visit."""
    with ui.button(icon=verbs.MORE).props("flat dense round size=sm") as more, ui.menu():
        wrap = ui.menu_item(on_click=lambda: remember("wrap", not held["wrap"])) \
            .classes("manager-menu-item")
        with wrap, ui.row().classes("items-center gap-2 no-wrap w-full"):
            ui.label(t("manager.logs.wrap")).classes("grow min-w-0")
            if held["wrap"]:
                ui.icon("check").classes("manager-menu-check")
        if held["path"]:
            ui.separator()
            # In the browser at the click, which is the only moment it will copy.
            ui.menu_item(t("word.copy_path")) \
                .classes("manager-menu-item") \
                .on("click", lambda event: clipboard.copied(bool(event.args), held["path"]),
                    js_handler=clipboard.COPY_JS % json.dumps(held["path"]))
            ui.menu_item(t("manager.logs.download"),
                         lambda: _download(library, held)).classes("manager-menu-item")
        ui.separator()
        ui.menu_item(t("manager.about.support_package"),
                     support_dialog.open_support_package).classes("manager-menu-item")
    more.tooltip(t("manager.logs.file_menu"))


@on_page
async def _download(library: Library, held: dict[str, Any]) -> None:
    try:
        content = await offload.io(library.log_file, held["source"])
    except Exception as exc:  # noqa: BLE001 - say why, never a bare 500
        ui.notify(t("said.action_failed"), caption=why(exc), type="negative")
        return
    ui.download.content(content, Path(held["path"]).name, "text/plain")


@on_page
async def _draw(viewport: Any, held: dict[str, Any]) -> None:
    # Only when already at the bottom. A tail that yanks you away from the line you are
    # reading is worse than no tail at all.
    at_bottom = True
    try:
        at_bottom = bool(await ui.run_javascript(
            f'(() => {{ const e = getElement({viewport.id}); '
            f'return !e || e.scrollHeight - e.scrollTop - e.clientHeight < 40; }})()',
            timeout=1.0))
    except Exception:  # noqa: BLE001 - a scroll probe must never take the page down
        at_bottom = True

    viewport.clear()
    with viewport:
        if held.get("error"):
            panel.facts(ui,
                    [panel.intro(t("manager.logs.read_failed"), hint=held["error"])])
            return
        if not held["records"]:
            panel.facts(ui, [panel.intro(
                t("manager.logs.nothing_matches") if held["level"] or held["contains"]
                else t("manager.logs.empty"))])
            return
        if held["view"] == DIGEST:
            _digest(held["records"])
        else:
            _stream(held["records"], held["wrap"])

    if at_bottom and held["view"] != DIGEST:
        ui.run_javascript(
            f'(() => {{ const e = getElement({viewport.id}); '
            f'if (e) e.scrollTop = e.scrollHeight; }})()')


def _stream(records: list[dict[str, Any]], wrap: bool) -> None:
    """Through the same renderer the device panel uses, so one install's log and
    another's do not read as two different things."""
    for record in records:
        with ui.row().classes("items-baseline gap-2 w-full no-wrap manager-log-row"):
            ui.label(str(record.get("when") or "")).classes("manager-log-when")
            level = str(record.get("level") or "")
            ui.label(level).classes("manager-log-level " + devices_page.level_class(level))
            ui.label(str(record.get("logger") or "")).classes("manager-log-source")
        # The one thing the shared treatment does not decide: wrapping is a choice on
        # this page, and a stack trace is unreadable either way without it. `shrink-0`
        # because an unwrapped label scrolls sideways, which lets a flex column squash
        # it to no height at all.
        ui.label(str(record.get("message") or "")).classes("manager-log-message shrink-0") \
            .style("" if wrap else "white-space:pre; overflow-x:auto")


def _digest(records: list[dict[str, Any]]) -> None:
    """The same records, collapsed by what they are rather than listed by when.

    Grouped on the logger plus the message's first line: a traceback varies below that
    line and repeats above it, so grouping on the whole message would count one mistake
    as several.
    """
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for record in records:
        message = str(record.get("message") or "")
        key = (str(record.get("logger") or ""), message.split("\n", 1)[0])
        found = grouped.setdefault(key, {"count": 0, "first": record.get("when", ""),
                                         "last": "", "level": record.get("level", ""),
                                         "message": message})
        found["count"] += 1
        found["last"] = record.get("when", "")
        # The worst level any of them arrived at: one warning among a hundred infos is
        # what somebody is looking for.
        if _rank(record.get("level")) > _rank(found["level"]):
            found["level"] = record.get("level")

    ordered = sorted(grouped.items(),
                     key=lambda item: (-_rank(item[1]["level"]), -item[1]["count"]))
    ui.label(t("manager.logs.distinct_records", count=len(ordered), records=len(records))) \
        .classes("manager-help px-3 pb-1")
    for (source, first_line), found in ordered:
        tone = devices_page.LOG_LEVELS.get(str(found["level"] or ""), "manager-log-text")
        with ui.row().classes("items-baseline gap-3 w-full no-wrap manager-log-row"):
            ui.label(f"{found['count']}x").classes(f"shrink-0 {tone}") \
                .style("min-width:4ch; text-align:right")
            with ui.column().classes("gap-0 grow min-w-0"):
                ui.label(first_line).classes(f"truncate {tone}")
                ui.label(t("manager.logs.first_last", source=(source), first=found["first"],
                        last=found["last"])) \
                    .classes("manager-log-detail")


def _rank(level: Any) -> int:
    return {"DEBUG": 0, "INFO": 1, "WARNING": 2,
            "ERROR": 3, "CRITICAL": 4}.get(str(level or "").upper(), 1)
