"""This device's DOF, the cabinet's lights and solenoids.

Firing an event drives hardware somebody may be standing at, which is what `input:act`
is declared for. It reaches only this install's DOF; a client aimed at another machine
calls that machine's copy of the route.
"""

from __future__ import annotations

from fastapi import APIRouter, Response

from common.config.paths import get_ini_config
from common.host import dof_service
from common.online import dof_sync

from . import jobs as jobs_api
from . import models, scopes
from .auth import requires

router = APIRouter(prefix="/dof", tags=["dof"])


@router.get("/event", summary="Which DOF event is on, and whether one could be turned on",
            dependencies=[requires(scopes.CONFIG_READ)])
def event_state() -> models.DofEventState:
    """What a surface needs to offer a test: the event on now, and why none could be
    turned on, in words."""
    outcome = dof_service.blocked(get_ini_config())
    event = dof_service.current_event()
    return models.DofEventState(event=event, blocked="" if outcome else outcome.words(),
                                no_boards=event is not None and dof_service.no_boards())


@router.post("/event", summary="Turn a DOF event on until it is stopped",
             dependencies=[requires(scopes.INPUT_ACT)])
def fire(payload: models.DofEventRequest) -> models.DofEventResult:
    """Turn the event on, firing it again if it is already on."""
    event = dof_service.turn_on(get_ini_config(), payload.event)
    return models.DofEventResult(event=event, no_boards=dof_service.no_boards())


@router.get("/config", summary="When the DOF config was last downloaded",
            dependencies=[requires(scopes.CONFIG_READ)])
def config_state() -> models.DofConfigState:
    """What a surface needs to say how fresh this device's DOF config is."""
    return models.DofConfigState(**dof_sync.state(get_ini_config()))


@router.post("/config", summary="Download the DOF config from the DOF Config Tool",
             status_code=202, dependencies=[requires(scopes.CONFIG_WRITE)])
def download_config(response: Response) -> models.JobResource:
    """Accepted, not done. Always downloads, whatever version the tool is at, and spends
    the stored API key; the job's result says whether any file changed."""
    job = dof_sync.start(get_ini_config())
    response.headers["Location"] = f"/api/v1/jobs/{job.id}"
    return models.JobResource(**jobs_api.resource(job))


@router.delete("/event", summary="Turn the DOF event off",
               dependencies=[requires(scopes.INPUT_ACT)])
def stop() -> models.DofEventResult:
    """Clear the event now on. Nothing on is not an error."""
    dof_service.clear_active_dof_event(get_ini_config())
    return models.DofEventResult(event=None)
