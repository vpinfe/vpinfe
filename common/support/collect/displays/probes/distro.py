"""The appliance Linuxes and Game Mode: SteamOS, VPinOS, and gamescope showing one screen
to every window."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

from common.host import vpinos

from ..model import OK, Reading
from ..run import Run
from . import version_in

SOURCE = "os-release"
STEAMOS = "steamos"
APPLIANCES = (STEAMOS, vpinos.ID)

GAME_MODE = "Game Mode: gamescope shows one screen to every window"


def _read(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def parse_os_release(text: str) -> dict[str, str]:
    found = {}
    for line in text.splitlines():
        key, _, value = line.partition("=")
        if key.strip():
            found[key.strip()] = value.strip().strip('"').strip("'")
    return found


def probe(env: Mapping[str, str], run: Run,
          read: Callable[[str], str] = _read) -> Reading:
    said = parse_os_release(read("/etc/os-release"))
    ident = said.get("ID", "").lower()
    facts: list[tuple[str, str]] = []
    if ident in APPLIANCES:
        facts.append(("Distribution", said.get("NAME", ident)))
        for label, key in (("Version", "VERSION_ID"), ("Build", "BUILD_ID")):
            if said.get(key):
                facts.append((label, said[key]))
    if ident == STEAMOS:
        branch = run(["steamos-select-branch", "-c"])
        if branch.ok and branch.text.strip():
            facts.append(("Update channel", branch.text.strip().splitlines()[0]))
    notes: tuple[str, ...] = ()
    if env.get("GAMESCOPE_WAYLAND_DISPLAY"):
        notes = (GAME_MODE,)
        version = run(["gamescope", "--version"])
        if version.ok and version_in(version.text):
            facts.append(("Gamescope", version_in(version.text)))
    if not facts and not notes:
        return Reading.failed(SOURCE, "os-release names neither an appliance nor Game Mode")
    return Reading(SOURCE, OK, facts=tuple(facts), notes=notes)

