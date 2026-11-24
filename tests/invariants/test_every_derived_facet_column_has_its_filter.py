"""A Manufacturer or Type column offers the same checkbox filter in every grid.

`grid.with_derived_facets` builds it from the rows. A module that declares one of those
columns and never calls it leaves that grid on a text box.
"""

from __future__ import annotations

import ast
import pathlib
import unittest

from manager.kit import grid
from tests.support import trees

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
MANAGER = REPO / "manager"

DECLARERS = {"column", "identifier", "list_column"}


def _field_of(node: ast.Call) -> object:
    named = next((kw.value for kw in node.keywords if kw.arg == "field"), None)
    first = named if named is not None else (node.args[0] if node.args else None)
    return first.value if isinstance(first, ast.Constant) else None


def _declares_a_facet(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in DECLARERS
                and _field_of(node) in grid.DERIVED_FACETS):
            return True
    return False


def _builds_the_facets(tree: ast.Module) -> bool:
    return any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
               and node.func.attr == "with_derived_facets" for node in ast.walk(tree))


def _without(tree: ast.Module) -> bool:
    return _declares_a_facet(tree) and not _builds_the_facets(tree)


class EveryDerivedFacetColumnHasItsFilter(unittest.TestCase):
    def test_no_grid_declares_one_and_leaves_it_on_a_text_box(self) -> None:
        bare = [path.relative_to(REPO).as_posix() for path in sorted(MANAGER.rglob("*.py"))
                if path.name != "grid.py" and _without(trees.tree_for(path))]
        self.assertEqual(bare, [], "pass the columns through grid.with_derived_facets:\n  "
                         + "\n  ".join(bare))

    def test_the_checker_can_actually_fail(self) -> None:
        declared = 'grid.column("manufacturer", "Maker")'
        self.assertTrue(_without(trees.parse_snippet(declared)))
        self.assertFalse(_without(trees.parse_snippet(
            declared + "\ngrid.with_derived_facets(COLUMNS, rows)")))
        self.assertFalse(_without(trees.parse_snippet('grid.column("name", "Name")')))
        self.assertTrue(_without(trees.parse_snippet(
            'grid.column(field="game_type", header="Type")')))


if __name__ == "__main__":
    unittest.main()
