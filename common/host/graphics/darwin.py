"""macOS: the accelerators in the I/O registry, which list the cards and read them in one
call. No program to install and no root.

`ioreg -a` prints a property list, which is a format rather than a layout that moves
between releases. Apple silicon has `Device Utilization %` and the memory the GPU has in
use out of the system's; a discrete AMD card on an Intel Mac adds VRAM, temperature, fan
and power where its driver reports them.
"""

from __future__ import annotations

import plistlib
import shutil
from typing import Any

from common.failures import why
from common.host.graphics import (
    UnreadableError,
    number,
    only_present,
    run,
    vendor_from_id,
    vendor_from_name,
)
from common.i18n import t

MEGABYTE = 1024 * 1024


def cards() -> list[dict[str, Any]]:
    program = shutil.which("ioreg") or "/usr/sbin/ioreg"
    text = run(program, ["-r", "-c", "IOAccelerator", "-a"], "ioreg")
    try:
        entries = plistlib.loads(text.encode())
    except Exception as exc:  # noqa: BLE001 - whatever the parser raises, the text was not a plist
        raise UnreadableError(t("error.graphics.tool_unreadable", tool="ioreg"), why(exc)) from exc
    if not isinstance(entries, list):
        raise UnreadableError(t("error.graphics.tool_unusable", tool="ioreg"))
    return [_card(entry, index) for index, entry in enumerate(entries, start=1)
            if isinstance(entry, dict)]


def _card(entry: dict[str, Any], index: int) -> dict[str, Any]:
    name = str(entry.get("model") or entry.get("IOClass") or t("word.unknown"))
    stats = entry.get("PerformanceStatistics")
    stats = stats if isinstance(stats, dict) else {}
    vendor = _vendor(entry, name)

    used, free = number(stats.get("vramUsedBytes")), number(stats.get("vramFreeBytes"))
    used_bytes = number(stats.get("In use system memory"))
    total_bytes = None
    if used is not None and free is not None:
        used_bytes, total_bytes = used, used + free

    return {"id": str(entry.get("IORegistryEntryID") or f"accelerator{index}"),
            "name": name, "vendor": vendor, "source": "macos",
            **only_present({
                "busy": number(stats.get("Device Utilization %")),
                "memory_used": used_bytes, "memory_total": total_bytes,
                "temperature": number(stats.get("Temperature(C)")),
                "power": number(stats.get("Total Power(W)")),
                "clock": number(stats.get("Core Clock(MHz)")),
                "fan_percent": number(stats.get("Fan Speed(%)")),
                "fan_rpm": number(stats.get("Fan Speed(RPM)"))})}


def _vendor(entry: dict[str, Any], name: str) -> str:
    raw = entry.get("vendor-id")
    if isinstance(raw, (bytes, bytearray)) and raw:
        return vendor_from_id(int.from_bytes(raw[:2], "little"))
    return vendor_from_name(name)
