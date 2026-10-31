"""Launchers And Programs: what each launcher runs, the programs VPinFE runs itself, and the
libraries it ships.

Nothing here runs Visual Pinball. A launcher's program is identified by what a person could
read without starting it: the Rev its settings file recorded, the file's size and date, the
bundle's `Info.plist` on macOS and the file's version resource on Windows.
"""

from __future__ import annotations

import os
import platform
import plistlib
from datetime import UTC, datetime
from pathlib import Path

from apps.vpx import build as vpx_build
from apps.vpx import config as vpx_config
from common.games import launchers
from common.host import about, dof_service, libdmdutil_service, pinmame_catalog, tools
from common.runtime import log_setup
from common.support.sections import Section, SectionText

PLUGIN_PREFIX = "Plugin."
WINDOWS_PROGRAMS = (".exe", ".bat", ".cmd", ".com")
LAUNCHES_SCANNED = 50
_STATES = {tools.State.FOUND.value: "Found", tools.State.MISSING.value: "Not installed",
           tools.State.UNUSABLE.value: "Not usable"}


def collect() -> SectionText:
    store = launchers.get_launcher_store()
    held = store.launchers()
    if not held:
        return SectionText.ok(["No launchers."] + _programs() + _libraries(None)
                              + _last_launch())
    default = launchers.default_launcher("vpx")
    own = _tables_each(store.mappings())
    lines: list[str] = []
    for one in held:
        is_default = default is not None and one.launcher_id == default.launcher_id
        lines += _launcher(one, is_default, own.get(one.launcher_id, 0)) + [""]
    return SectionText.ok(lines + _programs() + _libraries(default) + _last_launch())


def _tables_each(mappings: dict[str, str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for launcher_id in mappings.values():
        counts[launcher_id] = counts.get(launcher_id, 0) + 1
    return counts


def _launcher(one: launchers.Launcher, is_default: bool, tables: int) -> list[str]:
    lines = [f"### {one.display_name or one.launcher_id}", "",
             f"- **App:** {one.app or 'Not set'}",
             f"- **Enabled:** {_yes(one.enabled)}",
             f"- **Default:** {_yes(is_default)}",
             f"- **Tables set to this launcher:** {tables}"]
    program = _exe(one)
    lines.append(f"- **Program:** {program or 'Not set'}"
                 + (f" ({_file_facts(Path(program))})" if program else ""))
    if program and Path(program).is_file():
        version = _program_version(Path(program))
        if version:
            lines.append(f"- **Program version:** {version}")
    if one.app == "vpx":
        lines += _settings_file(one)
    return lines


def _exe(one: launchers.Launcher) -> str:
    field = next((each for each in one.fields() if each.path == "exe"), None)
    return str(one.in_effect(field.key) or "").strip() if field is not None else ""


def _yes(value: bool) -> str:
    return "Yes" if value else "No"


def _file_facts(path: Path) -> str:
    """Whether the file is there, and the three things about it that need no running."""
    try:
        stat = path.stat()
    except OSError:
        return "Not found"
    if not path.is_file():
        return "Not a file"
    kind = "Executable" if _executable(path) else "Not executable"
    size = f"{stat.st_size / 1_048_576:.1f} MB"
    when = datetime.fromtimestamp(stat.st_mtime, UTC).strftime("%Y-%m-%d")
    return f"{kind}, {size}, modified {when}"


def _executable(path: Path) -> bool:
    """On Windows every file passes `os.access`, so the extension is what says."""
    if _system() == "Windows":
        return path.suffix.lower() in WINDOWS_PROGRAMS
    return os.access(path, os.X_OK)


def _system() -> str:
    return platform.system()


def _program_version(path: Path) -> str:
    system = _system()
    if system == "Darwin":
        return _bundle_version(path)
    if system == "Windows":
        said = about._windows_file_version(str(path))
        return f"{said} (file version)" if said else ""
    return ""


def _bundle_version(path: Path) -> str:
    bundle = next((one for one in (path, *path.parents) if one.suffix.lower() == ".app"), None)
    if bundle is None:
        return ""
    try:
        with (bundle / "Contents" / "Info.plist").open("rb") as handle:
            info = plistlib.load(handle)
    except (OSError, ValueError, plistlib.InvalidFileException):
        return ""
    if not isinstance(info, dict):
        return ""
    short = str(info.get("CFBundleShortVersionString") or "").strip()
    build = str(info.get("CFBundleVersion") or "").strip()
    if short and build and build != short:
        return f"{short} (build {build})"
    return short or build


def _settings_file(one: launchers.Launcher) -> list[str]:
    path = vpx_config.settings_file({"ini_path": one.value("ini_path"),
                                     "bin_path": one.value("bin_path")})
    if path is None:
        return ["- **Settings file:** Not set"]
    if not path.is_file():
        return [f"- **Settings file:** {path} (Not found)"]
    if not os.access(path, os.R_OK):
        return [f"- **Settings file:** {path} (Not readable)"]
    found = vpx_config.read_file(path)
    lines = [f"- **Settings file:** {path} (Readable)"]
    said = found.value(vpx_config.BUILD_KEY)
    rev = vpx_build.rev_of(said)
    if said:
        lines.append(f"- **Settings file records:** {said}" + (f", Rev {rev}" if rev else ""))
    else:
        lines.append("- **Settings file records:** No version")
    plugins = sorted({each.section[len(PLUGIN_PREFIX):] for each in found.settings.values()
                      if each.section.startswith(PLUGIN_PREFIX)})
    lines.append(f"- **Plugins in the settings file:** {', '.join(plugins) or 'None'}")
    return lines


def _programs() -> list[str]:
    lines = ["### Programs VPinFE Runs", ""]
    shown = False
    for row in tools.report():
        state = str(row["state"])
        if state not in _STATES:
            continue
        shown = True
        detail = [str(row["path"])] if row["path"] else []
        if row["version"]:
            detail.append(str(row["version"]))
        lines.append(f"- **{row['name']}:** {_STATES[state]}"
                     + (f" ({', '.join(detail)})" if detail else ""))
    return lines if shown else lines + ["None needed on this device."]


def _libraries(default: launchers.Launcher | None) -> list[str]:
    program = str(default.in_effect("bin_path") or "") if default is not None else ""
    return ["", "### Bundled Libraries", "",
            f"- **DOF:** {_library(dof_service.find_dof_file(*dof_service._RUNNER_NAMES))}",
            "- **libdmdutil:** " + _library(libdmdutil_service.find_libdmdutil_file(
                *libdmdutil_service._WRAPPER_NAMES)),
            f"- **PinMAME:** {_library(pinmame_catalog.find_library(program))}"]


def _library(path: Path | None) -> str:
    if path is None:
        return "Not present"
    try:
        when = datetime.fromtimestamp(path.stat().st_mtime, UTC).strftime("%Y-%m-%d")
    except OSError:
        return "Present"
    return f"Present, modified {when}"


def _last_launch() -> list[str]:
    """The command VPinFE logged when it last started a game, as logged. The environment a
    game starts in is not part of that line."""
    heading = ["", "### Last Launch", ""]
    path = log_setup.log_file()
    if path is None:
        return heading + ["VPinFE is not writing a log file on this run."]
    found = [record for record in log_setup.read_records(path, LAUNCHES_SCANNED,
                                                        contains="Launching:")
             if str(record["message"]).startswith("Launching: ")]
    if not found:
        return heading + ["No game launched in this run's log."]
    last = found[-1]
    return heading + [f"- **When:** {last['when']}",
                      f"- **Command:** {str(last['message'])[len('Launching: '):]}"]


SECTION = Section("launchers", "Launchers And Programs", 50, collect)
