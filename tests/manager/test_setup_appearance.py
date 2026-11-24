"""Setup's Appearance step: the Manager's look, picked from the tiles Settings draws."""

from __future__ import annotations

import asyncio
import unittest
from typing import Any
from unittest import mock

from manager.kit import appearance, appearance_picker, offload
from manager.setup import setup, setup_appearance


def _ran(coro: Any) -> Any:
    return asyncio.run(coro)


class AskTests(unittest.TestCase):
    def _ask(self, configured: str = appearance.SYSTEM) -> dict[str, Any]:
        with mock.patch.object(appearance, "configured_mode", return_value=configured):
            return _ran(setup_appearance.ask())

    def test_the_tiles_are_the_ones_settings_offers_and_the_stored_look_is_picked(self) -> None:
        asked = self._ask("synthwave")
        field = asked["fields"][0]

        self.assertEqual(field["type"], "choice")
        self.assertEqual(field["value"], "synthwave")
        self.assertEqual(field["choices"], appearance_picker.choices())
        self.assertEqual([one[0] for one in field["choices"]], list(appearance.MODES))

    def test_choosing_repaints_the_page_before_anything_is_saved(self) -> None:
        asked = self._ask()
        with mock.patch.object(appearance, "repaint") as repaint:
            asked["changed"]({setup_appearance.KEY: "light"})
        repaint.assert_called_once_with("light")

    def test_a_value_nobody_offers_repaints_as_the_default(self) -> None:
        with mock.patch.object(appearance, "repaint") as repaint:
            setup_appearance.repaint({setup_appearance.KEY: "midnight"})
        repaint.assert_called_once_with(appearance.DEFAULT_MODE)


class AnswerTests(unittest.TestCase):
    def _answer(self, configured: str, picked: str) -> mock.Mock:
        client = mock.Mock()
        with mock.patch.object(appearance, "configured_mode", return_value=configured), \
             mock.patch.object(setup_appearance, "ApiClient", return_value=client), \
             mock.patch.object(offload, "io",
                               new=mock.AsyncMock(side_effect=lambda fn, *args: fn(*args))):
            _ran(setup_appearance.answer({setup_appearance.KEY: picked}))
        return client

    def test_a_different_look_is_written_to_the_manager_setting(self) -> None:
        client = self._answer("system", "synthwave")
        client.put_config.assert_called_once_with(
            {appearance.MODE_SECTION: {appearance.MODE_KEY: "synthwave"}})

    def test_the_look_already_stored_is_not_written_again(self) -> None:
        self._answer("system", "system").put_config.assert_not_called()


class StepTests(unittest.TestCase):
    def test_the_step_follows_how_you_play(self) -> None:
        keys = [one.key for one in setup.STEPS]
        self.assertEqual(keys[keys.index(setup.HOW) + 1], setup.APPEARANCE)


if __name__ == "__main__":
    unittest.main()
