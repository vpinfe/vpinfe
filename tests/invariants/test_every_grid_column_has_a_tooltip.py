"""Every grid column either carries a header tooltip or says it needs none: `help=` is a
real sentence, or `grid.NO_HELP` on a column whose header already says what it holds.
A column with no header words takes a tooltip, never the marker. `grid.build` refuses a
set with a bare column at run time; this finds the call that declares one without
drawing the page."""

from __future__ import annotations

import ast
import pathlib
import unittest

from tests.support import trees

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
MANAGER = REPO / "manager"

DECLARERS = {"column", "identifier", "list_column", "year_column", "named"}
GRID_MODULES = {"grid", "grid_module"}


def _declarations(tree: ast.Module) -> list[ast.Call]:
    return [node for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr in DECLARERS
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in GRID_MODULES]


def _help(node: ast.Call) -> ast.expr | None:
    return next((kw.value for kw in node.keywords if kw.arg == "help"), None)


def _says_something(node: ast.Call) -> bool:
    value = _help(node)
    if value is None:
        return False
    return not (isinstance(value, ast.Constant) and value.value == "")


def _is_marker(node: ast.Call) -> bool:
    value = _help(node)
    return isinstance(value, ast.Attribute) and value.attr == "NO_HELP"


def _header(node: ast.Call) -> ast.expr | None:
    if len(node.args) > 1:
        return node.args[1]
    return next((kw.value for kw in node.keywords if kw.arg == "header"), None)


class EveryGridColumnHasATooltip(unittest.TestCase):
    def test_every_declared_column_passes_help(self) -> None:
        found = 0
        bare = []
        for path in sorted(MANAGER.rglob("*.py")):
            if path.name == "grid.py":
                continue
            for node in _declarations(trees.tree_for(path)):
                found += 1
                if not _says_something(node):
                    field = ast.unparse(node.args[0]) if node.args else "?"
                    bare.append(f"{path.relative_to(REPO).as_posix()}:{node.lineno} {field}")
        self.assertGreater(found, 100, "the scan stopped finding column declarations")
        self.assertEqual(bare, [],
                         "a column explains itself in its header tooltip, passed as "
                         "help=, or says it needs none with help=grid.NO_HELP:\n  "
                         + "\n  ".join(bare))

    def test_a_column_with_no_header_words_never_takes_the_marker(self) -> None:
        wordless = []
        marked = 0
        for path in sorted(MANAGER.rglob("*.py")):
            if path.name == "grid.py":
                continue
            for node in _declarations(trees.tree_for(path)):
                if not _is_marker(node):
                    continue
                marked += 1
                header = _header(node)
                if header is None or (isinstance(header, ast.Constant) and not header.value):
                    wordless.append(f"{path.relative_to(REPO).as_posix()}:{node.lineno}")
        self.assertGreater(marked, 0, "the scan stopped finding the marker")
        self.assertEqual(wordless, [],
                         "a column with no header words needs a tooltip saying what it is")


if __name__ == "__main__":
    unittest.main()
