"""The in-process event bus.

A hook is part of an operation and can stop it; a subscriber is only told. Handlers
run on the publishing thread - this is not a work queue. See docs/common.md.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("vpinfe.common.events")

# Table lifecycle. Every hook on `launching` has finished before the table starts.
TABLE_LAUNCHING = "table.launching"
TABLE_LAUNCHED = "table.launched"
TABLE_EXITED = "table.exited"

# The session's play data is on disk. Its own event because `exited` fires first on
# purpose, before the runtime and the score are written.
TABLE_PLAY_RECORDED = "table.play_recorded"

# The library changed under whoever is holding it. Re-reading a game replaces the object
# rather than mutating it, so a holder of the old one is stale and cannot tell.
GAME_CHANGED = "game.changed"
COLLECTIONS_CHANGED = "collections.changed"

# The player moved to a game in the wheel. Fires once per wheel stop and nothing may
# block on it, so subscribers only. Named for what it carries: the wheel stops on a
# game, and the payload has never held a table.
GAME_SELECTED = "game.selected"

# Settings were saved. Carries `keys`, each as `section.key`, for the ones whose stored
# value changed, and `ini_config`, the store they were written to. A subscriber acts on
# the keys it owns, so a setting that applies `now` is one something here listens for.
SETTINGS_CHANGED = "settings.changed"

# An extension was switched on or off. Carries `extension` and `enabled`. Whether it runs is
# not decided here: one switched on after the run began starts at the next start.
EXTENSION_SWITCHED = "extension.switched"

# What waits for a restart or a reopen changed. Carries the whole answer, like
# play.state_changed, so a consumer that missed one is correct after the next.
#   config.waiting_changed  {waiting, reopen, http_port}
CONFIG_WAITING_CHANGED = "config.waiting_changed"

# Play-host state changed. Carries the whole new state, so a consumer that missed one
# is still correct after the next.
PLAY_STATE_CHANGED = "play.state_changed"

# What the frontend is showing changed: whether it is up, its collection, the game on
# the wheel. Carries the whole state, like play.state_changed.
FRONTEND_STATE_CHANGED = "frontend.state_changed"

# Who plays here, or who is up, changed. Carries the whole roster, like
# play.state_changed.
PLAYERS_CHANGED = "players.changed"

# A player rated a game. Plain data, for an extension to read.
#   game.rated  {game_id, player: {id, name, initials, owner, guest} | None, rating}
GAME_RATED = "game.rated"

# A player's account with an extension started or stopped sharing. Plain data, for the
# extension whose account it is:
#   account.share_changed  {player: {id, name, initials, owner, guest}, extension, share}
ACCOUNT_SHARE_CHANGED = "account.share_changed"

# Something is starting, stopping or restarting. Announced after it is confirmed, so a
# surface that did not ask can say what is about to happen. Subscribers only - a
# confirmation is asked for on the surface that asked, not on this.
LIFECYCLE_ACTING = "lifecycle.acting"

# Slow work, one shape everywhere:
#   job.progress  {job_id, pct, message}
#   job.done      {job_id}
#   job.failed    {job_id, error}
JOB_PROGRESS = "job.progress"
JOB_DONE = "job.done"
JOB_FAILED = "job.failed"

# A recording run started, moved to its next game, paused, resumed or ended. Carries the
# whole run, null once there is none, like play.state_changed.
#   capture.run_changed  {run}
CAPTURE_RUN_CHANGED = "capture.run_changed"

# A player pressed something outside the browser. Subscribers only, and deliberately:
# a press is not an operation anything may refuse, and a hook that could stop one would
# be a way for a plugin to make a cabinet button do nothing.
#   input.action  {action, phase: press|release, source, ttl_ms?, expired?}
INPUT_ACTION = "input.action"

# A Manager's Input page has asked for this device's controller, so its buttons and keys go
# there and do nothing here. Armed lapses after `ttl_ms` unless it is sent again. `browser`
# and `device` name the newest page's holder for the line the cabinet shows; `device` is ""
# for a page on this device.
#   input_capture.armed     {armed, ttl_ms, browser, device}
#   input_capture.press     {token, phase: down|up}   token as the Manager stores a button
#   input_capture.released  {}   a button held at the cabinet took them back from every page
INPUT_CAPTURE_ARMED = "input_capture.armed"
INPUT_CAPTURE_PRESS = "input_capture.press"
INPUT_CAPTURE_RELEASED = "input_capture.released"

DEFAULT_PRIORITY = 100

# Per-item or timer events: no debug line, handlers still run.
_UNLOGGED = frozenset({JOB_PROGRESS, INPUT_CAPTURE_ARMED})


@dataclass
class _Registrations:
    hooks: list[tuple[int, int, Callable]] = field(default_factory=list)
    subscribers: list[Callable] = field(default_factory=list)


_lock = threading.RLock()
_events: dict[str, _Registrations] = {}
_sequence = 0


def _slot(name: str) -> _Registrations:
    return _events.setdefault(name, _Registrations())


def hook(name: str, handler: Callable, *, priority: int = DEFAULT_PRIORITY) -> Callable:
    """Register a handler that the operation waits for. Lower priority runs first.

    Raising from a hook stops the operation. Register one only when that is the
    behavior you want.
    """
    global _sequence
    with _lock:
        _sequence += 1
        # The counter keeps equal priorities in registration order rather than
        # letting sort() compare the functions themselves.
        _slot(name).hooks.append((priority, _sequence, handler))
        _slot(name).hooks.sort(key=lambda entry: (entry[0], entry[1]))
    return handler


def subscribe(name: str, handler: Callable) -> Callable:
    """Register a handler that is told what happened and cannot affect it."""
    with _lock:
        _slot(name).subscribers.append(handler)
    return handler


def unsubscribe(name: str, handler: Callable) -> None:
    with _lock:
        slot = _slot(name)
        slot.hooks = [entry for entry in slot.hooks if entry[2] is not handler]
        slot.subscribers = [h for h in slot.subscribers if h is not handler]


def emit(name: str, **payload: Any) -> None:
    """Run the hooks in order, then notify the subscribers.

    Hook failures propagate. Subscriber failures are logged and contained.
    """
    with _lock:
        slot = _slot(name)
        hooks = [entry[2] for entry in slot.hooks]
        subscribers = list(slot.subscribers)

    if name not in _UNLOGGED:
        logger.debug("emit %s hooks=%s subscribers=%s", name, len(hooks), len(subscribers))

    for handler in hooks:
        handler(**payload)

    for handler in subscribers:
        try:
            handler(**payload)
        except Exception:
            logger.exception("Subscriber to %s failed", name)


def clear() -> None:
    """Drop every registration. For tests."""
    with _lock:
        _events.clear()


def registered(name: str) -> tuple[int, int]:
    """(hooks, subscribers) for an event. For tests and diagnostics."""
    with _lock:
        slot = _slot(name)
        return len(slot.hooks), len(slot.subscribers)
