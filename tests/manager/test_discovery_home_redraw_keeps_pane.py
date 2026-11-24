"""An apply from a pane opened on Home redraws the page, and the pane of the entry that was
open is drawn again with it."""

from __future__ import annotations

from typing import Any
from unittest import mock

from tests.support.home_shelf import ShelfHarness, entry, table

FOUND: dict[str, Any] = {"counted": "2026-10-01T10:00:00Z", "people": [], "rows": [
    entry("o1", "Atlantis", [table("t1", 0)], manufacturer="Bally", year=1989)]}


class HomeRedraw(ShelfHarness):
    FOUND = FOUND

    async def _after_an_apply(self, **state: Any) -> list[Any]:
        """What happened, in order, when the pane's apply asks Home to redraw."""
        self.state.update(state)
        await self._draw(rerender=mock.Mock(
            side_effect=lambda: self.selected.append("redrawn")))
        await self._redraw()
        return self.selected

    async def test_the_pane_of_the_open_entry_is_drawn_again_after_the_page(self) -> None:
        happened = await self._after_an_apply(entry="o1", workbench=True)

        self.assertEqual(["redrawn", {"id": "o1"}], happened)

    async def test_no_pane_is_drawn_where_none_was_open(self) -> None:
        self.assertEqual(["redrawn"], await self._after_an_apply(entry=None, workbench=True))

    async def test_no_pane_is_drawn_where_the_workbench_was_closed(self) -> None:
        self.assertEqual(["redrawn"], await self._after_an_apply(entry="o1", workbench=False))
