"""Protects: (c) a control or section built outside the facts grid ran past the others' edge,
and was cut back one control at a time.

What a panel or a page draws ends at `--content-width`, held by one rule on the scroller's
children. A width cap on a control or a row, or a second limit beside that one, brings the
patching back. A section that is meant to span the pane says so with `manager-wide`, and is
listed here, so that a new one is a decision somebody makes rather than a habit.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SHEET = ROOT / "manager" / "static" / "manager-base.css"
TOKENS = ROOT / "manager" / "kit" / "appearance.py"

RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
# Caps of the old kind: one per control, each a different spelling of the same edge.
RETIRED = ("--measure", "--row-measure", "--panel-measure", "--facts-label")
# The Python files that may draw `manager-wide`, with the reason each one spans the pane.
WIDE: dict[str, str] = {
}


def _rules() -> list[tuple[str, str]]:
    css = re.sub(r"/\*.*?\*/", "", SHEET.read_text(encoding="utf-8"), flags=re.S)
    return [(m.group(1).strip(), m.group(2)) for m in RULE.finditer(css)]


class ContentWidthHasOneOwner(unittest.TestCase):
    def test_it_found_the_rules(self) -> None:
        """Or the checks below pass by reading nothing."""
        self.assertGreater(len(_rules()), 500)

    def test_the_limit_is_one_rule_on_the_scrollers_children(self) -> None:
        holders = [selector for selector, body in _rules() if "var(--content-width)" in body]
        self.assertEqual(1, len(holders), holders)
        self.assertIn(".manager-workbench-body > *", holders[0])
        self.assertIn(".manager-region > *", holders[0])

    def test_the_old_per_control_caps_are_gone(self) -> None:
        text = SHEET.read_text(encoding="utf-8") + TOKENS.read_text(encoding="utf-8")
        self.assertEqual([], [name for name in RETIRED if name in text])

    def test_a_section_spans_the_pane_only_where_it_is_listed(self) -> None:
        drawn = {path.relative_to(ROOT).as_posix()
                 for path in (ROOT / "manager").rglob("*.py")
                 if "manager-wide" in path.read_text(encoding="utf-8")}
        self.assertEqual(set(WIDE), drawn)


if __name__ == "__main__":
    unittest.main()
