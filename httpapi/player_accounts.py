"""A player's accounts, their Share, and their cards.

Each account is the extension's: its fields, status and acts come from its own routes,
asked through `extensions.ask` with the player's id. What is core's here is Share, the
card's drawing, and that a secret never comes back.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Body, Request, Response
from starlette.concurrency import run_in_threadpool

from common import service_errors
from common.extensions import accounts, cards
from common.failures import why
from common.i18n import t
from common.players.players import get_roster

from . import models, scopes
from .auth import requires
from .errors import ApiError, NotFoundError
from .extensions import ask, ask_file

router = APIRouter(prefix="/players", tags=["players"])

SVG = "image/svg+xml"
_PICTURE_SUFFIX = {"image/png": ".png", "image/jpeg": ".jpg", "image/jpg": ".jpg",
                   "image/webp": ".webp", "image/gif": ".gif", "image/bmp": ".bmp",
                   "image/x-ms-bmp": ".bmp"}
_UNSAFE_IN_A_NAME = re.compile(r"[^A-Za-z0-9_-]")


@router.get("/{player_id}/accounts", summary="Every account this player can hold here",
            dependencies=[requires(scopes.PLAYERS_READ)])
async def list_accounts(player_id: str, request: Request) -> models.PlayerAccounts:
    """One per running extension that offers an account. One that cannot answer is
    listed with `error` rather than failing the rest."""
    get_roster().player_state(player_id)
    return models.PlayerAccounts(accounts=[await _read(request, player_id, declared)
                                           for declared in accounts.declared()])


@router.get("/{player_id}/accounts/{extension}", summary="One of a player's accounts",
            dependencies=[requires(scopes.PLAYERS_READ)])
async def get_account(player_id: str, extension: str,
                      request: Request) -> models.PlayerAccount:
    declared = _declared(player_id, extension)
    return _shaped(declared, player_id, await ask(request, extension, "GET",
                                                  _at(declared, player_id)))


@router.put("/{player_id}/accounts/{extension}", summary="Change a player's account",
            dependencies=[requires(scopes.PLAYERS_WRITE)])
async def put_account(player_id: str, extension: str, request: Request,
                      body: models.AccountValuesRequest = Body(...)) -> models.PlayerAccount:
    declared = _declared(player_id, extension)
    said = await ask(request, extension, "PUT", _at(declared, player_id),
                     {"values": body.values})
    return _shaped(declared, player_id, said)


@router.put("/{player_id}/accounts/{extension}/share",
            summary="Say whether a player's account shares",
            dependencies=[requires(scopes.PLAYERS_WRITE)])
async def put_share(player_id: str, extension: str, request: Request,
                    body: models.AccountShareRequest = Body(...)) -> models.PlayerAccount:
    declared = _declared(player_id, extension)
    get_roster().set_sharing(player_id, extension, body.share)
    return await _read(request, player_id, declared)


@router.post("/{player_id}/accounts/{extension}/acts/{act}",
             summary="Do one of an account's acts",
             dependencies=[requires(scopes.PLAYERS_WRITE)])
async def run_act(player_id: str, extension: str, act: str,
                  request: Request) -> dict[str, Any]:
    """Answers what the extension said came of it."""
    declared = _declared(player_id, extension)
    said = await ask(request, extension, "POST",
                     f"{_at(declared, player_id)}/acts/{quote(act, safe='')}", {})
    return accounts.scrubbed(said) if isinstance(said, dict) else {}


@router.get("/{player_id}/accounts/{extension}/card", summary="A player's card",
            response_class=Response, dependencies=[requires(scopes.PLAYERS_READ)])
async def get_card(player_id: str, extension: str, request: Request) -> Response:
    """The card as an SVG file. A 404 when the extension cannot make this player one."""
    declared = _declared(player_id, extension)
    said = await ask(request, extension, "GET", f"{_at(declared, player_id)}/card")
    return _card_file(declared, said)


@router.post("/{player_id}/accounts/{extension}/card",
             summary="Use a card for one of a player's accounts",
             dependencies=[requires(scopes.PLAYERS_WRITE)])
async def use_card(player_id: str, extension: str, request: Request,
                   body: models.GuestCardRequest = Body(...)) -> models.PlayerAccount:
    """The card's values replace the account's. The player's name, initials and Share
    stay as they are."""
    get_roster().player_state(player_id)
    card = cards.read(body.card)
    declared = accounts.reading(extension, card)
    said = await ask(request, extension, "POST", f"{declared['base']}/cards", {"card": card})
    values = dict((said.get("values") if isinstance(said, dict) else None) or {})
    written = await ask(request, extension, "PUT", _at(declared, player_id), {"values": values})
    return _shaped(declared, player_id, written)


@router.post("/{player_id}/accounts/{extension}/picture",
             summary="Use the service's picture as the player's",
             dependencies=[requires(scopes.PLAYERS_WRITE)])
async def use_picture(player_id: str, extension: str,
                      request: Request) -> models.PlayerResource:
    """Copies the picture the extension answers at `{base}/{player_id}/picture` once, as
    an upload of it would be kept. It is never followed: pressing it again takes the
    service's picture as it is then. A 404 when the service has none."""
    await _copy_picture(request, player_id, extension)
    return models.PlayerResource.model_validate(get_roster().player_state(player_id))


async def _copy_picture(request: Request, player_id: str, extension: str) -> None:
    """Asked only for a kept player: a guest is refused before the service is."""
    declared = _declared(player_id, extension)
    if get_roster().player(player_id).guest:
        raise service_errors.RefusedError(t("error.players.picture_guest"))
    found = await ask_file(request, extension, f"{_at(declared, player_id)}/picture")
    if found is None:
        raise NotFoundError(t("error.players.no_service_picture", extension=declared["label"]))
    served, content = found
    await run_in_threadpool(get_roster().set_picture, player_id,
                            f"picture{_PICTURE_SUFFIX.get(served, '')}", content)


@router.post("/guests/card", summary="Add a guest from their card", status_code=201,
             dependencies=[requires(scopes.PLAY_PLAYERS)])
async def add_guest_from_card(request: Request, body: models.GuestCardRequest = Body(...),
                              ) -> models.PlayerResource:
    """The extension that reads this kind of card says who it is. Joining again with the
    same card puts that guest up alone."""
    card = cards.read(body.card)
    declared = accounts.claiming(card)
    said = await ask(request, declared["extension"], "POST",
                     f"{declared['base']}/cards", {"card": card})
    guest = accounts.join(declared["extension"], said if isinstance(said, dict) else {})
    return models.PlayerResource.model_validate(get_roster().player_state(guest.player_id))


# -- shaping -------------------------------------------------------------------

def _declared(player_id: str, extension: str) -> dict[str, Any]:
    get_roster().player_state(player_id)
    return accounts.declared_by(extension)


def _at(declared: dict[str, Any], player_id: str) -> str:
    return f"{declared['base']}/{quote(player_id, safe='')}"


async def _read(request: Request, player_id: str,
                declared: dict[str, Any]) -> models.PlayerAccount:
    try:
        said = await ask(request, declared["extension"], "GET", _at(declared, player_id))
    except ApiError as exc:
        return models.PlayerAccount(
            extension=declared["extension"], label=declared["label"], error=why(exc),
            share=get_roster().sharing(player_id, declared["extension"]),
            share_help=str(declared.get("share_help") or ""),
            consent=[str(one) for one in declared.get("consent") or []],
            check=str(declared.get("check") or ""),
            reads_cards=bool(declared.get("cards")))
    return _shaped(declared, player_id, said)


def _shaped(declared: dict[str, Any], player_id: str, said: Any) -> models.PlayerAccount:
    answer = accounts.scrubbed(said) if isinstance(said, dict) else {}
    acts = [one for one in answer.get("acts") or [] if isinstance(one, dict)
            and str(one.get("key") or "").strip()]
    return models.PlayerAccount(
        extension=declared["extension"], label=declared["label"],
        share=get_roster().sharing(player_id, declared["extension"]),
        share_help=str(declared.get("share_help") or ""),
        consent=[str(one) for one in declared.get("consent") or []],
        check=str(declared.get("check") or ""),
        user_id=str(answer.get("user_id") or ""), claimed=bool(answer.get("claimed")),
        needs_consent=bool(answer.get("needs_consent")),
        page=str(answer.get("page") or ""),
        fields=[one for one in answer.get("fields") or [] if isinstance(one, dict)],
        status=str(answer.get("status") or ""),
        waiting=bool(answer.get("waiting")), waiting_count=int(answer.get("waiting_count") or 0),
        acts=[models.AccountAct(key=str(one["key"]),
                                label=str(one.get("label") or one["key"]),
                                description=str(one.get("description") or ""))
              for one in acts],
        card=bool(answer.get("card")), picture=bool(answer.get("picture")),
        reads_cards=bool(declared.get("cards")))


def _card_file(declared: dict[str, Any], said: Any) -> Response:
    card = said.get("card") if isinstance(said, dict) else None
    if not isinstance(card, dict):
        raise NotFoundError(t("error.players.no_card", extension=declared["label"]))
    stem = _UNSAFE_IN_A_NAME.sub("_", str(said.get("filename") or "").strip())
    name = f"{stem or declared['marker'] + '-card'}.svg"
    return Response(cards.drawn(card, declared["marker"]), media_type=SVG,
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})
