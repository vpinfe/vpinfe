"""Which monitors are attached, asked once and remembered.

Every frontend window queries this from its own thread, and the X11 libraries behind
it are not thread-safe, so the answer is serialized and cached.
"""

from __future__ import annotations

import logging
import sys
import threading
from typing import Any

from common.host import screen_names

logger = logging.getLogger("vpinfe.common.host.display_service")


# Xlib / libxcb / libXau are NOT thread-safe. screeninfo.get_monitors() opens an
# X11 display connection via ctypes, so calling it from several pywebview worker
# threads at once (each window's JS calls api.get_monitors() on its own thread)
# races inside libXau and corrupts the malloc heap -> SIGABRT. Serialize access
# with a lock and cache the result: monitor geometry is static for a session, so
# after the first successful query no further X connections are opened.
_monitors_lock = threading.Lock()
_monitors_cache = None
_names_cache: list[str] | None = None


# Two shapes with the same four fields: screeninfo's Monitor everywhere, and the
# frontend's own MonitorInfo on macOS, where screeninfo's coordinates are not the ones
# Chromium wants. Every reader here takes x, y, width and height and nothing else; the
# model name of each, which neither carries, comes from `get_display_names`.
def _query_monitors() -> list[Any]:
    if sys.platform == "darwin":
        from frontend.chromium_manager import get_mac_screens

        return get_mac_screens()

    from screeninfo import get_monitors

    return get_monitors()


def _log_screens(monitors: list[Any]) -> None:
    """Size and position of each screen, in order, so a log read alone says what the run
    drew on. Never a model name, which can be a person's own label."""
    found = "; ".join(f"{number}: {one.width}x{one.height} at {one.x},{one.y}"
                      for number, one in enumerate(monitors))
    logger.info("Screens: %s", found or "none found")


def get_display_monitors(refresh: bool = False) -> list[Any]:
    """Every display this machine has, or none when it cannot say.

    Enumeration fails on a machine with no session attached - a headless server, a
    container, a session that has gone away - and raising from here would strand the
    frontend. The theme asks for this list while it is starting up, so a throw leaves it
    connected, serving contract 2, and never finishing: a blank screen with everything
    reporting healthy. No monitors is a thing callers already handle; not answering is
    not.
    """
    global _monitors_cache, _names_cache
    with _monitors_lock:
        if refresh or _monitors_cache is None:
            try:
                _monitors_cache = _query_monitors()
            except Exception:
                logger.warning("Could not enumerate displays; continuing with none",
                               exc_info=True)
                _monitors_cache = []
            _names_cache = None
            _log_screens(_monitors_cache)
        return _monitors_cache


def get_display_names(refresh: bool = False) -> list[str]:
    """One model name per monitor, in `get_display_monitors`' order, "" where unknown."""
    global _names_cache
    monitors = get_display_monitors(refresh)
    with _monitors_lock:
        if _names_cache is None or len(_names_cache) != len(monitors):
            _names_cache = screen_names.model_names(monitors)
        return list(_names_cache)


def monitors_as_dicts() -> list[dict[str, Any]]:
    return [
        {
            "name": f"Monitor {i}",
            "x": monitor.x,
            "y": monitor.y,
            "width": monitor.width,
            "height": monitor.height,
        }
        for i, monitor in enumerate(get_display_monitors())
    ]
