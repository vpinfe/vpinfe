"""A module's `if __name__ == "__main__"` block is its last statement, and its only one.

Run as a script, the block runs where it stands, so a class written below it is never
defined and its tests never run.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from tests.support import trees
from tests.support.project import project_files

REPO = Path(__file__).resolve().parent.parent.parent
SKIP_DIRS = {"build", "third_party", "chromium", "web", "node_modules"}


def _is_main_block(node: ast.stmt) -> bool:
    if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
        return False
    sides = [node.test.left, *node.test.comparators]
    return (any(isinstance(side, ast.Name) and side.id == "__name__" for side in sides)
            and any(isinstance(side, ast.Constant) and side.value == "__main__"
                    for side in sides))


def main_blocks_out_of_place(tree: ast.Module) -> list[int]:
    """The line of every `__main__` block that is not the module's last statement."""
    return [node.lineno for index, node in enumerate(tree.body)
            if _is_main_block(node) and index != len(tree.body) - 1]


def _python_files() -> list[Path]:
    return [path for path in project_files("*.py")
            if not any(part in SKIP_DIRS for part in path.relative_to(REPO).parts)]


class MainBlockTests(unittest.TestCase):
    def test_a_block_above_a_class_is_found(self) -> None:
        tree = trees.parse_snippet("class A: pass\n"
                                    "if __name__ == '__main__':\n    main()\n"
                                    "class B: pass\n")
        self.assertEqual([2], main_blocks_out_of_place(tree))

    def test_a_second_block_is_found(self) -> None:
        block = "if __name__ == '__main__':\n    main()\n"
        self.assertEqual([1], main_blocks_out_of_place(trees.parse_snippet(block + block)))

    def test_a_block_at_the_end_is_in_place(self) -> None:
        tree = trees.parse_snippet("class A: pass\nif __name__ == '__main__':\n    main()\n")
        self.assertEqual([], main_blocks_out_of_place(tree))

    def test_every_main_block_in_the_tree_is_the_last_statement(self) -> None:
        self.maxDiff = None
        found = []
        for path in _python_files():
            tree = trees.tree_for(path)
            found += [f"{path.relative_to(REPO).as_posix()}:{line}"
                      for line in main_blocks_out_of_place(tree)]
        self.assertEqual([], found,
                         "move the __main__ block to the end of the file, and keep one")


if __name__ == "__main__":
    unittest.main()
