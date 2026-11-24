"""What a Manager handler says after a wait, said on the page."""

from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any

from nicegui import ui


def on_page[**P, R](handler: Callable[P, Awaitable[R]]) -> Callable[P, Coroutine[Any, Any, R]]:
    """`handler`, run under the page that was current when it was called rather than the
    control that called it. Whatever it draws outside a container of its own goes to the
    end of the page.
    """
    @functools.wraps(handler)
    def called(*args: P.args, **kwargs: P.kwargs) -> Coroutine[Any, Any, R]:
        page = ui.context.client

        async def run() -> R:
            with page:
                return await handler(*args, **kwargs)

        return run()

    return called
