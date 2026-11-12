"""A sign-in request, a Confirm Code, a pairing code or a program's ask to make a change,
shown on a Manager open on this device.

A sign-in waits for whoever sits at this device: Show draws the six digits, Ignore turns
that address away. A Confirm Code and a pairing code are drawn at once, since the person
reading them off this screen is the proof they are here. An ask is answered here with
Approve or Decline, or left for Needs You with Close. A Manager opened from another
device is never given any of them, because the digits are what lets that device in.
"""

from __future__ import annotations

from typing import Any

from nicegui import ui

from common.i18n import t
from common.security import approvals, sign_in
from httpapi import auth
from manager.kit import dialog as frame
from manager.kit import verbs

POLL_SECONDS = 2.0

_watching: set[str] = set()


def watching() -> bool:
    """Whether a Manager open on this device is looking for a code to draw."""
    return bool(_watching)


def _gone(client_id: str) -> None:
    _watching.discard(client_id)
    if not _watching:
        sign_in.instance().reveal_if_unseen()


def watch() -> None:
    """Start looking for a request, on a page this device opened."""
    try:
        client = ui.context.client
        if not auth.caller_is_local(client.request):
            return
    except Exception:  # noqa: BLE001 - a page with no request has nobody to ask
        return
    # A page is built before its socket opens, so it is counted at once. NiceGUI deletes a
    # page that never connected without calling on_disconnect, hence on_delete too; a
    # reconnect needs counting again.
    _watching.add(client.id)
    client.on_connect(lambda: _watching.add(client.id))
    client.on_disconnect(lambda: _gone(client.id))
    client.on_delete(lambda: _gone(client.id))
    held: dict[str, Any] = {"key": None, "dialog": None, "closed": set()}

    def poll() -> None:
        signer = sign_in.instance()
        code = next((one for one in signer.confirms() if one.id not in held["closed"]), None)
        request = None if code is not None else signer.pending()
        asked = None if code is not None or request is not None \
            else approvals.instance().on_screen()
        if code is not None:
            key: tuple[str, ...] | None = (code.id,)
        elif request is not None:
            key = (request.id, request.state)
        else:
            key = (asked.id,) if asked is not None else None
        if key == held["key"]:
            return
        held["key"] = key
        if held["dialog"] is not None:
            held["dialog"].close()
            held["dialog"] = None
        if code is not None:
            held["dialog"] = _show(code, held["closed"])
        elif request is not None:
            held["dialog"] = _ask(request)
        elif asked is not None:
            held["dialog"] = _approve(asked)

    ui.timer(POLL_SECONDS, poll)


def _who(agent: str, place: str) -> str:
    """Who is asking, as the person reading the screen would say it."""
    return t("manager.security.who_agent", agent=agent, place=place) if agent \
        else t("manager.security.who_browser", place=place)


def _for_code(code: sign_in.Confirm) -> str:
    if code.purpose == sign_in.PAIR:
        place = code.place or code.address
        return t("manager.security.pair_for", name=code.name, place=place) if code.name \
            else t("manager.security.pair_for_unnamed", place=place)
    return t("manager.security.confirm_for", who=_who(code.agent, code.place or code.address))


def _lasts(kind: str) -> str:
    """What approving lasts for, once the code has been typed."""
    if kind == "sign_in":
        return t("manager.security.lasts_sign_in", count=sign_in.SESSION_DAYS)
    if kind == "confirm":
        return t("manager.security.lasts_confirm", count=sign_in.CONFIRM_SECONDS // 60)
    return t("manager.security.lasts_pair")


def _show(code: sign_in.Confirm, closed: set[str]) -> ui.dialog:
    pairing = code.purpose == sign_in.PAIR

    def close() -> None:
        closed.add(code.id)
        sheet.close()

    with frame.opened(t("manager.security.pair_code" if pairing
                        else "manager.security.confirm_code"),
                      size="question", ignore_outside_click=True) as sheet:
        ui.label(_for_code(code)).classes("manager-help text-center")
        ui.label(code.code).classes("manager-digits w-full")
        ui.label(t("manager.security.code_good", count=sign_in.CODE_SECONDS // 60)) \
            .classes("manager-help text-center")
        ui.label(_lasts("pair" if pairing else "confirm")) \
            .classes("manager-help text-center")
        with frame.footer():
            frame.answer(t("word.close"), close, icon=verbs.CLOSE) \
                .tooltip(t("manager.security.code_close_hint"))
    frame.on_escape(sheet, lambda: closed.add(code.id))
    sheet.open()
    return sheet


def _ask(request: sign_in.Request) -> ui.dialog:
    signer = sign_in.instance()
    shown = request.state == sign_in.SHOWN
    # A request still waiting is answered with Show or Ignore; leaving it any other way
    # would strand it with nothing on this device to answer it.
    who = _who(request.agent, request.place or request.address)
    with frame.opened(t("manager.security.signin_title") if shown
                      else t("manager.security.wants", who=who),
                      size="question", persistent=not shown,
                      ignore_outside_click=shown) as sheet:
        if shown:
            ui.label(t("manager.security.for_browser", who=who)) \
                .classes("manager-help text-center")
            ui.label(request.code).classes("manager-digits w-full")
            ui.label(t("manager.security.code_good", count=sign_in.CODE_SECONDS // 60)) \
                .classes("manager-help text-center")
            ui.label(_lasts("sign_in")).classes("manager-help text-center")
            with frame.footer():
                frame.answer(t("word.close"), lambda: signer.dismiss(request.id),
                             icon=verbs.CLOSE).tooltip(t("manager.security.close_hint"))
        else:
            with frame.footer():
                frame.aside(t("manager.security.ignore"), lambda: signer.ignore(request.id),
                            icon=verbs.IGNORE).tooltip(t("manager.security.ignore_hint"))
                frame.answer(t("manager.security.show"), lambda: signer.reveal(request.id),
                             icon=verbs.UNHIDE).tooltip(t("manager.security.show_hint"))
    if shown:
        frame.on_escape(sheet, lambda: signer.dismiss(request.id))
    sheet.open()
    return sheet


def _approve(ask: approvals.Ask) -> ui.dialog:
    asks = approvals.instance()
    change = "\n".join(part for part in (" ".join((ask.method, ask.path)), ask.body) if part)
    with frame.opened(t("manager.security.asks", program=ask.token_name),
                      size="question", ignore_outside_click=True) as sheet:
        ui.label(change).classes("manager-need-change").props("tabindex=0")
        ui.label(t("manager.security.asks_lapse", count=max(1, asks.seconds_left(ask) // 60))) \
            .classes("manager-help")
        with frame.footer():
            frame.aside(t("manager.needs.decline"), lambda: asks.decline(ask.id),
                        icon=verbs.IGNORE).tooltip(t("manager.needs.decline.help"))
            frame.cancel(lambda: asks.leave_screen(ask.id), t("word.close")) \
                .tooltip(t("manager.security.asks_close_hint"))
            frame.answer(t("manager.needs.approve"), lambda: asks.approve(ask.id),
                         icon=verbs.ACCEPT).tooltip(t("manager.needs.approve.help"))
    frame.on_escape(sheet, lambda: asks.leave_screen(ask.id))
    sheet.open()
    return sheet
