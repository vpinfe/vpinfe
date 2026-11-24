"""Facet choices built from the library rather than from a list this code keeps.

The other choice filters in the grid list states the code owns. These two list whatever
VPS happens to have said, so what is worth pinning is that the choices follow the data and
that a column is left alone when a checkbox list would be the wrong shape for it.
"""

from __future__ import annotations

import unittest
from typing import Any

from manager.kit import grid
from manager.library import games


def rows(*pairs: tuple[str, str]) -> list[dict[str, Any]]:
    return [{"manufacturer": maker, "game_type": kind} for maker, kind in pairs]


def facet(columns: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    """The choices a column ended up with, or [] where it kept the text filter."""
    for definition in columns:
        if definition.get("field") == field:
            return (definition.get("filterParams") or {}).get("choices") or []
    raise AssertionError(f"no {field} column")


def columns_for(field: str) -> list[dict[str, Any]]:
    return [{"field": field, "headerName": field}]


class DerivedFacets(unittest.TestCase):

    def test_the_choices_are_the_values_the_library_holds(self) -> None:
        built = grid.with_derived_facets(
            columns_for("manufacturer"),
            rows(("Williams", "SS"), ("Bally", "EM"), ("Williams", "SS")))
        self.assertEqual([c["value"] for c in facet(built, "manufacturer")],
                         ["Bally", "Williams"])

    def test_they_are_ordered_the_way_a_person_reads_them(self) -> None:
        """Case-folded, so `atari` does not sort after `Zaccaria`."""
        built = grid.with_derived_facets(
            columns_for("manufacturer"),
            rows(("Zaccaria", "SS"), ("atari", "SS"), ("Bally", "SS")))
        self.assertEqual([c["label"] for c in facet(built, "manufacturer")],
                         ["atari", "Bally", "Zaccaria"])

    def test_a_blank_becomes_a_choice_of_its_own(self) -> None:
        """The component matches a blank cell on the empty value, so an unrecorded maker
        is filterable rather than being the one state the funnel cannot express."""
        built = grid.with_derived_facets(
            columns_for("manufacturer"), rows(("Williams", "SS"), ("", "SS")))
        choices = facet(built, "manufacturer")
        self.assertEqual(choices[-1], {"value": "", "label": "Not recorded"})

    def test_no_blank_choice_where_every_row_has_one(self) -> None:
        built = grid.with_derived_facets(
            columns_for("manufacturer"), rows(("Williams", "SS"), ("Bally", "SS")))
        self.assertNotIn("", [c["value"] for c in facet(built, "manufacturer")])

    def test_too_many_values_keeps_the_text_filter(self) -> None:
        """Past the ceiling a checkbox list is a haystack, and the funnel's own text box
        is the better tool - so the column is handed back untouched."""
        many = rows(*[(f"Maker {n}", "SS") for n in range(grid.FACET_CEILING + 1)])
        built = grid.with_derived_facets(columns_for("manufacturer"), many)
        self.assertEqual(facet(built, "manufacturer"), [])

    def test_a_grid_can_raise_the_ceiling(self) -> None:
        many = rows(*[(f"Maker {n}", "SS") for n in range(grid.FACET_CEILING + 1)])
        built = grid.with_derived_facets(columns_for("manufacturer"), many,
                                         grid.FACET_CEILING + 1)
        self.assertEqual(len(facet(built, "manufacturer")), grid.FACET_CEILING + 1)

    def test_a_column_that_is_not_a_derived_facet_is_untouched(self) -> None:
        before = columns_for("year")
        built = grid.with_derived_facets(before, rows(("Williams", "SS")))
        self.assertEqual(built, before)

    def test_the_module_constant_is_not_edited_in_place(self) -> None:
        """COLUMNS is built once at import, so a filter written into it would outlive the
        render that wanted it and follow the next library into the grid."""
        held = [dict(definition) for definition in games.COLUMNS]
        grid.with_derived_facets(games.COLUMNS, rows(("Williams", "SS")))
        self.assertEqual(games.COLUMNS, held)


if __name__ == "__main__":
    unittest.main()
