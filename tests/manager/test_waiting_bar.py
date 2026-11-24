"""The line over every page for settings saved and not yet in effect."""

from __future__ import annotations

import asyncio
import unittest
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

from nicegui import ui

from common.devices import serving
from manager.kit import confirm, offload, remembered
from manager.settings import waiting
from tests.support.clicks import said

_PAGE = ui.element()

PORTS = {"kind": "setting", "section": "network", "key": "http_port", "label": "HTTP port",
         "waits_for": "restart", "in_effect": 8001, "set": 8011}
SOCKET = {"kind": "setting", "section": "network", "key": "ws_port", "label": "WebSocket port",
          "waits_for": "restart", "in_effect": 8002, "set": 8012}
ROTATION = {"kind": "setting", "section": "display", "key": "rotation", "label": "Rotation",
            "waits_for": "reopen", "in_effect": 0, "set": 90}
VPINPLAY = {"kind": "extension", "section": "extensions", "key": "vpinplay",
            "label": "VPinPlay", "waits_for": "restart", "in_effect": False, "set": True}


def _answer(*entries: dict[str, Any], reopen: bool = False, port: int = 8011) -> dict[str, Any]:
    return {"waiting": list(entries), "reopen": reopen, "http_port": port}


def _labels(within: ui.element) -> list[str]:
    return [str(one.text) for one in within.descendants()
            if isinstance(one, ui.label) and not one.is_deleted]


def _buttons(within: ui.element) -> dict[str, ui.button]:
    return {str(one.props.get("label") or one.text): one for one in within.descendants()
            if isinstance(one, ui.button) and not one.is_deleted}


class WhatTheLineAsksFor(unittest.TestCase):

    def test_a_restart_when_anything_waits_on_one(self) -> None:
        self.assertEqual(waiting.waiting_for(_answer(PORTS, ROTATION, reopen=True)),
                         waiting.RESTART)

    def test_a_reopen_when_that_is_all_and_it_can_be_done(self) -> None:
        self.assertEqual(waiting.waiting_for(_answer(ROTATION, reopen=True)), waiting.REOPEN)

    def test_a_restart_when_a_reopen_cannot_be_done_here(self) -> None:
        self.assertEqual(waiting.waiting_for(_answer(ROTATION, reopen=False)),
                         waiting.RESTART)

    def test_nothing_when_nothing_waits(self) -> None:
        self.assertEqual(waiting.waiting_for(_answer()), "")

    def test_the_heading_says_what_the_changes_wait_for_and_names_no_setting(self) -> None:
        self.assertEqual(waiting.heading(_answer(PORTS, SOCKET)),
                         "Changes will be applied after VPinFE restarts")
        self.assertEqual(waiting.heading(_answer(ROTATION, reopen=True)),
                         "Changes will be applied after the Frontend restarts")


class Dismissing(unittest.TestCase):

    def test_hidden_while_everything_waiting_was_there_when_it_was_dismissed(self) -> None:
        answer = _answer(PORTS, SOCKET)
        self.assertTrue(waiting.hidden(answer, {"network.http_port", "network.ws_port"}))

    def test_a_newly_waiting_setting_brings_it_back(self) -> None:
        self.assertFalse(waiting.hidden(_answer(PORTS, SOCKET), {"network.http_port"}))

    def test_never_hidden_with_nothing_waiting(self) -> None:
        self.assertFalse(waiting.hidden(_answer(), {"network.http_port"}))


class Undoing(unittest.TestCase):

    def test_writes_back_the_values_in_effect_and_switches_extensions_off(self) -> None:
        values, extensions = waiting.undo_plan(_answer(PORTS, SOCKET, ROTATION, VPINPLAY))
        self.assertEqual(values, {"network": {"http_port": 8001, "ws_port": 8002},
                                  "display": {"rotation": 0}})
        self.assertEqual(extensions, ["vpinplay"])


class FollowingThePort(unittest.TestCase):

    def test_asks_the_new_address_and_goes_there(self) -> None:
        script = waiting.follow_script(8011)
        self.assertIn("String(8011)", script)
        self.assertIn("location.href = target", script)

    def test_does_nothing_on_the_port_already_open(self) -> None:
        self.assertIn("port === here", waiting.follow_script(8001))

    def test_does_nothing_on_the_page_served_over_loopback(self) -> None:
        serving.bound("loopback_port", 54321)
        self.addCleanup(serving.forget)

        script = waiting.follow_script(8011)

        self.assertIn('const loopback = String(54321);', script)
        self.assertIn("here === loopback", script)


class TheBar(unittest.IsolatedAsyncioTestCase):

    def setUp(self) -> None:
        self.memory: dict[str, Any] = {}
        patches: list[Any] = [
            patch.object(remembered, "get",
                         lambda key, default=None: self.memory.get(key, default)),
            patch.object(remembered, "put",
                         lambda key, value: self.memory.__setitem__(key, value)),
            patch.object(offload, "io", AsyncMock(side_effect=_direct)),
        ]
        for one in patches:
            one.start()
            self.addCleanup(one.stop)
        self.library = Mock()
        self.library.config_waiting.return_value = _answer(PORTS, SOCKET)

    async def _drawn(self) -> tuple[waiting.Bar, ui.element]:
        with _PAGE:
            bar = waiting.Bar(self.library)
            bar.place()
        await _on_page(bar.refresh)
        assert bar._slot is not None
        self.addCleanup(bar._slot.delete)
        return bar, bar._slot

    async def test_says_what_waits_and_offers_restart_undo_and_dismiss(self) -> None:
        _bar, slot = await self._drawn()
        self.assertIn("Changes will be applied after VPinFE restarts", _labels(slot))
        self.assertEqual(set(_buttons(slot)) - {""}, {"Restart VPinFE", "Undo Changes"})
        self.assertEqual(len([one for one in slot.descendants()
                              if isinstance(one, ui.button)]), 3)

    async def test_offers_reopen_in_place_of_restart_for_window_settings(self) -> None:
        self.library.config_waiting.return_value = _answer(ROTATION, reopen=True)
        _bar, slot = await self._drawn()
        self.assertEqual(set(_buttons(slot)) - {""}, {"Restart Frontend", "Undo Changes"})

    async def test_draws_nothing_when_nothing_waits(self) -> None:
        self.library.config_waiting.return_value = _answer()
        _bar, slot = await self._drawn()
        self.assertEqual(_labels(slot), [])

    async def test_dismissing_hides_it_until_another_change_waits(self) -> None:
        bar, slot = await self._drawn()
        close = [one for one in slot.descendants()
                 if isinstance(one, ui.button) and one.props.get("icon") == "close"][0]
        said(close, "click")
        self.assertEqual(_labels(slot), [])
        self.library.config_waiting.return_value = _answer(PORTS, SOCKET, ROTATION,
                                                          reopen=True)
        await _on_page(bar.refresh)
        self.assertIn("Changes will be applied after VPinFE restarts", _labels(slot))

    async def test_a_dismissed_setting_that_stops_waiting_is_forgotten(self) -> None:
        bar, slot = await self._drawn()
        bar._dismiss()
        self.library.config_waiting.return_value = _answer()
        await _on_page(bar.refresh)
        self.assertEqual(self.memory[waiting.REMEMBERED], [])

    async def test_undo_writes_the_values_in_effect_back(self) -> None:
        bar, _slot = await self._drawn()
        self.library.config_waiting.return_value = _answer()
        await _on_page(bar._undo)
        self.library.put_config.assert_called_once_with(
            {"network": {"http_port": 8001, "ws_port": 8002}})

    async def test_restart_asks_first_and_goes_nowhere_on_a_no(self) -> None:
        bar, _slot = await self._drawn()
        with patch.object(confirm, "ask", AsyncMock(return_value=False)):
            await _on_page(bar.restart)
        self.library.perform_action.assert_not_called()

    async def test_restart_on_a_yes_restarts_and_says_so_in_place_of_the_buttons(self) -> None:
        bar, slot = await self._drawn()
        with patch.object(confirm, "ask", AsyncMock(return_value=True)), \
                patch.object(ui, "run_javascript") as script:
            await _on_page(bar.restart)
        self.library.perform_action.assert_called_once_with("vpinfe", "restart")
        self.assertIn("Restarting VPinFE. This page follows when it is back.", _labels(slot))
        self.assertIn("String(8011)", script.call_args.args[0])

    async def test_reopen_reopens_the_windows_without_asking(self) -> None:
        self.library.config_waiting.return_value = _answer(ROTATION, reopen=True)
        self.library.perform_action.return_value = {"performed": True}
        bar, slot = await self._drawn()
        await _on_page(bar.reopen)
        self.library.perform_action.assert_called_once_with("frontend", "restart")
        self.assertIn("Restarting the Frontend", _labels(slot))

    async def test_reopen_says_so_when_nothing_reopened_the_windows(self) -> None:
        self.library.config_waiting.return_value = _answer(ROTATION, reopen=True)
        self.library.perform_action.return_value = {"performed": False}
        bar, slot = await self._drawn()
        with patch.object(ui, "notify") as note:
            await _on_page(bar.reopen)
        self.assertEqual(note.call_args.args[0], "Restart Frontend did not happen")
        self.assertNotIn("Restarting the Frontend", _labels(slot))

    async def test_undo_redraws_the_page_so_its_fields_show_the_restored_values(self) -> None:
        redraw = Mock()
        with _PAGE:
            bar = waiting.Bar(self.library, redraw)
            bar.place()
        self.addCleanup(lambda: bar._slot and bar._slot.delete())  # type: ignore[func-returns-value]
        await _on_page(bar.refresh)
        self.library.config_waiting.return_value = _answer()
        await _on_page(bar._undo)
        redraw.assert_called_once_with()


async def _on_page(call: Any) -> Any:
    with _PAGE:
        return await call()


async def _direct(call: Any, *args: Any, **kwargs: Any) -> Any:
    await asyncio.sleep(0)
    return call(*args, **kwargs)


if __name__ == "__main__":
    unittest.main()
