"""VPinFE and This Device: what this install is, and what it runs on.

The facts are About's own groups, so the page and the package cannot say different things.
"""

from __future__ import annotations

import os
from datetime import datetime
from importlib import metadata
from typing import Any

from common import i18n
from common.host import about
from common.support.sections import Section, SectionText

# A version here is a library this install runs on, so the list is short and named; it is
# never the whole of site-packages.
LIBRARIES = ("nicegui", "fastapi", "screeninfo", "pywebview")


def fact_lines(group: dict[str, Any]) -> list[str]:
    return [f"- **{label}:** {value}" for label, value in group["facts"]]


def _group(index: int) -> dict[str, Any]:
    return about.details()[index]


def vpinfe() -> SectionText:
    return SectionText.ok(fact_lines(_group(0)))


def device() -> SectionText:
    lines = fact_lines(_group(1))
    lines += [f"- **Interface language:** {i18n.language()}",
              f"- **UTC offset:** {_utc_offset()}",
              f"- **Processors:** {os.cpu_count() or 'Not reported'}",
              f"- **Memory:** {_memory()}"]
    lines += [f"- **{name}:** {version}" for name, version in _libraries()]
    return SectionText.ok(lines)


def _utc_offset() -> str:
    offset = datetime.now().astimezone().utcoffset()
    if offset is None:
        return "Not reported"
    minutes = int(offset.total_seconds() // 60)
    sign = "+" if minutes >= 0 else "-"
    return f"{sign}{abs(minutes) // 60:02d}:{abs(minutes) % 60:02d}"


def _memory() -> str:
    try:
        import psutil

        total = psutil.virtual_memory().total
    except Exception:  # noqa: BLE001 - a device that will not say is reported as such
        return "Not reported"
    return f"{round(total / 2**30, 1)} GB"


def _libraries() -> list[tuple[str, str]]:
    found = []
    for name in LIBRARIES:
        try:
            found.append((name, metadata.version(name)))
        except metadata.PackageNotFoundError:
            continue
    return found


SECTIONS = (
    Section("vpinfe", "VPinFE", 10, vpinfe),
    Section("device", "This Device", 20, device),
)
