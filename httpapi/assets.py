"""Every asset file in the library, as its own lens on it.

`common/games/asset_lens.py` builds the rows. Media is not here and assets are not there:
the two lenses answer different questions, and a matrix that mixes them is neither.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from common.games import asset_lens

from . import models, scopes
from .auth import requires

router = APIRouter(prefix="/assets", tags=["assets"])


@router.get("", summary="Every asset file in the library",
            dependencies=[requires(scopes.GAMES_READ)])
def list_assets(limit: int = Query(0, ge=0), offset: int = Query(0, ge=0),
                game: str = Query(""), kind: str = Query("")) -> models.AssetSlotList:
    """One row per asset file the library holds, plus one per file it does not.

    A file named for a table gets its own row. A file named for nothing - the residue of
    a table that was renamed or deleted - gets one too, and says so: it is the thing an
    audit of a folder wants to see, and nothing has ever shown it.
    """
    return models.AssetSlotList.model_validate(
        asset_lens.listing(limit, offset, game, kind))
