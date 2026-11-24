"""A launcher just added: the grid drawn again lands on its row, and the panel opens on it."""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import AsyncMock, patch

from manager.frontend import launchers


class LauncherAdded(unittest.IsolatedAsyncioTestCase):
    async def test_the_new_row_is_landed_on_and_opened(self) -> None:
        order: list[str] = []
        shown = AsyncMock(side_effect=lambda _row: order.append("show"))
        state: dict[str, Any] = {"show_launcher": shown}
        with patch.object(launchers.grid, "land_on",
                          side_effect=lambda *_: order.append("land")) as landed:
            await launchers._open(state, lambda: order.append("redraw"), "wide-copy")
        landed.assert_called_once_with(launchers.SCOPE, {"id": "wide-copy"})
        shown.assert_awaited_once_with({"id": "wide-copy"})
        self.assertEqual(order, ["land", "redraw", "show"])


if __name__ == "__main__":
    unittest.main()
