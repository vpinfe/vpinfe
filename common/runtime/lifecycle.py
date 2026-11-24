"""Starting, stopping and restarting things, however the request arrives.

    scope     frontend   vpinfe     system     table
    start     yes        -          -          -
    stop      yes        yes        poweroff   yes
    restart   yes        yes        yes        -

Two axes rather than four verbs, so reboot is `restart` at system scope and an instance
started headless can open its windows without being restarted.

**An origin is an address, not a category:** a confirm belongs on the surface that asked,
and a dialog raised anywhere else is a hang on a screen nobody is watching. Confirming and
notifying are separate, and only the first can block.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from common.i18n import t, t_source

logger = logging.getLogger("vpinfe.common.runtime.lifecycle")

# What a request applies to.
FRONTEND = "frontend"       # the Chromium windows; VPinFE keeps running
VPINFE = "vpinfe"           # VPinFE itself
SYSTEM = "system"           # the machine
TABLE = "table"             # the table being played; VPinFE and the frontend keep running

# What happens to it.
START = "start"
STOP = "stop"
RESTART = "restart"

# Where the frontend windows stand, which decides which frontend actions can run.
WINDOWS_OPEN = "open"
WINDOWS_CLOSED = "closed"
WINDOWS_SETUP = "setup"      # Setup's window is up, and is not closed or reopened from here
WINDOWS_OFF = "off"          # this install is not set up to be a frontend

# Naming it avoids "stop the system" reading as "stop VPinFE on the system".
POWEROFF = (SYSTEM, STOP)

_ALLOWED = {
    (FRONTEND, START), (FRONTEND, STOP), (FRONTEND, RESTART),
    (VPINFE, STOP), (VPINFE, RESTART),
    (SYSTEM, STOP), (SYSTEM, RESTART),
    # Stop only: starting a table is a launch, which needs to know which one, and
    # restarting one is that launch again.
    (TABLE, STOP),
}

# Where a request came from. Not a category: a confirm has to reach the person who asked.
SURFACE_FRONTEND = "frontend"
SURFACE_MANAGER = "manager"
SURFACE_SIGNAL = "signal"
SURFACE_EXTENSION = "extension"
# An HTTP caller. Never answerable: the request is already over by the time anything
# here could ask, so whoever called put the question to their user before calling.
SURFACE_API = "api"


@dataclass(frozen=True)
class Origin:
    """Who asked, and where to reach them: a window name, or a Manager client id."""

    surface: str
    address: str = ""

    @property
    def is_answerable(self) -> bool:
        """Whether there is anybody there to ask. A signal is not."""
        return self.surface in (SURFACE_FRONTEND, SURFACE_MANAGER) and bool(self.address)


@dataclass(frozen=True)
class Request:
    scope: str
    action: str
    origin: Origin
    reason: str = ""

    @property
    def pair(self) -> tuple[str, str]:
        return (self.scope, self.action)

    def describe(self) -> str:
        """What this asks for, in the words a person would use.

        In English, because every caller of this is a log line. `label` is the one the
        confirm card and the API ask for.
        """
        return describe(*self.pair)


# Every allowed pair. What each is called is in the catalog, keyed by the pair, because
# the confirm card asks in the user's language and the log asks in English.
PAIRS = frozenset({
    (FRONTEND, START),
    (FRONTEND, STOP),
    (FRONTEND, RESTART),
    (VPINFE, STOP),
    (VPINFE, RESTART),
    (SYSTEM, STOP),
    (SYSTEM, RESTART),
    (TABLE, STOP),
})


# Reading order for a surface that lists them: the table, then the windows, then VPinFE,
# then the machine - least to most, so the one that costs the most is furthest away.
_ORDER = (
    (TABLE, STOP),
    (FRONTEND, RESTART), (FRONTEND, STOP), (FRONTEND, START),
    (VPINFE, RESTART), (VPINFE, STOP),
    (SYSTEM, RESTART), (SYSTEM, STOP),
)


@dataclass
class _Registry:
    confirmers: dict = field(default_factory=dict)
    performers: dict = field(default_factory=dict)
    notifiers: list = field(default_factory=list)
    availability: dict = field(default_factory=dict)


_registry = _Registry()


def register_confirmer(surface: str, confirmer: Callable[[Request], bool]) -> None:
    """How to ask a person on `surface`. Returns True to proceed."""
    _registry.confirmers[surface] = confirmer


def register_performer(scope: str, action: str,
                       performer: Callable[[Request], object]) -> None:
    """What actually does it. One per scope/action, registered by the layer that owns it."""
    if (scope, action) not in _ALLOWED:
        raise ValueError(f"not a lifecycle action: {action} the {scope}")
    _registry.performers[(scope, action)] = performer


def register_notifier(notifier: Callable[[Request], object]) -> None:
    """Told about a request that is going ahead. Nothing waits on this and nothing may
    veto through it - it is how a surface that did not ask says what is about to happen."""
    # Registering the same one twice would announce twice; a performer cannot make that
    # mistake because it is keyed.
    if notifier not in _registry.notifiers:
        _registry.notifiers.append(notifier)


def register_availability(scope: str, action: str, probe: Callable[[], str]) -> None:
    """Why a pair that is wired up cannot run right now: `probe` answers the catalog key of
    the reason, or "" when it can. Listed with the pair, which is not hidden for it."""
    if (scope, action) not in _ALLOWED:
        raise ValueError(f"not a lifecycle action: {action} the {scope}")
    _registry.availability[(scope, action)] = probe


_snapshot: ContextVar[dict[str, object] | None] = ContextVar("lifecycle_snapshot", default=None)


@contextmanager
def snapshot() -> Iterator[None]:
    """Probes asked inside it that read a shared state through `once` see it one time."""
    token = _snapshot.set({})
    try:
        yield
    finally:
        _snapshot.reset(token)


def once[T](key: str, read: Callable[[], T]) -> T:
    """`read()`, remembered under `key` for the length of the enclosing `snapshot`."""
    held = _snapshot.get()
    if held is None:
        return read()
    if key not in held:
        held[key] = read()
    return held[key]  # type: ignore[return-value]


def unavailable(scope: str, action: str) -> str:
    """The reason key this pair cannot run now, or "" when it can."""
    probe = _registry.availability.get((scope, action))
    return probe() if probe is not None else ""


def reset_for_tests() -> None:
    _registry.confirmers.clear()
    _registry.performers.clear()
    _registry.notifiers.clear()
    _registry.availability.clear()


def offered() -> tuple[tuple[str, str], ...]:
    """Every pair this build allows, in a stable order. What a surface may draw."""
    return tuple(pair for pair in _ORDER if pair in _ALLOWED)


def performable(scope: str, action: str) -> bool:
    """Whether anything on this build actually does it.

    The allowed set says what the vocabulary has; this says what is wired up. They come
    apart on a build with no window owner, where nothing performs the frontend actions -
    and a button that reports success while nothing happened is worse than one that is not
    offered.
    """
    return (scope, action) in _registry.performers


def describe(scope: str, action: str) -> str:
    """What the pair is called, in English, for a log line.

    Written out rather than built from the scope and the action: the template read
    "stop the vpinfe", which names an internal scope at somebody about to be asked
    whether they meant it. Already sentence-cased, because str.capitalize lowercases the
    rest and "Quit VPinFE" would come back as "Quit vpinfe".
    """
    return t_source(_key(scope, action)) if _known(scope, action) \
        else f"{action} the {scope}"


def label(scope: str, action: str) -> str:
    """What the pair is called on screen, in the language now set."""
    return t(_key(scope, action)) if _known(scope, action) \
        else f"{action} the {scope}"


# The pairs that take the page or the screen away, and the sentence a person is shown
# first. A notice carrying one has no way down, so only these may have one: every other
# pair leaves the surface standing and the notice would stay on it.
_ANNOUNCED = {
    (VPINFE, RESTART): "manager.notice.restarting",
    (VPINFE, STOP): "manager.notice.quitting",
    (SYSTEM, RESTART): "manager.notice.rebooting",
    (SYSTEM, STOP): "manager.notice.powering_off",
}


def announcement(scope: str, action: str) -> str:
    """What is happening, said as a statement, or "" for a pair that is not announced."""
    key = _ANNOUNCED.get((scope, action))
    return t(key) if key else ""


def _key(scope: str, action: str) -> str:
    return f"action.{scope}.{action}"


def _known(scope: str, action: str) -> bool:
    return (scope, action) in PAIRS


def needs_confirmation(request: Request, confirm_scopes: Iterable[str] | None) -> bool:
    """Whether the user asked to be checked on this. By scope, because "ask before
    anything touches the system" is what people mean and a key per pair is a grid
    nobody fills in."""
    wanted = {str(s).strip().lower() for s in (confirm_scopes or []) if str(s).strip()}
    return request.scope in wanted


def confirm(request: Request, confirm_scopes: Iterable[str] | None) -> bool:
    """Ask, if this wants asking and there is somebody to ask.

    An unanswerable origin proceeds: a SIGTERM that waits on a dialog is a process that
    will not die. A surface that should answer and cannot denies instead - the window
    went away, so the person is not there either.
    """
    if not needs_confirmation(request, confirm_scopes):
        return True
    if not request.origin.is_answerable:
        logger.info("No surface to confirm with (%s); proceeding with %s",
                    request.origin.surface, request.describe())
        return True

    confirmer = _registry.confirmers.get(request.origin.surface)
    if confirmer is None:
        logger.warning("Nothing can confirm on %s; proceeding with %s",
                       request.origin.surface, request.describe())
        return True
    try:
        return bool(confirmer(request))
    except Exception:
        # A confirmer that breaks must not take the action with it, in either direction.
        logger.exception("Confirming %s failed; not proceeding", request.describe())
        return False


def announce(request: Request) -> None:
    """Tell every other surface what is about to happen. Never blocks the action."""
    for notifier in list(_registry.notifiers):
        try:
            notifier(request)
        except Exception:
            logger.exception("A lifecycle notifier failed; continuing")


def request(scope: str, action: str, *, origin: Origin,
            confirm_scopes: Iterable[str] = (),
            reason: str = "") -> bool:
    """Confirm, announce, then do it. Returns whether it went ahead."""
    if (scope, action) not in _ALLOWED:
        raise ValueError(f"not a lifecycle action: {action} the {scope}")

    pending = Request(scope, action, origin, reason)
    if not confirm(pending, confirm_scopes):
        logger.info("%s was declined at %s", pending.describe(), origin.surface)
        return False

    performer = _registry.performers.get(pending.pair)
    if performer is None:
        logger.error("Nothing performs %s on this build", pending.describe())
        return False

    logger.info("%s, asked for by %s", pending.describe(), origin.surface)
    announce(pending)
    performer(pending)
    return True
