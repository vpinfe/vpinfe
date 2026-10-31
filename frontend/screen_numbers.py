"""A window on each screen saying its number and name, for a few seconds.

What tells a person which screen the Manager means when its sizes are alike. Each window
is placed the way every frontend window is, so it lands where the frontend would open one,
and on Sway and Hyprland, which ignore that, the compositor is asked to send it there.
"""

from __future__ import annotations

import html
import logging
import os
import tempfile
import threading
import time
from collections.abc import Callable, Sequence
from typing import IO, Any
from urllib.parse import quote

from common.capture.adapters import output_of, wlr
from common.config import paths
from common.config.config_access import SettingsConfig, cfg_get
from common.i18n import t
from frontend.chromium_manager import ChromiumManager

logger = logging.getLogger("vpinfe.frontend.screen_numbers")

SECONDS = 8
# How long a window gets to appear in the compositor before it is given up on.
PLACE_WAIT = 5.0
PLACE_POLL = 0.1
PLACE_SETTLE = 2.0
PLACE_CHECK = 1.5
PLACE_ATTEMPTS = 3

_showing = threading.Lock()

# A window opened for a screen: the screen, its process and where Chromium's errors go.
Opened = tuple[Any, Any, IO[bytes]]


def page(number: int, name: str, size: str) -> str:
    """The page one screen shows. `name` is empty for a screen that has none."""
    named = f'<div class="name">{html.escape(name)}</div>' if name else ""
    return (
        "<!doctype html><meta charset=utf-8><title>VPinFE</title><style>"
        "html,body{margin:0;height:100%;background:#0f0722;color:#fff;overflow:hidden;"
        "font-family:system-ui,sans-serif;cursor:none}"
        "body{display:flex;flex-direction:column;align-items:center;justify-content:center;"
        "gap:2vmin;box-sizing:border-box;border:1.2vmin solid #00d9ff}"
        ".number{font-size:45vmin;font-weight:700;line-height:1}"
        ".name{font-size:7vmin;font-weight:600}.size{font-size:4vmin;color:#cbb8ea}"
        "</style>"
        f'<div class="number">{number}</div>{named}'
        f'<div class="size">{html.escape(size)}</div>')


def address(number: int, name: str, size: str) -> str:
    return "data:text/html;charset=utf-8," + quote(page(number, name, size))


def window_commands() -> wlr.WlrAdapter | None:
    """The session's own window commands, where it has them: Sway and Hyprland place a
    window themselves and take no notice of the position Chromium asks for."""
    return wlr.WlrAdapter(os.environ) if wlr.WlrAdapter.applies(os.environ) else None


def _view_of(compositor: wlr.WlrAdapter, proc: Any) -> str | None:
    deadline = time.monotonic() + PLACE_WAIT
    while proc.poll() is None:
        try:
            view = compositor.view_of(proc.pid)
        except (OSError, ValueError):
            logger.debug("Could not read the compositor's windows", exc_info=True)
            return None
        if view is not None or time.monotonic() >= deadline:
            return view
        time.sleep(PLACE_POLL)
    return None


def _place(compositor: wlr.WlrAdapter, opened: Sequence[tuple[Any, Any]]) -> list[Any]:
    """Each window to the output its screen is, full screen. Returns the screens whose window
    closed on the way: Chromium can abort on a command that reaches it while it starts, and
    opening that window again is the caller's."""
    try:
        outputs = compositor.outputs()
    except (OSError, ValueError):
        logger.warning("Could not read the compositor's outputs", exc_info=True)
        return []
    views = [(screen, proc, _view_of(compositor, proc)) for screen, proc in opened]
    time.sleep(PLACE_SETTLE)
    for screen, proc, view in views:
        output = output_of(screen, outputs)
        if output is None or view is None:
            if proc.poll() is None:
                logger.warning("Could not place the window on screen %s (%s)",
                               screen.number, "no such output" if output is None else "no window")
            continue
        try:
            compositor.fill_output(view, output)
            logger.debug("Placed the window on screen %s on %s", screen.number,
                         output.name)
        except (OSError, ValueError):
            logger.warning("The compositor would not place the window on screen %s",
                           screen.number, exc_info=True)
    time.sleep(PLACE_CHECK)
    return [screen for screen, proc in opened if proc.poll() is not None]


def report(screen: Any, proc: Any, errors: IO[bytes], *, again: bool = False) -> None:
    """What a window that went before its time said, and the output of those that did not."""
    gone = proc.poll() is not None
    said = ""
    if gone:
        errors.seek(0)
        said = errors.read().decode(errors="replace").strip()
    errors.close()
    if gone:
        (logger.info if again else logger.warning)(
            "The window on screen %s closed early (exit %s)",
            screen.number, proc.returncode)
    if said:
        logger.debug("Chromium for the window on screen %s said:\n%s",
                     screen.number, said)


def launch(manager: ChromiumManager, screen: Any, url: str) -> Opened:
    """A window on `screen` showing `url`, opened with the options the frontend's are."""
    config = paths.get_ini_config()
    settings = SettingsConfig.from_config(config)
    errors = tempfile.TemporaryFile()
    try:
        proc = manager.launch_window(
            f"screen{screen.number}",
            url, screen, screen.number, mute_audio=True,
            additional_options=settings.chrome_options,
            include_default_options=settings.default_chrome_options,
            exclude_options=cfg_get(config, "Settings", "chromeoptionsexclude", ""),
            stderr=errors)
    except BaseException:
        errors.close()
        raise
    return screen, proc, errors


def _number_window(screen: Any) -> str:
    size = t("manager.screens.size", width=screen.width, height=screen.height)
    return address(screen.number, screen.name, size)


def _open(manager: ChromiumManager, compositor: wlr.WlrAdapter | None,
          screens: Sequence[Any], seconds: float) -> None:
    opened: list[Opened] = []
    failure: Exception | None = None
    for screen in screens:
        try:
            opened.append(launch(manager, screen, _number_window(screen)))
        except Exception as exc:  # noqa: BLE001 - one screen failing is one screen
            logger.warning("Could not open the window on screen %s",
                           screen.number, exc_info=True)
            failure = exc
    try:
        if screens and not opened and failure is not None:
            raise failure
        if compositor is not None:
            place_all(manager, compositor, opened, _number_window)
        time.sleep(seconds)
    finally:
        for one in opened:
            report(*one)


def place_all(manager: ChromiumManager, compositor: wlr.WlrAdapter, opened: list[Opened],
              url_of: Callable[[Any], str]) -> None:
    """Places `opened`, and opens again a window that closed while it was being placed, at
    the address `url_of` gives for its screen, up to PLACE_ATTEMPTS in all. Edits `opened` as
    it goes, so it always holds what is still open."""
    pending = list(opened)
    for attempt in range(PLACE_ATTEMPTS):
        lost = _place(compositor, [(screen, proc) for screen, proc, _ in pending])
        if not lost or attempt == PLACE_ATTEMPTS - 1:
            return
        pending = []
        for one in [one for one in opened if one[0] in lost]:
            opened.remove(one)
            report(*one, again=True)
            try:
                pending.append(launch(manager, one[0], url_of(one[0])))
            except Exception:  # noqa: BLE001 - one screen failing is one screen
                logger.warning("Could not open the window on screen %s again",
                               one[0].number, exc_info=True)
        opened.extend(pending)


def focused(compositor: wlr.WlrAdapter | None) -> str | None:
    try:
        return compositor.focused_view() if compositor is not None else None
    except (OSError, ValueError):
        logger.debug("Could not read which window has focus", exc_info=True)
        return None


def refocus(compositor: wlr.WlrAdapter | None, view: str | None) -> None:
    """Called once the windows are closed: focusing another window earlier would end the
    full screen of a window on the same workspace."""
    if compositor is None or view is None:
        return
    try:
        compositor.focus_view(view)
    except (OSError, ValueError):
        logger.debug("Could not give focus back", exc_info=True)


def show(screens: Sequence[Any], seconds: float = SECONDS) -> None:
    """Open a window on each of `screens` and close them all after `seconds`. Blocking.

    A screen has `number`, `name`, `width`, `height`, `x` and `y`. A second call while one is
    showing does nothing.
    """
    if not _showing.acquire(blocking=False):
        return
    try:
        manager = ChromiumManager()
        compositor = window_commands()
        before = focused(compositor)
        try:
            _open(manager, compositor, screens, seconds)
        finally:
            manager.terminate_all()
            refocus(compositor, before)
    finally:
        _showing.release()
