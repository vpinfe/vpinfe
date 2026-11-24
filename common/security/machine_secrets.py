"""Secrets this install makes for itself, kept where only VPinFE's own user can read them.

One JSON file, `paths.SECRETS_PATH`, mode 0600. Never `vpinfe.json`: a config write, a
backup restore and a hand edit all reach that file, and nothing here may be reachable by
any of them.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from common import atomic_write
from common.config import paths

logger = logging.getLogger("vpinfe.common.security.machine_secrets")

_lock = threading.RLock()


def write_private(path: Path, data: Any) -> None:
    """`data` as JSON at `path`, readable by this user alone, in one replacing step."""
    with atomic_write.staged_for(path) as staged:
        descriptor = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
    tighten(path)


def tighten(path: Path) -> None:
    """Take every right off a file but the owner's. A no-op where the platform has no modes."""
    if sys.platform == "win32":
        return
    try:
        os.chmod(path, 0o600)
    except OSError:
        logger.warning("Could not make %s private", path)


def read_private(path: Path) -> dict[str, Any]:
    """The JSON object at `path`, or {} where there is none or it cannot be read."""
    try:
        found = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


def _load() -> dict[str, Any]:
    """The secrets held. A file that is there and cannot be read is moved aside first, so
    minting a new secret never silently drops the others that were in it."""
    path = paths.SECRETS_PATH
    try:
        kept = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        kept = None
    if isinstance(kept, dict):
        tighten(path)
        return kept
    aside = path.with_name(path.name + ".unreadable")
    try:
        os.replace(path, aside)
        logger.error("%s could not be read; kept as %s", path, aside.name)
    except OSError:
        logger.error("%s could not be read", path)
    return {}


def read() -> dict[str, Any]:
    with _lock:
        return _load()


def get(name: str, make: Callable[[], Any] = lambda: secrets.token_urlsafe(32)) -> Any:
    """The named secret, made and kept on first ask so every later ask answers the same."""
    with _lock:
        kept = _load()
        if name not in kept:
            kept[name] = make()
            paths.ensure_config_dir()
            write_private(paths.SECRETS_PATH, kept)
        return kept[name]


def put(name: str, value: Any) -> None:
    with _lock:
        kept = _load()
        kept[name] = value
        paths.ensure_config_dir()
        write_private(paths.SECRETS_PATH, kept)


def change(name: str, edit: Callable[[Any], Any]) -> None:
    """Replace the named secret with `edit` of what is held (None where nothing is), the
    read and the write under one lock so two changes cannot lose each other."""
    with _lock:
        kept = _load()
        kept[name] = edit(kept.get(name))
        paths.ensure_config_dir()
        write_private(paths.SECRETS_PATH, kept)


def drop(name: str) -> None:
    with _lock:
        kept = _load()
        if kept.pop(name, None) is not None:
            write_private(paths.SECRETS_PATH, kept)


SESSION_SECRET = "session_secret"


def session_secret() -> str:
    """What signs the Manager's session cookie."""
    return str(get(SESSION_SECRET))
