"""Which tables the file fetched from a release is tied to, once the import is done."""

from __future__ import annotations

import unittest
from unittest import mock

from manager.library import getting_file


class _Library:
    def __init__(self) -> None:
        self.tied: list[tuple[str, str, str]] = []

    def set_table_source(self, game_id: str, table_id: str, vps_file_id: str) -> None:
        self.tied.append((game_id, table_id, vps_file_id))


async def _inline(call, *args):
    return call(*args)


class TieTests(unittest.IsolatedAsyncioTestCase):
    async def tie(self, report: dict) -> list[tuple[str, str, str]]:
        library = _Library()
        release = getting_file.release_of("rel1", "2.3 · Dark")
        with mock.patch.object(getting_file.run, "io_bound", _inline):
            await getting_file.tie(library, "G1", release)(report, {})
        return library.tied

    async def test_a_table_the_file_replaced_is_tied_to_the_release(self) -> None:
        tied = await self.tie({"replaced_tables": [{"table": {"id": "t1"}}]})

        self.assertEqual(tied, [("G1", "t1", "rel1")])

    async def test_a_table_the_file_was_kept_beside_the_others_is_tied_too(self) -> None:
        tied = await self.tie({"added_tables": ["t9"]})

        self.assertEqual(tied, [("G1", "t9", "rel1")])

    async def test_a_table_a_patch_made_is_tied_too(self) -> None:
        tied = await self.tie({"patched_tables": ["t5"]})

        self.assertEqual(tied, [("G1", "t5", "rel1")])

    async def test_no_release_ties_nothing(self) -> None:
        library = _Library()

        await getting_file.tie(library, "G1", None)({"added_tables": ["t9"]}, {})

        self.assertEqual(library.tied, [])


if __name__ == "__main__":
    unittest.main()
