"""A Year column sorts and filters as a number, and a blank year sorts last.

`grid.year_column` is the one place that says so. A year declared through `grid.column`
is a text column: it sorts as a string, offers "contains" for a filter, and puts a blank
first.
"""

from __future__ import annotations

import ast
import pathlib
import unittest

from manager.kit import grid
from manager.library import assets, discovery, games, media
from tests.support import trees

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
MANAGER = REPO / "manager"

DECLARERS = {"column", "identifier", "list_column"}
GRID_MODULES = {"grid", "grid_module"}


def _year_fields(field: str) -> bool:
    return field.lower() == "year" or field.lower().endswith("_year")


def _field_of(node: ast.Call) -> object:
    """The literal a column call names as its field, positionally or as `field=`."""
    named = next((kw.value for kw in node.keywords if kw.arg == "field"), None)
    first = named if named is not None else (node.args[0] if node.args else None)
    return first.value if isinstance(first, ast.Constant) else None


def _text_years(tree: ast.Module) -> list[ast.Call]:
    """Calls that declare a year column as text."""
    return [node for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr in DECLARERS
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in GRID_MODULES
            and isinstance(field := _field_of(node), str) and _year_fields(field)]


class EveryYearColumnIsANumber(unittest.TestCase):
    def test_no_year_column_is_declared_as_text(self) -> None:
        text = []
        for path in sorted(MANAGER.rglob("*.py")):
            if path.name == "grid.py":
                continue
            text += [f"{path.relative_to(REPO).as_posix()}:{node.lineno}"
                     for node in _text_years(trees.tree_for(path))]
        self.assertEqual(text, [], "declare a year column with grid.year_column:\n  "
                         + "\n  ".join(text))

    def test_the_checker_can_actually_fail(self) -> None:
        found = _text_years(trees.parse_snippet('grid.column("year", "Year", help="x")'))
        self.assertEqual(len(found), 1)
        self.assertEqual(_text_years(trees.parse_snippet('grid.year_column("year", "Year")')), [])
        self.assertEqual(_text_years(trees.parse_snippet('grid.column("yearly", "Year")')), [])
        for source in ('grid.column(field="year", header="Year")',
                       'grid_module.identifier("Year", "Year")'):
            self.assertEqual(len(_text_years(trees.parse_snippet(source))), 1, source)

    def test_the_grids_that_show_a_year_declare_one_that_sorts_as_a_number(self) -> None:
        for module in (games, media, assets, discovery):
            with self.subTest(module=module.__name__):
                year = next(one for one in module.COLUMNS if one["field"] == "year")
                self.assertEqual(year["type"], "numericColumn")
                self.assertEqual(year["filter"], "agNumberColumnFilter")
                self.assertEqual(year[":comparator"], grid.BLANK_LAST)

    def test_the_rows_of_every_grid_hold_the_year_as_a_number(self) -> None:
        said = {"year": "1995", "id": "a", "name": "Taxi"}
        blank = {"year": "", "id": "b", "name": "Orbit"}
        for module in (media, assets):
            with self.subTest(module=module.__name__):
                made = module.rows([said, blank])
                self.assertEqual([one["year"] for one in made], [1995, None])


if __name__ == "__main__":
    unittest.main()
