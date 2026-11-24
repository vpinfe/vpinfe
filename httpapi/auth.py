"""The authorization boundary: identity in, declared scope enforced.

Who a request is comes from `SignInPolicy`. See docs/http-api.md.
"""

from __future__ import annotations

import hmac
import logging
import re
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, replace
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.routing import APIRoute
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import HTTPConnection
from starlette.responses import Response
from starlette.routing import BaseRoute, compile_path

from common import extensions
from common.extensions import reach
from common.host import this_machine
from common.i18n import t
from common.security import api_tokens as tokens
from common.security import approvals, encrypted_connections, machine_secrets, sign_in

from . import scopes
from .errors import CODE_FORBIDDEN, ApiError, error_response

logger = logging.getLogger("vpinfe.httpapi.auth")

_SCOPE_ATTR = "__vpinfe_scope__"

COOKIE = "vpinfe_session"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# The Manager's own calls to this API are made over loopback for a browser that may be on
# another device. This header says which sign-in a call is made for, so the API holds it
# to that sign-in's tier. Only a loopback caller holding the machine secret can sign one.
ACTING_HEADER = "x-vpinfe-acting"
ACTING_SECONDS = 60


# Where a caller reached us from, recorded so a policy has the fact to hand.
LOCAL = "local"
NETWORK = "network"

# The reasons a refusal carries in `details`, for a surface to act on rather than parse.
CONFIRM_NEEDED = "confirm_needed"
PROTECTED = "protected"
APPROVAL_INVALID = "approval_invalid"

# A token repeats a request with the id of the ask the Owner approved.
APPROVAL_HEADER = "x-vpinfe-approval"


@dataclass(frozen=True)
class Identity:
    name: str
    scopes: frozenset[str]
    # Defaults to the cautious answer: an identity that forgets to say must not
    # silently claim to be on the machine.
    origin: str = NETWORK
    # The sign-in a browser's cookie opened, when it was one.
    session_id: str = ""
    kind: str = ""
    token_id: str = ""

    def can(self, scope: str) -> bool:
        return scope in self.scopes

    @property
    def is_local(self) -> bool:
        return self.origin == LOCAL

    @property
    def tier(self) -> str:
        """How far this caller reaches: what an extension is told it may change."""
        if self.can(scopes.PROTECTED):
            return reach.PROTECTED
        return reach.LIBRARY if self.can(scopes.CONFIG_WRITE) else reach.PLAY


def _peer(request: HTTPConnection) -> str | None:
    client = getattr(request, "client", None)
    return getattr(client, "host", None) or None


def _port(request: HTTPConnection) -> int | None:
    server = (getattr(request, "scope", None) or {}).get("server")
    return server[1] if server and len(server) > 1 else None


def caller_is_local(request: HTTPConnection) -> bool:
    """Whether the request is this machine's own: its socket, a name it answers to, no
    forwarding header, and a page of VPinFE's own when a browser sent it. No client at all
    is an in-process call rather than a socket, so there is no remote party to be cautious
    about.
    """
    peer = _peer(request)
    if peer is None:
        return True
    return this_machine.request_is_this_machine(peer, request.headers, _port(request))


def caller_address(request: HTTPConnection) -> str:
    """Who the request came from, as a person would name it: the client a listed proxy
    forwarded for, else the socket's own address."""
    return encrypted_connections.forwarded_for(_peer(request), request.headers)


def caller_is_encrypted(request: HTTPConnection) -> bool:
    """Whether the request arrived over HTTPS, through a listed proxy that says it did, or
    from inside a Trusted Network. A Manager call made for a browser on another device
    says whether that browser's page came in over one."""
    claim = _acting_claim(request)
    if claim is not None:
        return claim[1]
    scheme = getattr(getattr(request, "url", None), "scheme", "http")
    return encrypted_connections.is_encrypted(_peer(request), request.headers, scheme)


def request_is_https(request: HTTPConnection) -> bool:
    """Whether the browser spoke HTTPS to this server or to a proxy listed for it, for a
    cookie that should only travel that way. A Trusted Network says nothing about the
    scheme."""
    scheme = getattr(getattr(request, "url", None), "scheme", "http")
    return scheme == "https" or encrypted_connections.said_https(_peer(request), request.headers)


def session_for(request: HTTPConnection) -> sign_in.Session | None:
    """The sign-in the request's cookie opens. A write the browser says another site began
    does not count, so a page of someone else's cannot act with a cookie it never held."""
    token = request.cookies.get(COOKIE, "")
    if not token:
        return None
    if (getattr(request, "method", "GET") not in SAFE_METHODS
            and request.headers.get("sec-fetch-site", "").lower() == "cross-site"):
        return None
    return sign_in.instance().lookup(token)


# What a Manager page open in a browser that is no longer signed in says it acts for. It names
# no sign-in, so the call is held to a stranger's tier rather than this device's.
NOBODY = "-"


def sign_acting(session_id: str, encrypted: bool = False) -> str:
    """The header value saying a loopback call is made for this sign-in, or for nobody, and
    whether the browser it is made for reached this page over an encrypted connection."""
    body = f"{session_id}.{int(time.time()) + ACTING_SECONDS}.{int(encrypted)}"
    return f"{body}.{_acting_mac(body)}"


def _acting_mac(body: str) -> str:
    return hmac.new(machine_secrets.session_secret().encode(), body.encode(),
                    "sha256").hexdigest()


def _acting_claim(request: HTTPConnection) -> tuple[str, bool] | None:
    headers = getattr(request, "headers", None)
    value = headers.get(ACTING_HEADER, "") if headers is not None else ""
    if not value or not caller_is_local(request):
        return None
    session_id, _, rest = value.partition(".")
    expires, _, rest = rest.partition(".")
    encrypted, _, mac = rest.partition(".")
    if (not session_id or not expires.isdigit() or int(expires) < time.time()
            or encrypted not in ("0", "1")):
        return None
    if not hmac.compare_digest(_acting_mac(f"{session_id}.{expires}.{encrypted}"), mac):
        return None
    return session_id, encrypted == "1"


def acting_for(request: HTTPConnection) -> str | None:
    """Who a loopback call says it is made for: a sign-in's id, `NOBODY`, or None when it
    says nothing, or says something that does not hold: a header from anywhere but this
    device, a stale one, one signed with another secret. `identify` tells those two apart."""
    claim = _acting_claim(request)
    return claim[0] if claim else None


def acting_session(request: HTTPConnection) -> sign_in.Session | None:
    """The sign-in a loopback call says it is made for, if that sign-in still exists."""
    claimed = acting_for(request)
    return sign_in.instance().session(claimed) if claimed else None


def caller_session(request: HTTPConnection) -> sign_in.Session | None:
    """The sign-in whose tier this request is held to: the one a Manager call says it acts
    for, else the one its own cookie opens."""
    return acting_session(request) or session_for(request)


def bearer_token(request: HTTPConnection) -> str:
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    return value.strip() if scheme.lower() == "bearer" else ""


class SignInPolicy:
    """This machine is trusted; another device is trusted once a code shown on this
    machine has signed its browser in, or when it holds a token. Anyone else may only ask what
    this instance is.

    `origin` records where the caller came from: a caller binds every interface by default,
    so "local" and "reachable" have never been the same question.
    """

    name = "sign-in"

    def identify(self, request: Request) -> Identity:
        everything = scopes.CORE | granted_extension_scopes()
        if caller_is_local(request):
            claimed = acting_for(request)
            if claimed is None:
                if (getattr(request, "headers", None) or {}).get(ACTING_HEADER):
                    return self._stranger()
                return Identity(name="local", scopes=everything, origin=LOCAL)
            acting = sign_in.instance().session(claimed)
            return self._signed_in(acting, everything, caller_is_encrypted(request)) \
                if acting is not None else self._stranger()
        bearer = bearer_token(request)
        if bearer:
            token = tokens.instance().lookup(bearer)
            return self._holder(token) if token is not None else self._stranger()
        session = session_for(request)
        return self._signed_in(session, everything, caller_is_encrypted(request)) \
            if session is not None else self._stranger()

    @staticmethod
    def _stranger() -> Identity:
        return Identity(name="network", scopes=frozenset({scopes.INSTANCE_READ}),
                        origin=NETWORK)

    @staticmethod
    def _signed_in(session: sign_in.Session, everything: frozenset[str],
                   encrypted: bool = False) -> Identity:
        """A Remote pass is Play and nothing more.  An Owner holds everything but the Protected
        tier, which a live Confirm adds, and an encrypted connection stands in for."""
        if session.kind != sign_in.OWNER:
            held = scopes.PLAY | granted_extension_scopes(reach.PLAY)
        elif encrypted or sign_in.instance().is_confirmed(session.id):
            held = everything
        else:
            held = frozenset(one for one in everything if not scopes.is_confirmed(one))
        return Identity(name=session.kind, scopes=frozenset(held), origin=NETWORK,
                        session_id=session.id, kind=session.kind)

    @staticmethod
    def _holder(token: tokens.Token) -> Identity:
        if token.access == tokens.PLAY:
            held = scopes.PLAY | granted_extension_scopes(reach.PLAY)
        else:
            held = scopes.LIBRARY | granted_extension_scopes(reach.PLAY, reach.LIBRARY)
            if token.can_power:
                held = held | {scopes.SYSTEM_ADMIN}
        return Identity(name=token.name, scopes=frozenset(held), origin=NETWORK,
                        kind="token", token_id=token.id)


def granted_extension_scopes(*tiers: str) -> frozenset[str]:
    """The `ext:` scopes of the extensions that are running, at these tiers or at all of
    them when none is named.

    Read per request, not held: disabling an extension takes its scopes with it, which
    is how the kill switch reaches a route that is already mounted.
    """
    held = extensions.granted_scopes()
    if not tiers:
        return held
    return frozenset(one for one in held if scopes.extension_tier(one) in tiers)


_policy: SignInPolicy = SignInPolicy()


def current_policy() -> SignInPolicy:
    return _policy


def set_policy(policy: SignInPolicy) -> None:
    """Swap the policy. For tests, and for whenever the deployment stops being local."""
    global _policy
    _policy = policy


def _is_api_description(request: Request) -> bool:
    """Whether the request is for the generated spec or its explorer page, which FastAPI
    serves outside any route that could declare a scope."""
    app = request.scope.get("app")
    served = [str(url) for url in (getattr(app, "openapi_url", None),
                                   getattr(app, "docs_url", None),
                                   getattr(app, "swagger_ui_oauth2_redirect_url", None)) if url]
    path = request.url.path
    root = request.scope.get("root_path", "")
    if root and path.startswith(root):
        path = path[len(root):]
    return path.rstrip("/") in served


def _relative_path(request: Request) -> str:
    """The path and query as an ask names them: relative to the API."""
    path = request.url.path
    root = request.scope.get("root_path", "")
    if root and path.startswith(root):
        path = path[len(root):]
    return f"{path}?{request.url.query}" if request.url.query else path


async def _approved(request: Request, identity: Identity, approval_id: str) -> Identity | None:
    """The token's identity with the Protected tier added for this one request, or None
    when the approval is not for this exact request. A match spends the approval."""
    # A chunked body has no length to check before reading it, and an approved change is
    # never one this large, so only a declared length is accepted.
    declared = request.headers.get("content-length", "")
    if "transfer-encoding" in request.headers or (
            declared.isdigit() and int(declared) > approvals.MAX_BODY_CHARS * 4):
        return None
    body = approvals.canonical_bytes(await request.body())
    if body is None or not approvals.instance().redeem(
            approval_id, identity.token_id, request.method, _relative_path(request), body):
        return None
    return replace(identity, scopes=identity.scopes | scopes.CONFIRMED
                   | granted_extension_scopes(reach.PROTECTED))


class ScopeMiddleware(BaseHTTPMiddleware):
    """Stamps the caller's identity on every request into the API."""

    async def dispatch(self, request: Request,
                       call_next: RequestResponseEndpoint) -> Response:
        identity = _policy.identify(request)
        presented = request.headers.get(APPROVAL_HEADER, "")
        if presented and identity.kind == "token":
            approved = await _approved(request, identity, presented)
            if approved is None:
                return error_response(403, CODE_FORBIDDEN, t("error.approvals.not_valid"),
                                      details={"reason": APPROVAL_INVALID})
            identity = approved
        request.state.identity = identity
        # The spec lists every route and scope, so it is for someone who can use them.
        if not identity.can(scopes.CONFIG_READ) and _is_api_description(request):
            return error_response(403, CODE_FORBIDDEN, t("error.auth.needs_scope",
                                                        scope=scopes.CONFIG_READ))
        # What an extension may change reads the tier off the request, so it is set around
        # everything the request runs, and ends with it.
        client = getattr(request, "client", None)
        origin = "this device" if identity.is_local else str(getattr(client, "host", ""))
        with reach.marking(identity.tier, origin):
            return await call_next(request)


class ForbiddenError(ApiError):
    def __init__(self, message: str = "", *,
                 details: dict[str, Any] | None = None) -> None:
        message = message or t("error.envelope.forbidden")
        super().__init__(CODE_FORBIDDEN, message, status_code=403, details=details)


def _refusal(identity: Identity, scope: str) -> ForbiddenError:
    """Why this caller may not have `scope`.  An Owner without a Confirm is told to confirm;
    a token or a Remote pass is told it never can."""
    if scopes.is_confirmed(scope):
        if identity.kind == sign_in.OWNER:
            return ForbiddenError(t("error.auth.confirm_needed"),
                                  details={"reason": CONFIRM_NEEDED, "scope": scope})
        return ForbiddenError(t("error.auth.protected"),
                              details={"reason": PROTECTED, "scope": scope})
    return ForbiddenError(t("error.auth.needs_scope", scope=scope),
                          details={"reason": "scope", "scope": scope})


def check(identity: Identity | None, scope: str, path: str = "") -> None:
    """Raise unless `identity` holds `scope`.  Using a Protected scope under a live Confirm
    extends the Confirm."""
    if identity is None:
        logger.error("No identity on %s - the scope middleware did not run", path)
        raise ForbiddenError(t("error.auth.unavailable"))
    if not identity.can(scope):
        raise _refusal(identity, scope)
    if scopes.is_confirmed(scope) and identity.session_id:
        sign_in.instance().is_confirmed(identity.session_id, use=True)


def require(request: Request, scope: str) -> None:
    """For a handler whose route is declared at a lower scope but whose body reaches for a
    higher one: a config key that names a command, an action that powers off."""
    check(getattr(request.state, "identity", None), scope, request.url.path)


def requires(scope: str) -> Any:
    """Declare the scope a route needs.

    Any because that is what it returns: FastAPI declares `Depends(...) -> Any` so a
    dependency can stand in for the value it resolves to. Narrowing it here would be
    a claim the framework does not make.

    Fails closed: if the identity is missing the boundary did not run, and the answer
    is no rather than a guess.
    """
    if not scopes.is_known(scope):
        raise ValueError(f"Unknown scope: {scope}")

    async def _check(request: Request) -> None:
        check(getattr(request.state, "identity", None), scope, request.url.path)

    setattr(_check, _SCOPE_ATTR, scope)
    return Depends(_check)


def route_scope(route: APIRoute) -> str | None:
    """The scope a route declares, or None."""
    for dependency in route.dependencies:
        call = getattr(dependency, "dependency", None)
        scope = getattr(call, _SCOPE_ATTR, None)
        if scope is not None:
            return scope
    return None


def iter_api_routes(app: FastAPI) -> Iterator[tuple[str, APIRoute]]:
    """Every route on the app, including those inside included routers.

    FastAPI does not flatten an included router into app.routes, so a plain walk
    misses everything mounted through include_router - which is nearly all of it.
    """
    def walk(routes: Sequence[BaseRoute],
             prefix: str = "") -> Iterator[tuple[str, APIRoute]]:
        for route in routes:
            if isinstance(route, APIRoute):
                yield prefix + route.path, route
            elif type(route).__name__ == "_IncludedRouter":
                original = getattr(route, "original_router", None)
                context = getattr(route, "include_context", None)
                inner = getattr(context, "prefix", "") if context is not None else ""
                if original is not None:
                    yield from walk(original.routes, prefix + (inner or ""))

    yield from walk(app.routes)


def assert_every_route_declares_a_scope(app: FastAPI) -> None:
    """Refuse to start with an unguarded route.

    The boundary is only un-bypassable if forgetting to use it is impossible, and the
    cheapest way to make it impossible is to not start.
    """
    missing = [
        f"{','.join(sorted(route.methods or ()))} {path}"
        for path, route in iter_api_routes(app)
        if route_scope(route) is None
    ]
    if missing:
        raise RuntimeError(
            "These API routes declare no scope: " + "; ".join(sorted(missing))
            + ". Add requires(<scope>) to each."
        )
    _remember_routes(app)


_known_routes: list[tuple[re.Pattern[str], frozenset[str], str]] = []


def _remember_routes(app: FastAPI) -> None:
    _known_routes[:] = [
        (compile_path(path)[0], frozenset(route.methods or ()), scope)
        for path, route in iter_api_routes(app)
        if (scope := route_scope(route)) is not None
    ]


def request_scope(method: str, path: str) -> str | None:
    """The scope this API demands for `method` on `path` (relative to the API prefix), or
    None for a route it does not know - which a caller holding less than everything must
    treat as refused."""
    for pattern, methods, scope in _known_routes:
        if method.upper() in methods and pattern.match(path):
            return scope
    return None
