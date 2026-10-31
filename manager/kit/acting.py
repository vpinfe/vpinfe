"""Which sign-in a Manager call is made for.

The Manager draws pages for browsers on other devices but calls the API from this one,
over loopback, where every caller is this device. So each call carries the sign-in of the
browser it is made for, and the API holds it to that tier. This device's own browser
carries none and stays local.

The sign-in is read off the page when work is handed to a worker thread, because the worker
has no page. Work that starts with no page at all - a timer, a background task - carries
nothing, and runs as this device.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
import logging
import os
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from nicegui import run, ui
from nicegui.client import Client
from nicegui.slot import Slot, get_task_id

from common.security import sign_in
from httpapi import auth

logger = logging.getLogger("vpinfe.manager.kit.acting")

# Long enough to read six digits off another screen and type them.
CONFIRM_WAIT_SECONDS = 600

# The API client keeps a pooled connection for each worker. A worker without one opens a
# connection and discards it.
WORKERS = min(32, (os.cpu_count() or 1) + 4)


@dataclass(frozen=True)
class Acting:
    """The browser a worker's calls are made for, and the page to ask it on."""

    session_id: str
    client: Client
    loop: asyncio.AbstractEventLoop
    encrypted: bool = False


_current: contextvars.ContextVar[Acting | None] = contextvars.ContextVar(
    "vpinfe_manager_acting", default=None)
_asking: dict[str, concurrent.futures.Future[bool]] = {}
_asking_lock = threading.Lock()


def current() -> Acting | None:
    return _current.get()


def _page_client() -> Client | None:
    """The client of the page being served, None with no page. Asking NiceGUI for one before
    the app starts makes a client with no request, which its storage pruning then fails on
    every ten seconds."""
    if not Slot.stacks.get(get_task_id()):
        return None
    return ui.context.client


def _page_acting() -> Acting | None:
    """The browser the page being served is open in, None for this device's own or for
    work with no page."""
    try:
        client = _page_client()
        if client is None:
            return None
        request = client.request
        loop = asyncio.get_running_loop()
    except Exception:  # noqa: BLE001 - no page, no browser to act for
        return None
    if request is None or auth.caller_is_local(request):
        return None
    session = auth.session_for(request)
    return Acting(session.id if session else auth.NOBODY, client, loop,
                  auth.caller_is_encrypted(request))


def browser_is_here() -> bool:
    """Whether the page being served is open on the device VPinFE runs on. A browser behind
    a reverse proxy, or one that arrived forwarded, is on another device."""
    try:
        client = _page_client()
        request = client.request if client is not None else None
    except Exception:  # noqa: BLE001 - no page, no browser to say
        return False
    return request is not None and auth.caller_is_local(request)


def play_only() -> bool:
    """Whether the page being served is open in a browser that came in with a Remote pass,
    which holds Play and nothing more."""
    try:
        client = _page_client()
        request = client.request if client is not None else None
    except Exception:  # noqa: BLE001 - no page, nothing to hold to Play
        return False
    if request is None or auth.caller_is_local(request):
        return False
    session = auth.session_for(request)
    return session is not None and session.kind == sign_in.REMOTE


class ActingPool(ThreadPoolExecutor):
    """NiceGUI's worker threads, each started with the sign-in of the page that handed it
    work."""

    def __init__(self) -> None:
        super().__init__(max_workers=WORKERS)

    def submit(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
        context = contextvars.copy_context()
        context.run(_current.set, _page_acting())
        return super().submit(context.run, fn, *args, **kwargs)


def install() -> None:
    """Hand NiceGUI's workers the sign-in. Safe to call again."""
    if not isinstance(run.thread_pool, ActingPool):
        run.thread_pool.shutdown(wait=False)
        run.thread_pool = ActingPool()


def confirm() -> bool:
    """Ask the browser a worker acts for to Confirm, and wait for its answer. Called from the
    worker, which is blocked meanwhile; a page asked twice at once draws one dialog."""
    acting = current()
    if acting is None:
        return False
    with _asking_lock:
        held = _asking.get(acting.client.id)
        if held is None:
            from manager.kit import confirm_code as dialog

            async def ask() -> bool:
                with acting.client:
                    return await dialog.ask()

            held = asyncio.run_coroutine_threadsafe(ask(), acting.loop)
            _asking[acting.client.id] = held
            held.add_done_callback(lambda _done: _asking.pop(acting.client.id, None))
    try:
        return bool(held.result(timeout=CONFIRM_WAIT_SECONDS))
    except Exception:  # noqa: BLE001 - a page closed with the dialog open is a no
        logger.info("The Confirm was not answered", exc_info=True)
        return False
