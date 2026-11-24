"""Settings stacks its sections with the same rules the workbench does, from a wider width."""

from __future__ import annotations

import re
import unittest

from tests.support.stylesheet import SHEET

WORKBENCH = "@container (max-width: 519px) {"
SETTINGS = "@container manager-sections (max-width: 599px) {"


def _block(text: str, opening: str) -> str:
    """What is inside the first `opening { ... }`, braces matched."""
    start = text.index(opening) + len(opening)
    depth = 1
    for at in range(start, len(text)):
        depth += {"{": 1, "}": -1}.get(text[at], 0)
        if not depth:
            return text[start:at]
    raise AssertionError(f"{opening} is never closed")


def _rules(body: str) -> set[tuple[str, str]]:
    """(selector, declarations) of each rule, whitespace collapsed."""
    squash = lambda s: re.sub(r"\s+", " ", s).strip()  # noqa: E731
    return {(squash(selector), squash(declarations))
            for selector, declarations in re.findall(r"([^{}]+)\{([^{}]*)\}", body)}


class SettingsAccordionMatchesTheWorkbenchs(unittest.TestCase):
    def test_every_rule_of_the_workbench_accordion_is_in_the_settings_one(self) -> None:
        text = re.sub(r"/\*.*?\*/", "", SHEET.read_text(encoding="utf-8"), flags=re.S)
        missing = _rules(_block(text, WORKBENCH)) - _rules(_block(text, SETTINGS))
        self.assertEqual(set(), missing)


if __name__ == "__main__":
    unittest.main()
