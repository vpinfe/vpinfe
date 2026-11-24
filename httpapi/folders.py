"""This device's folders and files, for a path field's Browse button.

`common/media/folder_browse.py` decides what is listed and how far it may look.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Query, Request
from starlette.responses import FileResponse

from common.games import table_ops
from common.i18n import t
from common.media import folder_browse
from common.uploads import upload_ops

from . import models, scopes
from .auth import ForbiddenError, caller_is_local, requires

router = APIRouter(prefix="/folders", tags=["folders"])


@router.get("", summary="This device's folders and files under a path",
            dependencies=[requires(scopes.CONFIG_WRITE)])
def list_folders(request: Request, path: str = Query(""), kind: str = Query("dir"),
                 suffix: list[str] = Query(default=[]),
                 hidden: bool = Query(False)) -> models.FolderListing:
    if not caller_is_local(request):
        raise ForbiddenError(t("error.folders.this_device_only"))
    return models.FolderListing.model_validate(
        folder_browse.listing(path, kind, tuple(suffix), hidden))


@router.get("/file", summary="One image, video or sound on this device",
            dependencies=[requires(scopes.CONFIG_WRITE)])
def get_file(request: Request, path: str = Query(...)) -> FileResponse:
    """The file itself, so a picker can show it before it is taken and then take it.

    The listing above is the whole disk, so this is held to media by extension: without
    that it would read any file the listing could name. `/filesystem/file` is the bounded
    one for API clients.
    """
    if not caller_is_local(request):
        raise ForbiddenError(t("error.folders.this_device_only"))
    return FileResponse(folder_browse.media_file(path))


@router.post("", summary="Make a folder on this device",
             dependencies=[requires(scopes.CONFIG_WRITE)])
def make_folder(request: Request, body: dict = Body(...)) -> models.FolderListing:
    """The new folder's own listing, so a picker can go straight into it.

    Writes to the disk, so it takes the scope adding a library folder takes - the folder
    is made for the same reason.
    """
    if not caller_is_local(request):
        raise ForbiddenError(t("error.folders.this_device_only"))
    return models.FolderListing.model_validate(
        folder_browse.make_folder(str(body.get("parent") or ""), str(body.get("name") or "")))


@router.post("/uploads", summary="Begin an upload session over a path on this device",
             dependencies=[requires(scopes.CONFIG_WRITE), requires(scopes.UPLOADS_WRITE)])
def begin_upload_here(request: Request, body: models.UploadFromPath) -> models.UploadBegun:
    """`/uploads/from_path` for a path the person picked in a browser of this device, so it
    is not held to the library's folders. That one stays bounded for API clients."""
    if not caller_is_local(request):
        raise ForbiddenError(t("error.folders.this_device_only"))
    return models.UploadBegun(**upload_ops.begin_over(folder_browse.source_here(body.path)))


@router.post("/games/{game_id}/tables", summary="Copy a game file on this device into a game",
             status_code=201,
             dependencies=[requires(scopes.CONFIG_WRITE), requires(scopes.GAMES_WRITE)])
def import_table_here(request: Request, game_id: str, body: models.TableImport) -> models.Table:
    """`/games/{id}/tables/import` for a path the person picked in a browser of this
    device. The file still has to be one a program plays."""
    if not caller_is_local(request):
        raise ForbiddenError(t("error.folders.this_device_only"))
    return models.Table(**table_ops.import_file(game_id,
                                                str(folder_browse.source_here(body.path))))
