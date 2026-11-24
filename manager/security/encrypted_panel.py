"""Encrypted Connections on Settings > Security: the two lists and the certificate.

Each edit is Protected, so the Manager only calls and its boundary asks for the Confirm.
What a list does is said in the lines under its heading; the page carries no logic of its own.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from nicegui import run, ui

from common.config.paths import get_ini_config
from common.devices.install_identity import display_name
from common.failures import why
from common.host import this_machine
from common.i18n import t
from manager.kit import dialog as frame
from manager.kit import folder_picker, offload, panel, verbs
from manager.kit.api import ApiError
from manager.kit.data import Library
from manager.kit.on_page import on_page

CERTIFICATE_SUFFIXES = (".pem", ".crt", ".cer", ".key")


def _entry(remove: Callable[[str], Any], value: str, hint: str) -> tuple[Any, ...]:
    """One address or range, with the way to take it off the list."""
    def draw() -> None:
        with ui.row().classes("items-center no-wrap gap-2 w-full"):
            ui.label(value).classes("manager-fact-value min-w-0 truncate").tooltip(value)
            panel.icon_action(t("manager.encrypted.remove_entry", address=value),
                              lambda: remove(value), icon=verbs.REMOVE, hint=hint)()

    return (panel.FULL, draw)


def _certificate_rows(held: dict[str, Any], choose: Callable[[], Any],
                      clear: Callable[[], Any]) -> list[tuple[Any, ...]]:
    certfile, keyfile = str(held.get("certfile") or ""), str(held.get("keyfile") or "")
    out: list[tuple[Any, ...]] = [(panel.HEADING, t("manager.encrypted.https")),
                                  panel.lede(t("manager.encrypted.https_line"))]
    if certfile:
        out.append((t("manager.encrypted.certificate_file"), panel.path_value(certfile)))
        out.append((t("manager.encrypted.key_file"), panel.path_value(keyfile)))
        out.append((panel.FULL, panel.action(t("manager.encrypted.certificate_change"), choose,
                                             icon=verbs.CHOOSE,
                                             hint=t("manager.encrypted.certificate_hint"))))
        out.append((panel.FULL, panel.action(t("manager.encrypted.certificate_clear"), clear,
                                             icon=verbs.CLEAR, danger=True,
                                             hint=t("manager.encrypted.certificate_clear_hint"))))
    else:
        out.append(panel.intro(t("manager.encrypted.https_none")))
        out.append((panel.FULL, panel.action(t("manager.encrypted.certificate_choose"), choose,
                                             icon=verbs.CHOOSE,
                                             hint=t("manager.encrypted.certificate_hint"))))
    problem = str(held.get("problem") or "")
    if problem:
        out.append(panel.note(t("manager.encrypted.problem"),
                              hint=t(f"manager.encrypted.problem_{problem}")))
    elif certfile and not held.get("https"):
        out.append(panel.note(t("manager.encrypted.next_start")))
    return out


def entries(held: dict[str, Any], device: str, *, add_proxy: Callable[[], Any],
            remove_proxy: Callable[[str], Any], add_network: Callable[[], Any],
            add_tailscale: Callable[[], Any], remove_network: Callable[[str], Any],
            choose: Callable[[], Any], clear: Callable[[], Any]) -> list[tuple[Any, ...]]:
    """What the section shows, in order.  Pure of the page, so a test reads it."""
    proxies = [str(one) for one in held.get("proxies") or []]
    networks = [str(one) for one in held.get("networks") or []]
    out: list[tuple[Any, ...]] = [
        (panel.HEADING, t("manager.encrypted.heading")),
        panel.lede(t("manager.encrypted.lead", device=device)),
        panel.lede(t("manager.encrypted.lead_skip")),
        (panel.HEADING, t("manager.encrypted.proxies")),
        panel.lede(t("manager.encrypted.proxies_what")),
        panel.lede(t("manager.encrypted.proxies_line"))]
    out += [_entry(remove_proxy, address, t("manager.encrypted.remove_proxy_hint"))
            for address in proxies] or [panel.intro(t("manager.encrypted.proxies_none"))]
    out.append((panel.FULL, panel.action(t("manager.encrypted.add_proxy"), add_proxy,
                                         icon=verbs.ADD,
                                         hint=t("manager.encrypted.add_proxy_hint"))))
    if not held.get("https") and any(this_machine.peer_is_this_machine(one) for one in proxies):
        out.append(panel.note(t("manager.encrypted.proxy_here")))
    out += [(panel.HEADING, t("manager.encrypted.networks")),
            panel.lede(t("manager.encrypted.networks_what")),
            panel.lede(t("manager.encrypted.networks_line"))]
    out += [_entry(remove_network, text, t("manager.encrypted.remove_network_hint"))
            for text in networks] or [panel.intro(t("manager.encrypted.networks_none"))]
    out.append((panel.FULL, panel.action(t("manager.encrypted.add_network"), add_network,
                                         icon=verbs.ADD,
                                         hint=t("manager.encrypted.add_network_hint"))))
    out.append((panel.FULL, panel.action(t("manager.encrypted.add_tailscale"), add_tailscale,
                                         icon=verbs.ADD,
                                         hint=t("manager.encrypted.add_tailscale_hint"))))
    out += _certificate_rows(held, choose, clear)
    return out


async def _ask_entry(title: str, label: str, help_line: str,
                     add: Callable[[str], Awaitable[Any]]) -> bool:
    """True once `add` took what was typed. A refusal stays under the field, with the
    dialog open, so the entry can be corrected."""
    fields: dict[str, Any] = {}

    async def keep() -> None:
        text = str(fields["value"].value or "").strip()
        if not text:
            frame.said(fields["value"], t("manager.encrypted.needs_address"))
            return
        try:
            await add(text)
        except ApiError as exc:
            frame.said(fields["value"], why(exc))
            return
        box.submit(True)

    with frame.opened(title, size="question", ignore_outside_click=True) as box:
        panel.facts(ui, [(label, lambda: fields.update(
            value=frame.field(name=label, room=True)))])
        ui.label(help_line).classes("manager-help")
        with frame.footer():
            frame.cancel(lambda: box.submit(False))
            go = frame.answer(t("manager.encrypted.add"), keep, icon=verbs.ADD)
    frame.enter_presses(go)
    frame.focus(box, fields["value"])
    return bool(await box)


async def _ask_certificate(library: Library, certfile: str,
                           keyfile: str) -> tuple[str, str] | None:
    async def browse(start: str) -> str | None:
        return await folder_picker.pick_path(library.folders, start, kind="file",
                                             suffixes=CERTIFICATE_SUFFIXES)

    fields: dict[str, Any] = {}

    def keep() -> None:
        cert, key = (str(fields[one].value or "").strip() for one in ("cert", "key"))
        if not cert:
            frame.said(fields["cert"], t("manager.encrypted.needs_certificate"))
        if not key:
            frame.said(fields["key"], t("manager.encrypted.needs_key"))
        if cert and key:
            box.submit((cert, key))

    with frame.opened(t("manager.encrypted.certificate_title"), size="wide",
                      ignore_outside_click=True) as box:
        panel.facts(ui, [
            (t("manager.encrypted.certificate_file"),
             lambda: fields.update(cert=frame.field(
                 certfile, browse=browse, name=t("manager.encrypted.certificate_file"),
                 room=True))),
            (t("manager.encrypted.key_file"),
             lambda: fields.update(key=frame.field(
                 keyfile, browse=browse, name=t("manager.encrypted.key_file"), room=True))),
        ])
        ui.label(t("manager.encrypted.certificate_hint")).classes("manager-help")
        with frame.footer():
            frame.cancel(lambda: box.submit(None))
            go = frame.answer(t("manager.encrypted.use_certificate"), keep, icon=verbs.CHOOSE)
    frame.enter_presses(go)
    frame.focus(box, fields["cert"])
    return await box


def page_parts(library: Library, refill: Callable[[], None],
               held: dict[str, Any]) -> list[tuple[Any, ...]]:
    def failed(exc: ApiError) -> None:
        ui.notify(t("manager.encrypted.not_changed"), caption=why(exc), type="negative")

    @on_page
    async def attempt(call: Callable[..., Any], *args: str) -> None:
        try:
            await offload.io(call, *args)
        except ApiError as exc:
            failed(exc)
            return
        refill()

    @on_page
    async def add_proxy() -> None:
        async def add(address: str) -> None:
            await offload.io(library.add_trusted_proxy, address)

        if await _ask_entry(t("manager.encrypted.add_proxy_title"),
                            t("manager.encrypted.proxy_label"),
                            t("manager.encrypted.add_proxy_help"), add):
            refill()

    @on_page
    async def add_network() -> None:
        async def add(text: str) -> None:
            await offload.io(library.add_encrypted_network, text)

        if await _ask_entry(t("manager.encrypted.add_network_title"),
                            t("manager.encrypted.network_label"),
                            t("manager.encrypted.add_network_help"), add):
            refill()

    @on_page
    async def add_tailscale() -> None:
        await attempt(library.add_tailscale)

    @on_page
    async def remove_proxy(address: str) -> None:
        await run.io_bound(library.remove_trusted_proxy, address)
        refill()

    @on_page
    async def remove_network(text: str) -> None:
        await run.io_bound(library.remove_encrypted_network, text)
        refill()

    @on_page
    async def choose() -> None:
        wanted = await _ask_certificate(library, str(held.get("certfile") or ""),
                                        str(held.get("keyfile") or ""))
        if wanted is not None:
            await attempt(library.choose_certificate, *wanted)

    @on_page
    async def clear() -> None:
        await run.io_bound(library.clear_certificate)
        refill()

    return entries(held, display_name(get_ini_config()),
                   add_proxy=add_proxy, remove_proxy=remove_proxy,
                   add_network=add_network, add_tailscale=add_tailscale,
                   remove_network=remove_network, choose=choose, clear=clear)
