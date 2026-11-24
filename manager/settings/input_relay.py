"""Hearing the buttons and keys VPinFE runs with from a Manager opened on another device.

The browser reads a gamepad and a keyboard only on its own device, so a binding table
opened elsewhere cannot hear the ones VPinFE runs with. While the page's switch is on it
asks, over `common.events`, for the frontend's controller window to report them, and passes
each one to the page's own listener. The Manager does not import the frontend: the bus is
the only thing between them.

A page holds the buttons from the moment it arms until it disarms, is taken from, or goes
away. The frontend names the newest holder at the cabinet.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from dataclasses import dataclass

from nicegui import Client

from common import events
from common.host import frontend_state, launch_state

logger = logging.getLogger("vpinfe.manager.settings.input_relay")

# How long the frontend keeps the controller for one ask, and how often a page asks again.
# A page that is closed or navigated away stops asking, and the cabinet's buttons and keys
# come back on their own after the first.
TTL_MS = 6000
REFRESH_S = 2.0

# How long the windows an Input page opened wait after the last page leaves, so a reload
# or a sleeping laptop that wakes does not close them under the person.
LEAVE_GRACE_S = 15.0

_TAKEN_JS = "window.__bindingLive && window.__bindingLive.taken && window.__bindingLive.taken()"


@dataclass
class _Lease:
    client: Client
    loop: asyncio.AbstractEventLoop
    browser: str
    device: str
    since: float


# The leases and the take-back marks change on the event loop and on the thread a
# frontend call arrives on, so each is read and written under this lock.
_lock = threading.RLock()
_leases: dict[str, _Lease] = {}
_present: dict[str, Client] = {}
_hooked: set[str] = set()
# Pages taken from at the cabinet. A refresh already on its way must not hand the buttons
# back, so the page stays out until its own switch is turned on.
_taken: set[str] = set()
_close_when_gone = False
_subscribed = False


def frontend_up() -> bool:
    return frontend_state.current().running


def table_playing() -> bool:
    return launch_state.current().launching


def arm(client: Client, *, browser: str = "", device: str = "") -> None:
    """The page on `client` wants the cabinet's buttons. Called again to keep them. A page
    with no connection is not asked for: it is asleep or gone, and one taken from at the
    cabinet is not asked for until `resume`."""
    if not client.has_socket_connection:
        return
    _subscribe()
    _hook(client)
    with _lock:
        if client.id in _taken:
            return
        held = _leases.get(client.id)
        _leases[client.id] = _Lease(client, asyncio.get_running_loop(), browser, device,
                                    held.since if held else time.monotonic())
    _announce()


def resume(client: Client) -> None:
    """The person turned the switch on, which is the only thing that lifts a take-back."""
    with _lock:
        _taken.discard(client.id)


def disarm(client: Client) -> bool:
    """Whether the page held the buttons until now."""
    with _lock:
        held = _leases.pop(client.id, None)
    if held is not None:
        _announce()
    return held is not None


def holders() -> int:
    with _lock:
        return len(_leases)


def present(client: Client) -> None:
    """The page on `client` is an Input page, whatever its switch says. What the windows an
    Input page opened wait on is whether any is left."""
    _subscribe()
    _present[client.id] = client
    _hook(client)


def close_with_the_last_page() -> None:
    """The frontend windows were opened for the Input pages and close when none is left."""
    global _close_when_gone
    _close_when_gone = True


def leave_open() -> None:
    global _close_when_gone
    _close_when_gone = False


def _hook(client: Client) -> None:
    if client.id in _hooked:
        return
    _hooked.add(client.id)
    client.on_disconnect(lambda: _disconnected(client))
    client.on_delete(lambda: _forget(client))


def _announce() -> None:
    with _lock:
        newest = max(_leases.values(), key=lambda lease: lease.since, default=None)
    if newest is None:
        events.emit(events.INPUT_CAPTURE_ARMED, armed=False, ttl_ms=0, browser="", device="")
        return
    events.emit(events.INPUT_CAPTURE_ARMED, armed=True, ttl_ms=TTL_MS,
                browser=newest.browser, device=newest.device)


def _disconnected(client: Client) -> None:
    disarm(client)
    _check_later(client.id)


def _forget(client: Client) -> None:
    _hooked.discard(client.id)
    with _lock:
        _taken.discard(client.id)
    disarm(client)
    _present.pop(client.id, None)
    _check_later(client.id)


def _check_later(client_id: str) -> None:
    if not _close_when_gone:
        return
    try:
        asyncio.get_running_loop().call_later(LEAVE_GRACE_S, _left)
    except RuntimeError:
        logger.debug("No loop to wait on after %s left", client_id)


def _left() -> None:
    """Closes the windows once no Input page is connected and no table is playing."""
    global _close_when_gone
    if not _close_when_gone:
        return
    if any(not client.is_deleted and client.has_socket_connection
           for client in _present.values()):
        return
    if not frontend_up():
        _close_when_gone = False
        return
    if table_playing():
        asyncio.get_running_loop().call_later(LEAVE_GRACE_S, _left)
        return
    _close_when_gone = False
    asyncio.get_running_loop().run_in_executor(None, _close_windows)


def _close_windows() -> None:
    from manager.kit.api import ApiClient

    try:
        ApiClient().perform_action("frontend", "stop", "the Input page that opened them left")
    except Exception:
        logger.exception("Could not close the frontend windows the Input page opened")


def _subscribe() -> None:
    global _subscribed
    if not _subscribed:
        events.subscribe(events.INPUT_CAPTURE_PRESS, _pressed)
        events.subscribe(events.INPUT_CAPTURE_RELEASED, _released)
        events.subscribe(events.FRONTEND_STATE_CHANGED, _frontend_changed)
        _subscribed = True


def _relay_js(token: str, phase: str) -> str:
    return (f"window.__bindingLive && window.__bindingLive.relay({json.dumps(token)}, "
            f"{'true' if phase == 'down' else 'false'})")


def _pressed(*, token: str, phase: str, **_payload: object) -> None:
    """A button or key on the cabinet. Runs on whichever thread the frontend's call arrived on."""
    code = _relay_js(token, phase)
    with _lock:
        held = list(_leases.values())
    for lease in held:
        lease.loop.call_soon_threadsafe(_push, lease.client, code)


def _released(**_payload: object) -> None:
    """A button held at the cabinet took the buttons back from every page."""
    with _lock:
        leases = list(_leases.values())
        _taken.update(lease.client.id for lease in leases)
        _leases.clear()
    for lease in leases:
        lease.loop.call_soon_threadsafe(_push, lease.client, _TAKEN_JS)
    events.emit(events.INPUT_CAPTURE_ARMED, armed=False, ttl_ms=0, browser="", device="")


def _frontend_changed(*, state: dict, **_payload: object) -> None:
    """Windows that closed some other way are not this page's to close the next time they open."""
    global _close_when_gone
    if not state.get("running"):
        _close_when_gone = False


def _push(client: Client, code: str) -> None:
    if client.is_deleted:
        _forget(client)
        return
    try:
        client.run_javascript(code)
    except Exception:
        logger.exception("Could not pass a controller button to a Manager page")


def reset_for_tests() -> None:
    global _subscribed, _close_when_gone
    with _lock:
        _leases.clear()
        _taken.clear()
    _present.clear()
    _hooked.clear()
    _close_when_gone = False
    _subscribed = False
