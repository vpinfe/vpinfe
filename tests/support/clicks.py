"""What a click, or the browser, does to the Manager, done from a test.

An async test runs in a task of its own, and a task starts with no slot, so a handler
that takes the page it is called from finds none there.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from nicegui import ui

# Made at import, outside any task, where NiceGUI still hands out the script's own page.
_PRESSED = ui.element()


async def press(handler: Callable[..., Awaitable[Any]], *args: Any, **kwargs: Any) -> Any:
    """`handler(*args, **kwargs)`, called and awaited from a slot on the page."""
    with _PRESSED:
        return await handler(*args, **kwargs)


def said(element: ui.element, kind: str, *args: Any) -> None:
    """What the browser sends for `kind`: a message per listener, in the order added."""
    for listener in list(element._event_listeners.values()):
        if listener.type == kind:
            element.client.handle_event({"id": element.id, "listener_id": listener.id,
                                         "args": [json.dumps(arg) for arg in args]})
