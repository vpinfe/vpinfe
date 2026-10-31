"""Visual Pinball launched to have its screens recorded.

What a recording changes goes into copies in the folder core hands over, never into a file
Visual Pinball keeps: it writes its settings back when it exits.
"""

from __future__ import annotations

import hashlib
import os
import re
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from common.apps.contract import VIEW_FSS, Entry

from . import ini as vini
from .config import read_at_table, read_file, settings_file, table_layer
from .launch import VPXLaunch
from .tablefile import fss_enabled

# In the program's own values: SyncMode 0 is No Sync, MaxFramerate -1 follows the display.
PACING = {"Player.SyncMode": "0", "Player.MaxFramerate": "-1"}
MUTED = {"Player.PlaySound": "0", "Player.PlayMusic": "0",
         "Player.SoundVolume": "0", "Player.MusicVolume": "0"}
# Player.BGSet 0 is the view the table is built for: Full Single Screen where it enables
# that, else Desktop.
FSS_VIEW = {"Player.BGSet": "0"}

SETTINGS_COPY = "VPinballX.ini"
TABLE_COPY = "table.ini"

# Each window's display, and its Output Mode where it has one: the playfield always shows.
DISPLAYS = {"playfield": ("Player.PlayfieldDisplay", ""),
            "backglass": ("Backglass.BackglassDisplay", "Backglass.BackglassOutput"),
            "scoreview": ("ScoreView.ScoreViewDisplay", "ScoreView.ScoreViewOutput"),
            "topper": ("Topper.TopperDisplay", "Topper.TopperOutput")}
# Output Mode 1, a window of its own. Unset and 0 are Disabled; 2 is inside the playfield.
FLOATING = "1"
TITLES = {"Visual Pinball Player": "playfield", "Visual Pinball Backglass": "backglass",
          "Visual Pinball Score View": "scoreview", "Visual Pinball Topper": "topper"}

# `Samsung Electric Company SAMSUNG 0x00000001 (DP-1 via HDMI)`.
_CONNECTOR = re.compile(r"\(\s*([^\s()]+)[^()]*\)\s*$")

# Beside a table, by its name or its folder's: VPX's settings for it, B2S's backglass
# (`B2SServer.cpp`), ScoreView's layouts (`ScoreViewPlugin.cpp`) and Nixie's.
BESIDE = (".ini", ".directb2s", ".scv", ".nix")

_LAUNCH = VPXLaunch()

# The Settings File's digest by path, with the size and time it was read at.
_DIGESTS: dict[str, tuple[int, int, str]] = {}
_DIGESTS_LOCK = threading.Lock()


def recording_values(sound: bool, view: str = "") -> dict[str, str]:
    values = dict(PACING) if sound else {**PACING, **MUTED}
    return {**values, **FSS_VIEW} if view == VIEW_FSS else values


def _stamp(path: Path) -> str:
    try:
        found = path.stat()
    except OSError:
        return f"{path}:-"
    return f"{path}:{found.st_size}:{found.st_mtime_ns}"


def _content(path: Path | None) -> str:
    if path is None:
        return "-"
    try:
        found = path.stat()
        with _DIGESTS_LOCK:
            held = _DIGESTS.get(str(path))
            if held is None or held[:2] != (found.st_size, found.st_mtime_ns):
                held = (found.st_size, found.st_mtime_ns,
                        hashlib.sha256(path.read_bytes()).hexdigest())
                _DIGESTS[str(path)] = held
    except OSError:
        return f"{path}:-"
    return f"{path}:{held[2]}"


def _beside(table: Path) -> list[str]:
    wanted = {f"{stem}{suffix}".lower() for stem in (table.stem, table.parent.name)
              for suffix in BESIDE}
    try:
        names = os.listdir(table.parent)
    except OSError:
        return []
    return sorted(_stamp(table.parent / name) for name in names if name.lower() in wanted)


def connector(display: str) -> str:
    """The output a display name names in the parentheses it ends with, or ""."""
    found = _CONNECTOR.search(str(display or ""))
    return found.group(1) if found else ""


class VPXCapture:
    def command(self, entry: Entry, settings: Mapping[str, Any], *, sound: bool,
                view: str, folder: str) -> list[str]:
        """The launch command with `-ini` naming a copy of the Settings File, and
        `-tableini` a copy of the table's own file where that sets one of the recording's
        values, since the table's would win over the Settings File's."""
        values = recording_values(sound, view)
        copy = Path(folder) / SETTINGS_COPY
        copy.write_text(vini.written(read_file(settings_file(settings)), values),
                        encoding="utf-8")
        cmd = _LAUNCH.command(entry, {**settings, "ini_path": str(copy)})

        table = read_file(table_layer(entry.table))
        if any(table.value(key) is not None for key in values):
            table_copy = Path(folder) / TABLE_COPY
            table_copy.write_text(vini.written(table, values), encoding="utf-8")
            cmd[-2:-2] = ["-tableini", str(table_copy)]
        return cmd

    def views(self, entry: Entry, settings: Mapping[str, Any]) -> frozenset[str]:
        return frozenset({VIEW_FSS}) if entry.table and fss_enabled(entry.table) else frozenset()

    def outputs(self, entry: Entry, settings: Mapping[str, Any]) -> dict[str, str]:
        app = read_file(settings_file(settings))
        table = read_file(table_layer(entry.table))

        def value(key: str) -> str:
            said = table.value(key) if read_at_table(key) else None
            return (app.value(key) or "" if said is None else said).strip()

        found: dict[str, str] = {}
        for window, (display, mode) in DISPLAYS.items():
            if mode and value(mode) != FLOATING:
                found[window] = ""
            elif name := connector(value(display)):
                found[window] = name
        return found

    def window(self, app_id: str, title: str) -> str:
        return TITLES.get(str(title or "").strip(), "")

    def titles(self) -> Mapping[str, str]:
        return dict(TITLES)

    def placed_by(self, entry: Entry, settings: Mapping[str, Any]) -> str:
        program = str(settings.get("bin_path") or "").strip()
        said = [_content(settings_file(settings)), _stamp(Path(program)) if program else "-"]
        if entry.table:
            table = Path(entry.table)
            said += [_stamp(table), *_beside(table)]
        return hashlib.sha256("\n".join(said).encode("utf-8")).hexdigest()
