"""Walking this machine's directories over the wire, so artwork can be picked from where
it landed.

`common/games/media_browse.py` decides what may be read and lists it. Reading a directory over
HTTP is a real capability, so it is bounded rather than trusted: the game library, plus
whatever folders the owner listed, and nothing else.
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from starlette.responses import FileResponse

from common import service_errors
from common.games import game_repository, media_browse
from common.games.asset_registry import extensions_listed
from common.i18n import t

from . import models, scopes
from .auth import requires
from .responses import LIBRARY_FILE

router = APIRouter(prefix="/filesystem", tags=["filesystem"])


@router.get("/roots", summary="Where browsing for media may start",
            dependencies=[requires(scopes.FILESYSTEM_READ)])
def get_roots(game: str = Query("")) -> models.FilesystemRootList:
    """An empty list is the honest answer for an install with no library configured and
    nothing allowlisted - not an error, and not a reason to offer the whole disk."""
    found = game_repository.game_by_id(game) if game else None
    game_dir = str(found.full_path_game or "") if found else ""
    return models.FilesystemRootList.model_validate(
        {"roots": media_browse.roots(game_dir)})


@router.get("/file", summary="One browsable media file",
            dependencies=[requires(scopes.FILESYSTEM_READ)])
def get_file(path: str = Query(...)) -> FileResponse:
    """Serve a file so it can be looked at before it is taken. Judging artwork means
    seeing it, and a name and a byte count do not do that."""
    return FileResponse(media_browse.media_file(path), headers=LIBRARY_FILE)


@router.get("/entries", summary="What is in one folder",
            dependencies=[requires(scopes.FILESYSTEM_READ)])
def get_entries(path: str = Query(...), kind: str = Query(""),
                archives: bool = Query(False)) -> models.FilesystemListing:
    """Folders and media files, folders first, both by name - and with `kind`, the files
    that asset kind takes, so a slot for a backglass can be filled from here too.
    `kind` is the registry's name or the asset lens's. With `archives`, archives too, for
    a caller that can take a kind out of one."""
    try:
        wanted = extensions_listed(kind, archives)
    except KeyError as exc:
        raise service_errors.RefusedError(t("error.assets.unknown_kind"),
                                          details={"unknown": kind}) from exc
    return models.FilesystemListing.model_validate(
        media_browse.entries(path, extensions=wanted))
