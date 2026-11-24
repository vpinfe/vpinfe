"""The Tools registry: programs on this device that VPinFE runs but neither ships nor plays
tables with.

Each Tool is discovered first. Its setting is the override, for where discovery finds
nothing or picks the wrong one, and a setting whose program does not run is passed over
rather than trusted.

Every program is run as argv, never through a shell, with a timeout and nothing on stdin.
A probe only ever passes an argument that makes the program describe itself and exit,
except where the one question a reader asks is the test of whether it can run at all.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from common.i18n import t
from common.launcher_path import resolve_launcher_path

from . import bundled_libraries, get_ffmpeg, vpinos

logger = logging.getLogger("vpinfe.common.host.tools")

LINUX = "linux"
DARWIN = "darwin"
WINDOWS = "windows"
# A key in `Tool.hint` only: VPinOS is Linux everywhere else.
VPINOS = vpinos.ID

# Long enough for a first run the operating system scans before starting it.
TIMEOUT = 10

# `creationflags` for every program VPinFE starts.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class State(StrEnum):
    FOUND = "found"
    MISSING = "missing"
    UNUSABLE = "unusable"
    NOT_HERE = "not_here"


# Who can put it right: VPinFE itself, once a person agrees; a person; or nobody.
FIX_AUTO = "auto"
FIX_USER = "user"
FIX_NONE = "none"

TIMED_OUT = "tools.unusable.timed_out"
NO_SNAPSHOT = "tools.unusable.no_snapshot"
DID_NOT_START = "tools.unusable.did_not_start"
FAILED = "tools.unusable.failed"


@dataclass(frozen=True)
class Probe:
    works: bool
    # For a person to read. Nothing is gated on it.
    version: str = ""
    # What it can do, by kind: `can["encoders"]` holds the names of ffmpeg's encoders.
    can: Mapping[str, frozenset[str]] = field(default_factory=dict)
    # A catalog key, where it does not work.
    reason: str = ""

    def has(self, kind: str, name: str) -> bool:
        return name in self.can.get(kind, frozenset())


@dataclass(frozen=True)
class Tool:
    id: str
    option: str
    # Its product name, for a sentence. Not translated.
    name: str
    # Executables to look for, by platform, most wanted first. No entry: not here.
    names: Mapping[str, tuple[str, ...]]
    probe: Callable[[Path], Probe]
    # A catalog key by platform, taking `tool`, `setting` and `section`.
    hint: Mapping[str, str]
    # Folders of its own to look in beyond PATH, by platform. `${VAR}` is expanded.
    places: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    # What VPinFE would download in its place here, where it can get one itself, and the
    # hint that says so.
    offer: Callable[[], Mapping[str, Any] | None] | None = None
    get_hint: str = ""
    # Whether a card or a feature on this device needs it. `report` leaves an unneeded
    # one not here; `resolve` never asks, because a reader calls it from inside the
    # listing this answers from.
    needed: Callable[[], bool] | None = None

    @property
    def section(self) -> str:
        return self.option.split(".", 1)[0]

    @property
    def key(self) -> str:
        return self.option.split(".", 1)[1]


@dataclass(frozen=True)
class Found:
    tool: Tool
    state: State
    path: Path | None = None
    probe: Probe | None = None
    # From the setting, as against discovered.
    set_here: bool = False


def here() -> str:
    if sys.platform.startswith("win"):
        return WINDOWS
    if sys.platform == "darwin":
        return DARWIN
    return LINUX


# Where a program is installed that a frontend or a service started with a short PATH
# does not look.
_PLACES: dict[str, tuple[str, ...]] = {
    DARWIN: ("/opt/homebrew/bin", "/usr/local/bin"),
    LINUX: ("/usr/local/bin",),
}


def resolve(tool: Tool, configured: str | None = None) -> Found:
    """The program this device would run as `tool`. `configured` stands in for the
    setting's value."""
    names = tool.names.get(here(), ())
    if not names:
        return Found(tool, State.NOT_HERE)
    setting = (_configured(tool) if configured is None else configured).strip()
    refused: list[Found] = []
    if setting and (path := _executable(setting)) is not None:
        found = Found(tool, State.FOUND, path, probed(tool, path), set_here=True)
        if found.probe is not None and found.probe.works:
            return found
        refused.append(found)
    for path in _candidates(tool, names):
        found = Found(tool, State.FOUND, path, probed(tool, path))
        if found.probe is not None and found.probe.works:
            return found
        refused.append(found)
    if refused:
        first = refused[0]
        return Found(tool, State.UNUSABLE, first.path, first.probe, first.set_here)
    return Found(tool, State.MISSING)


def _configured(tool: Tool) -> str:
    from common.config.paths import get_ini_config

    return str(get_ini_config().value(tool.section, tool.key) or "")


def _executable(value: str) -> Path | None:
    found = shutil.which(str(resolve_launcher_path(value)))
    return Path(found) if found else None


def _candidates(tool: Tool, names: tuple[str, ...]) -> list[Path]:
    """Every copy of each name, on PATH and then in the places PATH misses. The order of
    `names` wins over where a copy is."""
    places = [os.path.expandvars(place) for place in
              (*tool.places.get(here(), ()), *_PLACES.get(here(), ()))]
    seen: set[str] = set()
    out: list[Path] = []
    for name in names:
        for where in (None, *places):
            found = shutil.which(name, path=where)
            if found and (real := os.path.realpath(found)) not in seen:
                seen.add(real)
                out.append(Path(found))
    return out


# By tool, path and the file's identity, so a program replaced in place is asked again.
# Only what worked is kept: a failure can be something around the file that gets fixed.
_probes: dict[tuple[str, str, int, int], Probe] = {}


def probed(tool: Tool, path: Path) -> Probe:
    try:
        held = path.stat()
    except OSError:
        return Probe(False, reason=DID_NOT_START)
    key = (tool.id, str(path), held.st_mtime_ns, held.st_size)
    if (known := _probes.get(key)) is not None:
        return known
    answer = tool.probe(path)
    if answer.works:
        _probes[key] = answer
    else:
        logger.info("%s at %s is not usable: %s", tool.name, path, answer.reason)
    return answer


class _FailedError(Exception):
    """It ran, and exited with something other than 0."""


def ask(path: Path, *args: str) -> str:
    """What it printed on both streams. Raises `_FailedError`, `OSError` where it cannot
    start and `subprocess.TimeoutExpired` where it does not finish."""
    done = subprocess.run(
        [str(path), *args], capture_output=True, text=True, errors="replace",
        stdin=subprocess.DEVNULL, timeout=TIMEOUT, check=False,
        creationflags=NO_WINDOW, env=bundled_libraries.for_child())
    if done.returncode != 0:
        raise _FailedError(done.returncode)
    return (done.stdout or "") + (done.stderr or "")


def probing(asks: Callable[[Path], Probe]) -> Callable[[Path], Probe]:
    """`asks` as a probe: what `ask` raises is an answer, not an exception."""
    def probe(path: Path) -> Probe:
        try:
            return asks(path)
        except subprocess.TimeoutExpired:
            return Probe(False, reason=TIMED_OUT)
        except OSError:
            return Probe(False, reason=DID_NOT_START)
        except _FailedError:
            return Probe(False, reason=FAILED)
    return probe


_VERSION = re.compile(r"\d+\.\d+(?:\.\d+)*")


def version_in(said: str) -> str:
    """The first dotted number on the first line that says anything, or ""."""
    first = next((line for line in said.splitlines() if line.strip()), "")
    found = _VERSION.search(first)
    return found.group(0) if found else ""


# --- RAR --------------------------------------------------------------------------

# By a word in the program's name, the rarfile backend it is: rarfile names each by the
# prefix of its `<PREFIX>_TOOL` and `<PREFIX>_CONFIG`. `7zz` before `7z`, which it holds.
_RAR_BACKENDS = (("unrar", "UNRAR"), ("unar", "UNAR"), ("7zz", "SEVENZIP2"),
                 ("7z", "SEVENZIP"), ("bsdtar", "BSDTAR"))


def rar_backend(path: Path) -> str:
    """Which rarfile backend a RAR tool is. A name it does not know is taken as unrar."""
    stem = path.stem.lower()
    return next((backend for word, backend in _RAR_BACKENDS if word in stem), "UNRAR")


@probing
def _rar(path: Path) -> Probe:
    """The check rarfile itself runs before it uses one."""
    try:
        import rarfile
    except ImportError:
        return Probe(False, reason=DID_NOT_START)
    check = getattr(rarfile, f"{rar_backend(path)}_CONFIG")["check_cmd"][1:]
    return Probe(True, version_in(ask(path, *check)))


_RAR_NAMES = ("unrar", "unar", "7z", "7zz", "bsdtar")

RAR = Tool(
    id="rar",
    option="tools.rar_path",
    name="unar",
    names={LINUX: _RAR_NAMES, DARWIN: _RAR_NAMES, WINDOWS: ("unrar", "7z")},
    probe=_rar,
    hint={LINUX: "tools.rar.hint.linux", VPINOS: "tools.hint.vpinos",
          DARWIN: "tools.rar.hint.darwin", WINDOWS: "tools.rar.hint.windows"},
    places={WINDOWS: ("${ProgramFiles}/WinRAR", "${ProgramFiles}/7-Zip")},
)


# --- recording ----------------------------------------------------------------------

ENCODERS = "encoders"
INPUTS = "inputs"
FILTERS = "filters"
OPTIONS = "options"

# ` V....D libx264   libx264 H.264 ...`: what it encodes, five flags, then the name.
_ENCODER = re.compile(r"^ [VAS][A-Z.]{5} +([\w-]+) ", re.MULTILINE)
# ` D  avfoundation   AVFoundation input device`: D where it can be read from.
_INPUT_DEVICE = re.compile(r"^ D[E. ] +(\w+) ", re.MULTILINE)
# ` .. ddagrab   |->V   Grab Windows Desktop images...`: two flags (three before FFmpeg 5),
# the name, then what it takes and gives.
_FILTER = re.compile(r"^ [.A-Z]{2,3} +(\w+) +[AVN|]*->[AVN|]*", re.MULTILINE)


@probing
def _ffmpeg(path: Path) -> Probe:
    version = version_in(ask(path, "-hide_banner", "-version"))
    encoders = frozenset(_ENCODER.findall(ask(path, "-hide_banner", "-encoders")))
    if not encoders:
        raise _FailedError(0)
    return Probe(True, version, {
        ENCODERS: encoders,
        INPUTS: frozenset(_INPUT_DEVICE.findall(ask(path, "-hide_banner", "-devices"))),
        FILTERS: frozenset(_FILTER.findall(ask(path, "-hide_banner", "-filters"))),
    })


# An option as a program's help lists it: `-o`, `--no-damage`.
_OPTION = re.compile(r"(?<![\w-])(--?[A-Za-z][\w-]*)")


def _described(help_flag: str) -> Callable[[Path], Probe]:
    """A probe that reads what a program can do off the options its help lists."""
    @probing
    def probe(path: Path) -> Probe:
        options = frozenset(_OPTION.findall(ask(path, help_flag)))
        if not options:
            raise _FailedError(0)
        version = version_in(ask(path, "--version")) if "--version" in options else ""
        return Probe(True, version, {OPTIONS: options})
    return probe


_ON_LINUX_ONLY = {LINUX: "tools.hint.linux", VPINOS: "tools.hint.vpinos"}

FFMPEG = Tool(
    id="ffmpeg",
    option="tools.ffmpeg_path",
    name="FFmpeg",
    names={LINUX: ("ffmpeg",), DARWIN: ("ffmpeg",), WINDOWS: ("ffmpeg",)},
    probe=_ffmpeg,
    hint={**_ON_LINUX_ONLY, DARWIN: "tools.ffmpeg.hint.darwin",
          WINDOWS: "tools.ffmpeg.hint.windows"},
    places={DARWIN: (str(get_ffmpeg.FOLDER),), WINDOWS: (str(get_ffmpeg.FOLDER),)},
    offer=get_ffmpeg.offer,
    get_hint="tools.ffmpeg.hint.get",
)

GRIM = Tool(
    id="grim",
    option="tools.grim_path",
    name="grim",
    names={LINUX: ("grim",)},
    probe=_described("-h"),
    hint=_ON_LINUX_ONLY,
)

WF_RECORDER = Tool(
    id="wf_recorder",
    option="tools.wf_recorder_path",
    name="wf-recorder",
    names={LINUX: ("wf-recorder",)},
    probe=_described("-h"),
    hint=_ON_LINUX_ONLY,
)

ELEMENTS = "elements"
# `pipewire:  pipewiresrc: PipeWire source`, as gst-inspect-1.0 lists every element.
_ELEMENT = re.compile(r"^[\w-]+:  ([\w-]+): ", re.MULTILINE)


@probing
def _gstreamer(path: Path) -> Probe:
    """gst-launch-1.0 says only its version, on the line after the one its own name, with
    its "1.0", is on; gst-inspect-1.0, from the same install, lists the elements."""
    version = next((version_in(line) for line in ask(path, "--version").splitlines()
                    if line.startswith("GStreamer ")), "")
    elements = frozenset(_ELEMENT.findall(ask(path.with_name("gst-inspect-1.0"))))
    if not elements:
        raise _FailedError(0)
    return Probe(True, version, {ELEMENTS: elements})


GSTREAMER = Tool(
    id="gstreamer",
    option="tools.gstreamer_path",
    name="GStreamer",
    names={LINUX: ("gst-launch-1.0",)},
    probe=_gstreamer,
    hint=_ON_LINUX_ONLY,
)


# --- pressing keys ------------------------------------------------------------------

COMMANDS = "commands"


def said(path: Path, *args: str) -> str:
    """What it printed on both streams, whatever it exited with. For a program whose only
    way to describe itself is to refuse what it was given."""
    done = subprocess.run(
        [str(path), *args], capture_output=True, text=True, errors="replace",
        stdin=subprocess.DEVNULL, timeout=TIMEOUT, check=False,
        creationflags=NO_WINDOW, env=bundled_libraries.for_child())
    return (done.stdout or "") + (done.stderr or "")


@probing
def _wtype(path: Path) -> Probe:
    """Run with nothing to type, it prints its usage and exits before reaching Wayland.
    Any argument at all would be typed or parsed as a key."""
    if "usage" not in said(path).lower():
        raise _FailedError(0)
    return Probe(True)


# `Available commands:` then one command a line, as ydotool's help lists them.
_COMMAND = re.compile(r"^\s{0,8}([a-z][a-z0-9_-]*)\s*$", re.MULTILINE)


@probing
def _ydotool(path: Path) -> Probe:
    """`help` is answered before ydotool reaches its service, so it lists the commands
    whether or not the service runs."""
    text = ask(path, "help")
    listed = text.split("Available commands:", 1)
    commands = frozenset(_COMMAND.findall(listed[1])) if len(listed) == 2 else frozenset()
    if "key" not in commands:
        raise _FailedError(0)
    return Probe(True, can={COMMANDS: commands})


WTYPE = Tool(
    id="wtype",
    option="tools.wtype_path",
    name="wtype",
    names={LINUX: ("wtype",)},
    probe=_wtype,
    hint=_ON_LINUX_ONLY,
)

YDOTOOL = Tool(
    id="ydotool",
    option="tools.ydotool_path",
    name="ydotool",
    names={LINUX: ("ydotool",)},
    probe=_ydotool,
    hint=_ON_LINUX_ONLY,
)

# --- reading graphics cards ---------------------------------------------------------

def _needs_nvidia_smi() -> bool:
    from common.host import graphics

    return graphics.needs_nvidia_smi()


def _needs_nvtop() -> bool:
    from common.host import graphics

    return graphics.needs_nvtop()


@probing
def _nvidia_smi(path: Path) -> Probe:
    """The one question the reader asks of it. A driver that is not loaded makes it exit
    with a failure rather than list nothing."""
    listed = [line for line in ask(path, "--query-gpu=driver_version",
                                   "--format=csv,noheader").splitlines() if line.strip()]
    if not listed:
        raise _FailedError(0)
    return Probe(True, version_in(listed[0]))


@probing
def _nvtop(path: Path) -> Probe:
    """`-s`, one snapshot, is what the reader runs. An nvtop without it is a different
    program than the one needed, whatever its version says."""
    options = frozenset(_OPTION.findall(ask(path, "-h")))
    if not options:
        raise _FailedError(0)
    if "-s" not in options:
        return Probe(False, reason=NO_SNAPSHOT)
    version = version_in(ask(path, "--version")) if "--version" in options else ""
    return Probe(True, version, {OPTIONS: options})


NVIDIA_SMI = Tool(
    id="nvidia_smi",
    option="tools.nvidia_smi_path",
    name="nvidia-smi",
    names={LINUX: ("nvidia-smi",), WINDOWS: ("nvidia-smi",)},
    probe=_nvidia_smi,
    hint={LINUX: "tools.nvidia_smi.hint", WINDOWS: "tools.nvidia_smi.hint"},
    places={WINDOWS: ("${ProgramFiles}/NVIDIA Corporation/NVSMI",)},
    needed=_needs_nvidia_smi,
)

NVTOP = Tool(
    id="nvtop",
    option="tools.nvtop_path",
    name="nvtop",
    names={LINUX: ("nvtop",)},
    probe=_nvtop,
    hint=_ON_LINUX_ONLY,
    needed=_needs_nvtop,
)

TOOLS: tuple[Tool, ...] = (RAR, FFMPEG, GRIM, WF_RECORDER, GSTREAMER, WTYPE, YDOTOOL,
                           NVIDIA_SMI, NVTOP)


# --- what a person reads ------------------------------------------------------------

def remedy(tool: Tool, missing: bool = False) -> dict[str, Any]:
    """The fix for a missing or unusable one, as a catalog key with its parameters, and
    the setting that points at one instead. `words` renders it. Where it is `missing` and
    VPinFE can get one here, `get` is what it would fetch."""
    offered = tool.offer() if missing and tool.offer is not None else None
    if offered:
        return {"key": tool.get_hint, "params": {"tool": tool.name},
                "setting": tool.option, "get": dict(offered)}
    key = tool.hint.get(VPINOS, "") if vpinos.detected() else ""
    return {"key": key or tool.hint.get(here(), ""), "params": {"tool": tool.name},
            "setting": tool.option}


def fix_of(said: Mapping[str, Any] | None) -> str:
    """Who can put a stuck Tool right, by its remedy."""
    return FIX_AUTO if said and said.get("get") else FIX_USER


def words(said: Mapping[str, Any]) -> str:
    """A remedy or a reason in this install's language."""
    setting = str(said.get("setting") or "")
    extra = {"setting": t(f"config.{setting}.label"),
             "section": t("manager.section.settings")} if setting else {}
    return t(str(said.get("key") or ""), **dict(said.get("params") or {}), **extra)


def hint(tool: Tool) -> str:
    return words(remedy(tool))


def row(found: Found) -> dict[str, Any]:
    """One Tool as the API and the capture preflight report it."""
    tool = found.tool
    stuck = found.state in (State.MISSING, State.UNUSABLE)
    said = remedy(tool, missing=True) if stuck else None
    reason = found.probe.reason if found.probe is not None else ""
    unset = resolve(tool, "") if found.set_here else found
    return {
        "id": tool.id,
        "name": tool.name,
        "setting": tool.option,
        "state": found.state.value,
        "path": str(found.path or ""),
        "version": found.probe.version if found.probe is not None else "",
        "set_here": found.set_here,
        "default_path": str(unset.path or ""),
        "default_version": unset.probe.version if unset.probe is not None else "",
        "reason": ({"key": reason, "params": {}}
                   if found.state is State.UNUSABLE and reason else None),
        "fix": fix_of(said) if stuck else FIX_NONE,
        "remedy": said,
    }


def report() -> list[dict[str, Any]]:
    return [row(resolve(tool) if _wanted(tool) else Found(tool, State.NOT_HERE))
            for tool in TOOLS]


def _wanted(tool: Tool) -> bool:
    return tool.needed is None or not tool.names.get(here()) or tool.needed()
