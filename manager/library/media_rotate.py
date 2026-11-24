"""Rotate the selected pictures and videos: ask first, then the rotation as a job.

The files are replaced where they sit, so the ask names what is about to happen and the
copy taken first is the default answer.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from pathlib import PurePosixPath
from typing import Any, Literal

from nicegui import ui

from common.failures import why
from common.games import media_rotate
from common.i18n import t
from common.media.media_specs import IMAGE_FAMILY, VIDEO_FAMILY
from manager.kit import confirm, offload, verbs
from manager.kit.api import ApiClient
from manager.kit.on_page import on_page

_POLL_S = 1.0
_POLLS = 7200

_WAYS = {media_rotate.LEFT: "manager.media_rotate.way_left",
         media_rotate.RIGHT: "manager.media_rotate.way_right",
         media_rotate.AROUND: "manager.media_rotate.way_around"}
# What the Rotate submenu offers, in order.
MENU = ((media_rotate.LEFT, "manager.media_rotate.left"),
        (media_rotate.RIGHT, "manager.media_rotate.right"),
        (media_rotate.AROUND, "manager.media_rotate.around"))
_ICONS = {media_rotate.LEFT: verbs.ROTATE_LEFT, media_rotate.RIGHT: verbs.ROTATE_RIGHT,
          media_rotate.AROUND: verbs.ROTATE_RIGHT}


def turnable(rows: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """The files among these media rows a turn applies to, once each: a file that is
    there, and a picture or a video."""
    found: dict[tuple[str, str], None] = {}
    for row in rows:
        path = str(row.get("path") or "")
        if row.get("present") and PurePosixPath(path).suffix.lower() in IMAGE_FAMILY + VIDEO_FAMILY:
            found[(str(row["game_id"]), path)] = None
    return list(found)


def outcome(result: dict[str, Any]) -> tuple[str, Literal["positive", "warning", "info"]]:
    """What the turn says when it ends, and the notice type to say it with."""
    turned, skipped = int(result.get("turned") or 0), len(result.get("skipped") or ())
    said = t("manager.media_rotate.rotated", count=turned)
    if skipped:
        return t("manager.media_rotate.turned_skipped", turned=said,
                 skipped=skipped), "warning"
    return said, "positive"


@on_page
async def ask(picked: list[dict[str, Any]], way: str, state: dict[str, Any],
              then: Callable[[], Any]) -> None:
    """Turn the files among `picked` the given way, once the user has said yes."""
    files = turnable(picked)
    if not files:
        ui.notify(t("manager.media_rotate.nothing"), type="info")
        return
    left_out = sum(1 for row in picked if not turnable([row]))
    answer = await confirm.ask(
        t("manager.media_rotate.ask", count=len(files)),
        detail=[t(_WAYS[way]), t("manager.media_rotate.detail"),
                t("manager.media_rotate.own_file")],
        lines=([t("manager.media_rotate.also_skipped", count=left_out)]
               if left_out > 0 else []),
        confirm=t("manager.media_rotate.keep"), icon=_ICONS[way], danger=False,
        second=(t("manager.media_rotate.no_copies"), _ICONS[way]))
    if answer == confirm.Answer.CANCEL:
        return
    keep = answer == confirm.Answer.CONFIRM
    await run(lambda: ApiClient().rotate_media(files, way, keep), state, then)


@on_page
async def run(start: Callable[[], dict], state: dict[str, Any],
              then: Callable[[], Any]) -> None:
    """Start the turn and say what it did once it ends; the footer line reports it while
    it runs. `then` runs when anything was turned."""
    client = ApiClient()
    try:
        job = await offload.io(start)
    except Exception as exc:  # noqa: BLE001 - said, and nothing was turned
        ui.notify(t("manager.media_rotate.could_not_start"), caption=why(exc), type="warning")
        return
    watch = state.get("watch_jobs")
    if callable(watch):
        watch()
    for _ in range(_POLLS):
        await asyncio.sleep(_POLL_S)
        try:
            found = await offload.io(client.job, str(job.get("id") or ""))
        except Exception:  # noqa: BLE001 - the footer line still reports it
            return
        if found.get("state") == "running":
            continue
        if found.get("state") == "failed":
            ui.notify(t("manager.media_rotate.failed"), caption=str(found.get("error") or ""),
                      type="negative")
            return
        result = found.get("result") or {}
        said, level = outcome(result)
        skipped = [str(one.get("reason") or "") for one in result.get("skipped") or ()]
        ui.notify(said, caption="; ".join(dict.fromkeys(skipped)), type=level)
        if int(result.get("turned") or 0):
            answer = then()
            if inspect.isawaitable(answer):
                await answer
        return
