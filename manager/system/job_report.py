"""The report a finished job kept, opened from the Jobs view or from the toast that told
it ended, and drawn on a page of its own."""

from __future__ import annotations

from typing import Any

from nicegui import ui

from common.failures import why
from common.i18n import t
from manager.kit import dialog as frame
from manager.kit import offload, panel, report_groups, wizard
from manager.kit.api import ApiClient
from manager.kit.on_page import on_page
from manager.library import import_report
from manager.system import jobs_list

# Where each kind's result labels live in its catalog; a kind not named reads its keys as written.
UNDER = {"library_importer.import": "setup"}

# Rows of a list drawn before it says how many more there are: a library of thousands that
# all failed must not draw a label each.
SHOWN = 200


def draw(body: Any, job: dict) -> None:
    """The report of `job` in `body`."""
    wizard.report(body, job, UNDER.get(str(job.get("kind") or ""), ""), limit=SHOWN)


def in_its_own_tab(job_id: str) -> None:
    """The report's own address, with the opener kept: a link in it finds the Manager's
    window by name only from a tab that has one."""
    panel.link_out(t("manager.report.own_tab"), to=report_groups.report_address(job_id),
                   hint=t("manager.report.own_tab.hint"), opener=True)()


@on_page
async def open_for(job_id: str) -> None:
    """The kept report for `job_id`, in a dialog of its own."""
    try:
        job = await offload.io(ApiClient().job, job_id)
    except Exception as exc:  # noqa: BLE001 - the reason belongs on screen
        ui.notify(t("manager.jobs.report_unavailable"), caption=why(exc), type="negative")
        return
    with frame.opened("", size="wide", tall=True, classes="manager-wizard-card") as dialog:
        with ui.element("div").classes("manager-wizard-scroll"):
            ui.label(jobs_list.named(job)) \
                .classes("manager-dialog-title manager-wizard-title")
            body = ui.column().classes("w-full gap-0 manager-wizard-body")
        with frame.footer():
            in_its_own_tab(job_id)
            frame.cancel(dialog.close, t("word.close"))
    await import_report.check(job)
    draw(body, job)
    dialog.open()
