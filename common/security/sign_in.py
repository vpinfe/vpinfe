"""Signing a browser in: a code shown on the machine, and the sessions it opens.

A code is six digits, one live at a time, and only the machine's own screen, Manager or log
shows it. `request` makes one and says nothing about its digits; `reveal` is what Show does.
A session is a secret the browser keeps in a cookie. Only its hash is stored here, in a file
beside the secrets, so a copy of the file signs nobody in.

Nothing here knows about HTTP. `httpapi/sign_in.py` and `manager/security/sign_in_gate.py` carry it.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from common.config import paths
from common.i18n import t
from common.runtime import log_setup
from common.security import machine_secrets

logger = logging.getLogger("vpinfe.common.security.sign_in")

CODE_LENGTH = 6
CODE_SECONDS = 10 * 60
TRIES = 5
ASK_SPACING_SECONDS = 30
SPENT_BEFORE_DOUBLING = 3
CONFIRM_SECONDS = 30 * 60
CONFIRM_ASK_SECONDS = 5
PAIR_SPACING_LIMIT_SECONDS = 60 * 60
SESSION_DAYS = 90
SESSION_SECONDS = SESSION_DAYS * 24 * 60 * 60
# Writing the file on every request would put a disk write behind each page load, and a
# lookup runs on the event loop, so the write it does make is not waited for.
_TOUCH_SECONDS = 60

OWNER = "owner"
REMOTE = "remote"

WAITING = "waiting"
SHOWN = "shown"

CONFIRM = "confirm"
PAIR = "pair"


class SignInError(Exception):
    """A sign-in step refused. `reason` is one of the constants below, for the caller to word."""

    def __init__(self, reason: str, *, wait: int = 0) -> None:
        super().__init__(reason)
        self.reason = reason
        self.wait = wait


IGNORED = "ignored"
TOO_SOON = "too_soon"
NO_CODE = "no_code"
WRONG = "wrong"
USED_UP = "used_up"
EXPIRED = "expired"
# The authenticator's: none set up, a step already spent, and no code shown on screen.
NOT_SET = "not_set"
SPENT = "spent"
CODES_OFF = "codes_off"


@dataclass
class Request:
    """A browser asking for a code, as the machine sees it."""

    id: str
    address: str
    asked: float
    # What tells two browsers at one address apart: a secret only the asking browser holds.
    browser: str = field(default="", repr=False)
    state: str = WAITING
    code: str = ""
    tries: int = 0
    revealed: float = 0.0
    # What the asking browser calls itself ("Safari on iPad") and the name its address
    # answers to; both only help the person reading the screen tell who is asking.
    agent: str = ""
    place: str = ""


@dataclass(frozen=True)
class Session:
    id: str
    kind: str
    address: str
    agent: str
    created: float
    last_used: float
    # The Remote Code a phone came in with. A new code ends every session that carries the old.
    via: str = ""


@dataclass
class Confirm:
    """A Confirm Code asked for by a signed-in browser, shown at once on this device."""

    id: str
    session_id: str
    address: str
    asked: float
    code: str
    tries: int = 0
    purpose: str = CONFIRM
    agent: str = ""
    place: str = ""
    # The asking install's own name, for a pairing code.
    name: str = ""


@dataclass
class _Row:
    hashed: str
    session: Session = field(repr=False)


_BROWSERS = (("Edg/", "Edge"), ("EdgiOS/", "Edge"), ("OPR/", "Opera"), ("FxiOS/", "Firefox"),
             ("Firefox/", "Firefox"), ("CriOS/", "Chrome"), ("Chrome/", "Chrome"),
             ("Safari/", "Safari"))
_DEVICES = (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"),
            ("Macintosh", "Mac"), ("Windows", "Windows"), ("CrOS", "Chromebook"),
            ("Linux", "Linux"))


def describe_agent(user_agent: str) -> str:
    """"Safari on iPhone" for the list of signed-in browsers: enough to tell two apart, and
    the only part of a User-Agent worth keeping on disk."""
    browser = next((name for mark, name in _BROWSERS if mark in user_agent), "")
    device = next((name for mark, name in _DEVICES if mark in user_agent), "")
    return t("signin.agent", browser=browser, device=device) if browser and device \
        else browser or device


NAME_LENGTH = 60


def _plain(name: str) -> str:
    """A name another install sent, fit to draw: no control characters, bounded."""
    return "".join(ch for ch in name if ch.isprintable()).strip()[:NAME_LENGTH]


def _same(held: str, offered: str) -> bool:
    return hmac.compare_digest(held.encode(), offered.encode())


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class SignIn:
    """The codes asked for and the sessions given, for one install."""

    def __init__(self, path: Path | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self._path = path
        self._clock = clock
        self._lock = threading.RLock()
        self._request: Request | None = None
        self._ignored: set[str] = set()
        self._last_ask = 0.0
        self._spent_in_a_row = 0
        self._rows: dict[str, _Row] = {}
        self._confirms: dict[str, Confirm] = {}
        self._confirmed: dict[str, float] = {}
        self._pair: Confirm | None = None
        self._pair_last = 0.0
        self._pair_spent = 0
        self._loaded = False
        self._saving: threading.Thread | None = None
        self._write_lock = threading.Lock()
        self._version = 0
        self._written = 0
        self._listeners: list[Callable[[Request | None], None]] = []
        # Whether anything on this device can draw a code. Until the process says what it has
        # open, a screen is assumed, so a request waits for Show.
        self.screen: Callable[[], bool] = lambda: True

    # --- the file ---------------------------------------------------------------------

    def _file(self) -> Path:
        return self._path or paths.SESSIONS_PATH

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        for hashed, held in machine_secrets.read_private(self._file()).items():
            try:
                session = Session(id=str(held["id"]), kind=str(held["kind"]),
                                  address=str(held["address"]), agent=str(held.get("agent", "")),
                                  created=float(held["created"]),
                                  last_used=float(held["last_used"]),
                                  via=str(held.get("via", "")))
            except (KeyError, TypeError, ValueError):
                continue
            self._rows[hashed] = _Row(hashed, session)

    def _snapshot(self) -> tuple[int, dict[str, Any]]:
        self._version += 1
        return self._version, {
            row.hashed: {"id": row.session.id, "kind": row.session.kind,
                         "address": row.session.address, "agent": row.session.agent,
                         "created": row.session.created, "last_used": row.session.last_used,
                         "via": row.session.via}
            for row in self._rows.values()}

    def _write(self, version: int, held: dict[str, Any]) -> None:
        """Write a snapshot unless a newer one is already on disk."""
        with self._write_lock:
            if version < self._written:
                return
            try:
                machine_secrets.write_private(self._file(), held)
                self._written = version
            except OSError:
                logger.warning("Could not save the signed-in browsers", exc_info=True)

    def _save(self) -> None:
        self._write(*self._snapshot())

    def _save_in_background(self) -> None:
        """Save from a thread, for a caller on the event loop: a save waits on the disk, and
        the lock is not held while it does. Saves asked for while one is waiting are the one
        save."""
        if self._saving is not None:
            return

        def run() -> None:
            with self._lock:
                version, held = self._snapshot()
            try:
                self._write(version, held)
            finally:
                self._saving = None

        self._saving = threading.Thread(target=run, daemon=True, name="sign-in-save")
        self._saving.start()

    def wait_for_saves(self) -> None:
        """Return once a save asked for from a lookup has finished. For a test, so it does
        not leave one writing into a folder that is about to go."""
        waiting = self._saving
        if waiting is not None:
            waiting.join()

    def _forget_stale(self) -> None:
        cutoff = self._clock() - SESSION_SECONDS
        stale = [key for key, row in self._rows.items() if row.session.last_used < cutoff]
        for key in stale:
            del self._rows[key]
        if stale:
            self._save()

    # --- asking for a code ------------------------------------------------------------

    def on_request(self, listener: Callable[[Request | None], None]) -> None:
        """Be told when the live request changes: made, revealed, ignored, spent or expired."""
        self._listeners.append(listener)

    def _tell(self) -> None:
        with self._lock:
            current = replace(self._request) if self._request is not None else None
        for listener in list(self._listeners):
            try:
                listener(current)
            except Exception:
                logger.exception("A sign-in listener failed")

    def _spacing(self) -> int:
        doublings = max(0, self._spent_in_a_row - SPENT_BEFORE_DOUBLING + 1)
        return ASK_SPACING_SECONDS * (2 ** doublings)

    def next_ask_in(self) -> float:
        """Seconds until a different address may ask. Zero when it may now."""
        with self._lock:
            if not self._last_ask:
                return 0.0
            return max(0.0, self._last_ask + self._spacing() - self._clock())

    def ask(self, address: str, browser: str = "", *, agent: str = "",
            place: str = "") -> Request:
        """A browser at `address` wants a code. Raises `SignInError` when it may not have one.

        `browser` is that browser's own secret. Another browser at the same address is
        another asker, so it waits its turn rather than sharing the request."""
        with self._lock:
            if address in self._ignored:
                raise SignInError(IGNORED)
            now = self._clock()
            live = self._live()
            wait = int(self._last_ask + self._spacing() - now)
            same = live is not None and live.address == address and _same(live.browser, browser)
            fresh = True
            if live is not None and same and wait > 0:
                made, fresh = live, False
            else:
                if live is not None and not same and live.state == SHOWN:
                    raise SignInError(TOO_SOON, wait=self.seconds_left(live))
                if wait > 0 and self._last_ask:
                    raise SignInError(TOO_SOON, wait=wait)
                self._last_ask = now
                made = self._request = Request(id=uuid.uuid4().hex, address=address, asked=now,
                                               browser=browser, agent=agent,
                                               place=place or address)
                logger.warning("%s wants to sign in", address)
        if fresh:
            self._tell()
        if made.state == WAITING and not self.screen():
            return self.reveal(made.id) or made
        return made

    def reveal_if_unseen(self) -> None:
        """Show a request left waiting once nothing is left that could press Show."""
        request = self.pending()
        if request is not None and request.state == WAITING and not self.screen():
            self.reveal(request.id)

    def _live(self) -> Request | None:
        request = self._request
        if request is None:
            return None
        if self._clock() - request.asked > CODE_SECONDS:
            self._request = None
            return None
        return request

    def seconds_left(self, request: Request | Confirm) -> int:
        """How long `request`'s code stays good, by the clock the code is judged by."""
        return max(0, int(request.asked + CODE_SECONDS - self._clock()))

    def pending(self) -> Request | None:
        """The request the machine should be showing, or None."""
        with self._lock:
            return self._live()

    # --- the machine's side -----------------------------------------------------------

    def reveal(self, request_id: str) -> Request | None:
        """Show: make the digits and keep them for this request. None if it has gone."""
        with self._lock:
            request = self._live()
            if request is None or request.id != request_id:
                return None
            if request.state == WAITING:
                request.code = "".join(secrets.choice("0123456789") for _ in range(CODE_LENGTH))
                request.state = SHOWN
                request.revealed = self._clock()
                logger.warning("Sign-in code for %s: %s", request.address, request.code,
                               extra={log_setup.TERMINAL_ONLY: True})
        self._tell()
        return request

    def is_ignored(self, address: str) -> bool:
        with self._lock:
            return address in self._ignored

    def ignore(self, request_id: str) -> None:
        """Ignore: refuse this address's requests until restart."""
        with self._lock:
            request = self._live()
            if request is None or request.id != request_id:
                return
            self._ignored.add(request.address)
            self._request = None
        self._tell()

    def dismiss(self, request_id: str) -> None:
        with self._lock:
            request = self._request
            if request is not None and request.id == request_id:
                self._request = None
        self._tell()

    def reveal_now(self, address: str) -> Request:
        """A code for a browser that is on the machine itself: no Show step, since whoever
        can see this is already in front of it."""
        request = self.ask(address)
        revealed = self.reveal(request.id)
        assert revealed is not None
        return revealed

    # --- the browser's side -----------------------------------------------------------

    def verify(self, request_id: str, code: str, *, address: str, browser: str = "",
               kind: str = OWNER, agent: str = "") -> tuple[str, Session]:
        """Spend a try on `code`. Returns the session's secret and the session on success."""
        spent = False
        with self._lock:
            request = self._live()
            if request is None or request.id != request_id or request.state != SHOWN:
                raise SignInError(NO_CODE)
            if request.address != address or not _same(request.browser, browser):
                raise SignInError(NO_CODE)
            request.tries += 1
            right = hmac.compare_digest(request.code.encode(), code.strip().encode())
            if right:
                # Opened before the request is cleared: a page that finds no request waiting
                # reads who is signed in, and has to find the new browser there.
                opened = self.open(kind=kind, address=address, agent=agent)
                self._request = None
                self._spent_in_a_row = 0
            elif request.tries >= TRIES:
                self._request = None
                self._spent_in_a_row += 1
                spent = True
        if right:
            self._tell()
            return opened
        if spent:
            self._tell()
            raise SignInError(USED_UP)
        raise SignInError(WRONG)

    # --- sessions ---------------------------------------------------------------------

    def open(self, *, kind: str, address: str, agent: str = "",
             via: str = "") -> tuple[str, Session]:
        """A new session. An Owner who signs in has just proved presence, so it holds a Confirm."""
        with self._lock:
            self._load()
            self._forget_stale()
            token = secrets.token_urlsafe(32)
            now = self._clock()
            session = Session(id=uuid.uuid4().hex, kind=kind, address=address,
                              agent=agent[:200], created=now, last_used=now, via=via)
            hashed = _hash(token)
            self._rows[hashed] = _Row(hashed, session)
            if kind == OWNER:
                self._confirmed[session.id] = now
            self._save()
        return token, session

    def lookup(self, token: str) -> Session | None:
        """The session this secret opens, or None. Using it counts as use."""
        if not token:
            return None
        with self._lock:
            self._load()
            hashed = _hash(token)
            row = self._rows.get(hashed)
            if row is None:
                return None
            now = self._clock()
            if row.session.last_used < now - SESSION_SECONDS:
                del self._rows[hashed]
                self._save()
                return None
            if now - row.session.last_used >= _TOUCH_SECONDS:
                row.session = replace(row.session, last_used=now)
                self._save_in_background()
            return row.session

    def sessions(self) -> list[Session]:
        with self._lock:
            self._load()
            self._forget_stale()
            return sorted((row.session for row in self._rows.values()),
                          key=lambda s: s.last_used, reverse=True)

    def session(self, session_id: str) -> Session | None:
        """The session with this id if it is still signed in. Does not count as use."""
        with self._lock:
            self._load()
            for row in self._rows.values():
                if row.session.id == session_id:
                    return row.session
        return None

    def sign_out(self, session_id: str) -> bool:
        with self._lock:
            self._load()
            for key, row in list(self._rows.items()):
                if row.session.id == session_id:
                    del self._rows[key]
                    self._confirmed.pop(session_id, None)
                    self._confirms.pop(session_id, None)
                    self._save()
                    return True
        return False

    def sign_out_via(self, via: str) -> int:
        """End every session that came in with this Remote Code."""
        with self._lock:
            self._load()
            gone = [key for key, row in self._rows.items() if row.session.via == via]
            for key in gone:
                self._confirmed.pop(self._rows[key].session.id, None)
                del self._rows[key]
            if gone:
                self._save()
            return len(gone)

    # --- the Confirm ------------------------------------------------------------------

    def confirm_ask(self, session_id: str, address: str, place: str = "") -> Confirm:
        """A signed-in Owner wants a Confirm. The six digits are drawn on this device's screen
        at once, because whoever can see them is already in front of it. Asking again replaces
        the code, but not faster than every few seconds."""
        with self._lock:
            session = self.session(session_id)
            if session is None or session.kind != OWNER:
                raise SignInError(NO_CODE)
            now = self._clock()
            held = self._confirms.get(session_id)
            if held is not None and now - held.asked < CONFIRM_ASK_SECONDS:
                raise SignInError(TOO_SOON, wait=int(CONFIRM_ASK_SECONDS - (now - held.asked)) + 1)
            code = "".join(secrets.choice("0123456789") for _ in range(CODE_LENGTH))
            made = Confirm(id=uuid.uuid4().hex, session_id=session_id, address=address,
                           asked=now, code=code, agent=session.agent, place=place or address)
            self._confirms[session_id] = made
            logger.warning("%s asked for a Confirm Code", address)
            logger.warning("Confirm code for %s: %s", address, code,
                           extra={log_setup.TERMINAL_ONLY: True})
        self._tell()
        return made

    def confirm_verify(self, session_id: str, code: str) -> None:
        """Spend a try on the Confirm Code. On success the session holds a Confirm."""
        spent = False
        with self._lock:
            held = self._confirms.get(session_id)
            if held is None or self._clock() - held.asked > CODE_SECONDS:
                self._confirms.pop(session_id, None)
                raise SignInError(NO_CODE)
            held.tries += 1
            right = hmac.compare_digest(held.code.encode(), code.strip().encode())
            if right:
                self._confirms.pop(session_id, None)
                self._confirmed[session_id] = self._clock()
            elif held.tries >= TRIES:
                self._confirms.pop(session_id, None)
                spent = True
        self._tell()
        if right:
            return
        raise SignInError(USED_UP if spent else WRONG)

    def confirm_grant(self, session_id: str) -> None:
        """A Confirm from another way of proving presence: an authenticator or a secure link."""
        with self._lock:
            self._confirmed[session_id] = self._clock()

    def is_confirmed(self, session_id: str, *, use: bool = False) -> bool:
        """Whether this session holds a Confirm. `use` extends it, since a Confirm lasts
        while the person is still using it."""
        with self._lock:
            when = self._confirmed.get(session_id, 0.0)
            now = self._clock()
            if now - when > CONFIRM_SECONDS:
                self._confirmed.pop(session_id, None)
                return False
            if use:
                self._confirmed[session_id] = now
            return True

    def confirm_left(self, session_id: str) -> int:
        """Seconds this session's Confirm has left, 0 when it holds none."""
        with self._lock:
            when = self._confirmed.get(session_id, 0.0)
            return max(0, int(when + CONFIRM_SECONDS - self._clock())) if when else 0

    def confirms(self) -> list[Confirm]:
        """The codes this device should be showing besides a sign-in: Confirm Codes and a
        pairing code."""
        with self._lock:
            now = self._clock()
            for key in [key for key, held in self._confirms.items()
                        if now - held.asked > CODE_SECONDS]:
                del self._confirms[key]
            if self._pair is not None and now - self._pair.asked > CODE_SECONDS:
                self._pair = None
            found = list(self._confirms.values())
            if self._pair is not None:
                found.append(self._pair)
            return found

    # --- pairing ----------------------------------------------------------------------

    def pair_ask(self, address: str, name: str = "", place: str = "") -> Confirm:
        """Another install wants to pair. The digits are drawn here at once, for whoever is
        pairing it to read off this device's screen."""
        with self._lock:
            now = self._clock()
            doublings = max(0, self._pair_spent - SPENT_BEFORE_DOUBLING + 1)
            spacing = min(CONFIRM_ASK_SECONDS * 2 ** doublings, PAIR_SPACING_LIMIT_SECONDS)
            if self._pair_last and now - self._pair_last < spacing:
                raise SignInError(TOO_SOON, wait=int(spacing - (now - self._pair_last)) + 1)
            self._pair_last = now
            code = "".join(secrets.choice("0123456789") for _ in range(CODE_LENGTH))
            self._pair = Confirm(id=uuid.uuid4().hex, session_id="", address=address,
                                 asked=now, code=code, purpose=PAIR, place=place or address,
                                 name=_plain(name))
            made = self._pair
            logger.warning("%s wants to pair", address)
            logger.warning("Pairing code for %s: %s", address, code,
                           extra={log_setup.TERMINAL_ONLY: True})
        self._tell()
        return made

    def pair_verify(self, request_id: str, code: str) -> None:
        """Spend a try on the pairing code. Raises `SignInError` unless it was right."""
        spent = False
        with self._lock:
            held = self._pair
            if held is None or held.id != request_id or self._clock() - held.asked > CODE_SECONDS:
                raise SignInError(NO_CODE)
            held.tries += 1
            right = hmac.compare_digest(held.code.encode(), code.strip().encode())
            if right or held.tries >= TRIES:
                self._pair = None
                spent = not right
                self._pair_spent = self._pair_spent + 1 if spent else 0
        self._tell()
        if not right:
            raise SignInError(USED_UP if spent else WRONG)

    def pair_dismiss(self) -> None:
        with self._lock:
            self._pair = None
        self._tell()

    def sign_out_token(self, token: str) -> None:
        with self._lock:
            self._load()
            gone = self._rows.pop(_hash(token), None)
            if gone is not None:
                self._confirmed.pop(gone.session.id, None)
                self._confirms.pop(gone.session.id, None)
                self._save()

    def sign_out_everywhere(self, *, kind: str | None = None) -> int:
        with self._lock:
            self._load()
            gone = [key for key, row in self._rows.items()
                    if kind is None or row.session.kind == kind]
            for key in gone:
                self._confirmed.pop(self._rows[key].session.id, None)
                del self._rows[key]
            if gone:
                self._save()
            return len(gone)


_instance = SignIn()


def instance() -> SignIn:
    return _instance


def use(sign_in: SignIn) -> None:
    """Swap the process's sign-in. For tests."""
    global _instance
    _instance = sign_in
