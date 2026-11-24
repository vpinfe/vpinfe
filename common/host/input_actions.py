"""Input produced by something that is not the browser.

The keyboard and the gamepad both reach the frontend from inside the page. This is the
third producer: a caller says a player pressed something, and the install puts it on the
bus for the windows to answer. What an action *means* is `common/config/input_registry.py`; this
is only how one arrives.

A hold is a press and a release, never a discrete action repeated on a timer. Put the
timer in the caller and the acceleration curve has to live in every caller, and then
holding "next" feels different depending on whether you held a flipper, a key or a thumb.
One gesture, one curve, one place - and the place is the frontend, which already has it
for the keyboard.

Which is why a press expires. A release can be lost - a phone locks its screen, a network
drops - and a lost release is a wheel that spins until somebody notices. The press carries
a time to live, the caller renews it while the input is held, and the install releases on
its own if the renewal stops coming.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from common import events
from common.config import input_registry

logger = logging.getLogger("vpinfe.common.host.input_actions")

PRESS = "press"
RELEASE = "release"
# A tap is a press and a release in one, which is what a click or a key repeat is.
TAP = "tap"
PHASES = (TAP, PRESS, RELEASE)

# The `source` of a press the frontend's own page reported, as opposed to one an API caller,
# a mobile device or the table's keys made.
PAGE = "page"

# Long enough to survive a missed renewal on a phone, short enough that a wheel started
# by a lost release stops before anyone has to go and find out why. The caller is expected
# to renew at roughly a third of it.
DEFAULT_TTL_MS = 1500
MIN_TTL_MS = 200
# A ceiling rather than a policy: nothing needs a hold that outlives a page, and a caller
# that asks for one has almost certainly sent milliseconds where it meant seconds.
MAX_TTL_MS = 10_000

_lock = threading.Lock()
# One timer per action, not per caller. Two callers holding one action is a case the
# design deliberately leaves as last-press-wins rather than deciding it here; see the
# input design notes for the trigger that reopens it.
_held: dict[str, threading.Timer] = {}
_announcer = ThreadPoolExecutor(max_workers=1, thread_name_prefix="input-heard")
# Freezing a table waits for it to say it paused, so presses answered here run one at a
# time on a worker of their own and never hold up the caller or each other.
_playing = ThreadPoolExecutor(max_workers=1, thread_name_prefix="input-play")
# A press answered here that has not been let go of, and until when it counts as held.
_answered: dict[str, float] = {}


def known(action: str) -> bool:
    return any(one.name == action for one in input_registry.INPUT_ACTIONS)


def clamp_ttl(ttl_ms: int | None) -> int:
    if not ttl_ms:
        return DEFAULT_TTL_MS
    return max(MIN_TTL_MS, min(MAX_TTL_MS, int(ttl_ms)))


def press(action: str, *, source: str, ttl_ms: int | None = None) -> None:
    """Begin a hold, or renew one already running.

    The first press is announced and every renewal is silent: a renewal is the caller
    saying nothing has changed, and announcing it would be N presses for one gesture.
    """
    ttl = clamp_ttl(ttl_ms)
    with _lock:
        renewing = action in _held
        _arm(action, source, ttl)
    if renewing:
        return
    logger.debug("input: %s pressed by %s", action, source)
    events.emit(events.INPUT_ACTION,
                   action=action, phase=PRESS, source=source, ttl_ms=ttl)


def release(action: str, *, source: str) -> None:
    """End a hold. Announced even if nothing was holding it.

    A release with no press is not an error worth refusing: a caller that reconnects
    mid-gesture and lets go is doing the right thing, and the windows need to hear it
    whether or not this process saw the press.
    """
    with _lock:
        _disarm(action)
        _answered.pop(action, None)
    logger.debug("input: %s released by %s", action, source)
    events.emit(events.INPUT_ACTION, action=action, phase=RELEASE, source=source)


def tap(action: str, *, source: str) -> None:
    """One press and its release, for a caller with nothing to hold.

    Not a phase of its own: a press then a release already says this.
    """
    press(action, source=source)
    release(action, source=source)


def heard(action: str) -> bool:
    """A press the page already answered, put on the bus for extensions. False, and nothing
    announced, for a name that is not an input action.

    Not a hold: no release follows. `frontend/input_events.py` leaves `PAGE` out of what it
    sends the windows. Announced in order from one worker, because the page's calls are
    answered one at a time and a slow handler here would hold up its next Take Picture.
    """
    if not known(action):
        return False
    _announcer.submit(events.emit, events.INPUT_ACTION,
                      action=action, phase=PRESS, source=PAGE)
    return True


def answers_here(action: str) -> bool:
    """Whether a press of this action is answered in this process rather than by a window:
    Take Picture and Back while a table runs.

    A window in the background is not reliably running its page, and the page is not what
    freezes a table, so these never go through it.
    """
    if action not in input_registry.IN_PLAY:
        return False
    from common.host import launch_state

    state = launch_state.current()
    return state.launching and state.source != launch_state.SOURCE_CAPTURE


def play(action: str, *, source: str) -> None:
    """Answer one Take Picture or Back press here. Announced as `answered`, with no release
    after it, so a window is told how it went and never handed the press."""
    _playing.submit(_answer, action, source)


def _answer(action: str, source: str) -> None:
    from common.capture import freeze

    try:
        said = freeze.take_picture() if action == "take_picture" else freeze.resume()
    except Exception:  # noqa: BLE001 - one press must not end the worker
        logger.exception("input: %s pressed by %s was not answered", action, source)
        said = {}
    events.emit(events.INPUT_ACTION, action=action, phase=PRESS, source=source,
                answered=True, state=said.get("state", ""))


def settle() -> None:
    """Returns once every announcement and answer made so far has been delivered. For
    tests."""
    _playing.submit(lambda: None).result(timeout=5)
    _announcer.submit(lambda: None).result(timeout=5)


def act(action: str, phase: str, *, source: str,
        ttl_ms: int | None = None) -> dict:
    """One press, hold or release, named and put on the bus.

    A press with no matching release expires on its own, so a caller holding a button
    renews it - send `press` again with the same action - for as long as the thumb is
    down. That is the same press as far as the frontend is concerned; only the first one
    is announced.
    """
    from common import service_errors
    from common.config import input_registry
    from common.i18n import t

    action = (action or "").strip()
    if not known(action):
        raise service_errors.RefusedError(
            t("error.input.unknown_action", action=(action),
              actions=", ".join(one.name for one in input_registry.INPUT_ACTIONS)))
    phase = (phase or TAP).strip().lower()
    if phase not in PHASES:
        raise service_errors.RefusedError(
            t("error.input.unknown_phase", phases=", ".join(PHASES), phase=(phase)))

    if answers_here(action):
        _answer_here(action, phase, source, clamp_ttl(ttl_ms))
    elif phase == RELEASE:
        release(action, source=source)
    elif phase == PRESS:
        press(action, source=source, ttl_ms=ttl_ms)
    else:
        tap(action, source=source)
    return {"action": action, "phase": phase, "ttl_ms": clamp_ttl(ttl_ms),
            "holding": sorted(holding())}


def _answer_here(action: str, phase: str, source: str, ttl: int) -> None:
    """A press renewed while it is held is the same press, as it is everywhere else."""
    now = time.monotonic()
    with _lock:
        if phase == RELEASE:
            _answered.pop(action, None)
            return
        first = phase == TAP or now >= _answered.get(action, 0.0)
        if phase == PRESS:
            _answered[action] = now + ttl / 1000
    if first:
        play(action, source=source)


def release_all(*, source: str = "shutdown") -> None:
    """Let go of everything. For a process going away with a hold still running."""
    with _lock:
        holding = list(_held)
    for action in holding:
        release(action, source=source)


def holding() -> frozenset[str]:
    with _lock:
        return frozenset(_held)


def _arm(action: str, source: str, ttl: int) -> None:
    """Caller holds the lock."""
    _disarm(action)
    timer = threading.Timer(ttl / 1000, _expired, (action, source))
    timer.daemon = True
    _held[action] = timer
    timer.start()


def _disarm(action: str) -> None:
    """Caller holds the lock."""
    timer = _held.pop(action, None)
    if timer is not None:
        timer.cancel()


def _expired(action: str, source: str) -> None:
    """The renewal stopped coming, so let go on the caller's behalf.

    Worth a line above debug: every one of these is a release that was sent and lost, or
    a caller that stopped renewing without saying why, and both are worth being able to
    find afterwards.
    """
    with _lock:
        # Cancelled and fired at the same moment - the release already went out.
        if _held.pop(action, None) is None:
            return
    logger.info("input: %s held by %s expired without a release", action, source)
    events.emit(events.INPUT_ACTION, action=action, phase=RELEASE,
                   source=source, expired=True)
