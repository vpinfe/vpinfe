"""The question a Protected change asks a signed-in Owner: a code from this device's screen.

Only reached for a browser on another device. Whoever is at the machine sees the six digits
at once, so reading them off its screen is the proof of being in front of it.
"""

from __future__ import annotations

import logging
from typing import Any

from nicegui import run, ui

from common.config.paths import get_ini_config
from common.devices.install_identity import display_name
from common.failures import why
from common.i18n import t
from common.security import sign_in
from manager.kit import dialog as frame
from manager.kit import offload, panel, verbs
from manager.kit.api import ApiClient, ApiError
from manager.kit.on_page import on_page

logger = logging.getLogger("vpinfe.manager.kit.confirm_code")

# After these the code is gone and a new one is already on the screen.
NEW_CODE = ("no_code", "used_up")


async def ask() -> bool:
    """Whether the person confirmed. With an authenticator set up, its six digits; otherwise
    (or on request, when this device shows them) the code drawn on this device's screen,
    which the same six digits here answer."""
    device = display_name(get_ini_config())
    api = ApiClient()
    try:
        app = await run.io_bound(api.authenticator) or {}
    except ApiError as exc:
        ui.notify(t("manager.confirm.not_asked"), caption=why(exc), type="negative")
        return False
    using_app = bool(app.get("enabled"))

    @on_page
    async def show_screen_code() -> bool:
        try:
            await run.io_bound(api.confirm_code)
        except ApiError as exc:
            ui.notify(t("manager.confirm.not_asked"), caption=why(exc), type="negative")
            return False
        return True

    if not using_app and not await show_screen_code():
        return False

    def line_text() -> str:
        if using_app:
            return t("manager.confirm.app_line", minutes=sign_in.CONFIRM_SECONDS // 60)
        return t("manager.confirm.line", device=device, minutes=sign_in.CONFIRM_SECONDS // 60)

    def other_label() -> str:
        return t("manager.confirm.use_screen", device=device) if using_app \
            else t("manager.confirm.use_app")

    @on_page
    async def send() -> None:
        try:
            await offload.io(api.confirm_authenticator if using_app else api.confirm,
                             str(code.value or ""))
        except ApiError as exc:
            code.value = ""
            frame.said(code, why(exc))
            if not using_app and exc.details.get("reason") in NEW_CODE:
                await run.io_bound(api.confirm_code)
            return
        box.submit(True)

    @on_page
    async def switch() -> None:
        nonlocal using_app
        if using_app and not await show_screen_code():
            return
        using_app = not using_app
        said.text = line_text()
        other.text = other_label()
        code.value = ""

    with frame.opened(t("manager.confirm.title", device=device),
                      size="question", ignore_outside_click=True) as box:
        said = ui.label(line_text()).classes("manager-help")
        typed: dict[str, Any] = {}
        panel.facts(ui, [(t("manager.confirm.code"), lambda: typed.update(
            code=frame.field(name=t("manager.confirm.code"), room=True)))])
        code = typed["code"]
        code.props("inputmode=numeric autocomplete=one-time-code")
        with frame.footer():
            if app.get("enabled"):
                other = frame.aside(other_label(), switch, icon=verbs.LOCK)
            frame.cancel(lambda: box.submit(False))
            go = frame.answer(t("word.confirm"), send, icon=verbs.ACCEPT)
    frame.enter_presses(go)
    frame.focus(box, code)
    return bool(await box)
