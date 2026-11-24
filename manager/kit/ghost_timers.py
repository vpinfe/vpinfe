"""A page whose tab is gone stops polling until the tab comes back.

Timers of `MIN_POLL_S` or less are left running. NiceGUI does not say which timers are
one-shots, and a paused one-shot is dropped when its delay ends instead of waiting, so the
interval is the only guard; every one-shot a page starts is at or under this.
"""

from __future__ import annotations

from nicegui import Client, app
from nicegui.elements.timer import Timer

MIN_POLL_S = 1.0

_paused: dict[str, list[Timer]] = {}


def _pause(client: Client) -> None:
    if client.has_socket_connection:
        return
    held = [one for one in list(client.elements.values())
            if isinstance(one, Timer) and one.active and one.interval > MIN_POLL_S]
    for one in held:
        one.deactivate()
    _paused[client.id] = held


def _resume(client: Client) -> None:
    for one in _paused.pop(client.id, ()):
        if not one.is_deleted:
            try:
                one.activate()
            except AssertionError:
                continue


def _forget(client: Client) -> None:
    _paused.pop(client.id, None)


def install() -> None:
    app.on_disconnect(_pause)
    app.on_connect(_resume)
    app.on_delete(_forget)
