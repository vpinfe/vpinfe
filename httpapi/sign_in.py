"""Signing a browser in: ask for a code, type it, and manage who is signed in.

The code itself is shown on this device and never answered here. See common/security/sign_in.py.
"""

from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from common.config import paths
from common.devices.install_identity import display_name
from common.host import peer_name, this_machine
from common.i18n import t
from common.security import access_keys, authenticator, sign_in
from common.security import api_tokens as tokens

from . import scopes
from .auth import (
    COOKIE,
    caller_address,
    caller_is_encrypted,
    caller_is_local,
    caller_session,
    request_is_https,
    requires,
    session_for,
)
from .errors import CODE_FORBIDDEN, CODE_TOO_MANY_REQUESTS, ApiError
from .models import (
    AuthenticatorCode,
    AuthenticatorEnrolment,
    AuthenticatorState,
    ConfirmAsk,
    ConfirmAttempt,
    ConfirmState,
    PairAsk,
    PairAskBody,
    PairAttempt,
    Paired,
    RemoteCodeRenewed,
    ScreenCodes,
    SessionInfo,
    SessionList,
    SessionsEnded,
    SignedIn,
    SignInAsk,
    SignInAttempt,
    WhoAmI,
)

router = APIRouter(prefix="/auth", tags=["auth"])

# Held by the browser that asked for a code, so another browser at the same address cannot
# take that request or its code.
ASKING = "vpinfe_asking"

_REFUSALS = {
    sign_in.IGNORED: "error.signin.ignored",
    sign_in.NO_CODE: "error.signin.no_code",
    sign_in.WRONG: "error.signin.wrong",
    sign_in.USED_UP: "error.signin.used_up",
    sign_in.EXPIRED: "error.signin.no_code",
    sign_in.NOT_SET: "error.signin.not_set",
    sign_in.SPENT: "error.signin.spent",
    sign_in.CODES_OFF: "error.signin.codes_off",
}


def _device() -> str:
    return display_name(paths.get_ini_config())


def _refusal(error: sign_in.SignInError, *, app: bool = False) -> ApiError:
    if error.reason == sign_in.TOO_SOON:
        said = (t("error.signin.app_locked", count=max(1, -(-error.wait // 60))) if app
                else t("error.signin.too_soon", seconds=error.wait))
        return ApiError(CODE_TOO_MANY_REQUESTS, said,
                        status_code=429, details={"reason": error.reason, "wait": error.wait})
    return ApiError(CODE_FORBIDDEN, t(_REFUSALS[error.reason], device=_device()),
                    status_code=403, details={"reason": error.reason})


def _address(request: Request) -> str:
    return caller_address(request)


def _place(request: Request) -> str:
    return peer_name.place_of(_address(request))


def _guessing_as(request: Request) -> str:
    """Who wrong codes are counted against. A Manager call reaches here over this device's
    own address on behalf of a browser, so the sign-in it acts for is the one to count."""
    session = caller_session(request)
    return f"session:{session.id}" if session else _address(request)


def _same_site(request: Request) -> None:
    if request.headers.get("sec-fetch-site", "").lower() == this_machine.CROSS_SITE:
        raise ApiError(CODE_FORBIDDEN, t("error.signin.cross_site"), status_code=403)


def _asked(request: Request, browser: str) -> SignInAsk:
    signer = sign_in.instance()
    address = _address(request)
    try:
        made = signer.ask(address, browser,
                          agent=sign_in.describe_agent(request.headers.get("user-agent", "")),
                          place=peer_name.place_of(address))
    except sign_in.SignInError as error:
        raise _refusal(error) from error
    return SignInAsk(request_id=made.id, expires_in=signer.seconds_left(made),
                     can_ask_again_in=max(0, int(signer.next_ask_in())))


def _verified(attempt: SignInAttempt, request: Request) -> str:
    try:
        token, _ = sign_in.instance().verify(
            attempt.request_id, attempt.code, address=_address(request),
            browser=request.cookies.get(ASKING, ""),
            agent=sign_in.describe_agent(request.headers.get("user-agent", "")))
    except sign_in.SignInError as error:
        raise _refusal(error) from error
    return token


@router.post("/code", summary="Ask for a sign-in code", response_model=SignInAsk,
             dependencies=[requires(scopes.INSTANCE_READ), Depends(_same_site)])
def ask(request: Request, response: Response) -> SignInAsk:
    """Makes a request. The digits appear only after someone here chooses Show, unless
    nothing here can show it, and this answer holds none. Refused while an authenticator is
    set up and Game Room is not showing sign-in codes."""
    if not authenticator.instance().screen_codes():
        raise _refusal(sign_in.SignInError(sign_in.CODES_OFF))
    browser = request.cookies.get(ASKING) or secrets.token_urlsafe(24)
    asked = _asked(request, browser)
    response.set_cookie(ASKING, browser, max_age=sign_in.CODE_SECONDS, httponly=True,
                        samesite="strict", secure=request_is_https(request), path="/")
    return asked


def _signed_in(token: str, request: Request, response: Response) -> SignedIn:
    """Sets the cookie. A Remote sign-in the browser held is ended: this one replaces it."""
    held = session_for(request)
    if held is not None and held.kind == sign_in.REMOTE:
        sign_in.instance().sign_out(held.id)
    response.set_cookie(COOKIE, token, max_age=sign_in.SESSION_SECONDS, httponly=True,
                        samesite="lax", secure=request_is_https(request), path="/")
    return SignedIn()


@router.post("/session", summary="Sign in with the code this device showed",
             response_model=SignedIn,
             dependencies=[requires(scopes.INSTANCE_READ), Depends(_same_site)])
def sign_in_with_code(attempt: SignInAttempt, request: Request, response: Response) -> SignedIn:
    return _signed_in(_verified(attempt, request), request, response)


@router.get("/me", summary="Whether this browser is signed in", response_model=WhoAmI,
            dependencies=[requires(scopes.INSTANCE_READ)])
def me(request: Request) -> WhoAmI:
    session = session_for(request)
    return WhoAmI(signed_in=session is not None, local=caller_is_local(request),
                  kind=session.kind if session else "",
                  session_id=session.id if session else "",
                  confirmed=session is not None and session.kind == sign_in.OWNER
                  and (caller_is_encrypted(request)
                       or sign_in.instance().is_confirmed(session.id)))


@router.post("/sign_out", summary="Sign this browser out", response_model=SignedIn,
             dependencies=[requires(scopes.INSTANCE_READ), Depends(_same_site)])
def sign_out(request: Request, response: Response) -> SignedIn:
    """Ends the session this request's cookie opens. Signed out already is still signed out."""
    sign_in.instance().sign_out_token(request.cookies.get(COOKIE, ""))
    response.delete_cookie(COOKIE, path="/")
    return SignedIn(signed_in=False)


@router.get("/sessions", summary="The browsers signed in", response_model=SessionList,
            dependencies=[requires(scopes.ACCESS_READ)])
def sessions(request: Request) -> SessionList:
    current = session_for(request)
    rows: list[dict[str, Any]] = [
        {**vars(one), "current": current is not None and current.id == one.id}
        for one in sign_in.instance().sessions()]
    return SessionList(sessions=[SessionInfo(**row) for row in rows])


@router.delete("/sessions/{session_id}", summary="Sign one browser out",
               response_model=SessionsEnded, dependencies=[requires(scopes.ACCESS_WRITE)])
def end_one(session_id: str) -> SessionsEnded:
    return SessionsEnded(ended=1 if sign_in.instance().sign_out(session_id) else 0)


@router.delete("/sessions", summary="Sign every browser out", response_model=SessionsEnded,
               dependencies=[requires(scopes.ACCESS_WRITE)])
def end_all() -> SessionsEnded:
    return SessionsEnded(ended=sign_in.instance().sign_out_everywhere())


@router.post("/confirm/code", summary="Ask for a Confirm Code", response_model=ConfirmAsk,
             dependencies=[requires(scopes.INSTANCE_READ), Depends(_same_site)])
def ask_confirm(request: Request) -> ConfirmAsk:
    """Draws six digits on this device's screen. Only a signed-in Owner can ask: this
    device itself and a token hold no Confirm to ask for."""
    session = caller_session(request)
    if session is None:
        raise ApiError(CODE_FORBIDDEN, t("error.signin.required"), status_code=403)
    try:
        sign_in.instance().confirm_ask(session.id, _address(request), _place(request))
    except sign_in.SignInError as error:
        raise _refusal(error) from error
    return ConfirmAsk(expires_in=sign_in.CODE_SECONDS)


@router.post("/confirm", summary="Confirm with the code this device showed",
             response_model=ConfirmState,
             dependencies=[requires(scopes.INSTANCE_READ), Depends(_same_site)])
def confirm(attempt: ConfirmAttempt, request: Request) -> ConfirmState:
    session = caller_session(request)
    if session is None:
        raise ApiError(CODE_FORBIDDEN, t("error.signin.required"), status_code=403)
    try:
        sign_in.instance().confirm_verify(session.id, attempt.code)
    except sign_in.SignInError as error:
        raise _refusal(error) from error
    return ConfirmState(confirmed=True, expires_in=sign_in.instance().confirm_left(session.id))


@router.get("/confirm", summary="Whether the caller holds a Confirm", response_model=ConfirmState,
            dependencies=[requires(scopes.INSTANCE_READ)])
def confirmed(request: Request) -> ConfirmState:
    if caller_is_local(request) and caller_session(request) is None:
        return ConfirmState(confirmed=True, expires_in=sign_in.CONFIRM_SECONDS)
    session = caller_session(request)
    if session is not None and session.kind == sign_in.OWNER and caller_is_encrypted(request):
        return ConfirmState(confirmed=True, expires_in=sign_in.CONFIRM_SECONDS)
    left = sign_in.instance().confirm_left(session.id) if session else 0
    return ConfirmState(confirmed=left > 0, expires_in=left)


@router.post("/pair/code", summary="Ask this install for a pairing code",
             response_model=PairAsk,
             dependencies=[requires(scopes.INSTANCE_READ), Depends(_same_site)])
def ask_pair(request: Request, body: PairAskBody | None = None) -> PairAsk:
    """Another install wants to be paired.  The six digits appear on this device's screen
    and in no answer."""
    try:
        made = sign_in.instance().pair_ask(_address(request), body.name if body else "",
                                           _place(request))
    except sign_in.SignInError as error:
        raise _refusal(error) from error
    return PairAsk(request_id=made.id, expires_in=sign_in.CODE_SECONDS)


@router.post("/pair", summary="Pair with the code this install showed", response_model=Paired,
             dependencies=[requires(scopes.INSTANCE_READ), Depends(_same_site)])
def pair(attempt: PairAttempt) -> Paired:
    """Exchanges the code read off this screen for a Full Control token, shown here once."""
    try:
        sign_in.instance().pair_verify(attempt.request_id, attempt.code)
    except sign_in.SignInError as error:
        raise _refusal(error) from error
    name = attempt.name.strip() or t("auth.tokens.paired_name")
    secret, _ = tokens.instance().make(name, tokens.FULL)
    return Paired(secret=secret)


@router.get("/authenticator", summary="Whether an authenticator app is set up",
            response_model=AuthenticatorState, dependencies=[requires(scopes.ACCESS_READ)])
def authenticator_state() -> AuthenticatorState:
    held = authenticator.instance()
    return AuthenticatorState(enabled=held.enabled(), screen_codes=held.screen_codes())


@router.post("/authenticator/setup", summary="Start setting up an authenticator app",
             response_model=AuthenticatorEnrolment, dependencies=[requires(scopes.PROTECTED)])
def begin_authenticator() -> AuthenticatorEnrolment:
    """A new secret for the app to learn. The one in force keeps working until a code from
    the new one is accepted, so setting up again after a lost phone loses nothing."""
    made = authenticator.instance().begin(_device())
    return AuthenticatorEnrolment(secret=made.secret, uri=made.uri)


@router.put("/authenticator", summary="Finish setting up with the app's first code",
            response_model=AuthenticatorState, dependencies=[requires(scopes.PROTECTED)])
def finish_authenticator(attempt: AuthenticatorCode, request: Request) -> AuthenticatorState:
    try:
        authenticator.instance().activate(attempt.code, _guessing_as(request))
    except sign_in.SignInError as error:
        raise _refusal(error, app=True) from error
    return authenticator_state()


@router.delete("/authenticator", summary="Remove the authenticator app",
               response_model=AuthenticatorState, dependencies=[requires(scopes.PROTECTED)])
def remove_authenticator() -> AuthenticatorState:
    authenticator.instance().remove()
    return authenticator_state()


@router.put("/authenticator/screen_codes",
            summary="Show or stop showing sign-in codes on this device's screen",
            response_model=AuthenticatorState, dependencies=[requires(scopes.PROTECTED)])
def set_screen_codes(body: ScreenCodes) -> AuthenticatorState:
    try:
        authenticator.instance().set_screen_codes(body.on)
    except sign_in.SignInError as error:
        raise _refusal(error) from error
    return authenticator_state()


@router.post("/authenticator/session", summary="Sign in with the authenticator app",
             response_model=SignedIn,
             dependencies=[requires(scopes.INSTANCE_READ), Depends(_same_site)])
def sign_in_with_authenticator(attempt: AuthenticatorCode, request: Request,
                               response: Response) -> SignedIn:
    """Six digits from the app sign in from any address that has not been ignored. A step is
    spent once used."""
    address = _address(request)
    if sign_in.instance().is_ignored(address):
        raise _refusal(sign_in.SignInError(sign_in.IGNORED))
    _spend(attempt, address)
    token, _ = sign_in.instance().open(
        kind=sign_in.OWNER, address=address,
        agent=sign_in.describe_agent(request.headers.get("user-agent", "")))
    return _signed_in(token, request, response)


def _spend(attempt: AuthenticatorCode, address: str) -> None:
    try:
        authenticator.instance().verify(attempt.code, address)
    except sign_in.SignInError as error:
        raise _refusal(error, app=True) from error


@router.post("/authenticator/confirm", summary="Confirm with the authenticator app",
             response_model=ConfirmState,
             dependencies=[requires(scopes.INSTANCE_READ), Depends(_same_site)])
def confirm_with_authenticator(attempt: AuthenticatorCode, request: Request) -> ConfirmState:
    session = caller_session(request)
    if session is None or session.kind != sign_in.OWNER:
        raise ApiError(CODE_FORBIDDEN, t("error.signin.required"), status_code=403)
    _spend(attempt, _guessing_as(request))
    signer = sign_in.instance()
    signer.confirm_grant(session.id)
    return ConfirmState(confirmed=True, expires_in=signer.confirm_left(session.id))


@router.post("/remote_code/renew", summary="Make a new Remote Code",
             response_model=RemoteCodeRenewed, dependencies=[requires(scopes.ACCESS_WRITE)])
def renew_remote_code() -> RemoteCodeRenewed:
    """Every mobile device that came in with the old code is signed out."""
    old_id, _ = access_keys.renew_remote_pass()
    return RemoteCodeRenewed(ended=sign_in.instance().sign_out_via(old_id))
