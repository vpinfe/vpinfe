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

from typing import Any

from fastapi import APIRouter, Body, Request, Response

from common.games import launcher_ops, upgrade_ops
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


@router.post("/{launcher_id}/default", summary="Make it the default for its app",
             dependencies=[requires(scopes.CONFIG_WRITE)])
def make_launcher_default(launcher_id: str) -> dict[str, Any]:
    """The launcher its app's tables use unless they name another. It moves to the front
    of the list, since the default is the first one switched on."""
    return launcher_ops.make_default(launcher_id)


@router.put("/mappings/{table_id}", summary="Point a table at a launcher",
            dependencies=[requires(scopes.CONFIG_WRITE)])
def put_mapping(table_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """An empty `launcher_id` clears it, which is how a table goes back to the default."""
    return launcher_ops.assign(table_id, str(body.get("launcher_id") or ""))
