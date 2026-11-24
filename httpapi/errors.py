"""One error envelope for /api/v1: {"error": {code, message, details}}.

Routes raise; the handlers here do the shaping. See docs/http-api.md.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request

from common import service_errors
from common.failures import upstream, why
from common.i18n import t

logger = logging.getLogger("vpinfe.httpapi.errors")


# Part of the contract: clients branch on these, not on message text.
CODE_NOT_FOUND = "not_found"
CODE_INVALID_REQUEST = "invalid_request"
CODE_METHOD_NOT_ALLOWED = "method_not_allowed"
CODE_FEATURE_UNAVAILABLE = "feature_unavailable"
CODE_CONFLICT = "conflict"
CODE_UPSTREAM_UNAVAILABLE = "upstream_unavailable"
CODE_INTERNAL_ERROR = "internal_error"
CODE_UNAUTHORIZED = "unauthorized"
CODE_FORBIDDEN = "forbidden"
CODE_TOO_MANY_REQUESTS = "too_many_requests"

# Statuses that map to a specific code when they arrive as a bare HTTPException
# (from routing, or from code that raises HTTPException directly).
_STATUS_CODES = {
    401: CODE_UNAUTHORIZED,
    403: CODE_FORBIDDEN,
    404: CODE_NOT_FOUND,
    405: CODE_METHOD_NOT_ALLOWED,
    409: CODE_CONFLICT,
    422: CODE_INVALID_REQUEST,
    429: CODE_TOO_MANY_REQUESTS,
    501: CODE_FEATURE_UNAVAILABLE,
}


class ApiError(Exception):
    """An error to return to the client in the standard envelope."""

    def __init__(self, code: str, message: str, *, status_code: int = 400,
                 details: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details


class NotFoundError(ApiError):
    def __init__(self, message: str = "", *, details: Any = None) -> None:
        message = message or t("error.envelope.not_found")
        super().__init__(CODE_NOT_FOUND, message, status_code=404, details=details)


class InvalidRequestError(ApiError):
    def __init__(self, message: str = "", *, details: Any = None) -> None:
        message = message or t("error.envelope.invalid_request")
        super().__init__(CODE_INVALID_REQUEST, message, status_code=400, details=details)


class ConflictError(ApiError):
    """The request is fine, but the thing it asks for cannot happen right now -
    something is already using what it needs."""

    def __init__(self, message: str = "", *, details: Any = None) -> None:
        message = message or t("error.envelope.conflict")
        super().__init__(CODE_CONFLICT, message, status_code=409, details=details)


class FeatureUnavailableError(ApiError):
    """Exists, but not available here. The message is user-facing: say how to enable it."""

    def __init__(self, message: str = "", *, details: Any = None) -> None:
        message = message or t("error.envelope.feature_unavailable")
        super().__init__(CODE_FEATURE_UNAVAILABLE, message, status_code=501, details=details)


# What a service refuses, and the envelope it comes out as. One table, registered once,
# so a route does not carry a try/except that only renames the same four refusals.
_SERVICE_ERRORS: tuple[tuple[type[service_errors.ServiceError],
                          Callable[..., ApiError]], ...] = (
    (service_errors.NotFoundError, NotFoundError),
    (service_errors.RefusedError, InvalidRequestError),
    (service_errors.BlockedError, ConflictError),
    (service_errors.UnavailableError, FeatureUnavailableError),
)


def as_api_error(exc: service_errors.ServiceError) -> ApiError:
    """The envelope for a refusal, for a caller that has to raise rather than return."""
    for kind, envelope in _SERVICE_ERRORS:
        if isinstance(exc, kind):
            return envelope(str(exc), details=exc.details)
    return ApiError(CODE_INTERNAL_ERROR, str(exc), status_code=500, details=exc.details)


def error_response(status_code: int, code: str, message: str,
                   details: Any = None) -> JSONResponse:
    """Build an envelope response directly. Prefer raising ApiError."""
    error: dict[str, Any] = {"code": code, "message": message}
    error["details"] = jsonable_encoder(details) if details is not None else None
    return JSONResponse(status_code=status_code, content={"error": error})


def install_error_handlers(
        app: FastAPI,
        on_unhandled: Callable[[Request], None] | None = None) -> None:
    """Attach the envelope handlers. Only ever the /api/v1 app - these would turn the
    Manager's HTML error pages into JSON.

    `on_unhandled` is told which request raised something nobody expected, so the
    extension seam can take the extension that did it out. Passed in rather than
    imported: this module is what the seam raises through.
    """

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return error_response(exc.status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(service_errors.ServiceError)
    async def _service_error(request: Request,
                             exc: service_errors.ServiceError) -> JSONResponse:
        api = as_api_error(exc)
        return error_response(api.status_code, api.code, api.message, api.details)

    @app.exception_handler(OSError)
    async def _os_error(request: Request, exc: OSError) -> JSONResponse:
        said = why(exc)
        logger.warning("%s %s failed: %s", request.method, request.url.path, said)
        if upstream(exc):
            return error_response(502, CODE_UPSTREAM_UNAVAILABLE, said)
        return error_response(409, CODE_CONFLICT, said)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _STATUS_CODES.get(exc.status_code)
        if code is None:
            code = CODE_INVALID_REQUEST if exc.status_code < 500 else CODE_INTERNAL_ERROR
        return error_response(exc.status_code, code, str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request,
                                exc: RequestValidationError) -> JSONResponse:
        return error_response(422, CODE_INVALID_REQUEST,
                              t("error.envelope.unprocessable"), exc.errors())

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Log the cause; tell the client nothing beyond "we broke".
        logger.exception("Unhandled error serving %s %s", request.method, request.url.path)
        if on_unhandled is not None:
            try:
                on_unhandled(request)
            except Exception:
                logger.exception("Reporting the failure failed as well")
        return error_response(500, CODE_INTERNAL_ERROR, t("error.envelope.server_fault"))
