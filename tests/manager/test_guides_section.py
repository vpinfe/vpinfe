"""What the Guides section says about each guide, and what its rail row counts."""

from __future__ import annotations

import unittest

from manager.workbench.game_guides import _guides_label, guide_words


def _context(guides: list[dict], rule_sheet: bool = False) -> dict:
    class Library:
        media = {"g": {"rule_sheet": {"present": rule_sheet}}}

    return {"library": Library(), "game_id": "g", "game": {"guides": guides}}


class AGuide(unittest.TestCase):
    def test_a_maker_who_is_also_the_source_is_said_once(self) -> None:
        _, _, said = guide_words({"title": "AFM", "url": "https://p/", "source":
                                  "Pinball Primer", "authors": ["Pinball Primer"]})
        self.assertEqual("Pinball Primer", said)

    def test_the_source_comes_before_the_makers(self) -> None:
        _, _, said = guide_words({"title": "AFM", "url": "https://v/", "source":
                                  "VPUniverse", "authors": ["Kongedam", "Odradek"]})
        self.assertEqual("VPUniverse · Kongedam, Odradek", said)

    def test_no_title_falls_back_to_what_kind_of_guide_it_is(self) -> None:
        name, _, _ = guide_words({"kind": "tutorial", "title": "", "url": "https://p/"})
        self.assertEqual("Tutorial", name)


class TheRailRow(unittest.TestCase):
    def test_it_counts_the_guides_and_the_rule_sheet(self) -> None:
        self.assertEqual("Guides (3)", _guides_label(_context(
            [{"url": "https://a/"}, {"url": "https://b/"}], rule_sheet=True)))

    def test_a_hidden_guide_is_not_counted(self) -> None:
        self.assertEqual("Guides (1)", _guides_label(_context(
            [{"url": "https://a/"}, {"url": "https://b/", "hidden": True}])))

    def test_with_nothing_there_is_no_count(self) -> None:
        self.assertEqual("Guides", _guides_label(_context([])))


if __name__ == "__main__":
    unittest.main()
