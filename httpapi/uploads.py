"""Upload sessions, the asset import pipeline, and the VPSdb lens.

`common/uploads/upload_ops.py` runs the pipeline; `common/online/vps_lens.py` answers for
the catalog. What is here is the wire, and the two statuses only this layer can give: a
file that was too big is 413, and a session nothing can be made of is 422.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager

from fastapi import APIRouter, Body, File, Form, Query, UploadFile
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import Response

from common.extensions import reach
from common.failures import why
from common.online import game_offers, vps_lens
from common.uploads import upload_ops

from . import jobs as jobs_api
from . import models, scopes
from .auth import requires
from .errors import ApiError
from .responses import MEDIA_SIZE, revalidating_file

router = APIRouter(prefix="/uploads", tags=["uploads"])
vps_router = APIRouter(prefix="/vps", tags=["vps"])


@router.post("", summary="Begin an upload session", dependencies=[requires(scopes.UPLOADS_WRITE)])
def begin_upload(body: models.UploadBegin = Body(default_factory=models.UploadBegin)
                 ) -> models.UploadBegun:
    return models.UploadBegun(**upload_ops.begin(body.bytes))


@router.post("/from_path", summary="Begin a session over a folder on this device",
             dependencies=[requires(scopes.UPLOADS_WRITE), requires(scopes.FILESYSTEM_READ)])
def begin_upload_from(body: models.UploadFromPath) -> models.UploadBegun:
    """A file, a folder or an archive already here, read where it is and never removed
    with the session."""
    return models.UploadBegun(**upload_ops.begin_from(body.path))


@router.get("/{upload_id}", summary="Upload session summary",
            dependencies=[requires(scopes.UPLOADS_WRITE)])
def get_upload(upload_id: str) -> models.UploadSummary:
    return models.UploadSummary(**upload_ops.summary(upload_id))


@router.delete("/{upload_id}", summary="Abort an upload session",
               dependencies=[requires(scopes.UPLOADS_WRITE)])
def abort_upload(upload_id: str) -> models.Acknowledged:
    upload_ops.abort(upload_id)
    return models.Acknowledged(ok=True)


@router.post("/{upload_id}/files", summary="Add a file to an upload session",
             dependencies=[requires(scopes.UPLOADS_WRITE)])
async def add_upload_file(upload_id: str, relpath: str = Form(...),
                          file: UploadFile = File(...)) -> models.FileStored:
    try:
        stored = await run_in_threadpool(upload_ops.add_file, upload_id, relpath,
                                         file.file)
    except upload_ops.UploadTooLargeError as exc:
        raise ApiError("payload_too_large", why(exc), status_code=413) from exc
    return models.FileStored(**stored)


@router.put("/{upload_id}/file", summary="Stream a file into an upload session",
            dependencies=[requires(scopes.UPLOADS_WRITE)])
async def put_upload_file(upload_id: str, request: Request,
                          relpath: str = Query(...)) -> models.FileStored:
    """The request body is the file, as it is. Unlike the multipart route, nothing is
    copied to the system temp folder on the way: it goes straight into the session's folder
    beside the library, and `Content-Length` is checked against the room there first."""
    declared = int(request.headers.get("content-length") or 0)
    written = 0
    try:
        with ExitStack() as opened:
            put = await run_in_threadpool(
                opened.enter_context, upload_ops.file_writer(upload_id, relpath, declared))
            async for chunk in request.stream():
                written += len(chunk)
                await run_in_threadpool(put, chunk)
    except upload_ops.UploadTooLargeError as exc:
        raise ApiError("payload_too_large", why(exc), status_code=413) from exc
    return models.FileStored(bytes=written)


@router.get("/{upload_id}/analysis", summary="Analyze an upload session",
            dependencies=[requires(scopes.UPLOADS_WRITE)])
def analyze_upload(upload_id: str) -> models.Analysis:
    return models.Analysis(**upload_ops.analysis_of(upload_id))


@router.get("/{upload_id}/picture", summary="A picture the session holds",
            dependencies=[requires(scopes.UPLOADS_WRITE)])
def upload_picture(upload_id: str, request: Request, name: str = Query(...),
                   size: int | None = MEDIA_SIZE) -> Response:
    """The image dropped under `name`, or the member of an archive at that path inside it, at
    `size` (256 or 1024) when given. 404 for a file that is not an image, one the session does
    not hold, or a member too big to draw."""
    return revalidating_file(upload_ops.picture(upload_id, name, size).path, request)


@router.get("/{upload_id}/image", summary="The one picture a session holds",
            dependencies=[requires(scopes.UPLOADS_WRITE)])
def upload_image(upload_id: str, request: Request) -> Response:
    """The dropped picture as it arrived, for a place that takes exactly one. 400 where the
    session holds several files, a folder or archive, or a file that is not an image. The
    file is named `picture` plus its own suffix, whatever it was dropped as."""
    path = upload_ops.single_image(upload_id)
    response = revalidating_file(path, request)
    response.headers.update(
        {"Content-Disposition": f'inline; filename="picture{path.suffix.lower()}"'})
    return response


@router.post("/{upload_id}/plan", summary="Build an import plan",
             dependencies=[requires(scopes.UPLOADS_WRITE)])
def plan_upload(upload_id: str,
                payload: models.PlanRequest = Body(default_factory=models.PlanRequest),
                ) -> models.ImportPlanResource:
    """What the drop would do, before it does any of it."""
    try:
        return models.ImportPlanResource(
            **upload_ops.plan_for(upload_id, payload.model_dump()))
    except upload_ops.UnprocessableUploadError as exc:
        raise ApiError("unprocessable_upload", why(exc), status_code=422) from exc


@contextmanager
def _import_refusals() -> Iterator[None]:
    """The three ways an import is refused before it starts, as the 422 each one is."""
    try:
        yield
    except upload_ops.UnprocessableUploadError as exc:
        raise ApiError("unprocessable_upload", why(exc), status_code=422) from exc
    except upload_ops.PicksNeededError as exc:
        raise ApiError("choose_one", why(exc), status_code=422, details=exc.details) from exc
    except upload_ops.NothingImportableError as exc:
        raise ApiError("no_importable_assets", why(exc), status_code=422,
                       details=exc.details) from exc


@router.post("/{upload_id}/import", summary="Execute an import plan",
             dependencies=[requires(scopes.UPLOADS_WRITE)])
def import_upload(upload_id: str,
                  payload: models.ImportRequest = Body(default_factory=models.ImportRequest),
                  ) -> models.ImportReport:
    with _import_refusals():
        report = upload_ops.execute(upload_id, payload.model_dump(exclude={"declared"}),
                                    payload.declared)
    return models.ImportReport(**report)


@router.post("/{upload_id}/import/start", summary="Execute an import plan as a job",
             status_code=202, dependencies=[requires(scopes.UPLOADS_WRITE)])
def start_import(upload_id: str, response: Response,
                 payload: models.ImportRequest = Body(default_factory=models.ImportRequest),
                 ) -> models.JobResource:
    """`/import`, accepted and not done: converts each file the plan converts, then writes
    everything, so a conversion that fails or is stopped adds nothing. Refused here for
    anything `/import` would refuse; what goes wrong afterwards is the job's `error`. The
    job's `parts` say where each converted file is, by its path inside the drop."""
    with _import_refusals():
        job = upload_ops.start_import(upload_id, payload.model_dump(exclude={"declared"}),
                                      payload.declared)
    response.headers["Location"] = f"/api/v1/jobs/{job.id}"
    return models.JobResource(**jobs_api.resource(job))


@router.post("/import/stop", summary="Stop the import that is running",
             dependencies=[requires(scopes.UPLOADS_WRITE)])
def stop_import() -> models.Acknowledged:
    """Before anything is written: a stopped import adds nothing and leaves the upload as it
    was. `ok` is false where none is running."""
    return models.Acknowledged(ok=upload_ops.stop_import())


@vps_router.get("/entry/{vps_id}", summary="One VPSdb entry",
                dependencies=[requires(scopes.VPS_READ)])
def vps_entry(vps_id: str) -> models.VpsSearchResult:
    """Under `/entry/` rather than `/{vps_id}` so it cannot swallow `/search`, and so a
    later verb here does not have to be a reserved word."""
    return models.VpsSearchResult(**vps_lens.entry(vps_id))


@vps_router.get("/entry/{vps_id}/releases", summary="The releases VPSdb lists for one entry",
                dependencies=[requires(scopes.VPS_READ)])
def vps_releases(vps_id: str,
                 listed_as: str = Query("tableFiles")) -> models.VpsReleases:
    """Every record of one kind this device has, in the order VPSdb holds them - never
    ordered by likeness, which was measured at chance."""
    return models.VpsReleases.model_validate(vps_lens.releases(vps_id, listed_as))


@vps_router.get("/entry/{vps_id}/offers", summary="What every enabled source has for one entry",
                dependencies=[requires(scopes.VPS_READ)])
def vps_entry_offers(
    vps_id: str, request: Request,
    kind: list[str] = Query([], description="A kind to ask for, by its slot's name; repeat "
                                            "for more. None asks every kind each source serves"),
) -> models.EntryOffers:
    """`GET /games/{id}/offers` for an entry the library does not have to hold: the same
    sources asked in the same way, about the entry's VPS id and name alone. An extension's
    source is left out where the caller may not use that extension, and answers at Library at
    most. `404` for an entry the catalog does not list."""
    entry = vps_lens.entry(vps_id)
    reach.answer_at(reach.LIBRARY)
    identity = request.state.identity
    return models.EntryOffers.model_validate(game_offers.wire_for_entry(
        vps_id, kind, name=str(entry["name"] or ""),
        permits=lambda source: identity.can(scopes.extension_scope(source.extension,
                                                                   reach.LIBRARY))))


@vps_router.get("/sync", summary="When the catalog was last checked",
                dependencies=[requires(scopes.VPS_READ)])
def vps_sync_state() -> models.VpsSyncState:
    """What a surface needs to say how fresh the answers it is giving are."""
    return models.VpsSyncState(**vps_lens.sync_state())


@vps_router.post("/sync", summary="Check VPSdb for a newer catalog now",
                 dependencies=[requires(scopes.GAMES_WRITE)])
async def vps_sync() -> models.VpsSyncResult:
    """Asked for, so it ignores the schedule. The check is one line and the catalog is
    about 7 MB, so this is off the loop."""
    return models.VpsSyncResult(**await run_in_threadpool(vps_lens.sync_now))


@vps_router.get("/search", summary="Search VPSdb", dependencies=[requires(scopes.VPS_READ)])
def search_vps(q: str = "", limit: int = 20) -> models.VpsSearchResults:
    return models.VpsSearchResults.model_validate(vps_lens.search(q, limit))
