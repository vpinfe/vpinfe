"""The authenticator app on Settings > Security.

Setting up, setting up again after a lost phone, removing it and switching off the on-screen
sign-in code are all Protected, so the page only calls and the Manager's boundary asks for
the Confirm. Nothing here needs an authenticator to exist.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from nicegui import run, ui

from common.config import paths
from common.devices.install_identity import display_name
from common.failures import why
from common.i18n import t
from manager.kit import confirm, offload, panel, qr, verbs
from manager.kit import dialog as frame
from manager.kit.api import ApiError
from manager.kit.data import Library
from manager.kit.on_page import on_page


def entries(state: dict[str, Any], device: str, set_up: Callable[[], Any],
            remove: Callable[[], Any],
            screen_codes: Callable[[bool], Any]) -> list[tuple[Any, ...]]:
    """What the Authenticator section shows, in order. Pure of the page, so a test reads it."""
    out: list[tuple[Any, ...]] = [(panel.HEADING, t("manager.authenticator.heading"))]
    if not state.get("enabled"):
        out.append(panel.lede(t("manager.authenticator.none")))
        out.append((panel.FULL, panel.action(t("manager.authenticator.set_up"), set_up,
                                             icon=verbs.ADD,
                                             hint=t("manager.authenticator.set_up_hint"))))
        return out
    out.append(panel.lede(t("manager.authenticator.on")))
    out.append((t("manager.authenticator.screen_codes", device=device),
                panel.switch(bool(state.get("screen_codes")),
                             lambda event: screen_codes(bool(event.value)),
                             hint=t("manager.authenticator.screen_codes_hint"))))
    out.append((panel.FULL, panel.action(t("manager.authenticator.again"), set_up,
                                         icon=verbs.REFRESH,
                                         hint=t("manager.authenticator.again_hint"))))
    out.append((panel.FULL, panel.action(t("manager.authenticator.remove"), remove,
                                         icon=verbs.REMOVE, danger=True,
                                         hint=t("manager.authenticator.remove_hint"))))
    return out


async def _enrol(library: Library) -> bool:
    """The QR and the key, and the first code from the app that learned them. Whether it was
    accepted; a refusal stays in the dialog so the person can try the next code."""
    try:
        made = await offload.io(library.authenticator_setup)
    except ApiError as exc:
        ui.notify(t("manager.authenticator.setup_failed"), caption=why(exc), type="negative")
        return False
    secret = str(made.get("secret") or "")
    art = qr.svg(str(made.get("uri") or ""))

    @on_page
    async def send() -> None:
        try:
            await offload.io(library.authenticator_finish, str(code.value or ""))
        except ApiError as exc:
            code.value = ""
            frame.said(code, why(exc))
            return
        box.submit(True)

    with frame.opened(t("manager.authenticator.title"), size="question",
                      ignore_outside_click=True) as box:
        ui.label(t("manager.authenticator.line")).classes("manager-help")
        if art:
            ui.html(art).classes("manager-qr").props("aria-hidden=true")
        entered: dict[str, Any] = {}

        def key_field() -> None:
            ui.input(value=secret).props("outlined readonly dense").classes("w-full manager-log") \
                .props(f'aria-label="{t("manager.authenticator.key_label")}"')

        panel.facts(ui, [
            (t("manager.authenticator.key_label"), key_field),
            (t("manager.confirm.code"), lambda: entered.update(code=frame.field(
                name=t("manager.confirm.code"), room=True))),
        ])
        code = entered["code"]
        code.props("inputmode=numeric autocomplete=one-time-code")
        with frame.footer():
            frame.cancel(lambda: box.submit(False))
            go = frame.answer(t("manager.authenticator.set_up"), send, icon=verbs.ACCEPT)
    frame.enter_presses(go)
    frame.focus(box, code)
    return bool(await box)


def page_parts(library: Library, refill: Callable[[], None],
               state: dict[str, Any]) -> list[tuple[Any, ...]]:
    device = display_name(paths.get_ini_config())

    @on_page
    async def set_up() -> None:
        if await _enrol(library):
            refill()

    @on_page
    async def remove() -> None:
        if not await confirm.ask(
                t("manager.authenticator.remove_question"),
                detail=t("manager.authenticator.remove_detail"),
                confirm=t("manager.authenticator.remove"), icon=verbs.REMOVE, danger=True):
            return
        try:
            await run.io_bound(library.authenticator_remove)
        except ApiError as exc:
            ui.notify(t("manager.authenticator.not_changed"), caption=why(exc), type="negative")
        refill()

    @on_page
    async def screen_codes(on: bool) -> None:
        try:
            await offload.io(library.set_screen_codes, on)
        except ApiError as exc:
            ui.notify(t("manager.authenticator.not_changed"), caption=why(exc), type="negative")
        refill()

    return entries(state, device, set_up, remove, screen_codes)

