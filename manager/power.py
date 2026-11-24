"""The power icon at the foot of the rail: this install's own device menu, from every page.

The verbs are the ones the Devices page draws for this device. Restart and the window
reopen go through the waiting line, so they follow the Manager to a new port, and lead the
menu with how much is waiting once the line has been dismissed. On an install that owns
windows the menu also follows them: Close and Restart while they are up, Open while they
are not. The dot is up only while changes wait, and the hover then says how many.

A browser another device signed in ends its own sign-in from the foot of the menu, which is
the one entry about the person and not the device.
"""

from __future__ import annotations

from typing import Any

from nicegui import ui

from common.i18n import t
from common.runtime import lifecycle
from manager.devices import devices
from manager.kit import panel, verbs
from manager.kit.data import Library
from manager.security.signed_in_rows import SIGN_OUT_HERE
from manager.settings import waiting


class Power:
    def __init__(self, library: Library, bar: waiting.Bar, state: dict[str, Any],
                 entries: list[dict[str, Any]], local_id: str,
                 signed_in: bool = False) -> None:
        self.library = library
        self.signed_in = signed_in
        self.bar = bar
        self.state = state
        self.local_id = local_id
        self.device = next((one for one in entries
                            if str(one.get("device_id") or "") == local_id),
                           {"device_id": local_id})
        self._menu: ui.menu | None = None
        self._button: ui.element | None = None
        self._dot: ui.element | None = None
        self._tip: ui.element | None = None
        self._drawn: object = None
        self._open = False
        self._asking = False
        bar.watchers.append(self.refill)

    def place(self) -> None:
        with ui.element("div").classes("manager-nav-mark") as mark:
            button, self._menu = panel.menu_button(verbs.POWER, t("manager.page.power"))
            self._menu.props('anchor="bottom right" self="bottom left"')
            self._dot = ui.label("").classes("manager-nav-badge manager-nav-badge--dot")
        self._button = button
        self._tip = next(one for one in mark if isinstance(one, ui.tooltip))
        button.set_visibility(False)
        self._dot.set_visibility(False)
        self._menu.on("before-show", self._opened)
        self._menu.on("hide", self._closed)

    async def load(self) -> None:
        """What this install performs, unless the page has already read it."""
        cache = self.state.setdefault("device_actions", {})
        if self.local_id not in cache:
            cache[self.local_id] = await devices.load_actions(
                self.library, self.device, self.local_id, None)
        self.refill()

    def _opened(self) -> None:
        self._open = True

    def _closed(self) -> None:
        self._open = False
        self.refill()

    async def reload(self) -> None:
        """Ask again what this install performs, which changes as its windows open and close.
        An answer of nothing is a failed ask, so the menu keeps what it had."""
        if self._asking:
            return
        self._asking = True
        try:
            asked = await devices.load_actions(self.library, self.device, self.local_id, None)
        finally:
            self._asking = False
        cache = self.state.setdefault("device_actions", {})
        if asked or not cache.get(self.local_id):
            cache[self.local_id] = asked
        self.refill()

    def refill(self) -> None:
        """Draw the menu from what is cached and what the line says. Nothing is drawn
        again when it would be the same, and nothing while the menu is open under a pointer."""
        menu, button, dot = self._menu, self._button, self._dot
        if menu is None or button is None or dot is None or menu.is_deleted or self._open:
            return
        offered = (self.state.get("device_actions") or {}).get(self.local_id)
        lead = self.bar.lead()
        closed = devices.windows_closed(offered)
        routed = self.bar.routes()
        if lead is not None and closed and lead[0] == (lifecycle.FRONTEND, lifecycle.RESTART):
            lead = (devices.OPEN_FRONTEND, lead[1])
            routed[devices.OPEN_FRONTEND] = self.bar.reopen
        if lead is not None and not devices.offers(offered, lead[0]):
            lead = None
        acts = devices.acts(
            self.library, [devices.Target(self.device, self.library, offered, False)], None,
            routed=routed, led=lead)
        tail = [panel.Verb(t("manager.security.sign_out"),
                           lambda: ui.run_javascript(f"({SIGN_OUT_HERE})()"))] \
            if self.signed_in else []
        shape = (tuple((one.label, one.run is None, one.hint) for one in acts),
                 lead is not None, closed)
        if shape == self._drawn:
            return
        self._drawn = shape
        panel.verb_menu(menu, devices.device_label(self.device), acts, tail)
        button.set_visibility(bool(acts or tail))
        dot.set_visibility(lead is not None)
        if isinstance(self._tip, ui.tooltip):
            self._tip.set_text(t("manager.page.power_waiting", note=lead[1])
                               if lead is not None else t("manager.page.power"))
