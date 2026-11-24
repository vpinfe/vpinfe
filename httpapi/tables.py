"""Every table in the library, as its own lens on it.

A game's row cannot tell its tables apart - that is the whole of what this is for. A
folder holding four .vpx files collapses to one row under Games, and the questions that
are actually about a file (which is the default, which is out of date, which has its own
media) have nowhere to be asked.

Not a replacement for the game lens. Identity and shared media are the game's, and saying
so four times over is worse than saying it once. These are peers: the library seen by
folder, or seen by launchable file.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from common.games import table_lens

from . import models, scopes
from .auth import requires

router = APIRouter(prefix="/tables", tags=["tables"])


@router.get("/apps", summary="The programs that play a table",
            dependencies=[requires(scopes.GAMES_READ)])
def list_apps() -> models.LaunchAppList:
    """What can launch something in this library, and which files each one claims."""
    return models.LaunchAppList.model_validate(table_lens.launch_apps())


@router.get("", summary="Every table in the library",
            dependencies=[requires(scopes.GAMES_READ)])
def list_tables(limit: int = Query(0, ge=0), offset: int = Query(0, ge=0),
                game: str = Query(""), media: str = Query("")) -> models.TableRowList:
    """One row per launchable file, each carrying the game it belongs to.

    The game's name and maker ride along rather than being a lookup the caller has to
    make: this list is read to be shown, and a table named only by its filename is the
    thing the games lens already fails at.
    """
    return models.TableRowList.model_validate(
        table_lens.library_rows(limit, offset, game, media))
