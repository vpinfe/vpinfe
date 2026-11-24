"""A stand-in for `panel.region`, for a test that has no page to hold one."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any


class Immediate:
    """Draws where it is awaited, into whatever the test has put in place of the page.
    `on_refill` is called by a refill, so a test can see that one was asked for."""

    def __init__(self, build: Callable[[], Awaitable[Any] | Any],
                 swapped: Callable[[], Any] | None = None, *,
                 on_refill: Callable[[], Any] | None = None) -> None:
        self._build = build
        self._swapped = swapped
        self._on_refill = on_refill

    def __call__(self) -> None:
        pass

    def refill(self) -> None:
        if self._on_refill is not None:
            self._on_refill()

    async def refilled(self) -> None:
        result = self._build()
        if isinstance(result, Awaitable):
            await result
        if self._swapped is not None:
            self._swapped()
