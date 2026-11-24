"""Adding a VPX Mobile device by hand, from the Devices bar and from Send to device.

A mobile device cannot announce itself. A device that does not answer the probe is still
added on a second press: the address is right even if it is asleep now.
"""

from __future__ import annotations

from typing import Any

from nicegui import ui

from common.devices import device_client, device_registry
from common.failures import why
from common.i18n import t
from manager.kit import dialog as frame
from manager.kit import offload, panel, verbs
from manager.kit.api import ApiClient
from manager.kit.on_page import on_page

DEFAULT_PORT = 2112


def port_of(text: str) -> int | None:
    """The port a person typed, or None where it is not one."""
    text = text.strip()
    if not text.isdigit() or not 0 < int(text) < 65536:
        return None
    return int(text)


address_problem = device_registry.address_problem


async def _reason_not_answering(address: str, port: int) -> str:
    """Why the device at this address did not answer the probe, or "" when it did."""
    client = device_client.for_device(
        {"kind": device_registry.KIND_VPX_MOBILE, "address": address, "port": port})
    answer = await offload.io(device_client.probe, client)
    if answer.get("state") == device_client.ANSWERING:
        return ""
    return str(answer.get("reason") or "")


@on_page
async def add_mobile() -> dict[str, Any] | None:
    """The dialog. Returns the device that was added, or None when it was cancelled."""
    fields: dict[str, Any] = {}
    refused: dict[str, tuple[str, int]] = {}
    working: list[bool] = []

    def unrefuse() -> None:
        refused.clear()
        frame.said(fields["address"], "")
        frame.said(fields["port"], "")
        go.text = t("word.add")

    async def keep() -> None:
        if working:
            return
        working.append(True)
        try:
            await add()
        finally:
            working.clear()

    async def add() -> None:
        address = str(fields["address"].value or "").strip()
        port = port_of(str(fields["port"].value or ""))
        if problem := address_problem(address):
            frame.said(fields["address"], problem)
            return
        if port is None:
            frame.said(fields["port"], t("manager.devices.port_invalid"))
            return
        if refused.get("asked") != (address, port):
            reason = await _reason_not_answering(address, port)
            if reason:
                refused["asked"] = (address, port)
                frame.said(fields["address"], reason)
                go.text = t("manager.devices.add_anyway")
                return
        try:
            added = await offload.io(
                ApiClient().add_device, address, port,
                str(fields["name"].value or "").strip() or t("manager.devices.mobile_name"))
        except Exception as exc:  # noqa: BLE001 - the refusal is about the address
            frame.said(fields["address"], why(exc))
            return
        box.submit(added)

    def draw(key: str) -> Any:
        def drawn() -> None:
            if key == "name":
                fields[key] = frame.field(placeholder=t("manager.devices.mobile_name"))
            elif key == "address":
                fields[key] = frame.field()
            else:
                fields[key] = frame.field(str(DEFAULT_PORT))
            fields[key].props("bottom-slots")
        return drawn

    with frame.opened(t("manager.devices.add_mobile"), ignore_outside_click=True) as box:
        panel.facts(ui,
                    [(t("word.name"), draw("name")),
                     (t("manager.devices.address"), draw("address")),
                     (t("manager.devices.port"), draw("port"))])
        ui.label(t("manager.devices.add_mobile.help")).classes("manager-help")
        with frame.footer():
            frame.cancel(lambda: box.submit(None))
            go = frame.answer(t("word.add"), keep, icon=verbs.ADD)
    for key in ("address", "port"):
        fields[key].on_value_change(unrefuse)
    frame.focus(box, fields["address"])
    frame.enter_presses(go)
    return await box
