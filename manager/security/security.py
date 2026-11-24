"""Settings > Security: how another device signs in to this install, and who is signed in.

A browser on another device gets in by a code the VPinFE device shows. This page is where the
Owner sees who is waiting and what that let in, and takes it back.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from nicegui import background_tasks, ui

from common.config import paths
from common.devices import serving as serving_state
from common.failures import why
from common.i18n import t
from common.security import sign_in
from httpapi import auth
from manager.kit import offload, panel, when
from manager.kit.data import Library
from manager.kit.on_page import on_page
from manager.security import (
    authenticator_panel,
    encrypted_panel,
    sign_in_notice,
    signed_in_rows,
    token_panel,
)

logger = logging.getLogger("vpinfe.manager.security.security")


def this_session_id() -> str:
    """The sign-in the browser drawing this page holds, or "" for this machine's own."""
    try:
        session = auth.session_for(ui.context.client.request)
    except Exception:  # noqa: BLE001 - a page without a request is the machine's own
        return ""
    return session.id if session is not None else ""


def waiting_key(request: sign_in.Request | None) -> tuple[Any, ...] | None:
    """What the Waiting row says, as a value, so the poll can tell when it needs drawing."""
    if request is None:
        return None
    return (request.id, request.state, int((time.time() - request.asked) // 60))


def _waiting(request: sign_in.Request) -> tuple[Any, ...]:
    """Status only: Show and Ignore are answered on the popup every Manager open on this
    device carries."""
    asked = datetime.fromtimestamp(request.asked, UTC).isoformat()

    def draw() -> None:
        with ui.element("div").classes("manager-summary-text"):
            ui.label(request.address).classes("manager-fact-value truncate min-w-0")
            ui.label(t("manager.security.asked", when=when.ago(asked, inline=True))) \
                .classes("manager-summary-sub truncate min-w-0")

    return (t("manager.security.waiting"), draw)


def lede_for(sessions: list[dict[str, Any]], bind: str) -> str:
    """What the section's help line says. Where no other device can reach the Manager there
    is no code to explain, and no help line while nobody is signed in either."""
    days = sign_in.SESSION_SECONDS // 86400
    if serving_state.reachable_from_elsewhere(bind):
        return t("manager.security.sign_in_help", days=days)
    return t("manager.security.sign_in_idle", days=days) if sessions else ""


def entries(sessions: list[dict[str, Any]], request: sign_in.Request | None,
            open_list: Callable[[], Awaitable[None]], bind: str) -> list[tuple[Any, ...]]:
    """What the section shows, in order. Pure of the page, so a test reads it."""
    out: list[tuple[Any, ...]] = [(panel.HEADING, t("manager.security.sign_in"))]
    help_line = lede_for(sessions, bind)
    if help_line:
        out.append(panel.lede(help_line))
    if request is not None:
        out.append(_waiting(request))
    out.append(signed_in_rows.entry(t("manager.security.signed_in"), sessions, open_list))
    return out


async def _read(library: Library) -> dict[str, Any]:
    return {"sessions": await offload.io(library.sessions),
            "tokens": await offload.io(library.tokens),
            "encrypted": await offload.io(library.encrypted),
            "authenticator": await offload.io(library.authenticator)}


async def page(library: Library, redraw: Callable[[], None],
               state: dict[str, Any]) -> Callable[[], None]:
    """Read who is signed in, and hand back the draw. A change reads again and draws the
    sections where they stand."""
    mine = this_session_id()
    try:
        found = await _read(library)
    except Exception as exc:  # noqa: BLE001 - this page says why, never 500s
        reason = why(exc)
        return lambda: panel.facts(ui, [panel.intro(t("manager.security.read_failed"),
                                                    hint=reason)])
    bind = serving_state.network(paths.get_ini_config()).http_bind

    @on_page
    async def read_again() -> None:
        try:
            found.update(await _read(library))
        except Exception as exc:  # noqa: BLE001 - what is on screen stays
            ui.notify(t("manager.security.read_failed"), caption=why(exc), type="negative")
        drawn.refill()

    def refill() -> None:
        background_tasks.create(read_again(), name="security-refill")

    @on_page
    async def open_list() -> None:
        await signed_in_rows.open_list(library, found["sessions"], mine, after=_refilled)

    async def _refilled() -> None:
        refill()

    def parts() -> None:
        asked = sign_in.instance().pending()
        shown = waiting_key(asked)

        def poll() -> None:
            if waiting_key(sign_in.instance().pending()) != shown:
                refill()

        ui.timer(sign_in_notice.POLL_SECONDS, poll)
        panel.facts(
            ui, entries(found["sessions"], asked, open_list, bind)
            + authenticator_panel.page_parts(library, refill, found["authenticator"])
            + token_panel.page_parts(library, refill, found["tokens"])
            + encrypted_panel.page_parts(library, refill, found["encrypted"]))

    drawn = panel.region(parts)
    return drawn
