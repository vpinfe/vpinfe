"""Opening Metrics reads the readings once."""

from __future__ import annotations

import unittest
from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock, patch

from nicegui import ui

from manager.system import metrics

_PAGE = ui.element()


def _on_open(timer: MagicMock) -> list[Callable[[], Any]]:
    return [one.args[1] for one in timer.call_args_list
            if one.kwargs.get("once") or one.kwargs.get("immediate", True)]


class OpeningMetrics(unittest.IsolatedAsyncioTestCase):
    async def test_the_readings_are_read_once(self) -> None:
        library = MagicMock()
        library.metrics.return_value = {"available": True, "now": {}, "history": []}

        async def io(call: Callable[..., Any], *args: Any) -> Any:
            return call(*args)

        with patch.object(metrics.ui, "timer") as timer, \
                patch.object(metrics.offload, "io", new=io), _PAGE:
            metrics.build(library, {}, MagicMock())
            for run in _on_open(timer):
                await run()

        self.assertEqual(1, library.metrics.call_count)


if __name__ == "__main__":
    unittest.main()
