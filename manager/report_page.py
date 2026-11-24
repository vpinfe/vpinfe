"""A kept report on a page of its own, to keep open beside the Manager while the games in it
are worked through. No rail: the Manager is the other tab."""

from __future__ import annotations

from typing import Any

from nicegui import ui

from common.failures import why
from common.i18n import t
from manager.kit import appearance, offload, report_groups
from manager.kit import panel as panel_parts
from manager.kit.api import ApiClient
from manager.kit.grid import install_filters
from manager.library import import_report
from manager.system import job_report, jobs_list

# Raised by the tab coming back into view, which is when the games worked on in the other
# tab have changed what the report would say.
SHOWN_AGAIN = "report_shown_again"
LISTEN_FOR_RETURN = f"""
document.addEventListener('visibilitychange', () => {{
  if (!document.hidden) emitEvent('{SHOWN_AGAIN}');
}});
"""


@ui.page(report_groups.ADDRESS, title=t("manager.report.page_title"), reconnect_timeout=300)
async def report_page(job: str = "") -> None:
    """The report of the job `job` names."""
    appearance.apply_mode(appearance.configured_mode())
    install_filters()
    panel_parts.install_fact_tooltips()
    ui.query(".nicegui-content").classes("p-0 gap-0")
    drawn: dict[str, Any] = {}

    async def redraw() -> None:
        """Check the report against the library again and draw it where it was."""
        if "job" in drawn:
            await import_report.check(drawn["job"])
            job_report.draw(drawn["body"], drawn["job"])

    ui.on(SHOWN_AGAIN, redraw)
    await ui.context.client.connected()

    with ui.column().classes("w-full max-w-4xl mx-auto p-4 gap-2"):
        try:
            found = await offload.io(ApiClient().job, job)
        except Exception as exc:  # noqa: BLE001 - the reason belongs on screen
            ui.label(t("manager.jobs.report_unavailable")).classes("manager-dialog-title")
            ui.notify(t("manager.jobs.report_unavailable"), caption=why(exc), type="negative")
            return
        said = jobs_list.what_ran(str(found.get("kind") or ""))
        ui.page_title(t("manager.report.title", what=said))
        ui.label(said).classes("manager-dialog-title manager-wizard-title")
        body = ui.column().classes("w-full gap-0 manager-wizard-body")
        await import_report.check(found)
        job_report.draw(body, found)
        drawn.update(job=found, body=body)
    ui.run_javascript(panel_parts.NAV_CLICK)
    ui.run_javascript(LISTEN_FOR_RETURN)
