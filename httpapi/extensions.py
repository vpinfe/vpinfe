"""Where an extension meets the API: the gate, and who is blamed when one throws.

An extension declares the actions it gates its own routes on; core mints the scope from
its name and attaches it here. Nothing an extension can write reaches a route without a
gate, because the extension does not attach one.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import quote

import anyio
from fastapi import APIRouter, Depends, FastAPI, Request, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from common import extensions, service_errors
from common.extensions import accounts, pictures, serving
from common.failures import why
from common.i18n import t
from common.online import extension_ops

from . import extensions_served, models, responses, scopes
from .auth import requires
from .errors import CODE_INTERNAL_ERROR, ApiError, FeatureUnavailableError, NotFoundError

logger = logging.getLogger("vpinfe.httpapi.extensions")

PREFIX = "/ext"

router = APIRouter(prefix="/extensions", tags=["extensions"])


@router.get("", summary="What this install has loaded",
            dependencies=[requires(scopes.INSTANCE_READ)])
def list_extensions() -> dict:
    """Every extension this install looked at, running or not."""
    return {"extensions": [record.as_dict() for record in extensions.records()]}


@router.get("/needs", summary="What the running extensions ask for on Overview",
            dependencies=[requires(scopes.INSTANCE_READ)])
def get_extension_needs() -> dict:
    """Each need a running extension raises now, with the words and the thing a click
    opens. `away` names the extensions whose needs could not be read, which a dismissal
    kept against them must outlast."""
    return serving.needs_all(extensions.records())


@router.get("/list", summary="The VPinFE Extension List as last fetched",
            dependencies=[requires(scopes.INSTANCE_READ)])
def get_extension_list() -> dict:
    """`checked` is when it was last fetched, empty if never; each entry says what this
    install has of it. Makes no request."""
    return extension_ops.state()


@router.post("/list/check", summary="Fetch the VPinFE Extension List again",
             dependencies=[requires(scopes.CONFIG_WRITE)])
def check_extension_list() -> dict:
    try:
        return extension_ops.check()
    except extension_ops.UnavailableError as exc:
        raise ApiError("extension_list_unreachable", why(exc), status_code=503) from exc


@router.post("/list/update", summary="Update every extension the list has a newer version of",
             dependencies=[requires(scopes.PROTECTED)])
def update_extensions() -> dict:
    try:
        return extension_ops.update_all()
    except extension_ops.InstallFailedError as exc:
        raise ApiError("extension_install_failed", why(exc), status_code=502) from exc


@router.post("/{name}/install", summary="Install, update or reinstall an extension from the list",
             dependencies=[requires(scopes.PROTECTED)])
def install_extension(name: str) -> dict:
    """One call for all three: installing over a copy is what an update and a reinstall
    are. The extension runs from the next start."""
    try:
        return extension_ops.install(name)
    except service_errors.NotFoundError as exc:
        raise NotFoundError(why(exc)) from exc
    except extension_ops.InstallFailedError as exc:
        raise ApiError("extension_install_failed", why(exc), status_code=502) from exc


@router.delete("/{name}", summary="Remove an extension that was installed from the list",
               dependencies=[requires(scopes.PROTECTED)])
def remove_extension(name: str) -> dict:
    try:
        return extension_ops.remove(name)
    except service_errors.NotFoundError as exc:
        raise NotFoundError(why(exc)) from exc
    except extension_ops.InstallFailedError as exc:
        raise ApiError("extension_remove_failed", why(exc), status_code=502) from exc


@router.get("/{name}/picture", summary="The picture an extension's manifest names",
            dependencies=[requires(scopes.INSTANCE_READ)])
def get_extension_picture(name: str, request: Request, v: str = "") -> Response:
    """The picture in the extension's manifest, at list size, for one that is shipped or
    accepted. `v` is the version the page was drawn from; it is not read, and makes a new
    version's address a new one."""
    record = extensions.registry().get(name)
    if record is None:
        raise NotFoundError(t("error.extensions.not_found", name=name))
    return responses.media_file(pictures.identity(record), request, "")


@router.put("/{name}/enabled", summary="Switch an extension on or off",
            dependencies=[requires(scopes.CONFIG_WRITE)])
def put_extension_enabled(name: str, body: models.ExtensionSwitch) -> dict:
    record = extensions.switch(name, body.enabled)
    if record is None:
        raise NotFoundError(t("error.extensions.not_found", name=name))
    return record.as_dict()


@router.post("/{name}/accept", summary="Accept an extension VPinFE did not ship",
             dependencies=[requires(scopes.PROTECTED)])
def accept_extension(name: str) -> dict:
    """Accepts the extension as it declares itself now - the scopes and capabilities in
    its manifest - and starts it. A later version that declares more stops until it is
    accepted again. An installed copy that replaces a built-in starts at the next start;
    the built-in keeps running until then."""
    record = extensions.accept(name)
    if record is None:
        raise NotFoundError(t("error.extensions.not_found", name=name))
    return record.as_dict()


def _running(name: str) -> Any:
    """Refuse on an extension's own routes while it is not running.

    Its scopes are revoked as well, so this is not the only thing in the way. It is the
    one that says which extension and why, which a 403 would not.
    """
    async def check() -> None:
        record = extensions.registry().get(name)
        if record is None or not record.running:
            reason = (record.reason if record is not None
                      else t("extension.reason.not_installed"))
            display = record.display_name if record is not None else name
            raise FeatureUnavailableError(t("error.extensions.not_running",
                    display=(display),
                    value=(reason or t("extension.reason.none_recorded"))))

    return Depends(check)


def mount(api: FastAPI) -> None:
    """Mount every router an extension registered, gated on the scope it declared, and the
    routes core serves for what each declared through `ctx.ui`.

    Once, at startup, whatever becomes of the extension after. A disabled one keeps its
    paths and refuses on them, where a 404 would read as a typo.
    """
    for record, ext_router, scope in extensions.mounted():
        manifest = record.manifest
        unknown = sorted(one for one in (manifest.scopes if manifest else ())
                         if not scopes.is_known(one))
        if unknown:
            extensions.refuse(record.name, "extension.reason.unknown_scopes",
                              scopes=", ".join(unknown))
        for route in getattr(ext_router, "routes", []):
            # On each route rather than on the inclusion: the startup check that refuses
            # an ungated route reads what a route itself declares, and a gate it cannot
            # see is one nothing would notice the loss of.
            route.dependencies.extend([_running(record.name), requires(scope),
                                       extensions_served.answering_at(scopes.extension_tier(scope))])
        api.include_router(ext_router, prefix=f"{PREFIX}/{record.name}")
        logger.debug("Mounted %s/%s gated on %s", PREFIX, record.name, scope)
    for record in extensions.served():
        api.include_router(extensions_served.router(record.name, _running(record.name)),
                           prefix=f"{PREFIX}/{record.name}")


def _answering_for_an_extension(scope: Scope) -> bool:
    path = str(scope.get("path") or "")
    return path[len(str(scope.get("root_path") or "")):].startswith(f"{PREFIX}/")


class SecretsStayHere:
    """Takes the value out of every secret field an extension's route answers with."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not _answering_for_an_extension(scope):
            await self.app(scope, receive, send)
            return
        started: list[Message] = []
        body: list[bytes] = []

        async def hold(message: Message) -> None:
            if message["type"] == "http.response.start":
                message = _sandboxed(message)
                if _is_json(message):
                    started.append(message)
                    return
            if message["type"] != "http.response.body" or not started:
                await send(message)
                return
            body.append(message.get("body", b""))
            if message.get("more_body"):
                return
            kept = accounts.scrubbed_json(b"".join(body))
            headers = [(name, value) for name, value in started[0].get("headers", [])
                       if name.lower() != b"content-length"]
            await send({**started[0],
                        "headers": [*headers, (b"content-length", str(len(kept)).encode())]})
            await send({"type": "http.response.body", "body": kept})

        await self.app(scope, receive, hold)


# The API is mounted in the Manager's own app, so a page one of these routes serves has the
# Manager's origin. `sandbox` without `allow-same-origin` runs it as no origin at all.
SANDBOX = (b"content-security-policy", b"sandbox allow-scripts allow-forms")
NOSNIFF = (b"x-content-type-options", b"nosniff")


def _sandboxed(message: Message) -> Message:
    kept = [(name, value) for name, value in message.get("headers", [])
            if name.lower() != NOSNIFF[0]]
    return {**message, "headers": [*kept, SANDBOX, NOSNIFF]}


def _is_json(message: Message) -> bool:
    return any(name.lower() == b"content-type" and b"json" in value.lower()
               for name, value in message.get("headers", []))


async def ask(request: Request, extension: str, method: str, path: str,
              body: Any = None) -> Any:
    """What one of an extension's own routes answers, asked in-process through the whole
    API app, as the caller. An answer that is an error is raised as that error."""
    status, _, chunks = await _asked(request, extension, method, path, body)
    return _answered(status, chunks)


async def ask_file(request: Request, extension: str, path: str) -> tuple[str, bytes] | None:
    """The bytes a GET on one of an extension's own routes answers, with their content
    type. None for a 404, which is how a route says there is nothing; any other error is
    raised as that error."""
    status, headers, chunks = await _asked(request, extension, "GET", path)
    if status == 404:
        return None
    _answered(status, chunks)
    return headers.get("content-type", "").split(";")[0].strip().lower(), chunks


async def _asked(request: Request, extension: str, method: str, path: str,
                 body: Any = None) -> tuple[int, dict[str, str], bytes]:
    root = str(request.scope.get("root_path") or "")
    where = f"{root}{PREFIX}/{quote(extension, safe='')}{path}"
    raw = b"" if body is None else json.dumps(body).encode("utf-8")
    finished = anyio.Event()
    asked = False

    async def receive() -> Message:
        nonlocal asked
        if not asked:
            asked = True
            return {"type": "http.request", "body": raw, "more_body": False}
        await finished.wait()
        return {"type": "http.disconnect"}

    status, chunks = [500], []
    answered_headers: dict[str, str] = {}

    async def send(message: Message) -> None:
        if message["type"] == "http.response.start":
            status[0] = int(message["status"])
            answered_headers.update({name.decode("latin-1").lower(): value.decode("latin-1")
                                     for name, value in message.get("headers", [])})
        elif message["type"] == "http.response.body":
            chunks.append(message.get("body", b""))
            if not message.get("more_body"):
                finished.set()

    headers = [(name, value) for name, value in request.scope.get("headers", [])
               if name.lower() not in (b"content-length", b"content-type",
                                       b"transfer-encoding")]
    scope: Scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": method, "scheme": request.scope.get("scheme", "http"),
        "path": where, "raw_path": where.encode("utf-8"), "root_path": root,
        "query_string": b"", "client": request.scope.get("client"),
        "server": request.scope.get("server"),
        "headers": [*headers, (b"content-type", b"application/json"),
                    (b"content-length", str(len(raw)).encode())],
    }
    try:
        await request.app(scope, receive, send)
    except Exception:
        # An error nobody expected is answered with a 500 first and raised after; the
        # answer is what counts, and the extension has already been blamed for it.
        logger.debug("%s %s raised after answering", method, where, exc_info=True)
    return status[0], answered_headers, b"".join(chunks)


def _answered(status: int, body: bytes) -> Any:
    try:
        said = json.loads(body) if body else None
    except ValueError:
        said = None
    if status < 400:
        return said
    error = (said or {}).get("error") if isinstance(said, dict) else None
    if isinstance(error, dict):
        raise ApiError(str(error.get("code") or CODE_INTERNAL_ERROR),
                       str(error.get("message") or ""), status_code=status,
                       details=error.get("details"))
    raise ApiError(CODE_INTERNAL_ERROR, t("error.envelope.server_fault"),
                   status_code=status)


def blame(request: Request) -> None:
    """Disable the extension a failing request belonged to, if it belonged to one.

    Called from the envelope's unhandled-error handler. An extension raising an ApiError
    is answering rather than failing, and never arrives there.
    """
    path = request.url.path
    marker = f"{PREFIX}/"
    if marker not in path:
        return
    name = path.split(marker, 1)[1].split("/", 1)[0]
    if extensions.registry().get(name) is not None:
        extensions.disable(name, "extension.reason.failed_serving", path=path)
