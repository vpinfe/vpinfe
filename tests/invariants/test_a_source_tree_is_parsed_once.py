"""Every parse of Python source goes through `tests/support/trees.py`.

`trees.py` is the one place allowed to call `ast.parse`; everywhere else asks it for the
tree instead, through `tree_for` for a real file or `parse_snippet` for a literal fixture.
"""

from __future__ import annotations

import ast
import pathlib
import unittest

from tests.support import trees

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
TESTS = ROOT / "tests"
ALLOWED = TESTS / "support" / "trees.py"


def _test_sources() -> list[pathlib.Path]:
    return sorted(path for path in TESTS.rglob("*.py")
                  if "__pycache__" not in path.parts and path != ALLOWED)


def _calls_ast_parse(tree: ast.Module) -> list[int]:
    return [node.lineno for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "parse" and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "ast"]


class ASourceTreeIsParsedOnce(unittest.TestCase):
    def test_no_test_calls_ast_parse_directly(self) -> None:
        offenders = [f"{path.relative_to(ROOT).as_posix()}:{line}"
                     for path in _test_sources()
                     for line in _calls_ast_parse(trees.tree_for(path))]
        self.assertEqual(offenders, [],
                         "call trees.tree_for(path) for a real file or "
                         "trees.parse_snippet(source) for a literal fixture:\n  "
                         + "\n  ".join(offenders))

    def test_the_checker_can_actually_fail(self) -> None:
        self.assertEqual(_calls_ast_parse(trees.parse_snippet("ast.parse('x = 1')")), [1])
        self.assertEqual(_calls_ast_parse(trees.parse_snippet("trees.tree_for(path)")), [])

    def test_nothing_mutates_a_cached_tree(self) -> None:
        """Every path cached above still matches a fresh, independent parse.

        Proves no checker walks a shared tree with a `NodeTransformer` or edits a node
        in place, which would corrupt it for every later caller of the same path.
        """
        cached = trees.cached()
        self.assertGreater(len(cached), 100, "the walk above should have cached many trees")
        mismatched = [str(path) for path, tree in cached.items()
                     if ast.dump(tree) != ast.dump(trees.reparsed(path))]
        self.assertEqual(mismatched, [])


if __name__ == "__main__":
    unittest.main()
