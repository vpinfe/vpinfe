"""A grid that takes a view control uses it.

`view_control` hands back a function to call once the grid exists. Everything that makes
a view work is inside it - applying the view on open, the picker's change handler, the
View menu - so taking it and not calling it leaves a dropdown that does nothing.
"""

from __future__ import annotations

import ast
import pathlib
import unittest

from tests.support import trees

MANAGER = pathlib.Path(__file__).resolve().parent.parent.parent / "manager"


def _unwired(tree: ast.AST) -> list[str]:
    taken: list[str] = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Call)
                and getattr(node.value.func, "id", "") == "view_control"
                and isinstance(node.targets[0], ast.Tuple)
                and isinstance(node.targets[0].elts[0], ast.Name)):
            taken.append(node.targets[0].elts[0].id)
    called = {node.func.id for node in ast.walk(tree)
              if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    return [name for name in taken if name not in called]


class EveryViewControlIsWired(unittest.TestCase):
    def test_the_wire_function_is_called(self) -> None:
        stray = []
        for path in sorted(MANAGER.rglob("*.py")):
            stray += [f"{path.name}: {name}"
                      for name in _unwired(trees.tree_for(path))]
        self.assertEqual([], stray, f"view_control's wire function is never called: {stray}")

    def test_it_finds_the_grids_that_use_one(self) -> None:
        using = [path.name for path in sorted(MANAGER.rglob("*.py"))
                 if "view_control(" in path.read_text(encoding="utf-8")]
        self.assertGreaterEqual(len(using), 5, using)


if __name__ == "__main__":
    unittest.main()
