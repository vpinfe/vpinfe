"""The Displays And Layout section as About's groups: the package's own writer, read
back into headed groups of facts so the page and the package cannot disagree."""

from __future__ import annotations

import re
from typing import Any

from common.config.labels import field_label
from common.host.about import SubLabel
from common.support import sections

_FACT = re.compile(r"^(?P<nested>\s*)- \*\*(?P<label>[^*]+?):\*\*\s*(?P<value>.*)$")
_PARENT = re.compile(r"^- \*\*(?P<label>[^*]+)\*\*\s*$")
_BULLET = re.compile(r"^\s*- (?P<text>.+)$")

TITLE = "Displays And Layout"


def groups() -> list[dict[str, Any]]:
    """Each `###` block of the section as one group, the first under the section's own
    name. Facts keep the writer's order; a line with no label is a fact with an empty
    label, and a fact nested under another is a `SubLabel`."""
    from common.support.collect import displays

    text = sections.collected(displays.SECTION)
    if text.status != sections.OK:
        return [{"heading": field_label(TITLE), "facts": [("", f"Not reported: {text.reason}"
                                              if text.reason else "Not reported")]}]
    found: list[dict[str, Any]] = []
    facts: list[tuple[str, str]] = []
    fenced = False

    def close() -> None:
        if facts:
            found.append({"heading": heading, "facts": list(facts)})
        facts.clear()

    heading = field_label(TITLE)
    for line in text.lines:
        if line.startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            facts.append(("", line))
            continue
        if line.startswith("### "):
            close()
            heading = field_label(TITLE if line[4:].strip() == "Session"
                                  else line[4:].strip())
            continue
        if not line.strip():
            continue
        match = _FACT.match(line)
        if match:
            nested = bool(match["nested"])
            label = SubLabel(match["label"]) if nested else match["label"]
            facts.append((label, match["value"]))
            continue
        match = _PARENT.match(line)
        if match:
            facts.append((match["label"], ""))
            continue
        bullet = _BULLET.match(line)
        facts.append(("", bullet["text"] if bullet else line.strip()))
    close()
    return found
