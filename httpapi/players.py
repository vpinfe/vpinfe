"""Who plays on this install, and who the next game counts for.

`common/players/players.py` is what answers. A write that sets who is up answers with the whole
roster.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, File, Request, Response, UploadFile
from starlette.concurrency import run_in_threadpool

from common import events
from common.games import game_lens, player_records
from common.games.player_records import get_records
from common.players.players import get_roster

from . import models, scopes
from .auth import require, requires
from .events import declare_snapshot
from .responses import MEDIA_SIZE, MEDIA_VERSION, media_file

router = APIRouter(prefix="/players", tags=["players"])


def _roster() -> models.PlayerRoster:
    return models.PlayerRoster.model_validate(get_roster().state())


def _player(player_id: str) -> models.PlayerResource:
    return models.PlayerResource.model_validate(get_roster().player_state(player_id))


@router.get("", summary="Every player, and who is up",
            dependencies=[requires(scopes.PLAYERS_READ)])
def list_players() -> models.PlayerRoster:
    return _roster()


@router.post("", summary="Add a player", status_code=201,
             dependencies=[requires(scopes.PLAYERS_WRITE)])
def add_player(body: models.PlayerAddRequest = Body(...)) -> models.PlayerResource:
    return _player(get_roster().add_player(body.name, body.initials).player_id)


@router.post("/guests", summary="Add a guest from their initials", status_code=201,
             dependencies=[requires(scopes.PLAY_PLAYERS)])
def add_guest(body: models.GuestAddRequest = Body(...)) -> models.PlayerResource:
    return _player(get_roster().add_guest(body.initials, body.name).player_id)


@router.put("/up", summary="Say who is up",
            dependencies=[requires(scopes.PLAY_PLAYERS)])
def set_who_is_up(body: models.PlayersUpRequest = Body(...)) -> models.PlayerRoster:
    get_roster().set_who_is_up(body.ids)
    return _roster()


@router.get("/{player_id}", summary="One player",
            dependencies=[requires(scopes.PLAYERS_READ)])
def get_player(player_id: str) -> models.PlayerResource:
    return _player(player_id)


@router.patch("/{player_id}", summary="Rename a player, or change their initials",
              dependencies=[requires(scopes.PLAYERS_WRITE)])
def change_player(player_id: str,
                  body: models.PlayerChangeRequest = Body(...)) -> models.PlayerResource:
    get_roster().update_player(player_id, name=body.name, initials=body.initials)
    return _player(player_id)


@router.put("/{player_id}/up", summary="Put one player up, or take them down",
            dependencies=[requires(scopes.PLAY_PLAYERS)])
def set_up(player_id: str,
           body: models.PlayerUpRequest = Body(...)) -> models.PlayerRoster:
    get_roster().set_up(player_id, body.up)
    return _roster()


@router.get("/{player_id}/picture", summary="A player's picture",
            dependencies=[requires(scopes.PLAYERS_READ)])
def get_picture(player_id: str, request: Request, size: int | None = MEDIA_SIZE,
                v: str = MEDIA_VERSION) -> Response:
    return media_file(get_roster().picture(player_id, size), request, v)


@router.put("/{player_id}/picture", summary="Set a player's picture",
            dependencies=[requires(scopes.PLAYERS_WRITE)])
async def set_picture(player_id: str,
                      file: UploadFile = File(...)) -> models.PlayerResource:
    """Cropped to the centre square and kept at most 1024 pixels. A guest has none."""
    content = await file.read()
    await run_in_threadpool(get_roster().set_picture, player_id, file.filename or "", content)
    return _player(player_id)


@router.delete("/{player_id}/picture", summary="Clear a player's picture",
               dependencies=[requires(scopes.PLAYERS_WRITE)])
def clear_picture(player_id: str) -> models.PlayerResource:
    get_roster().clear_picture(player_id)
    return _player(player_id)


@router.get("/{player_id}/record", summary="What a player has done with each game",
            dependencies=[requires(scopes.PLAYERS_READ)])
def get_record(player_id: str) -> models.PlayerRecord:
    """Not the owner's, which is the library's."""
    return models.PlayerRecord.model_validate(
        get_records().view(get_roster().player(player_id)))


@router.put("/{player_id}/ratings/{game_id}", summary="A player's rating of a game",
            dependencies=[requires(scopes.PLAY_RATE)])
def put_rating(player_id: str, game_id: str,
               payload: models.RatingRequest = Body(...)) -> models.Rating:
    """Not the owner's, which `PUT /games/{id}/rating` writes."""
    player = get_roster().player(player_id)
    game_lens.game_or_refuse(game_id)
    return models.Rating(rating=get_records().set_rating(player, game_id, payload.rating))


@router.delete("/{player_id}", summary="Remove a player, or sign a guest out",
               status_code=204, dependencies=[requires(scopes.PLAY_PLAYERS)])
def remove_player(request: Request, player_id: str) -> Response:
    """Their accounts go with them, from every extension, and so does their record.
    Signing a guest out is Play; removing a kept player is `players:write`."""
    if not get_roster().player(player_id).guest:
        require(request, scopes.PLAYERS_WRITE)
    player_records.remove_player(player_id)
    return Response(status_code=204)


def declare_snapshots() -> None:
    declare_snapshot(events.PLAYERS_CHANGED, lambda: {"state": get_roster().state()})
