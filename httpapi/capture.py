"""Recording this device's own screens into its tables' media."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse

from common import events
from common.capture import adapters, commands, preflight, proposals, run, trial
from common.capture.adapters import portal
from common.host import frontend_state

from . import jobs as jobs_api
from . import models, scopes
from .auth import require, requires
from .events import declare_snapshot

router = APIRouter(prefix="/capture", tags=["capture"])


@router.get("", summary="What this device can record, and why not",
            dependencies=[requires(scopes.PLAY_READ)])
def get_capture() -> models.CaptureReport:
    return models.CaptureReport.model_validate(preflight.report())


def _request(body: models.CaptureRequest) -> run.Request:
    return run.Request(games=body.games,
                       tables=[(one.game, one.table) for one in body.tables],
                       kinds=body.kinds, existing=body.existing, settings=body.settings,
                       sound=body.sound,
                       confirmed=body.confirmed.count if body.confirmed else None,
                       review=body.review)


@router.post("/plan", summary="What a recording would fill and replace, doing nothing",
             dependencies=[requires(scopes.GAMES_READ)])
def plan_capture(body: models.CaptureRequest) -> models.CapturePlan:
    return models.CapturePlan.model_validate(run.plan(_request(body)))


def _started(job: Any, response: Response) -> models.JobResource:
    response.headers["Location"] = f"/api/v1/jobs/{job.id}"
    return models.JobResource(**jobs_api.resource(job))


@router.post("/runs", summary="Record games or tables, one after another", status_code=202,
             dependencies=[requires(scopes.CAPTURE_RUN)])
def start_capture(body: models.CaptureRequest, response: Response,
                  request: Request) -> models.JobResource:
    if COMMAND_SETTINGS & set(body.settings):
        require(request, scopes.PROTECTED)
    return _started(run.start(_request(body)), response)


COMMAND_SETTINGS = frozenset(commands.SETTINGS.values())


def _current() -> dict:
    return {"run": run.current()}


@router.get("/runs/current", summary="The recording run in hand, running or paused",
            dependencies=[requires(scopes.PLAY_READ)])
def get_current_run() -> models.CaptureRunCurrent:
    return models.CaptureRunCurrent.model_validate(_current())


@router.post("/runs/current/pause", summary="Hold the run, recording its game again later",
             dependencies=[requires(scopes.CAPTURE_RUN)])
def pause_run() -> models.CaptureRunCurrent:
    return models.CaptureRunCurrent.model_validate({"run": run.pause()})


@router.post("/runs/current/resume", summary="Carry on with a paused run", status_code=202,
             dependencies=[requires(scopes.CAPTURE_RUN)])
def resume_run(response: Response) -> models.JobResource:
    return _started(run.resume(), response)


@router.post("/runs/current/stop", summary="End the run, keeping what it has done",
             dependencies=[requires(scopes.CAPTURE_RUN)])
def stop_run() -> models.CaptureRunCurrent:
    return models.CaptureRunCurrent.model_validate({"run": run.stop()})


@router.post("/runs/current/discard", summary="Forget a run, keeping what it has done",
             dependencies=[requires(scopes.CAPTURE_RUN)])
def discard_run() -> models.CaptureRunCurrent:
    return models.CaptureRunCurrent.model_validate({"run": run.discard()})


@router.post("/test", summary="Record a few seconds of the playfield with the commands",
             dependencies=[requires(scopes.PROTECTED)])
def test_capture(body: models.CaptureTestRequest) -> models.CaptureTest:
    return models.CaptureTest.model_validate(trial.test(body.settings))


@router.post("/screens/choose", summary="Ask the desktop which screens VPinFE may record",
             status_code=202, dependencies=[requires(scopes.CAPTURE_RUN)])
def choose_screens(response: Response) -> models.JobResource:
    """Accepted, not done: the desktop's picker waits for someone at this device."""
    return _started(portal.start_choosing(adapters.resolve()), response)


@router.get("/proposals", summary="Recordings waiting for a decision",
            dependencies=[requires(scopes.GAMES_READ)])
def get_proposals() -> models.CaptureProposals:
    return models.CaptureProposals.model_validate(proposals.listing())


@router.get("/proposals/{proposal_id}/file", summary="Play a recording waiting for a decision",
            dependencies=[requires(scopes.GAMES_READ)])
def get_proposal_file(proposal_id: str) -> FileResponse:
    return FileResponse(proposals.file_of(proposal_id))


@router.post("/proposals/{proposal_id}", summary="Place a recording, or throw it away",
             dependencies=[requires(scopes.CAPTURE_RUN)])
def decide_proposal(proposal_id: str,
                    body: models.CaptureProposalUse) -> models.CaptureProposalUsed:
    decided = proposals.decide(proposal_id, body.use)
    frontend_state.decided(proposal_id)
    return models.CaptureProposalUsed.model_validate(decided)


@router.delete("/proposals", summary="Throw away every recording waiting",
               dependencies=[requires(scopes.CAPTURE_RUN)])
def discard_proposals() -> models.CaptureProposalsDiscarded:
    return models.CaptureProposalsDiscarded.model_validate(proposals.discard_all())


def declare_snapshots() -> None:
    declare_snapshot(events.CAPTURE_RUN_CHANGED, _current)
