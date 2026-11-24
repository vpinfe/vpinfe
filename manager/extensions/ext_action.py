"""What core draws when somebody presses an action an extension offers.

The extension describes what to ask and what to call; this binds its four calls -
`POST {base}/ask`, `POST {base}/check`, `POST {base}/act` and `POST {base}/run`, and
`GET {base}/progress` while one of them is slow - and hands
them to the shared wizard control (`manager/kit/wizard.py`), which does the drawing.
Nothing about the treatment comes from the extension, so every action looks like the
Manager rather than like whoever wrote it, and it keeps working if that extension later
runs somewhere else.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from manager.kit import offload, wizard
from manager.kit.api import STEP_TIMEOUT, ApiClient
from manager.kit.on_page import on_page

GIVE_UP_AFTER = 3


def calls(extension: str, action: dict, rows: Sequence[str] = (),
          who: Mapping[str, str] | None = None) -> wizard.Calls:
    """The action's four calls over HTTP, for the dialog host or for a flow that nests it
    inside steps of its own. `rows` are the ids of the list rows it was opened over and
    `who` the game it was opened for. The first call carries the rows in its body and the
    dialog's values carry them after; every call carries `who` beside its values."""
    client = ApiClient()
    base = f"/ext/{extension}{action.get('base') or ''}"
    game = {"who": dict(who)} if who else {}

    async def first(step: str | None = None) -> dict:
        return await offload.io(client.ext_post, f"{base}/ask",
                                {"step": step or "", "rows": list(rows), **game},
                                STEP_TIMEOUT)

    async def check(values: dict[str, Any], step: str) -> dict:
        return await offload.io(client.ext_post, f"{base}/check",
                                 {"values": values, "step": step, **game}, STEP_TIMEOUT)

    async def act(key: str, values: dict[str, Any], step: str) -> dict:
        return await offload.io(client.ext_post, f"{base}/act",
                                 {"act": key, "values": values, "step": step, **game},
                                 STEP_TIMEOUT)

    async def run(values: dict[str, Any], step: str) -> dict:
        return await offload.io(client.ext_post, f"{base}/run",
                                 {"values": values, "step": step, **game}, STEP_TIMEOUT)

    async def job(job_id: str) -> dict:
        return await offload.io(client.job, job_id)

    failed = {"in_a_row": 0}

    async def progress() -> dict:
        """How far a slow step has got, from the action's `GET {base}/progress`. An action
        that serves none stops being asked after a few answers in a row that say so; a
        busy server's one slow answer does not stop it."""
        if failed["in_a_row"] >= GIVE_UP_AFTER:
            return {}
        try:
            said = await offload.io(client.ext_get, f"{base}/progress")
        except Exception:  # noqa: BLE001 - a wait is not made worse by not knowing
            failed["in_a_row"] += 1
            return {}
        failed["in_a_row"] = 0
        return said

    return wizard.Calls(first, check, act, run, job, progress)


@on_page
async def open_action(extension: str, action: dict, *,
                      values: dict[str, Any] | None = None, until: str = "",
                      rows: Sequence[str] = (), who: Mapping[str, str] | None = None,
                      place: str = "", walking: bool = False) -> Any:
    """Run one action: ask what it asks, in as many steps as it asks it, then do it.

    True once it ran and the dialog was closed, so a page that shows what the action
    changes can redraw; False when it was cancelled first. `values` are answers it starts
    with, and `until` the step it is walked to, unasked, before it is shown. `rows` are
    the ids of the rows it is opened over and `who` the game it is opened for. In a walk
    (`walking`, at `place` in it) the way out is Skip or Stop, and the answer is
    `walks.SKIPPED` or `walks.STOPPED` as well."""
    return await wizard.open_dialog(
        label=str(action.get("label") or ""),
        calls=calls(extension, action, rows, who),
        under=f"ext.{extension}.action.{action.get('key')}",
        values={**(values or {}), **({"rows": list(rows)} if rows else {})},
        until=until, place=place, walking=walking, close_when_done=walking)
