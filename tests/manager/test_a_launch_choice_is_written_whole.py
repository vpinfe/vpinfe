"""Protects: (c) a Loading Screen or Launch Audio pick was sent without its section and refused.

The settings write takes `{section: {key: value}}`. A pick on either pair of dropdowns
has to arrive in that shape, with a fallback that resets and a chosen file in the same
request, or none of it is stored.
"""

from __future__ import annotations

import asyncio
import types
import unittest
from typing import Any
from unittest.mock import AsyncMock

from common.config import loading_choices as choices
from manager.settings import loading_screen

SECTION = choices.SECTION


def _screen(write: AsyncMock) -> loading_screen.LoadingScreen:
    return loading_screen.LoadingScreen(write, library=None, values={}, options=[],
                                        writable=True)


def _pick(handler: Any, value: str) -> None:
    asyncio.run(handler(types.SimpleNamespace(value=value)))


class AFirstChoiceIsWrittenUnderItsSection(unittest.TestCase):
    def test_a_loading_screen_and_the_fallback_it_resets_go_in_one_request(self) -> None:
        write = AsyncMock(return_value=True)
        screen = _screen(write)

        _pick(screen.screen._picked_main, choices.TABLE_PLAYFIELD)

        write.assert_awaited_once_with({SECTION: {
            choices.SCREEN_KEY: choices.TABLE_PLAYFIELD,
            choices.FALLBACK_KEY: choices.FALLBACK_START[choices.TABLE_PLAYFIELD]}})
        self.assertEqual(screen.screen.main, choices.TABLE_PLAYFIELD)

    def test_a_launch_audio_and_its_fallback_go_in_one_request(self) -> None:
        write = AsyncMock(return_value=True)
        screen = _screen(write)

        _pick(screen.sound._picked_main, choices.THEME_SOUND)

        write.assert_awaited_once_with({SECTION: {
            choices.SOUND_KEY: choices.THEME_SOUND,
            choices.SOUND_FALLBACK_KEY: choices.SOUND_FALLBACK_START[choices.THEME_SOUND]}})
        self.assertEqual(screen.sound.main, choices.THEME_SOUND)

    def test_a_fallback_goes_under_the_section_alone(self) -> None:
        write = AsyncMock(return_value=True)
        screen = _screen(write)
        screen.screen.main = choices.TABLE_VIDEO

        _pick(screen.screen._picked_fallback, choices.SPINNER)

        write.assert_awaited_once_with({SECTION: {choices.FALLBACK_KEY: choices.SPINNER}})

    def test_a_chosen_file_is_written_with_the_choice_that_plays_it(self) -> None:
        write = AsyncMock(return_value=True)
        screen = _screen(write)

        asyncio.run(screen.screen._take(choices.MY_VIDEO, "clip.mp4", main=True))

        assert write.await_args is not None
        (changes,), _ = write.await_args
        self.assertEqual(changes[SECTION][choices.SCREEN_KEY], choices.MY_VIDEO)
        self.assertEqual(changes[SECTION][choices.VIDEO_FILE_KEY], "clip.mp4")
        self.assertEqual(list(changes), [SECTION])

    def test_a_refused_write_keeps_the_choice_that_was_showing(self) -> None:
        screen = _screen(AsyncMock(return_value=False))
        before = screen.screen.main

        _pick(screen.screen._picked_main, choices.OFF)

        self.assertEqual(screen.screen.main, before)


if __name__ == "__main__":
    unittest.main()
