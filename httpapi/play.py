"""Play-host state.

The snapshot a client takes once; `play.state_changed` on the event stream is how
it hears about every change after that.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from common import events
from common.failures import why
from common.host import launch_state, play_service, table_controls

from . import models, scopes
from .auth import requires
from .errors import ApiError
from .events import declare_snapshot
from .input import source_of

router = APIRouter(prefix="/play", tags=["play"])


@router.get("/state", summary="What this play host is doing",
            dependencies=[requires(scopes.PLAY_READ)])
def get_play_state() -> models.PlayState:
    return models.PlayState(**launch_state.current().as_dict())


@router.post("/stop", summary="Close the table this play host is running",
             dependencies=[requires(scopes.PLAY_STOP)])
def stop_play() -> models.PlayStopped:
    """Close whatever is running, and say what was closed."""
    return models.PlayStopped.model_validate(play_service.stop_playing())


@router.get("/controls", summary="What can be pressed on the table that is running",
            dependencies=[requires(scopes.PLAY_READ)])
def get_table_controls() -> models.TableControls:
    """The answer holds for the table's life, so a client may keep it and ask again when
    `play.state_changed` says a different table runs."""
    return models.TableControls.model_validate(table_controls.listing())


@router.post("/controls/{control_id}", summary="Press one control of the running table",
             dependencies=[requires(scopes.INPUT_ACT)])
def press_table_control(control_id: str, request: Request,
                        payload: models.TableControlRequest | None = None,
                        ) -> models.TableControlPress:
    """Only a control the running table's app declares, by id; no key code is accepted.
    `pressed` says the key was sent to whichever window has focus."""
    try:
        return models.TableControlPress.model_validate(
            table_controls.press(
                control_id, source=source_of(payload.source if payload else "", request),
                phase=payload.phase if payload else table_controls.TAP,
                ttl_ms=payload.ttl_ms if payload else None))
    except table_controls.CannotSendKeysError as exc:
        raise ApiError("unavailable", why(exc), status_code=503) from exc


def declare_snapshots() -> None:
    """Hand the current state to a client the moment it subscribes.

    Without it a client connecting mid-launch sees nothing until the launch ends,
    and a reconnect after a gap would leave the overlay stuck.
    """
    declare_snapshot(events.PLAY_STATE_CHANGED,
                     lambda: {"state": launch_state.current().as_dict()})
