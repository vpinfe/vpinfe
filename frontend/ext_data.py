"""Fetching what extensions contribute about the game the player moved to.

Core makes the call and the windows are told the answer. The alternative is what one
vendor has today: every window on a cabinet calling somebody else's server for the same
game, three times, and losing the lot on a reload.

Off the wheel's thread. A connector's server being slow must not be something the player
feels while turning the wheel, so the fetch runs behind the selection rather than in it.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable, Sequence
from typing import Any

from common import events
from common.extensions import contributions, descriptors
from common.games.game import GameRecord

logger = logging.getLogger("vpinfe.frontend.ext_data")

_registered = False
_broadcast = None


def _fetch_and_tell(games: Sequence[GameRecord]) -> None:
    """The selected game first, then the ones either side of it.

    In that order and not together: the player is looking at the first one, and a
    neighbour fetched ahead of it would put somebody else's server between them and what
    is on screen.
    """
    for index, game in enumerate(games):
        descriptor = descriptors.game(game)
        found = contributions.refresh(descriptor)
        # A neighbour is fetched to be ready, not to be shown. Telling the windows about
        # a game nobody is looking at is a message per wheel step for nothing.
        if not found or index or _broadcast is None:
            continue
        # The message names the game it is about, so an answer arriving after the wheel
        # has moved lands on the entry it belongs to rather than the one now in front of
        # the player. Nothing has to be thrown away to prevent that.
        _broadcast({"type": "EntryDataChange",
                    "game_id": descriptor["game_id"], "ext": found})


def on_selected(*, game: GameRecord | None = None,
                neighbors: Iterable[GameRecord | None] = (),
                **_payload: Any) -> None:
    """The player moved to a game. Ask about it, and about where they are heading.

    The neighbours are asked on the same signal and for the same reason media is
    preloaded there: what somebody sees is then the answer fetched a step ago, and the
    gap only shows on the first game of a cold start.
    """
    if game is None or not contributions.keys():
        return
    wanted = [game, *[one for one in (neighbors or ()) if one is not None]]
    threading.Thread(target=_fetch_and_tell, args=(wanted,), daemon=True,
                     name="ext-data").start()


def register(broadcast: Callable[[dict], None]) -> None:
    """Attach to the bus. Safe to call more than once."""
    global _registered, _broadcast
    _broadcast = broadcast
    if _registered:
        return
    events.subscribe(events.GAME_SELECTED, on_selected)
    _registered = True


def reset_for_tests() -> None:
    global _registered, _broadcast
    if _registered:
        events.unsubscribe(events.GAME_SELECTED, on_selected)
    _registered = False
    _broadcast = None
