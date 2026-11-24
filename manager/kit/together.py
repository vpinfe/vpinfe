"""Blocking reads made at once from a worker thread."""

from __future__ import annotations

import contextvars
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

AT_ONCE = 8


def together(*calls: Callable[[], Any]) -> list[Any]:
    """Each call's answer, in the order given, at most AT_ONCE running together. Called from
    a worker thread, never the loop.

    Each runs in a copy of the caller's context, which is what carries the sign-in a
    call to this install is made for. Every call finishes before the first failure is
    raised, so nothing is left running behind a page that has already given up.
    """
    if not calls:
        return []
    if len(calls) == 1:
        return [calls[0]()]
    workers = min(len(calls), AT_ONCE)
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="together") as pool:
        futures = [pool.submit(contextvars.copy_context().run, call) for call in calls]
    return [future.result() for future in futures]
