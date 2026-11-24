"""What the Manager's stylesheet declares, read as text for a test that has no browser."""

from __future__ import annotations

import pathlib
import re

SHEET = pathlib.Path(__file__).resolve().parent.parent.parent / "manager/static/manager-base.css"


def declarations(selector: str, *, whole: bool = False) -> dict[str, str]:
    """Every property set by a rule whose selector ends in `selector` (is `selector`, with
    `whole`), the later rule winning a repeat. The cascade is not applied: what a rule
    that outranks another ends up doing is for a browser to say."""
    text = re.sub(r"/\*.*?\*/", "", SHEET.read_text(encoding="utf-8"), flags=re.S)
    found: dict[str, str] = {}
    for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", text):
        if any(one.strip() == selector if whole else one.strip().endswith(selector)
               for one in selectors.split(",")):
            for line in body.split(";"):
                name, _, value = line.partition(":")
                if value:
                    found[name.strip()] = value.strip()
    return found
