"""A star never takes a hover look on a screen that cannot hover.

On a touch screen the star last touched keeps `:hover` until something else is, so it
reads as lit by itself while the ones before it read as unrated.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

CSS = Path(__file__).resolve().parents[2] / "manager" / "static" / "manager-base.css"
GATE = "(hover: hover) and (pointer: fine)"


def _hover_rules_outside_the_gate(css: str) -> list[str]:
    """Every selector naming a star and `:hover` that is not inside the gate's `@media`."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    found: list[str] = []
    stack: list[str] = []
    prelude = ""
    for char in css:
        if char == "{":
            head = prelude.strip()
            if (not head.startswith("@") and "star" in head and ":hover" in head
                    and not any(GATE in at for at in stack)):
                found.append(head)
            stack.append(head)
            prelude = ""
        elif char == "}":
            if stack:
                stack.pop()
            prelude = ""
        elif char == ";":
            prelude = ""
        else:
            prelude += char
    return found


class StarHoverIsGatedTests(unittest.TestCase):
    def test_every_star_hover_waits_for_a_pointer_that_can_hover(self) -> None:
        self.assertEqual(_hover_rules_outside_the_gate(CSS.read_text(encoding="utf-8")), [])

    def test_the_check_sees_an_ungated_one(self) -> None:
        self.assertEqual(_hover_rules_outside_the_gate(
            ".manager-star:hover { opacity: 1; }"), [".manager-star:hover"])
        self.assertEqual(_hover_rules_outside_the_gate(
            f"@media {GATE} {{ .manager-star:hover {{ opacity: 1; }} }}"), [])


if __name__ == "__main__":
    unittest.main()
