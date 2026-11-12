"""The configured ways this install runs a table, over the wire.

The launchers themselves are the `launchers` list in `/config`: add, replace and remove
are `/config/launchers/{id}`. What stays here is what a list item cannot say - the
refusal a switch-off would get, the app's own settings, the default, and which table
runs on which.

Writing a field that decides what runs (the program, its flags and environment, a command
around a table) is Protected; the rest keeps the config scopes.

`common/games/launcher_ops.py` is what answers.
"""

from __future__ import annotations

import base64
import binascii
from typing import Any

from fastapi import APIRouter, Body, Request, Response

from common import service_errors
from common.games import launcher_ops, launcher_settings, upgrade_ops
from common.i18n import t
from common.runtime import jobs

from . import jobs as jobs_api
from . import protected_settings, scopes
from .auth import require, requires

router = APIRouter(prefix="/launchers", tags=["launchers"])


@router.get("", summary="Every launcher this install has",
            dependencies=[requires(scopes.CONFIG_READ)])
def list_launchers() -> dict[str, Any]:
    """The launchers in order, the tables that deviate, and which one is the default."""
    return launcher_ops.listing()


@router.post("/standing", summary="What an app's empty fields would read",
             dependencies=[requires(scopes.CONFIG_READ)])
def launcher_standing(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """For an app and the settings a launcher would hold, what each path field reads
    while left empty. A read: nothing is written, and no launcher need exist."""
    return launcher_ops.standing(str(body.get("app") or ""),
                                 dict(body.get("settings") or {}))


@router.get("/{launcher_id}/fallback", summary="Where its tables go if it is switched off",
            dependencies=[requires(scopes.CONFIG_READ)])
def launcher_fallback(launcher_id: str) -> dict[str, Any]:
    """How many tables it plays, what each group of them would use instead, and the
    refusal a switch-off would get, or ""."""
    return launcher_ops.fallback(launcher_id)


@router.get("/{launcher_id}/leftovers", summary="What an upgrade of its program left behind",
            dependencies=[requires(scopes.CONFIG_READ)])
def launcher_leftovers(launcher_id: str) -> dict[str, Any]:
    """The settings its program no longer reads, each with `action` - `move`, `place`,
    `turn_on` or `name` - what it is in words, a `warning` where moving it costs
    something, and for a `place` how many backglass files would take it."""
    return upgrade_ops.listing(launcher_id)


@router.post("/{launcher_id}/leftovers/move", summary="Move what an upgrade left behind",
             dependencies=[requires(scopes.CONFIG_WRITE), requires(scopes.GAMES_WRITE)])
def move_launcher_leftovers(launcher_id: str) -> dict[str, Any]:
    """Every `move` to its new name and every `place` into the backglass files, then the
    old settings blanked. A box whose files cannot all be written, or that is one box too many
    for the undo journal, keeps its settings and is named under `held_back`, with the files
    that refused or the `limit`. `undo` is the token for the
    undo route, empty where nothing was written."""
    return upgrade_ops.move(launcher_id)


@router.post("/{launcher_id}/leftovers/turn-on",
             summary="Turn on a setting an upgrade left switched off",
             dependencies=[requires(scopes.CONFIG_WRITE)])
def turn_on_launcher_leftover(launcher_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """`{"key"}` names a `turn_on` row. Answers the token that undoes it."""
    return upgrade_ops.turn_on(launcher_id, str(body.get("key") or ""))


@router.post("/{launcher_id}/leftovers/undo", summary="Put back what a move changed",
             dependencies=[requires(scopes.CONFIG_WRITE), requires(scopes.GAMES_WRITE)])
def undo_launcher_leftovers(launcher_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """`{"undo"}` is the token a move answered. The backglass files go back byte for byte
    and then the settings. Refused with a `409` while a setting has been changed since or a
    file cannot be put back, naming the files; those that could be are back."""
    return upgrade_ops.undo(launcher_id, str(body.get("undo") or ""))


@router.get("/{launcher_id}/config", summary="What the app it runs can be set to",
            dependencies=[requires(scopes.CONFIG_READ)])
def launcher_config(launcher_id: str, table: str = "",
                    scope: str = "launcher") -> dict[str, Any]:
    """The declared groups, and every value as it stands at one scope."""
    return launcher_ops.app_config(launcher_id, table, scope)


@router.put("/{launcher_id}/config", summary="Set values on the app it runs",
            dependencies=[requires(scopes.CONFIG_WRITE)])
def write_launcher_config(request: Request, response: Response, launcher_id: str,
                          body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Values at one scope. The app writes them into its own file in place, and names
    under `cleared` the ones it cleared instead, for holding the launcher's own value.

    `tables` in place of `table` writes the same values into each of them. With more than
    one that is accepted, not done: a job of kind `table_settings` answers, whose result
    names the tables written and each that could not be."""
    if protected_settings.launcher_body_protected(body):
        require(request, scopes.PROTECTED)
    done = launcher_ops.write_config(launcher_id, body)
    if isinstance(done, jobs.Job):
        response.status_code = 202
        response.headers["Location"] = f"/api/v1/jobs/{done.id}"
        return jobs_api.resource(done)
    return done


@router.post("/{launcher_id}/config/file", summary="Have its program write its settings file",
             dependencies=[requires(scopes.CONFIG_WRITE)])
def make_launcher_settings_file(launcher_id: str) -> dict[str, Any]:
    """Runs the program once, with the flag that makes it write its settings file and
    exit, and answers `{"files"}`. Never done by itself."""
    return launcher_ops.make_settings_file(launcher_id)


@router.post("/settings-file/undo", summary="Undo a change of settings file",
             dependencies=[requires(scopes.CONFIG_WRITE)])
def undo_launcher_settings_file(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Puts the launcher and its files back as they were before the change that gave
    `undo`. A token works once, and is gone when the server restarts."""
    return launcher_settings.undo(str(body.get("undo") or ""))


@router.post("/settings-file/places", summary="Where a settings file is looked for first",
             dependencies=[requires(scopes.CONFIG_READ)])
def launcher_settings_file_places(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """For an app and the settings a launcher holds: VPinFE's settings folder, marked
    `default`, and the folder its program keeps its own settings in."""
    return {"places": launcher_settings.places(str(body.get("app") or ""),
                                               dict(body.get("settings") or {}))}


@router.post("/{launcher_id}/settings-file", summary="Give it a settings file",
             dependencies=[requires(scopes.CONFIG_WRITE)])
def set_launcher_settings_file(launcher_id: str, request: Request,
                               body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """`choice` is `copy` (from `copy_from`, a launcher id, or `copy_file`), `fresh` (the
    program writes a standard one) or `pick` (`existing`). A copy or a fresh file goes to
    `save_dir` and `file_name`, which default to VPinFE's settings folder and the launcher's
    name. A launcher not yet added is named by `create`, and is added with its file.

    Where the file written over is one other launchers play with or the program's own, it
    answers `{"needs_replace"}` and changes nothing until `replace` is true. Otherwise
    `{"launcher", "file", "backed_up", "replaced", "undo"}`; `undo` is for the undo route,
    and is empty after a plain add.

    A `create` that sets the program or command a launcher runs needs `protected:write`,
    as adding the launcher through the config does."""
    create = body.get("create")
    if isinstance(create, dict) and protected_settings.launcher_body_protected(
            {"values": create.get("settings")}):
        require(request, scopes.PROTECTED)
    return launcher_settings.apply(launcher_id, body)


@router.post("/{launcher_id}/settings-file/own-copy", summary="Give it a copy of its own",
             dependencies=[requires(scopes.CONFIG_WRITE)])
def make_launcher_own_copy(launcher_id: str) -> dict[str, Any]:
    """Copies the settings it shares into VPinFE's settings folder, under its name, and
    has it play with the copy."""
    return launcher_settings.make_own_copy(launcher_id)


@router.post("/{launcher_id}/settings-file/received", summary="Take the settings file sent with it",
             dependencies=[requires(scopes.CONFIG_WRITE)])
def launcher_settings_file_received(launcher_id: str,
                                    body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """`{"data": base64}`, the file another install sent along with this launcher. It goes
    into VPinFE's settings folder under the launcher's name, and the launcher plays with
    it. Anything it replaces there is kept in Backups. The launcher has to be here already."""
    try:
        data = base64.b64decode(str(body.get("data") or ""), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise service_errors.RefusedError(t("error.launchers.bad_file")) from exc
    return launcher_settings.receive_file(launcher_id, data)


@router.post("/{launcher_id}/settings-file/renamed", summary="Its name changed",
             dependencies=[requires(scopes.CONFIG_WRITE)])
def launcher_was_renamed(launcher_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """`{"was": old name}`. A settings file VPinFE keeps for it, under the old name in
    VPinFE's own folder, takes the new one; any other file keeps its name."""
    return launcher_settings.follow_rename(launcher_id, str(body.get("was") or ""))


@router.post("/{launcher_id}/settings-file/delete", summary="Delete the file VPinFE made for it",
             dependencies=[requires(scopes.CONFIG_WRITE)])
def delete_launcher_own_file(launcher_id: str) -> dict[str, Any]:
    """Keeps the file in Backups, then deletes it. Only a file VPinFE made and no other
    launcher plays with; anything else answers `{"deleted": false}`."""
    return launcher_settings.delete_own_file(launcher_id)


@router.post("/{launcher_id}/default", summary="Make it the default for its app",
             dependencies=[requires(scopes.CONFIG_WRITE)])
def make_launcher_default(launcher_id: str) -> dict[str, Any]:
    """The launcher its app's tables use unless they name another. It moves to the front
    of the list, since the default is the first one switched on."""
    return launcher_ops.make_default(launcher_id)


@router.post("/order", summary="Put the launchers back in an earlier order",
             dependencies=[requires(scopes.CONFIG_WRITE)])
def restore_launcher_order(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """`{"order": [id, ...]}`, as `default` answered with `was`: those launchers first, in
    that order, and any it does not name after them."""
    return launcher_ops.restore_order(list(body.get("order") or []))


@router.put("/mappings/{table_id}", summary="Point a table at a launcher",
            dependencies=[requires(scopes.CONFIG_WRITE)])
def put_mapping(table_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """An empty `launcher_id` clears it, which is how a table goes back to the default."""
    return launcher_ops.assign(table_id, str(body.get("launcher_id") or ""))
