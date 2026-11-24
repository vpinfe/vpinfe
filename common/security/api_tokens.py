"""API tokens: a secret a program holds to call this install's API without a browser.

Two kinds of access. **Full Control** can do everything an Owner can without a Confirm: change
the library, the players, the collections. **Play** can launch, stop, press buttons, capture
and rate. Neither can change what runs, hand out access, destroy backups or take the device
down, and neither is ever asked for a Confirm. A Full Control token may be given one more
right, **Can Shut Down and Restart**, which is off until someone switches it on.

A secret is shown once, when it is made. Only its hash is kept, in a file beside the secrets,
so a copy of the file calls nothing. Nothing here knows about HTTP: `httpapi/tokens.py`
carries it.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from common.config import paths
from common.security import machine_secrets

logger = logging.getLogger("vpinfe.common.security.api_tokens")

FULL = "full"
PLAY = "play"
ACCESS = (FULL, PLAY)

PREFIX = "vpf_"
_TOUCH_SECONDS = 60


@dataclass(frozen=True)
class Token:
    id: str
    name: str
    access: str
    can_power: bool
    created: float
    last_used: float


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


class Tokens:
    def __init__(self, path: Path | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self._path = path
        self._clock = clock
        self._lock = threading.RLock()
        self._rows: dict[str, Token] = {}
        self._loaded = False

    def _file(self) -> Path:
        return self._path or paths.TOKENS_PATH

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        for hashed, held in machine_secrets.read_private(self._file()).items():
            try:
                self._rows[hashed] = Token(
                    id=str(held["id"]), name=str(held["name"]), access=str(held["access"]),
                    can_power=bool(held.get("can_power", False)),
                    created=float(held["created"]), last_used=float(held["last_used"]))
            except (KeyError, TypeError, ValueError):
                continue

    def _save(self) -> None:
        held: dict[str, Any] = {
            hashed: {"id": token.id, "name": token.name, "access": token.access,
                     "can_power": token.can_power, "created": token.created,
                     "last_used": token.last_used}
            for hashed, token in self._rows.items()}
        try:
            machine_secrets.write_private(self._file(), held)
        except OSError:
            logger.warning("Could not save the tokens", exc_info=True)

    def make(self, name: str, access: str, *, can_power: bool = False) -> tuple[str, Token]:
        """A new token and its secret. The secret is kept nowhere and cannot be read again."""
        if access not in ACCESS:
            raise ValueError(access)
        with self._lock:
            self._load()
            secret = PREFIX + secrets.token_urlsafe(32)
            token = Token(id=uuid.uuid4().hex, name=name.strip()[:60], access=access,
                          can_power=bool(can_power and access == FULL),
                          created=self._clock(), last_used=0.0)
            self._rows[_hash(secret)] = token
            self._save()
        return secret, token

    def lookup(self, secret: str) -> Token | None:
        """The token this secret is, or None. Using it counts as use."""
        if not secret.startswith(PREFIX):
            return None
        with self._lock:
            self._load()
            hashed = _hash(secret)
            token = self._rows.get(hashed)
            if token is None:
                return None
            now = self._clock()
            if now - token.last_used >= _TOUCH_SECONDS:
                token = self._rows[hashed] = replace(token, last_used=now)
                self._save()
            return token

    def tokens(self) -> list[Token]:
        with self._lock:
            self._load()
            return sorted(self._rows.values(), key=lambda held: held.created, reverse=True)

    def set_power(self, token_id: str, can_power: bool) -> Token | None:
        with self._lock:
            self._load()
            for hashed, token in self._rows.items():
                if token.id == token_id:
                    if token.access != FULL:
                        return token
                    changed = self._rows[hashed] = replace(token, can_power=can_power)
                    self._save()
                    return changed
        return None

    def revoke(self, token_id: str) -> bool:
        with self._lock:
            self._load()
            for hashed, token in list(self._rows.items()):
                if token.id == token_id:
                    del self._rows[hashed]
                    self._save()
                    return True
        return False


_instance = Tokens()


def instance() -> Tokens:
    return _instance


def use(tokens: Tokens) -> None:
    """Swap the process's tokens. For tests."""
    global _instance
    _instance = tokens
