"""What this install has run, and how each went.

Answers *what did this install just do, and how did it go*, from the job registry rather
than a log file. It is a grid, so it takes the grid's sorting and filters; the bar above
it narrows by outcome and by a find. A job that kept a report ends its Detail with a link
to it, which a double-click on its row opens too.

The list is read again every EVERY_SECONDS.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from nicegui import ui

from common.failures import why
from common.i18n import t
from manager.kit import busy, dropdown, grid, offload, panel, verbs
from manager.kit.data import Library
from manager.kit.on_page import on_page
from manager.system import job_report, jobs_list

EVERY_SECONDS = 2.0


def build(library: Library, state: dict[str, Any], redraw: Callable[[], None]) -> None:
    """The control bar, and the grid under it."""
    held: dict[str, Any] = {
        "outcome": state.get("jobs_outcome", ""),
        "find": state.get("jobs_find", ""),
        # What the box holds now: the grid, or the words standing in for it.
        "table": None,
        "rows": [],
        "error": "",
    }

    with ui.column().classes("w-full grow min-h-0 gap-2"):
        bar = ui.row().classes("items-center gap-2 w-full no-wrap px-3 pt-2")
        # The grid's own container: a flex column, so the grid takes the height left.
        box = ui.element("div").classes("w-full grow min-h-0 flex flex-col")

    @on_page
    async def load() -> None:
        found: list[dict[str, Any]] = []
        try:
            found = await offload.io(library.jobs, jobs_list.LIMIT)
        except Exception as exc:  # noqa: BLE001 - this page says why, never 500s
            held["error"] = why(exc)
        else:
            held["error"] = ""
        _draw(box, held, jobs_list.rows(found, time.time(), held["outcome"]), bool(found))

    def narrow(value: str) -> Any:
        held["outcome"] = state["jobs_outcome"] = value
        return load()

    def find(text: str) -> None:
        held["find"] = state["jobs_find"] = text
        table = held["table"]
        if table is not None and not isinstance(table, tuple):
            table.run_grid_method("setGridOption", "quickFilterText", text)

    with bar:
        dropdown.Select({"": t("manager.jobs.all_outcomes"),
                         **{one: t(f"manager.jobs.outcome.{one}")
                            for one in jobs_list.OUTCOMES}},
                        value=held["outcome"], look=dropdown.OUTLINED).classes("w-36") \
            .on_value_change(lambda e: narrow(e.value or ""))
        ui.input(placeholder=t("manager.jobs.find"), value=held["find"]) \
            .props("dense outlined clearable").classes("grow min-w-0") \
            .on_value_change(lambda e: find(e.value or ""))

    busy.fill(box, load)
    ui.timer(EVERY_SECONDS, load)


def report_asked(event: Any) -> Any:
    """The report of the job a row, or the link on it, asked for."""
    return job_report.open_for(str(event.args or ""))


def _draw(box: Any, held: dict[str, Any], shown: list[dict[str, Any]],
          any_ran: bool) -> None:
    """The grid, or the words that stand in for it. What is up is left alone when nothing
    about it changed: drawing it again would blink the page every tick."""
    if held["error"] or not shown:
        said = ("error", held["error"]) if held["error"] else (
            "none", t("manager.jobs.nothing_matches") if any_ran
            else t("manager.jobs.nothing_ran_yet"))
        if held["table"] == said:
            return
        held["table"] = said
        held["rows"] = []
        box.clear()
        with box:
            panel.facts(ui, [panel.intro(t("manager.jobs.read_failed"),
                                         hint=held["error"])] if held["error"]
                        else [panel.intro(said[1])])
        return
    table = held["table"]
    if table is None or isinstance(table, tuple):
        box.clear()
        held["rows"] = shown
        with box:
            menu: ui.context_menu

            def fill_menu(row: dict | None) -> None:
                found = next((one for one in held["rows"]
                              if row and one["id"] == row.get("id")), None)
                panel.verb_menu(menu, found["what"] if found else "", [
                    panel.Verb(t("manager.jobs.view_report"), icon=verbs.REVIEW,
                               run=lambda: job_report.open_for(found["id"]))
                ] if found and found["report"] else [])

            async def on_header_context(col_id: str | None) -> None:
                await grid.header_menu(menu, table, jobs_list.COLUMNS, col_id)

            table = grid.build(jobs_list.COLUMNS, shown, jobs_list.SCOPE,
                               on_context=fill_menu, on_header_context=on_header_context)
            menu = ui.context_menu()

        held["table"] = table
        table.run_grid_method("setGridOption", "quickFilterText", held["find"])
        return
    if held["rows"] != shown:
        held["rows"] = shown
        table.run_grid_method("setGridOption", "rowData", shown)
