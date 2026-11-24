"""Asking first: a program asks to make one Protected change, and the Owner decides.

The program names the exact request - method, path and body - and waits. Approving it lets
that one request through, once, and only if it is repeated by the same token with the same
method, path and body. Nothing is stored for the program to replay: the request itself is
never held, only its description, and a restart forgets every ask.

Nothing here knows about HTTP. `httpapi/approvals.py` carries the routes and
`httpapi/auth.py` redeems an approval on the request that presents it.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

logger = logging.getLogger("vpinfe.common.security.approvals")

LAPSE_SECONDS = 60 * 60
# What the Owner is shown and what a repeated request is held to. A body past this is not
# something a person can read before agreeing to it.
MAX_BODY_CHARS = 4096
PER_TOKEN = 5
IN_ALL = 20

WAITING = "waiting"
APPROVED = "approved"
DECLINED = "declined"
USED = "used"
LAPSED = "lapsed"

METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

TOO_BIG = "too_big"
TOO_MANY = "too_many"
NOT_JSON = "not_json"


class AskError(Exception):
    """An ask refused. `reason` is one of the constants above."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class TooBigError(AskError):
    def __init__(self) -> None:
        super().__init__(TOO_BIG)


class TooManyError(AskError):
    def __init__(self) -> None:
        super().__init__(TOO_MANY)


@dataclass(frozen=True)
class Ask:
    id: str
    token_id: str
    token_name: str
    method: str
    path: str
    # The body as `canonical` writes it, or "" for a request that has none.
    body: str
    asked: float
    state: str = WAITING
    # When the Owner answered, or when the ask lapsed.
    decided: float = 0.0
    # The machine's screen was closed on this ask without an answer. It stays in Needs You.
    left_screen: bool = False


def canonical(body: Any) -> str:
    """One spelling of a JSON body, so the same request written with other spacing or key
    order is the same request."""
    if body is None:
        return ""
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def canonical_bytes(raw: bytes) -> str | None:
    """`canonical` of a request's body as it arrived, or None when it is not JSON."""
    if not raw.strip():
        return ""
    try:
        return canonical(json.loads(raw))
    except (ValueError, RecursionError):
        return None


class Approvals:
    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._lock = threading.RLock()
        self._asks: dict[str, Ask] = {}
        self._listeners: list[Callable[[], None]] = []

    def on_change(self, listener: Callable[[], None]) -> None:
        """Be told when an ask is made, answered, used or lapses by being looked at."""
        self._listeners.append(listener)

    def _tell(self) -> None:
        for listener in list(self._listeners):
            try:
                listener()
            except Exception:
                logger.exception("An approvals listener failed")

    def _sweep(self) -> bool:
        """Lapse what has run out and forget what has been over for an hour. True when
        something changed."""
        now = self._clock()
        changed = False
        for key, ask in list(self._asks.items()):
            if ask.state == WAITING and now - ask.asked > LAPSE_SECONDS:
                self._asks[key] = replace(ask, state=LAPSED, decided=ask.asked + LAPSE_SECONDS)
                changed = True
            elif ask.state == APPROVED and now - ask.decided > LAPSE_SECONDS:
                self._asks[key] = replace(ask, state=LAPSED, decided=ask.decided + LAPSE_SECONDS)
                changed = True
            elif ask.state != WAITING and ask.state != APPROVED \
                    and now - ask.decided > LAPSE_SECONDS:
                del self._asks[key]
        return changed

    def seconds_left(self, ask: Ask) -> int:
        """How long an ask can still be answered, or an approval used."""
        if ask.state not in (WAITING, APPROVED):
            return 0
        since = ask.asked if ask.state == WAITING else ask.decided
        return max(0, int(since + LAPSE_SECONDS - self._clock()))

    # --- the program's side ---------------------------------------------------------

    def ask(self, token_id: str, token_name: str, method: str, path: str,
            body: Any = None) -> Ask:
        """A token asks to make this request. Asking for the same thing again while it is
        waiting or approved is the same ask. Raises `AskError` when it may not."""
        text = canonical(body)
        if len(text) > MAX_BODY_CHARS:
            raise TooBigError
        method = method.upper()
        with self._lock:
            changed = self._sweep()
            live = [one for one in self._asks.values() if one.state in (WAITING, APPROVED)]
            same = next((one for one in live if one.token_id == token_id
                         and one.method == method and one.path == path
                         and one.body == text), None)
            if same is not None:
                if changed:
                    self._tell()
                return same
            if (sum(1 for one in live if one.token_id == token_id) >= PER_TOKEN
                    or len(live) >= IN_ALL):
                raise TooManyError
            made = Ask(id=uuid.uuid4().hex, token_id=token_id, token_name=token_name,
                       method=method, path=path, body=text, asked=self._clock())
            self._asks[made.id] = made
            logger.info("%s asks to %s %s", token_name, method, path)
        self._tell()
        return made

    def get(self, ask_id: str) -> Ask | None:
        with self._lock:
            changed = self._sweep()
            found = self._asks.get(ask_id)
        if changed:
            self._tell()
        return found

    def of_token(self, token_id: str) -> list[Ask]:
        with self._lock:
            changed = self._sweep()
            found = sorted((one for one in self._asks.values() if one.token_id == token_id),
                           key=lambda one: one.asked)
        if changed:
            self._tell()
        return found

    def redeem(self, ask_id: str, token_id: str, method: str, path: str,
               body: str) -> bool:
        """Spend an approval on the request that carries it: the same token, method, path
        and body. A request that does not match leaves the approval as it was."""
        with self._lock:
            changed = self._sweep()
            ask = self._asks.get(ask_id)
            spent = False
            if (ask is not None and ask.state == APPROVED and ask.token_id == token_id
                    and ask.method == method.upper() and ask.path == path
                    and ask.body == body):
                self._asks[ask_id] = replace(ask, state=USED, decided=self._clock())
                spent = True
        if spent or changed:
            self._tell()
        return bool(spent)

    # --- the Owner's side -----------------------------------------------------------

    def waiting(self) -> list[Ask]:
        """The asks to put in front of the Owner, oldest first."""
        with self._lock:
            changed = self._sweep()
            found = sorted((one for one in self._asks.values() if one.state == WAITING),
                           key=lambda one: one.asked)
        if changed:
            self._tell()
        return found

    def _answer(self, ask_id: str, state: str, allowed: tuple[str, ...],
                token_id: str = "") -> Ask | None:
        with self._lock:
            changed = self._sweep()
            ask = self._asks.get(ask_id)
            if ask is None or ask.state not in allowed or (token_id and ask.token_id != token_id):
                answered = None
            else:
                answered = self._asks[ask_id] = replace(ask, state=state,
                                                        decided=self._clock())
        if answered is not None or changed:
            self._tell()
        return answered

    def approve(self, ask_id: str) -> Ask | None:
        """The Owner said yes. None when it was already answered or has lapsed."""
        return self._answer(ask_id, APPROVED, (WAITING,))

    def decline(self, ask_id: str, *, token_id: str = "") -> Ask | None:
        """The Owner said no, or - with `token_id` - the program took its own ask back. An
        approval not yet used can be taken back the same way."""
        return self._answer(ask_id, DECLINED, (WAITING, APPROVED), token_id)

    def forget_token(self, token_id: str) -> None:
        """The token was revoked: nothing it asked for is left for the Owner to answer."""
        with self._lock:
            gone = [key for key, ask in self._asks.items() if ask.token_id == token_id]
            for key in gone:
                del self._asks[key]
        if gone:
            self._tell()

    def leave_screen(self, ask_id: str) -> None:
        """The machine's screen was closed without an answer. Needs You still holds it."""
        with self._lock:
            ask = self._asks.get(ask_id)
            if ask is None or ask.left_screen:
                return
            self._asks[ask_id] = replace(ask, left_screen=True)
        self._tell()

    def on_screen(self) -> Ask | None:
        """The oldest ask the machine's screen should be showing."""
        return next((one for one in self.waiting() if not one.left_screen), None)


_instance = Approvals()


def instance() -> Approvals:
    return _instance


def use(approvals: Approvals) -> None:
    """Swap the process's approvals. For tests."""
    global _instance
    _instance = approvals
