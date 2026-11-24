"""Where this install looks for entries, over the wire.

What the list is lives under `/config/locations`, and which one new games go to is
`updates.new_games_location`. What is here is what the disk says about each location
right now, and the questions asked of the library through them.

`common/games/library_folder_ops.py` is what answers.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body

from common.games import library_folder_ops

from . import scopes
from .auth import requires

router = APIRouter(prefix="/locations", tags=["locations"])


@router.get("", summary="Every location this install looks in",
            dependencies=[requires(scopes.CONFIG_READ)])
def list_locations() -> dict[str, Any]:
    """The locations in order, each with what the disk says about it right now."""
    return library_folder_ops.listing()


@router.get("/destination", summary="Where a new game would be created",
            dependencies=[requires(scopes.CONFIG_READ)])
def new_game_destination() -> dict[str, Any]:
    """Where it would go, or why it could not, and what else it could go to instead.

    Declared above `/{location_id}`, which would otherwise read "destination" as an id.
    """
    return library_folder_ops.destination()


@router.get("/{location_id}/shadowed",
            summary="Game folders here whose id something else answers for",
            dependencies=[requires(scopes.CONFIG_READ)])
def list_shadowed(location_id: str) -> dict[str, Any]:
    """Both sides, because the question a person brings here is which of the two copies
    they meant to keep - and that cannot be answered by naming only one of them."""
    return library_folder_ops.shadowed_in(location_id)


@router.post("/{location_id}/shadowed/adopt",
             summary="Give one shadowed folder an id of its own",
             dependencies=[requires(scopes.CONFIG_WRITE)])
def adopt_shadowed(location_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Settle one clash by making this folder a game in its own right.

    The other way to settle it is to remove the location, which is right when the whole
    location is a second view of one library. That is `DELETE /config/locations/{id}` and
    it writes nothing at all.
    """
    return library_folder_ops.adopt(location_id, str(body.get("path") or ""))
