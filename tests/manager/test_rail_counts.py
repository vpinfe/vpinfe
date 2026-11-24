"""What the game's rail rows count, and the order they read in."""

from __future__ import annotations

import unittest
from unittest import mock

from manager.kit import offload
from manager.workbench import rail
from manager.workbench.game_table import _collections_held, _collections_label, _recounted


class _Library:
    def __init__(self, held: dict | Exception) -> None:
        self.held = held

    def game_collections(self, game_id: str) -> dict:
        if isinstance(self.held, Exception):
            raise self.held
        return self.held


class CollectionsRow(unittest.IsolatedAsyncioTestCase):
    def test_it_counts_the_collections_that_hold_the_game(self) -> None:
        self.assertEqual("Collections (2)", _collections_label({"in_collections": 2}))

    def test_zero_is_a_count_not_an_absence(self) -> None:
        self.assertEqual("Collections (0)", _collections_label({"in_collections": 0}))

    def test_a_failed_read_names_the_row_without_a_number(self) -> None:
        self.assertEqual("Collections", _collections_label({"in_collections": None}))

    async def test_a_game_kept_out_of_a_collection_is_not_in_it(self) -> None:
        library = _Library({"collections": [{"name": "A"}], "taken_out": [{"name": "B"}]})

        with mock.patch.object(offload, "io", new=_inline):
            self.assertEqual(1, await _collections_held(library, "g"))  # type: ignore[arg-type]

    async def test_a_read_that_raises_is_no_count(self) -> None:
        library = _Library(RuntimeError("down"))

        with mock.patch.object(offload, "io", new=_inline):
            self.assertIsNone(await _collections_held(library, "g"))  # type: ignore[arg-type]


class AfterAWriteThatMovesTheGame(unittest.IsolatedAsyncioTestCase):
    async def test_the_count_is_read_again_and_the_row_relabelled(self) -> None:
        library = _Library({"collections": [{"name": "A"}, {"name": "B"}], "taken_out": []})
        said: list[str] = []

        async def relabel() -> None:
            said.append(_collections_label(context))

        context = {"library": library, "game_id": "g", "in_collections": 1,
                   "relabels": [relabel]}

        with mock.patch.object(offload, "io", new=_inline):
            await _recounted(context)

        self.assertEqual(["Collections (2)"], said)


class TheRail(unittest.TestCase):
    def test_guides_is_the_last_row_of_a_game(self) -> None:
        keys = [one.key for one in rail.SECTIONS if "game" in one.subjects]

        self.assertEqual("guides", keys[-1])


async def _inline(call, *args):
    return call(*args)


if __name__ == "__main__":
    unittest.main()
