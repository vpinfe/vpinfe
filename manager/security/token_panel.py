"""The tokens a program holds to call this install, on Settings > Security.

A token is made here, shown once, and taken back here. Making one hands out access, so it
asks the Confirm like any Protected change; the page only calls and the Manager's
boundary asks.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from nicegui import run, ui

from common.failures import why
from common.i18n import t
from common.security import api_tokens
from manager.kit import clipboard, confirm, offload, panel, verbs, when
from manager.kit import dialog as frame
from manager.kit.api import ApiError
from manager.kit.data import Library
from manager.kit.on_page import on_page


def access_label(access: str) -> str:
    return t("manager.tokens.full" if access == api_tokens.FULL else "manager.tokens.play")


def last_used(row: dict[str, Any]) -> str:
    stamp = float(row.get("last_used") or 0)
    if not stamp:
        return t("manager.tokens.never_used")
    return t("manager.security.last_used", when=when.ago(when.from_epoch(stamp).isoformat(),
                                                         inline=True))


def _row_value(row: dict[str, Any], revoke: Callable[[str], Any],
               power: Callable[[str, bool], Any]) -> Callable[[], None]:
    def draw() -> None:
        with ui.row().classes("items-center no-wrap gap-3 w-full"):
            ui.label(access_label(str(row.get("access") or ""))).classes("manager-help")
            ui.label(last_used(row)).classes("manager-help grow")
            if row.get("access") == api_tokens.FULL:
                ui.label(t("manager.tokens.power")).classes("manager-help")
                panel.switch(bool(row.get("can_power")),
                             lambda event: power(str(row["id"]), bool(event.value)),
                             hint=t("manager.tokens.power_hint"))()
            panel.action(t("manager.tokens.revoke"), lambda: revoke(str(row["id"])),
                         icon=verbs.DELETE, inline=True, danger=True,
                         hint=t("manager.tokens.revoke_hint"))()

    return draw


def entries(rows: list[dict[str, Any]], make: Callable[[], Any],
            revoke: Callable[[str], Any],
            power: Callable[[str, bool], Any]) -> list[tuple[Any, ...]]:
    """What the Tokens section shows, in order. Pure of the page, so a test reads it."""
    out: list[tuple[Any, ...]] = [(panel.HEADING, t("manager.tokens.heading"))]
    if rows:
        out += [(str(row.get("name") or ""), _row_value(row, revoke, power)) for row in rows]
    else:
        out.append(panel.intro(t("manager.tokens.none")))
    out.append((panel.FULL, panel.action(t("manager.tokens.new"), make, icon=verbs.ADD,
                                         hint=t("manager.tokens.new_hint"))))
    return out


async def _shown_once(name: str, secret: str) -> None:
    """The secret, and the one chance to copy it."""
    with frame.opened(t("manager.tokens.made_title", name=name), size="wide",
                      ignore_outside_click=True) as box:
        ui.label(t("manager.tokens.made_line")).classes("manager-help")
        with ui.element("div").classes("w-full"):
            shown = ui.input(value=secret).props("outlined readonly dense") \
                .classes("w-full manager-log")
            shown.props["aria-label"] = t("manager.tokens.secret")
        with frame.footer():
            panel.action(t("word.copy"), lambda event: clipboard.copied(bool(event.args), secret),
                         icon=verbs.COPY, hint=t("manager.about.copy_hint"),
                         js=clipboard.COPY_JS % json.dumps(secret))()
            frame.answer(t("word.done"), box.close, icon=verbs.DONE)
    frame.focus(box, shown, select=True)
    box.open()
    await box


async def _ask_new() -> tuple[str, str, bool] | None:
    """The name, the access and the power switch, or None where it was cancelled."""
    held: dict[str, Any] = {"access": api_tokens.PLAY}
    fields: dict[str, Any] = {}

    def does(access: str) -> str:
        return t("manager.tokens.full_does" if access == api_tokens.FULL
                 else "manager.tokens.play_does")

    def chose(event: Any) -> None:
        held.update(access=event.value)
        fields["does"].text = does(event.value)
        power = fields["power"]
        if event.value == api_tokens.FULL:
            power.enable()
        else:
            power.value = False
            power.disable()

    def keep() -> None:
        name = str(fields["name"].value or "").strip()
        if not name:
            frame.said(fields["name"], t("said.needs_name"))
            return
        box.submit((name, held["access"], bool(fields["power"].value)))

    with frame.opened(t("manager.tokens.new_title"), size="question",
                      ignore_outside_click=True) as box:
        panel.facts(ui, [
            (t("manager.tokens.name"),
             lambda: fields.update(name=frame.field(name=t("manager.tokens.name"), room=True))),
            (t("manager.tokens.access"),
             panel.choice_buttons({api_tokens.FULL: access_label(api_tokens.FULL),
                                   api_tokens.PLAY: access_label(api_tokens.PLAY)},
                                  api_tokens.PLAY, chose)),
            (panel.ASIDE, lambda: fields.update(does=panel.line(does(api_tokens.PLAY)))),
            (t("manager.tokens.power"),
             panel.switch(False, lambda _event: None, disabled=True,
                          hint=t("manager.tokens.power_hint"),
                          on_control=lambda control: fields.update(power=control))),
        ])
        with frame.footer():
            frame.cancel(lambda: box.submit(None))
            go = frame.answer(t("manager.tokens.create"), keep, icon=verbs.CREATE)
    frame.enter_presses(go)
    frame.focus(box, fields["name"])
    return await box


def page_parts(library: Library, refill: Callable[[], None],
               rows: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
    @on_page
    async def make() -> None:
        wanted = await _ask_new()
        if wanted is None:
            return
        name, access, can_power = wanted
        try:
            made = await offload.io(library.make_token, name, access, can_power)
        except ApiError as exc:
            ui.notify(t("manager.tokens.not_made"), caption=why(exc), type="negative")
            return
        refill()
        await _shown_once(name, str(made.get("secret") or ""))

    @on_page
    async def revoke(token_id: str) -> None:
        name = next((str(row.get("name") or "") for row in rows if row.get("id") == token_id), "")
        if not await confirm.ask(t("manager.tokens.revoke_title", name=name),
                                 detail=t("manager.tokens.revoke_detail"),
                                 confirm=t("manager.tokens.revoke"), icon=verbs.DELETE):
            return
        await run.io_bound(library.revoke_token, token_id)
        refill()

    @on_page
    async def power(token_id: str, on: bool) -> None:
        try:
            await offload.io(library.set_token_power, token_id, on)
        except ApiError as exc:
            ui.notify(t("manager.tokens.not_changed"), caption=why(exc), type="negative")
        refill()

    return entries(rows, make, revoke, power)
