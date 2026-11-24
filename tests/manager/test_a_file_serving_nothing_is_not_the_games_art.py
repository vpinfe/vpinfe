"""The Games grid's shared slot is the file the game's tables fall to, and no other."""

from __future__ import annotations

import unittest
from unittest.mock import Mock

from manager.kit.data import Library

GAME = {"id": "g-1", "name": "Some Game", "folder": "/games/Some Game"}
SHARED_ABSENT = {"id": "g-1:wheel:", "game_id": "g-1", "kind": "wheel", "table": "",
                 "table_file": "", "present": False, "file": None, "via": None}
SHARED = {**SHARED_ABSENT, "present": True, "file": "Some Game.png", "via": "game"}
ORPHAN = {"id": "g-1:wheel:Gone Table.png", "game_id": "g-1", "kind": "wheel",
          "table": "", "table_file": "Gone Table", "present": True,
          "file": "Gone Table.png", "via": "orphan"}
UNUSED = {"id": "g-1:wheel:wheel.png", "game_id": "g-1", "kind": "wheel", "table": "",
          "table_file": "", "present": True, "file": "wheel.png", "via": "unused"}


def _library(*rows: dict) -> Library:
    client = Mock()
    client.all_media.return_value = list(rows)
    library = Library(client)
    library.games = [GAME]
    library.media = library._shared_media()
    return library


class TheSharedSlot(unittest.TestCase):
    def test_an_orphan_does_not_fill_a_gap(self) -> None:
        wheel = _library(SHARED_ABSENT, ORPHAN).media["g-1"]["wheel"]

        self.assertFalse(wheel["present"])
        self.assertIsNone(wheel["file"])

    def test_an_unused_file_does_not_stand_for_the_one_that_serves(self) -> None:
        wheel = _library(SHARED, UNUSED).media["g-1"]["wheel"]

        self.assertEqual((wheel["file"], wheel["via"]), ("Some Game.png", "game"))

    def test_a_later_page_does_not_stand_for_the_slot(self) -> None:
        flyer = {**SHARED, "id": "g-1:flyer:", "kind": "flyer", "file": "(Flyer) Some Game.png"}
        second = {**flyer, "id": "g-1:flyer::2", "page": 2, "named": "Flyer, page 2",
                  "file": "(Flyer 2) Some Game.png"}

        slot = _library(flyer, second).media["g-1"]["flyer"]

        self.assertEqual(slot["file"], "(Flyer) Some Game.png")

    def test_the_slot_counts_its_pages_from_the_listing(self) -> None:
        flyer = {**SHARED, "id": "g-1:flyer:", "kind": "flyer", "file": "(Flyer) Some Game.png"}
        later = [{**flyer, "id": f"g-1:flyer::{n}", "page": n} for n in (2, 3)]

        self.assertEqual(_library(flyer, *later).media["g-1"]["flyer"]["pages"], 3)
        self.assertEqual(_library(flyer).media["g-1"]["flyer"]["pages"], 1)
        self.assertEqual(_library(SHARED_ABSENT).media["g-1"]["wheel"]["pages"], 0)

    def test_a_game_whose_only_wheel_is_an_orphan_offers_no_wheel_column(self) -> None:
        self.assertEqual(_library(SHARED_ABSENT, ORPHAN).kinds_present(), [])


if __name__ == "__main__":
    unittest.main()
