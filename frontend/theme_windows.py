"""Which windows a theme wants, and what each one is called.

Declaring nothing gets the three VPinFE has always opened, under the names the theme's
contract uses - which is how index_table.html keeps working with no fallback lookup.
Everything else about a window follows from its name.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Sequence
from pathlib import Path

from common.config.config_access import ConfigSource, DisplayConfig
from frontend import theme_api, theme_contract

logger = logging.getLogger("vpinfe.frontend.theme_windows")

MANIFEST_KEY = "windows"

# The contract whose window names CANONICAL translates from.
OLDEST_CONTRACT = 1

# The three VPinFE opens when a theme says nothing. The controller is first here and
# launched last, so it takes focus.
DEFAULT_WINDOWS = {
    1: ("table", "bg", "dmd"),
    2: ("playfield", "backglass", "scoreview"),
}

# Contract 1's window names, and what each is called now. The names come from Visual
# Pinball's plugin contract - Playfield, Backglass, ScoreView - which is where a window
# name should come from, since VPinFE is a front end for it. In VPX a DMD is a render
# style rather than a window; the window that shows a score is ScoreView.
#
# A contract 1 theme keeps its own spellings: it still gets `table`, `bg` and `dmd`
# windows and still loads index_bg.html. Only the canonical side moved.
CANONICAL = {"table": "playfield", "bg": "backglass", "dmd": "scoreview"}

# Opened for any theme once the cabinet has a screen for it. A theme's own index_topper.html
# is used when it ships one; theme_api.CORE_WINDOW_PAGES is the page otherwise.
TOPPER = "topper"

# The same for the backglass and score view, from contract 2. Contract 1 calls those windows
# `bg` and `dmd`, so a theme on it is never offered these.
CORE_DISPLAYS = ("backglass", "scoreview")


# Windows whose label is not just the capitalized name. The contract 1 spellings keep
# the titles they shipped with, so a cabinet's window rules keep matching them.
TITLES = {"bg": "BG", "dmd": "DMD", "scoreview": "Score View"}

# A window name reaches the bootstrap page from the URL and is written into its HTML, so
# it is checked rather than trusted. Theme window names are plain words by construction -
# they have to name a file.
_NAME = re.compile(r"[A-Za-z0-9_-]+")


def window_title(window: str) -> str | None:
    """The label for a window, or None when the name could not be one."""
    name = str(window or "").strip()
    if not _NAME.fullmatch(name):
        return None
    return TITLES.get(name.lower(), name.capitalize())


def canonical(window: str) -> str:
    name = str(window or "").strip()
    return CANONICAL.get(name, name)


def screen_key(window: str) -> str:
    """The name under which this window's monitor is looked up.

    Still `<window>screenid` rather than the `windows.<name>.screen_id` the file now
    uses, because it is a lookup token here and not a location - `window_screen_id`
    resolves it either way, and a window a theme invented has no schema entry to move.
    """
    return f"{canonical(window)}screenid"


# Themes already warned about, so a name is reported once rather than per window open.
_warned_foreign: set[tuple[str, tuple[str, ...]]] = set()


def _warn_on_foreign_names(theme_dir: str | Path, contract: int,
                           names: Iterable[str]) -> None:
    """A theme declaring window names from a contract it does not serve.

    The theme still gets what it asked for - the name decides its page, its monitor and
    its WebSocket identity, and canonical() keeps the monitor lookup right. What it does
    not get is anything keyed on the media vocabulary: `bg` is a media kind at contract 1
    and nothing at contract 2, so a window that asks for its own name finds no artwork and
    no video, silently. Worth saying out loud, because nothing else will.
    """
    if contract <= OLDEST_CONTRACT:
        return
    foreign = [name for name in names if name in CANONICAL]
    if not foreign:
        return
    key = (str(theme_dir), tuple(foreign))
    if key in _warned_foreign:
        return
    _warned_foreign.add(key)
    logger.warning(
        "Theme declares contract %s but names its windows %s, which belong to contract "
        "%s. Use %s instead - the windows open either way, but media keyed on the window "
        "name resolves to nothing under the names it was given.",
        contract, ", ".join(foreign), OLDEST_CONTRACT,
        ", ".join(CANONICAL[name] for name in foreign))


def declared_windows(theme_dir: str | Path | None, contract: int) -> tuple[str, ...]:
    """The windows this theme wants, controller first."""
    default = DEFAULT_WINDOWS.get(contract, DEFAULT_WINDOWS[1])
    if theme_dir is None:
        return default
    manifest = theme_api.read_manifest(theme_dir) or {}
    declared = manifest.get(MANIFEST_KEY)
    if isinstance(declared, list):
        names = tuple(str(name).strip() for name in declared if str(name).strip())
        if names:
            _warn_on_foreign_names(theme_dir, contract, names)
            return names
    return _windows_with_a_page(theme_dir, default)


def active(config: ConfigSource | None) -> tuple[str, ...]:
    """The windows to open, controller first: the active theme's, then the topper (and at
    contract 2 the backglass and score view) when the cabinet has a screen for it and the
    theme does not declare it."""
    theme_dir = (theme_api.resolve_theme_dir(theme_api.get_theme_name(config))
                 if config is not None else None)
    contract = theme_contract.declared_contract(theme_dir) if theme_dir else OLDEST_CONTRACT
    windows = declared_windows(theme_dir, contract)
    if config is None:
        return windows
    displays = DisplayConfig.from_config(config)
    candidates = (TOPPER, *(CORE_DISPLAYS if contract >= theme_contract.CURRENT_CONTRACT else ()))
    return (*windows, *(name for name in candidates if name not in windows
                        and displays.window_screen_id(screen_key(name)).strip()))


def own_windows(windows: Iterable[str]) -> tuple[str, ...]:
    """The ones beyond Playfield, Backglass and Score View, under any contract's name."""
    return tuple(name for name in windows
                 if canonical(name) not in CANONICAL.values() and window_title(name))


def _windows_with_a_page(theme_dir: str | Path,
                         default: tuple[str, ...]) -> tuple[str, ...]:
    """The default, minus any window this theme has no page for.

    Only the default is trimmed - a declared window opens whether its page exists or not.
    """
    present = tuple(name for name in default
                    if (Path(theme_dir) / f"index_{name}.html").is_file())
    return present or default


def controller(windows: Sequence[str]) -> str:
    """The window that owns input, audio and the selection."""
    return windows[0] if windows else ""


def launch_order(windows: Iterable[str]) -> list[str]:
    """Controller last, so it ends up with focus."""
    return list(reversed(list(windows)))
