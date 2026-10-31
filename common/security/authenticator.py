"""An authenticator app's six digits: a second way to sign in and to hold a Confirm.

The standard time-based code (RFC 6238, SHA-1, 30 seconds, six digits), so any
authenticator app reads the enrolment. One step either side of now is accepted, and a step
is spent once used. Nothing needs an authenticator; it is set up on Security and removed
there.

The secret, the last step spent and the on-screen-code switch live in one
record in the secrets file, which only this user can read. Nothing here knows about HTTP.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import logging
import secrets
import struct
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import quote

from common.security import machine_secrets, sign_in

logger = logging.getLogger("vpinfe.common.security.authenticator")

KEY = "authenticator"
ISSUER = "VPinFE"
DIGITS = 6
STEP_SECONDS = 30
STEPS_EITHER_SIDE = 1
SECRET_BYTES = 20
LOCK_SECONDS = 30
LOCK_LIMIT_SECONDS = 60 * 60
PENDING_SECONDS = 10 * 60
# Addresses remembered for guessing limits. Past this, an address nobody has made room for
# shares one guard, so a stranger cannot grow the table.
GUARDS_KEPT = 256
SHARED = "?"
# Wrong codes from every address together before all of them wait.
ALL_WRONG = 4 * sign_in.TRIES


@dataclass
class _Guard:
    """What one address has got wrong, and until when it must wait."""

    wrong: int = 0
    locks: int = 0
    until: float = 0.0


@dataclass(frozen=True)
class Enrolment:
    """What the app needs to learn the secret: the text to type and the address a QR carries."""

    secret: str
    uri: str


def _code(secret: bytes, step: int) -> str:
    digest = hmac.new(secret, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(number % 10 ** DIGITS).zfill(DIGITS)


def _decoded(secret: str) -> bytes:
    try:
        return base64.b32decode(secret.encode("ascii"))
    except (binascii.Error, ValueError):
        raise sign_in.SignInError(sign_in.NOT_SET) from None


def uri_for(secret: str, account: str) -> str:
    """The address an authenticator app reads from a QR."""
    label = quote(f"{ISSUER}:{account}", safe="")
    return (f"otpauth://totp/{label}?secret={secret}&issuer={quote(ISSUER)}"
            f"&algorithm=SHA1&digits={DIGITS}&period={STEP_SECONDS}")


class Authenticator:
    """The one authenticator an install may have, and the limits on guessing its digits."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._lock = threading.RLock()
        self._pending: str = ""
        self._pending_until = 0.0
        self._guards: dict[str, _Guard] = {}
        self._everyone = _Guard()

    # --- the record -------------------------------------------------------------------

    @staticmethod
    def _record() -> dict:
        held = machine_secrets.read().get(KEY)
        return dict(held) if isinstance(held, dict) else {}

    @staticmethod
    def _keep(record: dict) -> None:
        machine_secrets.put(KEY, record)

    def enabled(self) -> bool:
        return bool(self._record().get("secret"))

    def screen_codes(self) -> bool:
        """Whether this device shows a sign-in code to a browser that asks. Always, until an
        authenticator is set up, since there is no other way in."""
        record = self._record()
        return not record.get("secret") or bool(record.get("screen_codes", True))

    def set_screen_codes(self, on: bool) -> None:
        with self._lock:
            record = self._record()
            if not record.get("secret"):
                raise sign_in.SignInError(sign_in.NOT_SET)
            record["screen_codes"] = bool(on)
            self._keep(record)

    # --- setting up -------------------------------------------------------------------

    def begin(self, account: str) -> Enrolment:
        """A new secret for the app to learn. The one in force stays until `activate`
        accepts a code from the new one, so a failed attempt loses nothing."""
        with self._lock:
            self._pending = base64.b32encode(secrets.token_bytes(SECRET_BYTES)).decode("ascii")
            self._pending_until = self._clock() + PENDING_SECONDS
            return Enrolment(self._pending, uri_for(self._pending, account))

    def activate(self, code: str, address: str = "") -> None:
        """Take `code` from the app that learned the pending secret, and make it the one in
        force. Raises `SignInError`."""
        with self._lock:
            if not self._pending or self._clock() >= self._pending_until:
                self._pending = ""
                raise sign_in.SignInError(sign_in.NO_CODE)
            step = self._matched(self._pending, code, address, after=-1)
            record = self._record()
            record.update(secret=self._pending, last=step)
            record.setdefault("screen_codes", True)
            self._keep(record)
            self._pending = ""

    def remove(self) -> None:
        with self._lock:
            record = self._record()
            for name in ("secret", "last", "screen_codes"):
                record.pop(name, None)
            self._keep(record)
            self._pending = ""

    # --- using it ---------------------------------------------------------------------

    def verify(self, code: str, address: str = "") -> None:
        """Spend `code` for the browser at `address`. Raises `SignInError`: not set up, wrong,
        locked, or already spent."""
        with self._lock:
            record = self._record()
            secret = str(record.get("secret") or "")
            if not secret:
                raise sign_in.SignInError(sign_in.NOT_SET)
            step = self._matched(secret, code, address, after=int(record.get("last") or 0))
            record["last"] = step
            self._keep(record)

    def _guard(self, address: str) -> _Guard:
        guard = self._guards.get(address)
        if guard is None:
            if len(self._guards) >= GUARDS_KEPT:
                now = self._clock()
                idle = [a for a, g in self._guards.items() if g.until <= now and a != SHARED]
                for old in idle[:GUARDS_KEPT // 2]:
                    del self._guards[old]
            if len(self._guards) >= GUARDS_KEPT:
                address = SHARED
            guard = self._guards.setdefault(address, _Guard())
        return guard

    def _missed(self, guard: _Guard, limit: int, who: str, now: float) -> None:
        guard.wrong += 1
        if guard.wrong >= limit:
            guard.wrong = 0
            wait = min(LOCK_SECONDS * 2 ** guard.locks, LOCK_LIMIT_SECONDS)
            guard.locks += 1
            guard.until = now + wait
            logger.warning("Authenticator codes from %s locked for %d seconds", who, wait)

    def _matched(self, secret: str, code: str, address: str, *, after: int) -> int:
        """The step `code` is for, when it is right for a step later than `after`. Wrong
        guesses count against `address`, so one stranger cannot lock the Owner out, and against
        every address together, so many addresses do not each get their own tries."""
        now = self._clock()
        guard = self._guard(address)
        for held in (guard, self._everyone):
            if now < held.until:
                raise sign_in.SignInError(sign_in.TOO_SOON, wait=int(held.until - now) + 1)
        shown = "".join(code.split())
        current = int(now // STEP_SECONDS)
        held_secret = _decoded(secret)
        found: list[int] = []
        # Every step is compared, so how long this takes does not say which one was close.
        for step in range(current - STEPS_EITHER_SIDE, current + STEPS_EITHER_SIDE + 1):
            if hmac.compare_digest(_code(held_secret, step).encode(), shown.encode()):
                found.append(step)
        if not found:
            self._missed(guard, sign_in.TRIES, address, now)
            self._missed(self._everyone, ALL_WRONG, "all addresses", now)
            raise sign_in.SignInError(sign_in.WRONG)
        step = max(found)
        if step <= after:
            raise sign_in.SignInError(sign_in.SPENT)
        for held in (guard, self._everyone):
            held.wrong = 0
            held.locks = 0
        return step


_instance = Authenticator()


def instance() -> Authenticator:
    return _instance


def use(authenticator: Authenticator) -> None:
    """Swap the process's authenticator. For tests."""
    global _instance
    _instance = authenticator
