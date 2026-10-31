"""Getting a Tool VPinFE can download itself, asked first and said afterwards."""

from __future__ import annotations

from typing import Any

from nicegui import ui

from common.failures import why
from common.host import tools
from common.i18n import size, t
from manager.kit import confirm, offload, verbs
from manager.library import record


async def ask_and_get(library: Any, tool_id: str, offered: dict[str, Any]) -> bool:
    """Say what is downloaded and from where, get it on a yes, and notify how it went.
    True where the Tool arrived."""
    if not await confirm.ask(
            t("manager.settings.get_ffmpeg_ask"),
            detail=t("manager.settings.get_ffmpeg_detail",
                     version=str(offered.get("version")), source=str(offered.get("source")),
                     size=size(int(offered.get("size") or 0))),
            confirm=t("manager.settings.get_ffmpeg"), icon=verbs.FETCH, danger=False):
        return False
    try:
        job = await offload.io(library.get_tool, tool_id)
    except Exception as exc:  # noqa: BLE001 - the reason belongs on screen
        ui.notify(t("manager.settings.ffmpeg_failed"), caption=why(exc), type="negative")
        return False
    ended = await record.ended_job(library, str(job.get("id") or ""))
    if not ended or ended.get("error"):
        ui.notify(t("manager.settings.ffmpeg_failed"), caption=str(ended.get("error") or ""),
                  type="negative")
        return False
    ui.notify(t("manager.settings.got_ffmpeg", version=str(ended.get("version"))),
              type="positive")
    return True


async def ask_and_get_ffmpeg(library: Any) -> bool:
    """`ask_and_get` for FFmpeg, found among the Tools the install lists. Where the install
    has nothing to download, says how to get one instead. True where the Tool arrived."""
    try:
        rows = await offload.io(library.config_tools)
    except Exception as exc:  # noqa: BLE001 - the reason belongs on screen
        ui.notify(t("manager.settings.ffmpeg_failed"), caption=why(exc), type="negative")
        return False
    row: dict[str, Any] = next((one for one in rows if str(one.get("id")) == "ffmpeg"), {})
    remedy = row.get("remedy") or {}
    if offered := remedy.get("get"):
        return await ask_and_get(library, "ffmpeg", offered)
    if remedy:
        ui.notify(tools.words(remedy), type="info")
    return False
