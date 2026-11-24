"""Window rules for a Sway or Hyprland session: each window sent to its screen.

Those compositors place a window by rule, so VPinFE keeps a file of rules for its own
windows and Visual Pinball's, and the person's config includes it with one line. GNOME and
KDE on Wayland have no such rule, so there is nothing to write for them.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from common.atomic_write import write_atomic
from common.config.paths import CONFIG_DIR
from common.host import tools
from common.i18n import t

SWAY = "sway"
HYPRLAND = "hyprland"
# A Wayland desktop with nothing to write a rule in.
GNOME = "gnome"
KDE = "kde"

# Chromium on Wayland names a window's app_id from the page it opens, `/app/<window>`,
# and ignores `--class`. The host and the port in front of it vary.
_APP_ID = r"^chrom(e|ium)-.*__app_{}-Default$"
PLAYFIELD = "playfield"

# The compositors' own syntax for reading another file, which the person copies as it is.
_INCLUDE = {SWAY: "include {}", HYPRLAND: "source = {}"}


@dataclass(frozen=True)
class Row:
    """A window of VPinFE's, by the name its URL carries, and the output it goes to ("" for
    none). `role` is the part it plays: playfield, backglass, scoreview or topper."""

    window: str
    role: str
    output: str


@dataclass(frozen=True)
class AppWindow:
    """A window of Visual Pinball's, by its title."""

    title: str
    role: str


def rules_path(kind: str, folder: Path | None = None) -> Path:
    return (CONFIG_DIR if folder is None else folder) / f"window-rules-{kind}.conf"


def include_line(kind: str, path: Path) -> str:
    quoted = f'"{path}"' if kind == SWAY and re.search(r"\s", str(path)) else str(path)
    return _INCLUDE[kind].format(quoted)


def session(env: Mapping[str, str], system: str | None = None) -> str:
    """The kind of Wayland session this is, or "" for anything the rules cannot help:
    X11, Windows, macOS, and VPinOS, which ships the line itself."""
    system = tools.here() if system is None else system
    if system != tools.LINUX or not env.get("WAYLAND_DISPLAY"):
        return ""
    if env.get("SWAYSOCK"):
        return SWAY
    if env.get("HYPRLAND_INSTANCE_SIGNATURE"):
        return HYPRLAND
    desktop = str(env.get("XDG_CURRENT_DESKTOP") or "").upper().split(":")
    if "KDE" in desktop:
        return KDE
    return GNOME if "GNOME" in desktop else ""


def _arg(text: str) -> str:
    return f'"{text}"' if re.search(r"[\s\"]", text) else text


def _anchored(rows: Sequence[Row]) -> str:
    names = [row.window for row in rows]
    return _APP_ID.format(names[0] if len(names) == 1 else f"({'|'.join(names)})")


def _titles(vpx: Sequence[AppWindow], *roles: str) -> str:
    named = [re.escape(one.title) for one in vpx if not roles or one.role in roles]
    return f"^{named[0]}$" if len(named) == 1 else f"^({'|'.join(named)})$"


HEADER = (
    "# Window rules for VPinFE's windows and Visual Pinball's.",
    "# VPinFE writes this file and replaces it when the screens change, so edits are lost.",
    "# To change the screen a window is on, use Settings > Hardware > Displays in VPinFE.",
)


def rules(kind: str, rows: Sequence[Row], vpx: Sequence[AppWindow]) -> str:
    """The file's text. A window with no screen is left where the desktop puts it, and
    still gets its border and focus rules."""
    placed = [one for one in rows if one.output]
    others = [one for one in vpx if one.role != PLAYFIELD]
    lines = list(HEADER)
    if kind == SWAY:
        for row in placed:
            lines.append(f'assign [app_id="{_anchored([row])}"] output {_arg(row.output)}')
        for one in vpx:
            home = next((row.output for row in placed if row.role == one.role), "")
            if home:
                lines.append(f'assign [title="{_titles([one])}"] output {_arg(home)}')
        if rows:
            lines.append(f'for_window [app_id="{_anchored(rows)}"] border none')
        if vpx:
            lines.append(f'for_window [title="{_titles(vpx)}"] border none')
        for row in rows:
            if row.role == PLAYFIELD:
                lines.append(f'for_window [app_id="{_anchored([row])}"] focus')
        for one in vpx:
            if one.role == PLAYFIELD:
                lines.append(f'for_window [title="{_titles([one])}"] focus')
        if others:
            lines.append(f'no_focus [title="{_titles(others)}"]')
        return "\n".join(lines) + "\n"
    for row in placed:
        lines.append(f"windowrulev2 = monitor {row.output}, class:{_anchored([row])}")
    for one in vpx:
        home = next((row.output for row in placed if row.role == one.role), "")
        if home:
            lines.append(f"windowrulev2 = monitor {home}, title:{_titles([one])}")
    if rows:
        lines.append(f"windowrulev2 = noborder, class:{_anchored(rows)}")
    if vpx:
        lines.append(f"windowrulev2 = noborder, title:{_titles(vpx)}")
    if others:
        lines.append(f"windowrulev2 = noinitialfocus, title:{_titles(others)}")
    return "\n".join(lines) + "\n"


def personal_config(kind: str, env: Mapping[str, str], home: Path | None = None) -> Path | None:
    """The config the compositor loads, when it can be added to. On Sway a config of one's
    own replaces the system's, so one is never made for the person. The first that exists
    is the one loaded, so a read-only one means the person adds the line."""
    base = home or Path.home()
    root = Path(env.get("XDG_CONFIG_HOME") or base / ".config")
    candidates = ([base / ".sway" / "config", root / "sway" / "config"]
                  if kind == SWAY else [root / "hypr" / "hyprland.conf"])
    first = next((one for one in candidates if one.is_file()), None)
    return first if first is not None and os.access(first, os.W_OK) else None


def is_added(config: Path | None, kind: str, rules_file: Path) -> bool:
    """Whether the config has a live line that includes `rules_file`."""
    if config is None:
        return False
    try:
        text = config.read_bytes().decode("utf-8", errors="replace")
    except OSError:
        return False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#"):
            continue
        said = line.split(None, 1)
        if kind == SWAY and len(said) == 2 and said[0] == "include":
            target = said[1].strip().strip('"').strip("'")
        elif kind == HYPRLAND and line.startswith("source"):
            target = line.partition("=")[2].partition(" #")[0].strip()
        else:
            continue
        if Path(os.path.expandvars(os.path.expanduser(target))) == rules_file:
            return True
    return False


@dataclass(frozen=True)
class Offer:
    kind: str
    rules: Path | None = None
    line: str = ""
    # Where Add it would put the line: None when only the person can.
    config: Path | None = None
    added: bool = False


def offer(env: Mapping[str, str], system: str | None = None,
          home: Path | None = None, folder: Path | None = None) -> Offer | None:
    """What Setup has to say about window placement here, or None where it has nothing.
    VPinOS has no offer: the line ships in its own compositor config."""
    kind = session(env, system)
    if not kind:
        return None
    if kind not in (SWAY, HYPRLAND):
        return Offer(kind)
    path = rules_path(kind, folder)
    config = personal_config(kind, env, home)
    return Offer(kind, path, include_line(kind, path), config, is_added(config, kind, path))


def write_rules(kind: str, text: str, folder: Path | None = None) -> Path:
    path = rules_path(kind, folder)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(path, lambda handle: handle.write(text))
    return path


def add(found: Offer, text: str, folder: Path | None = None) -> None:
    """Write the rules, then the one line into the person's config unless it is already
    there, checked now and not as the page last drew it. Raises OSError, and leaves the
    config as it was when the rules could not be written."""
    if found.config is None or found.rules is None:
        raise OSError(t("host.placement.no_config"))
    write_rules(found.kind, text, folder or found.rules.parent)
    if is_added(found.config, found.kind, found.rules):
        return
    held = found.config.read_bytes()
    gap = "\n" if held and not held.endswith(b"\n") else ""
    with open(found.config, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(f"{gap}\n# VPinFE window rules\n{found.line}\n")
