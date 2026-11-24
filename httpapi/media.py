"""Every media file in the library, as its own lens on it.

`common/games/media_lens.py` builds the rows. A row is a file, or the absence of one -
which is the difference from the media a game or a table reports, where a cell holds one
fact about one kind.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from common.games import media_lens, media_service

from . import models, scopes
from .auth import requires

router = APIRouter(prefix="/media", tags=["media"])


@router.get("", summary="Every media file in the library",
            dependencies=[requires(scopes.GAMES_READ)])
def list_media(limit: int = Query(0, ge=0), offset: int = Query(0, ge=0),
               game: str = Query(""), kind: str = Query("")) -> models.MediaSlotList:
    """One row per media file the library holds, plus one per file it does not.

    A game and kind has a **shared** row - the file each of its tables falls through to -
    and a table earns a row of its own only where a file is named for it. Two rows are
    deliberately not reported, because each would be the software inventing a gap: an
    empty per-table row, which would say a table-specific file ought to exist, and a
    missing shared row in a folder where every table already has its own.
    """
    return models.MediaSlotList.model_validate(
        media_lens.listing(limit, offset, game, kind))


@router.get("/shared", summary="The shared media rows, cut for counting",
            dependencies=[requires(scopes.GAMES_READ)])
def list_shared_media() -> models.SharedMediaList:
    """What `GET /media` lists for each game and kind that is the shared file or the lack of
    one, with `game_id`, `kind`, `page`, `present`, `file`, `path`, `via`, `origin`,
    `matched_to` and `version` and nothing else, and unsorted. Rows for a file named for one
    table, and `orphan` and `unused` files, are not here. For a client that counts coverage
    and has no use for the rest."""
    return models.SharedMediaList.model_validate({"media": media_lens.shared_rows()})


@router.get("/wheel-sets", summary="The wheel sets the library has media in",
            dependencies=[requires(scopes.GAMES_READ)])
def list_wheel_sets() -> models.WheelSetList:
    """The folder names under any game's `medias/wheels/`, and the reserved `logo`, which
    needs no folder. What `[Media] wheelset` and a theme's `wheelSet` can name."""
    return models.WheelSetList(sets=media_service.wheel_sets())
