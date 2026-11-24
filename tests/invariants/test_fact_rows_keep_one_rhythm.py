"""Fact rows are spaced by one rule, whatever surface draws them."""

from __future__ import annotations

import pathlib
import re
import unittest

CSS = pathlib.Path(__file__).resolve().parents[2] / "manager" / "static" / "manager-base.css"
BASE = ".manager-facts"
SPACING = re.compile(r"(?:^|[;\s])(row-gap|grid-auto-rows)\s*:")


def _rules() -> list[tuple[str, str]]:
    """(selector, declarations) for every rule, comments dropped."""
    text = re.sub(r"/\*.*?\*/", "", CSS.read_text(encoding="utf-8"), flags=re.S)
    return [(match.group(1).strip(), match.group(2))
            for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", text)]


class FactRowsKeepOneRhythm(unittest.TestCase):
    def test_no_surface_spaces_fact_rows_its_own_way(self) -> None:
        forks = [selector for selector, body in _rules()
                 if BASE in selector and selector != BASE and SPACING.search(body)]
        self.assertEqual([], forks)

    def test_the_base_rule_spaces_them(self) -> None:
        self.assertTrue(any(selector == BASE and "row-gap" in body
                            for selector, body in _rules()))


if __name__ == "__main__":
    unittest.main()
