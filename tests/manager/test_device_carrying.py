"""A mobile device's Carrying: the library's games it holds, and sending one from here."""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from nicegui import ui

from common.devices import device_client
from common.i18n import t
from manager.devices import devices
from manager.kit import game_tables, panel

_PAGE = ui.element()

DEVICE = {"device_id": "m1", "kind": "vpx_mobile", "display_name": "Tablet",
          "address": "192.0.2.7", "port": 2112}
HELD: list[dict[str, Any]] = [{"folder": "Alpha (Bally 1991)", "game_id": "g1"},
                              {"folder": "Stray", "game_id": None}]
GAMES = [{"id": "g1", "name": "Alpha"}, {"id": "g2", "name": "Beta"}]


def _context(games: list[dict[str, Any]]) -> dict[str, Any]:
    library = Mock(games=games)
    library.game_art.return_value = None
    library.thumbnail_look.return_value = None
    return {"device": DEVICE, "local_device_id": "Aaaa111111", "library": library,
            "reach": {"state": device_client.ANSWERING}, "rebuild": AsyncMock(),
            "state": {"watch_jobs": Mock()}}


async def _rows(context: dict[str, Any], held: list[dict[str, Any]]) -> list[Any]:
    api = Mock()
    api.device_games.return_value = held
    with patch.object(devices, "ApiClient", return_value=api), \
            patch.object(devices, "on_page", lambda handler: handler), \
            patch.object(devices.offload, "io", AsyncMock(side_effect=lambda f, *a: f(*a))):
        return await devices._carrying_rows(context)


def _texts(element: ui.element) -> list[str]:
    return [str(getattr(one, "text", "")) for one in element.descendants()]


class TheRows(unittest.IsolatedAsyncioTestCase):
    async def test_a_game_of_the_library_is_named_as_the_library_names_it(self) -> None:
        rows = await _rows(_context(GAMES), HELD)

        labels = [label for label, _draw in rows if isinstance(label, str)]
        self.assertEqual(labels, ["Alpha", "Stray"])

    async def test_a_folder_that_is_no_game_of_the_library_is_marked_missing(self) -> None:
        rows = await _rows(_context(GAMES), HELD)
        drawn = {label: draw for label, draw in rows if isinstance(label, str)}

        with _PAGE:
            holder = ui.element()
        with holder:
            drawn["Stray"]()
        self.assertIn(game_tables.GONE_WORDS[0], _texts(holder))
        holder.clear()
        with holder:
            drawn["Alpha"]()
        self.assertNotIn(game_tables.GONE_WORDS[0], _texts(holder))

    async def test_the_picker_comes_first_and_an_empty_device_says_so_below_it(self) -> None:
        rows = await _rows(_context(GAMES), [])

        self.assertIs(rows[0][0], panel.FULL)
        self.assertEqual(len(rows), 2)
        self.assertIs(rows[1][0], panel.ASIDE)

    async def test_a_library_with_no_games_offers_no_picker(self) -> None:
        rows = await _rows(_context([]), [])

        self.assertEqual([label for label, _draw in rows], [panel.ASIDE])
        self.assertEqual(t("manager.devices.carrying_empty"), "Nothing on it yet")


class SendingFromHere(unittest.IsolatedAsyncioTestCase):
    async def _picked(self, context: dict[str, Any]) -> AsyncMock:
        send = AsyncMock()
        picker = MagicMock()
        picker.props.return_value = picker
        picker.classes.return_value = picker
        draw = devices._send_picker(context, DEVICE, GAMES, {"g1"})
        with patch.object(devices.panel, "GamePicker", return_value=picker), \
                patch.object(devices, "thumbnails", MagicMock()), \
                patch.object(devices, "on_page", lambda handler: handler), \
                patch.object(devices.send_to_device, "send", send), \
                patch.object(devices.confirm, "ask",
                             AsyncMock(side_effect=AssertionError("a pick asked first"))):
            draw()
            picker.value = "g2"
            await picker.on_value_change.call_args.args[0]()
        self.picker = picker
        return send

    async def test_a_pick_sends_that_game_at_once_and_draws_the_panel_again(self) -> None:
        context = _context(GAMES)

        send = await self._picked(context)

        send.assert_awaited_once_with([{"id": "g2"}], DEVICE, context["state"])
        context["rebuild"].assert_awaited_once()
        self.assertIsNone(self.picker.value)
        self.picker.disable.assert_called_once()

    async def test_the_games_already_on_it_cannot_be_picked(self) -> None:
        context = _context(GAMES)
        built = MagicMock()
        built.props.return_value = built
        built.classes.return_value = built
        with patch.object(devices.panel, "GamePicker", return_value=built) as made, \
                patch.object(devices, "thumbnails", MagicMock()):
            devices._send_picker(context, DEVICE, GAMES, {"g1"})()

        self.assertEqual(made.call_args.args[:2], (GAMES, {"g1"}))


if __name__ == "__main__":
    unittest.main()
