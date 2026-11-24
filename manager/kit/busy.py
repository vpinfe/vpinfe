"""A region still being drawn: `aria-busy` on it at once, a spinner once it has taken a
while, and neither after the work ends, however it ends.

The spinner goes in with the mark. How long it stays hidden is the stylesheet's
(`.manager-busy`), not a timer here. Work that overlaps on one region shares one mark and
one spinner, and the region is let go when the last of it ends.
"""

from __future__ import annotations

import weakref
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from nicegui import background_tasks, ui


@dataclass
class _Held:
    count: int
    shown: ui.element


_HELD: weakref.WeakKeyDictionary[ui.element, _Held] = weakref.WeakKeyDictionary()


def _hold(region: ui.element, *, over: bool = False) -> None:
    region.props("aria-busy=true")
    held = _HELD.get(region)
    if held is None or held.shown.is_deleted:
        treatment = " items-center manager-busy--over" if over else ""
        with region:
            shown = ui.row().classes(f"w-full justify-center py-8 manager-busy{treatment}") \
                .props("aria-hidden=true")
            with shown:
                ui.spinner(size="lg").classes("text-primary")
        held = _HELD[region] = _Held(held.count if held else 0, shown)
    held.count += 1


def _let_go(region: ui.element) -> None:
    held = _HELD.get(region)
    if held is not None and held.count > 1:
        held.count -= 1
        return
    _HELD.pop(region, None)
    if region.is_deleted:
        return
    if held is not None and not held.shown.is_deleted:
        held.shown.delete()
    region.props(remove="aria-busy")


def until_gone(region: ui.element) -> None:
    """`region` busy for as long as it exists: a stand-in, deleted once what it stands
    in for is drawn."""
    _hold(region)


@contextmanager
def held(region: ui.element, *, over: bool = False) -> Iterator[None]:
    """`region` busy while the block runs, and not after it - returned, raised or
    cancelled. `over` for a region whose content stays up until the block replaces it:
    that content dims under the spinner rather than having it put after it."""
    _hold(region, over=over)
    try:
        yield
    finally:
        _let_go(region)


def fill(region: ui.element, work: Callable[[], Awaitable[Any]]) -> None:
    """`work` run once the browser has the page, with `region` busy from now until it
    ends.

    The one way to defer a region's first draw. A one-shot `ui.timer` anywhere else in
    the Manager fails `tests/invariants/test_a_region_filled_later_says_so.py`.
    """
    _hold(region)

    async def run() -> None:
        try:
            await work()
        finally:
            _let_go(region)

    ui.timer(0.01, run, once=True)


def start(region: ui.element, work: Callable[[], Awaitable[Any]]) -> None:
    """`work` begun now, off the caller, with `region` busy from now until it ends: for
    an act that reads before it draws again, where what `region` shows stays up, dimmed
    under the spinner, until `work` replaces it."""
    _hold(region, over=True)

    async def run() -> None:
        try:
            await work()
        finally:
            _let_go(region)

    background_tasks.create(run(), name="busy-start")
