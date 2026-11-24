"""Pairing this install with another VPinFE, from the Devices page.

The other install shows a code on its own screen and the person types it here, which is
what proves they are standing at it. That install then issues a Full Control token that
this one keeps. A device with no screen is paired by pasting a token made on its Security
page.
"""

from __future__ import annotations

import logging
from typing import Any

from nicegui import ui

from common.config.paths import get_ini_config
from common.devices.install_identity import display_name
from common.failures import why
from common.i18n import t
from manager.kit import dialog as frame
from manager.kit import offload, verbs
from manager.kit.on_page import on_page

logger = logging.getLogger("vpinfe.manager.devices.pairing")

PASTE = "paste"


def _refusal(exc: Exception) -> str:
    """What the other install said, or why it could not be asked."""
    try:
        return str(exc.response.json()["error"]["message"])  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - no wording from it, use what this side knows
        return why(exc)


async def pair(client: Any, device_name: str) -> bool:
    """Whether the pairing went through. Asks that install to show a code, then waits for
    the same six digits here."""
    try:
        request_id = await offload.io(client.pair_ask)
    except Exception as exc:  # noqa: BLE001 - an install that cannot be asked says so
        logger.info("Could not ask %s for a pairing code", device_name, exc_info=True)
        ui.notify(t("manager.pairing.not_asked"), caption=why(exc), type="negative")
        return False

    @on_page
    async def send() -> None:
        try:
            await offload.io(client.pair, request_id, str(code.value or "").strip(),
                             display_name(get_ini_config()))
        except Exception as exc:  # noqa: BLE001 - a wrong code is said, not raised
            problem.text = _refusal(exc)
            code.value = ""
            return
        box.submit(True)

    with frame.opened(t("manager.pairing.title", device=device_name),
                      size="question", ignore_outside_click=True) as box:
        ui.label(t("manager.pairing.line", device=device_name)).classes("manager-help")
        code = frame.field(placeholder=t("manager.confirm.code"))
        problem = ui.label("").classes("manager-help text-negative")
        with frame.footer():
            frame.aside(t("manager.pairing.paste"), lambda: box.submit(PASTE),
                        icon=verbs.COPY)
            frame.cancel(lambda: box.submit(False))
            go = frame.answer(t("manager.pairing.pair"), send, icon=verbs.ACCEPT)
    frame.enter_presses(go)
    frame.focus(box, code)
    answered = await box
    if answered == PASTE:
        return await paste(client, device_name)
    return bool(answered)


async def paste(client: Any, device_name: str) -> bool:
    """Pair with a token made on that install's Security page, for a device with no
    screen to show a code on."""
    with frame.opened(t("manager.pairing.paste_title", device=device_name),
                      size="question", ignore_outside_click=True) as box:
        ui.label(t("manager.pairing.paste_line", device=device_name)).classes("manager-help")
        secret = frame.field(placeholder=t("manager.pairing.token"))
        with frame.footer():
            frame.cancel(lambda: box.submit(False))
            go = frame.answer(t("manager.pairing.pair"),
                              lambda: box.submit(str(secret.value or "").strip()),
                              icon=verbs.ACCEPT)
    frame.enter_presses(go)
    frame.focus(box, secret)
    given = await box
    if not given:
        return False
    try:
        await offload.io(client.pair_with_token, str(given))
    except Exception as exc:  # noqa: BLE001 - an https install that does not answer says so
        logger.info("Could not read the certificate of %s", device_name, exc_info=True)
        ui.notify(t("manager.pairing.not_checked"), caption=why(exc), type="negative")
        return False
    return True
