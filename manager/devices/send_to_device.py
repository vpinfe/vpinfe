"""Putting games on a device that plays them but does not run VPinFE.

The same operation seen from two subjects, which is what this surface runs on. From the
library you start with the games you want and choose where they go; from a device you
start with the phone and manage what it is carrying. Neither covers the other - picking
twelve tables to push is a library job, and noticing a phone is carrying something stale
is a device job - so both are offered and both come through here.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from nicegui import run, ui

from common.devices import device_client, device_registry
from common.failures import why
from common.i18n import t
from manager.devices import mobile_add
from manager.kit import confirm, offload, panel, verbs
from manager.kit import dialog as frame
from manager.kit.api import ApiClient
from manager.kit.on_page import on_page

logger = logging.getLogger("vpinfe.manager.devices.send_to_device")

_NAMED = 8
_POLL_S = 1.0
_POLLS = 3600


def phones(devices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The devices games can be sent to, which is the ones that are not installs.

    An install is sent to by putting files in its library, which is a different
    operation with a different answer to where they go.
    """
    return [one for one in devices
            if str(one.get("kind") or "") == device_registry.KIND_VPX_MOBILE
            and one.get("address") and one.get("port")]


def name_of(device: dict[str, Any]) -> str:
    return str(device.get("display_name") or "").strip() or t("manager.send_to_device.device")


@on_page
async def ask_where(games: list[dict[str, Any]],
                    state: dict[str, Any] | None = None) -> None:
    """Choose a device, say what will happen, and start it.

    A confirm rather than a straight send: this copies whole tables over somebody's
    wifi, and the count and the destination are the two things they would want to have
    seen first.
    """
    if not games:
        return
    try:
        found = phones(await offload.io(ApiClient().devices))
    except Exception as exc:
        ui.notify(t("manager.send_to_device.devices_failed"), caption=why(exc),
                  type="negative")
        return
    if not found:
        if not await confirm.ask(
                t("manager.send_to_device.no_mobile"),
                detail=t("manager.send_to_device.no_mobile_detail"),
                confirm=t("manager.devices.add_mobile"), icon=verbs.ADD, danger=False):
            return
        added = await mobile_add.add_mobile()
        if not added:
            return
        found = [added]

    answers = await _asked(found)
    if len(found) == 1 and _is_answering(answers[_id(found[0])]):
        picked = found[0]
    else:
        picked = await _which(found, answers)
    if not picked:
        return
    mobile = await _with_mobile_settings(games, picked)
    answer = await confirm.ask(
        t("manager.send_to_device.send_games", count=len(games), device=name_of(picked)),
        detail=[t("manager.send_to_device.send_detail"),
                t("manager.send_to_device.mobile_settings", count=len(mobile))
                if mobile else ""],
        lines=await _called(mobile, games),
        confirm=t("manager.send_to_device.send"), icon=verbs.SEND, danger=False,
        second=(t("manager.send_to_device.with_settings"), verbs.SEND))
    if answer == confirm.Answer.CANCEL:
        return
    await send(games, picked, state,
               settings=answer == confirm.Answer.SECOND)


async def _with_mobile_settings(games: list[dict[str, Any]],
                                device: dict[str, Any]) -> list[str]:
    """The ids that would send a mobile settings file. A plan that cannot be read says
    nothing rather than stopping the send: it only decides what the confirm mentions."""
    try:
        return await offload.io(ApiClient().plan_send_to_device, _id(device),
                                [str(one["id"]) for one in games])
    except Exception:  # noqa: BLE001 - the send itself is the thing that must work
        return []


async def _called(ids: list[str], games: list[dict[str, Any]]) -> list[str]:
    """Names for the confirm's lines, at most a screenful: a count says the rest."""
    known = {str(one["id"]): str(one.get("name") or "") for one in games}
    names: list[str] = []
    for game_id in ids[:_NAMED]:
        name = known.get(game_id, "")
        if not name:
            try:
                name = str((await offload.io(ApiClient().game, game_id)).get("name") or "")
            except Exception:  # noqa: BLE001 - a line left out beats a confirm that fails
                continue
        names.append(name)
    return names


@on_page
async def send(games: list[dict[str, Any]], device: dict[str, Any],
               state: dict[str, Any] | None = None, *, settings: bool = False) -> None:
    """Start the transfer, and say so if it fails. The drawer's job line says how it is
    going while it runs."""
    client = ApiClient()
    try:
        job = await run.io_bound(client.send_to_device, str(device["device_id"]),
                                 [str(one["id"]) for one in games], settings=settings)
    except Exception as exc:
        ui.notify(t("manager.send_to_device.failed", device=name_of(device)),
                  caption=why(exc), type="negative")
        return
    ui.notify(t("manager.send_to_device.sending", count=len(games), device=name_of(device)),
            type="positive")
    watch = (state or {}).get("watch_jobs")
    if callable(watch):
        watch()
    for _ in range(_POLLS):
        await asyncio.sleep(_POLL_S)
        try:
            found = await offload.io(client.job, str((job or {}).get("id") or ""))
        except Exception:  # noqa: BLE001 - the footer line still reports it
            return
        if found.get("state") == "running":
            continue
        if found.get("state") == "failed":
            ui.notify(t("manager.send_to_device.failed", device=name_of(device)),
                      caption=str(found.get("error") or ""), type="negative")
        return


def _id(device: dict[str, Any]) -> str:
    return str(device.get("device_id") or "")


def _is_answering(probed: dict[str, Any]) -> bool:
    return probed.get("state") == device_client.ANSWERING


async def _asked(found: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Every device asked at once, so the wait is one device's 3s and not their sum."""
    async def ask(device: dict[str, Any]) -> dict[str, Any]:
        try:
            return await offload.io(ApiClient().probe_device, _id(device))
        except Exception as exc:  # noqa: BLE001 - not answering is an answer
            return {"state": device_client.UNREACHABLE, "reason": why(exc)}

    asking = ui.notification(t("manager.devices.checking"), spinner=True, timeout=None)
    try:
        probed = await asyncio.gather(*(ask(device) for device in found))
    finally:
        asking.dismiss()
    return {_id(device): one for device, one in zip(found, probed, strict=True)}


async def _which(found: list[dict[str, Any]],
                 answers: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Which device. One that did not answer stays in the list, dimmed, with its address
    and why, and can be asked again."""
    with frame.opened(t("manager.send_to_device.send_device")) as box:
        rows = ui.column().classes("gap-2")

        @on_page
        async def again(device: dict[str, Any]) -> None:
            answers.update(await _asked([device]))
            if not rows.is_deleted:
                draw()

        def draw() -> None:
            rows.clear()
            with rows:
                for device in found:
                    _row(device, answers[_id(device)], box, again)

        draw()
        with frame.footer():
            frame.cancel(lambda: box.submit(None))
    return await box or {}


def _row(device: dict[str, Any], probed: dict[str, Any], box: Any, again: Any) -> None:
    if _is_answering(probed):
        panel.action(name_of(device), lambda _e=None: box.submit(device), icon=verbs.SEND,
                     hint=t("manager.send_to_device.pick_this_device.help"))()
        return
    with ui.column().classes("gap-0"):
        reason = str(probed.get("reason") or "")
        panel.action(name_of(device), icon=verbs.SEND, enabled=False, hint=reason)()
        panel.line(f"{device.get('address')}:{device.get('port')}")
        panel.line(reason)
        panel.action(t("manager.page.try_again"), lambda _e=None: again(device),
                     icon=verbs.REFRESH, inline=True,
                     hint=t("manager.devices.probe_one"))()
