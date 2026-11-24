"""Rows dropped on a collection, as the server gathers them from the parts they arrive in."""

from __future__ import annotations

import asyncio
import json
import unittest
from typing import Any
from unittest import mock

from manager.kit import grid
from manager.library import collection_adds, row_drag


def _parts(turn: int, pairs: list[list[str]], **said: Any) -> list[dict[str, Any]]:
    cut: list[list[list[str]]] = [[]]
    size = 0
    for pair in pairs:
        cost = len(pair[0]) + len(pair[1]) + 8
        if size + cost > grid.SELECTION_PART_CHARS and cut[-1]:
            cut.append([])
            size = 0
        cut[-1].append(pair)
        size += cost
    return [{"collection": "Mine", "place": None, "foreign": 0, **said,
             "turn": turn, "at": at, "of": len(cut), "rows": part}
            for at, part in enumerate(cut)]


class DropTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state: dict[str, Any] = {}
        patcher = mock.patch.object(collection_adds, "add", new=mock.AsyncMock())
        self.add = patcher.start()
        self.addCleanup(patcher.stop)

    def _drop(self, parts: list[dict[str, Any]]) -> None:
        for part in parts:
            asyncio.run(row_drag.dropped(None, self.state, part))

    def test_a_drop_past_a_megabyte_arrives_whole(self) -> None:
        pairs = [[f"g{index:06d}", f"t{index:06d}"] for index in range(60_000)]
        parts = _parts(1, pairs, place=3)
        self.assertGreater(len(json.dumps([part["rows"] for part in parts])), 1_000_000)
        self.assertGreater(len(parts), 1)

        self._drop(parts[:-1])
        self.add.assert_not_called()
        self._drop(parts[-1:])

        (_library, name, rows), said = self.add.call_args
        self.assertEqual(name, "Mine")
        self.assertEqual(rows, [collection_adds.Row(game, table) for game, table in pairs])
        self.assertEqual((said["what"], said["at"]), (row_drag.TABLES, 3))

    def test_a_part_of_an_older_drop_is_dropped(self) -> None:
        older = _parts(1, [["g1", ""]] * 30_000)
        self._drop(_parts(2, [["g2", ""]]))
        self._drop(older)

        self.assertEqual(self.add.call_count, 1)
        self.assertEqual(self.add.call_args.args[2], [collection_adds.Row("g2", "")])


if __name__ == "__main__":
    unittest.main()
