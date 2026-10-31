"""A support package a person reviews and then sends.

Built once and held in memory under a random id, so the file that is downloaded is the one
that was reviewed. The routes sit behind the scope About and the log download already
require, and none of them serves text that has not been cleaned.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse, Response

from common.support import package

from . import models, scopes
from .auth import requires

router = APIRouter(prefix="/support", tags=["support"])


def _held(package_id: str) -> package.Package:
    found = package.held(package_id)
    if found is None:
        raise HTTPException(status_code=404, detail="No such support package, or it has expired")
    return found


def _built(options: models.SupportOptions) -> package.Package:
    chosen = package.Options(options.logs, options.previous_log, options.settings,
                             options.hide_tables, tuple(options.hide),
                             tuple(options.left_out), tuple(options.removed))
    try:
        return package.hold(package.build_package(chosen))
    except package.PackageTooLargeError:
        raise HTTPException(status_code=413, detail="The package is too large") from None


@router.post("", summary="Make a support package for review",
             dependencies=[requires(scopes.SYSTEM_READ)])
def build(options: models.SupportOptions) -> models.SupportPackage:
    """Gathers this device's details and logs, replaces what is private, and holds the
    result for a few minutes. Nothing is written to disk and nothing is sent anywhere."""
    return models.SupportPackage.model_validate(package.review(_built(options)))


@router.get("/{package_id}/file", summary="The package, as the zip that was reviewed",
            dependencies=[requires(scopes.SYSTEM_READ)], response_class=Response)
def download(package_id: str) -> Response:
    made = _held(package_id)
    return Response(made.data, media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="{made.filename}"'})


@router.get("/{package_id}/summary", summary="The package's summary, as Markdown",
            dependencies=[requires(scopes.SYSTEM_READ)], response_class=PlainTextResponse)
def summary(package_id: str) -> PlainTextResponse:
    return PlainTextResponse(_held(package_id).summary, media_type="text/markdown")
