"""What a window asks while its empty library waits on folders, and what arrival does."""

from __future__ import annotations

import types
import unittest
from typing import Any
from unittest import mock

from frontend.api import API


def _window(remote: bool = False) -> Any:
    return types.SimpleNamespace(library=types.SimpleNamespace(remote=remote), _waited=False)


class LibraryWaitingTests(unittest.TestCase):
    def test_arrival_sends_every_window_the_library_once(self) -> None:
        window = _window()
        with mock.patch("common.games.game_repository.waiting_for",
                        side_effect=[["nas.lan"], ["nas.lan"], [], []]), \
                mock.patch("frontend.play_events.library_arrived") as arrived:
            answers = [API.library_waiting(window) for _ in range(4)]

        self.assertEqual(answers, [["nas.lan"], ["nas.lan"], [], []])
        arrived.assert_called_once()

    def test_a_window_that_never_waited_sends_nothing(self) -> None:
        with mock.patch("common.games.game_repository.waiting_for", return_value=[]), \
                mock.patch("frontend.play_events.library_arrived") as arrived:
            self.assertEqual(API.library_waiting(_window()), [])

        arrived.assert_not_called()

    def test_a_library_on_another_install_is_not_waited_on_here(self) -> None:
        with mock.patch("common.games.game_repository.waiting_for") as asked:
            self.assertEqual(API.library_waiting(_window(remote=True)), [])

        asked.assert_not_called()


if __name__ == "__main__":
    unittest.main()
