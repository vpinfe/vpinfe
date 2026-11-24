"""The plugins an installed Visual Pinball has, as each one's `plugin.cfg` names it."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import ini as vini


@dataclass(frozen=True)
class Plugin:
    id: str
    name: str = ""
    description: str = ""


def folder(bin_path: str) -> Path | None:
    """Where the program loads plugins from: inside the bundle on macOS, beside the
    program elsewhere. None where no program is named."""
    program = Path(str(bin_path or "").strip())
    if not program.name:
        return None
    for candidate in (program, *program.parents):
        if candidate.suffix.lower() == ".app":
            return candidate / "Contents" / "PlugIns"
    return program.parent / "plugins"


def installed(bin_path: str) -> dict[str, Plugin] | None:
    """Every plugin in the program's folder, by id, or None where there is no folder to
    read. A folder whose `plugin.cfg` names no id is not a plugin to the program."""
    where = _plugins_folder(bin_path)
    if where is None:
        return None
    found: dict[str, Plugin] = {}
    for manifest in sorted(where.glob("*/plugin.cfg")):
        one = _read(manifest)
        if one is not None:
            found.setdefault(one.id, one)
    return found


def _plugins_folder(bin_path: str) -> Path | None:
    """The program's plugins folder where there is one, looked for beside the path given
    and then beside where a link at that path leads."""
    for where in (folder(bin_path), folder(_resolved(bin_path))):
        if where is not None and where.is_dir():
            return where
    return None


def _resolved(bin_path: str) -> str:
    given = str(bin_path or "").strip()
    try:
        return str(Path(given).resolve()) if given else ""
    except OSError:
        return given


def present(bin_path: str, *, predates_plugins: bool = False) -> frozenset[str] | None:
    """The ids of the plugins the program loads, or None where they cannot be read. A
    program with no plugins folder has none only when its build is known to predate
    plugins; otherwise the folder may be somewhere this path does not lead to, as with a
    wrapper script."""
    found = installed(bin_path)
    if found is not None:
        return frozenset(found)
    program = Path(str(bin_path or "").strip())
    return frozenset() if predates_plugins and program.name and program.exists() else None


def _read(manifest: Path) -> Plugin | None:
    try:
        said = vini.parse(manifest.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None

    def given(key: str) -> str:
        return (said.value(f"configuration.{key}") or "").strip().strip('"')

    return Plugin(given("id"), given("name"), given("description")) if given("id") else None
