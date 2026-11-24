"""Every setting this install has, and what it is currently set to.

`common/settings/config_service.py` describes, reads and writes settings for every surface. This
is the HTTP half: a client renders a settings page from what the install says about
itself instead of from a list it carries.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from fastapi import APIRouter, Body, Request, Response

from common import events
from common.capture import settings as capture_settings
from common.config import config_schema
from common.config.paths import get_ini_config
from common.failures import why
from common.host import get_ffmpeg, tools
from common.i18n import t
from common.online import theme_service
from common.settings import config_service, settings_waiting
from frontend import theme_windows

from . import jobs as jobs_api
from . import models, protected_settings, scopes
from .auth import require, requires
from .errors import ConflictError, InvalidRequestError, NotFoundError
from .events import declare_snapshot

router = APIRouter(prefix="/config", tags=["config"])

SECRET_SETTINGS = config_schema.SECRET_SETTINGS


def _hide_secrets(settings: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    hidden = {section: dict(entries) for section, entries in settings.items()}
    for section, key in SECRET_SETTINGS:
        if key in hidden.get(section, {}):
            hidden[section][key] = bool(str(hidden[section][key] or "").strip())
    return hidden


def _config_values(found: dict[str, Any]) -> models.ConfigValues:
    shown = {**found, config_service.SETTINGS: _hide_secrets(found[config_service.SETTINGS])}
    return models.ConfigValues.model_validate(shown)


def _windows() -> tuple[str, ...]:
    """The windows the active theme adds, which the schema cannot name in advance."""
    return theme_windows.own_windows(theme_windows.active(get_ini_config()))


@router.get("/schema", summary="Every setting this install has",
            dependencies=[requires(scopes.CONFIG_READ)])
def get_schema() -> models.ConfigSchema:
    """What a settings page is built from. Internal options are left out."""
    return models.ConfigSchema.model_validate(config_service.schema(_windows()))


@router.get("/paths", summary="Whether each path setting finds anything",
            dependencies=[requires(scopes.CONFIG_READ)], response_model_exclude_none=True)
def get_path_checks() -> models.ConfigPathChecks:
    """Every path setting and every path in a list's items, checked against this device's
    disk.

    The caller names no path. It asks about settings, and the install answers about the
    values it holds - so this cannot be used to ask whether a file exists somewhere a
    caller is not otherwise allowed to look.
    """
    return models.ConfigPathChecks.model_validate(config_service.path_states())


@router.get("/tools", summary="What each program VPinFE runs is, found or not",
            dependencies=[requires(scopes.CONFIG_READ)])
def get_tools() -> models.ConfigTools:
    """Looked for when asked, so a program installed a moment ago is found."""
    return models.ConfigTools.model_validate({"tools": tools.report()})


@router.post("/tools/{tool_id}/get", summary="Download a Tool VPinFE can get itself",
             status_code=202, dependencies=[requires(scopes.PROTECTED)])
def get_tool(tool_id: str, response: Response) -> models.JobResource:
    """Accepted, not done. The request is the consent. Only FFmpeg, on Windows and macOS,
    can be got."""
    if tool_id != tools.FFMPEG.id:
        raise NotFoundError(t("error.config.tool_unavailable", tool=tool_id))
    job = get_ffmpeg.start()
    response.headers["Location"] = f"/api/v1/jobs/{job.id}"
    return models.JobResource(**jobs_api.resource(job))


@router.get("/waiting", summary="What is saved and not yet in effect",
            dependencies=[requires(scopes.CONFIG_READ)])
def get_waiting() -> models.ConfigWaiting:
    """Each setting that differs from what this run is using, and what it waits for: a
    `restart`, or a `reopen` of the windows. Changing a value back clears it."""
    return models.ConfigWaiting.model_validate(settings_waiting.answer())


@router.get("", summary="What this install is set to",
            dependencies=[requires(scopes.CONFIG_READ)])
def get_values() -> models.ConfigValues:
    return _config_values(config_service.whole(_windows()))


def _validated(patch: dict[str, Any]) -> dict[str, Any]:
    """The patch, once it is known to be a well-formed one. Raises what the caller is told."""
    settings = patch.get(config_service.SETTINGS)
    if settings is not None and not isinstance(settings, dict):
        raise InvalidRequestError(t("error.config.settings_object"))
    with _refusals():
        config_service.require_sections(settings or {})
    refused = capture_settings.refused(settings or {})
    if refused:
        raise InvalidRequestError(refused)
    for section, entries in (settings or {}).items():
        for key, value in (entries or {}).items():
            if isinstance(value, bool) and config_schema.locate(section, key) in SECRET_SETTINGS:
                raise InvalidRequestError(
                    t("error.config.secret_is_text", key=f"{section}.{key}"))
    return _with_theme_options_checked(patch)


def _applied(patch: dict[str, Any]) -> models.ConfigValues:
    with _refusals():
        return _config_values(config_service.apply(patch, _windows()))


def apply_patch(patch: dict[str, Any]) -> models.ConfigValues:
    """A patch written for work this install does itself, after a request already asked for a
    Confirm: the move of the backups folder."""
    return _applied(_validated(patch))


@router.put("", summary="Change settings and lists",
            dependencies=[requires(scopes.CONFIG_WRITE)])
def put_values(request: Request, patch: dict[str, Any] = Body(...)) -> models.ConfigValues:
    """A patch shaped like the read: `settings` is written key by key, and a list sent
    replaces that list. In `theme_options`, each theme named takes the values given,
    checked against what the theme declares, and the others are left alone."""
    patch = _validated(patch)
    if protected_settings.patch_change(patch, config_service.whole(_windows())):
        require(request, scopes.PROTECTED)
    return _applied(patch)


def _with_theme_options_checked(patch: dict[str, Any]) -> dict[str, Any]:
    """Each theme's values coerced to what that theme declares. A theme that is not
    installed keeps what it is given, which is what a setup copied from another machine
    holds. A body of the wrong shape is left for `apply` to refuse in words."""
    wanted = patch.get(config_service.THEME_OPTIONS)
    if not isinstance(wanted, dict) or not all(isinstance(one, dict) for one in wanted.values()):
        return patch
    try:
        coerced = {folder: theme_service.coerce_option_values(folder.strip(), values)
                   for folder, values in wanted.items()}
    except ValueError as exc:
        raise InvalidRequestError(why(exc)) from exc
    return {**patch, config_service.THEME_OPTIONS: coerced}


@router.get("/settings", summary="Every setting by section and key",
            dependencies=[requires(scopes.CONFIG_READ)])
def get_settings() -> dict[str, dict[str, object]]:
    return _hide_secrets(config_service.values(_windows()))


@router.get("/settings/{section}", summary="The settings of one section",
            dependencies=[requires(scopes.CONFIG_READ)])
def get_section(section: str) -> dict[str, object]:
    found = _hide_secrets(config_service.values(_windows())).get(section)
    if found is None:
        raise NotFoundError(t("error.config.no_section", section=section))
    return found


def _declared(key: str) -> None:
    if config_schema.config_list(key) is None:
        raise NotFoundError(t("error.config.list.not_declared", list=key))


@contextmanager
def _refusals() -> Iterator[None]:
    try:
        yield
    except config_service.UnknownSettingsError as exc:
        raise InvalidRequestError(
            t("error.config.unknown_keys", keys=", ".join(exc.keys))) from exc
    except config_service.RefusedSettingsError as exc:
        raise InvalidRequestError("; ".join(exc.reasons.values()),
                                  details={"settings": exc.reasons}) from exc
    except config_service.MalformedPatchError as exc:
        raise InvalidRequestError(why(exc)) from exc
    except config_service.ReadOnlySettingsError as exc:
        raise InvalidRequestError(
            t("error.config.read_only", keys=", ".join(exc.keys))) from exc
    except config_schema.ConfigListError as exc:
        raise InvalidRequestError(why(exc)) from exc
    except config_service.NoSuchItemError as exc:
        raise NotFoundError(t("error.config.list.no_item", id=exc.item_id)) from exc
    except config_service.SettingsWriteError as exc:
        raise ConflictError(why(exc)) from exc


@router.get("/theme_options", summary="What was chosen on each theme",
            dependencies=[requires(scopes.CONFIG_READ)])
def get_theme_options() -> dict[str, dict[str, object]]:
    """By the theme's folder name. A theme nobody changed has no entry."""
    return config_service.theme_options()


@router.get("/theme_options/{theme}", summary="What was chosen on one theme",
            dependencies=[requires(scopes.CONFIG_READ)])
def get_theme_option_values(theme: str) -> dict[str, object]:
    found = config_service.theme_options().get(theme)
    if found is None:
        raise NotFoundError(t("error.config.theme_options_none", theme=theme))
    return found


def _guard_items(request: Request, list_key: str, items: list[dict[str, Any]]) -> None:
    """A list write that sets a launcher's program or command asks for a Confirm."""
    if list_key != protected_settings.LAUNCHERS:
        return
    held = config_service.list_items(list_key)
    if protected_settings.launchers_change(items, held):
        require(request, scopes.PROTECTED)


@router.get("/{list_key}", summary="Every item of a list",
            dependencies=[requires(scopes.CONFIG_READ)])
def get_list(list_key: str) -> list[dict[str, object]]:
    _declared(list_key)
    return config_service.list_items(list_key)


@router.put("/{list_key}", summary="Replace a list",
            dependencies=[requires(scopes.CONFIG_WRITE)])
def put_list(request: Request, list_key: str,
             items: list[dict[str, Any]] = Body(...)) -> list[dict[str, object]]:
    """The whole list, in the order sent: this is how a list is reordered. An item with
    no `id` is given one."""
    _declared(list_key)
    _guard_items(request, list_key, items)
    with _refusals():
        return config_service.replace_list(list_key, items)


@router.post("/{list_key}", summary="Add an item to a list", status_code=201,
             dependencies=[requires(scopes.CONFIG_WRITE)])
def post_item(request: Request, list_key: str,
              item: dict[str, Any] = Body(...)) -> dict[str, object]:
    """The install gives the item its `id`; `PUT /{list}/{id}` is how a caller names one."""
    _declared(list_key)
    _guard_items(request, list_key, [item])
    if "id" in item:
        raise InvalidRequestError(t("error.config.list.id_given", list=list_key))
    with _refusals():
        return config_service.add_item(list_key, item)


@router.get("/{list_key}/{item_id}", summary="One item of a list",
            dependencies=[requires(scopes.CONFIG_READ)])
def get_item(list_key: str, item_id: str) -> dict[str, object]:
    _declared(list_key)
    with _refusals():
        return config_service.list_item(list_key, item_id)


@router.put("/{list_key}/{item_id}", summary="Add or replace an item",
            dependencies=[requires(scopes.CONFIG_WRITE)])
def put_item(request: Request, list_key: str, item_id: str,
             item: dict[str, Any] = Body(...)) -> dict[str, object]:
    """Replaces the item with that id where it stands, or adds it under that id."""
    _declared(list_key)
    _guard_items(request, list_key, [{**item, "id": item_id}])
    if item.get("id", item_id) != item_id:
        raise InvalidRequestError(t("error.config.list.id_differs"))
    with _refusals():
        return config_service.put_item(list_key, item_id, item)


@router.delete("/{list_key}/{item_id}", summary="Remove an item", status_code=204,
               dependencies=[requires(scopes.CONFIG_WRITE)])
def delete_item(list_key: str, item_id: str) -> Response:
    _declared(list_key)
    with _refusals():
        config_service.delete_item(list_key, item_id)
    return Response(status_code=204)


def declare_snapshots() -> None:
    declare_snapshot(events.CONFIG_WAITING_CHANGED, settings_waiting.answer)
