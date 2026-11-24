"""The typed wrapper around `run.io_bound`, and the one case it exists for."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from manager.kit import offload


class OffloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_answer_comes_back_as_it_is(self) -> None:
        """The whole point: a caller reads the result without narrowing first."""
        with patch("manager.kit.offload.run.io_bound", new=AsyncMock(return_value={"a": 1})):
            answered: dict[str, int] = await offload.io(dict)
        self.assertEqual(answered.get("a"), 1)

    async def test_a_shutdown_cancels_rather_than_answering_none(self) -> None:
        """NiceGUI answers None when the app is going away, and 4.0 will raise
        CancelledError instead. Raising it now means the call sites do not carry a check
        that is due to be deleted, and a cancelled task unwinds without being logged as a
        failure."""
        with patch("manager.kit.offload.run.io_bound", new=AsyncMock(return_value=None)):
            with self.assertRaises(asyncio.CancelledError):
                await offload.io(dict)

    async def test_the_arguments_reach_the_callback(self) -> None:
        """It forwards, rather than quietly calling with nothing."""
        io_bound = AsyncMock(return_value="done")
        with patch("manager.kit.offload.run.io_bound", new=io_bound):
            await offload.io(str, "a", sep="-")  # type: ignore[arg-type]
        io_bound.assert_awaited_once_with(str, "a", sep="-")
