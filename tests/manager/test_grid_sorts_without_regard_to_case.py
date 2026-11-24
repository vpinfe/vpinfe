"""Grids are built with `accentedSort`, and no column orders text by code unit."""

from __future__ import annotations

import ast
import pathlib
import unittest
from unittest.mock import MagicMock, patch

from manager.kit import grid
from tests.support import trees

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
MANAGER = REPO / "manager"


def _options_of_a_built_grid() -> dict:
    columns = [grid.identifier("name", "Name", help="The name"),
               grid.column("author", "Author", help="Who made it")]
    with patch.object(grid, "ui", MagicMock()) as ui, \
            patch.object(grid.renderers, "install"):
        grid.build(columns, [], "manager.test.columns")
    return ui.aggrid.call_args.args[0]


def _comparators(tree: ast.Module) -> list[ast.expr]:
    """What each `:comparator` entry is set to, whether written there, named or called."""
    return [value for node in ast.walk(tree) if isinstance(node, ast.Dict)
            for key, value in zip(node.keys, node.values, strict=True)
            if isinstance(key, ast.Constant) and key.value == ":comparator"]


def _top_level(tree: ast.Module) -> dict[str, ast.AST]:
    found: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            found.update({t.id: node.value for t in node.targets if isinstance(t, ast.Name)})
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) \
                and node.value is not None:
            found[node.target.id] = node.value
        elif isinstance(node, ast.FunctionDef):
            found[node.name] = node
    return found


def _imports(tree: ast.Module) -> dict[str, pathlib.Path]:
    """Alias -> the manager module it names, for `from manager.x import module`."""
    found: dict[str, pathlib.Path] = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                path = REPO.joinpath(*node.module.split("."), f"{alias.name}.py")
                if path.is_file():
                    found[alias.asname or alias.name] = path
    return found


def _js_of(node: ast.AST, tree: ast.Module, depth: int = 0) -> str:
    """The JavaScript `node` stands for: a literal, an f-string, a constant named in this
    module or another under `manager`, or what a function there returns. Whatever cannot
    be followed adds nothing, and a comparator that reads as empty fails the scan."""
    if depth > 6:
        return ""
    below = depth + 1
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else ""
    if isinstance(node, ast.JoinedStr):
        return "".join(_js_of(part, tree, below) for part in node.values)
    if isinstance(node, ast.FormattedValue):
        return _js_of(node.value, tree, below)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _js_of(node.left, tree, below) + _js_of(node.right, tree, below)
    if isinstance(node, ast.Name | ast.Attribute):
        owner, target = _target(node, tree)
        return _js_of(target, owner, below) if target is not None else ""
    if isinstance(node, ast.Call):
        owner, target = _target(node.func, tree)
        if isinstance(target, ast.FunctionDef):
            return "".join(_js_of(sub.value, owner, below) for sub in ast.walk(target)
                           if isinstance(sub, ast.Return) and sub.value is not None)
    return ""


def _target(node: ast.AST, tree: ast.Module) -> tuple[ast.Module, ast.AST | None]:
    """The module and the definition a name, or `module.name`, points at."""
    if isinstance(node, ast.Name):
        return tree, _top_level(tree).get(node.id)
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        path = _imports(tree).get(node.value.id)
        if path is not None:
            owner = trees.tree_for(path)
            return owner, _top_level(owner).get(node.attr)
    return tree, None


def _compares_by_code_unit(source: str) -> bool:
    """Whether the JavaScript orders with `<` or `>` and never asks the locale. One that
    orders `getTime()` numbers is not ordering text."""
    bare = source.replace("=>", "")
    return (("<" in bare or ">" in bare)
            and "localeCompare" not in bare and "getTime" not in bare)


class EveryGridSortsText(unittest.TestCase):

    def test_the_one_place_grids_are_built_compares_text_by_locale(self) -> None:
        self.assertIs(_options_of_a_built_grid().get("accentedSort"), True)

    def test_the_default_column_does_not_replace_that_with_a_comparator(self) -> None:
        self.assertNotIn("comparator", grid.DEFAULT_COL_DEF)
        self.assertNotIn(":comparator", grid.DEFAULT_COL_DEF)

    def test_a_name_column_declares_no_comparator_of_its_own(self) -> None:
        for definition in (grid.identifier("name", "Name", help="x"),
                           grid.column("name", "Name", help="x")):
            self.assertNotIn(":comparator", definition)
            self.assertNotIn("comparator", definition)

    def test_no_column_in_manager_keeps_a_comparator_that_compares_by_code_unit(self) -> None:
        found, unread = [], []
        for path in sorted(MANAGER.rglob("*.py")):
            tree = trees.tree_for(path)
            for value in _comparators(tree):
                where = f"{path.relative_to(REPO).as_posix()}:{value.lineno}"
                source = _js_of(value, tree)
                if not source:
                    unread.append(where)
                elif _compares_by_code_unit(source):
                    found.append(where)
        self.assertEqual(unread, [], "a column comparator could not be read:\n  "
                         + "\n  ".join(unread))
        self.assertEqual(found, [], "a column comparator uses < or > on text:\n  "
                         + "\n  ".join(found))

    def test_the_checker_can_actually_fail(self) -> None:
        written = trees.parse_snippet("{':comparator': '(a, b) => a < b ? -1 : 1'}")
        named = trees.parse_snippet(
            "WORSE = '(a, b) => a < b ? -1 : 1'\ncolumn = {':comparator': WORSE}")
        called = trees.parse_snippet(
            "def worse(x):\n    return f'(a, b) => a < {x} ? -1 : 1'\n"
            "column = {':comparator': worse(1)}")
        for tree in (written, named, called):
            (value,) = _comparators(tree)
            self.assertTrue(_compares_by_code_unit(_js_of(value, tree)), ast.dump(value))
        self.assertTrue(_compares_by_code_unit("(a, b) => a < b ? -1 : 1"))
        self.assertFalse(_compares_by_code_unit("(a, b) => String(a).localeCompare(String(b))"))
        self.assertFalse(_compares_by_code_unit("(a, b) => a - b"))
        self.assertFalse(_compares_by_code_unit("(f, t) => new Date(t).getTime() < f ? -1 : 1"))

    def test_the_comparators_set_by_name_and_by_call_are_read(self) -> None:
        tree = trees.tree_for(MANAGER / "kit" / "grid.py")
        named = [v for v in _comparators(tree) if isinstance(v, ast.Name | ast.Call)]
        self.assertGreaterEqual(len(named), 2)
        for value in named:
            self.assertIn("descending", _js_of(value, tree))


if __name__ == "__main__":
    unittest.main()
