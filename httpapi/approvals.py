"""Asking first: a program with a Full Control token asks the Owner to approve one change.

It names the request, the Owner decides on Needs You or on this device's screen, and the
program then repeats that one request with the approval's id. See common/security/approvals.py.
Everything under /auth is out of an ask's reach, and these routes are under it.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request

from common.i18n import t
from common.security import approvals

from . import scopes
from .auth import ForbiddenError, request_scope, requires
from .errors import (
    CODE_TOO_MANY_REQUESTS,
    ApiError,
    InvalidRequestError,
    NotFoundError,
)
from .models import AskInfo, AskList, AskMake

router = APIRouter(prefix="/auth/asks", tags=["auth"])

# Out of an ask's reach: making a token, Trusted Networks, Trusted Proxies and the
# authenticator all live under it, and so do these routes.
NEVER_ASKED = "/auth"

def can_ask_for(method: str, path: str) -> str:
    """Why this request cannot be asked for, as a catalog key, or "" when it can."""
    only = path.partition("?")[0]
    if method.upper() not in approvals.METHODS:
        return "error.approvals.method"
    if "%" in only or not only.startswith("/") or only == NEVER_ASKED \
            or only.startswith(NEVER_ASKED + "/"):
        return "error.approvals.never"
    if request_scope(method, only) is None:
        return "error.approvals.unknown"
    return ""


def _view(ask: approvals.Ask) -> AskInfo:
    body: Any = json.loads(ask.body) if ask.body else None
    return AskInfo(id=ask.id, token=ask.token_name, method=ask.method, path=ask.path,
                   body=body, state=ask.state,
                   expires_in=approvals.instance().seconds_left(ask))


def _the_token(request: Request) -> Any:
    identity = getattr(request.state, "identity", None)
    if identity is None or identity.kind != "token":
        raise ForbiddenError(t("error.approvals.token_only"))
    return identity


def _own(request: Request, ask_id: str) -> approvals.Ask:
    identity = _the_token(request)
    found = approvals.instance().get(ask_id)
    if found is None or found.token_id != identity.token_id:
        raise NotFoundError(t("error.approvals.gone"))
    return found


def _asked(identity: Any, body: AskMake) -> approvals.Ask:
    refused = can_ask_for(body.method, body.path)
    if refused:
        raise InvalidRequestError(t(refused))
    try:
        return approvals.instance().ask(identity.token_id, identity.name,
                                        body.method, body.path, body.body)
    except approvals.TooManyError as error:
        raise ApiError(CODE_TOO_MANY_REQUESTS, t("error.approvals.too_many"),
                       status_code=429) from error
    except approvals.TooBigError as error:
        raise InvalidRequestError(t("error.approvals.too_big")) from error


@router.post("", summary="Ask the Owner to approve one change", response_model=AskInfo,
             dependencies=[requires(scopes.CONFIG_WRITE)])
def ask(body: AskMake, request: Request) -> AskInfo:
    """Only a Full Control token can ask. Nothing is held for later: once approved, repeat
    this exact request with the id in the X-VPinFE-Approval header."""
    return _view(_asked(_the_token(request), body))


@router.get("", summary="The changes waiting for the Owner", response_model=AskList,
            dependencies=[requires(scopes.ACCESS_READ)])
def waiting() -> AskList:
    return AskList(asks=[_view(one) for one in approvals.instance().waiting()])


@router.get("/{ask_id}", summary="Whether an ask has been answered", response_model=AskInfo,
            dependencies=[requires(scopes.CONFIG_WRITE)])
def read(ask_id: str, request: Request) -> AskInfo:
    return _view(_own(request, ask_id))


@router.delete("/{ask_id}", summary="Take an ask back", response_model=AskInfo,
               dependencies=[requires(scopes.CONFIG_WRITE)])
def withdraw(ask_id: str, request: Request) -> AskInfo:
    own = _own(request, ask_id)
    approvals.instance().decline(own.id, token_id=own.token_id)
    return _view(approvals.instance().get(own.id) or own)


@router.post("/{ask_id}/approve", summary="Approve an ask", response_model=AskInfo,
             dependencies=[requires(scopes.PROTECTED)])
def approve(ask_id: str) -> AskInfo:
    answered = approvals.instance().approve(ask_id)
    if answered is None:
        raise NotFoundError(t("error.approvals.gone"))
    return _view(answered)


@router.post("/{ask_id}/decline", summary="Decline an ask", response_model=AskInfo,
             dependencies=[requires(scopes.ACCESS_WRITE)])
def decline(ask_id: str) -> AskInfo:
    answered = approvals.instance().decline(ask_id)
    if answered is None:
        raise NotFoundError(t("error.approvals.gone"))
    return _view(answered)
