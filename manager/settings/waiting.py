"""The line over every page for settings that are saved and not yet in effect.

The server holds the answer (`GET /config/waiting`) and this only draws it, so every
Manager on every device says the same thing. Dismissing is the one thing kept here, in the
browser: it hides the entries that were waiting at the time, and an entry that waits
afterwards brings the line back.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from nicegui import ui

from common.devices import serving
from common.failures import why
from common.i18n import t
from common.runtime import lifecycle
from common.settings import settings_waiting
from manager.kit import confirm, offload, panel, remembered, verbs
from manager.kit.api import ApiClient
from manager.kit.data import Library
from manager.kit.on_page import on_page

logger = logging.getLogger("vpinfe.manager.settings.waiting")

POLL_S = 3.0
REMEMBERED = "waiting.dismissed"
# How long the browser keeps asking for the Manager to come back before giving up and
# leaving the page as it is.
FOLLOW_FOR_S = 120
RESTART_HOLD_S = 60.0
REOPEN_HOLD_S = 15.0

RESTART = settings_waiting.WAITS_RESTART
REOPEN = settings_waiting.WAITS_REOPEN


def entry_key(entry: dict[str, Any]) -> str:
    return f"{entry.get('section')}.{entry.get('key')}"


def waiting_for(answer: dict[str, Any]) -> str:
    """What the line asks for: the windows reopened when that is all anything waits on and
    it can be done here, otherwise VPinFE restarted. Empty when nothing waits."""
    entries = answer.get("waiting") or []
    if not entries:
        return ""
    if answer.get("reopen") and all(one.get("waits_for") == REOPEN for one in entries):
        return REOPEN
    return RESTART


def heading(answer: dict[str, Any]) -> str:
    said = "manager.waiting.reopen" if waiting_for(answer) == REOPEN \
        else "manager.waiting.restart"
    return t(said)


def hidden(answer: dict[str, Any], dismissed: set[str]) -> bool:
    """Dismissed while every entry still waiting was already waiting when it was."""
    keys = {entry_key(one) for one in answer.get("waiting") or []}
    return bool(keys) and keys <= dismissed


def undo_plan(answer: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """The values in effect, to write back, and the extensions to switch off again."""
    values: dict[str, dict[str, Any]] = {}
    extensions: list[str] = []
    for one in answer.get("waiting") or []:
        if one.get("kind") == settings_waiting.KIND_EXTENSION:
            extensions.append(str(one.get("key") or ""))
        else:
            values.setdefault(str(one["section"]), {})[str(one["key"])] = one.get("in_effect")
    return values, extensions


def follow_script(port: int) -> str:
    """The browser's side of a restart that moves the Manager: ask the new address until
    it answers, then go there. Nothing is done when the port is the one already open, and
    the page's own reconnect covers that. A page on the loopback listener never follows: its
    port is new at every start, and the one asked here is the network port."""
    return f"""
(() => {{
  const port = String({int(port)});
  const here = location.port || (location.protocol === 'https:' ? '443' : '80');
  const loopback = String({int(serving.bound_port("loopback_port") or 0)});
  if (port === here || here === loopback) return;
  const target = location.protocol + '//' + location.hostname + ':' + port;
  const until = Date.now() + {FOLLOW_FOR_S * 1000};
  const ask = async () => {{
    try {{
      await fetch(target + '/', {{mode: 'no-cors', cache: 'no-store'}});
      location.href = target + location.pathname + location.search;
      return;
    }} catch (gone) {{}}
    if (Date.now() < until) setTimeout(ask, 1000);
  }};
  setTimeout(ask, 1500);
}})();
"""


class Bar:
    """One per Manager page. `place` is called where the header draws it and draws from the
    last answer; `refresh` asks the server again and redraws when it moved."""

    def __init__(self, library: Library, redraw: Callable[[], None] | None = None) -> None:
        self.library = library
        self.redraw = redraw
        self.answer: dict[str, Any] = {}
        self.watchers: list[Callable[[], None]] = []
        self._slot: ui.element | None = None
        self._going: str = ""
        self._until = 0.0

    def place(self) -> None:
        self._slot = ui.column().classes("w-full gap-0")
        self._draw()

    @on_page
    async def refresh(self) -> None:
        try:
            found = await offload.io(self.library.config_waiting)
        except Exception:  # noqa: BLE001 - an install that is restarting answers nothing
            logger.debug("Could not read what is waiting", exc_info=True)
            return
        released = bool(self._going) and time.monotonic() >= self._until
        if found == self.answer and not released:
            return
        self.answer = found
        self._dismissed()
        self._draw()

    def count(self) -> int:
        return len(self.answer.get("waiting") or [])

    def _dismissed(self) -> set[str]:
        held = {str(one) for one in remembered.get(REMEMBERED, []) or []}
        keys = {entry_key(one) for one in self.answer.get("waiting") or []}
        # What stopped waiting is forgotten, so waiting on it again is news.
        if held - keys:
            held &= keys
            remembered.put(REMEMBERED, sorted(held))
        return held

    def quiet(self) -> bool:
        """Something waits and the line has been put away for it."""
        return (bool(self.count()) and not self._going
                and hidden(self.answer, self._dismissed()))

    def lead(self) -> tuple[tuple[str, str], str] | None:
        """What the line would have offered, and how much is waiting on it, for a menu to
        lead with once the line is gone. None while the line is up or nothing waits."""
        if not self.quiet():
            return None
        pair = (lifecycle.FRONTEND, lifecycle.RESTART) if waiting_for(self.answer) == REOPEN \
            else (lifecycle.VPINFE, lifecycle.RESTART)
        waiting = self.count()
        return pair, t("manager.waiting.changes", count=waiting)

    def routes(self) -> dict[tuple[str, str], Callable[[], Any]]:
        """The two actions the line performs, which also follow the Manager to a new port."""
        return {(lifecycle.VPINFE, lifecycle.RESTART): self.restart,
                (lifecycle.FRONTEND, lifecycle.RESTART): self.reopen}

    def _draw(self) -> None:
        self._draw_line()
        for watcher in self.watchers:
            watcher()

    def _draw_line(self) -> None:
        slot = self._slot
        if slot is None or slot.is_deleted:
            return
        slot.clear()
        if not self.answer.get("waiting"):
            self._going = ""
            return
        if self._going and time.monotonic() >= self._until:
            self._going = ""
        if self._going:
            with slot, ui.element("div").classes("manager-attention w-full mb-2"):
                ui.spinner(size="16px").classes("shrink-0")
                ui.label(self._going).classes("manager-attention-line grow")
            return
        if hidden(self.answer, self._dismissed()):
            return
        reopening = waiting_for(self.answer) == REOPEN
        with slot, ui.element("div").classes("manager-attention w-full mb-2"):
            ui.icon("error_outline").classes("manager-attention-icon")
            ui.label(heading(self.answer)).classes("manager-attention-line grow")
            if reopening:
                panel.action(t("manager.waiting.reopen_windows"), self.reopen,
                             icon=verbs.REFRESH,
                             hint=t("action.frontend.restart.help"))()
            else:
                panel.action(t("action.vpinfe.restart"), self.restart, icon=verbs.RESET,
                             hint=t("manager.waiting.restart_help"))()
            panel.action(t("manager.waiting.undo"), self._undo, icon=verbs.REVERT,
                         hint=t("manager.waiting.undo_help"))()
            panel.icon_only(verbs.CLOSE, self._dismiss, hint=t("manager.waiting.dismiss"))

    def _dismiss(self) -> None:
        remembered.put(REMEMBERED, sorted(entry_key(one)
                                          for one in self.answer.get("waiting") or []))
        self._draw()

    def _hold(self, said: str, seconds: float) -> None:
        """Say what is happening in place of the buttons, and give the buttons back if it
        has not cleared the entries by then."""
        self._going, self._until = said, time.monotonic() + seconds
        self._draw()

    @on_page
    async def restart(self) -> None:
        label = t("action.vpinfe.restart")
        if not await confirm.ask(f"{label}?", detail=t("manager.waiting.restart_detail"),
                                 confirm=label, icon=verbs.RESET):
            return
        port = int(self.answer.get("http_port") or 0)
        try:
            done = await offload.io(self.library.perform_action, "vpinfe", "restart")
        except Exception as exc:  # noqa: BLE001 - the reason belongs on the page
            ui.notify(t("said.action_failed"), caption=why(exc), type="negative")
            return
        if not done.get("performed"):
            ui.notify(t("manager.devices.not_happen", label=label), type="warning")
            return
        if self.count():
            self._hold(t("manager.waiting.restarting"), RESTART_HOLD_S)
        if port:
            ui.run_javascript(follow_script(port))

    @on_page
    async def reopen(self) -> None:
        try:
            done = await offload.io(self.library.perform_action, "frontend", "restart")
        except Exception as exc:  # noqa: BLE001 - the reason belongs on the page
            ui.notify(t("said.action_failed"), caption=why(exc), type="negative")
            return
        if not done.get("performed"):
            ui.notify(t("manager.devices.not_happen", label=t("manager.waiting.reopen_windows")),
                      type="warning")
            return
        if self.count():
            self._hold(t("manager.waiting.reopening"), REOPEN_HOLD_S)
        else:
            ui.notify(t("manager.devices.windows_reopened"), type="positive")
        await self.refresh()

    @on_page
    async def _undo(self) -> None:
        values, extensions = undo_plan(self.answer)
        try:
            if values:
                await offload.io(self.library.put_config, values)
            for name in extensions:
                await offload.io(ApiClient().set_extension_enabled, name, False)
        except Exception as exc:  # noqa: BLE001 - the reason belongs on the page
            ui.notify(t("manager.waiting.could_not_undo"), caption=why(exc), type="negative")
        remembered.put(REMEMBERED, [])
        await self.refresh()
        if self.redraw is not None:
            self.redraw()
