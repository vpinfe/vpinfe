"""nvidia-smi: reads every NVIDIA card in one call and ships with the proprietary driver.

Fields a card does not support print as `[N/A]` or `[Not Supported]`, which read as absent.
"""

from __future__ import annotations

import csv
from typing import Any

from common.host import tools
from common.host.graphics import UnreadableError, number, only_present, program, run
from common.i18n import t

TOOL = "nvidia-smi"
MEBIBYTE = 1024 * 1024
QUERY = ("pci.bus_id", "name", "utilization.gpu", "memory.used", "memory.total",
         "temperature.gpu", "power.draw", "clocks.gr", "fan.speed")


def read() -> list[dict[str, Any]]:
    """One entry per card, in the order nvidia-smi lists them, each with its `pci`
    address so a listing can match it."""
    text = run(program(tools.NVIDIA_SMI),
               [f"--query-gpu={','.join(QUERY)}", "--format=csv,noheader,nounits"], TOOL)
    cards = [_card(row) for row in csv.reader(text.splitlines(), skipinitialspace=True)
             if len(row) == len(QUERY)]
    if not cards:
        raise UnreadableError(t("error.graphics.tool_unusable", tool=TOOL))
    return cards


def pci_address(bus_id: str) -> str:
    """`00000000:01:00.0` as `0000:01:00.0`, the form the kernel lists it in."""
    parts = bus_id.strip().lower().split(":")
    return ":".join([parts[0][-4:], *parts[1:]]) if len(parts) == 3 else bus_id.strip().lower()


def _card(row: list[str]) -> dict[str, Any]:
    fields = dict(zip(QUERY, (one.strip() for one in row), strict=True))
    used, total = number(fields["memory.used"]), number(fields["memory.total"])
    return {"pci": pci_address(fields["pci.bus_id"]), "name": fields["name"],
            "vendor": "nvidia", "source": TOOL,
            **only_present({
                "busy": number(fields["utilization.gpu"]),
                "memory_used": None if used is None else used * MEBIBYTE,
                "memory_total": None if total is None else total * MEBIBYTE,
                "temperature": number(fields["temperature.gpu"]),
                "power": number(fields["power.draw"]),
                "clock": number(fields["clocks.gr"]),
                "fan_percent": number(fields["fan.speed"])})}
