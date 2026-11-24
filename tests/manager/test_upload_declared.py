"""What files imported for a game are declared to be."""

from __future__ import annotations

import unittest

from manager.library import uploads

ANALYSIS = {"assets": [{"entries": [{"path": "afm/pup/screen.mp4"},
                                    {"path": "afm.cRZ"}]},
                       {"entries": [{"path": ""}]}]}


class WhatAGameDeclares(unittest.TestCase):
    def test_every_file_is_declared_for_the_game_by_the_user(self) -> None:
        said = {"game_id": "game", "host": "user", "confirmed_by": "user"}
        self.assertEqual({"screen.mp4": said, "afm.cRZ": said},
                         uploads._declared(ANALYSIS, "game"))

    def test_nothing_is_declared_without_a_game(self) -> None:
        self.assertEqual({}, uploads._declared(ANALYSIS, ""))


if __name__ == "__main__":
    unittest.main()
