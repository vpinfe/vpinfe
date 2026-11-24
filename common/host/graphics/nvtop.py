"""nvtop, for a card nothing built in reads: `nvtop -s` prints one JSON snapshot.

Its values arrive as text with a unit on the end and its fields move
between releases, so every one is optional and read for its figure. `-s` needs nvtop 3.2,
and its output is valid JSON from 3.3.2: an older nvtop answers with a reason, not a crash.
"""

from __future__ import annotations

import json
from typing import Any

from common.failures import why
from common.host import tools
from common.host.graphics import (
    UnreadableError,
    number,
    only_present,
    program,
    run,
    vendor_from_name,
)
from common.i18n import t

TOOL = "nvtop"


def cards() -> list[dict[str, Any]]:
    text = run(program(tools.NVTOP), ["-s"], TOOL)
    try:
        snapshot = json.loads(text)
    except json.JSONDecodeError as exc:
        raise UnreadableError(t("error.graphics.tool_unreadable", tool=TOOL), why(exc)) from exc
    if not isinstance(snapshot, list) or not snapshot:
        raise UnreadableError(t("error.graphics.tool_empty", tool=TOOL))

    # Per card, not aggregated: two cards averaged describe neither.
    entries = [one for one in snapshot if isinstance(one, dict)]
    if not entries:
        raise UnreadableError(t("error.graphics.tool_unusable", tool=TOOL))
    several = len(entries) > 1
    return [_card(entry, index, several) for index, entry in enumerate(entries, start=1)]


def _card(entry: dict[str, Any], index: int, several: bool) -> dict[str, Any]:
    name = entry.get("device_name") or (t("metrics.gpu.card", number=index) if several
                                        else t("word.unknown"))
    return {"id": str(index), "name": str(name), "vendor": vendor_from_name(str(name)),
            "source": TOOL,
            **only_present({
                "busy": number(entry.get("gpu_util")),
                "memory_percent": number(entry.get("mem_util")),
                "temperature": number(entry.get("temp")),
                "power": number(entry.get("power_draw")),
                "clock": number(entry.get("gpu_clock")),
                "fan_percent": number(entry.get("fan_speed"))})}
