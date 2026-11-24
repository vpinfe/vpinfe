"""What this machine and this install *are*, as against what they are doing.

Facts, not readings, which is why they sit apart from `metrics` - and why the surface
showing them can offer a copy button. What somebody pastes into a bug report is this,
never a graph.

**Grouped here rather than by whoever draws it.** The screen and the clipboard are two
renderings of one answer, and a page that laid the groups out itself would be a second
place for the order to be decided - which is how the copied text and the visible text
come to disagree.

Nothing here is ever missing. A gap sends the reader looking for the gap, where
"Unknown" says the machine could not answer and that is itself the answer.
"""

from __future__ import annotations

import logging
import os
import platform
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

from common.host import bundled_libraries
from common.i18n import t

logger = logging.getLogger("vpinfe.common.host.about")

# Read once per process. Every one of these costs a subprocess, a config read or a walk
# of the install, and none of them changes while VPinFE is running.
_held: list[dict[str, Any]] | None = None


class PathValue(str):
    """A fact that is a path on disk. A placeholder like "Not set" stays a plain string, so
    a client knows which values to draw as paths."""


class SubLabel(str):
    """A fact's label that belongs to the fact above it, drawn indented behind it."""


# Unlike the other groups the displays are re-read after this long, or on `refresh`.
DISPLAYS_HELD_SECONDS = 30.0
_displays: tuple[float, list[dict[str, Any]]] | None = None


def _display_groups(refresh: bool) -> list[dict[str, Any]]:
    global _displays
    now = time.monotonic()
    if _displays is None or refresh or now - _displays[0] > DISPLAYS_HELD_SECONDS:
        from common.support.collect.displays import about_groups

        _displays = (now, about_groups.groups())
    return _displays[1]


def details(refresh: bool = False) -> list[dict[str, Any]]:
    """Every group, in the order they are read and copied."""
    global _held
    if _held is None or refresh:
        browser = _browser_path()
        _held = [
            {"heading": "VPinFE", "facts": [
                (t("word.version"), _version()),
                (t("about.fact.build"), build_flavor()),
                (t("about.fact.release_target"), release_target()),
                (t("about.fact.features"), _features()),
                (t("about.fact.frontend_theme"), _active_theme()),
            ]},
            {"heading": t("about.heading.device"), "facts": [
                (t("about.fact.host"), _hostname()),
                (t("about.fact.operating_system"), operating_system()),
                (t("about.fact.architecture"), platform.machine() or t("word.unknown")),
                (t("about.fact.windowing_system"), windowing_system()),
                ("Python", platform.python_version()),
                (t("about.fact.graphics"), _graphics()),
            ]},
            {"heading": t("about.heading.browser"), "facts": [
                (t("word.name"), _browser_name(browser)),
                (t("word.version"), _browser_version(browser) or t("word.unknown")),
                (t("word.path"), PathValue(browser) if browser else t("about.not_configured")),
            ]},
            {"heading": t("about.heading.locations"), "facts": _locations()},
        ]
    return _held


def shown(refresh: bool = False) -> list[dict[str, Any]]:
    """What About draws and copies: `details` and then the displays, which the support
    package reads for itself and so are not in `details`."""
    return [*details(refresh), *_display_groups(refresh)]


def described(refresh: bool = False) -> list[dict[str, Any]]:
    """`details` as the wire carries it: each fact a `label` and `value`, `path` where
    the value is a path on disk, and `sub` where the label belongs to the fact above."""
    return [{"heading": one["heading"],
             "facts": [{"label": label, "value": str(value),
                        "path": isinstance(value, PathValue),
                        "sub": isinstance(label, SubLabel)}
                       for label, value in one["facts"]]}
            for one in shown(refresh)]


def as_text() -> str:
    """The same answer as something to paste. Plain text with no markup: it goes into a
    forum post, a chat message and an issue, and only one of those renders a table."""
    lines: list[str] = []
    for group in shown():
        lines.append(f"{group['heading']}:")
        width = max((len(label) for label, _ in group["facts"]), default=0)
        for label, value in group["facts"]:
            indent = "    " if isinstance(label, SubLabel) else "  "
            lines.append(f"{indent}{label.ljust(width)}  {value}".rstrip() if label
                         else f"  {value}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def _version() -> str:
    from common.vpinfe_version import get_version

    return str(get_version() or t("word.unknown"))


def operating_system() -> str:
    system = platform.system() or t("word.unknown")
    if system == "Darwin":
        return f"macOS {platform.mac_ver()[0] or platform.release()}"
    if system == "Windows":
        return f"Windows {platform.release()} ({platform.version()})"
    return f"{system} {platform.release()}".strip()


def _hostname() -> str:
    try:
        return socket.gethostname() or t("word.unknown")
    except OSError:
        return t("word.unknown")


def _install_context() -> dict[str, Any]:
    try:
        from common.online.app_updater import get_install_context

        return dict(get_install_context() or {})
    except Exception:  # noqa: BLE001 - a report is not worth taking the page down for
        logger.debug("Could not read the install context", exc_info=True)
        return {}


def build_flavor() -> str:
    """Source, slim, fat or a non-release build. The first thing anybody reading a
    report needs, because it decides what else is even installed."""
    found = _install_context()
    reason = found.get("reason")
    if reason == "source_build":
        return t("about.build.source")
    if reason == "non_release_build":
        return t("about.build.non_release")
    if found.get("slim") is True:
        return t("about.build.slim")
    if found.get("slim") is False:
        return t("about.build.full")
    return t("word.unknown")


def release_target() -> str:
    return str(_install_context().get("triplet") or t("word.unknown"))


def _features() -> str:
    from common.config.paths import get_ini_config
    from common.devices import install_identity

    try:
        found = install_identity.features(get_ini_config())
    except Exception:  # noqa: BLE001
        logger.debug("Could not read the enabled features", exc_info=True)
        return t("word.unknown")
    return ", ".join(t(install_identity.LABELS.get(name, name)) for name in found
                     if name != install_identity.CORE) or t("word.none")


def _active_theme() -> str:
    try:
        from common.online import theme_service

        return theme_service.get_active_theme() or t("word.unknown")
    except Exception:  # noqa: BLE001
        logger.debug("Could not read the active theme", exc_info=True)
        return t("word.unknown")


def windowing_system() -> str:
    """What is drawing the windows. On Linux it decides whether a screen grab works at
    all, so it is worth reporting rather than inferring from the OS."""
    system = platform.system()
    if system == "Windows":
        return "Windows"
    if system == "Darwin":
        return "Quartz"
    session = os.environ.get("XDG_SESSION_TYPE", "").strip().lower()
    if os.environ.get("WAYLAND_DISPLAY", "").strip() or session == "wayland":
        return "Wayland"
    if os.environ.get("DISPLAY", "").strip() or session == "x11":
        return "X11"
    return t("word.unknown")


def _graphics() -> str:
    """One line, because a report asks whether there is a card at all. Watching one is
    what the Metrics page is for."""
    from common.host import graphics

    names, reason = graphics.names()
    return ", ".join(names) or reason or t("about.not_reported")


def _browser_path() -> str:
    try:
        from common.devices import device_client

        return str(device_client.local().browser_path() or "")
    except Exception:  # noqa: BLE001 - an install with no frontend has no browser
        logger.debug("Could not read the browser path", exc_info=True)
        return ""


def _browser_name(path: str) -> str:
    if not path:
        return t("about.not_configured")
    lowered = path.lower()
    for fragment, name in (("msedge", "Microsoft Edge"), ("chromium", "Chromium"),
                           ("chrome", "Google Chrome"), ("firefox", "Firefox")):
        if fragment in lowered:
            return name
    return Path(path).name or t("word.unknown")


def _browser_version(path: str) -> str:
    """Asked of the browser itself. A path says which browser is configured; only
    running it says which build is there."""
    if not path:
        return ""
    if platform.system() == "Windows":
        return _windows_file_version(path)
    try:
        done = subprocess.run([path, "--version"], capture_output=True, text=True,
                              timeout=5, check=False, env=bundled_libraries.for_child())
    except Exception:  # noqa: BLE001 - a browser that will not answer is not a failure
        logger.debug("Could not ask %s for its version", path, exc_info=True)
        return ""
    return (done.stdout or done.stderr or "").strip()


def _windows_file_version(path: str) -> str:
    """`--version` writes to a terminal Chrome does not have on Windows, so the file's
    own version resource is the only thing that answers."""
    # The path travels in the environment, never in the script text: PowerShell reads curly
    # quotes as quotes, so no escaping of the path inside the text is enough.
    command = "(Get-Item -LiteralPath $env:VPINFE_FILE).VersionInfo.ProductVersion"
    try:
        done = subprocess.run(["powershell", "-NoProfile", "-Command", command],
                              capture_output=True, text=True, timeout=5, check=False,
                              env={**os.environ, "VPINFE_FILE": path})
    except Exception:  # noqa: BLE001
        logger.debug("Could not read the version of %s", path, exc_info=True)
        return ""
    return (done.stdout or "").strip()


def _locations() -> list[tuple[str, str]]:
    """Where this install keeps its things. In a report because half of what looks like
    a bug is VPinFE reading a different directory than somebody edited."""
    from common.config.paths import CONFIG_DIR, VPINFE_INI_PATH
    from common.runtime.log_setup import log_file

    found: list[tuple[str, str]] = [
        (t("about.fact.configuration"), PathValue(CONFIG_DIR)),
        (t("about.fact.settings_file"), PathValue(VPINFE_INI_PATH.with_suffix(".json")))]
    log = log_file()
    found.append((t("about.fact.log_file"), PathValue(log) if log else t("about.no_log")))
    found += [(t("about.fact.library_folder"), path) for path in _library_folders()]
    return found


def _library_folders() -> list[str]:
    from common.games import library_folders

    try:
        held: list[str] = [PathValue(one.path) for one in library_folders.configured()]
    except Exception:  # noqa: BLE001
        logger.debug("Could not read the library folders", exc_info=True)
        return [t("word.unknown")]
    return held or [t("about.not_set")]


def reset_for_tests() -> None:
    global _held, _displays
    _held = None
    _displays = None
