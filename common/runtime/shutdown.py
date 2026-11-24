"""What a kill signal does to this process.

Startup has nothing to close yet and writes to the library as it goes, so a signal is
noted and acted on at the next step boundary. Once the frontend is up it takes the same
route as a user's own quit - anything else skips shutdown_services entirely.
"""

from __future__ import annotations

import logging
import signal
import threading
from collections.abc import Callable

_requested = threading.Event()


def handle_termination(request_shutdown: Callable[[], None]) -> None:
    """Route SIGTERM/SIGINT/SIGBREAK into request_shutdown. The last caller wins."""
    def _on_signal(signum: int, _frame: object) -> None:
        # Hand the signal back to the default so a second Ctrl+C still kills us
        # outright. Shutdown waits on services, and one of them can hang.
        signal.signal(signum, signal.SIG_DFL)
        _requested.set()
        request_shutdown()

    for name in ("SIGTERM", "SIGINT", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, _on_signal)
        except (OSError, ValueError):
            pass


def watch_during_startup() -> None:
    """Start noting a signal before there is anything to close."""
    handle_termination(lambda: None)


def requested() -> bool:
    return _requested.is_set()


def exit_if_requested(logger: logging.Logger) -> None:
    """Leave between startup steps rather than being killed inside one."""
    if not _requested.is_set():
        return
    logger.info("Shutdown requested during startup - stopping here.")
    raise SystemExit(0)


def reset_for_tests() -> None:
    _requested.clear()
