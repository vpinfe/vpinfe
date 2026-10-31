"""What this device's frontend is showing, and asking it to show something else.

A request is answered 202: the windows apply it, and the change arrives as the next
`frontend.state_changed` rather than in the response.
"""

from __future__ import annotations

from fastapi import APIRouter, Response

from common import events
from common.host import frontend_browser, frontend_state

from . import models, scopes
from .auth import requires
from .events import declare_snapshot, frontend_event

router = APIRouter(prefix="/frontend", tags=["frontend"])


def _state() -> dict:
    return frontend_event(frontend_state.current().as_dict())


@router.get("/state", summary="What the frontend is showing",
            dependencies=[requires(scopes.PLAY_READ)])
def get_frontend_state() -> models.FrontendState:
    return models.FrontendState.model_validate(_state()["state"])


@router.get("/browser", summary="What the frontend's browser can play",
            dependencies=[requires(scopes.PLAY_READ)])
def get_frontend_browser() -> models.FrontendBrowser:
    return models.FrontendBrowser.model_validate(frontend_browser.with_library())


@router.put("/collection", summary="Show a collection on the frontend", status_code=202,
            dependencies=[requires(scopes.INPUT_ACT)])
def show_collection(body: models.ShowCollectionRequest) -> Response:
    """An empty name is the whole library."""
    frontend_state.show(body.name)
    return Response(status_code=202)


@router.put("/game", summary="Move the frontend's wheel to a game", status_code=202,
            dependencies=[requires(scopes.INPUT_ACT)])
def move_wheel(body: models.MoveWheelRequest) -> Response:
    frontend_state.move_to(body.id)
    return Response(status_code=202)


@router.put("/preview", summary="Show a recording waiting for a decision, in place",
            status_code=202, dependencies=[requires(scopes.INPUT_ACT)])
def show_preview(body: models.ShowPreviewRequest) -> Response:
    frontend_state.preview(body.proposal, body.showing)
    return Response(status_code=202)


@router.delete("/preview", summary="Stop showing a recording", status_code=202,
               dependencies=[requires(scopes.INPUT_ACT)])
def end_preview() -> Response:
    frontend_state.end_preview()
    return Response(status_code=202)


def declare_snapshots() -> None:
    declare_snapshot(events.FRONTEND_STATE_CHANGED, _state)
