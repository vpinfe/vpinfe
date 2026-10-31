"""What has run since this install started, one row per job.

Drawn on the Jobs page. The registry keeps it in memory, so a restart starts the list
again, apart from the jobs that kept their result: those come back with a report to open.
"""

from __future__ import annotations

import json
from typing import Any

from common.i18n import t
from common.runtime import jobs
from manager.kit import grid, states, when

SCOPE = "manager.jobs"

# What the link and a double-click on a row ask the page for: the report of the row's job,
# on a row that has one. Answered by the listener in `manager/page.py`.
REPORT_ASKED = "hub_job_report"
_ASK = "emitEvent('" + REPORT_ASKED + "', params.data.id)"
OPENS_REPORT: dict[str, Any] = {
    ":onCellClicked":
        "params => { const link = params.event && params.event.target.closest("
        f"'[data-job-report]'); if (link) {{ params.event.preventDefault(); "
        f"if (params.event.detail < 2) {_ASK}; }} }}",
    ":onCellDoubleClicked":
        "params => { const link = params.event && params.event.target.closest("
        f"'[data-job-report]'); if (!link && params.data && params.data.report) {_ASK}; }}",
}

# The link follows the line and keeps its place on the row when the line is too long.
_DETAIL_RENDERER = (
    "params => {"
    " const esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;')"
    ".replace(/\"/g, '&quot;');"
    " const said = esc(params.value == null ? '' : params.value);"
    " if (!(params.data || {}).report) return said;"
    " return '<span class=\"manager-cell-detail\"><span title=\"' + said + '\">' + said"
    " + '</span><a class=\"manager-link\" href=\"#\" data-job-report=\"1\">' + "
    f"{json.dumps(t('manager.jobs.view_report'))} + '</a></span>'; }}"
)

LIMIT = jobs.HISTORY_LIMIT

STOPPED = "stopped"
# Finished, with parts that failed. A `done` job to the registry, which has
# three states; to the person reading the list it is a run to open.
WITH_ERRORS = "with_errors"
OUTCOMES = (jobs.RUNNING, jobs.DONE, WITH_ERRORS, jobs.FAILED, STOPPED)


def _outcome(one: str, tone: str, glyph: str = "") -> states.State:
    return states.State(one, t(f"manager.jobs.outcome.{one}"),
                        t(f"manager.jobs.outcome.{one}.help"), tone=tone, glyph=glyph)


# In the order a job lives: running, then how it ended.
OUTCOME_STATES = states.Table(
    covers=t("manager.jobs.outcome.help"),
    states=(_outcome(jobs.RUNNING, states.ACCENT),
            _outcome(jobs.DONE, states.PLAIN, states.TICK),
            _outcome(WITH_ERRORS, states.WARN),
            _outcome(jobs.FAILED, states.BAD),
            _outcome(STOPPED, states.QUIET)))


def _said(field: str) -> dict[str, Any]:
    """A column whose cell holds a number to sort by, and shows the row's `<field>_said`."""
    return {":valueFormatter": f"params => (params.data || {{}}).{field}_said || ''"}


COLUMNS = [
    grid.identifier("what", t("manager.jobs.what"), 230, pinned="left",
                    help=t("manager.jobs.what.help"), **OPENS_REPORT),
    grid.column("started", t("manager.jobs.started"), 190, filter=False,
                help=t("manager.jobs.started.help"), **_said("started"), **OPENS_REPORT),
    grid.column("took", t("manager.jobs.took"), 130, filter=False,
                help=t("manager.jobs.took.help"), **_said("took"), **OPENS_REPORT),
    OUTCOME_STATES.column("outcome", t("manager.jobs.outcome"), 140, **OPENS_REPORT),
    grid.column("detail", t("manager.jobs.detail"), 420,
                help=t("manager.jobs.detail.help"),
                **{":cellRenderer": _DETAIL_RENDERER}, **OPENS_REPORT),
]


def outcome_of(job: dict[str, Any]) -> str:
    state = str(job.get("state") or "")
    if state == jobs.DONE and job.get("stopped"):
        return STOPPED
    if state == jobs.DONE and int(job.get("problems") or 0):
        return WITH_ERRORS
    return state


# The outcomes worth going to read, which the rail's Jobs entry counts.
TO_LOOK_AT = (jobs.FAILED, WITH_ERRORS)
# Named in the tooltip up to here, and counted past it.
NAMED_UP_TO = 3


def unseen(found: list[dict[str, Any]], since: float) -> list[dict[str, Any]]:
    """The jobs that ended Failed or Done with errors after `since`."""
    return [job for job in found
            if outcome_of(job) in TO_LOOK_AT and float(job.get("finished_at") or 0) > since]


def unseen_said(items: list[dict[str, Any]]) -> str:
    """One phrase to a job, in the grid's words, and a count once there are too many."""
    if len(items) > NAMED_UP_TO:
        return t("manager.page.jobs_unseen", count=len(items))
    return "; ".join(
        t("manager.page.job_outcome", what=named(job),
          outcome=t(f"manager.jobs.outcome.{outcome_of(job)}")) for job in items)


def named(job: dict[str, Any]) -> str:
    """What the job calls itself, else its kind in words."""
    return str(job.get("title") or "") or what_ran(str(job.get("kind") or ""))


def what_ran(kind: str) -> str:
    """The kind in words, or as written where nothing names it: an extension's kinds are
    its own and carry its name."""
    key = f"manager.jobs.kind.{kind.replace('.', '_')}"
    said = t(key)
    return kind if said == key else said


def _detail(job: dict[str, Any], outcome: str) -> str:
    """The error for a failure, what it is doing now for a running job, else its counts."""
    field = {jobs.FAILED: "error", jobs.RUNNING: "message"}.get(outcome, "detail")
    return str(job.get(field) or "")


def row(job: dict[str, Any], now: float) -> dict[str, Any]:
    started = float(job.get("started_at") or 0)
    finished = job.get("finished_at")
    outcome = outcome_of(job)
    ran = (float(finished) if finished else now) - started
    return {"id": str(job.get("id") or ""),
            "what": named(job),
            "kind": str(job.get("kind") or ""),
            "started": started, "started_said": when.at_second(started),
            "took": ran, "took_said": when.took(ran) if started else "",
            "outcome": outcome,
            "report": bool(job.get("report")),
            "detail": _detail(job, outcome)}


def rows(found: list[dict[str, Any]], now: float, outcome: str = "") -> list[dict[str, Any]]:
    """Newest first, narrowed to one outcome where one is asked for."""
    kept = [row(job, now) for job in found if not outcome or outcome_of(job) == outcome]
    return sorted(kept, key=lambda one: one["started"], reverse=True)
