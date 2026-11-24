"""Slow work, as a resource.

The stream is how you follow a job; this is how you find out what is running
without having been connected, and how you learn the outcome if you missed the
event. Everything here is read-only - a job is started by asking for the work,
not by posting to /jobs, so the permission is always the permission of the work
itself.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from common.extensions import reach
from common.i18n import t
from common.runtime import jobs as job_registry

from . import models, scopes
from .auth import requires
from .errors import NotFoundError

router = APIRouter(prefix="/jobs", tags=["jobs"])


def tier_needed(job: job_registry.Job) -> str:
    """The tier a caller must reach to see this job: that of the request that started an
    extension's job, Library where it records none. Empty for core's own jobs, which anyone
    holding `jobs:read` may see."""
    if not job.extension:
        return ""
    return reach.for_reading(job.tier if job.tier in reach.TIERS else reach.LIBRARY)


def reaches(caller_tier: str, needed: str) -> bool:
    return not needed or reach.TIERS.index(caller_tier) >= reach.TIERS.index(needed)


def readable_at(job: job_registry.Job, caller_tier: str) -> bool:
    return reaches(caller_tier, tier_needed(job))


def caller_tier(request: Request) -> str:
    identity = getattr(request.state, "identity", None)
    return identity.tier if identity is not None else reach.PLAY


# Where each kind of job that can be stopped is stopped.
_STOPS = {job_registry.KIND_MEDIA_CAPTURE: "/api/v1/capture/runs/current/stop"}


def resource(job: job_registry.Job, with_result: bool = False) -> dict:
    links = {"self": f"/api/v1/jobs/{job.id}", "events": "/api/v1/events"}
    stop = job.stop_link or _STOPS.get(job.kind, "")
    if job.stoppable and job.state == job_registry.RUNNING and stop:
        links["stop"] = stop
    return {**job.snapshot(with_result), "links": links}


@router.get("", summary="Jobs, running first",
            dependencies=[requires(scopes.JOBS_READ)])
def list_jobs(request: Request,
              kind: str = Query("", description="Filter by job kind"),
              limit: int = Query(20, ge=1, le=job_registry.HISTORY_LIMIT,
                                 description="How many, running first then newest "
                                             "finished")) -> models.JobList:
    tier = caller_tier(request)
    found = [job for job in job_registry.recent(job_registry.HISTORY_LIMIT)
             if readable_at(job, tier) and (not kind or job.kind == kind)]
    return models.JobList.model_validate(
        {"jobs": [resource(job) for job in found[:limit]]})


@router.get("/{job_id}", summary="One job",
            dependencies=[requires(scopes.JOBS_READ)])
def get_job(job_id: str, request: Request) -> models.JobResource:
    """One job, and what it answered.

    The result is here and not on the list: some jobs answer with a row per game, and a
    listing carrying twenty of those would make watching the queue expensive. A job the
    caller may not see answers as an unknown one.
    """
    job = job_registry.get(job_id)
    if job is None or not readable_at(job, caller_tier(request)):
        raise NotFoundError(t("error.jobs.unknown_job", job_id=(job_id)))
    return models.JobResource(**resource(job, with_result=True))
