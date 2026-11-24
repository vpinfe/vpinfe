"""Tokens: what a program holds to call this API.

Making one is Protected, since it hands out access.  Listing and revoking need no Confirm:
a list is not a secret, and taking access back is never the dangerous direction.  Switching
on Can Shut Down and Restart is a hand-out, so it is Protected too.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from common.i18n import t
from common.security import api_tokens as tokens
from common.security import approvals

from . import scopes
from .auth import require, requires
from .errors import InvalidRequestError, NotFoundError
from .models import TokenChange, TokenInfo, TokenList, TokenMade, TokenMake, TokensEnded

router = APIRouter(prefix="/auth/tokens", tags=["auth"])


def _info(token: tokens.Token) -> TokenInfo:
    return TokenInfo(id=token.id, name=token.name, access=token.access,
                     can_power=token.can_power, created=token.created,
                     last_used=token.last_used)


@router.get("", summary="The tokens made", response_model=TokenList,
            dependencies=[requires(scopes.ACCESS_READ)])
def list_tokens() -> TokenList:
    return TokenList(tokens=[_info(one) for one in tokens.instance().tokens()])


@router.post("", summary="Make a token", response_model=TokenMade,
             dependencies=[requires(scopes.PROTECTED)])
def make_token(body: TokenMake) -> TokenMade:
    """The secret is in this answer and cannot be read again."""
    name = body.name.strip()
    if not name:
        raise InvalidRequestError(t("error.tokens.needs_name"))
    if body.access not in tokens.ACCESS:
        raise InvalidRequestError(t("error.tokens.access"))
    secret, token = tokens.instance().make(name, body.access, can_power=body.can_power)
    return TokenMade(secret=secret, token=_info(token))


@router.put("/{token_id}", summary="Change what a token may do", response_model=TokenInfo,
            dependencies=[requires(scopes.ACCESS_WRITE)])
def change_token(token_id: str, body: TokenChange, request: Request) -> TokenInfo:
    if body.can_power:
        require(request, scopes.PROTECTED)
    changed = tokens.instance().set_power(token_id, body.can_power)
    if changed is None:
        raise NotFoundError(t("error.tokens.gone"))
    return _info(changed)


@router.delete("/{token_id}", summary="Revoke a token", response_model=TokensEnded,
               dependencies=[requires(scopes.ACCESS_WRITE)])
def revoke_token(token_id: str) -> TokensEnded:
    ended = tokens.instance().revoke(token_id)
    approvals.instance().forget_token(token_id)
    return TokensEnded(ended=1 if ended else 0)

