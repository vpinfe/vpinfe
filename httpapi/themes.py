"""Frontend themes: what is available, what is installed, and which one plays, over the
wire.

`common/online/theme_ops.py` holds the registry and does the work. What is here is the
wire, and the two statuses only this layer can give: a source that will not load is 503
because nothing about this install is wrong, and an install or removal that failed
upstream is 502.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from fastapi import APIRouter, Body
from starlette.requests import Request
from starlette.responses import Response

from common.failures import why
from common.i18n import t
from common.media import sized_media
from common.online import theme_ops

from . import scopes
from .auth import requires
from .errors import ApiError, NotFoundError
from .responses import MEDIA_SIZE, MEDIA_VERSION, media_file

router = APIRouter(prefix="/themes", tags=["themes"])


@contextmanager
def _sources_read() -> Iterator[None]:
    try:
        yield
    except theme_ops.SourceUnavailableError as exc:
        raise ApiError("theme_source_unavailable", why(exc), status_code=503) from exc


@router.get("", summary="Every theme this install knows",
            dependencies=[requires(scopes.CONFIG_READ)])
def list_themes(refresh: bool = False) -> dict[str, Any]:
    """Active first, then installed, then the rest.

    `refresh` re-reads the sources now. Without it the answer is the last read, which is
    kept between restarts and repeated on the schedule set in `themes.refresh`.
    """
    with _sources_read():
        return theme_ops.listing(refresh)


@router.get("/unaccepted", summary="Installed themes that are stopped until accepted",
            dependencies=[requires(scopes.CONFIG_READ)])
def unaccepted() -> dict[str, Any]:
    """The themes in the themes folder that nobody has accepted, as `themes` of `key` and
    `name`. Reads the folder alone, so it never reaches the network."""
    return theme_ops.unaccepted()


@router.get("/{key}/preview", summary="A theme's preview picture",
            dependencies=[requires(scopes.CONFIG_READ)])
def get_preview(key: str, request: Request, size: int | None = MEDIA_SIZE,
                v: str = MEDIA_VERSION, layout: str = "") -> Response:
    """The picture the theme's manifest names, at `size` (256 or 1024) when given. `layout`
    (`desktop` or `cab`) asks for that layout's picture, and gets the theme's main one when
    it has none of its own. A theme not installed has it fetched from its host once per
    release and kept; `v` is the `preview_version` the listing gives for that picture, and
    a request that carries the current one may be kept for good. 404 for a theme with no
    picture, or one that cannot be fetched."""
    size_or = sized_media.size_or_refuse(size, "image")
    served = theme_ops.preview(key, size_or, layout)
    if served is None:
        raise NotFoundError(t("error.themes.no_preview", key=key))
    return media_file(served, request, v)


@router.get("/sources", summary="What each theme source said when it was last read",
            dependencies=[requires(scopes.CONFIG_READ)])
def sources() -> dict[str, Any]:
    """The configured sources in their order, as `registries` (lists of themes) and
    `repositories` (single themes). Each carries its kind, a `state`, a `level` to color
    it by, and a `reason` in words. Reads nothing: a source never read is `not_checked`."""
    return theme_ops.sources()


@router.post("/sources/check", summary="What adding an address would be",
             dependencies=[requires(scopes.PROTECTED)])
def check_source(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Reads `address` and says what it is, without saving it: a list or a theme, the
    address it would be stored under, and `addable`. Nothing is downloaded or installed.
    Saving is `PUT /config`."""
    return theme_ops.check_source(str(body.get("address") or ""))


@router.put("/sources/name", summary="Call a theme source something",
            dependencies=[requires(scopes.CONFIG_WRITE)])
def rename_source(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Gives the added source at `address` the name `name`, which replaces what it is
    shown as. A blank `name` takes it back to what the source is called without one.
    The address lists in `PUT /config` are unchanged: a name is kept apart from them. An
    address that is not added is 404."""
    return theme_ops.rename_source(str(body.get("address") or ""), str(body.get("name") or ""))


@router.post("/{key}/install", summary="Install or update a theme",
             dependencies=[requires(scopes.CONFIG_WRITE)])
def install(key: str) -> dict[str, Any]:
    """One call for both. Installing over an existing copy is what an update is, and two
    endpoints doing it would be two names for one act."""
    try:
        with _sources_read():
            return theme_ops.install(key)
    except theme_ops.InstallFailedError as exc:
        raise ApiError("theme_install_failed", why(exc), status_code=502) from exc


@router.post("/{key}/accept", summary="Accept a theme VPinFE did not list",
             dependencies=[requires(scopes.PROTECTED)])
def accept(key: str) -> dict[str, Any]:
    """Accepts the theme from the address this install knows it by: a source that is not the
    VPinFE Theme List, or the themes folder for one no source offers. Returns its `trust`.
    A theme from a different address asks again."""
    with _sources_read():
        return theme_ops.accept(key)


@router.delete("/{key}", summary="Remove an installed theme",
               dependencies=[requires(scopes.CONFIG_WRITE)])
def remove(key: str) -> dict[str, Any]:
    try:
        with _sources_read():
            return theme_ops.remove(key)
    except theme_ops.RemoveFailedError as exc:
        raise ApiError("theme_remove_failed", why(exc), status_code=502) from exc


@router.put("/active", summary="Choose which theme the frontend plays",
            dependencies=[requires(scopes.CONFIG_WRITE)])
def activate(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Written to the config. It takes effect when the frontend next starts, which the
    caller is expected to say - this endpoint changes a setting rather than restarting
    anything."""
    with _sources_read():
        return theme_ops.activate(str(body.get("key") or ""))


@router.get("/{key}/options", summary="A theme's own settings",
            dependencies=[requires(scopes.CONFIG_READ)])
def options(key: str) -> dict[str, Any]:
    """The schema a theme declares, and what it is currently set to. Its own shape rather
    than the install's config schema: a theme can declare a control this install has
    never heard of."""
    with _sources_read():
        return theme_ops.options(key)


@router.put("/{key}/options", summary="Change a theme's own settings",
            dependencies=[requires(scopes.CONFIG_WRITE)])
def save_options(key: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Kept beside the config rather than inside the theme, because the package is
    deleted by an update and values written into it were reset by the next one."""
    with _sources_read():
        return theme_ops.save_options(key, dict(body.get("values") or {}))
