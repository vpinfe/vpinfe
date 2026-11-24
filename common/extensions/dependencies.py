"""The dependencies an extension lists in its manifest, and the ones VPinFE already carries.

A line is `name==version`: exact, because what runs on a cabinet is what the author
tested, and the name is written the way PyPI compares it, so `Paho_MQTT` and `paho-mqtt`
are one dependency.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from packaging.tags import Tag, sys_tags
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from common.config import paths

# The folder pure-Python dependencies go under, and the file that marks a dependency as fully
# unpacked: written last, so a folder without it is a leftover and is never put on the path.
PURE = "py3"
RECEIPT = "dependency-receipt.json"

# Every distribution the build carries, the dependencies of VPinFE's own requirements
# included, across the three systems. An extension cannot bring a second copy of one: a
# process holds one. `tests/invariants/test_shipped_dependencies_are_named.py` fails when a
# requirement or one of its dependencies is missing here.
SHIPPED = frozenset({
    "aiofiles", "aiohappyeyeballs", "aiohttp", "aiosignal", "annotated-doc", "annotated-types",
    "anyio", "attrs", "bidict", "brotli", "certifi", "charset-normalizer", "click", "colorama",
    "cython", "docutils", "evdev", "fastapi", "frozenlist", "h11", "httpcore", "httpx", "idna",
    "ifaddr", "inflate64", "itsdangerous", "jeepney", "jinja2", "markdown2", "markupsafe",
    "multidict", "multivolumefile", "nicegui", "olefile", "opentelemetry-api", "orjson",
    "packaging", "pillow", "platformdirs", "propcache", "psutil", "py7zr", "pybcj",
    "pycryptodomex", "pydantic", "pydantic-core", "pygments", "pynput", "pyobjc-core",
    "pyobjc-framework-applicationservices", "pyobjc-framework-cocoa",
    "pyobjc-framework-coretext", "pyobjc-framework-quartz", "pyppmd", "python-dotenv",
    "python-engineio", "python-multipart", "python-socketio", "python-xlib", "qrcode",
    "rarfile", "requests", "screeninfo", "simple-websocket", "six", "starlette", "texttable",
    "tinycss2", "typing-extensions", "typing-inspection", "urllib3", "uvicorn", "watchfiles",
    "webencodings", "websockets", "wsproto", "yarl", "zeroconf",
})

# The most one extension may list, and the longest line: a manifest is read before anyone
# has agreed to anything, so what it can ask for is bounded.
MOST = 40
LONGEST_LINE = 120

_LINE = re.compile(r"^([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)==([^\s=,;<>!~@]+)$")


@dataclass(frozen=True, order=True)
class Dependency:
    """One dependency at one version. `name` is the normalized form."""

    name: str
    version: str

    def __str__(self) -> str:
        return f"{self.name}=={self.version}"


class BadLineError(ValueError):
    """A line that is not `name==version`; the text is the line as written."""


def parse_line(text: str) -> Dependency:
    """The dependency a manifest line names. Raises BadLineError for anything but an exact pin."""
    line = text.strip()
    found = _LINE.match(line) if len(line) <= LONGEST_LINE else None
    if not found:
        raise BadLineError(line)
    try:
        version = str(Version(found.group(2)))
    except InvalidVersion:
        raise BadLineError(line) from None
    return Dependency(canonicalize_name(found.group(1)), version)


def folder_name(tag: Tag) -> str:
    """The folder a wheel built for `tag` goes under: `py3` for pure Python, so it outlives
    a change of Python, and the interpreter, ABI and platform otherwise."""
    if tag.abi == "none" and tag.platform == "any" and tag.interpreter.startswith("py"):
        return PURE
    middle = "" if tag.abi == tag.interpreter else f"{tag.abi}-"
    return f"{tag.interpreter}-{middle}{tag.platform}"


def device_folders() -> list[str]:
    """The folder names a wheel this device can run could be under, best first."""
    names = [PURE]
    for tag in sys_tags():
        name = folder_name(tag)
        if name not in names:
            names.append(name)
    return names


def folder_of(one: Dependency, root: Path | None = None) -> Path | None:
    """Where `one` is unpacked for this device, or None when it is not here, or is only here
    for a device that is not this one. The folder is what goes on the import path."""
    base = root if root is not None else paths.DEPENDENCIES_DIR
    for name in device_folders():
        found = base / name / f"{one.name}-{one.version}"
        if (found / RECEIPT).is_file():
            return found
    return None


def of(lines: Iterable[str]) -> list[Dependency]:
    """The dependencies a manifest's lines name. A manifest that was parsed holds only lines
    that read, so none is skipped."""
    return [parse_line(line) for line in lines]


def missing(wanted: Iterable[Dependency], root: Path | None = None) -> list[Dependency]:
    """The dependencies of `wanted` that are not unpacked for this device."""
    return [one for one in wanted if folder_of(one, root) is None]


def conflict(wanted: Iterable[Dependency], others: Mapping[str, Iterable[Dependency]],
             ) -> tuple[str, Dependency] | None:
    """The first extension in `others` that holds a dependency of `wanted` at another version, and
    that dependency as `others` has it. A process holds one copy of a dependency, so the two cannot
    both run."""
    asked = {one.name: one for one in wanted}
    for name, held in others.items():
        for one in held:
            if one.name in asked and one != asked[one.name]:
                return name, one
    return None


def put_on_path(folders: Iterable[Path]) -> None:
    """Add dependency folders to the END of the import path, once each: a dependency can never
    shadow the standard library or VPinFE's own modules, whatever it is named."""
    for folder in folders:
        text = str(folder)
        if text not in sys.path:
            sys.path.append(text)
