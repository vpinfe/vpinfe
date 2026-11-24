"""The power icon at the foot of the rail: this install's own device menu."""

from __future__ import annotations

import asyncio
import unittest
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

from nicegui import background_tasks, ui

from common.i18n import t
from common.runtime import lifecycle
from manager import power
from manager.kit import offload, remembered
from manager.settings import waiting
from tests.support.clicks import press

LOCAL = "Aaaa111111"
THIS = {"device_id": LOCAL, "display_name": "Basement", "kind": "vpinfe"}
PORTS = {"kind": "setting", "section": "network", "key": "http_port", "label": "HTTP port",
         "waits_for": "restart", "in_effect": 8001, "set": 8011}
SOCKET = {"kind": "setting", "section": "network", "key": "ws_port", "label": "WebSocket port",
          "waits_for": "restart", "in_effect": 8002, "set": 8012}
ROTATION = {"kind": "setting", "section": "display", "key": "rotation", "label": "Rotation",
            "waits_for": "reopen", "in_effect": 0, "set": 90}
_PAGE = ui.element()


def _entry(scope: str, action: str) -> dict[str, Any]:
    return {"scope": scope, "action": action, "label": lifecycle.label(scope, action),
            "available": True, "reason": "", "reason_key": ""}


def _windows(up: bool) -> list[dict[str, Any]]:
    """What an install that owns windows lists: Open only when they are closed, Close only
    when they are up."""
    refused = {(lifecycle.FRONTEND, lifecycle.START): up,
               (lifecycle.FRONTEND, lifecycle.STOP): not up}
    return [{**_entry(scope, action), "available": not refused.get((scope, action), False)}
            for scope, action in lifecycle.offered()]


EVERY = _windows(up=True)
CLOSED = _windows(up=False)


def _answer(*entries: dict[str, Any], reopen: bool = False) -> dict[str, Any]:
    return {"waiting": list(entries), "reopen": reopen, "http_port": 8011}


async def _direct(call: Any, *args: Any) -> Any:
    return call(*args)


def _menu_labels(menu: ui.element) -> list[str]:
    return [str(one.text) for one in menu.descendants()
            if isinstance(one, ui.label) and not one.is_deleted]


def _shape(menu: ui.element) -> list[str]:
    """The menu top to bottom: each entry's label, and a dash where a rule is drawn."""
    out: list[str] = []
    for one in menu.descendants():
        if one.is_deleted:
            continue
        if isinstance(one, ui.separator):
            out.append("-")
        elif isinstance(one, ui.menu_item):
            out.append(next(str(inner.text) for inner in one.descendants()
                            if isinstance(inner, ui.label)))
    return out


async def _click(menu: ui.element, label: str) -> None:
    """Press the menu item that starts with `label` and wait for what it started."""
    item = next(one for one in menu.descendants()
                if isinstance(one, ui.menu_item)
                and any(isinstance(inner, ui.label) and str(inner.text).startswith(label)
                        for inner in one.descendants()))
    handler = next(listener.handler for listener in item._event_listeners.values()
                   if listener.type == "click")
    assert handler is not None

    async def go() -> None:
        started: list[Any] = []
        with patch.object(background_tasks, "create_or_defer",
                          lambda awaitable, **_: started.append(awaitable)):
            handler(None)
        await asyncio.gather(*started)

    await press(go)


class ThePowerIcon(unittest.IsolatedAsyncioTestCase):

    def setUp(self) -> None:
        self.memory: dict[str, Any] = {}
        for one in (
                patch.object(remembered, "get",
                             lambda key, default=None: self.memory.get(key, default)),
                patch.object(remembered, "put",
                             lambda key, value: self.memory.__setitem__(key, value)),
                patch.object(offload, "io", AsyncMock(side_effect=_direct))):
            one.start()
            self.addCleanup(one.stop)
        self.library = Mock()
        self.library.config_waiting.return_value = _answer()
        self.library.actions.return_value = EVERY
        self.state: dict[str, Any] = {}

    async def _placed(self, signed_in: bool = False) -> tuple[power.Power, waiting.Bar]:
        with _PAGE:
            bar = waiting.Bar(self.library)
            bar.place()
            icon = power.Power(self.library, bar, self.state, [THIS], LOCAL, signed_in=signed_in)
            icon.place()
        await press(bar.refresh)
        await press(icon.load)
        assert icon._menu is not None and icon._button is not None
        self.addCleanup(icon._button.delete)
        return icon, bar

    async def test_the_menu_is_this_installs_verbs_with_no_forget(self) -> None:
        icon, _bar = await self._placed()
        assert icon._menu is not None

        shown = _menu_labels(icon._menu)

        self.assertIn(t("action.vpinfe.restart"), shown)
        self.assertIn(t("action.system.stop"), shown)
        self.assertNotIn(t("word.forget"), shown)

    async def test_a_rule_falls_between_what_each_verb_acts_on(self) -> None:
        icon, _bar = await self._placed()
        assert icon._menu is not None

        self.assertEqual(_shape(icon._menu), [
            "-", t("action.frontend.stop"), t("action.frontend.restart"), "-",
            t("action.vpinfe.restart"), t("action.vpinfe.stop"), "-",
            t("action.system.restart"), t("action.system.stop")])

    async def test_a_browser_signed_in_from_elsewhere_ends_it_last_under_its_own_rule(self) -> None:
        icon, _bar = await self._placed(signed_in=True)
        assert icon._menu is not None

        shape = _shape(icon._menu)

        self.assertEqual(shape[-2:], ["-", t("manager.security.sign_out")])
        self.assertEqual(shape[-3], t("action.system.stop"))

    async def test_this_machines_own_browser_has_no_sign_out_to_offer(self) -> None:
        icon, _bar = await self._placed()
        assert icon._menu is not None

        self.assertNotIn(t("manager.security.sign_out"), _shape(icon._menu))

    async def test_the_icon_stays_for_a_sign_out_when_the_device_offers_nothing(self) -> None:
        self.library.actions.return_value = []
        icon, _bar = await self._placed(signed_in=True)
        assert icon._menu is not None and icon._button is not None

        self.assertTrue(icon._button.visible)
        self.assertEqual(_shape(icon._menu), [t("manager.security.sign_out")])

    async def test_no_dot_and_a_plain_restart_while_nothing_waits(self) -> None:
        icon, _bar = await self._placed()
        assert icon._menu is not None and icon._dot is not None

        self.assertFalse(icon._dot.visible)
        self.assertEqual(_menu_labels(icon._menu)[0], t("action.frontend.stop"))
        self.assertIn(t("action.vpinfe.restart"), _menu_labels(icon._menu))

    async def test_no_dot_while_the_line_is_showing(self) -> None:
        self.library.config_waiting.return_value = _answer(PORTS, SOCKET)
        icon, _bar = await self._placed()
        assert icon._dot is not None

        self.assertFalse(icon._dot.visible)

    async def test_the_dot_and_the_lead_follow_a_dismissed_line(self) -> None:
        self.library.config_waiting.return_value = _answer(PORTS, SOCKET)
        icon, bar = await self._placed()
        assert icon._menu is not None and icon._dot is not None

        bar._dismiss()

        self.assertTrue(icon._dot.visible)
        self.assertEqual(_menu_labels(icon._menu)[0],
                         f"{t('action.vpinfe.restart')} - 2 changes waiting")

    async def test_the_hover_says_how_many_changes_wait_while_the_dot_is_up(self) -> None:
        self.library.config_waiting.return_value = _answer(PORTS, SOCKET)
        icon, bar = await self._placed()
        assert isinstance(icon._tip, ui.tooltip)
        self.assertEqual(icon._tip.text, t("manager.page.power"))

        bar._dismiss()

        self.assertEqual(icon._tip.text, "Power - 2 changes waiting")

    async def test_the_hover_is_plain_again_when_nothing_waits(self) -> None:
        self.library.config_waiting.return_value = _answer(PORTS)
        icon, bar = await self._placed()
        assert isinstance(icon._tip, ui.tooltip)
        bar._dismiss()
        self.library.config_waiting.return_value = _answer()

        await press(bar.refresh)

        self.assertEqual(icon._tip.text, t("manager.page.power"))

    async def test_the_dot_goes_when_the_waiting_change_is_undone(self) -> None:
        self.library.config_waiting.return_value = _answer(PORTS)
        icon, bar = await self._placed()
        assert icon._menu is not None and icon._dot is not None
        bar._dismiss()
        self.library.config_waiting.return_value = _answer()

        await press(bar.refresh)

        self.assertFalse(icon._dot.visible)
        self.assertEqual(_menu_labels(icon._menu)[0], t("action.frontend.stop"))

    async def test_window_changes_alone_lead_with_the_reopen(self) -> None:
        self.library.config_waiting.return_value = _answer(ROTATION, reopen=True)
        icon, bar = await self._placed()
        assert icon._menu is not None

        bar._dismiss()

        self.assertEqual(_menu_labels(icon._menu)[0],
                         f"{t('action.frontend.restart')} - 1 change waiting")

    async def test_windows_up_closes_and_reopens_them_with_no_dot(self) -> None:
        icon, _bar = await self._placed()
        assert icon._menu is not None and icon._dot is not None

        shown = _menu_labels(icon._menu)

        self.assertIn(t("action.frontend.stop"), shown)
        self.assertIn(t("action.frontend.restart"), shown)
        self.assertNotIn(t("action.frontend.start"), shown)
        self.assertFalse(icon._dot.visible)

    async def test_windows_closed_offers_only_opening_them_and_no_dot(self) -> None:
        self.library.actions.return_value = CLOSED
        icon, _bar = await self._placed()
        assert icon._menu is not None and icon._dot is not None

        shown = _menu_labels(icon._menu)

        self.assertIn(t("action.frontend.start"), shown)
        self.assertNotIn(t("action.frontend.stop"), shown)
        self.assertNotIn(t("action.frontend.restart"), shown)
        self.assertFalse(icon._dot.visible)

    async def test_the_menu_follows_the_windows_when_asked_again(self) -> None:
        icon, _bar = await self._placed()
        assert icon._menu is not None and icon._dot is not None
        self.library.actions.return_value = CLOSED

        await press(icon.reload)

        self.assertIn(t("action.frontend.start"), _menu_labels(icon._menu))
        self.assertFalse(icon._dot.visible)

    async def test_an_ask_that_fails_leaves_the_menu_as_it_was(self) -> None:
        icon, _bar = await self._placed()
        assert icon._menu is not None and icon._button is not None
        before = _menu_labels(icon._menu)
        self.library.actions.return_value = []

        await press(icon.reload)

        self.assertEqual(_menu_labels(icon._menu), before)
        self.assertTrue(icon._button.visible)

    async def test_the_menu_is_not_redrawn_under_the_pointer_while_it_is_open(self) -> None:
        icon, _bar = await self._placed()
        assert icon._menu is not None
        self.library.actions.return_value = CLOSED
        icon._opened()

        await press(icon.reload)

        self.assertIn(t("action.frontend.stop"), _menu_labels(icon._menu))
        icon._closed()
        self.assertIn(t("action.frontend.start"), _menu_labels(icon._menu))

    async def test_opening_from_the_lead_goes_through_the_line(self) -> None:
        self.library.actions.return_value = CLOSED
        self.library.config_waiting.return_value = _answer(ROTATION, reopen=True)
        icon, bar = await self._placed()
        assert icon._menu is not None
        reopen = AsyncMock()

        with patch.object(bar, "reopen", reopen):
            bar._dismiss()
            await _click(icon._menu, t("action.frontend.start"))

        reopen.assert_awaited_once()

    async def test_waiting_window_changes_lead_with_opening_when_they_are_closed(self) -> None:
        self.library.actions.return_value = CLOSED
        self.library.config_waiting.return_value = _answer(ROTATION, reopen=True)
        icon, bar = await self._placed()
        assert icon._menu is not None

        bar._dismiss()

        self.assertEqual(_menu_labels(icon._menu)[0],
                         f"{t('action.frontend.start')} - 1 change waiting")

    async def test_an_install_without_windows_has_no_frontend_items_and_no_dot(self) -> None:
        self.library.actions.return_value = [one for one in EVERY
                                             if one["scope"] != lifecycle.FRONTEND]
        icon, _bar = await self._placed()
        assert icon._menu is not None and icon._dot is not None

        shown = _menu_labels(icon._menu)

        for action in ("start", "stop", "restart"):
            self.assertNotIn(t(f"action.frontend.{action}"), shown)
        self.assertFalse(icon._dot.visible)

    async def test_restart_from_the_menu_goes_through_the_line_and_follows_the_port(
            self) -> None:
        self.library.config_waiting.return_value = _answer(PORTS)
        icon, bar = await self._placed()
        bar._dismiss()
        assert icon._menu is not None

        with patch.object(waiting.confirm, "ask", AsyncMock(return_value=True)), \
                patch.object(ui, "run_javascript") as script:
            await _click(icon._menu, t("action.vpinfe.restart"))

        self.library.perform_action.assert_called_once_with("vpinfe", "restart")
        self.assertIn("String(8011)", script.call_args.args[0])

    async def test_restart_with_nothing_waiting_adds_no_toast_to_the_heads_up(self) -> None:
        icon, _bar = await self._placed()
        assert icon._menu is not None

        with patch.object(waiting.confirm, "ask", AsyncMock(return_value=True)), \
                patch.object(ui, "run_javascript"), \
                patch.object(ui, "notify") as toast:
            await _click(icon._menu, t("action.vpinfe.restart"))

        toast.assert_not_called()

    async def test_reopening_the_windows_says_it_as_a_result(self) -> None:
        icon, _bar = await self._placed()
        assert icon._menu is not None

        with patch.object(ui, "notify") as toast:
            await _click(icon._menu, t("action.frontend.restart"))

        toast.assert_called_once_with(t("manager.devices.windows_reopened"), type="positive")

    async def test_a_restart_that_was_not_performed_is_warned_about(self) -> None:
        self.library.perform_action.return_value = {"performed": False}
        icon, _bar = await self._placed()
        assert icon._menu is not None

        with patch.object(waiting.confirm, "ask", AsyncMock(return_value=True)), \
                patch.object(ui, "run_javascript") as script, \
                patch.object(ui, "notify") as toast:
            await _click(icon._menu, t("action.vpinfe.restart"))

        self.assertEqual(toast.call_args.kwargs["type"], "warning")
        script.assert_not_called()

    async def test_no_dot_when_the_device_does_not_offer_the_led_verb(self) -> None:
        self.library.config_waiting.return_value = _answer(PORTS)
        self.library.actions.return_value = [one for one in EVERY
                                             if one["scope"] != lifecycle.VPINFE]
        icon, bar = await self._placed()
        assert icon._dot is not None

        bar._dismiss()

        self.assertFalse(icon._dot.visible)

    async def test_an_install_that_performs_nothing_has_no_icon(self) -> None:
        self.library.actions.return_value = []
        icon, _bar = await self._placed()
        assert icon._button is not None

        self.assertFalse(icon._button.visible)

    async def test_what_the_page_already_read_is_not_read_again(self) -> None:
        self.state["device_actions"] = {LOCAL: EVERY}

        await self._placed()

        self.library.actions.assert_not_called()
