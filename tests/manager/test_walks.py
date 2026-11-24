"""Walking a selection: one dialog per row, Skip leaves a row, Stop ends the run."""

from __future__ import annotations

import asyncio
import contextlib
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from manager.kit import on_page, walks


def rows(*names: str) -> list[dict]:
    return [{"id": name} for name in names]


def walked(answers: list, chosen: list[dict], *, bad: tuple[str, ...] = ()):
    """Run a walk answering `answers` in turn; the places asked at and the rows written."""
    handed = iter(answers)
    places: list[str] = []
    written: list[tuple[str, object]] = []

    async def ask(_row, place):
        places.append(place)
        return next(handed)

    async def write(row, answer):
        if row["id"] in bad:
            raise RuntimeError("no")
        written.append((row["id"], answer))

    with patch.object(walks.ui, "notify", lambda *a, **k: None), \
            patch.object(on_page.ui, "context",
                         SimpleNamespace(client=contextlib.nullcontext())):
        changed = asyncio.run(walks.each(chosen, ask, write, "failed"))
    return changed, places, written


class Each(unittest.TestCase):
    def test_every_row_is_asked_at_its_place_in_the_run(self) -> None:
        _changed, places, _written = walked(["a", "b", "c"], rows("x", "y", "z"))

        self.assertEqual(["1 of 3", "2 of 3", "3 of 3"], places)

    def test_skip_leaves_a_row_unwritten(self) -> None:
        changed, _places, written = walked(["a", walks.SKIPPED, "c"], rows("x", "y", "z"))

        self.assertEqual(2, changed)
        self.assertEqual([("x", "a"), ("z", "c")], written)

    def test_stop_ends_the_run_and_keeps_what_was_written(self) -> None:
        changed, places, written = walked(["a", walks.STOPPED, "c"], rows("x", "y", "z"))

        self.assertEqual(1, changed)
        self.assertEqual(2, len(places))
        self.assertEqual([("x", "a")], written)

    def test_a_write_that_fails_does_not_end_the_run(self) -> None:
        changed, _places, written = walked(["a", "b"], rows("x", "y"), bad=("x",))

        self.assertEqual(1, changed)
        self.assertEqual([("y", "b")], written)

    def test_a_failed_write_says_so_with_the_reason(self) -> None:
        said: list[tuple[tuple, dict]] = []

        async def ask(_row, _place):
            return "a"

        async def write(_row, _answer):
            raise RuntimeError("disk full")

        with patch.object(walks.ui, "notify", lambda *a, **k: said.append((a, k))), \
                patch.object(on_page.ui, "context",
                             SimpleNamespace(client=contextlib.nullcontext())):
            asyncio.run(walks.each(rows("x"), ask, write, "failed"))

        self.assertEqual("failed", said[0][0][0])
        self.assertIn("disk full", str(said[0][1]["caption"]))
        self.assertEqual("negative", said[0][1]["type"])


if __name__ == "__main__":
    unittest.main()
