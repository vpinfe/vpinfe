"""Discovery: the front page, in one read.

What `/library/discovery` answers is every catalog entry; this answers the shelves a front
page draws from them, cut to what a row of tiles shows, from the last comparison. The
gaps a source can fill are the library's games now and the index the fill uses as the
process already holds it; this read makes no request. The marks a person puts on what it
lists, Wanted or Ignored, are kept here and leave the shelves they name.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from common.games import library_ops

from . import models, scopes
from .auth import requires

router = APIRouter(prefix="/discovery", tags=["discovery"])

# The longest window a shelf looks back over. The page offers a week, two and a month.
MOST_DAYS = 366


@router.get("/marks", summary="What is marked Wanted or Ignored",
            dependencies=[requires(scopes.GAMES_READ)])
def get_marks() -> models.DiscoveryMarks:
    """Every mark on this install, the newest first. `games:read`."""
    return models.DiscoveryMarks.model_validate(library_ops.discovery_marks())


@router.put("/marks", summary="Mark a game, release, gap or update Wanted or Ignored",
            dependencies=[requires(scopes.GAMES_WRITE)])
def put_mark(payload: models.DiscoveryMarkRequest) -> models.DiscoveryMarks:
    """A thing is unmarked, Wanted or Ignored, never two: marking it replaces the state it
    held, which is answered as `replaced`. `400` for a state or scope that does not exist,
    for Wanted on anything but a game or a release, for a blank `id`, and for a `detail`
    that does not suit the scope. A `version` mark skips that version of a release's update
    and an `updates` mark ignores every later one; on a picture a source has replaced they take
    `<entry>:<kind>` as the id, and `GET /pictures` reads them."""
    return models.DiscoveryMarks.model_validate(library_ops.mark_discovery(
        payload.state, payload.scope, payload.id, payload.detail))


@router.delete("/marks", summary="Take the mark off a thing",
               dependencies=[requires(scopes.GAMES_WRITE)])
def delete_mark(scope: str = Query(...), vps_id: str = Query(..., alias="id"),
                detail: str = Query("")) -> models.DiscoveryMarks:
    """Unmarking a thing nobody marked is not an error: `replaced` is then empty. The thing
    is named as `PUT` names it, and `400` for a scope that does not exist, a blank `id` or a
    `detail` that does not suit the scope. `422` when `scope` or `id` is missing."""
    return models.DiscoveryMarks.model_validate(library_ops.unmark_discovery(scope, vps_id, detail))


@router.get("/updates", summary="Games with a later version of something they hold",
            dependencies=[requires(scopes.GAMES_READ)])
def discovery_updates(game_id: str = Query(""),
                      set_aside: bool = Query(False)) -> models.HomeUpdates:
    """Every game of the library holding a file tied to a release VPS now lists at a higher
    version than when the tie was made, or one a source says has a later file, minus what is
    skipped or ignored. The count is what Discovery's menu entry shows. `game_id` narrows the
    answer to that one game, and only that game is looked at. With `set_aside` the moves a
    person skipped or ignored stay, each saying which in its `set_aside`. Nothing is asked of
    any site."""
    return models.HomeUpdates.model_validate(
        library_ops.discovery_updates(game_id, set_aside))


@router.get("/home", summary="Discovery's front page",
            dependencies=[requires(scopes.GAMES_READ)])
def discovery_home(days: int = Query(7, ge=1, le=MOST_DAYS)) -> models.DiscoveryHome:
    """New in the last `days`, the authors whose work the library holds, and the games that
    lack a kind VPS lists, the games it has none of, minus whatever is ignored, what is wanted
    and the games with a later version of something they hold. Never compared here: `counted`
    empty means the library has not been, and nothing then reads as held or lacking."""
    return models.DiscoveryHome.model_validate(library_ops.discovery_home(days))


@router.get("/missing", summary="Home's missing tabs, as counts and ids",
            dependencies=[requires(scopes.GAMES_READ)])
def discovery_missing() -> models.DiscoveryMissing:
    """What `/discovery/home` answers under `missing`, without the games to draw: for each tab
    `count`, `on_vps` and `game_ids`, and `counted`. A page that counts what a library lacks
    reads this and leaves the rest of Home unbuilt."""
    return models.DiscoveryMissing.model_validate(library_ops.discovery_missing())


@router.get("/pictures", summary="Pictures a source has replaced since they were placed",
            dependencies=[requires(scopes.GAMES_READ)])
def picture_changes(game_id: str = Query("")) -> models.PictureChanges:
    """Each picture VPinFE placed and has not seen changed that its source no longer
    publishes, from the last daily check, of the game `game_id` or of every game. Taking one
    is the fetch of its `kind`, `source` and `size`. Asks no site. Empty while Update
    Downloaded Media is on, which replaces them itself, and while the source is off."""
    return models.PictureChanges.model_validate(library_ops.picture_changes(game_id))
