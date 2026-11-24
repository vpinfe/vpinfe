"""Data an extension contributes about a game, fetched by core and never by the browser.

A theme reads `entry.ext.<key>`. It does not know which extension filled it in, and no
extension gets a method of its own on the theme surface - the alternative, which is what
exists today for one vendor, needs a new call for every connector that follows.

Core makes the third-party call. A page that reached out itself would put somebody else's
endpoint in every window on the cabinet, fetch the same answer once per screen, and lose
the lot on a reload.

Held per process, so one fetch serves every window and survives a page reload. Nothing is
persisted: a rating is somebody else's current answer, and a stale one read off disk at
startup would be worse than none.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger("vpinfe.common.extensions.contributions")


@dataclass(frozen=True)
class Contributor:
    """One extension's answer about one game, and how to get it."""

    extension: str
    key: str
    fetch: Callable[[dict], object]


_lock = threading.RLock()
_contributors: dict[str, Contributor] = {}
# {(key, game_id): value}. A key that has been asked and answered nothing holds None, so
# a game with no rating is not asked again on every wheel step.
_answers: dict[tuple[str, str], object] = {}


def register(extension: str, key: str, fetch: Callable[[dict], object]) -> None:
    with _lock:
        held = _contributors.get(key)
        if held is not None and held.extension != extension:
            raise ValueError(f"{key!r} is already contributed by {held.extension}")
        _contributors[key] = Contributor(extension=extension, key=key, fetch=fetch)
    logger.info("%s contributes %r to every entry", extension, key)


def forget(extension: str) -> None:
    """Drop an extension's contributions, and everything it had answered.

    Its answers go with it: they came from somewhere that is no longer running, and
    leaving them on entries would show a rating nothing can refresh.
    """
    with _lock:
        gone = [key for key, one in _contributors.items() if one.extension == extension]
        for key in gone:
            _contributors.pop(key, None)
        for held in [one for one in _answers if one[0] in gone]:
            _answers.pop(held, None)


def keys() -> tuple[str, ...]:
    with _lock:
        return tuple(sorted(_contributors))


def held(game_id: str) -> dict:
    """What is already known about a game, for the payload a theme is handed.

    Only what has been answered. The slot is present and empty at library load, because a
    list of four hundred games cannot wait on four hundred calls to somebody else's
    server - and a theme written as `if (rating)` is correct throughout without ever
    knowing there is a waiting state.
    """
    wanted = str(game_id or "")
    with _lock:
        return {key: value for (key, held_id), value in _answers.items()
                if held_id == wanted and value is not None}


def refresh(descriptor: dict) -> dict:
    """Ask every contributor about one game, and answer with what is new.

    A contributor that raises costs its own key and nothing else - one connector's server
    being down is not a reason for the wheel to stop showing another's badge.
    """
    game_id = str(descriptor.get("game_id") or "")
    if not game_id:
        return {}
    with _lock:
        wanted = [one for one in _contributors.values()
                  if (one.key, game_id) not in _answers]

    found: dict = {}
    for one in wanted:
        try:
            value = one.fetch(dict(descriptor))
        except Exception:
            logger.exception("%s could not answer for %s", one.extension, game_id)
            value = None
        with _lock:
            _answers[(one.key, game_id)] = value
        if value is not None:
            found[one.key] = value
    return found


def forget_answers(extension: str, key: str) -> None:
    """Drop every answer held under one key, so each game is asked again. Only the
    extension contributing the key can drop it."""
    with _lock:
        held = _contributors.get(key)
        if held is None or held.extension != extension:
            return
        for one in [answer for answer in _answers if answer[0] == key]:
            _answers.pop(one, None)


def forget_game(game_id: str) -> None:
    """Drop what is known about one game, so the next ask goes out again.

    For a player who has just rated a table: the cumulative answer has changed and the
    held one is the answer from before they did.
    """
    wanted = str(game_id or "")
    with _lock:
        for held in [one for one in _answers if one[1] == wanted]:
            _answers.pop(held, None)


def clear() -> None:
    """Forget every contributor and every answer. For tests."""
    with _lock:
        _contributors.clear()
        _answers.clear()
