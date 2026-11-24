"""Collections: the groupings a user makes over their library.

Two kinds behind one resource. A **manual** collection stores an explicit list of game
ids. A **filter** collection stores criteria and resolves to whatever matches when you
ask - so it has no member list to add to, and `PUT .../games/{id}` on one is refused
rather than silently doing nothing.

`common/games/collection_ops.py` is what answers. Here is the wire: the path, the scope,
the model, and turning a request's filter block into the criteria the store reads.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, File, Request, Response, UploadFile
from starlette.concurrency import run_in_threadpool

from common.games import collection_ops

from . import models, scopes
from .auth import requires
from .criteria import criteria_for
from .responses import MEDIA_SIZE, MEDIA_VERSION, media_file

router = APIRouter(prefix="/collections", tags=["collections"])


def _criteria_order(filters: models.CollectionFilters) -> dict:
    sent = filters.model_fields_set
    return {key: getattr(filters, field)
            for key, field in (("by", "order_by"), ("direction", "direction"))
            if field in sent}


@router.get("", summary="List collections",
            dependencies=[requires(scopes.COLLECTIONS_READ)])
def list_collections() -> models.CollectionList:
    return models.CollectionList.model_validate(collection_ops.listing())


@router.patch("", summary="Set the order of the collections",
              dependencies=[requires(scopes.COLLECTIONS_WRITE)])
def arrange_collections(
        request: models.CollectionsArrangementRequest = Body(...)) -> models.CollectionList:
    """Every collection named once, in the order the frontend shows them."""
    return models.CollectionList.model_validate(
        collection_ops.arrange_collections(request.order))


@router.get("/{name}", summary="One collection",
            dependencies=[requires(scopes.COLLECTIONS_READ)])
def get_collection(name: str) -> models.CollectionResource:
    return models.CollectionResource(**collection_ops.resource(name))


@router.get("/{name}/games", summary="The games in a collection",
            dependencies=[requires(scopes.COLLECTIONS_READ)])
def collection_games(name: str) -> models.GameList:
    """Resolved membership, so a filter collection answers the same question a manual one
    does. Ordering is the collection's own."""
    return models.GameList.model_validate(collection_ops.games_in(name))


@router.get("/{name}/members", summary="A collection's stored membership, and why",
            dependencies=[requires(scopes.COLLECTIONS_READ)])
def collection_members(name: str) -> models.CollectionMemberList:
    """What is written down, not what resolved - which is the difference an editor needs
    and every other lens hides. A member naming something this library no longer has is a
    row with `origin: "missing"`, not an absence."""
    return models.CollectionMemberList.model_validate(collection_ops.members_of(name))


@router.post("/{name}/members/preview",
             summary="A collection's membership with other rules, storing nothing",
             dependencies=[requires(scopes.COLLECTIONS_READ)])
def preview_members(name: str, request: models.MembersPreviewRequest = Body(...),
                    ) -> models.CollectionMemberPreview:
    return models.CollectionMemberPreview.model_validate(
        collection_ops.preview_members(name, criteria_for(request.filters)))


@router.get("/{name}/entries", summary="The entries a collection resolves to",
            dependencies=[requires(scopes.COLLECTIONS_READ)])
def collection_entries(name: str) -> models.EntryList:
    """The play lens: what a frontend would show, in the order it would show it. One entry
    per game; `GET /games/{id}/tables` lists the rest of a game's tables."""
    return models.EntryList.model_validate(collection_ops.entries_of(name))


@router.post("", summary="Create a collection", status_code=201,
             dependencies=[requires(scopes.COLLECTIONS_WRITE)])
def create_collection(response: Response,
                      request: models.CreateCollectionRequest = Body(...),
                      ) -> models.CollectionResource:
    made = collection_ops.create(
        request.name, request.games, request.description,
        criteria_for(request.filters) if request.filters is not None else None,
        _criteria_order(request.filters) if request.filters is not None else None,
        request.copy_of)
    response.headers["Location"] = made["links"]["self"]
    return models.CollectionResource(**made)


@router.delete("/{name}", summary="Delete a collection", status_code=204,
               dependencies=[requires(scopes.COLLECTIONS_WRITE)])
def delete_collection(name: str) -> Response:
    collection_ops.delete(name)
    return Response(status_code=204)


@router.put("/{name}/games/{game_id}", summary="Add a game to a collection",
            status_code=204, dependencies=[requires(scopes.COLLECTIONS_WRITE)])
def add_member(name: str, game_id: str,
               request: models.MemberRequest | None = Body(default=None)) -> Response:
    """Idempotent: adding a game that is already a member is a success, because the
    caller's intent - that it be in there - is satisfied either way.

    Works on any collection. Criteria and named members are combinable, and a member
    overrides what the criteria say for that game.
    """
    collection_ops.add_member(name, game_id, (request.table if request else "") or "",
                              request.after_table if request else None)
    return Response(status_code=204)


@router.put("/{name}/games/{game_id}/table",
            summary="Set which table a member names", status_code=204,
            dependencies=[requires(scopes.COLLECTIONS_WRITE)])
def set_member_table(name: str, game_id: str,
                     request: models.MemberTableRequest = Body(...)) -> Response:
    collection_ops.set_member_table(name, game_id, request.table, request.was)
    return Response(status_code=204)


@router.delete("/{name}/games/{game_id}", summary="Remove a game from a collection",
               status_code=204, dependencies=[requires(scopes.COLLECTIONS_WRITE)])
def remove_member(name: str, game_id: str, table: str | None = None) -> Response:
    """`?table=` removes exactly one row, `?table=` with no value the one that names no
    table, and omitting it entirely removes every ref naming this game."""
    collection_ops.remove_member(name, game_id, table)
    return Response(status_code=204)


@router.put("/{name}/excluded/{game_id}", summary="Exclude a game from a collection",
            status_code=204, dependencies=[requires(scopes.COLLECTIONS_WRITE)])
def add_exclusion(name: str, game_id: str,
                  request: models.MemberRequest | None = Body(default=None)) -> Response:
    """Take something out of what the criteria matched, and keep taking it out."""
    collection_ops.exclude(name, game_id, (request.table if request else "") or "")
    return Response(status_code=204)


@router.delete("/{name}/excluded/{game_id}", summary="Stop excluding a game",
               status_code=204, dependencies=[requires(scopes.COLLECTIONS_WRITE)])
def remove_exclusion(name: str, game_id: str, table: str | None = None) -> Response:
    """`?table=abc` lifts one exclusion, `?table=` with no value the one that names no
    table, and omitting it lifts every exclusion naming this game."""
    collection_ops.unexclude(name, game_id, table)
    return Response(status_code=204)


@router.put("/{name}/image", summary="Set a collection's image",
            dependencies=[requires(scopes.COLLECTIONS_WRITE)])
async def set_image(name: str, file: UploadFile = File(...)
                    ) -> models.CollectionResource:
    """Upload an icon and hang it on this collection.

    Served under /api/v1 rather than only in one surface's own static tree: the filename
    is on the resource, so a client outside that process has to be able to write one and
    fetch it back.
    """
    content = await file.read()
    return models.CollectionResource(**await run_in_threadpool(
        collection_ops.set_image, name, file.filename or "", content))


@router.delete("/{name}/image", summary="Clear a collection's image",
               status_code=204, dependencies=[requires(scopes.COLLECTIONS_WRITE)])
def clear_image(name: str) -> Response:
    collection_ops.clear_image(name)
    return Response(status_code=204)


@router.get("/{name}/image", summary="A collection's image",
            dependencies=[requires(scopes.COLLECTIONS_READ)])
def get_image(name: str, request: Request, size: int | None = MEDIA_SIZE,
              v: str = MEDIA_VERSION) -> Response:
    return media_file(collection_ops.image_path(name, size), request, v)


@router.post("/{name}/members/from_filters",
             summary="Keep what the criteria match, and drop the criteria",
             dependencies=[requires(scopes.COLLECTIONS_WRITE)])
def members_from_filters(name: str) -> models.CollectionResource:
    """Criteria as a way of building a list rather than a rule to keep. What they match
    right now becomes the membership, and the criteria and exclusions go."""
    return models.CollectionResource(**collection_ops.keep_result(name))


@router.patch("/{name}", summary="Change a collection",
              dependencies=[requires(scopes.COLLECTIONS_WRITE)])
def patch_collection(name: str,
                     request: models.PatchCollectionRequest = Body(...),
                     ) -> models.CollectionResource:
    """Name, image, criteria, membership and cap, in one place.

    A patch: only what is sent is written. Renaming should not require restating a
    collection's criteria, and a client that has to send the whole thing back is a client
    racing whatever else edited it meanwhile.
    """
    filters = request.filters
    return models.CollectionResource(**collection_ops.patch(
        name,
        new_name=request.name,
        games=request.games,
        criteria=criteria_for(filters) if filters is not None else None,
        criteria_order=_criteria_order(filters) if filters is not None else None,
        clear_criteria=request.clear_filters,
        limit=request.limit,
        clear_limit=request.clear_limit,
        description=request.description,
        image=request.image,
        in_frontend=request.in_frontend,
        order_by=request.order_by,
        direction=request.direction,
        paging_group=request.paging_group))


@router.put("/{name}/order", summary="Set the order of a collection's games",
            status_code=204, dependencies=[requires(scopes.COLLECTIONS_WRITE)])
def set_order(name: str,
              request: models.CollectionOrderRequest = Body(...)) -> Response:
    """The whole list, in order, atomically. Every id must already be a member, and the
    list is one entry per row - a game holding two named tables is named twice."""
    collection_ops.set_arrangement(name, request.games)
    return Response(status_code=204)
