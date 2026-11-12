"""The event stream: `GET /api/v1/events`, Server-Sent Events off the internal bus.

The bus runs its handlers on the thread that published, so nothing here may block:
a client gets a bounded queue, and one that cannot keep up is dropped rather than
allowed to slow a launch down. The stream subscribes and never hooks - a client on
the far end of a socket must not be able to stop an operation.

See docs/http-api.md for the contract.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections import deque
from collections.abc import AsyncIterator, Callable
from functools import lru_cache
from typing import Any

from fastapi import APIRouter, Header, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse

from common import events
from common.devices import install_identity
from common.extensions import reach
from common.games import game_identity
from common.i18n import t
from common.runtime import jobs as job_registry

from . import jobs as jobs_api
from . import scopes
from .auth import requires
from .errors import InvalidRequestError

logger = logging.getLogger("vpinfe.httpapi.events")

router = APIRouter(tags=["events"])

# Enough backlog for a client that reconnects promptly. Past it a resume is a gap,
# which the hello frame reports rather than papers over.
HISTORY_LIMIT = 256

# Per-client backlog. A client this far behind is not one that wants the rest of
# the stream, so it is dropped and left to reconnect.
QUEUE_LIMIT = 64

# Idle write. Also how a disconnect that produced no other traffic is noticed.
KEEPALIVE_SECONDS = 15.0

# How long the browser waits before reconnecting, in milliseconds.
RETRY_MS = 3000

# A transport frame, not a bus event: the stream is open, and here is whether the
# client's resume point was still available.
HELLO_EVENT = "stream.hello"


def _game_event(game: Any = None, table_id: str | None = None, **_: Any) -> dict:
    """The wire shape of a game lifecycle event.

    The bus carries the Game object and the whole ini config because its handlers
    are in-process. Neither belongs on a socket, so the stream sends a reference to
    the game rather than the game: an id, a name to show, and the link to fetch the
    rest. That link is what keeps this a pointer instead of a second, thinner answer
    to "what does a game look like".

    `table` is which of the game's tables launched, and it is why the launch events are named for a
    table. The bus carries it; dropping it here leaves the wire with an event called
    `table.launching` that says nothing about which table.
    """
    if game is None:
        return {"game": None}

    table = {"id": table_id} if table_id else None
    return {"game": _game_reference(game_identity.game_id(game), game.game_dir_name),
            "table": table}


def _table_event(game: Any = None, table_id: str | None = None, **payload: Any) -> dict:
    """A game event, and who started it: `source`, as `play.state_changed` has it."""
    return _game_event(game, table_id) | {
        name: payload[name] for name in ("source",) if name in payload}


def _session_event(game: Any = None, table_id: str | None = None, **payload: Any) -> dict:
    """A table event, and who played it: `up` and whether the game is Private from the
    launch on, and at the end the seconds, the high score table as read and each player's
    new entries."""
    return _table_event(game, table_id, **payload) | {
        name: payload[name]
        for name in ("up", "private", "seconds", "reading", "new_entries")
        if name in payload}


def _picture_event(game: Any = None, table_id: str | None = None, **payload: Any) -> dict:
    """A table event, and the picture it kept: its `file` among the game's pictures, when it
    was `taken` and whether the game is Private."""
    return _game_event(game, table_id) | {
        name: payload[name] for name in ("file", "taken", "private") if name in payload}


def _game_reference(game_id: str, name: str) -> dict:
    reference: dict[str, Any] = {"id": game_id, "name": name}
    if game_id:
        reference["links"] = {"self": f"/api/v1/games/{game_id}"}
    return reference


def frontend_event(state: dict) -> dict:
    """`frontend.state_changed`, and the snapshot and read that answer the same."""
    game = state.get("game")
    shown = state.get("preview")
    if shown:
        shown = shown | {"game": _game_reference(shown["game"]["id"], shown["game"]["name"])}
    return {"state": state | {"game": game and _game_reference(game["id"], game["name"]),
                              "preview": shown}}


def _job_event(**payload: Any) -> dict:
    """The job shape, kept to the fields common/events.py documents.

    Picking the fields rather than forwarding the payload is what makes the shape a
    contract: a caller adding a keyword does not change what subscribers receive.
    """
    return {name: payload[name] for name in ("job_id", "pct", "message", "error")
            if name in payload}


def _as_published(**payload: Any) -> dict:
    """For events whose bus payload is already wire-safe."""
    return payload


def _collections_event(**_payload: Any) -> dict:
    """That the collections changed, and nothing about where they live.

    The bus carries the file's path so in-process handlers can log it. A subscriber on
    another machine has a different path for the same collections, and would be told
    one true only here - so it is dropped and the event says only that a re-read is due.
    """
    return {}


def _lifecycle_event(**payload: Any) -> dict:
    """What is happening, and which kind of surface asked for it.

    The origin's address is dropped: it means nothing outside this process, and would
    name one user's browser tab to every other subscriber.
    """
    return {name: payload[name] for name in ("scope", "action", "description", "reason")
            if name in payload} | {"origin": payload.get("surface", "")}


# Which bus events reach the network, and the shape each one takes when it does.
# An explicit list rather than "whatever was emitted": an extension will one day
# publish onto the same bus, and what it may broadcast is a scope question that
# has to be answered before anything is streamed.
STREAMED_EVENTS: dict[str, Callable[..., dict]] = {
    events.TABLE_LAUNCHING: _table_event,
    events.TABLE_LAUNCHED: _session_event,
    events.TABLE_EXITED: _table_event,
    events.TABLE_PLAY_RECORDED: _session_event,
    events.TABLE_PICTURE_TAKEN: _picture_event,
    events.GAME_SELECTED: _game_event,
    # The library moved under whoever is holding it. Local subscribers get this
    # already; it crosses now because a frontend on another machine has no other way
    # to learn its copy is stale - it cannot watch the files.
    events.GAME_CHANGED: _game_event,
    events.COLLECTIONS_CHANGED: _collections_event,
    events.PLAY_STATE_CHANGED: _as_published,
    events.FRONTEND_STATE_CHANGED: frontend_event,
    events.PLAYERS_CHANGED: _as_published,
    events.LIFECYCLE_ACTING: _lifecycle_event,
    events.JOB_PROGRESS: _job_event,
    events.JOB_DONE: _job_event,
    events.JOB_FAILED: _job_event,
    events.CAPTURE_RUN_CHANGED: _as_published,
    events.CONFIG_WAITING_CHANGED: _as_published,
}

JOB_EVENTS = frozenset({events.JOB_PROGRESS, events.JOB_DONE, events.JOB_FAILED})


class _Stream:
    """One connected client.

    Owned by the event loop serving its request; the publishing thread reaches it
    only through `deliver`.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop, names: frozenset[str] | None,
                 tier: str = reach.LIBRARY) -> None:
        self.loop = loop
        self.names = names
        self.tier = tier
        self.queue: asyncio.Queue[str] = asyncio.Queue(QUEUE_LIMIT)
        self.overrun = False

    def wants(self, name: str, needed: str = "") -> bool:
        return (self.names is None or name in self.names) and jobs_api.reaches(self.tier, needed)

    def deliver(self, frame: str) -> None:
        """Hand a frame over from the publishing thread. Never blocks."""
        try:
            self.loop.call_soon_threadsafe(self.offer, frame)
        except RuntimeError:
            # The loop is gone; the generator's cleanup will drop this stream.
            self.overrun = True

    def offer(self, frame: str) -> None:
        if self.overrun:
            return
        try:
            self.queue.put_nowait(frame)
        except asyncio.QueueFull:
            # No sentinel to push - the queue is full by definition. The consumer
            # drains what it has and checks this flag before waiting again.
            self.overrun = True


_snapshots: dict[str, Callable[[], dict]] = {}

_lock = threading.Lock()
_streams: list[_Stream] = []
# (seq, name, frame, the tier a subscriber must reach to be sent it)
_history: deque[tuple[int, str, str, str]] = deque(maxlen=HISTORY_LIMIT)
_seq = 0


def declare_snapshot(name: str, provider: Callable[[], dict]) -> None:
    """Register the current value of a state-carrying event.

    A client connecting between two changes would otherwise learn nothing until the
    next one. The provider returns the payload shape the event itself carries, so a
    snapshot and a live event are the same frame to whoever reads them.
    """
    _snapshots[name] = provider


def _encode(payload: dict) -> str | None:
    """The data line's JSON, or None if the payload will not survive the trip."""
    try:
        return json.dumps(jsonable_encoder(payload))
    except Exception:
        logger.exception("Event payload will not serialize; dropping it")
        return None


def _frame(name: str, data: str, seq: int | None = None) -> str:
    """One SSE frame. Without a seq it carries no id, so it is not a resume point -
    which is right for hello and snapshot frames, neither of which is history."""
    head = f"id: {seq}\n" if seq is not None else ""
    return f"{head}event: {name}\ndata: {data}\n\n"


@lru_cache(maxsize=1)
def _install_id() -> str:
    """This install's id. Cached: `_dispatch` runs on the publishing thread and reading
    it off disk costs ~2ms, which a launch and every job tick would pay."""
    try:
        return install_identity.install_id()
    except Exception:
        logger.debug("Could not read this install's id for an event", exc_info=True)
        return ""


def _provenance() -> dict:
    """Which install an event happened on - the surface that asked stays dropped, above.

    Absent rather than empty, so "did not say" is not an id of "".
    """
    install_id = _install_id()
    return {"install_id": install_id} if install_id else {}


def _tier_needed(name: str, payload: dict) -> str:
    """The tier that may be sent this event. A job's progress and error text are its own
    work's to show, so they follow the job; an unknown job id is Library's."""
    if name not in JOB_EVENTS or not payload.get("job_id"):
        return ""
    job = job_registry.get(payload["job_id"])
    return jobs_api.tier_needed(job) if job is not None else reach.LIBRARY


def _dispatch(name: str, payload: dict) -> None:
    """Bus subscriber. Runs on whichever thread published, so it only formats and hands off."""
    global _seq

    data = _encode(STREAMED_EVENTS[name](**payload) | _provenance())
    if data is None:
        return
    needed = _tier_needed(name, payload)

    with _lock:
        _seq += 1
        frame = _frame(name, data, _seq)
        _history.append((_seq, name, frame, needed))
        targets = [stream for stream in _streams if stream.wants(name, needed)]

    for stream in targets:
        stream.deliver(frame)


def _handler_for(name: str) -> Callable[..., None]:
    # The bus calls handlers as handler(**payload), so the event name has to be
    # carried by the closure rather than passed.
    def handle(**payload: Any) -> None:
        _dispatch(name, payload)

    return handle


_HANDLERS = {name: _handler_for(name) for name in STREAMED_EVENTS}


def attach() -> None:
    """Subscribe to the bus.

    Idempotent by construction: building the app twice leaves one subscription, and
    a bus that has been cleared is wired up again.
    """
    for name, handler in _HANDLERS.items():
        events.unsubscribe(name, handler)
        events.subscribe(name, handler)


def reset() -> None:
    """Drop every stream and the replay history. For tests."""
    global _seq

    with _lock:
        _streams.clear()
        _history.clear()
        _seq = 0
    _snapshots.clear()
    _install_id.cache_clear()


def _parse_filter(raw: str) -> frozenset[str] | None:
    """The `events=` query parameter. Empty means everything."""
    wanted = {name.strip() for name in raw.split(",") if name.strip()}
    if not wanted:
        return None
    unknown = sorted(wanted - set(STREAMED_EVENTS))
    if unknown:
        raise InvalidRequestError(
            t("error.events.bad_event"),
            details={"unknown": unknown, "known": sorted(STREAMED_EVENTS)},
        )
    return frozenset(wanted)


def _resume_point(last_event_id: str | None) -> int | None:
    try:
        return int(last_event_id) if last_event_id else None
    except ValueError:
        return None


def _replay(resume: int | None, wanted: frozenset[str] | None,
            tier: str = reach.LIBRARY) -> tuple[list[str], bool]:
    """The frames a resuming client missed, and whether the resume was complete.
    Call with `_lock` held, so nothing is both replayed and delivered live.

    An incomplete resume is reported rather than hidden: the client is told to
    treat what it holds as stale instead of silently missing a job that finished.
    A resume point ahead of the sequence is one of ours from a previous run.
    """
    if resume is None or resume > _seq:
        return [], False
    if not _history:
        return [], resume == _seq
    if resume < _history[0][0] - 1:
        return [], False
    return [frame for seq, name, frame, needed in _history
            if seq > resume and (wanted is None or name in wanted)
            and jobs_api.reaches(tier, needed)], True


def _snapshot_frames(wanted: frozenset[str] | None) -> list[str]:
    frames = []
    for name in STREAMED_EVENTS:
        provider = _snapshots.get(name)
        if provider is None or (wanted is not None and name not in wanted):
            continue
        try:
            data = _encode(provider())
        except Exception:
            logger.exception("Snapshot for %s failed", name)
            continue
        if data is not None:
            frames.append(_frame(name, data))
    return frames


async def _stream(wanted: frozenset[str] | None, last_event_id: str | None,
                  tier: str = reach.LIBRARY) -> AsyncIterator[str]:
    """Serve one client until it goes away or falls too far behind."""
    stream = _Stream(asyncio.get_running_loop(), wanted, tier)
    with _lock:
        # Registering and reading the history together is what stops an event
        # arriving in between from being both replayed and delivered live.
        _streams.append(stream)
        position = _seq
        backlog, resumed = _replay(_resume_point(last_event_id), wanted, tier)

    try:
        yield f"retry: {RETRY_MS}\n\n"
        yield _frame(HELLO_EVENT, json.dumps({"seq": position, "resumed": resumed}))
        for frame in backlog:
            yield frame
        if not resumed:
            for frame in _snapshot_frames(wanted):
                yield frame

        # An overrun client still gets what was queued before it fell behind, so its
        # resume point is as far along as it can be when it comes back.
        while not (stream.overrun and stream.queue.empty()):
            try:
                yield await asyncio.wait_for(stream.queue.get(), KEEPALIVE_SECONDS)
            except TimeoutError:
                yield ": keepalive\n\n"
        logger.warning("Event stream client fell behind by more than %s frames; closing",
                       QUEUE_LIMIT)
    finally:
        with _lock:
            if stream in _streams:
                _streams.remove(stream)


@router.get("/events", summary="Subscribe to the event stream",
            response_class=StreamingResponse,
            dependencies=[requires(scopes.EVENTS_SUBSCRIBE)])
async def subscribe(
    request: Request,
    names: str = Query("", alias="events",
                       description="Comma-separated event names. Empty means all of them."),
    last_event_id: str | None = Header(
        None, description="Resume point. Browsers send this on reconnect by themselves."),
) -> StreamingResponse:
    wanted = _parse_filter(names)
    return StreamingResponse(
        _stream(wanted, last_event_id, jobs_api.caller_tier(request)),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Tells nginx and friends not to sit on the response waiting for a body.
            "X-Accel-Buffering": "no",
        },
    )
