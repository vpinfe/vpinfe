"""The game catalog.

A game is the pinball-machine concept - folder, identity, metadata, media and
assets. The launchable artifact is a table, exposed as a sub-resource, because a
game is not permanently one .vpx.

The answers come from `common/games/`: `game_lens` and `table_lens` read, `game_ops`,
`table_ops` and `media_ops` write. What is here is the wire - the path, the scope, the
model, and the few things only a request has, like an upload's bytes and a file response.
"""

from __future__ import annotations

import logging
import os
import secrets
import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, Body, File, Query, Request, UploadFile
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse, Response

from common.extensions import reach
from common.games import (
    archive_service,
    asset_lens,
    asset_ops,
    backglass_displays,
    backglass_ops,
    backglass_order,
    collection_ops,
    file_move,
    file_ops,
    game_lens,
    game_ops,
    high_score_reads,
    library_vps_state,
    media_browse,
    media_ops,
    outside_links,
    picture_ops,
    table_ops,
    table_swap,
    text_file,
)
from common.host import play_service
from common.i18n import t
from common.online import game_offers

from . import models, responses, scopes
from .auth import ForbiddenError, require, requires

logger = logging.getLogger("vpinfe.httpapi.games")

router = APIRouter(prefix="/games", tags=["games"])

PAGE = Query(1, ge=1, description="Which page of a paged kind; page 1 is the kind itself")
HELD_FILE = Query("", description="A file the slot holds, by the `file` name its `tiers` give, "
                                  "instead of the one it shows")
REMOVE_PAGE = Query(None, ge=1, description="Remove this page alone; none removes the kind")


@router.get("", summary="List games", dependencies=[requires(scopes.GAMES_READ)])
def list_games(
    q: str = Query("", description="Match against name, manufacturer or rom"),
    limit: int = Query(0, ge=0, description="0 returns everything"),
    offset: int = Query(0, ge=0),
) -> models.GameList:
    return models.GameList.model_validate(game_lens.listing(q, limit, offset))


@router.post("", summary="Create a game", status_code=201,
             dependencies=[requires(scopes.GAMES_WRITE)])
def create_game(body: models.NewGameRequest) -> models.GameResource:
    """A folder with a record in it, in the location new games go to."""
    return models.GameResource(**game_ops.create(body.name, body.location))


@router.post("/from_vps", summary="Create a game from a catalog entry", status_code=201,
             dependencies=[requires(scopes.GAMES_WRITE)])
def create_game_from_vps(body: models.NewGameFromVpsRequest) -> models.GameResource:
    """A game with no tables, matched to the entry; its media is filled if that is on."""
    return models.GameResource(**game_ops.create_from_vps(body.vps_id, body.location))


@router.get("/{game_id}", summary="One game", dependencies=[requires(scopes.GAMES_READ)])
def get_game(game_id: str) -> models.GameResource:
    return models.GameResource(**game_lens.detail(game_id))


@router.delete("/{game_id}/folder", summary="Delete a game's folder from the disk",
               dependencies=[requires(scopes.PROTECTED)])
def delete_game_folder(game_id: str) -> models.GameFolderDeleted:
    """Removes the whole folder: the tables, media, .info and everything else in it. There
    is no trash and no undo. A Protected action: this device, or an Owner who has just
    confirmed, and never a token."""
    return models.GameFolderDeleted(**game_ops.delete_from_disk(game_id))


@router.get("/{game_id}/collections", summary="The collections holding a game",
            dependencies=[requires(scopes.COLLECTIONS_READ)])
def get_game_collections(game_id: str) -> models.GameInCollections:
    return models.GameInCollections.model_validate(collection_ops.collections_of(game_id))


@router.get("/{game_id}/assets/detail", summary="One asset file or folder, in detail",
            dependencies=[requires(scopes.GAMES_READ)])
def get_asset_detail(game_id: str, path: str = Query(...),
                     lines: int = Query(asset_lens.HEAD_LINES, ge=0, le=100_000),
                     ) -> models.AssetDetail:
    """`lines` is how much of a text file to return; 0 is all of it."""
    return models.AssetDetail.model_validate(asset_lens.file_detail(game_id, path, lines))


@router.get("/{game_id}/assets/backglass/picture",
            summary="The picture a backglass file draws",
            dependencies=[requires(scopes.GAMES_READ)])
def get_backglass_picture(game_id: str, request: Request,
                          look: str = Query("on", description="`on`, `off` or `every`"),
                          version: str = Query("authentic",
                                               description="`authentic` or `fantasy`"),
                          grill: bool = Query(False, description="Cut as the renderer does"),
                          path: str = Query("", description="A file in the folder; the "
                                                            "one the folder reads if empty"),
                          size: int | None = responses.MEDIA_SIZE,
                          v: str = responses.MEDIA_VERSION) -> Response:
    return responses.media_file(backglass_ops.picture_file(
        game_id, "", look, version, grill, path, size), request, v)


@router.get("/{game_id}/tables/{table_id}/assets/backglass/picture",
            summary="The picture the table's backglass file draws",
            dependencies=[requires(scopes.GAMES_READ)])
def get_table_backglass_picture(game_id: str, table_id: str, request: Request,
                                look: str = Query("on"), version: str = Query("authentic"),
                                grill: bool = Query(False),
                                path: str = Query(""),
                                size: int | None = responses.MEDIA_SIZE,
                                v: str = responses.MEDIA_VERSION) -> Response:
    return responses.media_file(backglass_ops.picture_file(
        game_id, table_id, look, version, grill, path, size), request, v)


@router.get("/{game_id}/assets/backglass/scoreview",
            summary="The art a backglass file draws on the score view",
            dependencies=[requires(scopes.GAMES_READ)])
def get_backglass_scoreview(game_id: str, request: Request,
                             path: str = Query("", description="A file in the folder; the "
                                                               "one the folder reads if empty"),
                             size: int | None = responses.MEDIA_SIZE,
                             v: str = responses.MEDIA_VERSION) -> Response:
    return responses.media_file(backglass_ops.scoreview_file(game_id, "", path, size), request, v)


@router.get("/{game_id}/tables/{table_id}/assets/backglass/scoreview",
            summary="The score view art of the table's backglass file",
            dependencies=[requires(scopes.GAMES_READ)])
def get_table_backglass_scoreview(game_id: str, table_id: str, request: Request,
                                   path: str = Query(""),
                                   size: int | None = responses.MEDIA_SIZE,
                                   v: str = responses.MEDIA_VERSION) -> Response:
    return responses.media_file(
        backglass_ops.scoreview_file(game_id, table_id, path, size), request, v)


@router.get("/{game_id}/assets/{kind}/placements",
            summary="Where a file of this kind could go, and what it would replace",
            dependencies=[requires(scopes.GAMES_READ)])
def get_asset_placements(game_id: str, kind: str) -> models.AssetPlacementList:
    return models.AssetPlacementList.model_validate(asset_ops.placements(game_id, kind))


@router.get("/{game_id}/assets/{kind}/displaced",
            summary="What placing this file would replace",
            dependencies=[requires(scopes.GAMES_READ)])
def get_asset_displaced(game_id: str, kind: str, filename: str = Query(...),
                        table: str = Query("")) -> models.AssetDisplaced:
    return models.AssetDisplaced.model_validate(
        asset_ops.displaced(game_id, kind, filename, table))


async def _stage(upload: UploadFile) -> Path:
    """The upload in a real file, copied across in chunks.

    `upload.read()` would hold the whole body in memory, and a video is hundreds of
    megabytes. The parser has already spooled the upload to disk. The file is made with
    the mode a plain copy would get: media placement copies the mode across, and a
    private one would leave media no other user of a shared library can read.
    """
    path = Path(tempfile.gettempdir()) / (secrets.token_hex(12)
                                          + Path(upload.filename or "").suffix)
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666),
                   "wb") as staged:
        try:
            await run_in_threadpool(shutil.copyfileobj, upload.file, staged)
        except BaseException:
            staged.close()
            path.unlink(missing_ok=True)
            raise
    return path


async def _staged_asset(game_id: str, kind: str, table_id: str,
                        upload: UploadFile) -> dict:
    """The upload held in a real file for the service, and cleared whatever it does."""
    asset_ops.kind_or_refuse(kind)
    staged = await _stage(upload)
    try:
        return await run_in_threadpool(asset_ops.place_file, game_id, kind, table_id,
                                       staged, upload.filename or "")
    finally:
        staged.unlink(missing_ok=True)


@router.get("/{game_id}/assets/text", summary="A text file of a game, to edit",
            dependencies=[requires(scopes.GAMES_READ)])
def get_asset_text(game_id: str, path: str = Query(...)) -> models.AssetText:
    return models.AssetText.model_validate(text_file.read(game_id, path))


# Ahead of `/assets/{kind}`, which would take "text" for a kind.
@router.put("/{game_id}/assets/text", summary="Write a text file of a game",
            dependencies=[requires(scopes.GAMES_WRITE)])
def put_asset_text(game_id: str, body: models.AssetTextSave,
                   path: str = Query(...)) -> models.AssetTextSaved:
    return models.AssetTextSaved.model_validate(text_file.save(
        game_id, path, body.text, body.version, replace_changed=body.replace_changed,
        keep_copy=body.keep_copy, media=body.media))


@router.put("/{game_id}/assets/backglass/displays",
            summary="Set where a backglass file places the DMD, or take it out",
            dependencies=[requires(scopes.GAMES_WRITE)])
def put_backglass_displays(game_id: str, body: models.AssetDisplayPlace,
                           path: str = Query(...)) -> models.AssetDisplaysWritten:
    """Written into the file itself, which every table that reads it shares. Refused while a
    table of the game is playing. Each table of the game that the launcher's program lets
    draw its own order is brought to what the file now places, and one that cannot be
    written is named in `order_failed`."""
    written = backglass_displays.place(game_id, path, body.target, (body.x, body.y, body.w, body.h))
    written["order_failed"] = backglass_order.settle(game_id)
    return models.AssetDisplaysWritten.model_validate(written)


@router.post("/{game_id}/assets/backglass/displays/undo",
             summary="Put a backglass file's DMDs back as a change found them",
             dependencies=[requires(scopes.GAMES_WRITE)])
def undo_backglass_displays(game_id: str,
                            body: models.AssetDisplaysUndo) -> models.AssetDisplaysWritten:
    written = backglass_displays.undo(game_id, body.undo)
    written["order_failed"] = backglass_order.settle(game_id)
    return models.AssetDisplaysWritten.model_validate(written)


@router.put("/{game_id}/assets/{kind}", summary="Place a file under the folder's own name",
            dependencies=[requires(scopes.GAMES_WRITE)])
async def put_game_asset(game_id: str, kind: str,
                         file: UploadFile = File(...)) -> models.AssetWritten:
    return models.AssetWritten(**await _staged_asset(game_id, kind, "", file))


@router.put("/{game_id}/tables/{table_id}/assets/{kind}",
            summary="Place a file under one table's name",
            dependencies=[requires(scopes.GAMES_WRITE)])
async def put_table_asset(game_id: str, table_id: str, kind: str,
                          file: UploadFile = File(...)) -> models.AssetWritten:
    return models.AssetWritten(**await _staged_asset(game_id, kind, table_id, file))


@router.post("/{game_id}/assets/{kind}/import",
             summary="Copy a file from this device in under a table's or the folder's name",
             dependencies=[requires(scopes.GAMES_WRITE), requires(scopes.FILESYSTEM_READ)])
def import_asset(game_id: str, kind: str, body: models.AssetImport) -> models.AssetWritten:
    source = media_browse.within_roots(body.path)
    return models.AssetWritten(**asset_ops.place_file(game_id, kind, body.table, source))


@router.delete("/{game_id}/assets", summary="Remove one asset file",
               dependencies=[requires(scopes.GAMES_WRITE)])
def delete_asset(game_id: str, path: str = Query(...)) -> models.AssetRemoved:
    return models.AssetRemoved.model_validate(asset_ops.remove(game_id, path))


def _break_is_protected(request: Request, resolve: list[str]) -> None:
    """A change that goes ahead with an effect it was refused for loses something no copy
    holds, so it is not for a token."""
    if file_ops.RESOLVE_BREAK in resolve:
        require(request, scopes.PROTECTED)


RESOLVE = Query([], description="How to go on where the change was refused for its effects: "
                                "`break`, `replace`, `keep` or `fix`, as each effect lists")


@router.get("/{game_id}/files/impacts", summary="What changing a file of a game would do",
            dependencies=[requires(scopes.GAMES_READ)])
def get_file_impacts(game_id: str, path: str = Query(...), name: str | None = Query(None),
                     to: str | None = Query(None), remove: bool = Query(False),
                     ) -> models.FileImpacts:
    return models.FileImpacts.model_validate(file_ops.impacts(
        game_id, path, folder=False, name=name, to=to, remove=remove))


@router.patch("/{game_id}/files", summary="Rename or move a file of a game",
              dependencies=[requires(scopes.GAMES_WRITE)])
def change_game_file(game_id: str, body: models.EntryChange, request: Request,
                     path: str = Query(...)) -> models.EntryChanged:
    """Any file in the game's folder, by its path there. Never overwrites unless `replace`
    is answered, and what it replaces goes to Backups unless `keep_copy` is off."""
    _break_is_protected(request, body.resolve)
    return models.EntryChanged.model_validate(file_ops.change(
        game_id, path, folder=False, name=body.name, to=body.to, resolve=body.resolve,
        keep_copy=body.keep_copy))


@router.delete("/{game_id}/files", summary="Remove a file of a game",
               dependencies=[requires(scopes.GAMES_WRITE)])
def delete_game_file(game_id: str, request: Request, path: str = Query(...),
                     resolve: list[str] = RESOLVE, keep_copy: bool = Query(True),
                     ) -> models.AssetRemoved:
    """A copy goes to Backups first, unless `keep_copy` is off."""
    _break_is_protected(request, resolve)
    return models.AssetRemoved.model_validate(
        file_ops.remove(game_id, path, folder=False, resolve=resolve, keep_copy=keep_copy))


@router.get("/{game_id}/folders/impacts", summary="What changing a folder of a game would do",
            dependencies=[requires(scopes.GAMES_READ)])
def get_folder_impacts(game_id: str, path: str = Query(...), name: str | None = Query(None),
                       to: str | None = Query(None), remove: bool = Query(False),
                       ) -> models.FileImpacts:
    return models.FileImpacts.model_validate(file_ops.impacts(
        game_id, path, folder=True, name=name, to=to, remove=remove))


@router.post("/{game_id}/folders", summary="Make a folder in a game",
             status_code=201, dependencies=[requires(scopes.GAMES_WRITE)])
def make_game_folder(game_id: str, body: models.FolderMake) -> models.FolderMade:
    return models.FolderMade.model_validate(file_ops.make_folder(game_id, body.path))


@router.patch("/{game_id}/folders", summary="Rename or move a folder of a game",
              dependencies=[requires(scopes.GAMES_WRITE)])
def change_game_folder(game_id: str, body: models.EntryChange, request: Request,
                       path: str = Query(...)) -> models.EntryChanged:
    """The files in it keep where they came from. It cannot go into itself."""
    _break_is_protected(request, body.resolve)
    return models.EntryChanged.model_validate(file_ops.change(
        game_id, path, folder=True, name=body.name, to=body.to, resolve=body.resolve))


@router.delete("/{game_id}/folders", summary="Remove a folder of a game",
               dependencies=[requires(scopes.GAMES_WRITE)])
def delete_game_folder_inside(game_id: str, request: Request, path: str = Query(...),
                              resolve: list[str] = RESOLVE) -> models.AssetRemoved:
    """One with files in it is refused until `keep` moves them up a folder, or `break`
    removes them with it and no copy."""
    _break_is_protected(request, resolve)
    return models.AssetRemoved.model_validate(
        file_ops.remove(game_id, path, folder=True, resolve=resolve))


@router.get("/{game_id}/links", summary="Where a game, a table or a file is elsewhere",
            dependencies=[requires(scopes.GAMES_READ)])
def get_outside_links(game_id: str, table: str = Query(""),
                      path: str = Query("")) -> models.OutsideLinks:
    """The game's, a table's with `table`, or a file's with `path`, as extensions have
    contributed them."""
    return models.OutsideLinks.model_validate(outside_links.links(game_id, table, path))


@router.get("/{game_id}/tables", summary="A game's tables",
            dependencies=[requires(scopes.GAMES_READ)])
def get_games(game_id: str) -> models.TableList:
    return models.TableList.model_validate(table_ops.rows_of(game_id))


@router.get("/{game_id}/media", summary="A game's shared media",
            dependencies=[requires(scopes.GAMES_READ)])
def get_game_media(game_id: str, codecs: bool = Query(False)) -> models.MediaList:
    """Media is the artwork shown about a game - every kind, present or not, so a client
    can enumerate what is possible instead of guessing."""
    return models.MediaList.model_validate(media_ops.game_media(game_id, codecs))


@router.get("/{game_id}/media/overrides",
            summary="Kinds where a table has media of its own",
            dependencies=[requires(scopes.GAMES_READ)])
def get_media_overrides(game_id: str) -> models.MediaOverrideList:
    return models.MediaOverrideList.model_validate(media_ops.overrides(game_id))


@router.get("/{game_id}/media/{kind}", summary="One shared media file",
            dependencies=[requires(scopes.GAMES_READ)])
def get_game_media_file(game_id: str, kind: str, request: Request,
                        size: int | None = responses.MEDIA_SIZE,
                        v: str = responses.MEDIA_VERSION, page: int = PAGE,
                        file: str = HELD_FILE) -> Response:
    return responses.media_file(
        media_ops.media_file(game_id, kind, "", size, page, file), request, v)


@router.get("/{game_id}/tables/{table_id}/media", summary="One table's media",
            dependencies=[requires(scopes.GAMES_READ)])
def get_table_media(game_id: str, table_id: str,
                    codecs: bool = Query(False)) -> models.MediaList:
    return models.MediaList.model_validate(media_ops.table_media(game_id, table_id, codecs))


@router.get("/{game_id}/tables/{table_id}/media/{kind}", summary="One table's media file",
            dependencies=[requires(scopes.GAMES_READ)])
def get_table_media_file(game_id: str, table_id: str, kind: str, request: Request,
                         size: int | None = responses.MEDIA_SIZE,
                         v: str = responses.MEDIA_VERSION, page: int = PAGE,
                         file: str = HELD_FILE) -> Response:
    return responses.media_file(
        media_ops.media_file(game_id, kind, table_id, size, page, file), request, v)


@router.get("/{game_id}/media/{kind}/placements",
            summary="Where a file for this kind could go, and what it would replace",
            dependencies=[requires(scopes.GAMES_READ)])
def get_placements(game_id: str, kind: str) -> models.MediaPlacementList:
    return models.MediaPlacementList.model_validate(
        media_ops.placements(game_id, kind))


@router.post("/{game_id}/media/{kind}/import",
             summary="Put a file from this device into a slot",
             dependencies=[requires(scopes.GAMES_WRITE), requires(scopes.FILESYSTEM_READ)])
def import_media(game_id: str, kind: str, body: models.MediaImport) -> models.MediaWritten:
    """Copy artwork in from anywhere on this device the install is allowed to read.

    Both scopes, because it is both things: it reads a file off the disk and it writes a
    game's media, and holding one of those is not permission for the other. Which paths
    this install may read is this layer's question, so it is answered here.
    """
    source = media_browse.within_roots(body.path)
    return models.MediaWritten(
        **media_ops.place_file(game_id, kind, body.table, source, page=body.page))


@router.post("/{game_id}/media/backglass/from-file",
             summary="Draw the backglass file's picture into the backglass slot",
             dependencies=[requires(scopes.GAMES_WRITE)])
def place_backglass_picture(game_id: str, body: models.MediaFromFile) -> models.MediaWritten:
    """The picture as the renderer in effect shows it, recorded as drawn from the file.
    It is drawn again whenever the file changes, for as long as nobody replaces it."""
    return models.MediaWritten(
        **backglass_ops.place_picture(game_id, body.table, body.asset_kind))


@router.post("/{game_id}/media/{kind}/fetch",
             summary="Take a file from an online catalog into a slot",
             dependencies=[requires(scopes.GAMES_WRITE), requires(scopes.VPS_READ)])
def fetch_media(game_id: str, kind: str, body: models.MediaFetch) -> models.MediaWritten:
    return models.MediaWritten(**media_ops.fetch_file(
        game_id, kind, body.table, body.source, body.vps_id, body.size))


@router.get("/{game_id}/media/{kind}/detail", summary="One shared slot, in detail",
            dependencies=[requires(scopes.GAMES_READ)])
def get_game_media_detail(game_id: str, kind: str, page: int = PAGE) -> models.MediaDetail:
    return models.MediaDetail(**media_ops.detail(game_id, kind, "", page))


@router.get("/{game_id}/tables/{table_id}/media/{kind}/detail",
            summary="One table's slot, in detail",
            dependencies=[requires(scopes.GAMES_READ)])
def get_table_media_detail(game_id: str, table_id: str, kind: str,
                           page: int = PAGE) -> models.MediaDetail:
    return models.MediaDetail(**media_ops.detail(game_id, kind, table_id, page))


@router.get("/{game_id}/moves/{family}/{kind}",
            summary="What a move of the game's file to its tables would start from",
            dependencies=[requires(scopes.GAMES_READ)])
def get_file_move(game_id: str, family: str, kind: str) -> models.FileMoveState:
    """`family` is `media` or `asset`: the two kinds named backglass are the picture and
    the file the backglass server reads."""
    return models.FileMoveState.model_validate(file_move.state(game_id, family, kind))


@router.post("/{game_id}/moves/{family}/{kind}/to-tables",
             summary="Give the game's file to these tables",
             dependencies=[requires(scopes.GAMES_WRITE)])
def move_file_to_tables(game_id: str, family: str, kind: str,
                        body: models.FileMoveTables) -> models.FileMoved:
    return models.FileMoved.model_validate(
        file_move.to_tables(game_id, family, kind, body.tables))


@router.post("/{game_id}/moves/{family}/{kind}/to-game",
             summary="Make a table's own file the game's",
             dependencies=[requires(scopes.GAMES_WRITE)])
def move_file_to_game(game_id: str, family: str, kind: str,
                      body: models.FileMoveGame) -> models.FileMoved:
    return models.FileMoved.model_validate(
        file_move.to_game(game_id, family, kind, body.table))


@router.post("/{game_id}/moves/undo", summary="Put a move back",
             dependencies=[requires(scopes.GAMES_WRITE)])
def undo_file_move(game_id: str, body: models.FileMoveUndo) -> models.FileMoveUndone:
    return models.FileMoveUndone.model_validate(file_move.undo(game_id, body.undo))


@router.post("/{game_id}/media/{kind}/move",
             summary="Swap two pages of a slot",
             dependencies=[requires(scopes.GAMES_WRITE)])
def move_media_page(game_id: str, kind: str, body: models.MediaMove) -> models.MediaWritten:
    return models.MediaWritten.model_validate(
        media_ops.move_page(game_id, kind, body.table, body.page, body.to))


@router.get("/{game_id}/media/{kind}/displaced",
            summary="What placing this file here would replace",
            dependencies=[requires(scopes.GAMES_READ)])
def get_game_media_displaced(game_id: str, kind: str, filename: str = Query(...),
                             page: int = PAGE) -> models.MediaDisplaced:
    return models.MediaDisplaced(**media_ops.displaced(game_id, kind, filename, "", page))


@router.get("/{game_id}/tables/{table_id}/media/{kind}/displaced",
            summary="What placing this file for one table would replace",
            dependencies=[requires(scopes.GAMES_READ)])
def get_table_media_displaced(game_id: str, table_id: str, kind: str,
                              filename: str = Query(...),
                              page: int = PAGE) -> models.MediaDisplaced:
    return models.MediaDisplaced(
        **media_ops.displaced(game_id, kind, filename, table_id, page))


async def _staged_write(game_id: str, kind: str, table_id: str, stem: str,
                        upload: UploadFile, page: int) -> dict:
    """Hold the upload's bytes somewhere real, then hand the path to the service.

    Here rather than in the service because the staging is the request's: this owns the
    bytes as they arrive and clears the temporary file whatever the write does.
    """
    media_ops.kind_or_refuse(kind)
    staged = await _stage(upload)
    try:
        return await run_in_threadpool(media_ops.place_upload, game_id, kind, table_id,
                                       staged, stem, page)
    finally:
        staged.unlink(missing_ok=True)


@router.put("/{game_id}/media/{kind}", summary="Place a file every table shares",
            dependencies=[requires(scopes.GAMES_WRITE)])
async def put_game_media(game_id: str, kind: str, file: UploadFile = File(...),
                         page: int = PAGE) -> models.MediaWritten:
    """Named for the folder, so every table in it resolves this unless it has its own.

    A `page` from 2 to one past the last is added to the flyer the slot shows, at the tier
    where that flyer's page 1 is."""
    stem = Path(game_lens.game_or_refuse(game_id).full_path_game or "").name
    return models.MediaWritten(**await _staged_write(game_id, kind, "", stem, file, page))


@router.put("/{game_id}/tables/{table_id}/media/{kind}",
            summary="Place a file for one table", dependencies=[requires(scopes.GAMES_WRITE)])
async def put_table_media(game_id: str, table_id: str, kind: str,
                          file: UploadFile = File(...),
                          page: int = PAGE) -> models.MediaWritten:
    """Named for this .vpx, so it serves this table and no other. A `page` past 1 follows
    page 1 as `PUT .../media/{kind}` does."""
    stem = media_ops.stem_or_refuse(game_lens.game_or_refuse(game_id), table_id)
    return models.MediaWritten(
        **await _staged_write(game_id, kind, table_id, stem, file, page))


@router.delete("/{game_id}/media/{kind}", summary="Remove the file every table shares",
               dependencies=[requires(scopes.GAMES_WRITE)])
def delete_game_media(game_id: str, kind: str, page: int | None = REMOVE_PAGE
                      ) -> models.MediaRemoved:
    """The folder-named file, or the default-named one the game shows when it has none. A
    table's own media survives. With a `page`, that page alone goes and the ones after it
    move down a number; with none, the kind goes with all its pages."""
    return models.MediaRemoved.model_validate(media_ops.remove(game_id, kind, "", page))


@router.delete("/{game_id}/tables/{table_id}/media/{kind}",
               summary="Remove one table's file",
               dependencies=[requires(scopes.GAMES_WRITE)])
def delete_table_media(game_id: str, table_id: str, kind: str,
                       page: int | None = REMOVE_PAGE) -> models.MediaRemoved:
    return models.MediaRemoved.model_validate(
        media_ops.remove(game_id, kind, table_id, page))


@router.put("/{game_id}/tables/{table_id}/hidden",
            summary="Offer this table in the frontend, or stop offering it",
            dependencies=[requires(scopes.GAMES_WRITE)])
def put_table_hidden(game_id: str, table_id: str,
                     body: models.TableVisibility) -> models.TableHidden:
    return models.TableHidden.model_validate(
        table_ops.set_hidden(game_id, table_id, body.hidden))


@router.post("/{game_id}/tables/{table_id}/script",
             summary="Extract this table's script beside it",
             dependencies=[requires(scopes.GAMES_WRITE)])
def extract_table_script(game_id: str, table_id: str) -> models.Table:
    """**This changes which script the table runs.** VPX loads a sidecar in place of the
    one inside the .vpx, so extracting is how a table is patched."""
    return models.Table(**table_ops.extract_script(game_id, table_id))


@router.delete("/{game_id}/tables/{table_id}/script",
               summary="Remove the script beside this table",
               dependencies=[requires(scopes.GAMES_WRITE)])
def delete_table_script(game_id: str, table_id: str) -> models.Table:
    """Puts the table back on the script inside its .vpx. Whatever the sidecar held goes
    with it, which is why the surface asks first."""
    return models.Table(**table_ops.delete_script(game_id, table_id))


@router.put("/{game_id}/tables/{table_id}/rating", summary="Rate one table",
            dependencies=[requires(scopes.PLAY_RATE)])
def put_table_rating(game_id: str, table_id: str,
                     payload: models.RatingRequest) -> models.Table:
    """Returns the table rather than the rating, because a client that just rated one is
    about to redraw the row."""
    return models.Table(**table_ops.set_rating(game_id, table_id, payload.rating))


@router.put("/{game_id}/tables/{table_id}/source", summary="Say which release a table is",
            dependencies=[requires(scopes.GAMES_WRITE)])
def put_table_source(game_id: str, table_id: str,
                     payload: models.TableSourceRequest) -> models.Table:
    """An empty id takes the claim back."""
    return models.Table(
        **table_ops.set_source(game_id, table_id, payload.vps_file_id))


@router.put("/{game_id}/asset_source", summary="Say which VPS record one file is",
            dependencies=[requires(scopes.GAMES_WRITE)])
def put_asset_source(game_id: str,
                     payload: models.AssetSourceRequest) -> models.AssetSource:
    return models.AssetSource(
        **game_ops.set_file_source(game_id, payload.path, payload.vps_file_id))


@router.get("/{game_id}/vps_state", summary="What the catalog lists for this game, kind by kind",
            dependencies=[requires(scopes.GAMES_READ)])
def get_vps_state(game_id: str) -> models.VpsState:
    """State, not findings: which of these is worth telling somebody about is a judgement
    the surface makes with the library in front of it. `obtainable` says the catalog lists
    a file, never that the file is yours to take."""
    return models.VpsState(
        **library_vps_state.state_of(game_lens.game_or_refuse(game_id), game_id))


@router.get("/{game_id}/offers", summary="What every enabled source has for this game",
            dependencies=[requires(scopes.VPS_READ)])
def get_game_offers(
    game_id: str, request: Request,
    kind: list[str] = Query([], description="A kind to ask for, by its slot's name; repeat "
                                            "for more. None asks every kind each source serves"),
    vps_id: str = Query("", description="Ask about this entry instead of the game's own"),
    name: str = Query("", description="The entry's name, where `vps_id` is another's"),
) -> models.GameOffers:
    """Every enabled source's answer for the game, together. A source that cannot answer a
    kind says why on that kind and the rest still arrive. Needs `games:read` as well as the
    route's scope, and an extension's source is left out where the caller may not use that
    extension. A Private game is asked of no extension. Extensions answer at Library at
    most."""
    require(request, scopes.GAMES_READ)
    reach.answer_at(reach.LIBRARY)
    identity = request.state.identity
    return models.GameOffers.model_validate(game_offers.wire_for_game(
        game_id, kind, vps_id=vps_id, name=name,
        permits=lambda source: identity.can(scopes.extension_scope(source.extension,
                                                                   reach.LIBRARY))))


@router.get("/{game_id}/vps_details", summary="Where the game's details and its entry disagree",
            dependencies=[requires(scopes.GAMES_READ)])
def get_vps_details(game_id: str) -> models.VpsDetails:
    return models.VpsDetails.model_validate(game_ops.vps_details(game_id))


@router.put("/{game_id}/vps_details", summary="Take the entry's details",
            dependencies=[requires(scopes.GAMES_WRITE)])
def put_vps_details(game_id: str,
                    body: models.VpsAdoptRequest | None = None) -> models.VpsDetails:
    """Returns the disagreement that is left - none when everything was taken, and the
    rest when a caller named only some. A client is about to redraw the panel, and this
    is what it would ask for next."""
    return models.VpsDetails.model_validate(
        game_ops.adopt_details(game_id, (body.fields or None) if body else None))


@router.put("/{game_id}/default_table", summary="Which table this game offers first",
            dependencies=[requires(scopes.GAMES_WRITE)])
def put_default_table(game_id: str, body: models.TableDefault) -> models.TableList:
    """Record the choice, or clear it by naming nothing."""
    return models.TableList.model_validate(
        table_ops.set_default(game_id, body.table))


@router.post("/{game_id}/tables/import", summary="Copy a game file into this game",
             status_code=201,
             dependencies=[requires(scopes.GAMES_WRITE), requires(scopes.FILESYSTEM_READ)])
def import_table(game_id: str, body: models.TableImport) -> models.Table:
    """Both scopes, the same as putting artwork in a slot: it reads a file off the disk and
    it writes a game, and holding one of those is not permission for the other."""
    return models.Table(
        **table_ops.import_file(game_id, str(media_browse.within_roots(body.path))))


@router.post("/{game_id}/tables", summary="Add something this game holds with no file",
             status_code=201, dependencies=[requires(scopes.GAMES_WRITE)])
def add_keyed_table(game_id: str, body: models.NewTableRequest) -> models.Table:
    """Record a ROM, a Pinball FX table, or anything else its program finds by name - or,
    with a path, a file this game points at without holding."""
    if body.path:
        return models.Table(**table_ops.add_referenced(game_id, str(body.path)))
    return models.Table(**table_ops.add_keyed(game_id, body.app or "", body.key or ""))


@router.post("/{game_id}/tables/{table_id}/contain",
             summary="Copy a referenced table in and stop pointing at it",
             dependencies=[requires(scopes.GAMES_WRITE)])
def contain_table(game_id: str, table_id: str) -> models.Table:
    return models.Table(**table_ops.contain(game_id, table_id))


@router.delete("/{game_id}/tables/{table_id}", summary="Forget a table that is gone",
               dependencies=[requires(scopes.GAMES_WRITE)])
def delete_table(game_id: str, table_id: str) -> models.TableForgotten:
    """No file is deleted, because there is none - what goes is the entry describing it."""
    return models.TableForgotten.model_validate(table_ops.forget(game_id, table_id))


@router.get("/{game_id}/tables/{table_id}/swap", summary="What swapping or deleting a table moves",
            dependencies=[requires(scopes.GAMES_READ)])
def get_table_swap(game_id: str, table_id: str) -> models.SwapPlan:
    """The table named by version and author, and the files named for it, which go with it
    or are carried to the file that takes its place."""
    return models.SwapPlan.model_validate(table_swap.plan(game_id, table_id))


@router.delete("/{game_id}/tables/{table_id}/file", summary="Delete a table from disk",
               dependencies=[requires(scopes.GAMES_WRITE)])
def delete_table_file(game_id: str, table_id: str) -> models.TableDeleted:
    """The file and every file named for it, after a copy of each goes to Backups. The
    game plays its next table; a collection that named this one plays the game's default."""
    return models.TableDeleted.model_validate(table_swap.delete(game_id, table_id))


@router.post("/{game_id}/tables/{table_id}/swap_back", summary="Swap a table back to a kept file",
             dependencies=[requires(scopes.GAMES_WRITE)])
def swap_table_back(game_id: str, table_id: str,
                    payload: models.TableSwapBack) -> models.TableSwapped:
    return models.TableSwapped.model_validate(
        table_swap.undo(game_id, table_id, payload.copies, payload.source))


@router.post("/{game_id}/launch", summary="Launch a game on this play host",
             status_code=202, dependencies=[requires(scopes.LAUNCH_INVOKE)])
def launch_game(game_id: str,
                payload: models.LaunchRequest | None = Body(default=None),
                ) -> models.LaunchAccepted:
    """Start a game and return once it is starting, not once it is over."""
    started = play_service.start(game_id, (payload.file or None) if payload else None)
    return models.LaunchAccepted.model_validate(
        {**started,
         "links": {"state": "/api/v1/play/state", "events": "/api/v1/events"}})


@router.put("/{game_id}/details", summary="Say what the game is",
            dependencies=[requires(scopes.GAMES_WRITE)])
def put_game_details(game_id: str, body: models.GameDetails) -> models.GameResource:
    """Describe a game no catalog has matched. Not where a VPS id goes - the alt_vps_id
    override is where that is said."""
    return models.GameResource(
        **game_ops.set_details(game_id, body.model_dump(exclude_unset=True)))


@router.put("/{game_id}/rating", summary="Rate a game",
            dependencies=[requires(scopes.PLAY_RATE)])
def put_game_rating(game_id: str, payload: models.RatingRequest) -> models.Rating:
    """A whole-value PUT rather than a PATCH: the rating is the resource, and sending it
    again is the same request twice rather than a second increment."""
    return models.Rating.model_validate(game_ops.set_rating(game_id, payload.rating))


@router.put("/{game_id}/tags", summary="The tags on a game",
            dependencies=[requires(scopes.GAMES_WRITE)])
def put_game_tags(game_id: str, payload: models.TagsRequest) -> models.Tags:
    return models.Tags.model_validate(game_ops.set_tags(game_id, payload.tags))


@router.put("/{game_id}/guides", summary="The guides on a game",
            dependencies=[requires(scopes.GAMES_WRITE)])
def put_game_guides(game_id: str, payload: models.GuidesRequest) -> models.Guides:
    """The whole list, in order: which of VPS's are hidden, and the person's own. A guide
    VPS lists cannot be left out, only hidden."""
    return models.Guides.model_validate(game_ops.set_guides(
        game_id, [one.model_dump() for one in payload.guides]))


@router.put("/{game_id}/play_record", summary="Set a game's play counters",
            dependencies=[requires(scopes.GAMES_WRITE)])
def put_play_record(game_id: str, body: models.PlayRecordUpdate) -> models.PlayRecord:
    """For a library that arrives already played. Rating, favorite and tags are opinions
    and have their own routes; these three are a record of what happened."""
    return models.PlayRecord(**game_ops.set_play_record(
        game_id, play_count=body.play_count,
        play_time_seconds=body.play_time_seconds, last_played=body.last_played))


@router.delete("/{game_id}/play_record", summary="Reset a game's play counters",
               dependencies=[requires(scopes.GAMES_WRITE)])
def reset_play_record(game_id: str) -> models.PlayRecord:
    """A DELETE, because what it removes is a record of what happened."""
    return models.PlayRecord(**game_ops.reset_play_record(game_id))


@router.get("/{game_id}/high_scores", summary="A game's high scores",
            dependencies=[requires(scopes.GAMES_READ)])
def get_high_scores(game_id: str) -> models.HighScores | None:
    """The default table's ROM. Read again first where its score file is newer than the
    read kept. Null where the table has no ROM, or a ROM with nothing that could be read."""
    return _high_scores(high_score_reads.for_game(game_id))


@router.get("/{game_id}/tables/{table_id}/high_scores", summary="A table's high scores",
            dependencies=[requires(scopes.GAMES_READ)])
def get_table_high_scores(game_id: str, table_id: str) -> models.HighScores | None:
    return _high_scores(high_score_reads.for_table(game_id, table_id))


def _high_scores(found: dict | None) -> models.HighScores | None:
    return models.HighScores.model_validate(found) if found is not None else None


@router.get("/{game_id}/pictures", summary="A game's pictures",
            dependencies=[requires(scopes.GAMES_READ)])
def get_pictures(game_id: str, table: str = Query("")) -> models.PictureList:
    """Newest first. `table` keeps the ones taken while that table played."""
    return models.PictureList.model_validate(picture_ops.listing(game_id, table))


@router.get("/{game_id}/pictures/{name}", summary="One picture",
            dependencies=[requires(scopes.GAMES_READ)])
def get_picture(game_id: str, name: str, request: Request,
                size: int | None = responses.MEDIA_SIZE,
                v: str = responses.MEDIA_VERSION) -> Response:
    return responses.media_file(picture_ops.picture_file(game_id, name, size), request, v)


@router.put("/{game_id}/pictures/{name}", summary="Put a picture on a game",
            dependencies=[requires(scopes.GAMES_WRITE)])
async def put_picture(game_id: str, name: str,
                      file: UploadFile = File(...)) -> models.Picture:
    """A PNG, under a name no picture of this game has: 409 where one does."""
    content = await file.read()
    return models.Picture.model_validate(
        await run_in_threadpool(picture_ops.put, game_id, name, content))


@router.delete("/{game_id}/pictures/{name}", summary="Delete a picture",
               dependencies=[requires(scopes.GAMES_WRITE)])
def delete_picture(game_id: str, name: str) -> models.PictureRemoved:
    return models.PictureRemoved.model_validate(picture_ops.remove(game_id, name))


@router.put("/{game_id}/tables/{table_id}/tags", summary="The tags on one table",
            dependencies=[requires(scopes.GAMES_WRITE)])
def put_table_tags(game_id: str, table_id: str, payload: models.TagsRequest) -> models.Tags:
    return models.Tags.model_validate(table_ops.set_tags(game_id, table_id, payload.tags))


@router.delete("/{game_id}/tables/{table_id}/play_record",
               summary="Reset one table's play counters",
               dependencies=[requires(scopes.GAMES_WRITE)])
def reset_table_record(game_id: str, table_id: str) -> models.TablePlayRecord:
    return models.TablePlayRecord(
        **table_ops.reset_play_record(game_id, table_id))


@router.put("/{game_id}/favorite", summary="Mark a game a favorite",
            dependencies=[requires(scopes.PLAY_RATE)])
def put_game_favorite(game_id: str, payload: models.FavoriteRequest) -> models.Favorite:
    """A whole-value PUT, for the reason the rating gives: the flag is the resource, and
    sending it twice is the same request rather than a toggle that races itself."""
    return models.Favorite.model_validate(
        game_ops.set_favorite(game_id, payload.favorite))


@router.put("/{game_id}/private", summary="Mark a game Private",
            dependencies=[requires(scopes.GAMES_WRITE)])
def put_game_private(game_id: str, payload: models.PrivateRequest) -> models.Private:
    """Private: never sent to a community service, whoever plays it."""
    return models.Private.model_validate(game_ops.set_private(game_id, payload.private))


def _sent(payload: models.OverridesPatch) -> dict:
    """Only the fields the client actually sent. `None` is "leave alone"; `""` and
    `false` are real values that clear an override."""
    return {name: value for name, value in payload.model_dump().items()
            if value is not None}


@router.put("/{game_id}/overrides", summary="Set a game's overrides",
            dependencies=[requires(scopes.GAMES_WRITE)])
def put_game_overrides(game_id: str,
                       payload: models.OverridesPatch) -> models.GameOverrides:
    return models.GameOverrides.model_validate(
        game_ops.set_overrides(game_id, _sent(payload)))


@router.put("/{game_id}/tables/{table_id}/overrides",
            summary="Set one table's overrides",
            dependencies=[requires(scopes.GAMES_WRITE)])
def put_table_overrides(game_id: str, table_id: str,
                        payload: models.OverridesPatch) -> models.TableOverrides:
    return models.TableOverrides(
        **table_ops.set_overrides(game_id, table_id, _sent(payload)))


@router.delete("/{game_id}/vps_match", summary="Say this game is in no catalog",
               dependencies=[requires(scopes.GAMES_WRITE)])
def delete_game_vps_match(game_id: str) -> models.GameOverrides:
    """Different from clearing the override, which puts the scan's answer back. This says
    there is no answer, and the scan's is not to be used."""
    return models.GameOverrides.model_validate(game_ops.declare_no_match(game_id))


@router.get("/{game_id}/archive", summary="Download the game folder as an archive",
            dependencies=[requires(scopes.GAMES_READ)])
def get_game_archive(request: Request, game_id: str, download_token: str = "",
                     full: bool = False, file: str = "") -> FileResponse:
    if full:
        # The default bundle rides games:read; the whole folder is its own permission.
        # Local trust grants both today.
        identity = getattr(request.state, "identity", None)
        if identity is None or not identity.can(scopes.GAMES_EXPORT_FULL):
            raise ForbiddenError(t("error.auth.needs_scope", scope=scopes.GAMES_EXPORT_FULL))

    archive = archive_service.archive_for(game_id, everything=full, table=file)
    logger.info("Created download archive: %s", archive.path)

    def cleanup() -> None:
        archive_service.cleanup_archive(archive)
        logger.info("Cleaned up temp archive: %s", archive.temp_dir)

    headers = {}
    if download_token and download_token.isalnum():
        # Progress signal for the page that started the download. Not authentication.
        headers["Set-Cookie"] = (
            f"vpinfe_vpxz_download_{download_token}=1; Max-Age=60; Path=/; SameSite=Lax")

    return FileResponse(
        archive.path,
        media_type="application/octet-stream",
        filename=archive.filename,
        headers=headers,
        background=BackgroundTask(cleanup),
    )
