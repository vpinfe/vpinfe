"""Operations over the library as a whole, rather than one game.

A scan rewrites metadata across every game folder, which is why it lives here and not
under /games/{id} - it is not an operation on a game, and pretending it were would put a
library-wide write behind a per-table path.

`common/games/library_ops.py` does the work. The long ones answer 202 with a Location
header: accepted, not done, and something to watch while it runs.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Request, Response

from common.games import (
    collection_ops,
    game_ops,
    game_service,
    library_ops,
    marks_ops,
    media_convert,
    media_fill,
    media_rotate,
    owned,
)
from common.i18n import t
from common.runtime import jobs as job_registry

from . import jobs as jobs_api
from . import models, scopes
from .auth import ForbiddenError, requires
from .criteria import criteria_for

router = APIRouter(prefix="/library", tags=["library"])


def _accepted(response: Response, job: job_registry.Job) -> models.JobResource:
    """A started job, as the caller is handed it: where to watch, and what it is."""
    response.headers["Location"] = f"/api/v1/jobs/{job.id}"
    return models.JobResource(**jobs_api.resource(job))


@router.get("/filters", summary="What this library can be filtered on",
            dependencies=[requires(scopes.GAMES_READ)])
def filters() -> models.FilterAxisList:
    """Every filter axis, with the values this library holds. Projected from the registry
    the resolver matches on, so the two cannot disagree."""
    return models.FilterAxisList.model_validate(library_ops.filter_axes())


@router.post("/owned", summary="Which of these catalog ids this library holds",
             dependencies=[requires(scopes.GAMES_READ)])
def post_owned(payload: models.OwnedRequest) -> models.OwnedMap:
    """A POST so a long list fits: it reads and changes nothing."""
    return models.OwnedMap.model_validate(owned.owned(payload.ids, payload.keys, payload.kind))


@router.get("/game_collections", summary="Which collections hold each game",
            dependencies=[requires(scopes.COLLECTIONS_READ)])
def game_collections() -> models.LibraryGameCollections:
    """`GET /games/{id}/collections` for every game at once. A game no collection holds
    is not listed."""
    return models.LibraryGameCollections.model_validate(collection_ops.game_collections())


@router.get("/tags", summary="Every tag, and what each one says",
            dependencies=[requires(scopes.GAMES_READ)])
def list_tags() -> models.TagList:
    return models.TagList.model_validate(library_ops.tags())


@router.put("/tags/{tag}", summary="Say what a tag means and what color it wears",
            dependencies=[requires(scopes.GAMES_WRITE)])
def put_tag(tag: str, body: models.TagUpdate) -> models.TagResource:
    return models.TagResource.model_validate(
        library_ops.put_tag(tag, body.description, body.color))


@router.post("/tags/merge", summary="Fold tags into one",
             dependencies=[requires(scopes.GAMES_WRITE)])
def merge_tags(payload: models.TagMerge) -> models.TagSweep:
    """Rename is one source into a name nothing uses; merge is several into one."""
    return models.TagSweep.model_validate(
        library_ops.merge_tags(payload.sources, payload.into))


@router.delete("/tags/{tag}", summary="Remove a tag from every game",
               dependencies=[requires(scopes.GAMES_WRITE)])
def delete_tag(tag: str) -> models.TagSweep:
    return models.TagSweep.model_validate(library_ops.drop_tag(tag))


@router.get("/entries", summary="The entries the whole library resolves to",
            dependencies=[requires(scopes.GAMES_READ)])
def entries() -> models.EntryList:
    """The play lens over everything, which is what a frontend shows before a collection
    is chosen. `GET /collections/{name}/entries` is the same lens narrowed to one."""
    return models.EntryList.model_validate(library_ops.entries())


@router.post("/preview", summary="What a rule would match, storing nothing",
             dependencies=[requires(scopes.GAMES_READ)])
def preview(request: models.PreviewRequest = Body(...)) -> models.EntryList:
    """A POST because criteria are a structure rather than a query string: the registry
    grows, and several axes take a list."""
    return models.EntryList.model_validate(
        library_ops.preview(criteria_for(request.filters), request.limit or 0))


@router.post("/scan", summary="Rebuild game metadata from VPSdb", status_code=202,
             dependencies=[requires(scopes.GAMES_WRITE)])
def scan(response: Response,
         request: models.ScanRequest | None = Body(default=None)) -> models.JobResource:
    """Accepted, not done: the work runs on its own thread and reports on the event
    stream. The scope is games:write because that is what a scan does - it writes a .info
    for every game it can match."""
    options = request or models.ScanRequest()
    return _accepted(response, library_ops.scan(options.model_dump()))


@router.post("/auto_match", summary="Match games again from their folder names",
             dependencies=[requires(scopes.GAMES_WRITE)])
def auto_match(payload: models.AutoMatchRequest) -> models.AutoMatchResult:
    """Done when it answers: it reads the catalog on disk, never the network. A match a
    person made, or a no-match they declared, is left as it is."""
    return models.AutoMatchResult(**library_ops.auto_match(payload.game_ids))


@router.put("/private", summary="Mark a selection of games Private, or not",
            dependencies=[requires(scopes.GAMES_WRITE)])
def put_private(payload: models.PrivateSelection) -> models.PrivateSweep:
    """`PUT /games/{id}/private` for each game named. An id the library does not hold is
    a 404, and nothing is written."""
    return models.PrivateSweep.model_validate(
        game_ops.set_private_many(payload.game_ids, payload.private))


@router.put("/marks", summary="Set marks on a selection of tables or games", status_code=202,
            dependencies=[requires(scopes.GAMES_WRITE)])
def put_marks(request: Request, response: Response,
              payload: models.MarksRequest) -> models.JobResource:
    """Accepted, not done. Each edit names a table, or a game where `table_id` is left out,
    and the marks to set on it: a table takes `rating`, `tags`, `hidden`,
    `delete_nvram_on_close` and `launcher` (an empty one clears it); a game takes
    `rating`, `tags`, `favorite` and `private`. The job's result names each row written
    and each that could not be. Setting a `launcher` also needs `config:write`."""
    edits = [edit.model_dump(exclude_none=True) for edit in payload.edits]
    if any("launcher" in edit for edit in edits):
        identity = getattr(request.state, "identity", None)
        if identity is None or not identity.can(scopes.CONFIG_WRITE):
            raise ForbiddenError(t("error.auth.needs_scope", scope=scopes.CONFIG_WRITE))
    return _accepted(response, marks_ops.start(edits))


@router.post("/media/missing", summary="What getting missing media would fetch",
             dependencies=[requires(scopes.GAMES_READ)])
def missing_media(payload: models.MissingMediaRequest) -> models.MissingMedia:
    """A read, posted because a selection of ids does not fit in a query string."""
    return models.MissingMedia(**media_fill.plan(payload.game_ids))


@router.get("/media/offered", summary="Which games a source can fill a media slot for",
            dependencies=[requires(scopes.GAMES_READ)])
def fillable_media() -> models.OfferedMedia:
    """The games the fill would find a file for, by kind: Overview leaves these out of the
    games it lists as lacking that kind, since Discovery offers them."""
    return models.OfferedMedia(kinds=media_fill.offered_games())


@router.post("/media/fill", summary="Get missing media", status_code=202,
             dependencies=[requires(scopes.GAMES_WRITE)])
def fill_media(response: Response, payload: models.MediaFillRequest) -> models.JobResource:
    """Accepted, not done. Fills gaps only: a file already there is never replaced."""
    slots = [(slot.game_id, slot.kind) for slot in payload.slots]
    return _accepted(response, media_fill.start(payload.game_ids, payload.kinds, slots))


@router.post("/media/rotate", summary="Turn pictures and videos", status_code=202,
             dependencies=[requires(scopes.GAMES_WRITE)])
def rotate_media(response: Response, payload: models.MediaRotateRequest) -> models.JobResource:
    """Accepted, not done. Each file is replaced by its turned version; with `keep` the
    file is copied into the backups folder first, and `/backups` lists it. A file that
    cannot be turned is left as it was and named in the job's result."""
    targets = [media_rotate.Target(one.game_id, one.path) for one in payload.files]
    return _accepted(response, media_rotate.start(targets, payload.turn, payload.keep))


def _convert_arguments(payload: models.MediaConvertRequest) -> tuple[
        list[media_convert.Target], dict[str, object], list[media_convert.Device] | None]:
    chosen = payload.model_dump(exclude={"files", "devices"}, exclude_none=True)
    devices = ([media_convert.Device(one.label, one.plays) for one in payload.devices]
               if payload.devices else None)
    return [media_convert.Target(one.game_id, one.path) for one in payload.files], chosen, devices


@router.post("/media/convertible", summary="What converting these files would do",
             dependencies=[requires(scopes.GAMES_READ)])
def plan_media_convert(payload: models.MediaConvertRequest) -> models.MediaConvertPlan:
    """Nothing is changed. Each file is named with what would happen to it, and each choice
    that applies is listed with its options, one recommended, and a sentence for each."""
    targets, chosen, devices = _convert_arguments(payload)
    return models.MediaConvertPlan.model_validate(
        media_convert.plan(targets, chosen, devices).made())


@router.post("/media/convert", summary="Convert pictures, sounds and videos", status_code=202,
             dependencies=[requires(scopes.GAMES_WRITE)])
def convert_media(response: Response, payload: models.MediaConvertRequest) -> models.JobResource:
    """Accepted, not done. Each file is converted whole or left as it was, and a choice left
    out takes its recommendation, named in the job's result. With `keep` the original is
    copied into the backups folder first, and `/backups` lists it."""
    targets, chosen, devices = _convert_arguments(payload)
    return _accepted(response, media_convert.start(targets, chosen, devices))


@router.get("/dismissed", summary="Which Overview rows are set aside, and over what",
            dependencies=[requires(scopes.GAMES_READ)])
def get_dismissed() -> models.Dismissals:
    return models.Dismissals.model_validate(library_ops.dismissed())


@router.put("/dismissed/{row}", summary="Set an Overview row aside over these ids",
            dependencies=[requires(scopes.GAMES_WRITE)])
def put_dismissed(row: str, payload: models.DismissalRequest) -> models.Dismissals:
    """Replaces what the row was dismissed over. The row stays away while every id it
    counts is in the list; none at all brings it back."""
    return models.Dismissals.model_validate(library_ops.dismiss(row, payload.ids))


@router.get("/vps_state", summary="What the catalog lists across the whole library",
            dependencies=[requires(scopes.GAMES_READ)])
def library_vps_state() -> models.LibraryVpsState:
    """The rollup from the last comparison, and when. Never live: a request that ran it would be
    seconds slow on any real library. `computed` empty means it has never run, which is
    not the same as every count being zero."""
    return models.LibraryVpsState(**library_ops.vps_rollup())


@router.get("/discovery", summary="What the catalog lists, against what the library holds",
            dependencies=[requires(scopes.GAMES_READ)])
def library_discovery() -> models.LibraryDiscovery:
    """One row per catalog entry, from the last comparison and never compared here. `counted`
    empty means it has never run, and `in_library` is then null on every row rather than
    false."""
    return models.LibraryDiscovery.model_validate(library_ops.vps_discovery())


@router.post("/vps_state", summary="Compare with VPS again", status_code=202,
             dependencies=[requires(scopes.GAMES_READ)])
def recount_vps_state(response: Response) -> models.JobResource:
    """Accepted, not done. Read-only work, so it takes its own job kind and may run beside
    a scan rather than queueing behind one."""
    return _accepted(response, library_ops.recount_vps_state())


@router.post("/refresh", summary="Find tables added or removed on disk", status_code=202,
             dependencies=[requires(scopes.GAMES_WRITE)])
def refresh(response: Response) -> models.JobResource:
    """Accepted, not done. The local counterpart to a scan: it re-reads the folders, gives
    every .vpx it finds an id, notes the ones that are gone, and reads whatever nothing has
    read yet. It never touches the network."""
    return _accepted(response, library_ops.refresh())


@router.get("/info", summary="What the library's metadata files need",
            dependencies=[requires(scopes.GAMES_READ)])
def info_maintenance() -> models.InfoMaintenance:
    return models.InfoMaintenance.model_validate(library_ops.info_maintenance())


@router.post("/info/upgrade", summary="Upgrade every .info to the current format",
             status_code=202, dependencies=[requires(scopes.GAMES_WRITE)])
def upgrade_info(response: Response) -> models.JobResource:
    """Accepted, not done. Each file is copied to `<name>.info.vpinfe-<stamp>` before it
    is rewritten, and `/backups` lists it."""
    return _accepted(response, library_ops.one_info_pass(
        game_service.upgrade_info, t("said.title_info_upgrade")))


@router.get("/patches", summary="What script fixes are published for this library",
            dependencies=[requires(scopes.GAMES_READ)])
def script_patches() -> models.ScriptPatches:
    """What the daily check found, kept in the cache and never asked of the index here. Its
    own read rather than a flag on the apply, because what is being decided is whether to
    let something write a file into every game folder that needs one. Being able to ask
    first is the difference between a confirm and a leap."""
    return models.ScriptPatches(**library_ops.offered_patches())


@router.post("/patches", summary="Fetch the published script fixes", status_code=202,
             dependencies=[requires(scopes.GAMES_WRITE)])
def apply_script_patches(response: Response,
                         request: models.PatchRequest | None = Body(default=None)
                         ) -> models.JobResource:
    """Accepted, not done. A table that already has a sidecar is left alone and recorded
    as patched: whatever is in that file is what the table runs, and replacing it would
    overwrite somebody else's work."""
    options = request or models.PatchRequest()
    return _accepted(response, library_ops.apply_script_patches(options.games))
