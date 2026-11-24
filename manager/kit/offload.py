"""Run blocking work off the event loop, and answer with what it returned.

`nicegui.run.io_bound` is declared `-> R | None`. The `None` is real but narrow: it means
the call was cancelled or the app is shutting down, never that the work produced nothing.
Every caller here reads the answer immediately - `.get(...)`, an iteration, an index - so
with that declaration each one is a type error, and there were sixty.

Guarding all sixty would be the wrong fix twice over. It would say a service can answer
`None` when it cannot, and NiceGUI's own docstring says the shape is temporary: 4.0 raises
`CancelledError` instead and asks that `if result is None` checks be treated as interim.

So this raises it now. A cancelled task is what actually happened, `asyncio` already
unwinds one without logging it as a failure, and when 4.0 lands this module is what
changes rather than sixty call sites.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from nicegui import run


async def io[**P, R](callback: Callable[P, R], *args: P.args, **kwargs: P.kwargs) -> R:
    """`run.io_bound`, typed as returning what the callback returns.

    Raises `asyncio.CancelledError` where NiceGUI would have answered `None` - the app is
    going away, so there is no answer to give and nothing further to do on this path.

    **Only for a callback that returns something.** It cannot tell a shutdown apart from a
    callback that answers `None` legitimately, so a void call would raise on every
    success. Those keep calling `run.io_bound` directly, which is what half the call sites
    do and why they have never needed this.
    """
    answered = await run.io_bound(callback, *args, **kwargs)
    if answered is None:
        raise asyncio.CancelledError(
            f"{getattr(callback, '__name__', callback)} did not finish: cancelled or "
            "shutting down")
    return answered
