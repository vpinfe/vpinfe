"""The Logs control bar is drawn again only when what it is drawn from changed.

The page reads on a timer, and clearing the bar on a tick closes a menu somebody has
open before they can pick from it.
"""

from __future__ import annotations

import unittest
from typing import Any

from manager.system import logs


def _held(**more: Any) -> dict[str, Any]:
    return {"view": logs.STREAM, "sources": ["a.log"], "path": "/logs/a.log", "source": "",
            "visual_pinball": "", "follow": True, "wrap": True, "level": "", "contains": "",
            **more}


class WhatTheBarIsDrawnFrom(unittest.TestCase):
    def test_a_tick_that_read_the_same_log_leaves_the_bar_as_it_is(self) -> None:
        self.assertEqual(logs._bar_key(_held()), logs._bar_key(_held()))

    def test_a_choice_the_selects_hold_themselves_leaves_the_bar_as_it_is(self) -> None:
        for more in ({"view": logs.DIGEST}, {"level": "ERROR"}, {"contains": "serum"}):
            with self.subTest(more):
                self.assertEqual(logs._bar_key(_held()), logs._bar_key(_held(**more)))

    def test_following_or_wrapping_draws_the_bar_again(self) -> None:
        """The follow button's icon and the menu's wording are read from them."""
        self.assertNotEqual(logs._bar_key(_held()), logs._bar_key(_held(follow=False)))
        self.assertNotEqual(logs._bar_key(_held()), logs._bar_key(_held(wrap=False)))

    def test_a_log_that_rotated_draws_its_bar_again(self) -> None:
        self.assertNotEqual(logs._bar_key(_held()),
                            logs._bar_key(_held(sources=["a.log", "a.log.1"])))

    def test_another_file_draws_its_bar_again(self) -> None:
        self.assertNotEqual(logs._bar_key(_held()),
                            logs._bar_key(_held(path="/logs/b.log")))

    def test_a_source_that_appears_draws_its_bar_again(self) -> None:
        """Visual Pinball's log is named once a launcher says where it writes, and the
        select has to offer it."""
        self.assertNotEqual(logs._bar_key(_held()),
                            logs._bar_key(_held(visual_pinball="v.log")))


if __name__ == "__main__":
    unittest.main()
