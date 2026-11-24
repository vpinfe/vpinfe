"""A recording waiting for a decision, shown in place on the windows already open.

Whoever asks - the Remote through `PUT /frontend/preview`, or the page's own buttons -
the windows get one `ProposalPreview` message and each draws what belongs on its screen.
A run the main menu started is followed to its end, and what it left waiting is shown
one after another.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from common import events
from common.capture import proposals
from common.failures import why
from common.host import frontend_state, launch_state
from common.i18n import t
from common.runtime import jobs
from common.service_errors import BlockedError, NotFoundError, ServiceError

if TYPE_CHECKING:
    from frontend.device_channel import DeviceChannel

logger = logging.getLogger("vpinfe.frontend.preview")

MESSAGE = "ProposalPreview"
SHOWINGS = (frontend_state.BEFORE, frontend_state.AFTER)

_lock = threading.Lock()
_bridge: DeviceChannel | None = None
_registered = False
# The proposals still to show after the one on show.
_series: list[str] = []
# The job of a run the main menu started, followed to its end.
_followed: set[str] = set()


def _row(proposal_id: str) -> dict[str, Any]:
    for row in proposals.listing()["proposals"]:
        if row["id"] == proposal_id:
            return row
    raise NotFoundError(t("error.capture.no_such_proposal"), details={"proposal": proposal_id})


def _send(preview: dict[str, Any] | None) -> None:
    if _bridge is not None:
        _bridge.send_event_all_with_iframe({"type": MESSAGE, "preview": preview})


def _present(row: dict[str, Any], showing: str, refused: str = "") -> None:
    """Show a proposal; `refused` is why the last choice on it was not taken, drawn in
    place of the buttons' hint."""
    kind = str(row["kind"])
    _send({"proposal": row["id"], "showing": showing, "kind": kind,
           "label": t(f"media.kind.{kind}.label"), "game_id": row["game_id"],
           "table_id": row["table_id"], "before": row["replaces"] is not None,
           "refused": refused})
    frontend_state.previewing(frontend_state.Preview(
        str(row["id"]), showing, kind, str(row["table_id"]), str(row["game_id"]),
        str(row["name"])))


def show(proposal_id: str | None, showing: str = frontend_state.AFTER,
         series: Sequence[str] = ()) -> None:
    """Show a proposal, then each of `series` as the one before it is decided; None ends
    what is on show and leaves the rest waiting."""
    if proposal_id is None:
        return end()
    if showing not in SHOWINGS:
        showing = frontend_state.AFTER
    row = _row(proposal_id)
    if launch_state.current().launching:
        raise BlockedError(t("error.frontend.table_running"))
    with _lock:
        _series[:] = [one for one in series if one != proposal_id]
    _present(row, showing)


def end() -> None:
    with _lock:
        _series.clear()
    _send(None)
    frontend_state.previewing(None)


def _next() -> None:
    """Show the next proposal still waiting, or end."""
    while True:
        with _lock:
            upcoming = _series.pop(0) if _series else None
        if upcoming is None:
            return end()
        try:
            return _present(_row(upcoming), frontend_state.AFTER)
        except NotFoundError:
            continue


def switch(showing: str) -> dict[str, Any]:
    """The recording or the file it would replace, for the proposal on show."""
    shown = frontend_state.current().preview
    if shown is not None and showing in SHOWINGS and showing != shown.showing:
        try:
            _present(_row(shown.proposal), showing)
        except NotFoundError:
            _next()
    return {}


def decide(use: bool) -> dict[str, Any]:
    """Keep the proposal on show, or throw it away, and move on. A choice refused - the
    backups folder cannot be written to - says why on the screen and leaves the proposal
    waiting and on show."""
    shown = frontend_state.current().preview
    if shown is None:
        return {}
    try:
        proposals.decide(shown.proposal, use)
    except NotFoundError:
        _next()
        raise
    except ServiceError as exc:
        logger.warning("Proposal %s was not decided: %s", shown.proposal, exc)
        _present(_row(shown.proposal), shown.showing, why(exc))
        return {}
    except BaseException:
        _next()
        raise
    _next()
    return {}


def gone(proposal_id: str) -> None:
    """A proposal was decided elsewhere; move past it if it is the one on show."""
    shown = frontend_state.current().preview
    if shown is not None and shown.proposal == proposal_id:
        _next()


def _launching(**_payload: Any) -> None:
    """A table takes the screens; what was on show waits."""
    if frontend_state.current().preview is not None:
        end()


def follow(job_id: str) -> None:
    """Show what this run leaves waiting once it ends."""
    with _lock:
        _followed.add(job_id)


def _ended(job_id: str = "", **_payload: Any) -> None:
    with _lock:
        if job_id not in _followed:
            return
        _followed.discard(job_id)
    job = jobs.get(job_id)
    result = job.result if job is not None else None
    if not isinstance(result, dict):
        return
    waiting = [str(one["id"]) for table in result.get("tables") or []
               for one in table.get("proposed") or []]
    if not waiting:
        return
    try:
        show(waiting[0], frontend_state.AFTER, waiting[1:])
    except ServiceError:
        logger.exception("Could not show what the recording left waiting")


def register(ws_bridge: DeviceChannel) -> None:
    """Attach the windows to the preview. Idempotent."""
    global _bridge, _registered
    _bridge = ws_bridge
    frontend_state.register_preview(show, gone)
    if _registered:
        return
    events.subscribe(events.JOB_DONE, _ended)
    events.subscribe(events.TABLE_LAUNCHING, _launching)
    _registered = True


def reset_for_tests() -> None:
    global _bridge, _registered
    if _registered:
        events.unsubscribe(events.JOB_DONE, _ended)
        events.unsubscribe(events.TABLE_LAUNCHING, _launching)
    _bridge = None
    _registered = False
    with _lock:
        _series.clear()
        _followed.clear()
