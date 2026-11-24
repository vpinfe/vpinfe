"""What this device's frontend is showing: whether it is up, the collection, the game on
the wheel, and a recording shown in place for a decision.

Every change is announced as `frontend.state_changed`. The frontend reports and this
only holds what it last said, so a request to show something is answered by the next
report rather than by the call that asked.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, replace

from common import events
from common.i18n import t
from common.service_errors import BlockedError

_lock = threading.Lock()

BEFORE = "before"
AFTER = "after"


@dataclass(frozen=True)
class Preview:
    """A recording waiting for a decision, on show: `showing` is `before`, the file
    serving its slot now, or `after`, the recording."""

    proposal: str
    showing: str
    kind: str
    table_id: str
    game_id: str
    game_name: str

    def as_dict(self) -> dict:
        return {"proposal": self.proposal, "showing": self.showing, "kind": self.kind,
                "table_id": self.table_id,
                "game": {"id": self.game_id, "name": self.game_name}}


@dataclass(frozen=True)
class FrontendState:
    running: bool = False
    collection: str = ""
    game_id: str = ""
    game_name: str = ""
    preview: Preview | None = None

    def as_dict(self) -> dict:
        game = ({"id": self.game_id, "name": self.game_name}
                if self.game_id or self.game_name else None)
        return {"running": self.running, "collection": self.collection, "game": game,
                "preview": self.preview.as_dict() if self.preview else None}


_state = FrontendState()
_show: Callable[[str], None] | None = None
_move_to: Callable[[str], None] | None = None
_present: Callable[[str | None, str], None] | None = None
_gone: Callable[[str], None] | None = None


def current() -> FrontendState:
    with _lock:
        return _state


def _change(make: Callable[[FrontendState], FrontendState]) -> FrontendState:
    """Swap the state and announce it, if it changed. The event goes out after the lock
    is released, because a handler may read the state back."""
    global _state
    with _lock:
        new_state = make(_state)
        if new_state == _state:
            return _state
        _state = new_state
    events.emit(events.FRONTEND_STATE_CHANGED, state=new_state.as_dict())
    return new_state


def started(collection: str) -> FrontendState:
    """A window is up. Leaves the state alone when one already was."""
    return _change(lambda now: now if now.running else FrontendState(True, collection))


def showing(collection: str, game_id: str = "", game_name: str = "") -> FrontendState:
    """The wheel stopped on this game in this collection, or on nothing."""
    return _change(lambda now: FrontendState(True, collection, game_id, game_name,
                                             now.preview))


def previewing(preview: Preview | None) -> FrontendState:
    """The windows show this recording, or none."""
    return _change(lambda now: replace(now, preview=preview))


def stopped() -> FrontendState:
    return _change(lambda _now: FrontendState())


def register_driver(show: Callable[[str], None], move_to: Callable[[str], None]) -> None:
    """How a request reaches the windows. `show` raises NotFoundError for a collection
    that does not exist, `move_to` for a game the collection on screen does not hold."""
    global _show, _move_to
    _show, _move_to = show, move_to


def register_preview(present: Callable[[str | None, str], None],
                     gone: Callable[[str], None]) -> None:
    """How a recording reaches the windows. `present` shows a proposal, raising
    NotFoundError where there is none by that id, or with None ends what is on show;
    `gone` hears that a proposal was decided elsewhere."""
    global _present, _gone
    _present, _gone = present, gone


def _driver() -> tuple[Callable[[str], None], Callable[[str], None]]:
    if _show is None or _move_to is None or not current().running:
        raise BlockedError(t("error.frontend.not_running"))
    return _show, _move_to


def show(collection: str) -> None:
    """Ask the windows to show a collection, "" being the whole library."""
    _driver()[0](collection)


def move_to(game_id: str) -> None:
    """Ask the wheel to move to a game in the collection on screen."""
    _driver()[1](game_id)


def preview(proposal_id: str, showing: str = AFTER) -> None:
    """Ask the windows to show a proposal in place."""
    if _present is None or not current().running:
        raise BlockedError(t("error.frontend.not_running"))
    _present(proposal_id, showing)


def end_preview() -> None:
    """Ask the windows to stop showing a proposal. Nothing on show is not a failure."""
    if _present is not None and current().preview is not None:
        _present(None, AFTER)


def decided(proposal_id: str) -> None:
    """A proposal was used or thrown away; the windows move past it if it is on show."""
    shown = current().preview
    if _gone is not None and shown is not None and shown.proposal == proposal_id:
        _gone(proposal_id)


def reset_for_tests() -> None:
    global _state, _show, _move_to, _present, _gone
    with _lock:
        _state = FrontendState()
    _show = _move_to = None
    _present = _gone = None
